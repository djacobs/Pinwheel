"""Migration: Add `meta` JSON column to entity tables.

Safe additive migration — adds a nullable JSON column with default `{}`.
Idempotent: skips tables that already have the column.
Works on SQLite.

Usage:
    python scripts/migrate_add_meta.py [DATABASE_URL]

If DATABASE_URL is not provided, reads from the DATABASE_URL env var
or defaults to sqlite:///pinwheel.db.
"""

from __future__ import annotations

import os
import sqlite3
import sys


TABLES_TO_MIGRATE = [
    "teams",
    "hoopers",
    "game_results",
    "seasons",
    "schedule",
    "box_scores",
    "players",
]


def _get_db_path(database_url: str) -> str:
    """Extract SQLite file path from a database URL."""
    # Handle both sync and async URLs
    url = database_url.replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")
    return url


def _has_column(cursor: sqlite3.Cursor, table: str, column: str) -> bool:
    """Check if a table has a specific column."""
    cursor.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in cursor.fetchall()]
    return column in columns


def migrate(database_url: str) -> None:
    """Add meta JSON column to all entity tables."""
    db_path = _get_db_path(database_url)
    print(f"Migrating database: {db_path}")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    for table in TABLES_TO_MIGRATE:
        # Check if table exists
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        if not cursor.fetchone():
            print(f"  {table}: table does not exist, skipping")
            continue

        if _has_column(cursor, table, "meta"):
            print(f"  {table}: meta column already exists, skipping")
            continue

        print(f"  {table}: adding meta column...")
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN meta TEXT DEFAULT '{{}}'")

    conn.commit()
    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    url = (
        sys.argv[1]
        if len(sys.argv) > 1
        else os.environ.get("DATABASE_URL", "sqlite:///pinwheel.db")
    )
    migrate(url)
