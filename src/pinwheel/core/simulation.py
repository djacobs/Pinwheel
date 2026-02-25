"""Top-level simulation engine.

simulate_game(home, away, rules, seed) -> GameResult
Pure function. No side effects, no database, no API calls.
See SIMULATION.md.

Phase 2a: simulate_game always constructs an ActionRegistry from the RuleSet.
The registry is the single code path — no more dual-path branching.

Phase 3b: simulate_game reads turn structure (quarters, clock, Elam trigger,
recovery, safety cap) from a GameDefinition instead of hardcoded values.
Basketball behavior is preserved — basketball_game_definition() produces
identical values to what was previously hardcoded.
"""

from __future__ import annotations

import logging
import math
import random
import time

from pinwheel.core.hooks import (
    GameEffect,
    HookContext,
    HookPoint,
    RegisteredEffect,
    apply_hook_results,
    fire_effects,
    fire_hooks,
)
from pinwheel.core.meta import MetaStore
from pinwheel.core.possession import PossessionResult, resolve_possession
from pinwheel.core.state import GameState, HooperState, PossessionContext
from pinwheel.models.game import GameResult, HooperBoxScore, PossessionLog, QuarterScore
from pinwheel.models.game_definition import (
    ActionRegistry,
    GameDefinition,
    basketball_actions,
    basketball_game_definition,
)
from pinwheel.models.rules import RuleSet
from pinwheel.models.team import Team, TeamStrategy

logger = logging.getLogger(__name__)


def _fire_sim_effects(
    hook: str,
    game_state: GameState,
    rules: RuleSet,
    rng: random.Random,
    effect_registry: list[RegisteredEffect] | None,
    meta_store: MetaStore | None,
) -> PossessionContext:
    """Fire new-style effects at a simulation hook point.

    Applies score/stamina modifiers immediately. Returns a PossessionContext
    with accumulated modifiers for the possession engine to consume.
    """
    empty = PossessionContext()
    if not effect_registry:
        return empty

    ctx = HookContext(
        game_state=game_state,
        rules=rules,
        rng=rng,
        meta_store=meta_store,
    )
    results = fire_effects(hook, ctx, effect_registry)
    if results:
        apply_hook_results(results, ctx)

    if not results:
        return empty

    # Accumulate possession-level modifiers from all results
    narratives: list[str] = []
    for r in results:
        if r.narrative:
            narratives.append(r.narrative)

    # Flow control: any effect that blocks wins; first substitute wins
    any_block = any(r.block_action for r in results)
    substitute = next(
        (r.substitute_action for r in results if r.substitute_action), None
    )

    # Merge all action biases into a single dict. Legacy per-field biases
    # on HookResult (at_rim_bias, mid_range_bias, three_point_bias) are
    # folded into the action_biases dict alongside any custom action names.
    merged_biases: dict[str, float] = {}
    for r in results:
        # Legacy fields → standard action names
        if r.at_rim_bias != 0.0:
            merged_biases["at_rim"] = merged_biases.get("at_rim", 0.0) + r.at_rim_bias
        if r.mid_range_bias != 0.0:
            merged_biases["mid_range"] = merged_biases.get("mid_range", 0.0) + r.mid_range_bias
        if r.three_point_bias != 0.0:
            merged_biases["three_point"] = (
                merged_biases.get("three_point", 0.0) + r.three_point_bias
            )
        # action_biases dict entries (additive, supports any action name)
        for k, v in r.action_biases.items():
            merged_biases[k] = merged_biases.get(k, 0.0) + v

    return PossessionContext(
        shot_probability_modifier=sum(r.shot_probability_modifier for r in results),
        shot_value_modifier=sum(r.shot_value_modifier for r in results),
        extra_stamina_drain=sum(r.extra_stamina_drain for r in results),
        action_biases=merged_biases,
        turnover_modifier=sum(r.turnover_modifier for r in results),
        random_ejection_probability=sum(
            r.random_ejection_probability for r in results
        ),
        bonus_pass_count=sum(r.bonus_pass_count for r in results),
        narrative_tags=narratives,
        block_action=any_block,
        substitute_action=substitute,
    )


def _build_hooper_states(team: Team) -> list[HooperState]:
    """Create mutable HooperState for each hooper on a team."""
    return [HooperState(hooper=h, on_court=h.is_starter) for h in team.hoopers]


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute distance in miles between two lat/lon points using Haversine formula."""
    r = 3958.8  # Earth radius in miles
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
        * math.sin(d_lon / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(a))


def _compute_travel_distance(home: Team, away: Team) -> float:
    """Compute travel distance in miles from away team's venue to home venue."""
    h_loc = home.venue.location
    a_loc = away.venue.location
    if len(h_loc) >= 2 and len(a_loc) >= 2:
        return _haversine_miles(a_loc[0], a_loc[1], h_loc[0], h_loc[1])
    return 0.0


def _check_substitution(
    game_state: GameState,
    rules: RuleSet,
    possession_log: list[PossessionLog],
    reason: str = "foul_out",
) -> None:
    """Check and perform substitutions for both teams.

    Two triggers:
    - foul_out: an ejected player is replaced by the best bench player
    - fatigue: at quarter breaks, swap the most fatigued active player
      with a bench player who has higher stamina
    """
    for is_home in (True, False):
        active = game_state.home_active if is_home else game_state.away_active
        bench = game_state.home_bench if is_home else game_state.away_bench

        if not bench:
            continue

        if reason == "foul_out":
            # Find ejected players who are still marked on_court
            all_agents = game_state.home_agents if is_home else game_state.away_agents
            for player in all_agents:
                if player.ejected and player.on_court:
                    # Pick best bench player by stamina
                    best_bench = max(bench, key=lambda b: b.current_stamina)
                    game_state.substitute(player, best_bench)
                    # Log substitution
                    team_id = (
                        game_state.home_agents[0].hooper.team_id
                        if is_home
                        else game_state.away_agents[0].hooper.team_id
                    )
                    log = PossessionLog(
                        quarter=game_state.quarter,
                        possession_number=game_state.possession_number,
                        offense_team_id=team_id,
                        ball_handler_id=best_bench.hooper.id,
                        action="substitution",
                        result=f"foul_out:{player.hooper.name}:{best_bench.hooper.name}",
                        home_score=game_state.home_score,
                        away_score=game_state.away_score,
                    )
                    possession_log.append(log)
                    # Refresh bench list since we just moved someone
                    bench = game_state.home_bench if is_home else game_state.away_bench
                    if not bench:
                        break

        elif reason == "fatigue":
            # Find the active player with lowest stamina
            if not active:
                continue
            worst = min(active, key=lambda a: a.current_stamina)
            # Apply strategy modifier to substitution threshold
            threshold = rules.substitution_stamina_threshold
            strategy = game_state.home_strategy if is_home else game_state.away_strategy
            if strategy:
                threshold += strategy.substitution_threshold_modifier
            if worst.current_stamina < threshold:
                best_bench = max(bench, key=lambda b: b.current_stamina)
                if best_bench.current_stamina > worst.current_stamina:
                    game_state.substitute(worst, best_bench)
                    team_id = (
                        game_state.home_agents[0].hooper.team_id
                        if is_home
                        else game_state.away_agents[0].hooper.team_id
                    )
                    log = PossessionLog(
                        quarter=game_state.quarter,
                        possession_number=game_state.possession_number,
                        offense_team_id=team_id,
                        ball_handler_id=best_bench.hooper.id,
                        action="substitution",
                        result=f"fatigue:{worst.hooper.name}:{best_bench.hooper.name}",
                        home_score=game_state.home_score,
                        away_score=game_state.away_score,
                    )
                    possession_log.append(log)


def resolve_turn(
    game_state: GameState,
    rules: RuleSet,
    rng: random.Random,
    last_three: bool = False,
    poss_ctx: PossessionContext | None = None,
    action_registry: ActionRegistry | None = None,
    game_def: GameDefinition | None = None,
) -> PossessionResult:
    """Resolve one turn of the game.

    This is the single dispatch point between the turn-structure loop
    (``_run_quarter`` / ``_run_elam``) and the possession-level engine.
    For basketball (the only game right now), it delegates directly to
    ``resolve_possession()``.

    Future game types can be dispatched here based on
    ``game_def.name`` or a ``turn_type`` field, routing to different
    resolution functions without modifying the callers.

    Args:
        game_state: Mutable game state.
        rules: Current RuleSet.
        rng: Seeded random number generator.
        last_three: Whether the previous possession ended with a made three.
        poss_ctx: Effect-derived modifiers for this possession.
        action_registry: Data-driven action definitions.
        game_def: Game structure definition (currently unused but
            threaded for future dispatch).

    Returns:
        A PossessionResult with the outcome of the turn.
    """
    return resolve_possession(
        game_state, rules, rng, last_three, poss_ctx,
        action_registry=action_registry,
    )


def _run_quarter(
    game_state: GameState,
    rules: RuleSet,
    rng: random.Random,
    effects: list[GameEffect],
    possession_log: list[PossessionLog],
    new_effects: list[RegisteredEffect] | None = None,
    meta_store: MetaStore | None = None,
    action_registry: ActionRegistry | None = None,
    game_def: GameDefinition | None = None,
) -> None:
    """Run one quarter using the game clock.

    The clock duration is read from ``game_def.quarter_clock_seconds``
    when a GameDefinition is provided, falling back to
    ``rules.quarter_minutes * 60`` otherwise.
    """
    if game_def is not None:
        game_state.game_clock_seconds = game_def.quarter_clock_seconds
    else:
        game_state.game_clock_seconds = rules.quarter_minutes * 60.0

    # Reset team fouls at the start of each quarter
    game_state.home_team_fouls = 0
    game_state.away_team_fouls = 0

    # Fire sim.quarter.pre
    _fire_sim_effects("sim.quarter.pre", game_state, rules, rng, new_effects, meta_store)

    last_three = False
    poss_num = 0
    while game_state.game_clock_seconds > 0:
        if game_state.game_over:
            break

        poss_num += 1
        game_state.possession_number = poss_num
        game_state.total_possessions += 1

        fire_hooks(HookPoint.PRE_POSSESSION, game_state, effects)
        poss_ctx = _fire_sim_effects(
            "sim.possession.pre", game_state, rules, rng, new_effects, meta_store,
        )

        result = resolve_turn(
            game_state, rules, rng, last_three, poss_ctx,
            action_registry=action_registry,
            game_def=game_def,
        )

        # Decrement game clock
        game_state.game_clock_seconds -= result.time_used

        # Format remaining time as M:SS on the possession log
        if result.log:
            remaining = max(0.0, game_state.game_clock_seconds)
            minutes = int(remaining // 60)
            seconds = int(remaining % 60)
            result.log.game_clock = f"{minutes}:{seconds:02d}"
            possession_log.append(result.log)

        # Track agent minutes
        minutes_used = result.time_used / 60.0
        for a in game_state.home_active + game_state.away_active:
            a.minutes += minutes_used

        # Track whether last possession was a made three
        last_three = result.shot_made and result.shot_type == "three_point"

        # Check for foul-out substitutions after each possession
        _check_substitution(game_state, rules, possession_log, reason="foul_out")

        # Alternate possession
        if game_def is None or game_def.alternating_possession:
            game_state.home_has_ball = not game_state.home_has_ball

        # Safety cap
        cap = game_def.safety_cap_possessions if game_def else rules.safety_cap_possessions
        if game_state.total_possessions >= cap:
            game_state.game_over = True
            break


def _run_elam(
    game_state: GameState,
    rules: RuleSet,
    rng: random.Random,
    effects: list[GameEffect],
    possession_log: list[PossessionLog],
    new_effects: list[RegisteredEffect] | None = None,
    meta_store: MetaStore | None = None,
    action_registry: ActionRegistry | None = None,
    game_def: GameDefinition | None = None,
) -> None:
    """Run the Elam Ending period.

    The Elam target margin is read from ``game_def.elam_target_margin``
    when a GameDefinition is provided, falling back to ``rules.elam_margin``.
    """
    margin = game_def.elam_target_margin if game_def else rules.elam_margin
    leading_score = max(game_state.home_score, game_state.away_score)
    game_state.elam_target_score = leading_score + margin
    game_state.elam_activated = True

    fire_hooks(HookPoint.ELAM_START, game_state, effects)
    _fire_sim_effects("sim.elam.start", game_state, rules, rng, new_effects, meta_store)

    last_three = False
    poss = 0
    while not game_state.game_over:
        poss += 1
        game_state.possession_number = poss
        game_state.total_possessions += 1

        poss_ctx = _fire_sim_effects(
            "sim.possession.pre", game_state, rules, rng, new_effects, meta_store,
        )
        result = resolve_turn(
            game_state, rules, rng, last_three, poss_ctx,
            action_registry=action_registry,
            game_def=game_def,
        )
        if result.log:
            possession_log.append(result.log)
        last_three = result.shot_made and result.shot_type == "three_point"

        # Check if target reached
        if game_state.home_score >= game_state.elam_target_score:
            game_state.game_over = True
            break
        if game_state.away_score >= game_state.elam_target_score:
            game_state.game_over = True
            break

        # Check for foul-out substitutions
        _check_substitution(game_state, rules, possession_log, reason="foul_out")

        # Alternate possession
        if game_def is None or game_def.alternating_possession:
            game_state.home_has_ball = not game_state.home_has_ball

        # Safety cap
        cap = game_def.safety_cap_possessions if game_def else rules.safety_cap_possessions
        if game_state.total_possessions >= cap:
            game_state.game_over = True
            break


def _halftime_recovery(
    game_state: GameState,
    rules: RuleSet,
    game_def: GameDefinition | None = None,
) -> None:
    """Recover stamina at halftime.

    Recovery amount is read from ``game_def.halftime_recovery`` when a
    GameDefinition is provided, falling back to
    ``rules.halftime_stamina_recovery``.
    """
    recovery = (
        game_def.halftime_recovery
        if game_def is not None
        else rules.halftime_stamina_recovery
    )
    for agent in game_state.home_agents + game_state.away_agents:
        agent.current_stamina = min(1.0, agent.current_stamina + recovery)


def _quarter_break_recovery(
    game_state: GameState,
    rules: RuleSet,
    game_def: GameDefinition | None = None,
) -> None:
    """Recover stamina between quarters (not halftime).

    Recovery amount is read from ``game_def.quarter_break_recovery``
    when a GameDefinition is provided, falling back to
    ``rules.quarter_break_stamina_recovery``.
    """
    recovery = (
        game_def.quarter_break_recovery
        if game_def is not None
        else rules.quarter_break_stamina_recovery
    )
    for agent in game_state.home_agents + game_state.away_agents:
        agent.current_stamina = min(1.0, agent.current_stamina + recovery)


def _build_box_scores(game_state: GameState) -> list[HooperBoxScore]:
    """Build box scores from hooper states."""
    box_scores = []
    for hs in game_state.home_agents + game_state.away_agents:
        # Compute plus-minus (simplified: team score diff while on court)
        is_home = hs.hooper.team_id == game_state.home_agents[0].hooper.team_id
        pm = game_state.home_score - game_state.away_score
        if not is_home:
            pm = -pm

        box_scores.append(
            HooperBoxScore(
                hooper_id=hs.hooper.id,
                hooper_name=hs.hooper.name,
                team_id=hs.hooper.team_id,
                minutes=round(hs.minutes, 1),
                points=hs.points,
                field_goals_made=hs.field_goals_made,
                field_goals_attempted=hs.field_goals_attempted,
                three_pointers_made=hs.three_pointers_made,
                three_pointers_attempted=hs.three_pointers_attempted,
                free_throws_made=hs.free_throws_made,
                free_throws_attempted=hs.free_throws_attempted,
                rebounds=hs.rebounds,
                assists=hs.assists,
                steals=hs.steals,
                turnovers=hs.turnovers,
                fouls=hs.fouls,
                plus_minus=pm,
            )
        )
    return box_scores


def simulate_game(
    home: Team,
    away: Team,
    rules: RuleSet,
    seed: int,
    game_id: str = "",
    effects: list[GameEffect] | None = None,
    home_strategy: TeamStrategy | None = None,
    away_strategy: TeamStrategy | None = None,
    effect_registry: list[RegisteredEffect] | None = None,
    meta_store: MetaStore | None = None,
    action_registry: ActionRegistry | None = None,
    game_def: GameDefinition | None = None,
) -> GameResult:
    """Simulate a complete 3v3 basketball game.

    Pure function: deterministic given inputs + seed.
    Turn structure (quarters, Elam Ending, recovery) is read from the
    ``game_def`` GameDefinition when provided. Returns immutable GameResult.

    Args:
        effect_registry: New-style effects to fire at hook points.
        meta_store: In-memory metadata store for effects to read/write.
        action_registry: Data-driven action definitions. When ``None``
            (default), a basketball registry is built automatically from
            the provided RuleSet. The registry is always used — there is
            no separate hardcoded path.
        game_def: Data-driven game structure. When ``None`` (default),
            a basketball definition is built automatically from the
            provided RuleSet.
    """
    start_time = time.monotonic()
    rng = random.Random(seed)
    _effects = effects or []

    # Always ensure we have a GameDefinition — build from rules if not provided
    if game_def is None:
        game_def = basketball_game_definition(rules)

    # Always ensure we have an ActionRegistry — build from rules if not provided
    if action_registry is None:
        action_registry = ActionRegistry(basketball_actions(rules))

    if not game_id:
        game_id = f"g-0-{seed}"

    # Build mutable state
    travel_distance = _compute_travel_distance(home, away)
    game_state = GameState(
        home_agents=_build_hooper_states(home),
        away_agents=_build_hooper_states(away),
        home_strategy=home_strategy,
        away_strategy=away_strategy,
        home_venue_altitude_ft=home.venue.altitude_ft,
        home_venue_surface=home.venue.surface,
        travel_distance_miles=travel_distance,
    )

    # Apply pre-game travel fatigue to the away team
    if rules.home_court_enabled and rules.travel_fatigue_enabled and travel_distance > 0:
        travel_penalty = travel_distance * rules.travel_fatigue_per_mile
        for agent in game_state.away_agents:
            agent.current_stamina = max(0.15, agent.current_stamina - travel_penalty)

    quarter_scores: list[QuarterScore] = []
    possession_log: list[PossessionLog] = []

    # Fire sim.game.pre for new-style effects
    _fire_sim_effects("sim.game.pre", game_state, rules, rng, effect_registry, meta_store)

    # Read turn structure from game definition
    total_quarters = game_def.quarters
    elam_quarter = game_def.elam_trigger_quarter
    halftime_q = game_def.halftime_after_quarter

    # Regular quarters (everything before the Elam quarter)
    for q in range(1, elam_quarter):
        game_state.quarter = q
        home_before = game_state.home_score
        away_before = game_state.away_score

        _run_quarter(
            game_state, rules, rng, _effects, possession_log,
            new_effects=effect_registry, meta_store=meta_store,
            action_registry=action_registry,
            game_def=game_def,
        )

        fire_hooks(HookPoint.QUARTER_END, game_state, _effects)
        _fire_sim_effects(
            "sim.quarter.end", game_state, rules, rng,
            effect_registry, meta_store,
        )

        quarter_scores.append(
            QuarterScore(
                quarter=q,
                home_score=game_state.home_score - home_before,
                away_score=game_state.away_score - away_before,
            )
        )

        # Quarter breaks: halftime after the designated quarter
        if q == halftime_q:
            _halftime_recovery(game_state, rules, game_def=game_def)
            _fire_sim_effects(
                "sim.halftime", game_state, rules, rng,
                effect_registry, meta_store,
            )
        else:
            _quarter_break_recovery(game_state, rules, game_def=game_def)

        # Fatigue-based substitution at quarter breaks
        _check_substitution(game_state, rules, possession_log, reason="fatigue")

    # Elam Ending (or final quarter if Elam is disabled)
    if not game_state.game_over and game_def.elam_ending_enabled:
        game_state.quarter = total_quarters
        home_before = game_state.home_score
        away_before = game_state.away_score

        _run_elam(
            game_state, rules, rng, _effects, possession_log,
            new_effects=effect_registry, meta_store=meta_store,
            action_registry=action_registry,
            game_def=game_def,
        )

        quarter_scores.append(
            QuarterScore(
                quarter=total_quarters,
                home_score=game_state.home_score - home_before,
                away_score=game_state.away_score - away_before,
            )
        )
    elif not game_state.game_over and not game_def.elam_ending_enabled:
        # No Elam: run the final quarter as a regular clock-based quarter
        game_state.quarter = total_quarters
        home_before = game_state.home_score
        away_before = game_state.away_score

        _run_quarter(
            game_state, rules, rng, _effects, possession_log,
            new_effects=effect_registry, meta_store=meta_store,
            action_registry=action_registry,
            game_def=game_def,
        )

        quarter_scores.append(
            QuarterScore(
                quarter=total_quarters,
                home_score=game_state.home_score - home_before,
                away_score=game_state.away_score - away_before,
            )
        )

    fire_hooks(HookPoint.GAME_END, game_state, _effects)
    _fire_sim_effects("sim.game.end", game_state, rules, rng, effect_registry, meta_store)

    # Determine winner
    winner = home.id if game_state.home_score >= game_state.away_score else away.id

    elapsed_ms = (time.monotonic() - start_time) * 1000

    result = GameResult(
        game_id=game_id,
        home_team_id=home.id,
        away_team_id=away.id,
        home_score=game_state.home_score,
        away_score=game_state.away_score,
        winner_team_id=winner,
        seed=seed,
        total_possessions=game_state.total_possessions,
        elam_activated=game_state.elam_activated,
        elam_target_score=game_state.elam_target_score,
        quarter_scores=quarter_scores,
        box_scores=_build_box_scores(game_state),
        possession_log=possession_log,
        duration_ms=elapsed_ms,
        home_strategy_summary=home_strategy.raw_text if home_strategy else "",
        away_strategy_summary=away_strategy.raw_text if away_strategy else "",
    )

    logger.info(
        "game_complete game_id=%s duration_ms=%.1f possessions=%d score=%d-%d elam=%s seed=%d",
        result.game_id,
        result.duration_ms,
        result.total_possessions,
        result.home_score,
        result.away_score,
        result.elam_activated,
        result.seed,
    )

    return result
