"""Top-level simulation engine.

simulate_game(home, away, rules, seed) → GameResult
Pure function. No side effects, no database, no API calls.
See SIMULATION.md.
"""

from __future__ import annotations

import logging
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
from pinwheel.core.possession import resolve_possession
from pinwheel.core.state import GameState, HooperState, PossessionContext
from pinwheel.models.game import GameResult, HooperBoxScore, PossessionLog, QuarterScore
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

    return PossessionContext(
        shot_probability_modifier=sum(r.shot_probability_modifier for r in results),
        shot_value_modifier=sum(r.shot_value_modifier for r in results),
        extra_stamina_drain=sum(r.extra_stamina_drain for r in results),
        at_rim_bias=sum(r.at_rim_bias for r in results),
        mid_range_bias=sum(r.mid_range_bias for r in results),
        three_point_bias=sum(r.three_point_bias for r in results),
        turnover_modifier=sum(r.turnover_modifier for r in results),
        random_ejection_probability=sum(
            r.random_ejection_probability for r in results
        ),
        bonus_pass_count=sum(r.bonus_pass_count for r in results),
        narrative_tags=narratives,
    )


def _build_hooper_states(team: Team) -> list[HooperState]:
    """Create mutable HooperState for each hooper on a team."""
    return [HooperState(hooper=h, on_court=h.is_starter) for h in team.hoopers]


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


def _run_quarter(
    game_state: GameState,
    rules: RuleSet,
    rng: random.Random,
    effects: list[GameEffect],
    possession_log: list[PossessionLog],
    new_effects: list[RegisteredEffect] | None = None,
    meta_store: MetaStore | None = None,
) -> None:
    """Run one quarter using the game clock."""
    game_state.game_clock_seconds = rules.quarter_minutes * 60.0

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

        result = resolve_possession(game_state, rules, rng, last_three, poss_ctx)

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
        game_state.home_has_ball = not game_state.home_has_ball

        # Safety cap
        if game_state.total_possessions >= rules.safety_cap_possessions:
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
) -> None:
    """Run the Elam Ending period."""
    leading_score = max(game_state.home_score, game_state.away_score)
    game_state.elam_target_score = leading_score + rules.elam_margin
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
        result = resolve_possession(game_state, rules, rng, last_three, poss_ctx)
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

        game_state.home_has_ball = not game_state.home_has_ball

        # Safety cap
        if game_state.total_possessions >= rules.safety_cap_possessions:
            game_state.game_over = True
            break


def _halftime_recovery(
    game_state: GameState,
    rules: RuleSet,
) -> None:
    """Recover stamina at halftime."""
    for agent in game_state.home_agents + game_state.away_agents:
        agent.current_stamina = min(1.0, agent.current_stamina + rules.halftime_stamina_recovery)


def _quarter_break_recovery(
    game_state: GameState,
    rules: RuleSet,
) -> None:
    """Recover stamina between quarters (not halftime)."""
    recovery = rules.quarter_break_stamina_recovery
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
) -> GameResult:
    """Simulate a complete 3v3 basketball game.

    Pure function: deterministic given inputs + seed.
    4 quarters + Elam Ending. Returns immutable GameResult.

    Args:
        effect_registry: New-style effects to fire at hook points.
        meta_store: In-memory metadata store for effects to read/write.
    """
    start_time = time.monotonic()
    rng = random.Random(seed)
    _effects = effects or []

    if not game_id:
        game_id = f"g-0-{seed}"

    # Build mutable state
    game_state = GameState(
        home_agents=_build_hooper_states(home),
        away_agents=_build_hooper_states(away),
        home_strategy=home_strategy,
        away_strategy=away_strategy,
    )

    quarter_scores: list[QuarterScore] = []
    possession_log: list[PossessionLog] = []

    # Fire sim.game.pre for new-style effects
    _fire_sim_effects("sim.game.pre", game_state, rules, rng, effect_registry, meta_store)

    # Quarters 1 through elam_trigger_quarter
    num_quarters = rules.elam_trigger_quarter + 1  # e.g., Q1-Q3 then Elam
    for q in range(1, num_quarters):
        game_state.quarter = q
        home_before = game_state.home_score
        away_before = game_state.away_score

        _run_quarter(
            game_state, rules, rng, _effects, possession_log,
            new_effects=effect_registry, meta_store=meta_store,
        )

        fire_hooks(HookPoint.QUARTER_END, game_state, _effects)
        _fire_sim_effects("sim.quarter.end", game_state, rules, rng, effect_registry, meta_store)

        quarter_scores.append(
            QuarterScore(
                quarter=q,
                home_score=game_state.home_score - home_before,
                away_score=game_state.away_score - away_before,
            )
        )

        # Quarter breaks: halftime after Q2, shorter break after Q1/Q3
        if q == 2:
            _halftime_recovery(game_state, rules)
            _fire_sim_effects(
                "sim.halftime", game_state, rules, rng, effect_registry, meta_store,
            )
        else:
            _quarter_break_recovery(game_state, rules)

        # Fatigue-based substitution at quarter breaks
        _check_substitution(game_state, rules, possession_log, reason="fatigue")

    # Elam Ending
    if not game_state.game_over:
        game_state.quarter = num_quarters
        home_before = game_state.home_score
        away_before = game_state.away_score

        _run_elam(
            game_state, rules, rng, _effects, possession_log,
            new_effects=effect_registry, meta_store=meta_store,
        )

        quarter_scores.append(
            QuarterScore(
                quarter=num_quarters,
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
