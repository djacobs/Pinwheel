# Pinwheel Dev Log — 2026-02-20

Previous logs: [DEV_LOG_2026-02-10.md](DEV_LOG_2026-02-10.md) (Sessions 1-5), [DEV_LOG_2026-02-11.md](DEV_LOG_2026-02-11.md) (Sessions 6-16), [DEV_LOG_2026-02-12.md](DEV_LOG_2026-02-12.md) (Sessions 17-33), [DEV_LOG_2026-02-13.md](DEV_LOG_2026-02-13.md) (Sessions 34-47), [DEV_LOG_2026-02-14.md](DEV_LOG_2026-02-14.md) (Sessions 48-70), [DEV_LOG_2026-02-15.md](DEV_LOG_2026-02-15.md) (Sessions 71-89), [DEV_LOG_2026-02-16.md](DEV_LOG_2026-02-16.md) (Sessions 90-106), [DEV_LOG_2026-02-17.md](DEV_LOG_2026-02-17.md) (Sessions 107-111), [DEV_LOG_2026-02-18.md](DEV_LOG_2026-02-18.md) (Session 112), [DEV_LOG_2026-02-19.md](DEV_LOG_2026-02-19.md) (Sessions 113-115)

## Where We Are

- **2079 tests**, zero lint errors (Session 124)
- **Days 1-7 complete:** simulation engine, governance + AI interpretation, reports + game loop, web dashboard + Discord bot + OAuth + evals framework, APScheduler, presenter pacing, AI commentary, UX overhaul, security hardening, production fixes, player pages overhaul, simulation tuning, home page redesign, live arena, team colors, live zone polish
- **Day 8:** Discord notification timing, substitution fix, narration clarity, Elam display polish, SSE dedup, deploy-during-live resilience
- **Day 9:** The Floor rename, voting UX, admin veto, profiles, trades, seasons, doc updates, mirror->report rename
- **Day 10:** Production bugfixes — presentation mode, player enrollment, Discord invite URL
- **Day 11:** Discord defer/timeout fixes, get_active_season migration, playoff progression pipeline
- **Day 12:** P0 fixes — /join, score spoilers, strategy system, trade verification, substitution verification
- **Day 13:** Self-heal missing player enrollments, decouple governance from game simulation
- **Day 14:** Admin visibility, season lifecycle phases, effects system, NarrativeContext, game richness audit, SQLite write lock fix, playoff series, V2 interpreter, e2e verification, workbench
- **Day 15:** Overnight wave execution — amendments, repeal, milestones, drama pacing, effects wave 2, documentation, Discord guard, V2 tier detection, tick-based scheduling, SSE dedup, team links, playoff series banners
- **Day 16:** AI intelligence layer, Amplify Human Judgment (9 features), P0/P1 security hardening, doc reconciliation, Messages API phases 1-2, performance optimization, video demo pipeline
- **Day 17:** Repo cleanup, excluded demo PNGs from git, showboat image fix, deployed
- **Day 18:** Report prompt simplification, regen-report command, production report fix, report ordering fix
- **Day 19:** Resilient proposal pipeline — deferred interpreter, mock fallback detection, custom_mechanic activation
- **Day 20:** Smarter reporter — bedrock facts, playoff series context, prior-season memory, model switch to claude-sonnet-4-6
- **Day 21:** Playoff series bracket, governance adjourned fix, arena light-safe colors, offseason bracket fix
- **Day 22:** Proper bracket layout with CSS grid connecting lines
- **Day 23:** Effects pipeline fix, deferred interpreter fix, proposal resubmission, admin guide
- **Day 24:** Generic condition evaluator, conditional_sequence gate fix, World 2 architecture design
- **Day 25 Session 116:** Production audit — 0 effect.registered events ever, fixed interpreter busy UX, raw param names, duplicate proposals
- **Day 25 Sessions 117-118:** Full audit + P1/P2 fixes; playoff schedule gap fixed; deferred_interpreter crash fixed
- **Day 25 Session 119:** Arena UX polish — subtitle shows round + phase, leader separator, series game numbers
- **Day 25 Session 120:** Playoff chaos — two simultaneous finals, Burnside ghosted; fixed series record logic, cleaned production data, fixed series game number to use full history
- **Day 25 Session 121:** Series context headlines now show pre-game state; fixed round 12 wrong team IDs in production DB
- **Live at:** https://pinwheel.fly.dev
- **Latest commit:** `563fb26` — fix: career performance table shows all seasons by linking hoopers by name

## Today's Agenda

- [x] Audit: do any passed proposals have game impact? (Answer: no — 0 effect.registered events in production)
- [x] Remove "Interpreter busy" / deferred retry path from bot.py
- [x] Fix governance page: show impact_analysis not raw parameter names (stamina_drain_rate)
- [x] Fix rules_changed section: human-readable parameter labels
- [x] Cancel 10 duplicate proposals, keep 5 batch-3 (real AI interpretation)
- [ ] Record demo video (3-minute hackathon submission)
- [x] Fix UUIDs in arena play-by-play and Discord notifications
- [x] Hooper page: season name, bold game/league highs, past seasons, bio
- [x] Hooper page: career performance table (all seasons, current highlighted)

---

## Session 116 — Production Audit + Interpreter UX + Governance Cleanup

**What was asked:** Do all currently passed rule changes have game impact? User also flagged two UX violations: never show "Interpreter busy", never show raw parameter names like `stamina_drain_rate`.

**What was built:**

Production audit:
- Queried all governance events in production DB — found 0 `effect.registered` events ever
- All 5 passed proposals (Feb 15-17) used V1 interpretation format, predating the effects_v2 pipeline fix (Session 113)
- The 5 resubmitted proposals from Sessions 113-114 are in the current season (62f4295a) with real V2 interpretation — waiting for votes
- Found 15 proposals in current season (3 duplicate runs of the same 5 texts)

Interpreter busy — removed:
- `bot.py`: deleted the entire `is_mock_fallback` deferred retry branch
- No more "The Interpreter is overwhelmed right now. Your proposal has been queued" — mock fallback proceeds immediately to the Confirm/Revise UI
- The deferred interpreter background process still runs but can never be triggered from Discord

Raw parameter names — fixed:
- `governance.html`: removed `Change <code>{{ p.interpretation.parameter }}</code> from X to Y` block entirely
- Now shows only `impact_analysis` (human-readable), with confidence hidden when < 50%
- Rules Enacted section: `rc.parameter` → `rc.parameter_label` (e.g. `stamina_drain_rate` → "Stamina Drain Rate")
- `pages.py` governance route: builds `parameter_label` from `RULE_TIERS` lookup with title-case fallback

Duplicate proposals — cancelled:
- Wrote `scripts/cancel_duplicate_proposals.py` — identifies proposals not in KEEP_IDS set, appends `proposal.cancelled` events
- `pages.py` governance route: filters `proposal.cancelled` events from the displayed list
- Ran on production: cancelled 10 (batches 1+2), kept 5 (batch 3, 85-92% confidence)
- Governance page now shows exactly 5 clean proposals open for voting

**Files modified (4):** `templates/pages/governance.html`, `src/pinwheel/api/pages.py`, `src/pinwheel/discord/bot.py`, `scripts/cancel_duplicate_proposals.py`

**2058 tests, zero lint errors.**

**What could have gone better:** The 3-run duplication was caused by running the resubmit script before deploying the Session 114 JSON parsing fix, then re-running after. The resubmit script should have checked for existing open proposals with the same text before submitting.

---

## Session 117 — Full Codebase Audit + Systematic P1/P2 Fixes

**What was asked:** Run a full codebase audit using all available review agents, then spin off parallel background agents to fix every P1 and P2 issue found.

**What was built:**

Audit:
- Ran 8 review agents simultaneously (kieran-python-reviewer, security-sentinel, performance-oracle, architecture-strategist, pattern-recognition-specialist, data-integrity-guardian, git-history-analyzer, code-simplicity-reviewer)
- Synthesized 30 findings: 4 P1 critical, 15 P2 important, 11 P3 nice-to-have
- Written to `docs/CODE_REVIEW_2026-02-20.md`

P1 fixes (all 4 resolved):
- FK enforcement: added `PRAGMA foreign_keys=ON` to `_set_sqlite_pragmas` — exposed 3 latent test bugs in `test_discord.py` (missing season_id in vote payloads, reversed create_team args), all fixed
- Auth: added `require_api_admin` dependency to `POST /api/seasons`, `POST /api/pace`, `POST /api/pace/advance` — dev mode bypasses auth, prod enforces admin session
- XSS: added `nh3.clean()` after markdown conversion in `_prose_to_html` — prevents script injection via AI-generated content
- Parallel AI: refactored `_phase_ai` in game_loop.py to use `asyncio.gather()` for all independent calls — expected 40–200s wall-clock → 2–10s per round

P2 fixes (11 of 15 resolved; 4 deferred as too large):
- Session factory: module-level `_session_factories` dict cache in `db/engine.py`, `api/deps.py`
- DB constraints: `UniqueConstraint("season_id", "sequence_number")` on governance_events + `index=True` on `BoxScoreRow.team_id` and `PlayerRow.team_id`; applied unique index to both local and production DB
- Dead code: deleted `ai/mirror.py`, `models/mirror.py`, `api/mirrors.py` (639 lines); confirmed all 7 flagged repository methods have callers
- N+1 queries: standings API (50+N → 2 queries), `get_team_game_results` (N → 2 queries)
- Admin season bug: fixed `select(SeasonRow).limit(1)` → `repo.get_active_season()` in 4 admin files
- Return types: added `-> HTMLResponse` to 20+ handlers across 7 files
- SSE security: 19-entry `ALLOWED_EVENT_TYPES` frozenset + 100-connection semaphore; 19 new tests in `test_sse_security.py`
- `current_attributes` cache: two-key cache (stamina + base attrs tuple) — eliminates 24K–48K Pydantic allocations per round
- `model_dump()` → `getattr()` in `check_gate()` — 2,400 serializations/game eliminated
- Discord admin auth: unified `/new-season` and `/activate-mechanic` to use `PINWHEEL_ADMIN_DISCORD_ID` (fail-closed when unset)
- Type annotations: `object` → real types in `ai/usage.py`, `core/deferred_interpreter.py`, `core/presenter.py`, `core/game_loop.py` (`_row_to_team`); removed all associated `# type: ignore` comments

Deferred items (also resolved this session):
- P2.17 rename: removed 23 `AgentRow`/`create_agent` aliases across 6 files; created `models/constants.py` as home for `ATTRIBUTE_ORDER` (was in `api/charts.py` — layer violation)
- P2.8 bare excepts: narrowed all 106 `except Exception:` blocks across 24 files to specific exception families; kept last-resort handlers with explanatory comments
- P2.6 layer violations: inlined `get_token_balance` from `core/tokens` into `repository.py`; `ATTRIBUTE_ORDER` moved to `models/constants.py`
- P2.5 god objects: wrote architectural split plan to `docs/plans/god-object-split-plan.md` (repository → 8 mixins, pages → 9 routers, bot → 5 handler modules, game_loop → 6 phase modules)

**Files modified (40+):** `db/engine.py`, `db/models.py`, `db/repository.py`, `models/constants.py` (new), `models/team.py`, `models/game.py`, `models/tokens.py`, `api/seasons.py`, `api/pace.py`, `api/pages.py`, `api/admin_costs.py`, `api/admin_review.py`, `api/admin_roster.py`, `api/admin_season.py`, `api/admin_workbench.py`, `api/eval_dashboard.py`, `api/events.py`, `api/standings.py`, `api/deps.py`, `api/charts.py`, `auth/deps.py`, `auth/oauth.py`, `ai/usage.py`, `ai/search.py`, `ai/classifier.py`, `ai/interpreter.py`, `core/game_loop.py`, `core/state.py`, `core/moves.py`, `core/deferred_interpreter.py`, `core/presenter.py`, `core/hooks.py`, `core/governance.py`, `core/narrative.py`, `core/scheduler_runner.py`, `core/season.py`, `core/effects.py`, `discord/bot.py`, `discord/views.py`, `config.py`, `evals/rule_evaluator.py`; deleted `ai/mirror.py`, `models/mirror.py`, `api/mirrors.py`; new `tests/test_sse_security.py`, `docs/CODE_REVIEW_2026-02-20.md`, `docs/plans/god-object-split-plan.md`

**2077 tests, zero lint errors.**

**What could have gone better:** The return-type annotations added by Agent 4 introduced 10 E501 lint errors (lines over 100 chars) that needed a post-hoc `ruff format` pass. Agents should run `ruff format` on their files before reporting clean lint.

---

## Session 118 — Playoff Schedule Gap + Deferred Interpreter Crash

**What was asked:** Games stopped on the home page. Investigate and fix.

**What was built:**

Root cause analysis:
- Production logs showed `AttributeError: 'GovernanceEventRow' object has no attribute 'timestamp'` in `deferred_interpreter.py` every minute — but that was a separate crash, not the cause of games stopping
- Round 12 had 0 schedule entries: `_advance_playoff_series()` runs in `_phase_persist_and_finalize()` (Session 2), which is wrapped in `try/except Exception` — but a server restart mid-game can throw `asyncio.CancelledError` (subclass of `BaseException`, not `Exception`), so the playoff schedule insert was silently lost while game results from Session 1 were already committed
- Verified series state: Burnside beat Hawthorne 2-0 (rounds 10-11), Rose City and St. Johns tied 1-1 (rounds 10-11) — game 3 needed but never scheduled

Fixes:
- Manually inserted round 12 semifinal schedule entry: Rose City Thorns (home) vs St. Johns Herons via `/tmp/fix_schedule_v2.py` on production
- Fixed `deferred_interpreter.py:expire_stale_pending()`: `ev.timestamp` → `getattr(ev, "created_at", None) or getattr(ev, "timestamp", None)` — handles both `GovernanceEventRow` (ORM, uses `created_at`) and `GovernanceEvent` (Pydantic, uses `timestamp`); the function's type annotation said `GovernanceEvent` but runtime returned `GovernanceEventRow`

**Files modified (1):** `src/pinwheel/core/deferred_interpreter.py`

**2077 tests, zero lint errors.**

**What could have gone better:** The `get_pending_interpretations` function has a wrong return type annotation (`-> list[GovernanceEvent]`) but actually returns `list[GovernanceEventRow]`. The real fix would be to make it consistently return one type; the `getattr` fallback is a symptom-fix. Also, the `_phase_persist_and_finalize` should catch `BaseException` (or specifically `asyncio.CancelledError`) to ensure playoff advancement always runs, or be split so schedule insertion is done in its own commit.

---

## Session 119 — Arena UX Polish (subtitle, leader separator, series game numbers)

**What was asked:** Three UX bugs spotted: (1) Arena subtitle showed "4 rounds · 8 games" (the recent-slice count, not full season info), (2) star performer names ran together "Rosa Vex 28 ptsWren Silvas 35 pts" with no separator, (3) all three Rose City/St. Johns playoff games were labeled "Game 2" instead of Game 1, Game 2, Game 3.

**What was built:**
- Arena subtitle: replaced `rounds|length` count with `Round {{ arena_round }}` + `· Playoffs` / `· Offseason` using `latest_round` and `season.status` passed from the route
- Leader separator: added `<span class="live-leader-sep">·</span>` between home/away leader spans (both server-rendered and SSE JS paths); CSS `.live-leader-sep` with `margin: 0 0.4rem` and dimmed opacity
- Series game numbers: `matchup_index` is the slot in the round (always 1 for the St. Johns/Rose City semifinal), not the game in the series. Added `series_game_number` computed by counting each team-pair's appearances in `rounds` oldest-first; template uses it for playoff games and falls back to `matchup_index + 1` for regular season
- Bonus: venv was corrupted (pytest not in `.venv/bin/`); rebuilt with `uv sync --extra dev`

**Files modified (3):** `src/pinwheel/api/pages.py`, `templates/pages/arena.html`, `static/css/pinwheel.css`

**2077 tests, zero lint errors.**

**What could have gone better:** The `series_game_number` fix only applies to games in the 4-round display window. If a series spans rounds outside that window, the count would be off. A more robust fix would store `series_game_number` directly on `GameResultRow` when the game is recorded.

---

## Session 120 — Playoff Chaos: Two Simultaneous Finals, Burnside Ghosted

**What was asked:** Two bugs reported via screenshots: (1) Round 12 game labeled "Game 1" when it was Game 3, (2) Two identical Rose City vs St. Johns games scheduled simultaneously at 3pm. Then a third screenshot revealed the real disaster: two "Championship Finals" games running live simultaneously between Rose City and St. Johns — Burnside Breakers (who swept Hawthorne 2-0) had never been scheduled for the finals at all.

**What was built:**

Root cause analysis:
- `_get_playoff_series_record` filtered games by `playoff_rounds = {ALL rounds with any playoff schedule entry}`. When round 12's manually-inserted schedule entry was committed after `_advance_playoff_series` started running, round 12's game result was excluded from the count — making Rose City/St. Johns look 1-1 instead of 2-1
- Result: a 3rd semifinal was scheduled (round 13 idx=1), AND the finals were created with St. Johns (wrong!) instead of Burnside — so two simultaneous "Championship Finals" games between Rose City and St. Johns appeared, while Burnside was entirely absent

Code fix — `game_loop.py`:
- `_get_playoff_series_record`: changed `playoff_rounds` from all-playoff-rounds to pair-specific scheduled rounds — `scheduled_rounds = {s.round_number for s in playoff_schedule if frozenset({s.home_team_id, s.away_team_id}) == pair}`
- `_schedule_next_series_game`: added `IntegrityError` guard so duplicate-schedule attempts from retries log a warning instead of crashing

Production data cleanup (via `/tmp/fix_playoff_chaos3.py`):
- Deleted 2 wrong game_results for rounds 13+ (Rose City vs St. Johns phantom finals games), plus their box_scores
- Deleted 3 wrong schedule entries for rounds 13-14
- Inserted correct finals: Burnside Breakers (home) vs Rose City Thorns (away), round 13, idx=0, phase=finals

Series game number — full history fix (`pages.py`):
- The Session 119 `series_game_number` fix counted appearances in the 4-round display window. Round 12 (Game 3) was the only game in the window, so it showed as "Game 1"
- Fixed: fetch all season game_results + full playoff schedule via `repo.get_all_games` + `repo.get_full_schedule`; build `pair→scheduled_rounds` map; count games per pair up to each game's `round_number`

**Files modified (2):** `src/pinwheel/core/game_loop.py`, `src/pinwheel/api/pages.py`

**2077 tests, zero lint errors.**

**What could have gone better:** The root trigger was `asyncio.CancelledError` escaping `_phase_persist_and_finalize`'s `except Exception` guard during a server restart — silently dropping the round 12 schedule entry while game results were already committed. The real fix is to catch `BaseException` (or split schedule insertion into its own commit so it can't be lost). The `_get_playoff_series_record` bug was a latent logic error that turned a missing schedule entry into cascading chaos.

---

## Session 121 — Series Context Pre-Game State + Round 12 Team ID Fix

**What was asked:** Games 1 and 2 showed series headlines generated from the wrong context — both said "decisive Game 3 showdown" when they should reflect the stakes at the time each game was played. Also: round 12 still showed wrong game numbers.

**What was built:**

Root cause of wrong game numbers and wrong headlines (same bug):
- The round 12 schedule entry (manually inserted by `fix_schedule_v2.py`) used team IDs from a different DB context — `8c604a32`/`60c99636` instead of the season's actual Rose City (`44e3232c`) and St. Johns (`bc1607a1`) IDs
- The game_result and box_scores for round 12 also got these wrong IDs when the game loop ran against the bad schedule entry
- So `scheduled_rounds` for the real Rose City/St. Johns pair was `{10, 11}` (not `{10, 11, 12}`), breaking series_game_number and series record computation for round 12

Production data fix (via `/tmp/fix_round12_team_ids.py`):
- Updated schedule entry `8e4def28`: home/away team IDs → 44e3232c/bc1607a1
- Updated game_result `da1f154d`: home/away/winner team IDs fixed (Rose City won 59-49)
- Updated 8 box_score rows (4 Rose City, 4 St. Johns)

Series context pre-game state fix:
- `_get_playoff_series_record` in `game_loop.py`: added optional `before_round` parameter — when set, excludes games with `round_number >= before_round`
- `_compute_series_context_for_game` in `pages.py`: added `round_number` parameter, passes it as `before_round`
- Arena route: passes `g["round_number"]` to `_compute_series_context_for_game` for each game
- Result: Game 1 shows 0-0 opening context, Game 2 shows 1-0 (St. Johns leads) context, Game 3 shows 1-1 decisive game context

**Files modified (2):** `src/pinwheel/core/game_loop.py`, `src/pinwheel/api/pages.py`

**2077 tests, zero lint errors.**

**What could have gone better:** The root cause was `fix_schedule_v2.py` querying teams by `WHERE season_id=?` instead of `WHERE id IN (SELECT DISTINCT home_team_id FROM schedule WHERE season_id=?)`. If the teams table has rows from multiple seasons (or teams lack a season_id column), the former query can silently return wrong teams. The lesson: always derive team IDs from existing schedule entries for the target season.

---

## Session 122 — UUID Fixes + Hooper Page Enhancements

**What was asked:** Fix raw UUIDs appearing in (1) the arena live play-by-play where defenders/rebounders not in the box score showed as IDs, (2) Discord game-over notifications showing team IDs when the server restarts mid-game. Also enhance the hooper detail page: show which season the game log belongs to, bold game-high and league-high stat lines, collapse past seasons to single aggregate rows, and add a bio field.

**What was built:**

UUID fixes:
- `pages.py` game detail handler: `hooper_names` cache now loads from both teams' full rosters (all hoopers in `team.hoopers`) before falling back to individual box score lookups — defenders and rebounders who don't appear in box scores are now resolved
- `scheduler_runner.py` resume path: after building the name cache from `get_teams_for_season()`, a defensive fallback loop fetches any team IDs in game results that weren't returned by the season query (e.g. teams from a prior season that appear in current-season game records due to manual data repairs) and logs a warning

Hooper page enhancements:
- `db/repository.py`: added `get_league_season_highs(season_id)` — `SELECT MAX(points), MAX(assists), MAX(steals) FROM box_scores JOIN game_results` for a season; returns `{"points": N, "assists": N, "steals": N}`
- `pages.py` hooper handler: groups box scores by season, builds `game_log` from current season only, computes `personal_bests` (per-hooper max this season), fetches `league_bests` via new repo method, annotates each game log entry with `pts_is_personal_best`, `pts_is_league_best`, `ast_is_personal_best`, `ast_is_league_best`, `stl_is_personal_best`, `stl_is_league_best` flags; builds `past_seasons` list with aggregate stats per past season using `compute_season_averages`
- `templates/pages/hooper.html`: Game Log card header now shows current season name + legend ("★ league high, **bold** personal best"); points/assists/steals cells render with gold ★ for league bests or `<strong>` for personal bests; new Past Seasons card above game log shows one aggregate row per past season (GP, PPG, FG%, 3P%, APG, SPG, TOPG)
- Bio section: already present in template and handler from a prior session — confirmed working, no changes needed

**Files modified (5):** `src/pinwheel/db/repository.py`, `src/pinwheel/api/pages.py`, `templates/pages/hooper.html`, `src/pinwheel/core/scheduler_runner.py`, `tests/test_db.py`

**2079 tests, zero lint errors.**

**What could have gone better:** The `hooper_names` cache bug existed since the game detail page was built — loading only from box score participants instead of full rosters. Integration tests for the game detail page should have covered the play-by-play rendering with a complete cast of players (not just scorers), which would have caught this. The live SSE UUID issue root cause was not definitively confirmed — the resume-path fix addresses the most likely production trigger, but the exact live path failure mode wasn't isolated.

---

## Session 123 — Hooper Career Performance Table

**What was asked:** Add a career performance view to the hooper page — a table with one row per season, same columns as the game log, plus games played, showing aggregate stats per season.

**What was built:**
- `pages.py`: replaced separate `past_seasons` build with a unified `career_seasons` list — current season first (is_current=True), then any past seasons; extracted `_bs_to_dict` helper to avoid repeating the 10-field dict literal; passes `career_seasons` to template
- `hooper.html`: replaced "Past Seasons" card with "Career Performance" card using `career_seasons`; added FT% column (was missing from the old Past Seasons table); current season row gets a subtle accent tint and "(current)" label
- `pinwheel.css`: added `.career-current-season td` rule with faint accent background via `color-mix()`

**Files modified (3):** `src/pinwheel/api/pages.py`, `templates/pages/hooper.html`, `static/css/pinwheel.css`

**2079 tests, zero lint errors.**

**What could have gone better:** The table always shows if there's any career data, but the "Season Averages" stats grid in the sidebar now overlaps somewhat with the Career Performance table's current-season row. Could consider collapsing the sidebar stats grid once the career table is present.

---

## Session 124 — Career Performance Cross-Season Fix

**What was asked:** Career Performance table showed only the current season because `carry_over_teams` creates a new hooper ID each season — past seasons' box scores lived under a different ID and were invisible.

**What was built:**
- `repository.py`: added `get_hoopers_by_name(name)` — queries all `HooperRow` records with the exact same name across all seasons; name is the only stable cross-season identifier since `carry_over_teams` keeps names constant
- `pages.py`: replaced single `get_box_scores_for_hooper(hooper_id)` call with a loop over all same-name hoopers from `get_hoopers_by_name()`; aggregates their box scores into `games_by_season` so past seasons now appear in the Career Performance table

**Files modified (2):** `src/pinwheel/db/repository.py`, `src/pinwheel/api/pages.py`

**2079 tests, zero lint errors.**

**What could have gone better:** Name-based linking is fragile if two different players share a name. A more robust solution would store an explicit `prior_hooper_id` FK on `HooperRow` during `carry_over_teams`, creating a linked list that survives name changes. For now, names are unique within the simulation so this is safe.
