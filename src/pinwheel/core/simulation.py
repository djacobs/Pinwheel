"""Top-level simulation engine.

simulate_game(home, away, rules, seed) → GameResult
Pure function. No side effects, no database, no API calls.
See SIMULATION.md.
"""

from __future__ import annotations

import logging
import random
import time

from pinwheel.core.hooks import GameEffect, HookPoint, fire_hooks
from pinwheel.core.possession import resolve_possession
from pinwheel.core.state import AgentState, GameState
from pinwheel.models.game import AgentBoxScore, GameResult, PossessionLog, QuarterScore
from pinwheel.models.rules import RuleSet
from pinwheel.models.team import Team

logger = logging.getLogger(__name__)


def _build_agent_states(team: Team) -> list[AgentState]:
    """Create mutable AgentState for each agent on a team."""
    return [AgentState(agent=agent) for agent in team.agents]


def _run_quarter(
    game_state: GameState,
    rules: RuleSet,
    rng: random.Random,
    effects: list[GameEffect],
    possession_log: list[PossessionLog],
) -> None:
    """Run one quarter using the game clock."""
    game_state.game_clock_seconds = rules.quarter_minutes * 60.0
    last_three = False
    poss_num = 0
    while game_state.game_clock_seconds > 0:
        if game_state.game_over:
            break

        poss_num += 1
        game_state.possession_number = poss_num
        game_state.total_possessions += 1

        fire_hooks(HookPoint.PRE_POSSESSION, game_state, effects)

        result = resolve_possession(game_state, rules, rng, last_three)

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
        for a in game_state.home_starters + game_state.away_starters:
            a.minutes += minutes_used

        # Track whether last possession was a made three
        last_three = result.shot_made and result.shot_type == "three_point"

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
) -> None:
    """Run the Elam Ending period."""
    leading_score = max(game_state.home_score, game_state.away_score)
    game_state.elam_target_score = leading_score + rules.elam_margin
    game_state.elam_activated = True

    fire_hooks(HookPoint.ELAM_START, game_state, effects)

    last_three = False
    poss = 0
    while not game_state.game_over:
        poss += 1
        game_state.possession_number = poss
        game_state.total_possessions += 1

        result = resolve_possession(game_state, rules, rng, last_three)
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


def _build_box_scores(game_state: GameState) -> list[AgentBoxScore]:
    """Build box scores from agent states."""
    box_scores = []
    for agent_state in game_state.home_agents + game_state.away_agents:
        # Compute plus-minus (simplified: team score diff while on court)
        is_home = agent_state.agent.team_id == game_state.home_agents[0].agent.team_id
        pm = game_state.home_score - game_state.away_score
        if not is_home:
            pm = -pm

        box_scores.append(
            AgentBoxScore(
                agent_id=agent_state.agent.id,
                agent_name=agent_state.agent.name,
                team_id=agent_state.agent.team_id,
                minutes=round(agent_state.minutes, 1),
                points=agent_state.points,
                field_goals_made=agent_state.field_goals_made,
                field_goals_attempted=agent_state.field_goals_attempted,
                three_pointers_made=agent_state.three_pointers_made,
                three_pointers_attempted=agent_state.three_pointers_attempted,
                free_throws_made=agent_state.free_throws_made,
                free_throws_attempted=agent_state.free_throws_attempted,
                rebounds=agent_state.rebounds,
                assists=agent_state.assists,
                steals=agent_state.steals,
                turnovers=agent_state.turnovers,
                fouls=agent_state.fouls,
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
) -> GameResult:
    """Simulate a complete 3v3 basketball game.

    Pure function: deterministic given inputs + seed.
    4 quarters + Elam Ending. Returns immutable GameResult.
    """
    start_time = time.monotonic()
    rng = random.Random(seed)
    _effects = effects or []

    if not game_id:
        game_id = f"g-0-{seed}"

    # Build mutable state
    game_state = GameState(
        home_agents=_build_agent_states(home),
        away_agents=_build_agent_states(away),
    )

    quarter_scores: list[QuarterScore] = []
    possession_log: list[PossessionLog] = []

    # Quarters 1 through elam_trigger_quarter
    num_quarters = rules.elam_trigger_quarter + 1  # e.g., Q1-Q3 then Elam
    for q in range(1, num_quarters):
        game_state.quarter = q
        home_before = game_state.home_score
        away_before = game_state.away_score

        _run_quarter(game_state, rules, rng, _effects, possession_log)

        fire_hooks(HookPoint.QUARTER_END, game_state, _effects)

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
        else:
            _quarter_break_recovery(game_state, rules)

    # Elam Ending
    if not game_state.game_over:
        game_state.quarter = num_quarters
        home_before = game_state.home_score
        away_before = game_state.away_score

        _run_elam(game_state, rules, rng, _effects, possession_log)

        quarter_scores.append(
            QuarterScore(
                quarter=num_quarters,
                home_score=game_state.home_score - home_before,
                away_score=game_state.away_score - away_before,
            )
        )

    fire_hooks(HookPoint.GAME_END, game_state, _effects)

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
