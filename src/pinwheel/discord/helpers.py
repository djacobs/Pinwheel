"""Discord bot helpers — governor auth, DB session context."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from pinwheel.db.engine import get_session
from pinwheel.db.repository import Repository

logger = logging.getLogger(__name__)


class GovernorNotFound(Exception):
    """Raised when a Discord user is not enrolled as a governor."""


@dataclass(frozen=True)
class GovernorInfo:
    """Resolved governor context for a Discord interaction."""

    player_id: str
    discord_id: str
    team_id: str
    team_name: str
    season_id: str


@asynccontextmanager
async def db_session(
    engine: AsyncEngine,
) -> AsyncGenerator[Repository, None]:
    """Yield a Repository bound to a fresh async session."""
    async with get_session(engine) as session:
        yield Repository(session)


async def get_current_season_id(engine: AsyncEngine) -> str | None:
    """Return the active season's ID, or None."""
    async with get_session(engine) as session:
        repo = Repository(session)
        season = await repo.get_active_season()
        return season.id if season else None


async def get_governor(engine: AsyncEngine, discord_id: str) -> GovernorInfo:
    """Look up a governor by Discord user ID.

    Raises GovernorNotFound if the user is not enrolled this season.
    """
    async with get_session(engine) as session:
        repo = Repository(session)
        season = await repo.get_active_season()
        if not season:
            raise GovernorNotFound(
                "There's no active season right now. "
                "Ask an admin to start one with `/new-season`."
            )

        player = await repo.get_player_by_discord_id(discord_id)
        if player is None or player.team_id is None or player.enrolled_season_id != season.id:
            raise GovernorNotFound(
                "You're not enrolled as a governor this season. "
                "Use `/join` to pick a team and get started."
            )

        team = await repo.get_team(player.team_id)
        team_name = team.name if team else player.team_id

        return GovernorInfo(
            player_id=player.id,
            discord_id=discord_id,
            team_id=player.team_id,
            team_name=team_name,
            season_id=season.id,
        )


async def get_governor_info(repo: Repository, discord_id: str) -> GovernorInfo | None:
    """Look up a governor by Discord ID using an existing repo session.

    Returns None if the user is not enrolled as a governor this season.
    Unlike ``get_governor`` (which opens its own session), this reuses
    the caller's session — useful inside an existing ``get_session`` block.
    """
    season = await repo.get_active_season()
    if not season:
        return None

    player = await repo.get_player_by_discord_id(discord_id)
    if player is None or player.team_id is None or player.enrolled_season_id != season.id:
        return None

    team = await repo.get_team(player.team_id)
    team_name = team.name if team else player.team_id

    return GovernorInfo(
        player_id=player.id,
        discord_id=discord_id,
        team_id=player.team_id,
        team_name=team_name,
        season_id=season.id,
    )
