"""Async SQLAlchemy engine and session factory.

Usage:
    engine = create_engine(settings.database_url)
    async with get_session(engine) as session:
        ...
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(database_url: str) -> AsyncEngine:
    """Create an async SQLAlchemy engine.

    For SQLite, enables WAL journal mode and a 15-second busy timeout so
    concurrent sessions (scheduler, Discord commands, web requests) don't
    immediately fail with "database is locked".
    """
    connect_args: dict[str, object] = {}
    if "sqlite" in database_url:
        connect_args["timeout"] = 15

    engine = create_async_engine(database_url, echo=False, connect_args=connect_args)

    if "sqlite" in database_url:

        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragmas(dbapi_conn: object, connection_record: object) -> None:
            cursor = dbapi_conn.cursor()  # type: ignore[union-attr]
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=15000")
            cursor.close()

    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create a session factory bound to the given engine."""
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def get_session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """Yield an async session that auto-commits on success, rolls back on error."""
    factory = create_session_factory(engine)
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
