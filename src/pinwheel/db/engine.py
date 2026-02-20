"""Async SQLAlchemy engine and session factory (SQLite-only).

Usage:
    engine = create_engine(settings.database_url)
    async with get_session(engine) as session:
        ...
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from pinwheel.db.models import Base

logger = logging.getLogger(__name__)


def create_engine(database_url: str) -> AsyncEngine:
    """Create an async SQLAlchemy engine.

    Enables WAL journal mode and a 15-second busy timeout so concurrent
    sessions (scheduler, Discord commands, web requests) don't immediately
    fail with "database is locked".
    """
    connect_args: dict[str, object] = {"timeout": 15}

    engine = create_async_engine(database_url, echo=False, connect_args=connect_args)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn: object, connection_record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[union-attr]
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=15000")
        # Enforce ForeignKey constraints — without this all FK declarations are
        # decorative and orphaned records can accumulate silently.
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


# Module-level cache: one session factory per engine instance.
# Keyed by the engine's sync_engine identity so multiple test engines remain
# isolated, while all production requests share a single factory.
_session_factories: dict[int, async_sessionmaker[AsyncSession]] = {}


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return a cached session factory bound to *engine*.

    The factory is created once per engine instance and reused on every
    subsequent call.  This avoids the overhead of constructing a new
    ``async_sessionmaker`` for every inbound HTTP request.
    """
    key = id(engine.sync_engine)
    if key not in _session_factories:
        _session_factories[key] = async_sessionmaker(engine, expire_on_commit=False)
    return _session_factories[key]


@asynccontextmanager
async def get_session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """Yield an async session that auto-commits on success, rolls back on error."""
    factory = create_session_factory(engine)
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:  # Re-raise pattern — must catch all to ensure rollback on any error
            await session.rollback()
            raise


# ---------------------------------------------------------------------------
# Auto-migration: detect and add missing columns at startup
# ---------------------------------------------------------------------------

_SQLITE_TYPE_MAP: dict[str, str] = {
    "String": "VARCHAR",
    "Text": "TEXT",
    "Integer": "INTEGER",
    "BigInteger": "BIGINT",
    "Float": "FLOAT",
    "Boolean": "BOOLEAN",
    "DateTime": "DATETIME",
    "JSON": "JSON",
    "NullType": "TEXT",
}


def _sqlite_col_type(sa_type: object) -> str:
    """Convert a SQLAlchemy type to a SQLite type string."""
    type_name = type(sa_type).__name__
    base = _SQLITE_TYPE_MAP.get(type_name, "TEXT")
    if type_name == "String" and hasattr(sa_type, "length") and sa_type.length:
        return f"VARCHAR({sa_type.length})"
    return base


def _scalar_default_sql(column: object) -> str | None:
    """Extract a SQL DEFAULT literal from a column, or None.

    Only handles scalar (non-callable) Python-side defaults and server_default.
    Callable defaults (e.g. ``default=dict``) are Python-side only and have no
    SQL equivalent — returns None for those.
    """
    if column.server_default is not None:  # type: ignore[union-attr]
        return str(column.server_default.arg)  # type: ignore[union-attr]
    if column.default is not None and column.default.is_scalar:  # type: ignore[union-attr]
        val = column.default.arg  # type: ignore[union-attr]
        if val is None:
            return None
        if isinstance(val, bool):
            return "1" if val else "0"
        if isinstance(val, (int, float)):
            return str(val)
        if isinstance(val, str):
            escaped = val.replace("'", "''")
            return f"'{escaped}'"
    return None


async def auto_migrate_schema(conn: AsyncConnection) -> int:
    """Compare ORM models against actual SQLite schema, add missing columns.

    For each table in ``Base.metadata``:

    1. ``PRAGMA table_info(table)`` to get existing columns.
    2. Compare against the model's column definitions.
    3. For missing columns that are nullable **or** have a SQL-expressible
       default: ``ALTER TABLE ADD COLUMN``.
    4. For missing columns that are NOT NULL with no SQL default: log a
       warning and skip (unsafe to add — existing rows would violate the
       constraint).

    Returns the number of columns added.
    """
    added = 0
    for table_name, table in Base.metadata.tables.items():
        result = await conn.execute(text(f"PRAGMA table_info({table_name})"))
        rows = result.fetchall()
        if not rows:
            # Table doesn't exist yet — create_all handles this
            continue
        existing_cols = {row[1] for row in rows}

        for column in table.columns:
            if column.name in existing_cols:
                continue

            col_type = _sqlite_col_type(column.type)
            default_sql = _scalar_default_sql(column)

            if default_sql is not None:
                col_def = f"{column.name} {col_type} DEFAULT {default_sql}"
            elif column.nullable:
                col_def = f"{column.name} {col_type}"
            else:
                logger.warning(
                    "auto_migrate: skipping %s.%s — NOT NULL with no SQL default",
                    table_name,
                    column.name,
                )
                continue

            await conn.execute(
                text(f"ALTER TABLE {table_name} ADD COLUMN {col_def}")
            )
            logger.info("auto_migrate: added %s.%s", table_name, column.name)
            added += 1

    return added
