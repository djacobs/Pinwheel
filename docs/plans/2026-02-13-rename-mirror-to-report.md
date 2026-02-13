# Plan: Rename "mirror" → "report" / "reporter" across the app

## Context

The user observed that "mirror" implies perfect reflection — but the AI interprets, has editorial judgment, and a curved surface by design. A **reporter** is a better metaphor: reporters watch, notice patterns, surface what's newsworthy, and have a perspective. In a basketball league, a reporter is the most natural thing in the world.

**Naming convention:**
- **"reporter"** — the agent/role in player-facing prose ("The reporter is watching")
- **"report"** — the artifact/output in code and prose ("Read the reports", `Report` model, `generate_simulation_report()`)
- **One exception:** Line 241 of `NEW_GOVERNOR_GUIDE.md` keeps "the AI" — it's the one place explaining the feature, not immersing the player

## Scope

~300 instances across 40+ files. Mechanical rename, no logic changes.

### 1. Rename Python source files (3 files)
- `src/pinwheel/models/mirror.py` → `src/pinwheel/models/report.py`
- `src/pinwheel/ai/mirror.py` → `src/pinwheel/ai/report.py`
- `src/pinwheel/api/mirrors.py` → `src/pinwheel/api/reports.py`

### 2. Rename Python identifiers in source files

**`src/pinwheel/models/report.py`** (formerly mirror.py):
- `MirrorType` → `ReportType`
- `Mirror` class → `Report` class
- `MirrorUpdate` → `ReportUpdate`
- `mirror_type` field → `report_type` field
- `mirror_id` field → `report_id` field
- All docstrings/comments: "mirror" → "report"

**`src/pinwheel/ai/report.py`** (formerly mirror.py):
- `SIMULATION_MIRROR_PROMPT` → `SIMULATION_REPORT_PROMPT` (and _B variant)
- `GOVERNANCE_MIRROR_PROMPT` → `GOVERNANCE_REPORT_PROMPT` (and _B variant)
- `PRIVATE_MIRROR_PROMPT` → `PRIVATE_REPORT_PROMPT` (and _B variant)
- **NOTE:** Variable names rename, but the prompt *text content* keeps "mirror" language — these are AI-internal instructions, not player-facing
- `generate_mirror_with_prompt()` → `generate_report_with_prompt()`
- `generate_simulation_mirror()` → `generate_simulation_report()` (and mock)
- `generate_governance_mirror()` → `generate_governance_report()` (and mock)
- `generate_private_mirror()` → `generate_private_report()` (and mock)
- `mirror_type=` → `report_type=` in all constructors
- `mirror_id_prefix` → `report_id_prefix`
- ID prefixes: `m-sim-` → `r-sim-`, `m-gov-` → `r-gov-`, `m-priv-` → `r-priv-`

**`src/pinwheel/api/reports.py`** (formerly mirrors.py):
- Router prefix: `/api/mirrors` → `/api/reports`
- Router tags: `["mirrors"]` → `["reports"]`
- `get_round_mirrors()` → `get_round_reports()`
- `get_private_mirrors()` → `get_private_reports()`
- `get_latest_mirrors()` → `get_latest_reports()`
- All `mirror_type` references → `report_type`

**`src/pinwheel/db/models.py`:**
- `MirrorRow` → `ReportRow`
- `__tablename__ = "mirrors"` → `__tablename__ = "reports"`
- `mirror_type` column → `report_type` column
- Index names: `ix_mirrors_*` → `ix_reports_*`
- Docstrings: "mirror" → "report"

**`src/pinwheel/db/repository.py`:**
- `store_mirror()` → `store_report()`
- `get_mirrors_for_round()` → `get_reports_for_round()`
- `get_private_mirrors()` → `get_private_reports()`
- `get_latest_mirror()` → `get_latest_report()`
- All `MirrorRow` → `ReportRow`
- All `mirror_type` → `report_type`

**`src/pinwheel/core/game_loop.py`:**
- All imports: `mirror` → `report` module paths
- `Mirror` → `Report` type references
- `mirrors: list[Mirror]` → `reports: list[Report]`
- `sim_mirror` → `sim_report`, `gov_mirror` → `gov_report`, `priv_mirror` → `priv_report`
- Event type: `"mirror.generated"` → `"report.generated"`
- `mirror_type` in event payloads → `report_type`
- `RoundResult.mirrors` → `RoundResult.reports`
- Log messages: "mirrors" → "reports"

**`src/pinwheel/main.py`:**
- Import: `from pinwheel.api.mirrors` → `from pinwheel.api.reports`
- Router alias: `mirrors_router` → `reports_router`

**`src/pinwheel/api/pages.py`:**
- Route: `/mirrors` → `/reports`
- Function: `mirrors_page()` → `reports_page()`
- Template references to mirrors

**`src/pinwheel/api/events.py`:**
- Comment: `"mirror.generated"` → `"report.generated"`

**`src/pinwheel/discord/bot.py`:**
- `mirrors_command()` → `reports_command()`
- `_handle_mirrors()` → `_handle_reports()`
- `_query_latest_mirrors()` → `_query_latest_reports()`
- `_send_private_mirror()` → `_send_private_report()`
- Event handler: `"mirror.generated"` → `"report.generated"`
- All Mirror/MirrorUpdate imports → Report/ReportUpdate

**`src/pinwheel/discord/embeds.py`:**
- `build_mirror_embed()` → `build_report_embed()`
- Import: `Mirror` → `Report`

**Evals modules** (`src/pinwheel/evals/`):
- `gqi.py`: `get_mirrors_for_round` → `get_reports_for_round`, `mirror_words` → `report_words`, `mirror_meaningful` → `report_meaningful`, comments
- `grounding.py`: `mirror_id` → `report_id`, `mirror_type` → `report_type`, comments
- `behavioral.py`: `compute_mirror_impact_rate()` → `compute_report_impact_rate()`
- `rubric.py`: `score_mirror()` → `score_report()`
- `prescriptive.py`: `mirror_id` → `report_id`, `mirror_type` → `report_type`

### 3. Rename test files and update test code

- `tests/test_mirrors.py` → `tests/test_reports.py`
- Update all test files that reference mirror identifiers (test_models.py, test_db.py, test_discord.py, test_pages.py, test_game_loop.py, test_scheduler_runner.py, test_commentary.py, and all test_evals/ files)

### 4. Update HTML templates

**`templates/base.html`:**
- Nav link: `/mirrors` → `/reports`, text "Mirrors" → "Reports"

**`templates/pages/mirrors.html`** → rename to `templates/pages/reports.html`:
- Title: "Mirrors" → "Reports"
- All mirror CSS classes → report CSS classes
- All template text: "mirror" → "report"

**`templates/pages/home.html`:**
- Comments and headings: "Mirror" → "Report"/"Reporter"
- CSS classes: `.mirror-badge` → `.report-badge`, `.home-mirror` → `.home-report`
- Section text: "The Mirror Reflects" → "The Reporter" or similar
- Link: `/mirrors` → `/reports`

**`templates/pages/game.html`, `arena.html`:**
- CSS classes: `.mirror-card` → `.report-card`, etc.
- Template variable names if any

**`templates/pages/play.html`:**
- "The Mirror Reflects" section → update heading and copy
- Mirror references → report/reporter references

**`templates/pages/privacy.html`:**
- "Private Mirrors" → "Private Reports"
- All mirror references → report references

**`templates/pages/terms.html`:**
- "Privacy of Mirrors" → "Privacy of Reports"
- Mirror references → report references

**`templates/pages/eval_dashboard.html`:**
- "Mirror Impact Rate" → "Report Impact Rate"
- "mirrors checked" → "reports checked"

### 5. Update CSS

**`static/css/pinwheel.css`:**
- `--accent-mirror` → `--accent-report`
- `.mirror-card` → `.report-card`
- `.mirror-type` → `.report-type`
- `.mirror-content` → `.report-content`
- `.mirror-meta` → `.report-meta`
- `.mirror-badge` → `.report-badge`
- `.home-mirror` → `.home-report`
- `.home-mirror-content` → `.home-report-content`
- All comments: "Mirror" → "Report"

### 6. Update scripts

**`scripts/run_demo.sh`:**
- "social mirror" → "reporter"
- "simulation mirrors" → "simulation reports"
- "AI Mirrors" → "Reports"
- URL: `/mirrors` → `/reports`
- Screenshot filename: `06_mirrors.png` → `06_reports.png`

**`scripts/demo_seed.py`:**
- `result.mirrors` → `result.reports`
- `m.mirror_type` → `m.report_type`

### 7. Update documentation

**`docs/NEW_GOVERNOR_GUIDE.md`:**
- Section "## The Mirrors" → "## The Reports"
- Line 241: "the AI generates mirrors" → "the AI files reports" (the one place AI stays)
- "Simulation Mirror" → "Simulation Report", etc.
- "Read the mirrors" → "Read the reports"
- "The mirror is watching" → "The reporter is watching"
- "The mirror starts noting" → "The reporter starts noting"
- "noted by the mirror" → "noted by the reporter"
- "Visible to the mirror" → "Visible to the reporter"
- "The mirror will note" → "The reporter will note"
- "what the game sees about *you*" → keep or adjust
- `/mirrors` command → `/reports`
- Loop step 9: "The mirrors" → "The reports"

**`CLAUDE.md`:**
- All architectural references: mirror → report/reporter
- Module paths: `ai/mirror.py` → `ai/report.py`, etc.

**Other docs** (SECURITY.md, GLOSSARY.md, GAME_LOOP.md, PLAYER.md, VIEWER.md, plans/):
- All mirror references → report/reporter

### 8. Database re-seed required

No Alembic. Table rename (`mirrors` → `reports`, column `mirror_type` → `report_type`) requires dropping and re-creating. Run `demo_seed.py seed` after the rename to repopulate.

## Execution order

1. Rename the 3 Python source files + 1 template file (git mv)
2. Global find-replace in Python source files (models → repo → ai → core → api → discord → evals → main)
3. Update templates (HTML)
4. Update CSS
5. Update tests (rename file + update all references)
6. Update scripts
7. Update docs (NEW_GOVERNOR_GUIDE.md, CLAUDE.md, others)
8. Run `ruff check` + `ruff format`
9. Run `pytest -x -q` — fix any breakage
10. Re-seed if needed to verify end-to-end

## Verification

1. `ruff check src/ tests/` — no lint errors
2. `pytest -x -q` — all tests pass
3. `grep -ri "mirror" src/ templates/ static/ tests/` — zero hits (confirms complete rename)
4. Spot-check NEW_GOVERNOR_GUIDE.md: "the AI" appears only on line ~241
5. Start dev server, visit `/reports` — page loads correctly
