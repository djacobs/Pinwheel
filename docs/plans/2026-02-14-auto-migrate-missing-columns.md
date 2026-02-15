# Plan: Auto-migrate missing columns on startup

## Context

Commit `6495981` added `meta` JSON columns to `SeasonRow`, `TeamRow`, and `HooperRow` in the SQLAlchemy model but the production SQLite database was never migrated. Every page that queries the `seasons` table failed with `no such column: seasons.meta`, taking down `/`, `/reports`, and most other pages. We manually ran ALTER TABLE on prod to fix it.

The root cause: `Base.metadata.create_all()` creates new tables but **does not add columns to existing tables**. The codebase has hand-coded `_add_column_if_missing()` calls in `main.py` lifespan for specific columns, but this approach requires developers to remember to add a migration line every time they add a column. That's what was forgotten.

## Approach

Replace the hand-coded migration calls with a **generic auto-migrator** that introspects `Base.metadata` at startup and adds any missing columns automatically.

## Files to modify

- `src/pinwheel/main.py` — Replace `_add_column_if_missing` calls with `auto_migrate_schema()`
- `src/pinwheel/db/engine.py` — Add `auto_migrate_schema()` function (lives near DB engine, not in main)
- `tests/test_db.py` — Add test for schema drift detection

## Implementation

### 1. New function in `src/pinwheel/db/engine.py`

```python
async def auto_migrate_schema(conn) -> int:
    """Compare ORM models against actual SQLite schema, add missing columns.

    For each table in Base.metadata:
      1. PRAGMA table_info(table) to get existing columns
      2. Compare against model's column definitions
      3. For missing columns: if nullable or has a server_default/default, ALTER TABLE ADD COLUMN
      4. For missing columns that are NOT nullable with no default: log error, skip (unsafe)

    Returns the number of columns added.
    """
```

Logic for deriving the column definition string from SQLAlchemy `Column` objects:
- Type: use `column.type.compile(dialect=engine.dialect)` or a simple SQLite type map (`Integer→INTEGER`, `String→VARCHAR`, `JSON→JSON`, `Boolean→BOOLEAN`, `DateTime→DATETIME`, `BigInteger→BIGINT`)
- Default: if `column.default` has a scalar `.arg`, use `DEFAULT <value>`. For callable defaults, skip (SQLAlchemy handles those in Python, not SQL).
- Nullable: if `column.nullable`, no constraint needed (SQLite columns are nullable by default). If not nullable and no default, log warning and skip.

### 2. Update `src/pinwheel/main.py` lifespan

Replace lines 55-66 (the 6 hand-coded `_add_column_if_missing` calls) with:

```python
from pinwheel.db.engine import auto_migrate_schema
added = await auto_migrate_schema(conn)
if added:
    logger.info("auto-migration: added %d column(s)", added)
```

Keep `_add_column_if_missing` as a private helper (called by auto_migrate_schema internally), or inline it.

### 3. Add test in `tests/test_db.py`

Test that creates a DB with `create_all()`, drops a column via raw SQL, then calls `auto_migrate_schema()` and verifies it was re-added. Also test the safety case: a non-nullable, no-default column should be logged/skipped, not crash.

## What this does NOT do

- Rename columns, change types, or drop columns — only additive
- Handle tables that don't exist (that's `create_all()`'s job)
- Replace Alembic for complex migrations — this is a SQLite-specific safety net

## Verification

1. `uv run pytest -x -q` — all tests pass
2. Delete `meta` column from local DB, restart server, verify it gets re-added
3. Check logs for migration messages
