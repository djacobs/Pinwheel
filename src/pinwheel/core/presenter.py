"""Real-time presentation layer — replays pre-computed game results over wall-clock time.

The simulation engine runs instantly and produces deterministic GameResult objects.
The presenter takes those results and drips possession-by-possession events through
the EventBus so the frontend receives them in real time via SSE.

All games in a round run concurrently — the arena shows them side by side.

The presenter also writes running state to ``PresentationState.live_games`` so
the arena page can server-render current scores on every page load — no gap
after a reload or deploy.

Usage:
    state = PresentationState()
    await present_round(game_results, event_bus, state, ...)
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from pinwheel.core.drama import DramaAnnotation, annotate_drama, get_drama_summary, normalize_delays
from pinwheel.core.narrate import narrate_play
from pinwheel.models.game import GameResult

logger = logging.getLogger(__name__)


@dataclass
class LiveGameState:
    """Running state for a single live game — everything the template needs."""

    game_index: int
    game_id: str
    home_team_id: str
    away_team_id: str
    home_team_name: str
    away_team_name: str
    home_team_color: str = "#888"
    home_team_color2: str = "#1a1a2e"
    away_team_color: str = "#888"
    away_team_color2: str = "#1a1a2e"
    home_score: int = 0
    away_score: int = 0
    quarter: int = 1
    game_clock: str = ""
    status: str = "live"  # "live" | "final"
    recent_plays: list[dict] = field(default_factory=list)
    box_scores: list[dict] = field(default_factory=list)
    elam_target: int | None = None
    home_leader: dict | None = None
    away_leader: dict | None = None


@dataclass
class PresentationState:
    """Tracks active presentation for re-entry guard and cancellation."""

    is_active: bool = False
    current_round: int = 0
    current_game_index: int = 0
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    live_games: dict[int, LiveGameState] = field(default_factory=dict)
    game_results: list[GameResult] = field(default_factory=list)
    game_summaries: list[dict] = field(default_factory=list)
    name_cache: dict[str, str] = field(default_factory=dict)
    color_cache: dict[str, tuple[str, str]] = field(default_factory=dict)

    def reset(self) -> None:
        """Reset state for a new presentation."""
        self.is_active = False
        self.current_round = 0
        self.current_game_index = 0
        self.cancel_event = asyncio.Event()
        self.live_games = {}
        self.game_results = []
        self.game_summaries = []
        self.name_cache = {}
        self.color_cache = {}


async def present_round(
    game_results: list[GameResult],
    event_bus: object,
    state: PresentationState,
    game_interval_seconds: int = 0,
    quarter_replay_seconds: int = 300,
    name_cache: dict[str, str] | None = None,
    color_cache: dict[str, tuple[str, str]] | None = None,
    on_game_finished: Callable[[int], Awaitable[None]] | None = None,
    game_summaries: list[dict] | None = None,
    skip_quarters: int = 0,
) -> None:
    """Replay a round's games concurrently over real time via EventBus.

    All games in a round start simultaneously and stream possessions in
    parallel.  No team plays twice in a single round, so concurrent
    presentation is physically correct.  The *spacing* between rounds is
    handled by the APScheduler cron — not by this function.

    Args:
        game_results: Pre-computed game results from simulation.
        event_bus: EventBus instance for publishing events.
        state: Shared PresentationState for re-entry guard.
        game_interval_seconds: Kept for API compat; ignored. Round spacing
            is controlled by the cron schedule.
        quarter_replay_seconds: Wall-clock seconds to replay each quarter.
        name_cache: Mapping of entity IDs to display names (team IDs, hooper IDs).
        color_cache: Mapping of team IDs to (primary_color, secondary_color) tuples.
        on_game_finished: Async callback invoked with game_index after each game finishes.
        game_summaries: Game summary dicts from step_round (for Discord notifications).
        skip_quarters: Number of quarters to fast-forward through (for resume after deploy).
    """
    if state.is_active:
        logger.warning(
            "present_round called while presentation already active (round %d)",
            state.current_round,
        )
        return

    names = name_cache or {}
    colors = color_cache or {}
    state.is_active = True
    state.cancel_event.clear()
    state.game_results = list(game_results)
    state.game_summaries = list(game_summaries or [])
    state.name_cache = dict(names)
    state.color_cache = dict(colors)
    state.live_games = {}

    try:
        tasks = [
            _present_full_game(
                game_idx=idx,
                game_result=gr,
                total_games=len(game_results),
                event_bus=event_bus,
                state=state,
                quarter_replay_seconds=quarter_replay_seconds,
                names=names,
                colors=colors,
                on_game_finished=on_game_finished,
                skip_quarters=skip_quarters,
            )
            for idx, gr in enumerate(game_results)
        ]
        await asyncio.gather(*tasks)

        # Derive playoff_context from game summaries for the round event
        _round_pc: str | None = None
        if state.game_summaries:
            _round_pc = state.game_summaries[0].get("playoff_context")
            if _round_pc:
                _round_pc = str(_round_pc)

        await event_bus.publish(
            "presentation.round_finished",
            {
                "round": state.current_round,
                "games_presented": len(game_results),
                "playoff_context": _round_pc,
            },
        )

    finally:
        state.is_active = False


def _compute_leaders(
    game_result: GameResult, names: dict[str, str]
) -> tuple[dict | None, dict | None]:
    """Return top scorer per team as (home_leader, away_leader) dicts."""
    home_best: dict | None = None
    away_best: dict | None = None
    for bs in game_result.box_scores:
        entry = {
            "hooper_id": bs.hooper_id,
            "hooper_name": names.get(bs.hooper_id, bs.hooper_name),
            "points": bs.points,
        }
        if bs.team_id == game_result.home_team_id:
            if home_best is None or bs.points > home_best["points"]:
                home_best = entry
        else:
            if away_best is None or bs.points > away_best["points"]:
                away_best = entry
    return home_best, away_best


async def _present_full_game(
    game_idx: int,
    game_result: GameResult,
    total_games: int,
    event_bus: object,
    state: PresentationState,
    quarter_replay_seconds: int,
    names: dict[str, str],
    colors: dict[str, tuple[str, str]],
    on_game_finished: Callable[[int], Awaitable[None]] | None,
    skip_quarters: int = 0,
) -> None:
    """Present a single game: starting event → possessions → finished event."""
    if state.cancel_event.is_set():
        return

    home_name = names.get(game_result.home_team_id, game_result.home_team_id)
    away_name = names.get(game_result.away_team_id, game_result.away_team_id)
    home_colors = colors.get(game_result.home_team_id, ("#888", "#1a1a2e"))
    away_colors = colors.get(game_result.away_team_id, ("#888", "#1a1a2e"))

    # Create LiveGameState entry so the arena can server-render mid-game
    live = LiveGameState(
        game_index=game_idx,
        game_id=game_result.game_id,
        home_team_id=game_result.home_team_id,
        away_team_id=game_result.away_team_id,
        home_team_name=home_name,
        away_team_name=away_name,
        home_team_color=home_colors[0],
        home_team_color2=home_colors[1],
        away_team_color=away_colors[0],
        away_team_color2=away_colors[1],
    )
    live.elam_target = game_result.elam_target_score
    state.live_games[game_idx] = live

    # Extract playoff_context from game_summaries if available
    _playoff_context: str | None = None
    if game_idx < len(state.game_summaries):
        _pc = state.game_summaries[game_idx].get("playoff_context")
        if _pc:
            _playoff_context = str(_pc)

    await event_bus.publish(
        "presentation.game_starting",
        {
            "game_index": game_idx,
            "total_games": total_games,
            "home_team_id": game_result.home_team_id,
            "away_team_id": game_result.away_team_id,
            "home_team_name": home_name,
            "away_team_name": away_name,
            "home_team_color": home_colors[0],
            "home_team_color2": home_colors[1],
            "away_team_color": away_colors[0],
            "away_team_color2": away_colors[1],
            "playoff_context": _playoff_context,
        },
    )

    await _present_game(
        game_idx,
        game_result,
        event_bus,
        state,
        quarter_replay_seconds,
        names,
        colors,
        skip_quarters=skip_quarters,
    )

    if state.cancel_event.is_set():
        return

    # Compute leaders from pre-computed box scores
    home_leader, away_leader = _compute_leaders(game_result, names)
    live.status = "final"
    live.home_leader = home_leader
    live.away_leader = away_leader

    # Merge game summary data (commentary, winner, etc.) into the event
    finished_data: dict = {
        "game_index": game_idx,
        "home_team_id": game_result.home_team_id,
        "away_team_id": game_result.away_team_id,
        "home_team_name": home_name,
        "away_team_name": away_name,
        "home_score": game_result.home_score,
        "away_score": game_result.away_score,
        "home_leader": home_leader,
        "away_leader": away_leader,
    }
    # Attach game summary fields so Discord gets full context
    if game_idx < len(state.game_summaries):
        summary = state.game_summaries[game_idx]
        finished_data.update(
            {
                "home_team": summary.get("home_team", home_name),
                "away_team": summary.get("away_team", away_name),
                "winner_team_id": summary.get("winner_team_id", ""),
                "elam_activated": summary.get("elam_activated", False),
                "total_possessions": summary.get("total_possessions", 0),
                "commentary": summary.get("commentary", ""),
                "playoff_context": summary.get("playoff_context"),
            }
        )

    await event_bus.publish("presentation.game_finished", finished_data)

    if on_game_finished is not None:
        try:
            await on_game_finished(game_idx)
        except Exception:
            logger.exception("on_game_finished callback failed for game %d", game_idx)


async def _present_game(
    game_idx: int,
    game_result: GameResult,
    event_bus: object,
    state: PresentationState,
    quarter_replay_seconds: int,
    names: dict[str, str],
    colors: dict[str, tuple[str, str]],
    skip_quarters: int = 0,
) -> None:
    """Drip a single game's possessions over real time with dramatic pacing.

    Pre-annotates the full game with ``annotate_drama()`` and uses per-possession
    delays instead of a flat delay. Dramatic moments get more time, routine
    moments get less, but the total quarter duration stays the same.

    SSE events are enriched with ``drama_level`` and ``drama_tags`` so the
    frontend can apply visual treatments. Before "peak" possessions, a
    ``presentation.suspense`` event is emitted so the frontend can show a
    visual buildup during the pause.
    """
    possessions = game_result.possession_log
    if not possessions:
        return

    # Pre-annotate the full game for dramatic pacing
    annotations = annotate_drama(game_result)
    annotation_map = {a.possession_index: a for a in annotations}

    drama_summary = get_drama_summary(annotations)
    logger.info(
        "drama_annotated game=%s possessions=%d routine=%d elevated=%d high=%d peak=%d",
        game_result.game_id,
        len(annotations),
        drama_summary.get("routine", 0),
        drama_summary.get("elevated", 0),
        drama_summary.get("high", 0),
        drama_summary.get("peak", 0),
    )

    # Build a global index for each possession (order in the full log)
    possession_global_indices: dict[int, int] = {}
    for global_idx, p in enumerate(possessions):
        possession_global_indices[id(p)] = global_idx

    # Group possessions by quarter to pace each quarter independently
    quarters: dict[int, list] = {}
    for p in possessions:
        quarters.setdefault(p.quarter, []).append(p)

    sorted_quarter_nums = sorted(quarters.keys())

    # Fast-forward through skipped quarters (deploy resume)
    if skip_quarters > 0:
        skipped = sorted_quarter_nums[:skip_quarters]
        sorted_quarter_nums = sorted_quarter_nums[skip_quarters:]
        # Update LiveGameState with the final scores from skipped quarters
        for qn in skipped:
            quarter_poss = quarters[qn]
            if quarter_poss:
                last_p = quarter_poss[-1]
                live = state.live_games.get(game_idx)
                if live is not None:
                    live.home_score = last_p.home_score
                    live.away_score = last_p.away_score
                    live.quarter = last_p.quarter
        if sorted_quarter_nums:
            logger.info(
                "resume: game %d skipped %d quarters, resuming at Q%d",
                game_idx,
                skip_quarters,
                sorted_quarter_nums[0],
            )
        else:
            logger.info(
                "resume: game %d skipped all quarters (game already finished)",
                game_idx,
            )

    for quarter_num in sorted_quarter_nums:
        if state.cancel_event.is_set():
            return

        quarter_possessions = quarters[quarter_num]
        if not quarter_possessions:
            continue

        # Get annotations for this quarter's possessions and compute delays
        quarter_annotations: list[DramaAnnotation] = []
        for p in quarter_possessions:
            global_idx = possession_global_indices[id(p)]
            ann = annotation_map.get(global_idx)
            if ann:
                quarter_annotations.append(ann)
            else:
                quarter_annotations.append(
                    DramaAnnotation(
                        possession_index=global_idx,
                        level="routine",
                        tags=[],
                        delay_multiplier=1.0,
                    )
                )

        delays = normalize_delays(quarter_annotations, quarter_replay_seconds)
        # Compute the base delay for suspense threshold comparison
        base_delay = quarter_replay_seconds / max(len(quarter_possessions), 1)

        for i, possession in enumerate(quarter_possessions):
            if state.cancel_event.is_set():
                return

            ann = quarter_annotations[i]

            player_name = names.get(possession.ball_handler_id, possession.ball_handler_id)
            offense_name = names.get(possession.offense_team_id, possession.offense_team_id)
            defender_name = (
                names.get(possession.defender_id, possession.defender_id)
                if possession.defender_id
                else ""
            )
            rebounder_name = (
                names.get(possession.rebound_id, possession.rebound_id)
                if possession.rebound_id
                else ""
            )

            narration = narrate_play(
                player=player_name,
                defender=defender_name,
                action=possession.action,
                result=possession.result,
                points=possession.points_scored,
                move=possession.move_activated,
                rebounder=rebounder_name,
                is_offensive_rebound=possession.is_offensive_rebound,
                seed=possession.possession_number,
                assist_id=possession.assist_id,
            )

            # During Elam ending, show target score instead of empty clock
            elam_target = game_result.elam_target_score
            game_clock = possession.game_clock
            if not game_clock and elam_target:
                game_clock = f"Target score: {elam_target}"

            play_dict: dict = {
                "game_index": game_idx,
                "quarter": possession.quarter,
                "offense_team_id": possession.offense_team_id,
                "offense_team_name": offense_name,
                "offense_color": colors.get(possession.offense_team_id, ("#888",))[0],
                "ball_handler_id": possession.ball_handler_id,
                "ball_handler_name": player_name,
                "action": possession.action,
                "result": possession.result,
                "points_scored": possession.points_scored,
                "home_score": possession.home_score,
                "away_score": possession.away_score,
                "game_clock": game_clock,
                "elam_target": elam_target,
                "narration": narration,
                "drama_level": ann.level,
                "drama_tags": ann.tags,
            }

            # Emit suspense event before peak possessions with long pauses
            if ann.level == "peak" and delays[i] > base_delay * 1.5:
                await event_bus.publish(
                    "presentation.suspense",
                    {
                        "game_index": game_idx,
                        "drama_tags": ann.tags,
                        "seconds_until_play": round(delays[i], 1),
                    },
                )

            # Update LiveGameState so server-render stays current
            live = state.live_games.get(game_idx)
            if live is not None:
                live.home_score = possession.home_score
                live.away_score = possession.away_score
                live.quarter = possession.quarter
                live.game_clock = game_clock
                live.recent_plays.append(play_dict)
                # Keep only last 30 plays in memory
                if len(live.recent_plays) > 30:
                    live.recent_plays = live.recent_plays[-30:]

            await event_bus.publish("presentation.possession", play_dict)

            await asyncio.sleep(delays[i])
