# Plan: Season Archive Fix + Playoff Series Reports

## Context

Two lifecycle gaps exist:
1. `close_offseason()` never calls `archive_season()` — seasons can reach COMPLETE without being archived (no memorial, no history entry).
2. No report is generated when a playoff series concludes — the `"series"` report type exists in the model but nothing produces it. Additionally, governors on participating teams should be able to collaboratively edit these reports.

---

## Part 1: Wire archive_season() into close_offseason()

**Files:**
- `src/pinwheel/core/season.py` — Add `api_key: str = ""` param to `close_offseason()`, call `archive_season()` after transitioning to COMPLETE (wrapped in try/except so archive failure doesn't block the transition)
- `src/pinwheel/core/scheduler_runner.py` — Pass `api_key=api_key` at both call sites (lines 524 and 539)
- `tests/` — Test that close_offseason produces a SeasonArchiveRow; test that archive failure doesn't prevent COMPLETE transition

---

## Part 2: Series Report Generation

**Trigger:** After `_advance_playoff_series()` returns in `_phase_persist_and_finalize()` (game_loop.py:1820), inspect the deferred events for series completion (`season.semifinals_complete`, `season.playoffs_complete`). For each completed series, generate a report.

**Files:**
- `src/pinwheel/ai/report.py` — Add `SERIES_REPORT_PROMPT`, `generate_series_report()`, `generate_series_report_mock()`. Prompt: 2-3 paragraph sports recap covering the arc, turning point, and clinching game. Context includes team names, game-by-game scores, series record, series type.
- `src/pinwheel/core/game_loop.py` — Add `_get_series_games()` helper (queries playoff games between two teams) and `_generate_series_reports()` (parses deferred events, gathers data, calls AI, stores via `repo.store_report()`). Wire into `_phase_persist_and_finalize()` after line 1820. Store with `report_type="series"`, `team_id=winner_id`, `metadata_json={"series_type", "winner_id", "loser_id", "record"}`.

**Latency note:** AI call happens inside Session 2, but series completion is rare (max 3x per season) and the report is short (~500 tokens). Acceptable.

---

## Part 3: Web Display

Series reports already flow through the reports page — the template at `templates/pages/reports.html` renders `{{ m.report_type|title }} Report` which will show "Series Report". The `phase` badge logic only fires for `semifinal`/`finals` which come from game data, not report type. For series reports, add a series-type badge using the `metadata_json` if available. Small template tweak.

**File:** `templates/pages/reports.html` — Add badge for series reports showing "SEMIFINAL SERIES" or "CHAMPIONSHIP FINALS SERIES".

---

## Part 4: /edit-series Discord Command

**Pattern:** Follows the existing `ReviseProposalModal` pattern — slash command opens a modal with pre-filled text, governor edits, content saved to DB.

**Auth:** Only governors whose `team_id` matches `winner_id` or `loser_id` in the report's `metadata_json` can edit. Both teams can collaborate.

**Files:**
- `src/pinwheel/db/repository.py` — Add `update_report_content(report_id, content)` and `get_series_reports(season_id)` methods
- `src/pinwheel/discord/views.py` — Add `EditSeriesModal` (paragraph text input, max 4000 chars, pre-filled with current content). `on_submit()` calls `repo.update_report_content()`, appends `report.edited` event for audit trail, sends confirmation embed.
- `src/pinwheel/discord/embeds.py` — Add `build_series_edit_embed()` confirmation embed
- `src/pinwheel/discord/bot.py` — Register `/edit-series` command with autocomplete. Autocomplete queries `get_series_reports()` filtered to reports where governor's team participated. Handler validates auth, opens modal.

**Discord posting:** Series reports will auto-post to play-by-play channel via the existing `report.generated` event bus → `build_report_embed()` pipeline (embeds.py already maps `"series"` → "Series Report").

---

## Part 5: Tests

- `test_game_loop.py` — Series report generated on semifinal decided; series report generated on finals decided; mock content includes team names; metadata stored correctly
- `test_discord.py` — `/edit-series` auth allows participating governor; rejects non-participating; saves updated content; `report.edited` event appended
- Season lifecycle test — `close_offseason()` produces archive row; archive failure is non-fatal

---

## Implementation Order

1. Part 1 (archive fix) — independent, low risk
2. Part 2 (series report generation) — core logic
3. Part 3 (web display) — small template tweak
4. Part 4 (/edit-series command) — depends on Part 2
5. Part 5 (tests) — alongside each part
