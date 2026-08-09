"""Micro possession engine — event-chain simulation (Phase 3).

Runs when ``GameDefinition.possession_engine == "micro"``. Each possession
is a chain of events (initiate → pass/dribble → shot/turnover) driven by
``ActionDefinition.transitions`` — the whole event graph is data, so
governance can reshape it through the existing GameDefinitionPatch path.

Design (docs/plans/2026-08-08-granular-sim-engine.md, "Simulation loop"):

- Role+zone state, no coordinates (``PossessionState`` in ``core/state.py``).
- Hard cap of ``MAX_CHAIN_STEPS`` chain steps; the shot clock is
  authoritative — expiry is a dead-ball turnover.
- Next-event selection: transition weights x weight_attributes(actor)
  x runtime scales (turnover/violation ingredients) + strategy and
  ``PossessionContext.transition_biases``. The ``"shot"`` pseudo-target
  delegates to the standard shot-class selection path, so governance
  ``action_biases`` keep their existing meaning.
- Resolution by ``resolution_type``: ``attribute_check`` (shots, via
  ``compute_shot_probability_v2``), ``contested_check`` (actor attribute
  vs defender), ``automatic``.
- Shared mechanics are imported from ``core/possession.py`` — free throws,
  box-outs, blocks, loose-ball fouls, rebounds, subtypes — never duplicated.
- Returns the SAME ``PossessionResult`` shape: one summary ``PossessionLog``
  row with the full event chain attached.

The macro engine (default) is untouched — no existing RNG stream changes.
"""

from __future__ import annotations

import random

from pinwheel.core.defense import (
    SCHEME_CONTEST_MODIFIER,
    assign_matchups,
    get_primary_defender,
    select_scheme,
)
from pinwheel.core.hooks import RegisteredEffect
from pinwheel.core.meta import MetaStore
from pinwheel.core.moves import (
    apply_move_modifier,
    apply_move_secondary_effects,
    get_triggered_moves,
    passive_turnover_modifier,
)
from pinwheel.core.possession import (
    PossessionResult,
    SurfaceModifiers,
    _fire_category_hook,
    attempt_rebound,
    box_out_pre_roll,
    check_foul,
    derive_shot_subtype,
    drain_stamina,
    get_surface_modifiers,
    maybe_block,
    maybe_loose_ball_foul,
    resolve_free_throws,
    select_action,
    select_ball_handler,
    turnover_probability,
    zone_for_action,
)
from pinwheel.core.scoring import (
    compute_shot_probability_v2,
    logistic,
    points_for_action,
)
from pinwheel.core.state import (
    GameState,
    HooperState,
    PossessionContext,
    PossessionState,
)
from pinwheel.models.game import GameEvent, PossessionLog
from pinwheel.models.game_definition import (
    ActionDefinition,
    ActionRegistry,
    basketball_micro_actions,
    basketball_micro_chain_nodes,
)
from pinwheel.models.rules import RuleSet

# Hard cap on chain steps per possession — the shot clock almost always
# terminates the chain first; this is the safety net against degenerate
# governance-defined transition graphs.
MAX_CHAIN_STEPS = 16

# Default per-event clock cost when an ActionDefinition leaves
# time_cost_seconds at 0.0, and the uniform jitter applied to every cost.
DEFAULT_TIME_COST_SECONDS = 2.0
TIME_JITTER_SECONDS = 0.4

# Clock cost of the shot attempt itself (release + arc).
SHOT_TIME_COST_SECONDS = 2.6

# Turnover-branch weights are scaled by turnover_probability()/this value,
# so governance turnover knobs (turnover_rate_modifier, scheme pressure,
# effect modifiers, moves) move micro turnover rates the same direction as
# macro. 0.055 is the macro formula's value for a league-average handler.
TURNOVER_PROBABILITY_BASELINE = 0.055

# Under this much shot clock, shot candidates get a weight multiplier —
# offenses force something up rather than eat the violation.
CLOCK_PRESSURE_SECONDS = 4.0
CLOCK_PRESSURE_SHOT_MULTIPLIER = 4.0

# Shot-probability bonus when the chain created an open look (drive win).
OPEN_SHOT_BONUS = 0.04

# Shot-clock reset after an offensive rebound (14s-style, capped by rules).
SECOND_CHANCE_CLOCK_SECONDS = 14.0

# Fallback chain nodes, used when the registry has no "initiate" node —
# e.g. governance flipped possession_engine via modify_structure without
# adding chain nodes. Keeps possessions from bricking.
_FALLBACK_CHAIN_NODES: dict[str, ActionDefinition] = {
    a.name: a for a in basketball_micro_chain_nodes()
}


def _event_type_for(node: ActionDefinition) -> str:
    """Namespaced event type for a chain node.

    ``pass_swing`` (category ``pass``) → ``"pass.swing"``;
    ``drive`` (category ``dribble``) → ``"dribble.drive"``.
    """
    suffix = node.name
    prefix = f"{node.category}_"
    if suffix.startswith(prefix):
        suffix = suffix[len(prefix):]
    return f"{node.category}.{suffix}"


def _resolve_node(
    node: ActionDefinition,
    actor: HooperState,
    defender: HooperState,
    rng: random.Random,
) -> bool:
    """Resolve a non-shot chain node by its resolution_type.

    - ``automatic``: always succeeds, no RNG draw.
    - ``contested_check``: logistic on (actor attribute - defender defense),
      centered so an even matchup sits at the node's midpoint.
    - ``attribute_check`` (non-shot): logistic on the raw actor attribute.
    """
    if node.resolution_type == "automatic":
        return True
    attr = getattr(actor.current_attributes, node.primary_attribute, 50)
    if node.resolution_type == "contested_check":
        opp = defender.current_attributes.defense
        p = logistic(
            (attr - opp) + 50.0, node.base_midpoint, node.base_steepness,
        )
    else:  # attribute_check on a non-shot node
        p = logistic(attr, node.base_midpoint, node.base_steepness)
    return rng.random() < p


def resolve_possession_micro(
    game_state: GameState,
    rules: RuleSet,
    rng: random.Random,
    last_possession_three: bool = False,
    context: PossessionContext | None = None,
    action_registry: ActionRegistry | None = None,
    effect_registry: list[RegisteredEffect] | None = None,
    meta_store: MetaStore | None = None,
    active_hooks: frozenset[str] = frozenset(),
) -> PossessionResult:
    """Resolve one possession as a micro event chain.

    Same contract as ``resolve_possession()`` — one summary
    ``PossessionLog`` row with the granular chain in ``log.events``.
    Offensive rebounds continue the chain (second chance + clock reset)
    instead of ending the possession, so ``is_offensive_rebound`` is
    always False at exit and callers alternate possession normally.
    """
    if action_registry is None:
        action_registry = ActionRegistry(basketball_micro_actions(rules))
    ctx = context or PossessionContext()
    _effects = effect_registry or []

    registry_has_chain = "initiate" in action_registry

    def node_of(name: str) -> ActionDefinition | None:
        found = action_registry.get(name)
        if found is None and not registry_has_chain:
            return _FALLBACK_CHAIN_NODES.get(name)
        return found

    # Micro consumes second-chance context internally — clear any stale
    # cross-possession flags (parity with the macro path's consume-then-
    # clear behavior).
    game_state.second_chance = False
    game_state.last_rebound_hooper_id = ""

    off_strategy = game_state.offense_strategy
    def_strategy = game_state.defense_strategy
    pace = off_strategy.pace_modifier if off_strategy else 1.0
    def_intensity = def_strategy.defensive_intensity if def_strategy else 0.0

    offense_is_away = not game_state.home_has_ball
    hc_enabled = rules.home_court_enabled
    home_crowd_shot_mod = 0.0
    if hc_enabled and not offense_is_away:
        home_crowd_shot_mod = rules.home_crowd_boost
    crowd_pressure_mod = rules.crowd_pressure if (hc_enabled and offense_is_away) else 0.0
    altitude = game_state.home_venue_altitude_ft
    surface_mods: SurfaceModifiers = get_surface_modifiers(game_state.home_venue_surface)

    offense = game_state.offense
    defense = game_state.defense
    if not offense or not defense:
        return PossessionResult(
            time_used=rules.shot_clock_seconds + rules.dead_ball_time_seconds,
        )

    offense_team_id = (
        game_state.home_agents[0].hooper.team_id
        if game_state.home_has_ball
        else game_state.away_agents[0].hooper.team_id
    )

    events: list[GameEvent] = []

    def emit(
        event_type: str,
        actor_id: str = "",
        target_id: str = "",
        outcome: str = "",
        detail: str = "",
        points: int = 0,
        zone: str = "",
        tags: list[str] | None = None,
    ) -> None:
        events.append(
            GameEvent(
                seq=len(events),
                event_type=event_type,
                actor_id=actor_id,
                target_id=target_id,
                team_id=offense_team_id,
                outcome=outcome,
                detail=detail,
                points=points,
                zone=zone,
                tags=tags or [],
            )
        )

    extra_logs: list[PossessionLog] = []

    # Random ejection check (effect-driven) — same semantics as macro.
    if ctx.random_ejection_probability > 0.0 and rng.random() < ctx.random_ejection_probability:
        active_all = offense + defense
        if active_all:
            victim = rng.choice(active_all)
            victim.ejected = True
            ejection_log = PossessionLog(
                quarter=game_state.quarter,
                possession_number=game_state.possession_number,
                offense_team_id=offense_team_id,
                ball_handler_id=victim.hooper.id,
                action="ejection",
                result=f"effect_ejection:{victim.hooper.name}",
                defensive_scheme="",
                home_score=game_state.home_score,
                away_score=game_state.away_score,
            )
            offense = game_state.offense
            defense = game_state.defense
            if not offense or not defense:
                return PossessionResult(
                    time_used=rules.shot_clock_seconds + rules.dead_ball_time_seconds,
                    log=ejection_log,
                )
            extra_logs.append(ejection_log)

    # Setup: scheme, matchups, ball handler — reuse defense.py wholesale.
    scheme = select_scheme(offense, defense, game_state, rules, rng, strategy=def_strategy)
    matchups = assign_matchups(offense, defense, scheme, rng)
    scheme_mod = SCHEME_CONTEST_MODIFIER[scheme]
    if def_strategy:
        scheme_mod += def_strategy.defensive_intensity

    total_team_fga = sum(a.field_goals_attempted for a in offense)
    handler = select_ball_handler(offense, rng, rules=rules, total_team_fga=total_team_fga)

    def _finish_turnover(action: str, defender_id: str = "") -> PossessionResult:
        """Common exit for every turnover terminal."""
        _chain_fatigue()
        drain_stamina(
            offense, scheme, is_defense=False, rules=rules,
            pace_modifier=pace,
            is_away=offense_is_away, altitude_ft=altitude,
            surface_stamina_multiplier=surface_mods.stamina_drain_multiplier,
        )
        drain_stamina(
            defense, scheme, is_defense=True, rules=rules,
            defensive_intensity=def_intensity, pace_modifier=pace,
            is_away=not offense_is_away, altitude_ft=altitude,
            surface_stamina_multiplier=surface_mods.stamina_drain_multiplier,
        )
        game_state.last_action = action
        game_state.last_result = "turnover"
        game_state.consecutive_makes = 0
        game_state.consecutive_misses += 1
        if "sim.turnover.post" in active_hooks:
            _fire_category_hook(
                "sim.turnover.post", game_state, rules, rng,
                _effects, meta_store, hooper=state.ball_handler,
            )
        log = PossessionLog(
            quarter=game_state.quarter,
            possession_number=game_state.possession_number,
            offense_team_id=offense_team_id,
            ball_handler_id=state.ball_handler.hooper.id,
            defender_id=defender_id,
            action=action,
            result="turnover",
            defensive_scheme=scheme,
            home_score=game_state.home_score,
            away_score=game_state.away_score,
            events=events,
        )
        return PossessionResult(
            turnover=True,
            scoring_team_home=game_state.home_has_ball,
            defensive_scheme=scheme,
            time_used=play_time * pace + rules.dead_ball_time_seconds,
            log=log,
            extra_logs=extra_logs,
        )

    play_time = 0.0
    chain_steps = 0

    # Flow control from governance effects — a blocked possession is dead.
    if ctx.block_action:
        state = PossessionState(ball_handler=handler, events=events)
        play_time = 2.0
        return _finish_turnover("blocked_by_effect")

    state = PossessionState(
        ball_handler=handler,
        shot_clock_remaining=rules.shot_clock_seconds,
        events=events,
    )

    def _chain_fatigue() -> None:
        """Extra drain proportional to chain length.

        Micro possessions run longer than macro ones (the chain folds
        second chances in), so a flat per-possession drain would leave
        players fresher late — and shooting hotter — than the macro
        engine. Charged before the standard drain calls.
        """
        extra = rules.stamina_drain_rate * 0.08 * chain_steps
        if extra <= 0.0:
            return
        for a in offense:
            a.current_stamina = max(0.15, a.current_stamina - extra)
        for d in defense:
            d.current_stamina = max(0.15, d.current_stamina - extra)

    def _assign_roles() -> HooperState:
        """Refresh role maps for the current ball handler; return on-ball defender."""
        state.roles = {
            a.hooper.id: ("handler" if a is state.ball_handler else "spacer")
            for a in offense
        }
        primary = get_primary_defender(state.ball_handler, matchups, defense)
        state.def_roles = {
            d.hooper.id: ("on_ball" if d is primary else "helper") for d in defense
        }
        return primary

    on_ball_defender = _assign_roles()

    def _turnover_scale() -> float:
        """Scale for turnover-branch weights from macro turnover ingredients."""
        p = turnover_probability(
            state.ball_handler, scheme, rules=rules,
            effect_turnover_modifier=(
                ctx.turnover_modifier
                + surface_mods.turnover_rate_modifier
                + passive_turnover_modifier(state.ball_handler)
            ),
            crowd_pressure_modifier=crowd_pressure_mod,
        )
        return p / TURNOVER_PROBABILITY_BASELINE

    def _violation_scale() -> float:
        """Scale for violation-branch weights: strictness knob + handler IQ."""
        iq_factor = 1.0 - state.ball_handler.current_attributes.iq / 200.0
        return max(0.0, rules.violation_strictness * iq_factor)

    def _pick_next(node: ActionDefinition, success: bool) -> str:
        """Select the next chain target from the node's transition table."""
        if success and node.success_transitions:
            table = node.success_transitions
        elif not success and node.failure_transitions:
            table = node.failure_transitions
        else:
            table = node.transitions
        if not table:
            return "shot"
        names: list[str] = []
        weights: list[float] = []
        for name, base_weight in sorted(table.items()):
            w = base_weight + ctx.transition_biases.get(name, 0.0)
            if w <= 0.0:
                continue
            if name == "shot":
                if state.pass_count < rules.min_pass_per_possession:
                    continue
                if state.shot_clock_remaining < CLOCK_PRESSURE_SECONDS:
                    w *= CLOCK_PRESSURE_SHOT_MULTIPLIER
            else:
                cand = node_of(name)
                if cand is None:
                    continue
                if cand.zone_requirement and cand.zone_requirement != state.zone:
                    continue
                if cand.weight_attributes:
                    attrs = state.ball_handler.current_attributes
                    affinity = 1.0 + sum(
                        getattr(attrs, a, 0) * f
                        for a, f in sorted(cand.weight_attributes.items())
                    ) / 100.0
                    w *= max(0.1, affinity)
                if cand.category == "turnover":
                    w *= _turnover_scale()
                elif cand.category == "violation":
                    w *= _violation_scale()
            if w <= 0.0:
                continue
            names.append(name)
            weights.append(w)
        if not names:
            return "shot"
        return rng.choices(names, weights=weights, k=1)[0]

    def _consume_clock(cost: float) -> None:
        nonlocal play_time
        cost = max(0.2, cost + rng.uniform(-TIME_JITTER_SECONDS, TIME_JITTER_SECONDS))
        state.shot_clock_remaining -= cost
        play_time += cost

    putback_eligible = False
    move_name = ""
    next_name = "initiate"

    # Terminal outcome accumulators (filled by the shot branch)
    pts = 0
    made = False
    foul_on_defender = False
    fouling_id = ""
    shot_type = ""
    shooter_id = ""
    assist_id = ""
    rebound_id = ""
    shot_defender_id = ""
    terminal = ""  # "shot" | "turnover" handled inline

    for _ in range(MAX_CHAIN_STEPS):
        chain_steps += 1
        if state.shot_clock_remaining <= 0.0:
            # Shot clock expiry — dead-ball turnover.
            state.ball_handler.turnovers += 1
            emit(
                "turnover.shot_clock",
                actor_id=state.ball_handler.hooper.id,
                outcome="dead_ball",
            )
            return _finish_turnover("shot_clock_violation")

        if "sim.event.pre" in active_hooks:
            _fire_category_hook(
                "sim.event.pre", game_state, rules, rng,
                _effects, meta_store, hooper=state.ball_handler,
            )

        if next_name == "shot":
            _consume_clock(SHOT_TIME_COST_SECONDS)
            handler = state.ball_handler
            shooter_id = handler.hooper.id

            chosen = select_action(
                handler, game_state, rules, rng,
                effect_biases=ctx, surface=surface_mods,
                action_registry=action_registry,
            )
            if ctx.substitute_action and ctx.substitute_action in (
                "at_rim", "mid_range", "three_point",
            ):
                chosen = ctx.substitute_action  # type: ignore[assignment]
            shot_type = chosen
            is_putback = putback_eligible and state.second_chance

            triggered = get_triggered_moves(
                handler, shot_type, last_possession_three,
                game_state.elam_activated, rng,
            )
            primary_defender = _assign_roles()
            shot_defender_id = primary_defender.hooper.id
            def_triggered = get_triggered_moves(
                primary_defender, shot_type, last_possession_three,
                game_state.elam_activated, rng,
            )

            action_def = action_registry.get(shot_type)
            if action_def is None:
                action_def = ActionDefinition(
                    name=shot_type, base_midpoint=40.0, base_steepness=0.05,
                )
            score_diff = abs(game_state.home_score - game_state.away_score)
            base_prob = compute_shot_probability_v2(
                handler, primary_defender, action_def, scheme_mod, rules,
                score_differential=score_diff,
            )

            hook_shot_prob_mod = 0.0
            hook_shot_value_mod = 0
            if "sim.shot.pre" in active_hooks:
                hook_shot_prob_mod, hook_shot_value_mod = _fire_category_hook(
                    "sim.shot.pre", game_state, rules, rng,
                    _effects, meta_store, hooper=handler,
                )

            has_assister = state.potential_assister is not None and not is_putback
            assister_bonus = 0.03 if has_assister else 0.0
            open_bonus = OPEN_SHOT_BONUS if state.open_shot else 0.0
            prob = max(
                0.01,
                min(
                    0.99,
                    base_prob
                    + ctx.shot_probability_modifier
                    + home_crowd_shot_mod
                    + surface_mods.shot_probability_modifier
                    + hook_shot_prob_mod
                    + assister_bonus
                    + open_bonus,
                ),
            )

            move_value_bonus = 0
            if triggered:
                move = triggered[0]
                if not move_name:
                    move_name = move.name
                handler.moves_activated.append(move.name)
                prob = apply_move_modifier(move, prob, rng, action=shot_type)
                move_value_bonus = apply_move_secondary_effects(move, handler)
            for def_move in def_triggered:
                primary_defender.moves_activated.append(def_move.name)
                prob = apply_move_modifier(def_move, prob, rng, action=shot_type)
                if not move_name:
                    move_name = def_move.name

            shot_roll = rng.random()
            made = shot_roll < prob
            pts_this = points_for_action(action_def, rules) if made else 0

            if made:
                u = shot_roll / prob if prob > 0 else 0.0
            else:
                u = (shot_roll - prob) / (1.0 - prob) if prob < 1.0 else 0.0
            shot_subtype = derive_shot_subtype(
                shot_type, handler, u,
                is_putback=is_putback,
                has_assister=has_assister,
            )
            if scheme_mod >= 0.06:
                primary_defender.contested_shots += 1

            shot_tags: list[str] = []
            if state.open_shot:
                shot_tags.append("open")
            if state.second_chance:
                shot_tags.append("second_chance")
            emit(
                f"shot.{shot_type}",
                actor_id=handler.hooper.id,
                target_id=primary_defender.hooper.id,
                outcome="made" if made else "missed",
                detail=shot_subtype,
                zone=zone_for_action(shot_type) or state.zone,
                tags=shot_tags,
            )
            shot_event = events[-1]

            if made:
                pts_this = max(
                    0,
                    pts_this
                    + ctx.shot_value_modifier
                    + ctx.bonus_pass_count
                    + move_value_bonus
                    + hook_shot_value_mod,
                )
                shot_event.points = pts_this

            handler.field_goals_attempted += 1
            if shot_type == "three_point":
                handler.three_pointers_attempted += 1
            if made:
                handler.field_goals_made += 1
                handler.points += pts_this
                if shot_type == "three_point":
                    handler.three_pointers_made += 1
            if has_assister and state.potential_assister is not None:
                state.potential_assister.potential_assists += 1

            # Foul check — same mechanics as macro, FTs via shared helper.
            if check_foul(
                primary_defender, shot_type, scheme, rng,
                rules=rules, defensive_intensity=def_intensity,
            ):
                foul_on_defender = True
                primary_defender.fouls += 1
                fouling_id = primary_defender.hooper.id
                if game_state.home_has_ball:
                    game_state.away_team_fouls += 1
                    defense_team_fouls = game_state.away_team_fouls
                else:
                    game_state.home_team_fouls += 1
                    defense_team_fouls = game_state.home_team_fouls
                if primary_defender.fouls >= rules.personal_foul_limit:
                    primary_defender.ejected = True
                emit(
                    "foul.shooting",
                    actor_id=primary_defender.hooper.id,
                    target_id=handler.hooper.id,
                    detail=shot_subtype,
                    tags=["and_one"] if made else [],
                )
                if "sim.foul.post" in active_hooks:
                    _fire_category_hook(
                        "sim.foul.post", game_state, rules, rng,
                        _effects, meta_store, hooper=primary_defender,
                    )
                if not made:
                    ft_attempts = action_def.free_throw_attempts_on_foul
                    if defense_team_fouls >= rules.team_foul_bonus_threshold:
                        ft_attempts += 1
                    pts_this += resolve_free_throws(
                        handler, primary_defender, action_registry, rules, rng,
                        attempts=ft_attempts, emit=emit,
                    )
                else:
                    if "and_one" not in shot_event.tags:
                        shot_event.tags.append("and_one")
                    pts_this += resolve_free_throws(
                        handler, primary_defender, action_registry, rules, rng,
                        attempts=1, emit=emit, detail="and_one",
                    )

            # Block conversion on an unfouled miss (shared helper).
            if (
                not made
                and not foul_on_defender
                and shot_type in ("at_rim", "mid_range")
            ):
                maybe_block(
                    primary_defender, handler, shot_type, shot_event, rng, emit,
                )

            # Rebound branch on an unfouled miss.
            orb_continues = False
            if not made and not foul_on_defender:
                if "sim.rebound.pre" in active_hooks:
                    _fire_category_hook(
                        "sim.rebound.pre", game_state, rules, rng,
                        _effects, meta_store,
                    )
                box_bonus = box_out_pre_roll(defense, rng, emit)
                rebounder, is_orb = attempt_rebound(
                    offense, defense, rng, rules=rules, defensive_bonus=box_bonus,
                )
                rebounder.rebounds += 1
                rebound_id = rebounder.hooper.id
                reb_roll = rng.random()
                if is_orb:
                    reb_contested = True
                    reb_detail = "tip" if reb_roll < 0.15 else ""
                else:
                    reb_contested = reb_roll < 0.4
                    reb_detail = ""
                emit(
                    "rebound.offensive" if is_orb else "rebound.defensive",
                    actor_id=rebounder.hooper.id,
                    outcome="contested" if reb_contested else "uncontested",
                    detail=reb_detail,
                    zone="paint",
                )
                if reb_contested:
                    maybe_loose_ball_foul(
                        defense, rebounder, game_state, rules, rng, emit,
                        _effects, meta_store, active_hooks,
                    )
                if "sim.rebound.post" in active_hooks:
                    _fire_category_hook(
                        "sim.rebound.post", game_state, rules, rng,
                        _effects, meta_store, hooper=rebounder,
                    )
                if is_orb:
                    # Second chance: chain continues — 14s-style clock reset,
                    # rebounder becomes the handler in the paint.
                    orb_continues = True
                    state.ball_handler = rebounder
                    state.zone = "paint"
                    state.open_shot = False
                    state.second_chance = True
                    state.potential_assister = None
                    putback_eligible = True
                    state.shot_clock_remaining = min(
                        SECOND_CHANCE_CLOCK_SECONDS, rules.shot_clock_seconds,
                    )
                    # Charge the scramble/reset — keeps game-clock cost per
                    # second chance comparable to the macro engine, where an
                    # ORB starts a whole new timed possession.
                    play_time += rules.dead_ball_time_seconds
                    on_ball_defender = _assign_roles()

            # Assist credit on a make (putbacks unassisted) + screen stub.
            if made and has_assister and state.potential_assister is not None:
                assister = state.potential_assister
                assister.assists += 1
                assist_id = assister.hooper.id
                emit(
                    "assist",
                    actor_id=assister.hooper.id,
                    target_id=handler.hooper.id,
                    outcome="success",
                )
                others = [
                    a for a in offense
                    if a.hooper.id not in (handler.hooper.id, assist_id)
                ]
                if others and rng.random() < 0.15:
                    screener = others[0]
                    screener.screen_assists += 1
                    emit(
                        "screen.assist",
                        actor_id=screener.hooper.id,
                        target_id=handler.hooper.id,
                        outcome="success",
                    )

            if "sim.shot.post" in active_hooks:
                _fire_category_hook(
                    "sim.shot.post", game_state, rules, rng,
                    _effects, meta_store, hooper=handler,
                )
            if "sim.event.post" in active_hooks:
                _fire_category_hook(
                    "sim.event.post", game_state, rules, rng,
                    _effects, meta_store, hooper=handler,
                )

            pts += pts_this
            if orb_continues:
                made = False
                foul_on_defender = False
                rebound_id = ""
                second_chance_node = node_of("second_chance")
                next_name = "second_chance" if second_chance_node else "initiate"
                continue
            terminal = "shot"
            break

        # ---- Non-shot chain node ----
        node = node_of(next_name)
        if node is None:
            next_name = "shot"
            continue

        _consume_clock(
            node.time_cost_seconds if node.time_cost_seconds > 0.0
            else DEFAULT_TIME_COST_SECONDS
        )

        if node.category == "turnover":
            # Live-ball turnover terminal — attribution by subtype.
            handler = state.ball_handler
            handler.turnovers += 1
            if node.name.endswith("bad_pass"):
                stealer = rng.choice(defense)
                stealer.steals += 1
                stealer.deflections += 1
                emit(
                    "turnover.bad_pass",
                    actor_id=handler.hooper.id,
                    target_id=stealer.hooper.id,
                    outcome="stolen",
                )
            else:
                stealer = on_ball_defender
                stealer.steals += 1
                emit(
                    "turnover.lost_ball",
                    actor_id=handler.hooper.id,
                    target_id=stealer.hooper.id,
                    outcome="stolen",
                )
            return _finish_turnover("turnover", defender_id=stealer.hooper.id)

        if node.category == "violation":
            # Dead-ball violation terminal (travel / double dribble).
            handler = state.ball_handler
            handler.turnovers += 1
            v_roll = rng.random()
            violation = "travel" if v_roll < 0.6 else "double_dribble"
            emit(
                f"turnover.{violation}",
                actor_id=handler.hooper.id,
                outcome="dead_ball",
            )
            return _finish_turnover(violation)

        success = _resolve_node(node, state.ball_handler, on_ball_defender, rng)
        emit(
            _event_type_for(node),
            actor_id=state.ball_handler.hooper.id,
            target_id=(
                on_ball_defender.hooper.id
                if node.resolution_type == "contested_check"
                else ""
            ),
            outcome="success" if success else "failure",
            zone=state.zone,
            tags=list(node.emits_tags),
        )
        state.last_event = node.name

        # Category-specific state updates.
        if node.category == "pass" and success:
            passer = state.ball_handler
            teammates = [a for a in offense if a is not passer]
            if teammates:
                weights = [
                    max(1, t.current_attributes.scoring + t.current_attributes.iq)
                    for t in teammates
                ]
                receiver = rng.choices(teammates, weights=weights, k=1)[0]
                passer.passes_made += 1
                state.pass_count += 1
                state.potential_assister = passer
                state.ball_handler = receiver
                state.zone = "perimeter"
                state.open_shot = False
                putback_eligible = False
                on_ball_defender = _assign_roles()
        elif node.category == "dribble":
            state.dribble_count += 1
            putback_eligible = False
            if "drive" in node.emits_tags or node.name == "drive":
                state.ball_handler.drives += 1
                if success:
                    state.zone = "paint"
                    state.open_shot = True

        if "sim.event.post" in active_hooks:
            _fire_category_hook(
                "sim.event.post", game_state, rules, rng,
                _effects, meta_store, hooper=state.ball_handler,
            )

        next_name = _pick_next(node, success)
    else:
        # Chain-step cap reached without a terminal — treat as a shot
        # clock turnover (the clock is authoritative; this is the net).
        state.ball_handler.turnovers += 1
        emit(
            "turnover.shot_clock",
            actor_id=state.ball_handler.hooper.id,
            outcome="dead_ball",
        )
        return _finish_turnover("shot_clock_violation")

    if terminal != "shot":
        # Loop exhausted via break without a shot terminal cannot happen;
        # guard for safety.
        state.ball_handler.turnovers += 1
        emit(
            "turnover.shot_clock",
            actor_id=state.ball_handler.hooper.id,
            outcome="dead_ball",
        )
        return _finish_turnover("shot_clock_violation")

    # ---- Shot terminal: score, stamina, tracking, summary log ----
    if pts > 0:
        if game_state.home_has_ball:
            game_state.home_score += pts
        else:
            game_state.away_score += pts

    _chain_fatigue()
    drain_stamina(
        offense, scheme, is_defense=False, rules=rules,
        pace_modifier=pace,
        is_away=offense_is_away, altitude_ft=altitude,
        surface_stamina_multiplier=surface_mods.stamina_drain_multiplier,
    )
    drain_stamina(
        defense, scheme, is_defense=True, rules=rules,
        defensive_intensity=def_intensity, pace_modifier=pace,
        is_away=not offense_is_away, altitude_ft=altitude,
        surface_stamina_multiplier=surface_mods.stamina_drain_multiplier,
    )
    if ctx.extra_stamina_drain > 0.0:
        state.ball_handler.current_stamina = max(
            0.15, state.ball_handler.current_stamina - ctx.extra_stamina_drain
        )

    game_state.last_action = shot_type
    if made:
        game_state.last_result = "made"
        game_state.consecutive_makes += 1
        game_state.consecutive_misses = 0
    else:
        game_state.last_result = "missed"
        game_state.consecutive_makes = 0
        game_state.consecutive_misses += 1

    log = PossessionLog(
        quarter=game_state.quarter,
        possession_number=game_state.possession_number,
        offense_team_id=offense_team_id,
        ball_handler_id=shooter_id,
        action=shot_type,
        result="made" if made else ("foul" if foul_on_defender else "missed"),
        points_scored=pts,
        defender_id=shot_defender_id,
        assist_id=assist_id,
        rebound_id=rebound_id,
        is_offensive_rebound=False,
        move_activated=move_name,
        defensive_scheme=scheme,
        home_score=game_state.home_score,
        away_score=game_state.away_score,
        events=events,
    )

    return PossessionResult(
        points_scored=pts,
        scoring_team_home=game_state.home_has_ball,
        turnover=False,
        foul_on_defender=foul_on_defender,
        fouling_agent_id=fouling_id,
        shooter_id=shooter_id,
        assist_id=assist_id,
        rebound_id=rebound_id,
        is_offensive_rebound=False,
        shot_type=shot_type,
        shot_made=made,
        move_activated=move_name,
        defensive_scheme=scheme,
        time_used=play_time * pace + rules.dead_ball_time_seconds,
        log=log,
        extra_logs=extra_logs,
    )
