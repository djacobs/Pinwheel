"""Scheduled game-round advancement.

Provides ``tick_round`` which is invoked by APScheduler on the cron cadence
defined by ``settings.pinwheel_game_cron``.  Each tick finds the active season,
determines the next round number, and calls ``step_round`` to execute it.

Errors are logged but never propagated so the scheduler keeps running.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from pinwheel.core.event_bus import EventBus
from pinwheel.core.game_loop import step_round_multisession, tally_pending_governance
from pinwheel.core.presenter import PresentationState, present_round
from pinwheel.db.engine import get_session
from pinwheel.db.models import BotStateRow, GameResultRow
from pinwheel.db.repository import Repository

logger = logging.getLogger(__name__)


PRESENTATION_STATE_KEY = "presentation_active"
TICK_LOCK_KEY = "tick_round_lock"
TICK_LOCK_TIMEOUT_SECONDS = 300  # 5 minutes — stale lock recovery

_MACHINE_ID: str = os.environ.get("FLY_MACHINE_ID", "") or str(uuid4())


async def _try_acquire_tick_lock(engine: AsyncEngine, machine_id: str) -> bool:
    """Atomically try to claim the tick_round lock. Returns True if acquired."""
    async with get_session(engine) as session:
        repo = Repository(session)
        existing = await repo.get_bot_state(TICK_LOCK_KEY)
        if existing:
            data = json.loads(existing)
            age = time.time() - data.get("acquired_at", 0)
            if age < TICK_LOCK_TIMEOUT_SECONDS:
                return False  # Lock held by another instance, not stale
            # Stale lock — take it over
            logger.warning(
                "tick_lock_stale age=%.0fs old_machine=%s",
                age,
                data.get("machine_id", "unknown"),
            )
        await repo.set_bot_state(
            TICK_LOCK_KEY,
            json.dumps({"machine_id": machine_id, "acquired_at": time.time()}),
        )
        return True


async def _release_tick_lock(engine: AsyncEngine, machine_id: str) -> None:
    """Release the lock only if we still own it."""
    async with get_session(engine) as session:
        existing_val = await Repository(session).get_bot_state(TICK_LOCK_KEY)
        if existing_val:
            data = json.loads(existing_val)
            if data.get("machine_id") == machine_id:
                row = await session.get(BotStateRow, TICK_LOCK_KEY)
                if row:
                    await session.delete(row)
                    await session.flush()


async def _persist_presentation_start(
    engine: AsyncEngine,
    season_id: str,
    round_number: int,
    game_row_ids: list[str],
    quarter_replay_seconds: int,
) -> None:
    """Store presentation metadata in DB so it survives a deploy."""
    data = json.dumps(
        {
            "season_id": season_id,
            "round_number": round_number,
            "started_at": datetime.now(UTC).isoformat(),
            "game_row_ids": game_row_ids,
            "quarter_replay_seconds": quarter_replay_seconds,
        }
    )
    async with get_session(engine) as session:
        repo = Repository(session)
        await repo.set_bot_state(PRESENTATION_STATE_KEY, data)


async def _clear_presentation_state(engine: AsyncEngine) -> None:
    """Remove the presentation_active flag from DB."""
    async with get_session(engine) as session:
        repo = Repository(session)
        existing = await repo.get_bot_state(PRESENTATION_STATE_KEY)
        if existing is not None:
            from pinwheel.db.models import BotStateRow

            row = await session.get(BotStateRow, PRESENTATION_STATE_KEY)
            if row is not None:
                await session.delete(row)
                await session.flush()


def _build_name_cache(teams_cache: dict) -> dict[str, str]:
    """Build a flat {id: name} mapping from teams_cache for the presenter."""
    names: dict[str, str] = {}
    for team in teams_cache.values():
        names[team.id] = team.name
        for hooper in team.hoopers:
            names[hooper.id] = hooper.name
    return names


def _build_color_cache(teams_cache: dict) -> dict[str, tuple[str, str]]:
    """Build a {team_id: (primary, secondary)} mapping from teams_cache."""
    colors: dict[str, tuple[str, str]] = {}
    for team in teams_cache.values():
        colors[team.id] = (
            getattr(team, "color", "#888") or "#888",
            getattr(team, "color_secondary", "#1a1a2e") or "#1a1a2e",
        )
    return colors


async def _present_and_clear(
    engine: AsyncEngine,
    game_results: list,
    event_bus: EventBus,
    state: PresentationState,
    game_interval_seconds: int = 0,
    quarter_replay_seconds: int = 300,
    name_cache: dict[str, str] | None = None,
    color_cache: dict[str, tuple[str, str]] | None = None,
    on_game_finished: object = None,
    game_summaries: list[dict] | None = None,
    skip_quarters: int = 0,
    governance_summary: dict | None = None,
    report_events: list[dict] | None = None,
    deferred_season_events: list[tuple[str, dict]] | None = None,
) -> None:
    """Wrapper: run present_round, then clear the persisted state flag."""
    try:
        await present_round(
            game_results=game_results,
            event_bus=event_bus,
            state=state,
            game_interval_seconds=game_interval_seconds,
            quarter_replay_seconds=quarter_replay_seconds,
            name_cache=name_cache,
            color_cache=color_cache,
            on_game_finished=on_game_finished,
            game_summaries=game_summaries,
            skip_quarters=skip_quarters,
        )
    finally:
        # Publish deferred report events after presentation finishes
        for rev in report_events or []:
            await event_bus.publish("report.generated", rev)

        # Publish governance notification after presentation finishes
        if governance_summary:
            await event_bus.publish(
                "governance.window_closed",
                governance_summary,
            )

        # Publish deferred season events after presentation finishes
        # (championship, playoffs_complete, regular_season_complete, etc.)
        for event_type, event_data in deferred_season_events or []:
            await event_bus.publish(event_type, event_data)

        await _clear_presentation_state(engine)
        logger.info("presentation_state_cleared")


async def resume_presentation(
    engine: AsyncEngine,
    event_bus: EventBus,
    presentation_state: PresentationState,
    quarter_replay_seconds: int = 300,
) -> bool:
    """Check for an interrupted presentation and resume it if found.

    Called during app startup.  Reads the ``presentation_active`` key from
    BotStateRow, calculates how many quarters elapsed since the presentation
    started, reconstructs GameResult objects from the DB, and launches
    ``present_round`` with ``skip_quarters``.

    Returns True if a presentation was resumed, False otherwise.
    """
    from pinwheel.models.game import GameResult, HooperBoxScore, PossessionLog, QuarterScore

    async with get_session(engine) as session:
        repo = Repository(session)
        raw = await repo.get_bot_state(PRESENTATION_STATE_KEY)
        if raw is None:
            return False

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("resume: invalid presentation_active JSON, clearing")
            await _clear_presentation_state(engine)
            return False

        season_id = data["season_id"]
        round_number = data["round_number"]
        game_row_ids = data["game_row_ids"]
        stored_qrs = data.get("quarter_replay_seconds", quarter_replay_seconds)
        started_at = datetime.fromisoformat(data["started_at"])

        # Calculate how many quarters to skip
        elapsed = (datetime.now(UTC) - started_at).total_seconds()
        skip_quarters = int(elapsed // stored_qrs) if stored_qrs > 0 else 0

        logger.info(
            "resume: found interrupted presentation round=%d elapsed=%.0fs skip_quarters=%d",
            round_number,
            elapsed,
            skip_quarters,
        )

        # Reconstruct GameResult objects from DB rows
        game_results: list[GameResult] = []
        for gid in game_row_ids:
            row = await repo.get_game_result(gid)
            if row is None:
                logger.warning("resume: game %s not found, skipping", gid)
                continue

            # Rebuild possession log from stored JSON
            possession_log = []
            if row.play_by_play:
                for p in row.play_by_play:
                    possession_log.append(PossessionLog(**p))

            # Rebuild quarter scores
            quarter_scores = []
            if row.quarter_scores:
                for qs in row.quarter_scores:
                    quarter_scores.append(QuarterScore(**qs))

            # Rebuild box scores
            box_scores = []
            for bs in row.box_scores:
                box_scores.append(
                    HooperBoxScore(
                        hooper_id=bs.hooper_id,
                        hooper_name="",  # Will be filled from name_cache
                        team_id=bs.team_id,
                        points=bs.points,
                        field_goals_made=bs.field_goals_made,
                        field_goals_attempted=bs.field_goals_attempted,
                        three_pointers_made=bs.three_pointers_made,
                        three_pointers_attempted=bs.three_pointers_attempted,
                        free_throws_made=bs.free_throws_made,
                        free_throws_attempted=bs.free_throws_attempted,
                        assists=bs.assists,
                        steals=bs.steals,
                        turnovers=bs.turnovers,
                        minutes=bs.minutes,
                    )
                )

            game_results.append(
                GameResult(
                    game_id=row.id,
                    home_team_id=row.home_team_id,
                    away_team_id=row.away_team_id,
                    home_score=row.home_score,
                    away_score=row.away_score,
                    winner_team_id=row.winner_team_id,
                    seed=row.seed,
                    total_possessions=row.total_possessions,
                    elam_activated=row.elam_target is not None,
                    elam_target_score=row.elam_target,
                    quarter_scores=quarter_scores,
                    box_scores=box_scores,
                    possession_log=possession_log,
                )
            )

        if not game_results:
            logger.warning("resume: no game results reconstructed, clearing state")
            await _clear_presentation_state(engine)
            return False

        # Build name + color caches from team data
        teams = await repo.get_teams_for_season(season_id)
        name_cache: dict[str, str] = {}
        color_cache: dict[str, tuple[str, str]] = {}
        for t in teams:
            name_cache[t.id] = t.name
            color_cache[t.id] = (t.color or "#888", t.color_secondary or "#1a1a2e")
            for h in t.hoopers:
                name_cache[h.id] = h.name

        # Defensive fallback: look up any game team IDs not covered by season query.
        # This guards against cross-season schedule entries or data anomalies.
        all_game_team_ids = {
            tid
            for gr in game_results
            for tid in (gr.home_team_id, gr.away_team_id)
        }
        for tid in all_game_team_ids - set(name_cache.keys()):
            t = await repo.get_team(tid)
            if t:
                name_cache[t.id] = t.name
                color_cache[t.id] = (t.color or "#888", t.color_secondary or "#1a1a2e")
                for h in t.hoopers:
                    name_cache[h.id] = h.name
                logger.warning(
                    "resume: team %s not in season %s — loaded directly",
                    tid,
                    season_id,
                )

        # Fill in hooper_name on box scores now that we have the name cache
        for gr in game_results:
            for bs in gr.box_scores:
                if not bs.hooper_name and bs.hooper_id in name_cache:
                    bs.hooper_name = name_cache[bs.hooper_id]

        # Build game summaries for Discord (minimal — just what embeds need)
        game_summaries: list[dict] = []
        for gr in game_results:
            game_summaries.append(
                {
                    "home_team": name_cache.get(gr.home_team_id, gr.home_team_id),
                    "away_team": name_cache.get(gr.away_team_id, gr.away_team_id),
                    "home_team_name": name_cache.get(gr.home_team_id, gr.home_team_id),
                    "away_team_name": name_cache.get(gr.away_team_id, gr.away_team_id),
                    "home_score": gr.home_score,
                    "away_score": gr.away_score,
                    "winner_team_id": gr.winner_team_id,
                    "elam_activated": gr.elam_activated,
                    "total_possessions": gr.total_possessions,
                    "commentary": "",
                }
            )

    # Set up presentation state
    presentation_state.current_round = round_number

    # Create callback to mark games as presented
    async def mark_presented(game_index: int) -> None:
        async with get_session(engine) as mark_session:
            mark_repo = Repository(mark_session)
            if game_index < len(game_row_ids):
                await mark_repo.mark_game_presented(game_row_ids[game_index])

    asyncio.create_task(
        _present_and_clear(
            engine=engine,
            game_results=game_results,
            event_bus=event_bus,
            state=presentation_state,
            quarter_replay_seconds=stored_qrs,
            name_cache=name_cache,
            color_cache=color_cache,
            on_game_finished=mark_presented,
            game_summaries=game_summaries,
            skip_quarters=skip_quarters,
        )
    )

    logger.info(
        "resume: presentation restarted round=%d skip_quarters=%d games=%d",
        round_number,
        skip_quarters,
        len(game_results),
    )
    return True


async def tick_round(
    engine: AsyncEngine,
    event_bus: EventBus,
    api_key: str = "",
    presentation_state: PresentationState | None = None,
    presentation_mode: str = "instant",
    game_interval_seconds: int = 1800,
    quarter_replay_seconds: int = 300,
    governance_interval: int = 1,
) -> None:
    """Advance the active season by one round.

    * Finds the first season in the database.
    * Determines the next round number from max(round_number) of existing
      game results + 1.
    * Calls ``step_round`` to execute simulation, governance, reports, and evals.
    * Commits on success; rolls back on error.

    If no season exists the tick is silently skipped.
    All exceptions are caught and logged so the scheduler is never interrupted.
    """
    # Skip if a presentation is still playing — avoids piling up unseen rounds
    if presentation_state is not None and presentation_state.is_active:
        logger.info("tick_round_skip: presentation still active")
        return

    # Distributed lock: only one Fly.io machine executes tick_round at a time
    machine_id = _MACHINE_ID
    lock_acquired = False
    try:
        lock_acquired = await _try_acquire_tick_lock(engine, machine_id)
    except SQLAlchemyError:
        logger.exception("tick_round_lock_acquire_error")
        return

    if not lock_acquired:
        logger.info("tick_round_skip: lock held by another instance")
        return

    try:
        round_result = None
        season_id = ""
        next_round = 0

        # --- Pre-flight session: determine season state + next round number ---
        async with get_session(engine) as session:
            repo = Repository(session)

            # Find active season
            season = await repo.get_active_season()
            if season is None:
                logger.info("tick_round_skip: no active season")
                return

            # Championship phase: check if window expired, then transition to OFFSEASON
            if season.status == "championship":
                config = season.config or {}
                ends_at_str = config.get("championship_ends_at")
                if ends_at_str:
                    ends_at = datetime.fromisoformat(ends_at_str)
                    if datetime.now(UTC) >= ends_at:
                        from pinwheel.core.season import enter_offseason

                        try:
                            from pinwheel.config import Settings

                            offseason_window = Settings().pinwheel_offseason_window
                        except (ValidationError, ValueError, TypeError):
                            offseason_window = 3600

                        await enter_offseason(
                            repo,
                            season.id,
                            duration_seconds=offseason_window,
                            event_bus=event_bus,
                        )
                        logger.info(
                            "championship_window_expired season=%s -> offseason",
                            season.id,
                        )
                    else:
                        logger.info(
                            "tick_round_skip: championship window still open "
                            "season=%s ends_at=%s",
                            season.id,
                            ends_at_str,
                        )
                else:
                    # No ends_at configured — transition to offseason immediately
                    from pinwheel.core.season import enter_offseason

                    try:
                        from pinwheel.config import Settings

                        offseason_window = Settings().pinwheel_offseason_window
                    except (ValidationError, ValueError, TypeError):
                        offseason_window = 3600

                    await enter_offseason(
                        repo,
                        season.id,
                        duration_seconds=offseason_window,
                        event_bus=event_bus,
                    )
                    logger.info(
                        "championship_no_deadline season=%s -> offseason",
                        season.id,
                    )
                return

            # Offseason phase: tally governance on each tick, then close when window expires
            if season.status == "offseason":
                config = season.config or {}
                ends_at_str = config.get("offseason_ends_at")

                # Tally pending governance proposals every tick during offseason
                from pinwheel.models.rules import RuleSet as _RuleSet

                ruleset = _RuleSet(**(season.current_ruleset or {}))
                max_round_result = await session.execute(
                    select(func.coalesce(func.max(GameResultRow.round_number), 0)).where(
                        GameResultRow.season_id == season.id
                    )
                )
                last_round: int = max_round_result.scalar_one()
                _new_ruleset, tallies, gov_data = await tally_pending_governance(
                    repo, season.id, last_round, ruleset, event_bus,
                )
                if tallies:
                    proposals_data = gov_data.get("proposals", [])
                    proposal_tallies = []
                    for tally in tallies:
                        tally_data = tally.model_dump(mode="json")
                        for p_data in proposals_data:
                            if p_data.get("id") == tally.proposal_id:
                                tally_data["proposal_text"] = p_data.get("raw_text", "")
                                break
                        proposal_tallies.append(tally_data)
                    governance_summary = {
                        "round": last_round,
                        "proposals_count": len(tallies),
                        "rules_changed": len([t for t in tallies if t.passed]),
                        "tallies": proposal_tallies,
                    }
                    await event_bus.publish(
                        "governance.window_closed",
                        governance_summary,
                    )
                    logger.info(
                        "offseason_governance_tick season=%s tallies=%d",
                        season.id,
                        len(tallies),
                    )

                # Check if offseason window has expired
                if ends_at_str:
                    ends_at = datetime.fromisoformat(ends_at_str)
                    if datetime.now(UTC) >= ends_at:
                        from pinwheel.core.season import close_offseason

                        await close_offseason(repo, season.id, event_bus=event_bus, api_key=api_key)
                        logger.info(
                            "offseason_window_expired season=%s -> complete",
                            season.id,
                        )
                    else:
                        logger.info(
                            "offseason_window_still_open season=%s ends_at=%s",
                            season.id,
                            ends_at_str,
                        )
                else:
                    # No ends_at configured — close immediately
                    from pinwheel.core.season import close_offseason

                    await close_offseason(repo, season.id, event_bus=event_bus, api_key=api_key)
                    logger.info(
                        "offseason_no_deadline season=%s -> complete",
                        season.id,
                    )
                return

            # Completed/archived: game sim is done, but governance still runs
            if season.status in ("completed", "complete", "archived"):
                from pinwheel.models.rules import RuleSet

                ruleset = RuleSet(**(season.current_ruleset or {}))
                # Query max round_number from game results
                max_round_result = await session.execute(
                    select(func.coalesce(func.max(GameResultRow.round_number), 0)).where(
                        GameResultRow.season_id == season.id
                    )
                )
                last_round: int = max_round_result.scalar_one()
                new_ruleset, tallies, gov_data = await tally_pending_governance(
                    repo, season.id, last_round, ruleset, event_bus,
                )
                if tallies:
                    # Build governance summary for Discord notification
                    proposals_data = gov_data.get("proposals", [])
                    proposal_tallies = []
                    for tally in tallies:
                        tally_data = tally.model_dump(mode="json")
                        for p_data in proposals_data:
                            if p_data.get("id") == tally.proposal_id:
                                tally_data["proposal_text"] = p_data.get("raw_text", "")
                                break
                        proposal_tallies.append(tally_data)

                    governance_summary = {
                        "round": last_round,
                        "proposals_count": len(tallies),
                        "rules_changed": len([t for t in tallies if t.passed]),
                        "tallies": proposal_tallies,
                    }
                    await event_bus.publish(
                        "governance.window_closed",
                        governance_summary,
                    )
                    logger.info(
                        "governance_only_tick season=%s tallies=%d",
                        season.id,
                        len(tallies),
                    )
                return

            season_id = season.id

            # Determine next round: max existing round_number + 1
            max_round_result = await session.execute(
                select(func.coalesce(func.max(GameResultRow.round_number), 0)).where(
                    GameResultRow.season_id == season_id
                )
            )
            last_round = max_round_result.scalar_one()
            next_round = last_round + 1

        # --- Pre-flight session closed — lock released ---

        logger.info(
            "tick_round_start season=%s round=%d",
            season_id,
            next_round,
        )

        # --- Main phase: multi-session round (releases lock during AI calls) ---
        round_result = await step_round_multisession(
            engine,
            season_id,
            round_number=next_round,
            event_bus=event_bus,
            api_key=api_key,
            governance_interval=governance_interval,
            suppress_spoiler_events=(presentation_mode == "replay"),
        )

        # --- Post-round session: instant-mode presentation bookkeeping ---
        if presentation_mode != "replay" and round_result.game_results:
            async with get_session(engine) as session:
                repo = Repository(session)
                for gid in round_result.game_row_ids:
                    await repo.mark_game_presented(gid)

            # Publish presentation.game_finished for each game
            for summary in round_result.games:
                await event_bus.publish(
                    "presentation.game_finished",
                    dict(summary),
                )
            # Publish presentation.round_finished
            await event_bus.publish(
                "presentation.round_finished",
                {
                    "round": next_round,
                    "games_presented": len(round_result.game_results),
                },
            )

            # Publish deferred report events now (no delay in instant mode)
            for rev in round_result.report_events:
                await event_bus.publish("report.generated", rev)

            # Publish governance notification alongside presentation events
            if round_result.governance_summary:
                await event_bus.publish(
                    "governance.window_closed",
                    round_result.governance_summary,
                )

            # Publish season events immediately in instant mode
            for event_type, event_data in round_result.deferred_season_events:
                await event_bus.publish(event_type, event_data)

            logger.info(
                "instant_mode: marked %d games as presented",
                len(round_result.game_row_ids),
            )

        # Replay-mode presentation (background task with its own sessions)
        if (
            presentation_mode == "replay"
            and presentation_state is not None
            and round_result is not None
            and round_result.game_results
            and not presentation_state.is_active
        ):
            presentation_state.current_round = next_round

            # Build name + color cache from teams_cache for human-readable events
            name_cache = _build_name_cache(round_result.teams_cache)
            color_cache = _build_color_cache(round_result.teams_cache)

            # Create callback to mark games as presented in the DB
            game_row_ids = round_result.game_row_ids

            async def mark_presented(game_index: int) -> None:
                async with get_session(engine) as mark_session:
                    mark_repo = Repository(mark_session)
                    if game_index < len(game_row_ids):
                        await mark_repo.mark_game_presented(game_row_ids[game_index])

            # Persist start time so we can resume after deploy
            await _persist_presentation_start(
                engine,
                season_id,
                next_round,
                round_result.game_row_ids,
                quarter_replay_seconds,
            )

            asyncio.create_task(
                _present_and_clear(
                    engine=engine,
                    game_results=round_result.game_results,
                    event_bus=event_bus,
                    state=presentation_state,
                    game_interval_seconds=game_interval_seconds,
                    quarter_replay_seconds=quarter_replay_seconds,
                    name_cache=name_cache,
                    color_cache=color_cache,
                    on_game_finished=mark_presented,
                    game_summaries=round_result.games,
                    governance_summary=round_result.governance_summary,
                    report_events=round_result.report_events,
                    deferred_season_events=round_result.deferred_season_events,
                )
            )
            logger.info(
                "presentation_started season=%s round=%d games=%d",
                season_id,
                next_round,
                len(round_result.game_results),
            )

        logger.info(
            "tick_round_done season=%s round=%d",
            season_id,
            next_round,
        )
    except Exception:  # Last-resort handler — step_round, DB, AI, and event-bus errors
        logger.exception("tick_round_error")
    finally:
        await _release_tick_lock(engine, machine_id)
