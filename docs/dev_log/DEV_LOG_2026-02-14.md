# Pinwheel Dev Log — 2026-02-14

Previous logs: [DEV_LOG_2026-02-10.md](DEV_LOG_2026-02-10.md) (Sessions 1-5), [DEV_LOG_2026-02-11.md](DEV_LOG_2026-02-11.md) (Sessions 6-16), [DEV_LOG_2026-02-12.md](DEV_LOG_2026-02-12.md) (Sessions 17-33), [DEV_LOG_2026-02-13.md](DEV_LOG_2026-02-13.md) (Sessions 34-47)

## Where We Are

- **1163 tests**, zero lint errors (Session 70)
- **Days 1-7 complete:** simulation engine, governance + AI interpretation, reports + game loop, web dashboard + Discord bot + OAuth + evals framework, APScheduler, presenter pacing, AI commentary, UX overhaul, security hardening, production fixes, player pages overhaul, simulation tuning, home page redesign, live arena, team colors, live zone polish
- **Day 8:** Discord notification timing, substitution fix, narration clarity, Elam display polish, SSE dedup, deploy-during-live resilience
- **Day 9:** The Floor rename, voting UX, admin veto, profiles, trades, seasons, doc updates, mirror→report rename
- **Day 10:** Production bugfixes — presentation mode, player enrollment, Discord invite URL
- **Day 11:** Discord defer/timeout fixes, get_active_season migration, playoff progression pipeline
- **Day 12:** P0 fixes — /join, score spoilers, strategy system, trade verification, substitution verification
- **Day 13:** Self-heal missing player enrollments, decouple governance from game simulation
- **Day 14:** Admin visibility, season lifecycle phases 1 & 2
- **Live at:** https://pinwheel.fly.dev
- **Day 15:** Tiebreakers, offseason governance, season memorial, injection evals, GQI/rule evaluator wiring, Discord UX humanization
- **Latest commit:** Session 70 (Wave 3 complete — e2e verification + workbench)

## Day 13 Agenda (Governance Decoupling + Hackathon Prep) — COMPLETE

### Governance decoupling
- [x] Extract `tally_pending_governance()` from `step_round()` (Session 48)
- [x] Add governance-only path in `tick_round()` for completed seasons (Session 48)
- [x] Clean up interaction mock pattern in `test_discord.py` (Session 48)
- [x] Add lifecycle integration tests (Session 48)
- [x] Fix governance user journey P0 — tokens on `/join` (Session 49)
- [x] Lifecycle SVG diagram (Session 49)

---

## Day 14 Agenda (End-to-End User Experience)

Focus: a new user should be able to `/join`, govern, watch games, and experience a full season arc without hitting dead ends.

### P0 — Broken UX (users hit dead ends)
- [x] **Season Lifecycle (Phases 1 & 2)** — Phase enum (SETUP→ACTIVE→PLAYOFFS→CHAMPIONSHIP→OFFSEASON→COMPLETE), championship ceremony with awards. Phases 3 (offseason) and 4 (tiebreakers) deferred. *(Session 50)*
- [x] **Admin visibility / governor roster** — `/roster` Discord command + `/admin/roster` web page. *(Session 50)*
- [x] **Proposal Effects System** — Proposals can do ANYTHING, not just tweak RuleSet parameters. Callbacks at every hook point in the system, meta JSON columns on all entities, effect execution engine. The game starts as basketball and finishes as ???. *(Session 63)*
- [x] **Season schedule fix** — Seasons stopping after 5 rounds / 7 games. Root cause: `num_cycles=1` default in `generate_round_robin()`. Fix: restructured so each round = 1 complete round-robin (6 games with 4 teams), renamed `num_cycles` → `num_rounds`, `governance_interval` default → 1. 725 tests pass.
- [x] **Remove Alembic** — Removed from `pyproject.toml` (+ transitive dep `mako`). Never imported anywhere. 725 tests pass.

### P1 — Thin UX (works but feels empty)
- [x] **NarrativeContext module** — Dataclass computed per round with standings, streaks, rivalries, playoff implications, rule changes. Passed to all output systems so commentary/reports/embeds reflect dramatic context. *(Session 65)*
- [x] **Game Richness audit** — Audit all player-facing outputs against `GAME_MOMENTS.md`. Playoff games should feel different from regular season. Championship finals should feel epic. *(Session 66)*
- [x] **Multi-parameter interpretation + expanded RuleSet** — 5 new RuleSet params (turnover/foul rate modifiers, offensive rebound weight, stamina drain, dead ball time), compound proposals, multi-parameter tally. *(Session 66)*

### P0.5 — Critical pre-hackathon
- [x] **End-to-end workflow verification** — 48-test e2e suite covering full player journey. No production bugs found — all Wave 1-2 integrations work correctly. *(Session 70)*
- [ ] **Reset season history to 0** — Clear all season/game data but retain user and team associations (player enrollments, team names/colors/mottos). Fresh start for hackathon demo with real players still enrolled.

### P2 — Missing features (complete the arc)
- [x] **Playoff progression fixes** — Best-of-N series + deferred events during replay. *(Session 61)*
- [x] **Offseason governance** — Configurable governance window between seasons (`PINWHEEL_OFFSEASON_WINDOW`). Championship → offseason → complete. *(Session 54)*
- [x] **Tiebreakers** — Head-to-head, point differential, points scored. Tiebreaker games when all three criteria tie. *(Session 54)*
- [x] **Season memorial data** — Statistical leaders, key moments, head-to-head records, rule timeline. Data backbone for end-of-season reports. *(Session 54)*
- [ ] **Demo verification** — Run full Showboat/Rodney pipeline, update screenshots for hackathon submission. *(Small)*

### P3 — Infrastructure (quality of life)
- [x] **Workbench + safety layer** — Admin review queue + safety workbench with injection classifier test bench. *(Session 70)*
- [x] **GameEffect hooks** — Wired effect lifecycle into game loop: load→fire→tick→flush. Governance tally uses effects-aware path. *(Session 66)*
- [x] **Cleanup** — Remove dead `GovernanceWindow` model, rebounds in narration. *(Session 65)*

### Wave execution plan (Session 62+)

Remaining work structured into four waves optimized for parallelism and dependency order.

**Wave 1 — Foundation (parallel, no interdependencies)**
- [x] **Proposal Effects System** (P0, large) — Rewires how proposals execute. Callbacks at every hook point, meta JSON columns, effect execution engine. Independent of output systems. *(Session 63)*
- [x] **NarrativeContext module** (P1, medium) — Read-only data aggregation layer (streaks, rivalries, playoff implications) that feeds all output systems. Independent of proposal mechanics. *(Session 65)*
- [x] **Cleanup** (P3, small) — Remove dead `GovernanceWindow` model, rebounds in narration. Trivial, no dependencies. *(Session 65)*

*Why parallel:* These three touch entirely different subsystems. Proposal Effects rewires governance execution. NarrativeContext is a read-only layer for output enrichment. Cleanup is dead code removal. No conflicts.

**Wave 2 — Build on foundations (parallel, each depends on a Wave 1 item)**
- [x] **Game Richness audit** (P1, medium) — Audit all player-facing outputs against `GAME_MOMENTS.md`. *Depends on NarrativeContext* — that module provides the dramatic context data this audit wires into outputs. *(Session 66)*
- [x] **GameEffect hooks** (P3, medium) — Rule changes trigger visual/mechanical effects in simulation. *Depends on Proposal Effects* — effects need the hook points and execution engine from the effects system. *(Session 66)*
- [x] **Multi-parameter interpretation + expanded RuleSet** (P1, medium) — Compound proposals, more tunable parameters. *Depends on Proposal Effects* — expands the target space proposals can hit, needs the broader effects architecture in place first. *(Session 66)*

*Why parallel:* All three depend on Wave 1 items but are independent of each other. Game Richness touches output templates. GameEffect hooks touch simulation. Multi-parameter interpretation touches the AI interpreter. No conflicts.

**Wave 3 — Verify (after Waves 1-2 land)**
- [x] **End-to-end workflow verification** (P0.5) — 48-test e2e suite. No production bugs found. *(Session 70)*
- [x] **Workbench + safety layer** (P3, large) — Admin review queue (`/admin/review`) + safety workbench (`/admin/workbench`) with injection classifier test bench. *(Session 70)*

*Why here:* E2e verification must follow the architectural changes in Waves 1-2 to be meaningful. Running it earlier would just verify the old system. Workbench is truly independent but lower priority (P3), so it slots here rather than competing for attention in Wave 1.

**Wave 4 — Hackathon prep (sequential, do last)**
1. [ ] **Reset season history to 0** — Clear game/season data, retain player enrollments. Fresh slate for demo.
2. [ ] **Demo verification** — Run full Showboat/Rodney pipeline, capture updated screenshots on the clean slate.

*Why last and sequential:* Reset destroys data — must happen after all features are verified. Demo captures the final state — must happen after reset. Order is non-negotiable.

**Critical path:** Proposal Effects → GameEffect hooks + Multi-param interpretation → E2E verification → Reset → Demo. The NarrativeContext → Game Richness track runs in parallel and doesn't block the critical path.

### Open issues (deferred)
- [x] Future: Rebounds in play-by-play narration *(Session 65)*
- [x] Future: Best-of-N playoff series *(Session 61)*
- [x] Cleanup: Remove dead `GovernanceWindow` model if no longer referenced *(Session 65)*

---

## Session 48 — Decouple Governance from Game Simulation

**What was asked:** Implement the plan to decouple governance tallying from game simulation. The core bug: governance is coupled to game simulation — when a season completes, `tick_round()` exits immediately, so governance tallying never runs. Proposals and votes are accepted but never tallied on completed seasons.

**What was built:**

### Extract `tally_pending_governance()` from `step_round()`
- Created standalone async function in `game_loop.py` that gathers confirmed-but-unresolved proposals, reconstructs `Proposal` objects from submitted events, gathers votes, calls `tally_governance()`, updates the season ruleset if changed, and returns `(updated_ruleset, tallies, governance_data)`.
- Refactored `step_round()` to call `tally_pending_governance()` instead of inlining ~90 lines of governance logic. The `governance_interval` modulo check stays in `step_round()`.

### Add governance-only path in `tick_round()`
- Changed the completed/archived season early return in `scheduler_runner.py`: instead of skipping entirely, it now queries the last round number, calls `tally_pending_governance()`, and publishes a `governance.window_closed` event if there are tallies.
- No `governance_interval` modulo check for completed seasons — tallies immediately whenever pending proposals exist.

### Clean up interaction mock pattern in `test_discord.py`
- Added `make_interaction(**overrides)` helper that builds a fully-configured Discord interaction mock with `response`, `followup`, `user` (with `spec=discord.Member`), `send`, and `channel`.
- Replaced all 15 scattered mock setup blocks with calls to `make_interaction()`.

### Lifecycle integration tests (4 new)
- `test_governance.py`: `TestGovernanceLifecycleAcrossSeasonCompletion` — 3 tests: tally pending on completed season (full propose→vote→enact cycle), governance with null/default ruleset, no pending proposals is noop.
- `test_scheduler_runner.py`: `test_tick_round_tallies_governance_on_completed_season` — verifies `tick_round()` publishes `governance.window_closed` when season is completed but proposals are pending.

**Files modified (5):** `src/pinwheel/core/game_loop.py`, `src/pinwheel/core/scheduler_runner.py`, `tests/test_discord.py`, `tests/test_governance.py`, `tests/test_scheduler_runner.py`

**665 tests (6 new), zero lint errors.**

**What could have gone better:** The initial refactor of `step_round()` only built `governance_summary` when `tallies` was non-empty, but the existing test `test_resolved_proposals_not_retallied` expected `governance_summary` to exist even with 0 proposals (the old code always built it inside the governance interval block). Caught by tests immediately.

---

## Session 49 — Fix Governance User Journey P0 + Lifecycle Diagram

**What was asked:** Implement the plan to fix the governance user journey P0: governors who `/join` get zero tokens and can't propose. Also generate a comprehensive SVG lifecycle chart of the entire game.

**What was built:**

### Grant initial tokens on `/join`
- Added `regenerate_tokens()` call after `enroll_player()` in `_handle_join()` so new governors immediately receive 2 PROPOSE, 2 AMEND, 2 BOOST tokens and can propose without waiting for the next governance interval.

### Grant tokens during self-heal enrollment sync
- Added `regenerate_tokens()` call after `enroll_player()` in `_sync_role_enrollments()` so governors re-enrolled after a DB reseed also get tokens.

### Better zero-token UX
- Updated `build_token_balance_embed()` in `embeds.py` to show a helpful message when all tokens are zero: "You have no tokens. Tokens regenerate at the next governance interval."
- Updated the "no PROPOSE tokens" error in `/propose` to tell users about `/tokens` and regeneration timing.

### Tests (5 new)
- `test_discord.py`: `test_join_grants_tokens` — full DB integration test verifying `/join` grants 2/2/2 tokens.
- `test_discord.py`: `TestBuildTokenBalanceEmbed` — 2 tests for zero/nonzero balance embed message.
- `test_governance.py`: `TestMidSeasonGovernorTokens` — 2 tests: mid-season governor has tokens after regen, mid-season governor can submit a proposal.

### Comprehensive lifecycle SVG
- Created `docs/LIFECYCLE.svg` (80KB, 14 sections) covering: season creation, player join flow, game loop orchestration, governance flow (propose/vote/tally/enact), token economy, customization features (/strategy, /bio, /trade-hooper), AI integration, API routes, season completion/playoffs, round timeline, event sourcing model, Discord channel map, and complete slash command reference.

**Files modified (5):** `src/pinwheel/discord/bot.py`, `src/pinwheel/discord/embeds.py`, `tests/test_discord.py`, `tests/test_governance.py`, `docs/LIFECYCLE.svg`

**670 tests (5 new), zero lint errors.**

**What could have gone better:** Initial file paths in the plan referenced `src/pinwheel/...` without the `Pinwheel/` parent directory prefix. The glob search found the correct paths quickly.

---

## Session 50 — Admin Visibility + Season Lifecycle Phases 1 & 2

**What was asked:** Implement the two P0 items from the Day 14 agenda: admin visibility / governor roster (small) and season lifecycle phases 1 & 2 (large). Both ran as background tasks overnight.

**What was built:**

### Admin Visibility / Governor Roster (7 new tests)
- **`/roster` Discord command** — shows all enrolled governors with team, token balances (P/A/B format), proposals submitted, votes cast. Uses `build_roster_embed()`.
- **`/admin/roster` web page** — admin-gated HTML table with governor name (linked to profile), team (with color dot), PROPOSE/AMEND/BOOST tokens, proposals submitted/passed/failed, votes cast. Auth-gated via `PINWHEEL_ADMIN_DISCORD_ID`.
- **`build_roster_embed()`** in `embeds.py` — handles empty roster, truncates at Discord's 4096 char limit, uses governance blue styling.
- **New route module** `api/admin_roster.py` + template `templates/pages/admin_roster.html`, registered in `main.py`.
- **Tests:** `/roster` command registration, no-engine handler, `TestBuildRosterEmbed` (3 tests), `TestAdminRoster` (3 tests in test_pages.py).

### Season Lifecycle Phases 1 & 2 (40 new tests)
- **`SeasonPhase` enum** (StrEnum, 8 phases) in `core/season.py` with `normalize_phase()` for backward compatibility (maps `"completed"` → `COMPLETE`, `"archived"` → `COMPLETE`).
- **`ALLOWED_TRANSITIONS` dict** — validates legal phase transitions. `COMPLETE` is terminal.
- **`ACTIVE_PHASES` frozen set** — everything except SETUP and COMPLETE counts as active.
- **`transition_season()`** — validates transition, updates status, publishes `season.phase_changed` event, raises `ValueError` for invalid transitions.
- **`compute_awards()`** — 6 awards: MVP (PPG), Defensive Player (SPG), Most Efficient (FG%), Most Active Governor (proposals + votes), Coalition Builder (trades), Rule Architect (pass rate).
- **`enter_championship()`** — transitions to CHAMPIONSHIP, computes awards, stores config JSON (`champion_team_id`, `awards`, `championship_ends_at`), publishes `season.championship_started` event.
- **`step_round()` updated** — playoff completion now calls `enter_championship()` instead of direct `"completed"` status.
- **`tick_round()` updated** — handles CHAMPIONSHIP phase: checks expiry window, transitions to COMPLETE when expired.
- **Discord notifications** — `season.championship_started` sends gold embed with champion + awards; `season.phase_changed` to `"complete"` sends completion embed.
- **`get_active_season()` updated** — excludes `"complete"` and `"setup"`, so championship/offseason/tiebreaker phases are active.
- **Full backward compatibility** — Season 1 `"completed"` data unaffected.
- **Tests:** New `test_season_lifecycle.py` (40 tests across 8 classes): enum, normalize, transitions, awards, championship, full lifecycle, scheduler handling, active-season queries.

**Files modified (12):** `src/pinwheel/core/season.py`, `src/pinwheel/core/game_loop.py`, `src/pinwheel/core/scheduler_runner.py`, `src/pinwheel/db/repository.py`, `src/pinwheel/discord/bot.py`, `src/pinwheel/discord/embeds.py`, `src/pinwheel/api/admin_roster.py` (new), `src/pinwheel/main.py`, `templates/pages/admin_roster.html` (new), `tests/test_discord.py`, `tests/test_pages.py`, `tests/test_game_loop.py`, `tests/test_season_lifecycle.py` (new)

**717 tests (47 new), zero lint errors.**

**What could have gone better:** Both tasks ran as parallel background agents. The season lifecycle agent reported 717 tests but the admin visibility agent reported 638 — likely because it ran its subset before the other agent's tests existed. Running the full suite after both completed confirmed 717 all passing with no conflicts.

---

## Session 51 — Governor Proposal Inspection + Hooper Trade Fix

**What was asked:** Investigate why `/roster` shows 0 proposals for JudgeJedd despite having submitted one. Find out what happened to the proposal. Fix any ID mismatch bugs. Make the bot powerful enough to answer these questions itself.

**What was built:**

### Production investigation
- Queried production database via `fly ssh`: JudgeJedd's proposal ("The first team to score 69 points wins the game") was Tier 5, confidence 0.3 — flagged for admin review. It was stuck in `pending_review` in Season 1 (now completed). Admin notification DM was likely lost during early deploys.
- Formally rejected the proposal with a message explaining the season ended before review. Refunded 2 PROPOSE tokens.
- Sent JudgeJedd a DM explaining what happened and encouraging resubmission in Season TWO.
- Posted a public announcement in #general honoring the league's first-ever proposal.

### Fix hooper trade ID mismatch (bug)
- `bot.py:2128` used `str(interaction.user.id)` (Discord snowflake) as `proposer_id` instead of `gov.player_id` (UUID). This caused hooper trade events to store the wrong `governor_id`, making them invisible to activity queries. Fixed to use `gov.player_id`.

### Fix `get_governor_activity` — detect `pending_review` and `rejected` status
- `repository.py:get_governor_activity()` now queries for `proposal.pending_review` and `proposal.rejected` events, correctly assigning those statuses instead of showing them as generic "pending".

### New `/proposals` command
- Shows all proposals in the current season (or all seasons) with full lifecycle status labels: Submitted, Awaiting Admin Review, On the Floor (voting open), Passed, Failed, Rejected by Admin.
- Displays proposer name, tier, parameter, and status for each proposal.
- Season filter parameter: "Current season" or "All seasons".

### Enhanced `/profile` embed
- Now shows individual proposal details with status badges below the summary. Governors can see exactly what happened to each of their proposals.

### New `_STATUS_LABELS` dict + `build_proposals_embed()` in embeds.py
- Human-readable status labels for all proposal lifecycle states.
- `build_proposals_embed()` builds a Discord embed listing up to 10 proposals with status, tier, parameter, and proposer name.

### New repository methods
- `get_all_proposals(season_id)` — returns all proposals in a season with full lifecycle status.
- `get_all_seasons()` — returns all seasons, most recent first.
- `get_all_players()` — returns all players regardless of season or team.

### Tests (8 new)
- `test_db.py`: `TestGovernorActivity` — 5 tests: `pending_review` status detected, `rejected` status detected, `get_all_proposals` with mixed statuses, `get_all_seasons`, `get_all_players`.
- `test_discord.py`: `TestProposalsEmbed` — 3 tests: empty proposals, proposals with data and governor names, profile embed shows proposal details.

**Files modified (4):** `src/pinwheel/discord/bot.py`, `src/pinwheel/db/repository.py`, `src/pinwheel/discord/embeds.py`, `tests/test_db.py`, `tests/test_discord.py`

**725 tests (8 new), zero lint errors.**

**What could have gone better:** The `fly ssh` command doesn't support shell pipes, so the initial DB query failed on quote escaping. Writing a standalone Python script and piping it via stdin (`cat script.py | fly ssh console -C "python -"`) worked cleanly. Also, Discord secrets are named `DISCORD_BOT_TOKEN` not `DISCORD_TOKEN` in Fly — the notification script needed a fallback lookup.

---

## Session 52 — Remove "The AI Sees" Branding

**What was asked:** Remove "The AI Sees" section title on the home page and simplify the reports page tagline to just "The reporter describes — it never prescribes."

**What was built:**
- Renamed home page report section from "The AI Sees" to "Reports"
- Trimmed reports page tagline from "AI-generated reports on gameplay and the Floor. The reporter describes — it never prescribes." to just "The reporter describes — it never prescribes."

**Files modified (2):** `templates/pages/home.html`, `templates/pages/reports.html`

**725 tests, zero lint errors.**

**What could have gone better:** Nothing — straightforward copy change.

---

## Session 53 — Architecture Plans + CLAUDE.md Updates

**What was asked:** Save the completed TECH_ARCHITECTURE.md and API_ARCHITECTURE.md plans to `docs/plans/`. Update CLAUDE.md with all 15 Discord commands and governance_interval default. Fix season schedule (3 rounds of 6 games each). Remove Alembic. Write plan for Proposal Effects System.

**What was built:**
- Saved `docs/plans/2026-02-14-tech-architecture-doc.md` — 12-section plan covering simulation engine, season lifecycle, presenter system, AI systems, governance pipeline, hooks/effects, database schema, env vars, eval framework, round orchestration, event bus, key design decisions
- Saved `docs/plans/2026-02-14-api-architecture-doc.md` — 9-section plan covering all REST endpoints, SSE streaming, Discord commands/views/events, web pages, auth flow, design decisions
- Updated CLAUDE.md: all 15 Discord commands documented in table, `PINWHEEL_GOVERNANCE_INTERVAL=1` (was 3), bot.py comment updated to reflect 15 commands
- Season schedule restructured (via background agent): `num_cycles` → `num_rounds`, each round = 1 complete round-robin (6 games), `governance_interval` default → 1
- Alembic removed from `pyproject.toml` (via background agent)
- Proposal Effects System plan written: callbacks everywhere, meta columns on 7 tables, effect execution engine

**Files modified (11):** `docs/plans/2026-02-14-tech-architecture-doc.md` (new), `docs/plans/2026-02-14-api-architecture-doc.md` (new), `docs/plans/2026-02-14-proposal-effects-system.md` (new), `docs/plans/2026-02-14-season-memorial-system.md` (new), `CLAUDE.md`, `pyproject.toml`, `src/pinwheel/core/scheduler.py`, `src/pinwheel/config.py`, `src/pinwheel/core/game_loop.py`, `src/pinwheel/core/scheduler_runner.py`, `scripts/demo_seed.py`

**725 tests, zero lint errors.**

**What could have gone better:** Context ran out mid-session due to many large background agents running in parallel. The API_ARCHITECTURE plan completed but wasn't saved before the context compacted. Recovered cleanly on resume.

---

## Session 54 — Tiebreakers + Offseason + Memorial + Evals Wiring + Discord UX

**What was asked:** Run the post-commit checklist. The uncommitted changes from the previous session(s) included tiebreaker logic, offseason governance, season memorial data, injection classifier evals, GQI/rule evaluator wiring into the game loop, Discord error message humanization, eval dashboard expansion, and RUN_OF_PLAY updates.

**What was built:**

### Tiebreaker system (`season.py`, +458 lines)
- `check_tiebreakers()` resolves ties at playoff cutoff using three criteria in order: (a) head-to-head record, (b) point differential, (c) points scored.
- `_compute_head_to_head()` helper scans game results for direct matchups between tied teams.
- When all three criteria are identical, flags that tiebreaker games are needed.
- `step_round()` updated to recognize `tiebreaker_check` and `tiebreakers` season statuses.

### Offseason governance (`season.py` + `scheduler_runner.py`)
- `enter_offseason()` transitions season from championship to offseason phase, creates a governance window for meta-rule changes between seasons.
- Scheduler runner now transitions championship → offseason → complete (was championship → complete).
- Configurable window duration via `PINWHEEL_OFFSEASON_WINDOW` setting (default 3600s).
- During offseason, `tick_round()` tallies governance proposals on every tick and closes the window when expired.

### Season memorial data (`memorial.py`, new, 406 lines)
- `compute_statistical_leaders()` — top 3 per category (PPG, APG, SPG, FG%).
- Key moments, head-to-head records, rule timeline data collection.
- Data backbone for end-of-season narrative reports (AI generation is separate phase).

### Injection classifier evals (`injection.py`, new, 93 lines)
- `store_injection_classification()` stores prompt injection classification results as eval records.
- No private report content stored — only classification outcome and truncated preview.

### GQI + Rule Evaluator wired into game loop
- `_run_evals()` now runs GQI computation (`compute_gqi` + `store_gqi`) after each round.
- `_run_evals()` now runs Opus-powered rule evaluation (`evaluate_rules` + `store_rule_evaluation`) after each round.
- Both pass `api_key` through from `step_round()`.

### Eval dashboard expansion
- Additional eval types shown on `/admin/evals` page (template + route updates).
- New `InjectionClassification` Pydantic model in `evals/models.py`.

### Discord error message humanization
- All bot error messages rewritten to be actionable and friendly.
- "Database not available" → "The league database is temporarily unavailable. Try `/join` again in a moment -- if this persists, let an admin know."
- "No active season" → "There's no active season right now. Ask an admin to start one with `/new-season`."
- Generic "Something went wrong" messages now include specific recovery actions and context-aware suggestions (e.g., locked DB vs team-specific vs general failures).
- `GovernorNotFound` message updated in `helpers.py`.

### RUN_OF_PLAY.md expanded (+121 lines)
- Additional product documentation for the run of play.

### Test fixes (3)
- Fixed `test_handle_roster_no_engine` — assert "unavailable" instead of old "Database not available".
- Fixed `test_trade_target_not_enrolled` — assert "isn't enrolled" instead of old "not enrolled".
- Fixed `test_governor_not_found_no_season` — case-insensitive regex for "no active season".
- Fixed lint error in `bot.py:1799` — line too long.

**Files modified (21):** `docs/product/RUN_OF_PLAY.md`, `src/pinwheel/api/eval_dashboard.py`, `src/pinwheel/api/governance.py`, `src/pinwheel/config.py`, `src/pinwheel/core/game_loop.py`, `src/pinwheel/core/scheduler_runner.py`, `src/pinwheel/core/season.py`, `src/pinwheel/db/models.py`, `src/pinwheel/discord/bot.py`, `src/pinwheel/discord/helpers.py`, `src/pinwheel/discord/views.py`, `src/pinwheel/evals/ab_compare.py`, `src/pinwheel/evals/models.py`, `src/pinwheel/evals/rubric.py`, `src/pinwheel/main.py`, `src/pinwheel/models/report.py`, `templates/pages/eval_dashboard.html`, `tests/test_discord.py`, `tests/test_evals/test_eval_dashboard.py`, `tests/test_game_loop.py`, `tests/test_season_lifecycle.py`

**New files (5):** `src/pinwheel/core/memorial.py`, `src/pinwheel/evals/injection.py`, `tests/test_memorial.py`, `tests/test_evals/test_injection.py`, `tests/test_evals/test_eval_wiring.py`

**840 tests (115 new), zero lint errors.**

**What could have gone better:** Three test assertions broke because error messages were humanized but the tests still asserted the old strings. Caught immediately by the test run — quick pattern-match fixes.

---

## Session 55 — Fix SQLite Write Lock Contention During `/join`

**What was asked:** Players get "Something went wrong joining the team" when `/join` coincides with `tick_round`. Root cause: `tick_round` holds a single SQLite session for 30-90 seconds while `step_round` interleaves fast DB writes with slow AI API calls. SQLite allows only one writer — so `/join`, `/propose`, `/vote` all fail during that window. Fix: release the DB write lock between AI calls.

**What was built:**

### Phase dataclasses (`game_loop.py`)
- `_SimPhaseResult` — carries simulation/governance data between phases (teams, game results, tallies, governor activity).
- `_AIPhaseResult` — carries AI-generated content (commentaries, highlight reel, reports).

### Three extracted phase functions (`game_loop.py`)
- `_phase_simulate_and_govern()` — Session 1 (~2-3s): load season/teams, simulate games, store results + box scores, tally governance, query governor activity. No AI calls.
- `_phase_ai()` — No DB session: generate commentary, highlights, simulation/governance/private reports via mock or API. Pure I/O.
- `_phase_persist_and_finalize()` — Session 2 (~1-2s): attach commentary, store reports, run evals, season progression checks, publish round.completed event.

### `step_round_multisession(engine, ...)` (`game_loop.py`)
- New function that opens/closes separate DB sessions per phase. The write lock is released during the 30-90s AI phase, allowing Discord commands to write freely.

### `step_round()` refactored (`game_loop.py`)
- Body replaced with calls to the three phase functions. Same signature, same behavior, backward-compatible.

### `tick_round()` restructured (`scheduler_runner.py`)
- Pre-flight session: get active season, handle championship/offseason/completed checks, determine next round number. Session closed.
- Calls `step_round_multisession(engine, ...)` — manages its own sessions with lock release.
- Post-round session: mark games presented (instant mode), publish presentation events.

### Lock timeline after fix
```
Pre-flight session (~1s): get season, check status, determine round
   [LOCK RELEASED]
Session 1 (~2-3s): simulate games, store results, tally governance
   [LOCK RELEASED — /join, /propose, /vote can write here]
AI calls (~30-90s): commentary, highlights, reports (NO session)
   [LOCK RELEASED — /join, /propose, /vote can write here]
Session 2 (~1-2s): store reports, run evals, season progression
   [LOCK RELEASED]
Post-round session (~1s): mark games presented
   [LOCK RELEASED]
```

### Tests (17 new)
- `TestPhaseSimulateAndGovern` (4): returns `_SimPhaseResult`, handles empty rounds, governance tally, DB storage.
- `TestPhaseAI` (2): returns `_AIPhaseResult` with mock content, private reports for active governors.
- `TestPhasePersistAndFinalize` (2): stores reports, attaches commentary to summaries.
- `TestStepRoundMultisession` (5): produces same results, stores games/reports in DB, handles empty rounds, publishes events.
- `TestStepRoundBackwardCompat` (2): existing `step_round` still works identically.
- `TestMultisessionLockRelease` (2): verifies tick_round uses multisession, proves concurrent DB access succeeds during AI phase.

**Files modified (4):** `src/pinwheel/core/game_loop.py`, `src/pinwheel/core/scheduler_runner.py`, `tests/test_game_loop.py`, `tests/test_scheduler_runner.py`

**857 tests (17 new), zero lint errors.**

**What could have gone better:** Nothing significant — the phase extraction was clean, all 53 existing tests passed on first run after refactoring.

---

## Session 56 — Home Page Season Fix + Play Page Overhaul + /join Required

**What was asked:** Three issues: (1) Home page shows "Season 1" even though multiple seasons have been played and a new season "THREE" was started. (2) No schedule of upcoming games visible on the home page. (3) `/join` command in Discord says team name is optional — it should be required. Also: audit the `/play` page against RUN_OF_PLAY to ensure new players know how to join, what part of the season they're in, and what's happening next.

**What was built:**

### Fix `_get_active_season_id()` — root cause of both home page bugs
- The function used `select(SeasonRow).limit(1)` which always returned the first season ever created (Season 1), not the current active season.
- Replaced with `repo.get_active_season()` which filters by non-terminal status (excludes completed/archived/setup).
- Added `_get_active_season()` helper that returns `(season_id, season_name)` tuple for pages that need the name.
- This fixes ALL pages site-wide, not just the home page — standings, arena, governance, rules, reports, team profiles, and hooper profiles all called `_get_active_season_id()`.

### Dynamic season name on home page
- Passed `season_name` from the DB to the template context.
- Replaced hardcoded `"Season 1"` with `{{ season_name }}` in the hero pulse bar.

### Missing upcoming schedule fix
- Same root cause: when `_get_active_season_id()` returned Season 1 (completed), there was no "next round" to schedule. Now it returns the active season which has upcoming rounds.

### `/play` page overhaul — aligned with RUN_OF_PLAY
- **Season context**: Shows current season name, phase description ("Regular season in progress — Round N complete"), teams, and games played.
- **How to Join section**: Step-by-step with team names listed, token grants explained (2 PROPOSE, 2 AMEND, 2 BOOST).
- **Season Structure section**: Visual flow diagram — Regular Season → Playoffs → New Season — explaining round-robin, best-of-3 semis, best-of-5 finals.
- **Governance Tokens section**: Three-card layout explaining PROPOSE, AMEND, BOOST with costs and regeneration rules.
- **Voting section**: Vote weight (1.0 per team split among governors), boosting mechanic, ties fail.
- **Proposal Tiers section**: T1-T5+ with thresholds (50%/50%/60%/60%/67%).
- **Private reports**: Added to "Reflect" role card and FAQ — DM you get with your own governance patterns.
- **Between seasons FAQ**: New FAQ entry explaining what carries over (teams, hoopers, enrollments, rules) and what resets (tokens).
- **Discord commands**: Added 7 missing commands — `/bio`, `/trade-hooper`, `/standings`, `/schedule`, `/reports`, `/profile`, `/proposals`.
- **Wild Card flow**: Added "You confirm" step to the proposal flow diagram.
- **Join CTA**: Shows available team names at bottom.

### CSS for new play page components
- `.play-join-steps` / `.join-step` — numbered step cards for join flow
- `.play-season-flow` / `.season-flow-step` — horizontal flow diagram with arrows
- `.play-tokens-grid` / `.play-token-card` — three-column token economy cards
- `.play-vote-rules` / `.play-vote-rule` — bordered rule explanation cards
- `.play-tiers` / `.play-tier-row` — tier listing with colored tier numbers
- Responsive breakpoints for mobile

### `/join` team parameter made required
- Changed `team: str = ""` to `team: str` in Discord bot command definition.
- Updated `@app_commands.describe` to remove "leave blank to see all teams" hint.
- Updated CLAUDE.md Discord Commands table.

**Files modified (6):** `src/pinwheel/api/pages.py`, `templates/pages/home.html`, `templates/pages/play.html`, `static/css/pinwheel.css`, `src/pinwheel/discord/bot.py`, `CLAUDE.md`

**857 tests, zero lint errors.**

**What could have gone better:** The `_get_active_season_id()` bug affected every page on the site for the entire life of the project — it was a Day 1 "hackathon shortcut" that never got replaced. Should have been caught when `get_active_season()` was implemented in Session 50.

---

## Session 57 — Admin Visibility + Enrollment Fix + Test Alignment

**What was asked:** Multiple requests across a long session: (1) Investigate why `/roster` shows reset player assignments. (2) Fix lost team enrollments during season transitions. (3) Build comprehensive ADMIN_GUIDE.md. (4) Build `/admin/season` page with runtime config and pace controls. (5) Fix roster to not be season-scoped. (6) Show proposal details (including pending/failed) on roster. (7) Fix 18 pre-existing test failures from circle method scheduler mismatch.

**What was built:**

### Enrollment fix — `start_new_season()` fallback (`season.py`)
- Root cause: `start_new_season` step 4 only carried enrollments from `completed`/`archived` seasons via `get_latest_completed_season()`. If admin ran `/new-season` before properly completing the previous season, enrollments silently dropped.
- Fix: falls back to most recent season (any status) when no completed season exists. Also pass `previous_season_id` explicitly in the `/new-season` Discord handler.
- Created `scripts/fix_enrollments.py` for production data repair (dry-run by default, idempotent).

### Comprehensive ADMIN_GUIDE.md (`docs/product/ADMIN_GUIDE.md`)
- Expanded from ~59 lines to 200+ lines with: season management, veto flow, pace control, admin web pages, env vars, Fly.io deployment, database backup, Discord bot setup, things to know.

### `/admin/season` page (new route + template)
- Current season card: status, round, teams, governors, games played, start date.
- Runtime Configuration table: pace (colored), auto-advance, presentation mode, governance interval, gov window, evals, environment, quarter duration, game gap.
- Season History table: all seasons with status badges, team/game counts, dates.
- Quick Actions: HTMX-powered pace control buttons (FAST/NORMAL/SLOW/MANUAL) + manual round advance.

### Admin roster redesign
- Changed from season-scoped (`get_players_for_season`) to showing ALL players via `get_all_players()`.
- Added Joined column, Pending proposals column.
- Inline proposal details: status badge (PASSED/PENDING/FAILED/VETOED/CONFIRMED), round number, truncated text.

### Test alignment — circle method (18 tests fixed)
- The scheduler uses the circle method: 4 teams → 3 matchdays (2 games each), not 1 round with 6 games.
- Tests across 6 files incorrectly assumed all C(4,2)=6 games land in round 1.
- Fixed assertions in `test_e2e.py`, `test_commentary.py`, `test_game_loop.py`, `test_scheduler_runner.py`, `test_memorial.py`, `test_season_archive.py`.

**Files modified (14):** `src/pinwheel/core/season.py`, `src/pinwheel/discord/bot.py`, `docs/product/ADMIN_GUIDE.md`, `src/pinwheel/api/admin_season.py` (new), `templates/pages/admin_season.html` (new), `src/pinwheel/api/admin_roster.py`, `templates/pages/admin_roster.html`, `templates/base.html`, `src/pinwheel/main.py`, `tests/test_pages.py`, `tests/test_discord.py`, `tests/test_e2e.py`, `tests/test_commentary.py`, `tests/test_game_loop.py`, `tests/test_scheduler_runner.py`, `tests/test_memorial.py`, `tests/test_season_archive.py`

**New files (3):** `scripts/fix_enrollments.py`, `src/pinwheel/api/admin_season.py`, `templates/pages/admin_season.html`

**880 tests, zero lint errors.**

**What could have gone better:** The 18 test failures from the circle method mismatch were pre-existing — introduced when the scheduler was restructured in Session 53 but the tests weren't updated. Should have been caught in that session's test run (the previous session reported 857 tests passing, suggesting these tests were somehow skipped or the failures were masked).

---

## Session 58 — Resilient Test Assertions + Scheduler Vocabulary Cleanup

**What was asked:** Fix scheduler vocabulary ("matchday" → "round" = complete round-robin cycle), then make all test game-count assertions resilient to team count changes so they won't break when expanding from 4 to 8+ teams.

**What was built:**

### Scheduler vocabulary cleanup (`scheduler.py`)
- "round" now consistently means a complete round-robin cycle: every team plays every other team once. With 4 teams, a round = C(4,2) = 6 games.
- Games within a round are ordered by `matchup_index` and played consecutively — no team plays two games at once.
- `num_rounds` controls how many complete round-robin cycles are generated.
- Module docstring, function docstring, and inline comments all updated to reflect this vocabulary.

### Resilient test assertions (6 test files)
- Replaced 35 hardcoded magic numbers (`== 6`, `== 28`, `== 12`, `== 9`, `== 10`, `== 3`) with values computed from team count.
- Each test file now has a `NUM_TEAMS` constant at the top. Changing it automatically updates all assertions.
- Used `math.comb(n, 2)` or `n * (n - 1) // 2` to compute expected games per round.
- `total_wins == 6` → `total_wins == comb(NUM_TEAMS, 2)`, `wins + losses == 3` → `wins + losses == NUM_TEAMS - 1`, etc.
- Playoff bracket assertions (`== 3` for 2 semis + 1 final) left untouched — playoff format is a separate concern.

**Files modified (7):** `src/pinwheel/core/scheduler.py`, `tests/test_api/test_e2e.py`, `tests/test_commentary.py`, `tests/test_game_loop.py`, `tests/test_memorial.py`, `tests/test_scheduler_runner.py`, `tests/test_season_archive.py`

**880 tests, zero lint errors.**

**What could have gone better:** Went through two iterations on the scheduler fix — first made each circle-method slot its own round (wrong), then corrected to one round = complete round-robin. The user's clear vocabulary definition ("round = set of 6 games where each team plays 3 total") resolved the ambiguity immediately.

---

## Session 59 — Fix Duplicate Discord Channel Creation

**What was asked:** Why are there so many duplicate Discord channels (especially `st-johns-herons` appearing ~8 times)?

**What was built:**

### `on_ready` guard (`bot.py`)
- `on_ready` fires on every Discord reconnect, not just the first connection. Each reconnect re-ran `_setup_server()` and attempted to create all channels again.
- Added `_setup_done: bool` flag — setup only runs once per bot lifecycle.
- Also guarded `_event_listener_task` to only start if not already running.

### Category-free fallback name lookup (`bot.py`)
- `_get_or_create_shared_channel()` and `_setup_team_channel_and_role()` both filtered by `category=category` when looking up channels by name. If a channel existed outside the "PINWHEEL FATES" category (from an older setup or Discord reorder), the lookup missed it and created a duplicate.
- Added fallback: if no match in the expected category, search guild-wide by name before creating.

**Files modified (1):** `src/pinwheel/discord/bot.py`

**880 tests, zero lint errors.**

**What could have gone better:** This bug existed since the bot was first deployed. The `on_ready` reconnect behavior is well-documented in discord.py but easy to overlook. Should have added the guard from Day 1.

---

## Session 60 — Remove PostgreSQL, Go SQLite-Only

**What was asked:** Production runs on Fly.io with a SQLite file on a persistent volume. There is no Postgres instance. Remove the `asyncpg` dependency and all PostgreSQL references — dead code and docs cleanup.

**What was built:**
- Removed `asyncpg>=0.30` from `pyproject.toml`, relocked with `uv lock`
- Simplified `db/engine.py` — removed `if "sqlite"` guards, always sets timeout and PRAGMA listener, updated docstring to "SQLite-only"
- Cleaned `db/repository.py` — removed `.with_for_update()` and PostgreSQL comment (SQLite is single-writer, no locking needed)
- Added "SQLite only, no PostgreSQL support" comment in `config.py`
- Removed "PostgreSQL MVCC" reference from `game_loop.py` isolation comment
- Updated 7 docs: CLAUDE.md (tech stack + env vars), DEMO_MODE.md (environment table + env vars), OPS.md (rewrote architecture diagram, database section, deployment, backup, cost), ADMIN_GUIDE.md (backup, deploy, env vars), COLOPHON.md (stack table), README.md (removed `fly postgres` commands)
- Did NOT touch `docs/dev_log/` or `docs/plans/` (historical records)

**Files modified (12):** `pyproject.toml`, `uv.lock`, `src/pinwheel/db/engine.py`, `src/pinwheel/db/repository.py`, `src/pinwheel/config.py`, `src/pinwheel/core/game_loop.py`, `CLAUDE.md`, `docs/DEMO_MODE.md`, `docs/OPS.md`, `docs/product/ADMIN_GUIDE.md`, `docs/product/COLOPHON.md`, `README.md`

**882 tests, zero lint errors.**

**What could have gone better:** Nothing — straightforward cleanup. The `asyncpg` dependency and PostgreSQL docs were pure dead weight since production moved to SQLite on a Fly volume.

---

## Session 61 — P0 Playoff Fixes: Deferred Events + Best-of-N Series

**What was asked:** Fix two P0 bugs: (1) Playoff games were never replayed for humans — season events (championship started, etc.) were published immediately after simulation, spoiling results in Discord before the replay presentation finished. (2) Playoffs were best-of-1 in Season THREE — the semis should be best-of-3 and finals best-of-5, but no series logic existed.

**What was built:**

### P0 #1: Deferred season events during replay (`game_loop.py`, `scheduler_runner.py`)
- When `suppress_spoiler_events=True`, season events (`season.regular_season_complete`, `season.semifinals_complete`, `season.playoffs_complete`, `season.championship_started`) are collected in a `deferred_season_events` list instead of being published immediately.
- `RoundResult` gained a `deferred_season_events` field to carry them back to the scheduler.
- `_present_and_clear()` in `scheduler_runner.py` publishes deferred events AFTER the replay presentation finishes.
- In instant mode, deferred events publish immediately (no replay delay needed).

### P0 #2: Best-of-N playoff series (`game_loop.py`, `rules.py`)
- Changed defaults: `playoff_semis_best_of: 3` (was 5), `playoff_finals_best_of: 5` (was 7).
- Removed three functions: `_determine_semifinal_winners()`, `_create_finals_entry()`, `_check_all_playoffs_complete()`.
- Added `_series_wins_needed(best_of)` — returns `(best_of + 1) // 2`.
- Added `_get_playoff_series_record(repo, season_id, team_a_id, team_b_id)` — counts wins from playoff game results.
- Added `_schedule_next_series_game(...)` — alternates home court (higher seed home on games 1, 3, 5).
- Added `_advance_playoff_series(...)` — main series logic: identifies semi/finals pairs, checks win counts, schedules next games, creates finals when semis decided, enters championship when finals decided. Handles both 2-team (direct finals) and 4-team (semis then finals) brackets.
- Improved `playoff_context` detection in `_phase_simulate_and_govern()` — correctly distinguishes semi vs finals games by checking the initial playoff round's team pairs.

### Tests (12 new, 1 removed)
- `TestPlayoffSeries` (4): `_series_wins_needed` unit test, `_get_playoff_series_record` integration test, best-of-3 semi multi-game series test, home court alternation test.
- `TestDeferredSeasonEvents` (3): events suppressed when `suppress_spoiler_events=True`, events published when not suppressed, `season.regular_season_complete` deferred.
- Removed `test_check_all_playoffs_complete` (referenced deleted function).
- All existing `TestPlayoffProgression` tests updated with `_BO1_RULESET` for backward compatibility.
- Fixed `test_season_archive.py` and `test_season_lifecycle.py` — added bo1 playoff rules to prevent series expansion in tests not testing series logic.
- Fixed 5 pre-existing lint issues (unused imports in `bot.py`, `test_scheduler_runner.py`).

**Files modified (7):** `src/pinwheel/core/game_loop.py`, `src/pinwheel/core/scheduler_runner.py`, `src/pinwheel/models/rules.py`, `tests/test_game_loop.py`, `tests/test_season_archive.py`, `tests/test_season_lifecycle.py`, `src/pinwheel/discord/bot.py`

**888 tests (12 new, 1 removed), zero lint errors.**

**What could have gone better:** The default ruleset change (`playoff_semis_best_of=3`) caused two tests in other files to fail — `test_season_archive` and `test_season_lifecycle` both ran full season lifecycles that included playoffs, and the new default meant series didn't clinch in 1 game. Adding `playoff_semis_best_of=1, playoff_finals_best_of=1` to those tests' starting rulesets fixed it cleanly.

---

## Session 62 — Admin Nav: Auth-Gated Landing Page

**What was asked:** Admin nav links (Evals, Season) were env-gated (`development`/`staging` only), invisible in production. Replace with a single "Admin" nav item gated on `PINWHEEL_ADMIN_DISCORD_ID` that works in all environments, leading to a landing page hub.

**What was built:**

### `is_admin` in auth context (`pages.py`)
- Added `is_admin` boolean to `_auth_context()`: true when current user's Discord ID matches `PINWHEEL_ADMIN_DISCORD_ID`. Available in every template.

### Auth-gated nav (`base.html`)
- Replaced env-gated `{% if pinwheel_env in ['development', 'staging'] %}` block (two links: Evals, Season) with `{% if is_admin %}` block (one link: Admin).
- Admin link visible in all environments for authenticated admin users only.

### `/admin` landing page (`pages.py` + `templates/pages/admin.html`)
- New route with auth checks: redirects to login if unauthenticated (when OAuth enabled), returns 403 for non-admins.
- Hub page with three cards linking to Season, Governors, and Evals admin pages. Styled with existing card classes and accent colors.

### Tests (7 new)
- `TestAdminLandingPage`: renders for admin, 403 for non-admin, redirect for unauthenticated with OAuth, 403 without OAuth, nav visible for admin, nav hidden for non-admin, nav hidden when logged out.
- Updated 2 existing tests in `test_eval_dashboard.py` to reflect auth-gated (not env-gated) nav behavior.

**Files modified (4):** `src/pinwheel/api/pages.py`, `templates/base.html`, `templates/pages/admin.html` (new), `tests/test_pages.py`, `tests/test_evals/test_eval_dashboard.py`

**902 tests (7 new, 2 updated), zero lint errors.**

**What could have gone better:** The `admin_auth_client` test fixture initially didn't explicitly set `discord_client_id=""` and `discord_client_secret=""`, so the Settings class picked up values from the environment, causing the "403 without OAuth" test to get a 302 redirect instead. Fixed by explicitly clearing those values in the fixture.

---

## Session 63 — Proposal Effects System (Core Implementation)

**What was asked:** Implement the Proposal Effects System plan (`docs/plans/2026-02-14-proposal-effects-system.md`). Key pieces: effect callback registry with hooks at every point in the system, meta JSON columns on relevant DB tables, effect execution engine, updated AI interpreter for structured effects, updated governance pipeline to execute effects when proposals pass.

**What was built:**

### New Pydantic Models (`models/governance.py`)
- `EffectSpec` — structured effect with 5 types: `parameter_change`, `meta_mutation`, `hook_callback`, `narrative`, `composite`. Fields for meta operations, hook points, action code, conditions, lifetime.
- `ProposalInterpretation` — AI interpretation as a list of EffectSpecs. Backward compatible via `to_rule_interpretation()` and `from_rule_interpretation()`.
- New `GovernanceEventType` entries: `effect.registered`, `effect.expired`, `effect.repealed`.
- `EffectType`, `EffectDuration`, `MetaValue` type aliases.

### MetaStore (`core/meta.py`, new)
- In-memory read/write cache keyed by `(entity_type, entity_id, field)`.
- Operations: `get`, `set`, `increment`, `decrement`, `toggle`, `get_all`, `load_entity`, `snapshot`.
- Dirty tracking for efficient DB flushing via `get_dirty_entities()`.

### Hook System Rewrite (`core/hooks.py`)
- Legacy system preserved: `HookPoint` enum, `GameEffect` protocol, `fire_hooks()`.
- New system added: `HookContext` (unified context), `HookResult` (structured mutations), `Effect` protocol, `RegisteredEffect` (concrete implementation).
- `RegisteredEffect` supports: structured condition evaluation (`gte`/`lte`/`eq` on meta fields), action primitives (`modify_score`, `modify_probability`, `modify_stamina`, `write_meta`, `add_narrative`), entity reference templates (`{winner_team_id}`, `{home_team_id}`), round ticking, serialization.
- `fire_effects()` and `apply_hook_results()` functions.

### Effect Registry (`core/effects.py`, new)
- `EffectRegistry`: register/deregister, query by hook point, tick round lifetimes, build human-readable summary.
- `effect_spec_to_registered()`: converts AI-produced EffectSpec to runtime RegisteredEffect.
- `register_effects_for_proposal()`: registers effects and persists via `effect.registered` events.
- `load_effect_registry()`: rebuilds from event store, skips expired/repealed.
- `persist_expired_effects()`: writes `effect.expired` events.

### AI Interpreter v2 (`ai/interpreter.py`)
- `interpret_proposal_v2()`: AI-powered interpretation returning `ProposalInterpretation` with full hook point catalog and action primitive vocabulary.
- `interpret_proposal_v2_mock()`: deterministic mock detecting swagger/morale, bonus/boost, rename/call patterns.
- `INTERPRETER_V2_SYSTEM_PROMPT`: comprehensive prompt with all hook points, action primitives, condition checks.

### Governance Pipeline Extension (`core/governance.py`)
- `tally_governance_with_effects()`: extends `tally_governance` to register effects for passing proposals.
- `_extract_effects_from_proposal()` and `get_proposal_effects_v2()`.

### Meta JSON Columns (`db/models.py`)
- Added `meta: JSON` column to 7 ORM models: `TeamRow`, `HooperRow`, `GameResultRow`, `BoxScoreRow`, `SeasonRow`, `ScheduleRow`, `PlayerRow`.

### Repository Meta Methods (`db/repository.py`)
- `update_team_meta()`, `update_hooper_meta()`, `update_season_meta()`, `update_game_result_meta()`, `update_player_meta()`.
- `flush_meta_store()`: routes MetaStore dirty entries to appropriate update methods.
- `load_team_meta()`, `load_all_team_meta()`.

### Simulation Integration (`core/simulation.py`)
- `simulate_game()` accepts `effect_registry` and `meta_store` parameters.
- `_fire_sim_effects()` helper fires new-style effects at hook points.
- Hook fire points: `sim.game.pre`, `sim.quarter.pre`, `sim.possession.pre`, `sim.quarter.end`, `sim.halftime`, `sim.elam.start`, `sim.game.end`.
- Backward compatible: `None` registry/meta_store produces identical results.

### Game Loop Integration (`core/game_loop.py`)
- `_phase_simulate_and_govern()` loads `EffectRegistry` and creates `MetaStore` at round start.
- Loads team meta from DB into MetaStore.
- Fires `round.pre`, `round.game.pre`, `round.game.post`, `round.post` effects.
- Passes effect_registry and meta_store to `simulate_game()`.
- Flushes MetaStore dirty entries to DB after all games.
- Ticks effect lifetimes and persists expirations.
- Builds effects summary for report context.

### Migration Script (`scripts/migrate_add_meta.py`, new)
- Safe `ALTER TABLE ... ADD COLUMN meta TEXT` for each table.
- Idempotent: skips tables that already have the column.

### Tests (83 total, all new)
- `TestMetaStore` (13): all MetaStore operations.
- `TestEffectSpec` (5): model construction.
- `TestProposalInterpretation` (4): conversion methods.
- `TestRegisteredEffect` (14): should_fire, apply, conditions, action primitives, serialization, tick_round.
- `TestFireEffects` (6): fire_effects and apply_hook_results.
- `TestEffectRegistry` (8): registry operations.
- `TestEffectSpecToRegistered` (5): spec-to-registered conversion.
- `TestEffectPersistence` (4): event store persistence.
- `TestTallyGovernanceWithEffects` (3): effects-aware tallying.
- `TestInterpreterV2Mock` (7): mock v2 interpreter patterns.
- `TestEffectsEndToEnd` (3): full swagger scenario, condition-not-met, expiration lifecycle.
- `TestSimulationEffectsIntegration` (3): simulation with effects, meta modification during sim, backward compatibility.
- `TestDBMetaColumns` (3): team meta column, flush_meta_store, load_all_team_meta.
- `TestMigrationScript` (1): idempotent migration.

**Files modified (8):** `src/pinwheel/models/governance.py`, `src/pinwheel/core/hooks.py`, `src/pinwheel/ai/interpreter.py`, `src/pinwheel/core/governance.py`, `src/pinwheel/core/simulation.py`, `src/pinwheel/core/game_loop.py`, `src/pinwheel/db/models.py`, `src/pinwheel/db/repository.py`

**New files (4):** `src/pinwheel/core/meta.py`, `src/pinwheel/core/effects.py`, `tests/test_effects.py`, `scripts/migrate_add_meta.py`

**1041 tests (139 new), zero lint errors.**

**What could have gone better:** The `PlayerAttributes` model gained three new fields (`ego`, `chaotic_alignment`, `fate`) since the effects plan was written. Test fixtures needed updating. Also, the `Venue` model requires a `capacity` field that was initially missed. Both caught immediately by test failures.

---

## Session 64 — Fix Duplicate Discord Channels (Root Cause)

**What was asked:** Duplicate team channels keep appearing in Discord despite multiple prior fixes (Sessions 59, 61). Investigate every commit from today, trace all channel creation code paths, and find the actual root cause.

**What was built:**

### Root cause: guild cache incomplete on `on_ready` (`bot.py`)
- `on_ready` can fire before Discord has fully populated `guild.text_channels` — Discord sends guild data in chunks. When the local cache is incomplete, `discord.utils.get(guild.text_channels, name=slug)` returns `None` for channels that actually exist, and a duplicate is created.
- This explains why the bug survived every prior fix: the `_setup_done` guard, the category-free fallback, and the distributed lock all worked correctly, but all relied on the same broken assumption — that `guild.text_channels` is complete when `on_ready` fires.
- **Fix:** `_setup_server()` now calls `guild.fetch_channels()` (a real Discord API call) once at the start of setup and passes the complete channel list to `_get_or_create_shared_channel()` and `_setup_team_channel_and_role()`. The API call guarantees a complete picture regardless of cache state. Runs once per bot lifecycle so the latency cost is negligible.

### Atomic distributed lock (`bot.py`)
- The old lock used a read-check-write pattern susceptible to TOCTOU races during rolling deploys (two instances both read "no lock" before either writes).
- **Fix:** Replaced with `INSERT OR IGNORE INTO bot_state` — exactly one writer succeeds, the other gets rowcount=0 and backs off. Stale locks (from crashed instances) are expired via `DELETE` with `json_extract` timestamp check before the atomic insert.
- **Bug found:** The raw SQL `INSERT` was missing the `updated_at` column (NOT NULL with only a Python-level default in the ORM, no SQL-level DEFAULT). `OR IGNORE` silently swallowed the constraint violation, causing the lock to never be acquired. Fixed by including `updated_at` in the INSERT.

### Test updates (`test_discord.py`)
- Updated 5 test classes to mock `guild.fetch_channels()` instead of `guild.text_channels`/`guild.categories`: `TestSetupServer` (2 tests), `TestBotStatePersistence` (2 tests), `TestSetupIdempotencyWithDB` (2 tests).

**Files modified (2):** `src/pinwheel/discord/bot.py`, `tests/test_discord.py`

**970 tests, zero lint errors.**

**What could have gone better:** The atomic lock fix initially failed silently because `INSERT OR IGNORE` swallowed the NOT NULL constraint violation on `updated_at`. No error was logged (the `OR IGNORE` clause suppresses the error at the SQL level, not Python level), and the captured test logs only showed WARNING+. Adding `updated_at` to the raw SQL INSERT fixed it. Lesson: when mixing raw SQL with ORM models, account for all NOT NULL columns — Python-level defaults don't apply to raw SQL.

---

## Session 65 — Wave 1 Complete: NarrativeContext + Rebounds + Cleanup + Effects Doc

**What was asked:** Execute Wave 1 of the remaining work plan — three parallel items (NarrativeContext module, cleanup, rebounds in narration) plus write a dedicated tech doc for the Proposal Effects System.

**What was built:**

### NarrativeContext module (`core/narrative.py`, 47 new tests)
- `NarrativeContext` dataclass computed per round: standings, win/loss streaks, head-to-head records for current matchups, hot players (20+ pts), active rule changes with narrative descriptions, governance state (pending proposals, next tally round), season arc position (early/mid/late/playoff/championship).
- `compute_narrative_context()` async function — reads from DB, never writes. Wrapped in try/except in the game loop so failure never blocks a round.
- `format_narrative_for_prompt()` — formats context as structured text for AI prompt injection.
- Wired into `_phase_simulate_and_govern()` (computed once per round) and `_phase_ai()` (passed to commentary, highlights, reports). All output functions accept `narrative: NarrativeContext | None = None` for backward compatibility.
- Mock paths enriched: commentary mentions win streaks 3+, reports include rule change context and late-season arc notes, governance reports mention pending proposals.

### Rebounds in narration (`core/narrate.py`, 12 new tests)
- Rebounds were already simulated (`attempt_rebound()` in `possession.py`) and tracked in box scores but invisible in play-by-play narration.
- Added `is_offensive_rebound: bool` field to `PossessionLog` and `PossessionResult`.
- 8 narration templates (4 offensive, 4 defensive) in `narrate.py`. Only fire on missed shots — no rebound text on makes, fouls, or turnovers.
- Wired into game detail page (`pages.py`), live SSE presenter (`presenter.py`), and AI commentary box score context (`commentary.py`).

### Cleanup: GovernanceWindow removal (-2 tests)
- Removed `GovernanceWindow` class (13 lines) from `models/governance.py`.
- Removed `"window.opened"` and `"window.closed"` from `GovernanceEventType`.
- Removed `close_governance_window()` function (34 lines) from `core/governance.py`.
- Removed 2 dead-code tests, rewrote 2 tests to use `tally_governance` directly.

### Effects System tech doc (`docs/EFFECTS_SYSTEM.md`)
- Comprehensive technical documentation: design principles, 5-layer architecture breakdown, simulation integration, the "swagger" end-to-end example, hook point reference table, effect lifetime semantics, file reference map.

**Files modified (14):** `src/pinwheel/core/narrative.py` (new), `src/pinwheel/core/narrate.py`, `src/pinwheel/core/possession.py`, `src/pinwheel/core/game_loop.py`, `src/pinwheel/ai/commentary.py`, `src/pinwheel/ai/report.py`, `src/pinwheel/api/pages.py`, `src/pinwheel/core/presenter.py`, `src/pinwheel/models/game.py`, `src/pinwheel/models/governance.py`, `src/pinwheel/core/governance.py`, `tests/test_narrative.py` (new), `tests/test_narrate.py`, `tests/test_simulation.py`, `tests/test_governance.py`, `docs/EFFECTS_SYSTEM.md` (new)

**970 tests (57 new, 2 removed), zero lint errors.**

**What could have gone better:** All four Wave 1 tasks ran as parallel background agents. The Proposal Effects agent (Session 63) committed all changes together including NarrativeContext and rebounds, since those agents finished while it was still running. Test counts differed across agents (cleanup: 900, NarrativeContext: 1023, rebounds: 1034, effects: 1041) because each saw a different snapshot of the working tree. A subsequent pruning pass (Session 64) brought the count to 970. No merge conflicts.

---

## Session 66 — Game Richness Audit (Wave 2)

**What was asked:** Comprehensive Game Richness audit of every player-facing output system against `GAME_MOMENTS.md`. The principle: "A playoff game that reads like a regular-season game is a bug." Audit and fix Discord embeds, HTML templates, mock report/commentary generators, presenter events, and bot event dispatch.

**What was built:**

### Discord embeds — playoff awareness (6 functions updated)
- `build_game_result_embed` — "CHAMPIONSHIP FINALS:" / "SEMIFINAL:" in title, gold color for finals, Stage field.
- `build_standings_embed` — win/loss streak indicators (W5, L3), phase-aware titles ("Playoffs", "Championship").
- `build_schedule_embed` — playoff labels in title.
- `build_commentary_embed` — phase in title and footer.
- `build_round_summary_embed` — phase labels, champion mention when playoffs_complete.
- `build_team_game_result_embed` — "CHAMPIONS!" for finals win, "Eliminated" for semifinal loss.

### Mock report generators — playoff differentiation
- `generate_simulation_report_mock` — playoff phase opener ("THE CHAMPIONSHIP FINALS" / "SEMIFINAL PLAYOFFS"), playoff-specific game descriptions, hot_players mention, season_arc notes.
- `generate_governance_report_mock` — playoff opener ("CHAMPIONSHIP GOVERNANCE" / "PLAYOFF GOVERNANCE"), elimination context.

### HTML templates — phase badges and streak indicators (6 templates)
- `home.html` — phase label in hero pulse, streak indicators in mini-standings.
- `standings.html` — STRK column, phase badge in subtitle.
- `arena.html` — playoff round headers ("CHAMPIONSHIP FINALS", "SEMIFINAL PLAYOFFS").
- `game.html` — phase badge above game header.
- `governance.html` — phase badge ("PLAYOFFS" / "CHAMPIONSHIP") in title, contextual tagline for elimination/championship governance.
- `reports.html` — phase tags on report type labels ("SEMIFINAL PLAYOFFS" / "CHAMPIONSHIP FINALS").

### Pages API — phase context propagation
- 3 new helper functions: `_get_season_phase()`, `_get_game_phase()`, `_compute_streaks_from_games()`.
- `home_page`, `standings_page` — pass `season_phase` and `streaks`.
- `arena_page` — compute `round_phase` per round.
- `game_page` — pass `game_phase`.
- `governance_page` — pass `season_phase`.
- `reports_page` — pass per-report `phase` and `season_phase`.

### Presenter — playoff_context in SSE events
- `game_starting` event includes `playoff_context` from game_summaries.
- `game_finished` event includes `playoff_context` from game_summaries.
- `round_finished` event includes `playoff_context` derived from game_summaries.

### Game loop + Discord bot wiring
- `game_loop.py` — `playoff_context` added to game_summaries dict.
- `bot.py` — `_dispatch_event` extracts `playoff_context` from event data, passes to all embed builders.

**Files modified (11):** `src/pinwheel/discord/embeds.py`, `src/pinwheel/ai/report.py`, `src/pinwheel/api/pages.py`, `src/pinwheel/core/presenter.py`, `src/pinwheel/core/game_loop.py`, `src/pinwheel/discord/bot.py`, `templates/pages/home.html`, `templates/pages/standings.html`, `templates/pages/arena.html`, `templates/pages/game.html`, `templates/pages/governance.html`, `templates/pages/reports.html`, `tests/test_commentary.py`, `tests/test_pages.py`

**1085 tests (115 new), zero lint errors.**

**What could have gone better:** The Showboat demo pipeline (`run_demo.sh`) has a fragile screenshot invocation that failed on argument parsing. Rodney screenshots were not captured automatically. Manual refresh would be needed for demo artifacts.

---

## Session 67 — Doc Updates for Auto-Migrate Schema

**What was asked:** Update CLAUDE.md and EFFECTS_SYSTEM.md to reflect the new `auto_migrate_schema()` system introduced in commit `1d9964c`. The system replaces hand-coded `_add_column_if_missing()` calls with a generic startup introspection that compares ORM models against SQLite `PRAGMA table_info` and adds missing columns automatically.

**What was built:**

### CLAUDE.md updates
- **Tech Stack:** Updated "Schema managed via" note to mention `auto_migrate_schema()` alongside `create_all()`.
- **LIVE DATA section:** Replaced the "Schema changes require migration scripts" bullet with a new bullet documenting auto-migration for additive changes (nullable columns and columns with scalar defaults) while noting destructive changes still need manual scripts.

### EFFECTS_SYSTEM.md updates
- **MetaStore "Database backing" paragraph:** Updated to note that `auto_migrate_schema()` handles the meta columns at startup, making `scripts/migrate_add_meta.py` redundant (kept for reference).
- **File Reference table:** Marked the `scripts/migrate_add_meta.py` row as superseded by `auto_migrate_schema()`.

**Files modified (2):** `CLAUDE.md`, `docs/EFFECTS_SYSTEM.md`

**1089 tests, zero lint errors.**

---

## Session 68 — Fix /join Interaction Timeout (Autocomplete Event Loop Congestion)

**What was asked:** Adriana's `/join` attempt shows Discord's generic "Something went wrong joining the team." instead of our custom error messages. The error message doesn't exist anywhere in the codebase.

**What was built:**

### Root cause identified via Fly.io production logs
- **Chain of failure:** Team autocomplete DB queries (4 in 2 seconds) congested the asyncio event loop. By the time the `/join` command handler ran, Discord's 3-second interaction window had expired. `defer()` raised `NotFound: 404 Unknown interaction`. The `except Exception` handler tried `followup.send()` — which requires a successful defer — creating a double failure. The exception escaped entirely, and Discord showed its own generic error.
- **Why our error messages never appeared:** `followup.send()` only works after `defer()`. When `defer()` fails, `followup.send()` also fails, so none of our error messages in the except block could be delivered.

### Fix 1: Remove DB queries from team autocomplete
- `_autocomplete_teams()` now uses **only** the in-memory cache populated at startup. If the cache isn't populated yet, returns empty results instead of falling back to a DB query. This prevents autocomplete from congesting the event loop during typing.

### Fix 2: Graceful handling of expired interactions
- Wrapped `defer()` in `_handle_join()` with a `try/except (discord.NotFound, discord.HTTPException)` guard. If the interaction already expired, logs a warning and returns cleanly instead of cascading into the followup failure.

**Files modified (1):** `src/pinwheel/discord/bot.py`

**1163 tests, zero lint errors.**

**What could have gone better:** The "Something went wrong" message not existing in our codebase was the key diagnostic clue — it meant Discord itself was generating it because our handler crashed without responding. We should add a global `CommandTree.on_error` handler to catch any future cases where commands fail without responding.

---

## Session 69 — Upgrade /propose to V2 Effects Interpreter

**What was asked:** Adriana proposed "the ball is lava and holding it costs extra stamina" — a creative, clearly interpretable rule that maps to `stamina_drain_rate`. The V1 interpreter returned "Could not map to a game parameter" with 30% confidence. Upgrade `/propose` to use the V2 effects interpreter (which already exists but wasn't wired into Discord) and make the system prompt more creative about embracing metaphorical proposals.

**What was built:**

### V2 system prompt — creative proposal guidance (`interpreter.py`)
- Added "Embrace Creative Proposals" section to `INTERPRETER_V2_SYSTEM_PROMPT` with 8 examples of metaphorical proposals mapped to mechanical effects ("the ball is lava" → stamina_drain_rate, "let them cook" → foul_rate_modifier decrease, etc.).
- Guidance to set confidence >= 0.7 for proposals with clear gameplay intent, reserving low confidence for genuinely ambiguous proposals.

### `/propose` switched to V2 (`bot.py`)
- Replaced `interpret_proposal` / `interpret_proposal_mock` with `interpret_proposal_v2` / `interpret_proposal_v2_mock`.
- V2 returns `ProposalInterpretation`; converted to `RuleInterpretation` via `.to_rule_interpretation()` for tier detection and backward compat.
- Both `interpretation` and `interpretation_v2` passed to view and embed.
- Injection rejection creates both V1 and V2 interpretation objects.

### Revise modal switched to V2 (`views.py`)
- Same change in `ReviseProposalModal.on_submit`: uses V2 interpreter, converts for compat, updates both `interpretation` and `interpretation_v2` on parent view.

### ProposalConfirmView carries V2 (`views.py`)
- Added `interpretation_v2: ProposalInterpretation | None = None` parameter to `__init__`.

### Rich V2 embed display (`embeds.py`)
- `build_interpretation_embed` accepts optional `interpretation_v2`. When present with effects, renders each effect type with appropriate labels:
  - Parameter Change: `` `stamina_drain_rate`: 1.0 -> 1.5 ``
  - Hook: hook point + description
  - Meta: operation + target + description
  - Narrative: description text
- Falls back to legacy single-parameter display when no V2 interpretation.

### V2 mock patterns for lava/fire (`interpreter.py`)
- Added keyword detection for "lava", "hot potato", "fire", "burn", "scorching" → `stamina_drain_rate` increase + narrative effect about the ball being dangerously hot. Confidence 0.85.

**Files modified (4):** `src/pinwheel/ai/interpreter.py`, `src/pinwheel/discord/bot.py`, `src/pinwheel/discord/views.py`, `src/pinwheel/discord/embeds.py`

**1163 tests, zero lint errors.**

**What could have gone better:** Nothing significant — the V2 interpreter, `ProposalInterpretation` model, and `.to_rule_interpretation()` conversion were all already in place from Session 63. This was primarily a wiring change.

---

## Session 70 — Wave 3 Complete: E2E Verification + Workbench

**What was asked:** Execute Wave 3 — end-to-end workflow verification and workbench + safety layer. Both ran as parallel background agents.

**What was built:**

### E2E workflow verification (48 new tests)
- New `tests/test_api/test_e2e_workflow.py` with 3 test classes:
  - `TestFullWorkflow` (28 tests) — complete player lifecycle: season creation → team setup → governor enrollment → proposal submission → confirmation → voting → game simulation → governance tally with rule enactment → effects firing (meta_mutation) → narrative context computation → compound proposals → new RuleSet params → standings → reports → season progression → playoff bracket → playoff games → championship → effect expiration → event bus integration.
  - `TestWebPageRoutes` (11 tests) — all key pages return 200: home, arena, standings, governance, reports, rules, play, terms, privacy, health, API standings.
  - `TestIntegrationBugs` (9 tests) — expanded RuleSet params, JSON round-trips, narrative format, governance double-tally prevention, empty schedule, multisession, hook_callback firing.
- **No production bugs found.** All Wave 1-2 integrations work correctly end-to-end.
- Fixed missing `presentation_state` in test fixtures for `test_admin_workbench.py` and `test_admin_review.py`.

### Workbench + safety layer (26 new tests)
- **`/admin/review`** — Proposal review queue. Queries governance events for flagged proposals, shows status (pending/resolved/passed/failed), tier badges, confidence bars, injection classification alerts. Auth-gated.
- **`/admin/workbench`** — Safety workbench. Visual defense stack pipeline (6 layers: sanitization → classifier → interpreter → validation → human-in-the-loop → admin review). Interactive HTMX-powered injection classifier test bench. 6 sample proposals (3 legit, 3 injection). Classifier config display.
- Both linked from `/admin` landing page. No new dependencies, no schema changes.

**Files created (6):** `tests/test_api/test_e2e_workflow.py`, `src/pinwheel/api/admin_review.py`, `src/pinwheel/api/admin_workbench.py`, `templates/pages/admin_review.html`, `templates/pages/admin_workbench.html`, `tests/test_admin_review.py`, `tests/test_admin_workbench.py`

**Files modified (3):** `src/pinwheel/main.py`, `templates/pages/admin.html`, `tests/test_admin_workbench.py`, `tests/test_admin_review.py`

**1163 tests (74 new), zero lint errors.**

**What could have gone better:** The workbench agent's test fixtures were missing `presentation_state`, caught by the e2e agent when it ran the full suite. Since both agents ran in parallel, the e2e agent fixed it in its own pass.
