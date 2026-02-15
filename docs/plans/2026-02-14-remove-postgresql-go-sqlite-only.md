# Remove PostgreSQL — Go SQLite-Only

## Context

Production runs on Fly.io with a SQLite file on a persistent volume (`/data`). There is no Postgres instance attached. The `asyncpg` dependency and all PostgreSQL references are dead code/docs. This cleanup removes them to simplify the stack and eliminate confusion.

## Changes

### 1. Remove `asyncpg` dependency
- **`pyproject.toml` line 15** — Delete `"asyncpg>=0.30",`
- Run `uv lock` to regenerate `uv.lock` without asyncpg

### 2. Simplify `src/pinwheel/db/engine.py`
- Remove the `if "sqlite" in database_url` guards — SQLite is the only engine now
- Always set `connect_args["timeout"] = 15` and always register the PRAGMA listener
- Simplify docstring to reflect SQLite-only

### 3. Clean up `src/pinwheel/db/repository.py` (~line 316-321)
- Remove `.with_for_update()` and the PostgreSQL comment — SQLite is single-writer, this is a no-op
- Keep the sequence assignment logic, just drop the lock clause

### 4. Update `src/pinwheel/config.py` line 90
- Add a comment clarifying SQLite-only: `# SQLite only — no PostgreSQL support`

### 5. Update docs (reword PostgreSQL → SQLite)

| File | Change |
|------|--------|
| `CLAUDE.md` line 20 | Reword to "SQLite via SQLAlchemy 2.0 async (aiosqlite)" |
| `CLAUDE.md` line 308 | Change `DATABASE_URL` comment to SQLite-only |
| `docs/DEMO_MODE.md` lines 13, 184 | Remove asyncpg/PostgreSQL references |
| `docs/OPS.md` | Remove `fly postgres` commands and PostgreSQL architecture references; document the SQLite volume-mount approach instead |
| `docs/product/ADMIN_GUIDE.md` | Remove `fly postgres` setup commands |
| `docs/product/COLOPHON.md` line 19 | Simplify to SQLite-only |
| `README.md` lines 107-108 | Remove `fly postgres` deployment commands |

### 6. Do NOT touch
- `docs/dev_log/` — Historical record, leave as-is
- `docs/plans/` — Historical plans, leave as-is

## Verification
1. `uv sync --extra dev` — Confirm asyncpg is no longer installed
2. `uv run pytest -x -q` — All tests pass (they already use SQLite)
3. `uv run ruff check src/ tests/` — Clean lint
4. `grep -ri asyncpg src/ pyproject.toml` — No hits
5. `grep -ri postgresql src/` — No hits
