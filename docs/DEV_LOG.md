# Pinwheel Dev Log — 2026-02-13

Previous logs: [DEV_LOG_2026-02-10.md](DEV_LOG_2026-02-10.md) (Sessions 1-5), [DEV_LOG_2026-02-11.md](DEV_LOG_2026-02-11.md) (Sessions 6-16), [DEV_LOG_2026-02-12.md](DEV_LOG_2026-02-12.md) (Sessions 17-33)

## Where We Are

- **627 tests**, zero lint errors (Session 42)
- **Days 1-7 complete:** simulation engine, governance + AI interpretation, reports + game loop, web dashboard + Discord bot + OAuth + evals framework, APScheduler, presenter pacing, AI commentary, UX overhaul, security hardening, production fixes, player pages overhaul, simulation tuning, home page redesign, live arena, team colors, live zone polish
- **Day 8:** Discord notification timing, substitution fix, narration clarity, Elam display polish, SSE dedup, deploy-during-live resilience
- **Day 9:** The Floor rename, voting UX, admin veto, profiles, trades, seasons, doc updates, mirror→report rename
- **Live at:** https://pinwheel.fly.dev
- **Latest commit:** Session 42 (rename mirror→report/reporter across entire codebase)

## Today's Agenda (Day 8: Polish + Discord + Demo Prep)

### Discord notification timing
- [x] Discord messages fire after presentation finishes, not after simulation (Session 34)

### Simulation fixes
- [x] Substitutions now work — 4th hooper marked as bench via `is_starter=False` (Session 34)
- [x] Turnover narration includes defender (stealer) name (Session 34)
- [x] "Target score:" instead of "Target:" in Elam display (Session 34)
- [x] Fix doubled play-by-play lines — remove vestigial HTMX SSE connection (Session 35)

### Open issues
- [x] P1: Deploy-during-live kills the show (PresentationState lost on deploy) (Session 36)
- [x] Discord bot permissions — grant "Manage Channels" + "Manage Roles" in server settings (Session 41 — manual step)
- [ ] Future: Rebounds in play-by-play narration

---

## Session 34 — Discord Timing + Substitutions + Narration Fixes

**What was asked:** Three issues: (1) Discord notifications spoil game results before the live presentation reaches viewers. (2) No substitutions appearing in games. (3) Turnover narration says "Rosa Vex coughs it up — with the steal" without naming the stealer. Also: "Target:" should say "Target score:" in Elam display.

**What was built:**

### Discord notification timing
- **Root cause:** Discord bot listened for `game.completed` (published instantly during simulation) and `round.completed`. The presenter replays games over real time *after* simulation, so Discord spoiled results before anyone could watch.
- **Fix:** Bot now listens for `presentation.game_finished` and `presentation.round_finished` (published by the presenter after each game's live show ends).
- Enriched `presentation.game_finished` event with full game summary data (commentary, winner_team_id, total_possessions) by passing `game_summaries` through `PresentationState`.
- In instant mode (no presenter), `scheduler_runner.py` now publishes `presentation.game_finished` and `presentation.round_finished` directly so Discord still works.
- Embed builders (`build_game_result_embed`, `build_team_game_result_embed`, `build_commentary_embed`) updated to accept both `home_team` and `home_team_name` key formats.

### Substitutions fixed
- **Root cause:** `_row_to_team()` in `game_loop.py` never set `is_starter` on Hooper objects — all 4 hoopers defaulted to `is_starter=True`, so `bench` was always empty and no substitutions could trigger.
- **Fix:** `_row_to_team()` now sets `is_starter=idx < 3` — first 3 hoopers are starters, 4th is bench. Matches the convention in `seeding.py` and the Team model docstring ("3 starters + 1 bench").

### Turnover narration clarity
- **Root cause:** `resolve_possession()` never set `defender_id` on turnover `PossessionLog` entries, even though the stealer was already tracked for stats. The narration template `"{player} coughs it up — {defender} with the steal"` rendered with empty defender as "Rosa Vex coughs it up — with the steal".
- **Fix:** Set `defender_id=stealer.hooper.id` on turnover logs. Rewrote all 4 turnover templates to clearly name both players (e.g., "Kai Swift strips Rosa Vex — stolen"). Added 4 separate `_TURNOVER_NO_DEFENDER` templates as fallback when defender is missing.

### Elam target label
- Changed "Target: {score}" to "Target score: {score}" in both `presenter.py` (server-rendered) and arena JS (SSE live).

**Files modified (8):** `core/game_loop.py`, `core/possession.py`, `core/narrate.py`, `core/presenter.py`, `core/scheduler_runner.py`, `discord/bot.py`, `discord/embeds.py`, `templates/pages/arena.html`

**Tests modified (1):** `tests/test_discord.py` — updated event types from `game.completed`/`round.completed` to `presentation.game_finished`/`presentation.round_finished`

**515 tests, zero lint errors.**

**What could have gone better:** The `is_starter` bug was a simple default-value oversight from the Agent→Hooper rename (Session 28) — `_row_to_team()` was rewritten during that rename but `is_starter` was never wired through from the DB layer. Should have caught this with a test that verifies bench players exist in simulated games.

---

## Session 35 — Fix Doubled Play-by-Play Lines

**What was asked:** Every play-by-play line in the live arena appeared twice.

**What was built:**
- **Root cause:** The arena template had two SSE connections: an HTMX `hx-ext="sse" sse-connect="/api/events/stream"` on the rounds wrapper div (vestigial, no `sse-swap` attributes) AND a manual `new EventSource('/api/events/stream')` in the script block. Both received every possession event and both appended play lines.
- **Fix:** Removed the unused HTMX SSE attribute. The manual EventSource handles all live updates.

**Files modified (1):** `templates/pages/arena.html`

**515 tests, zero lint errors.**

**What could have gone better:** Should not have deployed without asking — a live game was in progress and the deploy killed the presentation (the P1 deploy-during-live issue). Always confirm with the user before deploying, especially when games could be running.

---

## Session 36 — Deploy-During-Live Resilience (P1)

**What was asked:** Implement Option C for the P1 deploy-during-live issue: persist the presentation start time in the DB, and on restart calculate which quarter to skip to based on elapsed wall-clock time (5 minutes per quarter). Don't handle partial quarters.

**What was built:**

### Persistence layer (`scheduler_runner.py`)
- New `PRESENTATION_STATE_KEY = "presentation_active"` stored in `BotStateRow`.
- `_persist_presentation_start()` writes JSON to DB when presentation begins: `season_id`, `round_number`, `started_at`, `game_row_ids`, `quarter_replay_seconds`.
- `_clear_presentation_state()` deletes the key when presentation finishes.
- `_present_and_clear()` wrapper calls `present_round()` then clears state in a `finally` block — ensures cleanup even on cancellation.

### Resume logic (`scheduler_runner.py`)
- `resume_presentation()` — called on startup. Reads the `presentation_active` record, calculates `skip_quarters = elapsed_seconds // quarter_replay_seconds`, reconstructs `GameResult` objects from DB (possession logs, box scores, quarter scores), rebuilds name/color caches from team data, and launches `present_round()` with `skip_quarters`.

### Presenter skip (`presenter.py`)
- `present_round()`, `_present_full_game()`, and `_present_game()` all accept `skip_quarters: int = 0`.
- `_present_game()` fast-forwards through skipped quarters: updates `LiveGameState` with final scores from each skipped quarter, then starts streaming from the next quarter normally.

### Startup recovery (`main.py`)
- On startup, calls `resume_presentation()` first. If no interrupted presentation is found, falls back to the existing mark-all-as-presented logic.

**Files modified (4):** `core/presenter.py`, `core/scheduler_runner.py`, `main.py`, `tests/test_presenter.py`, `tests/test_scheduler_runner.py`

**Tests added (9):** 3 presenter tests (skip early quarters, skip all quarters, skip zero), 6 scheduler_runner tests (persist/clear round-trip, resume with no state, invalid JSON, missing games, full reconstruct + start, skip_quarters calculation).

**524 tests, zero lint errors.**

**What could have gone better:** The `BotStateRow.value` column is `String(500)` which could be tight if there are many game IDs. For now it's fine (typical round has 2-3 games, UUIDs are 36 chars each), but a longer `Text` column would be more future-proof.

---

## Session 37 — Governance Interval-Based Tallying

**What was asked:** Replace the dead window-based governance tallying (no code ever writes `window.opened` events, so governance never ran) with interval-based tallying: every Nth round (default 3, configurable). Also fix governance notification timing (fire after presentation, not during simulation) and make `/vote` skip already-resolved proposals.

**What was built:**

### Interval-based governance tallying
- **Root cause:** `step_round()` checked for `window.opened` events to decide when to tally, but no code ever wrote those events. The `window_id` on proposals was always `""`. Governance tallying was a dead code path.
- **Fix:** New `PINWHEEL_GOVERNANCE_INTERVAL` config (default 3). `step_round()` now checks `round_number % governance_interval == 0`. Gathers all `proposal.confirmed` events that have no matching `proposal.passed`/`proposal.failed` (unresolved), deduplicates, gathers votes, and calls `tally_governance()`.
- New `tally_governance()` function in `governance.py` — same logic as `close_governance_window()` but takes `season_id` directly, no window concept, no `window.closed` event. `close_governance_window()` refactored to delegate to it.

### Governance notification timing
- Removed `governance.window_closed` EventBus publish from `step_round()` (was firing instantly during simulation).
- In instant mode: `tick_round()` publishes `governance.window_closed` alongside `presentation.round_finished`.
- In replay mode: `_present_and_clear()` publishes `governance.window_closed` after `present_round()` finishes, in the `finally` block.
- Added `governance_summary` field to `RoundResult` to carry the notification data from `step_round()` to `tick_round()`.

### `/vote` filters resolved proposals
- `_handle_vote()` now fetches `proposal.passed`/`proposal.failed` events and filters them out, so votes only target unresolved proposals.

**Files modified (6):** `config.py`, `core/governance.py`, `core/game_loop.py`, `core/scheduler_runner.py`, `main.py`, `discord/bot.py`

**Tests added (10):** 3 in `test_governance.py` (tally_governance enacts, no window.closed event, close_governance_window delegates), 5 in `test_game_loop.py` (tallies on interval round, skips non-interval, interval=1 every round, resolved not retallied, no governance event from step_round), 2 in `test_scheduler_runner.py` (instant mode publishes after presentation, governance_interval passed through).

**534 tests, zero lint errors.**

**What could have gone better:** Nothing major. The test for "resolved proposals not retallied" initially tried to use round 6, but the 4-team round-robin only generates 3 rounds of games. Fixed by using `governance_interval=1` with rounds 1 and 2 instead.

---

## Session 38 — Token Regeneration + GQI Fix

**What was asked:** After a doc audit revealed conflicts between product docs and implementation, prioritize: (1) wire token regeneration into the governance tally step, (2) fix GQI `compute_vote_deliberation()` bug, (3) put remaining doc updates on tomorrow's todo list.

**What was built:**

### Token regeneration wired into governance tally
- After governance tallying runs in `step_round()`, iterates over all teams and their enrolled governors, calling `regenerate_tokens()` for each.
- Grants 2 propose, 2 amend, 2 boost tokens per governor per governance cycle.
- Added structured logging: `tokens_regenerated season=... round=... governors=N`.
- 2 tests: tokens regenerated on governance tally round, tokens NOT regenerated on non-tally round.

### GQI `compute_vote_deliberation()` fix
- **Root cause:** The function depended on `window.opened` events (never written) to determine when voting started. Always returned the fallback 0.5.
- **Fix:** Now measures time from `proposal.confirmed` to `vote.cast` for each vote, normalized by `window_duration_seconds` (default 120s). Returns average normalized delay across all votes.
- Updated module docstring to reflect the new approach.
- 3 tests: no proposals returns 0.5, 60s delay with 120s window returns 0.5, instant vote returns 0.0.

**Files modified (2):** `core/game_loop.py`, `evals/gqi.py`

**Tests modified (2):** `tests/test_game_loop.py` (2 new), `tests/test_evals/test_gqi.py` (3 new)

**539 tests, zero lint errors.**

**What could have gone better:** Nothing — straightforward fixes guided by the doc audit.

---

## Today's Agenda (Day 9)

### P0: UX & Model — Decisions needed before alpha testers

- [x] **Rename "Governance" to "The Floor"** — 15 files renamed throughout UI, embeds, templates, docs. "The Floor Has Spoken." (Session 40)
- [x] **Voting UX overhaul** — `/vote` with proposal autocomplete, announcement embeds, vote counts + participation, per-proposal results. (Session 40)
- [x] **Admin veto for wild proposals** — `pending_review` status for Tier 5+ or confidence < 0.5, admin DM with Approve/Reject, token refund on reject. (Session 40)
- [x] **Governor profiles** — `/governors/{id}` web page + `/profile` Discord command with Floor record. (Session 40)
- [x] **Surface team identity** — Render team motto on team page + Discord embeds. Show team strategy on team page. Add `/bio` command for hooper bios from Discord. (Session 39)
- [x] **Fix hooper trade per-team majority** — Per-team majority voting with team-specific rejection messages. (Session 40)

### P1: Season lifecycle

- [x] **Season end detection** — Auto-detects completion, computes standings, generates playoff bracket. (Session 40)
- [x] **Season archiving** — `SeasonArchiveRow` table, `archive_season()`, web pages for archive list + detail. (Session 40)
- [x] **New season flow** — `start_new_season()` with team/hooper/governor carry-over, `/new-season` admin command, `POST /api/seasons`. (Session 40)

### P2: Doc updates (from Session 38 audit)

#### High priority (docs describe dead/wrong behavior)
- [x] **GAME_LOOP.md** — Rewrote "Three Clocks" → "Two Clocks", removed governance window concept, described interval-based tallying, updated state machine, fixed SSE event names, updated Dev/Staging table (Session 41)
- [x] **INTERFACE_CONTRACTS.md** — Fixed governance SSE events (`governance.window_closed`), marked dead event store types (`window.opened`, `window.closed`, `vote.revealed`), noted `proposal.pending_review` and `proposal.rejected` additions (Session 41)
- [x] **DEMO_MODE.md** + **OPS.md** + **CLAUDE.md** — Added `PINWHEEL_GOVERNANCE_INTERVAL`, `PINWHEEL_PRESENTATION_MODE`, `PINWHEEL_ADMIN_DISCORD_ID` env vars, fixed pace modes to fast/normal/slow/manual (Session 41)
- [x] **GLOSSARY.md** — Rewrote "Window" → "Tally Round", fixed "Boost" definition (doubles vote weight, not visibility) (Session 41)

#### Medium priority
- [x] **RUN_OF_PLAY.md** — Replaced twice-daily governance windows with interval-based round cadence model (Session 41)
- [x] **SIMULATION.md** — Fixed default shot clock (12 → 15), fixed halftime_stamina_recovery default (0.25 → 0.40) and range (0.5 → 0.6) (Session 41)
- [x] **ACCEPTANCE_CRITERIA.md** — Updated 11 criteria referencing governance windows → tally rounds (Session 41)

#### Low priority (cleanup)
- [ ] Remove dead code: `GovernanceWindow` model if no longer referenced, `window.opened`/`vote.revealed` event type constants in `models/governance.py`

### P2: Naming
- [x] **Rename "mirror" → "report"/"reporter"** — ~300 instances across 40+ files. Reporter = the agent/role in player-facing prose, report = the artifact/output in code. AI prompt text keeps "mirror" internally (4 instances). (Sessions 41–42)

---

## Session 39 — Surface Team Identity + /bio Command

**What was asked:** Surface existing team identity features (motto, strategy, hooper bios) that are stored in the database but never displayed. Add a `/bio` Discord command for writing hooper backstories. Show motto in Discord welcome embed. Show team strategy on team page. Show hooper backstory snippets in welcome embed.

**What was built:**

### Team motto in Discord welcome embed
- Modified `build_welcome_embed()` in `embeds.py` to accept `motto` parameter. Renders as italic quote below the team name in the embed description.
- Updated `/join` handler in `bot.py` to pass `motto=target_team.motto` to the embed builder.

### Team strategy on team page
- Modified `team_page()` in `pages.py` to query `strategy.set` governance events, find the latest one matching the team, and pass `team_strategy` to the template context.
- Added "Current Strategy" card to `team.html` template with italic quoted text.

### `/bio` Discord slash command
- Added `/bio` command registration in `_setup_commands()` with autocomplete for hooper names (own team only).
- Added `_handle_bio()` method with validation: enrollment check, empty text check, 500-char limit, hooper must be on governor's team.
- Calls `repo.update_hooper_backstory()` and returns ephemeral `build_bio_embed()` confirmation.
- Added `build_bio_embed()` function in `embeds.py`.

### Hooper backstories in welcome embed
- Modified `build_welcome_embed()` to render backstory snippets (first 100 chars with "..." truncation) as block-quoted lines under each hooper in the roster section.
- Updated `/join` handler to include `backstory` field in hooper dicts passed to the embed builder.
- Added `/bio` mention to the Quick Start section of the welcome embed.

### Tests (15 new)
- `TestHooperBackstory` in `test_db.py` (3 tests): update backstory, nonexistent hooper returns None, clear backstory to empty.
- `TestBioCommand` in `test_discord.py` (5 tests): not enrolled, empty text, text too long (500 chars), hooper not found, success with DB persistence verification.
- `TestWelcomeEmbedExtended` in `test_discord.py` (5 tests): with motto, without motto, with backstory, backstory truncation at 100 chars, /bio in quick start.
- `TestBuildBioEmbed` in `test_discord.py` (1 test): verifies embed title and description.
- Updated `test_bot_has_slash_commands` to include "bio" in expected commands.

### Pre-existing test fixes
- Fixed `test_governance_report` assertion: embed title changed from "Governance Report" to "The Floor" in a previous session but test was not updated.
- Fixed `test_tokens_shows_balance` assertion: embed title changed from "Governance Tokens" to "Floor Tokens" but test was not updated.

**Files modified (6):** `src/pinwheel/discord/embeds.py`, `src/pinwheel/discord/bot.py`, `src/pinwheel/api/pages.py`, `templates/pages/team.html`, `tests/test_db.py`, `tests/test_discord.py`

**627 tests, zero lint errors.**

**What could have gone better:** Two pre-existing test failures (assertions referencing old "Governance" branding instead of "The Floor") had to be fixed before the new tests could run. These should have been caught in the session that renamed the branding. The `get_hoopers_for_team` method is referenced in `bot.py` but doesn't exist in `repository.py` -- hoopers are accessed through the team relationship instead. This inconsistency should be cleaned up.

---

## Session 40 — 9-Feature Parallel Build

**What was asked:** Implement all 9 items from the Day 9 agenda: rename Governance to The Floor, voting UX overhaul, admin veto for wild proposals, governor profiles, surface team identity (done in S39), fix hooper trade per-team majority, season end detection, season archiving, new season flow. All launched as parallel background agents.

**What was built:**

### 1. Rename "Governance" → "The Floor"
- 15 files updated: embeds, bot, views, 8 templates, glossary, governor guide, tests
- User-facing strings only — internal code names unchanged
- Vote results now say "The Floor Has Spoken"

### 2. Voting UX overhaul
- `/vote` now has optional `proposal` parameter with autocomplete listing all open proposals
- Public "New Proposal on the Floor" announcement embed posted when proposals go live
- Vote tally shows raw counts alongside weighted totals: "2.50 (3 votes)"
- Participation field: "N of M possible voters (X%)"
- Per-proposal result embeds posted to Discord + team channels (not just generic summary)
- `VoteTally` model gained `yes_count`, `no_count`, `total_eligible` fields

### 3. Admin veto for wild proposals
- `pending_review` status for Tier 5+ or confidence < 0.5 proposals
- `AdminReviewView` with Approve/Reject buttons DM'd to admin (24h timeout)
- `AdminRejectReasonModal` for rejection with reason
- Token refund on admin rejection
- Config: `PINWHEEL_ADMIN_DISCORD_ID`

### 4. Governor profiles
- `/governors/{player_id}` web page with Floor record, proposal history, token balance
- `/profile` Discord command (ephemeral embed)
- Governor links on team pages
- New `get_governor_activity()` and `get_events_by_governor()` repository methods

### 5. Fix hooper trade per-team majority
- `HooperTrade` model gained `from_team_voters` and `to_team_voters` fields
- `tally_hooper_trade()` now checks per-team majority independently
- Team-specific rejection messages: "Trade Rejected — {team_name} voted against"

### 6. Season end detection + playoffs
- `_check_season_complete()` detects when all scheduled rounds are played
- `compute_standings_from_repo()` computes final W-L standings
- `generate_playoff_bracket()` creates #1v#4, #2v#3 bracket
- `season.regular_season_complete` event published with standings + bracket
- `RoundResult` gained `season_complete`, `final_standings`, `playoff_bracket` fields

### 7. Season archiving
- `SeasonArchiveRow` table: standings, ruleset, rule history, champion, aggregate counts
- `archive_season()` function gathers all data and creates snapshot
- Web pages: `/seasons/archive` (list) and `/seasons/archive/{id}` (detail with standings + rule timeline)

### 8. New season flow
- `start_new_season()` with optional rule carry-forward
- `carry_over_teams()` copies teams, hoopers, governor enrollments
- `/new-season` admin-only Discord command
- `POST /api/seasons` API endpoint
- Config: `PINWHEEL_CARRY_FORWARD_RULES`

**Files modified (~30+):** `config.py`, `core/governance.py`, `core/game_loop.py`, `core/tokens.py`, `core/season.py` (new), `db/models.py`, `db/repository.py`, `models/governance.py`, `models/tokens.py`, `discord/bot.py`, `discord/embeds.py`, `discord/views.py`, `api/pages.py`, `api/seasons.py` (new), `main.py`, 8 templates, `NEW_GOVERNOR_GUIDE.md`, `GLOSSARY.md`, 7 test files

**627 tests, zero lint errors.**

**What could have gone better:** Running 9 agents in parallel on overlapping files was risky — agents touching `bot.py`, `embeds.py`, and `views.py` could have conflicted. The fact that all changes integrated cleanly was fortunate — each agent edited different functions/sections. For future parallel builds, grouping by file ownership would be safer.

---

## Session 41 — Doc Updates (from Session 38 Audit)

**What was asked:** Update all docs that describe dead/wrong behavior (governance windows, incorrect defaults, missing env vars) identified in the Session 38 audit. Also rename "mirror" → "report"/"reporter" across the entire codebase.

**What was built:**

### Doc updates (7 files)
- **GAME_LOOP.md** — Rewrote "Three Clocks" → "Two Clocks", removed governance window concept, described interval-based tallying, updated state machine, fixed SSE event names
- **INTERFACE_CONTRACTS.md** — Fixed governance SSE events, marked dead event store types, noted new proposal statuses
- **DEMO_MODE.md + OPS.md + CLAUDE.md** — Added `PINWHEEL_GOVERNANCE_INTERVAL`, `PINWHEEL_PRESENTATION_MODE`, `PINWHEEL_ADMIN_DISCORD_ID` env vars
- **GLOSSARY.md** — Rewrote "Window" → "Tally Round", fixed "Boost" definition
- **RUN_OF_PLAY.md** — Replaced governance windows with interval-based cadence
- **SIMULATION.md** — Fixed default shot clock (12→15), halftime recovery (0.25→0.40)
- **ACCEPTANCE_CRITERIA.md** — Updated 11 criteria referencing governance windows → tally rounds

### Mirror → report rename started
- Renamed 3 Python source files (`models/mirror.py`, `ai/mirror.py`, `api/mirrors.py`) and 1 template
- Updated all Python identifiers in source files (models, repo, AI, core, API, discord, evals, main)
- Updated HTML templates and CSS classes
- Renamed test file `test_mirrors.py` → `test_reports.py`

**Files modified (~30):** 7 docs, 15+ source files, 6 templates, 1 CSS file, 8 test files

**627 tests, zero lint errors.**

**What could have gone better:** The docs agent approach (one Edit call per reference × 553 references in 26 doc files) was too slow. Bulk `sed` across all docs files completed instantly. For high-volume mechanical renames, always prefer batch tools over iterative edits.

---

## Session 42 — Complete Mirror→Report Rename + Guide Review

**What was asked:** Continue and complete the mirror→report/reporter rename from Session 41. Then review changes to `NEW_GOVERNOR_GUIDE.md`.

**What was built:**

### Completed the rename across all remaining files
- Fixed `embeds.py`: `COLOR_MIRROR` → `COLOR_REPORT`
- Fixed `home.html`: comment `STANDINGS + MIRROR` → `STANDINGS + REPORT`
- Updated all test files: `test_models.py`, `test_db.py`, `test_reports.py`, `test_discord.py`, `test_game_loop.py`, `test_scheduler_runner.py`, `test_pages.py`, `test_commentary.py`, 5 eval test files
- Updated `demo_seed.py` and `run_demo.sh`
- Bulk sed across 26 docs files (553 replacements), plus 3 manual fixes for uppercase variants in ASCII art
- Ran `ruff format` (43 files), `ruff check --select I --fix` (3 import sort fixes)

### Reviewed NEW_GOVERNOR_GUIDE.md
- All 15 rename sites read naturally ("the reporter is watching", "noted by the reporter", etc.)
- "the AI" exception at line 284 correctly preserved
- Found and fixed pre-existing corruption at line 54: heading "## The Floor: How to Reshape the G..." was smashed into body text with escaped backticks

### Verification
- Final grep: 0 unintended "mirror" references (only 4 intentional in `ai/report.py` prompt text)
- 627 tests pass, zero lint errors

**Files modified (~45):** `discord/embeds.py`, `templates/pages/home.html`, 12 test files, `scripts/demo_seed.py`, `scripts/run_demo.sh`, 26 docs files, `docs/NEW_GOVERNOR_GUIDE.md` (formatting fix)

**627 tests, zero lint errors.**

**What could have gone better:** The rename spanned two context sessions due to the sheer number of files. Starting with a comprehensive grep inventory up front (which we did) was the right call — it prevented missed references. The decision to use bulk sed for docs instead of individual Edit calls saved significant time.
