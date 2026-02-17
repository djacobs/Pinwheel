# Game Phase Tracking + Haiku-Powered Series Descriptions

## Context

The series description banner says "Burnside Breakers lead 2-0 · First to 2 wins advances" even when the series is already clinched. We just patched this with an `if` branch, but the deeper issue is the same as the interpreter: the code doesn't *understand* the situation — it formats strings from numbers.

Two changes:
1. **Track game phases properly** — the code already knows whether a game is semifinal or finals when creating schedule entries, but writes `phase="playoff"` for everything. Store the actual phase.
2. **Replace rigid description templates with Haiku** — give Haiku the full situation (teams, record, phase, clinched/not, sweep/close series) and let it write a natural, situationally-aware description.

## Changes

### 1. Store precise phase on ScheduleRow entries
**File:** `src/pinwheel/core/game_loop.py`

The code already has `"playoff_round": "semifinal"` and `"playoff_round": "finals"` in bracket dicts but writes `phase="playoff"` to the DB. Change all 5 `create_schedule_entry` calls:

| Location | Currently | Change to |
|----------|-----------|-----------|
| `_schedule_next_series_game()` :238 | `phase="playoff"` | Accept `phase` parameter, pass through |
| `_advance_playoff_series()` :435 | `phase="playoff"` | `phase="finals"` (this creates the finals entry) |
| `_build_playoff_bracket()` :619 | `phase="playoff"` | `phase="semifinal"` |
| `_build_playoff_bracket()` :651 | `phase="playoff"` | `phase="finals"` (2-team direct finals) |
| Callers of `_schedule_next_series_game` :354, :407, :526 | no phase | Pass `"semifinal"` or `"finals"` based on context |

### 2. Add `phase` column to GameResultRow
**File:** `src/pinwheel/db/models.py:116`

Add: `phase: Mapped[str | None] = mapped_column(String(20), nullable=True)`

Nullable → auto-migrated by `auto_migrate_schema()`. Existing rows get NULL (regular season).

### 3. Copy phase from ScheduleRow when storing game results
**File:** `src/pinwheel/core/game_loop.py:1173`

Pass `entry.phase` to `store_game_result()`. Add `phase` parameter to `store_game_result()` in `src/pinwheel/db/repository.py:212`.

### 4. Update `_get_game_phase()` to read phase from DB
**File:** `src/pinwheel/api/pages.py:187`

Instead of inferring phase by comparing team pairs, read `schedule[0].phase` directly. The inference logic was a workaround for not storing the phase — now we store it. Keep the inference as fallback for games created before this change.

### 5. Replace rigid description in `build_series_context` with Haiku
**File:** `src/pinwheel/api/pages.py:210`

Replace the if/elif chain with a Haiku call. Give Haiku:
- Phase (semifinal / finals)
- Team names
- Series record (e.g. 2-0)
- Whether clinched (wins >= wins_needed)
- Best-of-N
- Who leads, by how much

Haiku returns a short (1-2 sentence) natural-language description. Examples of what it might produce:
- "Burnside Breakers sweep the Herons 2-0 to advance to the finals"
- "Championship Finals tied 1-1 — this is anybody's series"
- "Rose City Thorns lead 2-1, one win from the championship"

Implementation: reuse the classifier's `_get_client()` pattern from `src/pinwheel/ai/classifier.py`. Small prompt, `max_tokens=100`, 10s timeout. Mock fallback for tests returns a simple template string.

Add a new function `_generate_series_description()` in `pages.py` (or a new `ai/series.py` if it grows). Make `build_series_context` async and call it.

### 6. Update callers of `build_series_context`
**File:** `src/pinwheel/api/pages.py`

`build_series_context` becomes async. Its caller `_compute_series_context_for_game` (line ~313) is already async, so this is a signature change only.

## Files Modified
- `src/pinwheel/db/models.py` — add `phase` to GameResultRow
- `src/pinwheel/db/repository.py` — accept `phase` in `store_game_result()`
- `src/pinwheel/core/game_loop.py` — write precise phases, pass phase to game storage
- `src/pinwheel/api/pages.py` — read phase from DB, Haiku description generation
- `tests/test_pages.py` — update series context tests, add Haiku mock tests
- `tests/test_game_loop.py` — verify phase is stored correctly

## Verification
1. `uv run pytest -x -q` — all tests pass
2. `uv run ruff check src/ tests/` — lint clean
3. Deploy and check arena page — series descriptions should be natural language
