"""FastAPI dependency injection for database sessions and repository."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from pinwheel.db.engine import create_session_factory
from pinwheel.db.repository import Repository


async def get_engine(request: Request) -> AsyncEngine:
    """Get the database engine from app state."""
    return request.app.state.engine


async def get_session(
    engine: Annotated[AsyncEngine, Depends(get_engine)],
) -> AsyncGenerator[AsyncSession, None]:
    """Yield an async session."""
    factory = create_session_factory(engine)
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:  # Re-raise pattern — must catch all to ensure rollback on any error
            await session.rollback()
            raise


async def get_repo(session: Annotated[AsyncSession, Depends(get_session)]) -> Repository:
    """Get a repository instance bound to the current session."""
    return Repository(session)


RepoDep = Annotated[Repository, Depends(get_repo)]
