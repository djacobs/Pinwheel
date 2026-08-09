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

import dataclasses
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
    GameDefinition,
    basketball_micro_actions,
    basketball_micro_chain_nodes,
)
from pinwheel.models.rules import RuleSet
from pinwheel.models.team import Move

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

# Shot-probability bonus when the chain created an open look (drive win,
# backdoor cut, kickout off a help rotation) or the closeout never arrives.
OPEN_SHOT_BONUS = 0.04

# Shot-probability malus when the closeout arrives (contested shot).
CONTESTED_SHOT_MALUS = 0.02

# Chain bonuses (Tier-2): ball movement and screens tilt the NEXT shot.
SKIP_PASS_SHOT_BONUS = 0.03
EXTRA_PASS_SHOT_BONUS = 0.02
KICKOUT_SHOT_BONUS = 0.02
SCREEN_ROLL_SHOT_BONUS = 0.03
SCREEN_POP_SHOT_BONUS = 0.02
SPOT_UP_SHOT_BONUS = 0.02
CURL_SHOT_BONUS = 0.02

# Steal-gamble outcome split: the deflection share is flat; the steal share
# scales with the defender-vs-handler edge; the rest is a blow-by.
STEAL_DEFLECTION_CHANCE = 0.25

# Transition possessions (opened by a live-ball turnover or defensive
# rebound): quicker initiation and a small at-rim bias.
TRANSITION_TIME_SCALE = 0.6
TRANSITION_AT_RIM_BIAS = 6.0

# Shot-clock reset after an offensive rebound (14s-style, capped by rules).
SECOND_CHANCE_CLOCK_SECONDS = 14.0

# ---- Tier-3 structural surface (Phase 5) ----
# None of these draw RNG on the default path: check-ball/clear-arc are
# gated by GameDefinition flags (default off), injury by rules.injury_rate
# (default 0.0), goaltending by the violation_goaltending node's
# selection_weight (default 0.0), and the violation/foul nodes by their
# transition weights (default 0.0).

# Clock cost of a FIBA-3x3-style check-ball restart.
CHECK_BALL_TIME_COST_SECONDS = 1.2

# Clock cost of clearing the ball beyond the arc after a live-ball change.
CLEAR_ARC_TIME_COST_SECONDS = 1.4

# Clear-arc failure chance: base minus an IQ discount (floor 2%).
CLEAR_ARC_FAIL_BASE = 0.10
CLEAR_ARC_FAIL_FLOOR = 0.02

# Stamina floor an injured hooper drops to for the rest of the game.
INJURY_STAMINA_FLOOR = 0.15

# Terminal dead-ball violation nodes: name -> (event_type, log action).
_TERMINAL_VIOLATIONS: dict[str, tuple[str, str]] = {
    "violation_backcourt": ("violation.backcourt", "backcourt"),
    "violation_three_second": ("violation.three_second", "three_second"),
    "violation_lane": ("violation.lane", "lane"),
}

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
    game_def: GameDefinition | None = None,
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

    def _finish_turnover(
        action: str, defender_id: str = "", live_ball: bool = False
    ) -> PossessionResult:
        """Common exit for every turnover terminal.

        ``live_ball`` turnovers (steals, strips, picked-off passes) open a
        transition opportunity for the other team next possession.
        """
        game_state.pass_count_last_possession = state.pass_count
        game_state.transition_possession = live_ball
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
            points_scored=early_points,
            defensive_scheme=scheme,
            home_score=game_state.home_score,
            away_score=game_state.away_score,
            events=events,
            tags=["transition"] if opened_in_transition else [],
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

    # Points banked mid-chain (Tier-3 clear-path / away-from-play free
    # throws). Committed to the game score the moment they're earned, so
    # they survive any possession ending — including a later turnover.
    early_points = 0

    # Transition context: opened by the PREVIOUS possession's live-ball
    # turnover or defensive rebound. The flag stays visible on GameState
    # for the whole possession (governance conditions gate on it); every
    # exit path re-sets it for the next possession. A Tier-3 take/clear-
    # path foul kills the advantage mid-possession; the summary log's
    # "transition" tag reflects how the possession OPENED.
    is_transition = game_state.transition_possession
    opened_in_transition = is_transition

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

    # ---- Tier-2 chain state (Phase 4) ----
    # Effective-defender override from defense.switch / defense.help_rotation.
    # Cleared whenever the ball changes hands (matchups re-form).
    defender_override: HooperState | None = None

    # Cutter designated by the last screen.off_ball, consumed by cut nodes.
    pending_cutter: HooperState | None = None

    # Moves triggered by chain events (drive/iso/pass/initiate) before the
    # shot exists — applied at shot resolution through the same
    # apply_move_modifier code path as shot-time triggers.
    pending_off_moves: list[tuple[HooperState, Move]] = []
    pending_def_moves: list[tuple[HooperState, Move]] = []
    pending_seen: set[tuple[str, str]] = set()

    def _queue_moves(
        actor: HooperState,
        action_name: str,
        defender: HooperState | None = None,
    ) -> None:
        """Collect moves triggered by a chain event for the eventual shot."""
        if actor.hooper.moves:
            for mv in get_triggered_moves(
                actor, action_name, last_possession_three,
                game_state.elam_activated, rng,
            ):
                key = (actor.hooper.id, mv.name)
                if key not in pending_seen:
                    pending_seen.add(key)
                    pending_off_moves.append((actor, mv))
        if defender is not None and defender.hooper.moves:
            for mv in get_triggered_moves(
                defender, action_name, last_possession_three,
                game_state.elam_activated, rng,
            ):
                key = (defender.hooper.id, mv.name)
                if key not in pending_seen:
                    pending_seen.add(key)
                    pending_def_moves.append((defender, mv))

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
                # Tier-3: take/clear-path/away-from-play fouls only emerge
                # from transition possessions.
                if "transition_only" in cand.emits_tags and not is_transition:
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
                    # Paint-dwell violations scale with time camped in the
                    # paint (counting the current paint stay) — an offense
                    # that never parks in the lane keeps the weight at zero.
                    if "paint_dwell" in cand.emits_tags:
                        dwell = state.paint_event_streak + (
                            1 if state.zone == "paint" else 0
                        )
                        w *= float(dwell)
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
    ended_with_drb = False  # final miss rebounded by the defense → transition

    # Transition possessions bias shot selection slightly toward the rim.
    if is_transition:
        shot_ctx = dataclasses.replace(
            ctx,
            action_biases={
                **ctx.action_biases,
                "at_rim": ctx.action_biases.get("at_rim", 0.0)
                + TRANSITION_AT_RIM_BIAS,
            },
        )
    else:
        shot_ctx = ctx

    def _move_ball(
        receiver: HooperState,
        new_zone: str,
        passer: HooperState | None = None,
        open_shot: bool = False,
    ) -> None:
        """Move the ball to ``receiver`` (optionally crediting a pass)."""
        nonlocal on_ball_defender, defender_override, putback_eligible
        if passer is not None:
            passer.passes_made += 1
            state.pass_count += 1
            game_state.pass_count_last_possession = state.pass_count
            state.potential_assister = passer
        state.ball_handler = receiver
        state.zone = new_zone
        state.open_shot = open_shot
        putback_eligible = False
        defender_override = None
        on_ball_defender = _assign_roles()

    # ---- Tier-3 pre-chain events (Phase 5) — all inert by default ----

    # Injury: pace-scaled rare event, OFF unless rules.injury_rate > 0.
    # The victim's stamina floors for the rest of the game (recovery
    # skips injured hoopers). No cross-game persistence (future work).
    if rules.injury_rate > 0.0:
        injury_p = min(1.0, rules.injury_rate / max(0.5, pace))
        if rng.random() < injury_p:
            healthy = [a for a in offense + defense if not a.injured]
            if healthy:
                victim = rng.choice(healthy)
                victim.injured = True
                victim.current_stamina = INJURY_STAMINA_FLOOR
                emit(
                    "injury",
                    actor_id=victim.hooper.id,
                    outcome="hobbled",
                    zone=state.zone,
                )

    # FIBA-3x3 check-ball restart: dead-ball possessions open with the
    # ball checked at the top. Transition possessions skip it — live ball.
    if game_def is not None and game_def.check_ball_restarts and not is_transition:
        _consume_clock(CHECK_BALL_TIME_COST_SECONDS)
        emit(
            "admin.check_ball",
            actor_id=state.ball_handler.hooper.id,
            outcome="success",
            zone="perimeter",
        )

    # FIBA-3x3 clear-arc rule: after a live-ball change the offense must
    # clear the ball beyond the arc; failing is a dead-ball violation.
    if game_def is not None and game_def.clear_arc_required and is_transition:
        _consume_clock(CLEAR_ARC_TIME_COST_SECONDS)
        clear_fail_p = max(
            CLEAR_ARC_FAIL_FLOOR,
            CLEAR_ARC_FAIL_BASE
            - state.ball_handler.current_attributes.iq / 1000.0,
        )
        if rng.random() < clear_fail_p:
            state.ball_handler.turnovers += 1
            emit(
                "violation.clear_arc",
                actor_id=state.ball_handler.hooper.id,
                outcome="dead_ball",
                zone="perimeter",
            )
            return _finish_turnover("clear_arc")
        emit(
            "admin.clear_arc",
            actor_id=state.ball_handler.hooper.id,
            outcome="success",
            zone="perimeter",
        )

    for _ in range(MAX_CHAIN_STEPS):
        chain_steps += 1
        # Paint-dwell counter (Tier-3): consecutive chain steps with the
        # ball in the paint. Scales the violation_three_second edge weight,
        # so the violation only threatens offenses camped in the lane.
        if state.zone == "paint":
            state.paint_event_streak += 1
        else:
            state.paint_event_streak = 0
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
                effect_biases=shot_ctx, surface=surface_mods,
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
            # defense.switch / defense.help_rotation changed who is on the
            # ball — the override is the effective defender for resolution.
            if (
                defender_override is not None
                and not defender_override.ejected
                and defender_override.on_court
            ):
                primary_defender = defender_override
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

            # Contest / closeout (Tier-2): every shot is either open or
            # contested. A chain-created open look (drive win, backdoor,
            # kickout synergy) is never closed out; otherwise the effective
            # defender's closeout ability decides. Contested shots take a
            # small malus and credit the defender's contested_shots stat.
            if state.open_shot:
                contested = False
            else:
                d_attrs = primary_defender.current_attributes
                closeout = d_attrs.defense * 0.6 + d_attrs.speed * 0.4
                p_contested = max(
                    0.05, min(0.92, 0.40 + closeout / 250.0 + scheme_mod),
                )
                contested = rng.random() < p_contested
            emit(
                "defense.contest",
                actor_id=primary_defender.hooper.id,
                target_id=handler.hooper.id,
                outcome="contested" if contested else "open",
                detail="closeout",
                zone=zone_for_action(shot_type) or state.zone,
            )
            if contested:
                primary_defender.contested_shots += 1
            open_bonus = 0.0 if contested else OPEN_SHOT_BONUS
            contest_malus = CONTESTED_SHOT_MALUS if contested else 0.0
            # Chain-earned bonus (skip/extra passes, screens, spot-ups) is
            # consumed by this shot.
            chain_bonus = state.next_shot_bonus
            state.next_shot_bonus = 0.0

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
                    + open_bonus
                    + chain_bonus
                    - contest_malus,
                ),
            )

            move_value_bonus = 0
            applied_move_names: set[str] = set()
            if triggered:
                move = triggered[0]
                applied_move_names.add(move.name)
                if not move_name:
                    move_name = move.name
                handler.moves_activated.append(move.name)
                prob = apply_move_modifier(move, prob, rng, action=shot_type)
                move_value_bonus = apply_move_secondary_effects(move, handler)
            for def_move in def_triggered:
                applied_move_names.add(def_move.name)
                primary_defender.moves_activated.append(def_move.name)
                prob = apply_move_modifier(def_move, prob, rng, action=shot_type)
                if not move_name:
                    move_name = def_move.name
            # Moves triggered earlier in the chain (drive/iso/pass/initiate
            # events) apply here through the same code path.
            for owner, mv in pending_off_moves:
                if mv.name in applied_move_names:
                    continue
                applied_move_names.add(mv.name)
                owner.moves_activated.append(mv.name)
                if not move_name:
                    move_name = mv.name
                prob = apply_move_modifier(mv, prob, rng, action=shot_type)
                move_value_bonus += apply_move_secondary_effects(mv, owner)
            for owner, mv in pending_def_moves:
                if mv.name in applied_move_names:
                    continue
                applied_move_names.add(mv.name)
                owner.moves_activated.append(mv.name)
                if not move_name:
                    move_name = mv.name
                prob = apply_move_modifier(mv, prob, rng, action=shot_type)

            shot_roll = rng.random()
            made = shot_roll < prob

            # Tier-3 goaltending: a rare defensive goaltend on a missed
            # rim/mid shot awards the basket. The violation_goaltending
            # node's selection_weight IS the per-missed-shot probability —
            # 0.0 by default, so no RNG is drawn until governance
            # activates it (or removes the node, FIBA-style "legal after
            # rim contact").
            goaltended = False
            if not made and shot_type in ("at_rim", "mid_range"):
                gt_node = action_registry.get("violation_goaltending")
                if (
                    gt_node is not None
                    and gt_node.selection_weight > 0.0
                    and rng.random() < min(1.0, gt_node.selection_weight)
                ):
                    made = True
                    goaltended = True

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
            shot_tags: list[str] = ["contested" if contested else "open"]
            if state.second_chance:
                shot_tags.append("second_chance")
            if is_transition:
                shot_tags.append("transition")
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
            if goaltended:
                shot_event.tags.append("goaltended")
                emit(
                    "violation.goaltending",
                    actor_id=primary_defender.hooper.id,
                    target_id=handler.hooper.id,
                    outcome="basket_awarded",
                    zone=zone_for_action(shot_type) or state.zone,
                )

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
                    ended_with_drb = True
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

            # Assist credit on a make (putbacks unassisted).
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
            # Real screen-assist credit (replaces the Phase-2 stub): a made
            # basket after a screen this possession credits the screener.
            if (
                made
                and state.screener is not None
                and state.screener.hooper.id != handler.hooper.id
            ):
                scr = state.screener
                scr.screen_assists += 1
                emit(
                    "screen.assist",
                    actor_id=scr.hooper.id,
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
                ended_with_drb = False
                # Reset chain state — the scramble breaks screens, cuts,
                # switches, and any earned shot bonus.
                state.screener = None
                state.next_shot_bonus = 0.0
                state.kickout_open = False
                defender_override = None
                pending_cutter = None
                pending_off_moves.clear()
                pending_def_moves.clear()
                pending_seen.clear()
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

        node_cost = (
            node.time_cost_seconds if node.time_cost_seconds > 0.0
            else DEFAULT_TIME_COST_SECONDS
        )
        if is_transition and chain_steps == 1:
            # Transition offense initiates faster.
            node_cost *= TRANSITION_TIME_SCALE
        _consume_clock(node_cost)

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
            return _finish_turnover(
                "turnover", defender_id=stealer.hooper.id, live_ball=True,
            )

        if node.category == "violation":
            handler = state.ball_handler
            if node.name == "violation_kicked_ball":
                # Defensive violation — offense keeps the ball with a
                # 14s-style shot-clock reset (Tier-3, non-terminal).
                kicker = on_ball_defender
                state.shot_clock_remaining = max(
                    state.shot_clock_remaining,
                    min(
                        SECOND_CHANCE_CLOCK_SECONDS,
                        float(rules.shot_clock_seconds),
                    ),
                )
                emit(
                    "violation.kicked_ball",
                    actor_id=kicker.hooper.id,
                    target_id=handler.hooper.id,
                    outcome="offense_retains",
                    zone=state.zone,
                )
                state.last_event = node.name
                if "sim.event.post" in active_hooks:
                    _fire_category_hook(
                        "sim.event.post", game_state, rules, rng,
                        _effects, meta_store, hooper=state.ball_handler,
                    )
                next_name = _pick_next(node, True)
                continue
            if node.name in _TERMINAL_VIOLATIONS:
                # Tier-3 dead-ball violation terminal (backcourt /
                # three-second paint dwell / lane).
                event_type, action = _TERMINAL_VIOLATIONS[node.name]
                handler.turnovers += 1
                emit(
                    event_type,
                    actor_id=handler.hooper.id,
                    outcome="dead_ball",
                    zone=state.zone,
                )
                return _finish_turnover(action)
            # Dead-ball violation terminal (travel / double dribble).
            handler.turnovers += 1
            v_roll = rng.random()
            violation = "travel" if v_roll < 0.6 else "double_dribble"
            emit(
                f"turnover.{violation}",
                actor_id=handler.hooper.id,
                outcome="dead_ball",
            )
            return _finish_turnover(violation)

        if node.category == "foul":
            # Tier-3 transition-foul nodes: take / clear_path /
            # away_from_play. All kill the break (side-out, half-court
            # reset); clear-path and away-from-play also award one free
            # throw before the offense retains the ball.
            handler = state.ball_handler
            fouler = on_ball_defender
            fouler.fouls += 1
            if game_state.home_has_ball:
                game_state.away_team_fouls += 1
            else:
                game_state.home_team_fouls += 1
            if fouler.fouls >= rules.personal_foul_limit:
                fouler.ejected = True
            suffix = (
                node.name[len("foul_"):]
                if node.name.startswith("foul_")
                else node.name
            )
            emit(
                f"foul.{suffix}",
                actor_id=fouler.hooper.id,
                target_id=handler.hooper.id,
                outcome="transition_killed",
                zone=state.zone,
                tags=[t for t in node.emits_tags if t != "transition_only"],
            )
            if "sim.foul.post" in active_hooks:
                _fire_category_hook(
                    "sim.foul.post", game_state, rules, rng,
                    _effects, meta_store, hooper=fouler,
                )
            if node.name in ("foul_clear_path", "foul_away_from_play"):
                ft_pts = resolve_free_throws(
                    handler, fouler, action_registry, rules, rng,
                    attempts=1, emit=emit, detail=suffix,
                )
                if ft_pts:
                    # Bank the points immediately — they must survive any
                    # later possession ending, including a turnover.
                    if game_state.home_has_ball:
                        game_state.home_score += ft_pts
                    else:
                        game_state.away_score += ft_pts
                    early_points += ft_pts
            # The foul kills the break: half-court reset, advantage gone.
            is_transition = False
            game_state.transition_possession = False
            shot_ctx = ctx
            state.open_shot = False
            state.zone = "perimeter"
            state.last_event = node.name
            if "sim.event.post" in active_hooks:
                _fire_category_hook(
                    "sim.event.post", game_state, rules, rng,
                    _effects, meta_store, hooper=state.ball_handler,
                )
            next_name = _pick_next(node, True)
            continue

        if node.category == "defense" and node.name == "steal_attempt":
            # Steal gamble: steal / deflection + loose-ball scramble / blow-by.
            handler = state.ball_handler
            gambler = on_ball_defender
            g_attrs = gambler.current_attributes
            h_attrs = handler.current_attributes
            edge = (
                (g_attrs.defense + g_attrs.speed) / 2.0
                - (h_attrs.iq + h_attrs.speed) / 2.0
            )
            p_steal = max(0.10, min(0.55, 0.30 + edge / 500.0))
            gamble_roll = rng.random()
            if gamble_roll < p_steal:
                gambler.steals += 1
                handler.turnovers += 1
                emit(
                    "defense.steal_attempt",
                    actor_id=gambler.hooper.id,
                    target_id=handler.hooper.id,
                    outcome="steal",
                    zone=state.zone,
                )
                emit(
                    "turnover.lost_ball",
                    actor_id=handler.hooper.id,
                    target_id=gambler.hooper.id,
                    outcome="stolen",
                )
                return _finish_turnover(
                    "turnover", defender_id=gambler.hooper.id, live_ball=True,
                )
            if gamble_roll < p_steal + STEAL_DEFLECTION_CHANCE:
                # Deflection — loose-ball scramble (hustle stat for the winner).
                gambler.deflections += 1
                emit(
                    "defense.steal_attempt",
                    actor_id=gambler.hooper.id,
                    target_id=handler.hooper.id,
                    outcome="deflection",
                    zone=state.zone,
                )
                scramblers = list(offense) + list(defense)
                scramble_weights = [
                    max(
                        1.0,
                        s.current_attributes.speed
                        + s.current_attributes.stamina * 50,
                    )
                    for s in scramblers
                ]
                winner = rng.choices(scramblers, weights=scramble_weights, k=1)[0]
                winner.loose_balls += 1
                won_by_defense = winner in defense
                emit(
                    "hustle.loose_ball",
                    actor_id=winner.hooper.id,
                    outcome="defense" if won_by_defense else "offense",
                    zone=state.zone,
                )
                if won_by_defense:
                    handler.turnovers += 1
                    winner.steals += 1
                    return _finish_turnover(
                        "turnover", defender_id=winner.hooper.id, live_ball=True,
                    )
                _move_ball(winner, "perimeter")
            else:
                # Blow-by — the gamble fails and the offense has an advantage.
                emit(
                    "defense.steal_attempt",
                    actor_id=gambler.hooper.id,
                    target_id=handler.hooper.id,
                    outcome="blow_by",
                    zone=state.zone,
                )
                state.open_shot = True
            state.last_event = node.name
            if "sim.event.post" in active_hooks:
                _fire_category_hook(
                    "sim.event.post", game_state, rules, rng,
                    _effects, meta_store, hooper=state.ball_handler,
                )
            next_name = _pick_next(node, True)
            continue

        if node.category == "defense":
            # defense.switch / defense.help_rotation — change the effective
            # defender used for shot resolution.
            handler = state.ball_handler
            if node.name == "defense_switch":
                candidates = [d for d in defense if d is not on_ball_defender]
                new_def: HooperState | None = None
                if state.screener is not None:
                    new_def = get_primary_defender(
                        state.screener, matchups, defense,
                    )
                if (new_def is None or new_def is on_ball_defender) and candidates:
                    new_def = rng.choice(candidates)
                if new_def is not None:
                    defender_override = new_def
                    emit(
                        "defense.switch",
                        actor_id=new_def.hooper.id,
                        target_id=handler.hooper.id,
                        outcome="success",
                        zone=state.zone,
                    )
            elif node.name == "help_rotation":
                helpers = [d for d in defense if d is not on_ball_defender]
                if helpers:
                    helper = max(
                        helpers, key=lambda d: d.current_attributes.defense,
                    )
                    defender_override = helper
                    # The helper's man is now open — kickout synergy.
                    state.kickout_open = True
                    emit(
                        "defense.help_rotation",
                        actor_id=helper.hooper.id,
                        target_id=handler.hooper.id,
                        outcome="success",
                        zone=state.zone,
                    )
            state.last_event = node.name
            if "sim.event.post" in active_hooks:
                _fire_category_hook(
                    "sim.event.post", game_state, rules, rng,
                    _effects, meta_store, hooper=state.ball_handler,
                )
            next_name = _pick_next(node, True)
            continue

        if node.category == "screen":
            # Screens: screener = best-strength teammate (defense attribute
            # as the strength proxy). A later made basket credits their
            # screen_assists stat.
            handler = state.ball_handler
            teammates = [a for a in offense if a is not handler]
            if teammates:
                screener = max(
                    teammates, key=lambda t: t.current_attributes.defense,
                )
                state.screener = screener
                state.screens_set += 1
                if node.name == "screen_on_ball":
                    s_attrs = screener.current_attributes
                    branch = rng.choices(
                        ["roll", "pop", "slip"],
                        weights=[
                            max(1, s_attrs.defense),
                            max(1, s_attrs.scoring),
                            max(1, s_attrs.speed // 2),
                        ],
                        k=1,
                    )[0]
                    emit(
                        "screen.on_ball",
                        actor_id=screener.hooper.id,
                        target_id=handler.hooper.id,
                        outcome="success",
                        detail=branch,
                        zone=state.zone,
                        tags=list(node.emits_tags),
                    )
                    if branch == "roll":
                        state.next_shot_bonus += SCREEN_ROLL_SHOT_BONUS
                    elif branch == "pop":
                        state.next_shot_bonus += SCREEN_POP_SHOT_BONUS
                    else:
                        # Slip: the screener cuts early — handler hits them
                        # rolling to the rim for an open look.
                        _move_ball(
                            screener, "paint", passer=handler, open_shot=True,
                        )
                else:  # screen_off_ball
                    others = [t for t in teammates if t is not screener]
                    pending_cutter = others[0] if others else screener
                    emit(
                        "screen.off_ball",
                        actor_id=screener.hooper.id,
                        target_id=pending_cutter.hooper.id,
                        outcome="success",
                        zone=state.zone,
                        tags=list(node.emits_tags),
                    )
                if "sim.screen.post" in active_hooks:
                    _fire_category_hook(
                        "sim.screen.post", game_state, rules, rng,
                        _effects, meta_store, hooper=screener,
                    )
            state.last_event = node.name
            if "sim.event.post" in active_hooks:
                _fire_category_hook(
                    "sim.event.post", game_state, rules, rng,
                    _effects, meta_store, hooper=state.ball_handler,
                )
            next_name = _pick_next(node, True)
            continue

        if node.category == "cut":
            # Cuts: the designated cutter (from the last off-ball screen,
            # else the quickest teammate) tries to get open. Successful
            # curls/backdoors are a pass to the cutter.
            handler = state.ball_handler
            teammates = [a for a in offense if a is not handler]
            cutter: HooperState | None = None
            if (
                pending_cutter is not None
                and pending_cutter is not handler
                and not pending_cutter.ejected
            ):
                cutter = pending_cutter
            elif teammates:
                cutter = max(
                    teammates,
                    key=lambda t: t.current_attributes.speed
                    + t.current_attributes.iq,
                )
            pending_cutter = None
            cut_success = True
            if node.name in ("cut_curl", "cut_backdoor") and cutter is not None:
                cut_def = get_primary_defender(cutter, matchups, defense)
                c_attrs = cutter.current_attributes
                quickness = c_attrs.speed * 0.6 + c_attrs.iq * 0.4
                p_cut = logistic(
                    (quickness - cut_def.current_attributes.defense) + 50.0,
                    node.base_midpoint,
                    node.base_steepness,
                )
                cut_success = rng.random() < p_cut
                emit(
                    _event_type_for(node),
                    actor_id=cutter.hooper.id,
                    target_id=cut_def.hooper.id,
                    outcome="success" if cut_success else "failure",
                    zone=state.zone,
                    tags=list(node.emits_tags),
                )
                if cut_success:
                    if node.name == "cut_backdoor":
                        # High-quality look at the rim.
                        _move_ball(cutter, "paint", passer=handler, open_shot=True)
                    else:
                        _move_ball(cutter, "mid", passer=handler)
                        state.next_shot_bonus += CURL_SHOT_BONUS
                    if "sim.pass.post" in active_hooks:
                        _fire_category_hook(
                            "sim.pass.post", game_state, rules, rng,
                            _effects, meta_store, hooper=state.ball_handler,
                        )
            else:
                actor = cutter or handler
                emit(
                    _event_type_for(node),
                    actor_id=actor.hooper.id,
                    outcome="success",
                    zone=state.zone,
                    tags=list(node.emits_tags),
                )
                if node.name == "spot_up":
                    state.next_shot_bonus += SPOT_UP_SHOT_BONUS
            state.last_event = node.name
            if "sim.event.post" in active_hooks:
                _fire_category_hook(
                    "sim.event.post", game_state, rules, rng,
                    _effects, meta_store, hooper=state.ball_handler,
                )
            next_name = _pick_next(node, cut_success)
            continue

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
        if node.category == "admin":
            # Half-court set-up — real trigger for setup moves (Phase 4).
            _queue_moves(state.ball_handler, "initiate")
        elif node.category == "pass":
            _queue_moves(state.ball_handler, "pass")
            if success:
                passer = state.ball_handler
                teammates = [a for a in offense if a is not passer]
                if teammates:
                    if node.name == "pass_entry":
                        # Entry passes feed the post — weight by scoring.
                        weights = [
                            max(1, t.current_attributes.scoring)
                            for t in teammates
                        ]
                    else:
                        weights = [
                            max(
                                1,
                                t.current_attributes.scoring
                                + t.current_attributes.iq,
                            )
                            for t in teammates
                        ]
                    receiver = rng.choices(teammates, weights=weights, k=1)[0]
                    # Zone transitions: entry perimeter→paint, kickout
                    # paint→perimeter, everything else stays outside.
                    new_zone = "paint" if node.name == "pass_entry" else "perimeter"
                    open_shot = False
                    if node.name == "pass_kickout" and state.kickout_open:
                        # Help rotated — the kickout finds the open man.
                        open_shot = True
                        state.kickout_open = False
                    _move_ball(receiver, new_zone, passer=passer, open_shot=open_shot)
                    if node.name == "pass_skip":
                        state.next_shot_bonus += SKIP_PASS_SHOT_BONUS
                    elif node.name == "pass_extra":
                        state.next_shot_bonus += EXTRA_PASS_SHOT_BONUS
                    elif node.name == "pass_kickout" and not open_shot:
                        state.next_shot_bonus += KICKOUT_SHOT_BONUS
                if "sim.pass.post" in active_hooks:
                    _fire_category_hook(
                        "sim.pass.post", game_state, rules, rng,
                        _effects, meta_store, hooper=state.ball_handler,
                    )
        elif node.category == "dribble":
            state.dribble_count += 1
            putback_eligible = False
            if node.name == "iso":
                _queue_moves(state.ball_handler, "iso", defender=on_ball_defender)
            else:
                _queue_moves(state.ball_handler, "drive")
            if "drive" in node.emits_tags or node.name == "drive":
                state.ball_handler.drives += 1
                if success:
                    state.zone = "paint"
                    state.open_shot = True
            elif node.name == "crossover" and success:
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

    # Tier-2 governance scalars: final pass count, and whether this
    # possession's ending (defensive rebound) opens a transition for the
    # other team.
    game_state.pass_count_last_possession = state.pass_count
    game_state.transition_possession = ended_with_drb

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
        points_scored=pts + early_points,
        defender_id=shot_defender_id,
        assist_id=assist_id,
        rebound_id=rebound_id,
        is_offensive_rebound=False,
        move_activated=move_name,
        defensive_scheme=scheme,
        home_score=game_state.home_score,
        away_score=game_state.away_score,
        events=events,
        tags=["transition"] if opened_in_transition else [],
    )

    return PossessionResult(
        points_scored=pts + early_points,
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
