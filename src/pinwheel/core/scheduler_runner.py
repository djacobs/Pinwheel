"""Scheduled game-round advancement.

Provides ``tick_round`` which is invoked by APScheduler on the cron cadence
defined by ``settings.pinwheel_game_cron``.  Each tick finds the active season,
determines the next round number, and calls ``step_round`` to execute it.

Errors are logged but never propagated so the scheduler keeps running.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from pinwheel.core.event_bus import EventBus
from pinwheel.core.game_loop import step_round
from pinwheel.db.engine import get_session
from pinwheel.db.models import GameResultRow, SeasonRow
from pinwheel.db.repository import Repository

logger = logging.getLogger(__name__)


async def tick_round(
    engine: AsyncEngine,
    event_bus: EventBus,
    api_key: str = "",
) -> None:
    """Advance the active season by one round.

    * Finds the first season in the database.
    * Determines the next round number from max(round_number) of existing
      game results + 1.
    * Calls ``step_round`` to execute simulation, governance, mirrors, and evals.
    * Commits on success; rolls back on error.

    If no season exists the tick is silently skipped.
    All exceptions are caught and logged so the scheduler is never interrupted.
    """
    try:
        async with get_session(engine) as session:
            repo = Repository(session)

            # Find active season (same pattern used across the codebase)
            result = await session.execute(select(SeasonRow).limit(1))
            season = result.scalar_one_or_none()
            if season is None:
                logger.info("tick_round_skip: no active season")
                return

            season_id: str = season.id

            # Determine next round: max existing round_number + 1
            max_round_result = await session.execute(
                select(func.coalesce(func.max(GameResultRow.round_number), 0)).where(
                    GameResultRow.season_id == season_id
                )
            )
            last_round: int = max_round_result.scalar_one()
            next_round = last_round + 1

            logger.info(
                "tick_round_start season=%s round=%d",
                season_id,
                next_round,
            )

            await step_round(
                repo,
                season_id,
                round_number=next_round,
                event_bus=event_bus,
                api_key=api_key,
            )

            logger.info(
                "tick_round_done season=%s round=%d",
                season_id,
                next_round,
            )
    except Exception:
        logger.exception("tick_round_error")
