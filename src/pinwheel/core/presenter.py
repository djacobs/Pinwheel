"""Real-time presentation layer — replays pre-computed game results over wall-clock time.

The simulation engine runs instantly and produces deterministic GameResult objects.
The presenter takes those results and drips possession-by-possession events through
the EventBus so the frontend receives them in real time via SSE.

Usage:
    state = PresentationState()
    await present_round(game_results, event_bus, state, ...)
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from pinwheel.core.narrate import narrate_play
from pinwheel.models.game import GameResult

logger = logging.getLogger(__name__)


@dataclass
class PresentationState:
    """Tracks active presentation for re-entry guard and cancellation."""

    is_active: bool = False
    current_round: int = 0
    current_game_index: int = 0
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)

    def reset(self) -> None:
        """Reset state for a new presentation."""
        self.is_active = False
        self.current_round = 0
        self.current_game_index = 0
        self.cancel_event = asyncio.Event()


async def present_round(
    game_results: list[GameResult],
    event_bus: object,
    state: PresentationState,
    game_interval_seconds: int = 1800,
    quarter_replay_seconds: int = 300,
    name_cache: dict[str, str] | None = None,
    on_game_finished: Callable[[int], Awaitable[None]] | None = None,
) -> None:
    """Replay a round's games over real time via EventBus.

    Args:
        game_results: Pre-computed game results from simulation.
        event_bus: EventBus instance for publishing events.
        state: Shared PresentationState for re-entry guard.
        game_interval_seconds: Wall-clock seconds between game starts.
        quarter_replay_seconds: Wall-clock seconds to replay each quarter.
        name_cache: Mapping of entity IDs to display names (team IDs, hooper IDs).
        on_game_finished: Async callback invoked with game_index after each game finishes.
    """
    if state.is_active:
        logger.warning(
            "present_round called while presentation already active (round %d)",
            state.current_round,
        )
        return

    names = name_cache or {}
    state.is_active = True
    state.cancel_event.clear()
    games_presented = 0

    try:
        for game_idx, game_result in enumerate(game_results):
            if state.cancel_event.is_set():
                logger.info("Presentation cancelled at game %d", game_idx)
                break

            state.current_game_index = game_idx

            await event_bus.publish(
                "presentation.game_starting",
                {
                    "game_index": game_idx,
                    "total_games": len(game_results),
                    "home_team_id": game_result.home_team_id,
                    "away_team_id": game_result.away_team_id,
                    "home_team_name": names.get(game_result.home_team_id, game_result.home_team_id),
                    "away_team_name": names.get(game_result.away_team_id, game_result.away_team_id),
                },
            )

            await _present_game(game_result, event_bus, state, quarter_replay_seconds, names)

            if state.cancel_event.is_set():
                break

            await event_bus.publish(
                "presentation.game_finished",
                {
                    "game_index": game_idx,
                    "home_team_id": game_result.home_team_id,
                    "away_team_id": game_result.away_team_id,
                    "home_team_name": names.get(game_result.home_team_id, game_result.home_team_id),
                    "away_team_name": names.get(game_result.away_team_id, game_result.away_team_id),
                    "home_score": game_result.home_score,
                    "away_score": game_result.away_score,
                },
            )

            if on_game_finished is not None:
                try:
                    await on_game_finished(game_idx)
                except Exception:
                    logger.exception("on_game_finished callback failed for game %d", game_idx)

            games_presented = game_idx + 1

            # Wait between games (except after the last one)
            if game_idx < len(game_results) - 1:
                try:
                    await asyncio.wait_for(
                        state.cancel_event.wait(),
                        timeout=game_interval_seconds,
                    )
                    # If we get here, cancel was set
                    break
                except TimeoutError:
                    pass  # Normal — timeout means we proceed to next game

        await event_bus.publish(
            "presentation.round_finished",
            {"round": state.current_round, "games_presented": games_presented},
        )

    finally:
        state.is_active = False


async def _present_game(
    game_result: GameResult,
    event_bus: object,
    state: PresentationState,
    quarter_replay_seconds: int,
    names: dict[str, str],
) -> None:
    """Drip a single game's possessions over real time."""
    possessions = game_result.possession_log
    if not possessions:
        return

    # Group possessions by quarter to pace each quarter independently
    quarters: dict[int, list] = {}
    for p in possessions:
        quarters.setdefault(p.quarter, []).append(p)

    for quarter_num in sorted(quarters.keys()):
        if state.cancel_event.is_set():
            return

        quarter_possessions = quarters[quarter_num]
        if not quarter_possessions:
            continue

        # Calculate delay between possessions for this quarter
        delay = quarter_replay_seconds / max(len(quarter_possessions), 1)

        for possession in quarter_possessions:
            if state.cancel_event.is_set():
                return

            player_name = names.get(possession.ball_handler_id, possession.ball_handler_id)
            offense_name = names.get(possession.offense_team_id, possession.offense_team_id)
            defender_name = (
                names.get(possession.defender_id, possession.defender_id)
                if possession.defender_id else ""
            )

            narration = narrate_play(
                player=player_name,
                defender=defender_name,
                action=possession.action,
                result=possession.result,
                points=possession.points_scored,
                move=possession.move_activated,
                seed=possession.possession_number,
            )

            await event_bus.publish(
                "presentation.possession",
                {
                    "quarter": possession.quarter,
                    "offense_team_id": possession.offense_team_id,
                    "offense_team_name": offense_name,
                    "ball_handler_id": possession.ball_handler_id,
                    "ball_handler_name": player_name,
                    "action": possession.action,
                    "result": possession.result,
                    "points_scored": possession.points_scored,
                    "home_score": possession.home_score,
                    "away_score": possession.away_score,
                    "game_clock": possession.game_clock,
                    "narration": narration,
                },
            )

            await asyncio.sleep(delay)
