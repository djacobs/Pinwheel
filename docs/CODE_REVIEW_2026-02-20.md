# Code Review — Full Codebase Audit

**Date:** 2026-02-20
**Target:** Pinwheel Fates (main branch, full codebase)
**Codebase:** 94 source files, 69 test files, ~40K lines Python

## Review Agents Used

| Agent | Focus |
|-------|-------|
| kieran-python-reviewer | Type safety, Pythonic patterns, async correctness |
| security-sentinel | OWASP, auth/authz, AI sandboxing, XSS, injection |
| performance-oracle | Hot paths, DB queries, async bottlenecks, scalability |
| architecture-strategist | Layer violations, separation of concerns, dependency direction |
| pattern-recognition-specialist | Consistency, duplication, anti-patterns, naming |
| data-integrity-guardian | FK enforcement, transactions, constraints, migrations |
| git-history-analyzer | Churn hotspots, fix patterns, code growth |
| code-simplicity-reviewer | Dead code, YAGNI, over-abstraction |

---

## Findings Summary

- **Total Findings:** 30
- **P1 CRITICAL:** 4
- **P2 IMPORTANT:** 15
- **P3 NICE-TO-HAVE:** 11

---

## P1 — CRITICAL

### 1. Foreign key enforcement disabled

`PRAGMA foreign_keys=ON` is never set in `db/engine.py`. All `ForeignKey()` declarations are purely decorative. Orphaned records can accumulate silently.

**Files:** `src/pinwheel/db/engine.py` (lines 40-46)
**Source:** Data Integrity Guardian

### 2. Unauthenticated state-modifying endpoints

`POST /api/seasons` (creates new seasons) and `POST /api/pace` + `/api/pace/advance` (changes game pacing, triggers round advancement) have zero auth. Any anonymous HTTP request can modify game state.

**Files:** `src/pinwheel/api/seasons.py` (line 22), `src/pinwheel/api/pace.py` (lines 60-120)
**Source:** Security Sentinel

### 3. XSS via `| prose | safe` template filter

The `_prose_to_html` filter in `pages.py` converts text to HTML via the `markdown` library, then renders with `| safe` disabling Jinja2 autoescaping. The `markdown` library passes through raw HTML tags. If AI-generated report content ever contains `<script>` tags (via prompt injection of the report AI), it would execute in every viewer's browser.

**Fix:** Add `nh3.clean()` after markdown conversion.

**Files:** `src/pinwheel/api/pages.py` (lines 91-103), `templates/pages/reports.html` (line 28), `templates/pages/home.html` (lines 59, 66, 234)
**Source:** Security Sentinel

### 4. Sequential AI calls block round cycle

`_phase_ai` in `game_loop.py` runs 20+ Claude API calls sequentially (commentary per game, private reports per governor, behavioral reports, etc.). Each takes 2-10 seconds. With 8 governors, this is 40-200 seconds of wall-clock time. At 20 governors, 2-10 minutes.

**Fix:** `asyncio.gather()` for independent calls.

**Files:** `src/pinwheel/core/game_loop.py` (lines 1671-1827)
**Source:** Performance Oracle

---

## P2 — IMPORTANT

### 5. God objects

`repository.py` (1,599 lines, 60+ methods), `bot.py` (4,346 lines), `pages.py` (3,357 lines), `game_loop.py` (2,635 lines). These four files represent the highest churn and bug-fix concentration in the codebase.

**Source:** Architecture Strategist, Git History Analyzer, Pattern Recognition

### 6. Layer violations

- Repository imports from API layer (`ATTRIBUTE_ORDER` from `api/charts.py`)
- Repository imports from core (`get_token_balance` from `core/tokens.py`)
- core imports from discord (`embeds`, `views` in `deferred_interpreter.py`)
- Direct `anthropic` API call in `pages.py` bypassing `ai/` layer
- API handlers bypass Repository pattern (direct `repo.session` access in 6+ admin files)

**Source:** Architecture Strategist

### 7. `current_attributes` allocates a new Pydantic model per access

Called 20-40 times per possession. With 100 possessions/game and 2 games/round, that's 2,000-8,000 unnecessary Pydantic instantiations per round. Cache with stamina-based invalidation.

**Files:** `src/pinwheel/core/state.py` (lines 64-78)
**Source:** Performance Oracle

### 8. 106 bare `except Exception:` instances

Primarily in `bot.py` (40), `game_loop.py` (25), `views.py` (15). Every unique failure mode gets the same treatment. Replace with specific exception families for better production debugging.

**Source:** Pattern Recognition, Python Reviewer

### 9. 85+ `type: ignore` annotations

Mostly caused by typing parameters as `object` instead of their real types (`TeamRow`, `AsyncSession`, `AsyncEngine`, `Settings`, `EventBus`). The `_row_to_team()` function alone has 12. Fixing the types eliminates the annotations.

**Files:** `src/pinwheel/core/game_loop.py` (lines 75-104), `src/pinwheel/ai/usage.py` (line 92), `src/pinwheel/core/deferred_interpreter.py` (lines 340-345), `src/pinwheel/core/presenter.py` (line 88)
**Source:** Python Reviewer, Pattern Recognition

### 10. Dead code: ~1,270 lines

- `ai/mirror.py` (506 lines, zero importers)
- `models/mirror.py` (43 lines, only imported by dead mirror.py)
- `api/mirrors.py` (90 lines, never mounted, references nonexistent repo methods)
- 7 unused repository methods (120 lines)
- Legacy `GameEffect` protocol with zero implementations in `hooks.py` + `simulation.py`

**Source:** Code Simplicity Reviewer

### 11. Buggy `_get_active_season_id` duplication

Copy-pasted across 4 admin modules, each using `select(SeasonRow).limit(1)` which returns the FIRST season, not the ACTIVE one. Only `eval_dashboard.py` and `pages.py` correctly use `repo.get_active_season()`.

**Files:** `src/pinwheel/api/admin_costs.py`, `admin_review.py`, `admin_roster.py`
**Source:** Pattern Recognition

### 12. Missing return types on 24+ page handlers

Every handler in `pages.py` plus admin handlers omit return type annotations, violating the project's "type hints on all function signatures" rule.

**Files:** `src/pinwheel/api/pages.py`, `admin_costs.py`, `admin_review.py`, `admin_season.py`, `admin_workbench.py`, `admin_roster.py`, `eval_dashboard.py`
**Source:** Python Reviewer

### 13. N+1 query patterns in multiple locations

- `compute_standings_from_repo` (1 query per team for names)
- `get_team_game_results` (1 query per game for opponent names)
- `_check_earned_moves` (1 query per hooper for stats)
- Standings API (hardcoded loop of 1-50 round queries)

**Files:** `src/pinwheel/core/game_loop.py` (lines 550-575, 107-167), `src/pinwheel/db/repository.py` (lines 1156-1180), `src/pinwheel/api/standings.py` (lines 18-31)
**Source:** Performance Oracle, Pattern Recognition

### 14. Session factory recreated per request

`deps.py` and `engine.py` both create a new `async_sessionmaker` on every call instead of caching it at startup.

**Files:** `src/pinwheel/api/deps.py` (lines 20-31), `src/pinwheel/db/engine.py` (lines 56-66)
**Source:** Performance Oracle, Python Reviewer

### 15. SSE stream unauthenticated with no rate limit

Any anonymous client can open unlimited SSE connections, no `event_type` validation against an allowlist. Resource exhaustion vector.

**Files:** `src/pinwheel/api/events.py` (lines 24-66)
**Source:** Security Sentinel

### 16. No unique constraint on governance sequence numbers

`GovernanceEventRow.sequence_number` relies on flush-dependent `SELECT MAX()` with no uniqueness guarantee. If flush ordering ever changes, duplicate sequences go undetected.

**Files:** `src/pinwheel/db/models.py` (line 196)
**Source:** Data Integrity Guardian

### 17. Agent-to-Hooper rename incomplete

25+ backward-compatible aliases (`AgentRow = HooperRow`, `create_agent = create_hooper`, etc.) scattered across 5+ files. Cognitive overhead with no external consumer.

**Files:** `src/pinwheel/db/models.py`, `models/team.py`, `models/game.py`, `core/state.py`, `db/repository.py`
**Source:** Pattern Recognition, Python Reviewer

### 18. `model_dump()` in simulation hot path

`check_gate()` in `moves.py` serializes a Pydantic model to dict on every move check (~2,400 calls/game). Replace with `getattr()` — one-line fix.

**Files:** `src/pinwheel/core/moves.py` (line 94)
**Source:** Performance Oracle

### 19. Inconsistent admin auth

Discord bot uses `guild_permissions.administrator`, web admin uses `PINWHEEL_ADMIN_DISCORD_ID`. Different permission models for the same actions.

**Files:** `src/pinwheel/discord/bot.py` (line 3851)
**Source:** Security Sentinel

---

## P3 — NICE-TO-HAVE

### 20. Proposal status derivation duplicated 3x

The pattern of reconstructing proposal lifecycle status from governance events appears in `repository.py` (twice), `game_loop.py`, and `narrative.py`.

**Source:** Pattern Recognition, Python Reviewer

### 21. AI usage tracking boilerplate repeated 10-12x

Every AI function repeats the same 15-line pattern for `track_latency` + `extract_usage` + `record_ai_usage`. Candidate for a decorator/wrapper.

**Source:** Pattern Recognition

### 22. Three different standings computation paths

API, pages, and game_loop each compute standings differently. The API endpoint loops rounds 1-50 with N+1 queries.

**Source:** Pattern Recognition

### 23. Injection classifier fails open by design

Documented, but consider fail-closed for production. When the classifier fails, proposals bypass the first defense layer.

**Files:** `src/pinwheel/ai/classifier.py` (lines 163-170)
**Source:** Security Sentinel

### 24. `lifespan` function is 145 lines

Handles database creation, migration, presentation recovery, Discord startup, APScheduler config. Decompose into named helpers.

**Files:** `src/pinwheel/main.py` (lines 35-180)
**Source:** Python Reviewer

### 25. Test engine/repo fixtures duplicated across 8+ test files

The exact same `engine` and `repo` fixtures are copy-pasted in `test_db.py`, `test_governance.py`, `test_game_loop.py`, `test_reports.py`, `test_pages.py`, and more. Centralize in `conftest.py`.

**Source:** Pattern Recognition, Python Reviewer

### 26. `evals/golden.py` and `evals/attribution.py` are test artifacts in source

`golden.py` (320 lines) defines test fixtures, never imported at runtime. `attribution.py` (81 lines) has no consumer. Move or remove.

**Source:** Code Simplicity Reviewer

### 27. Phase map dictionaries duplicated 4x

Status-to-phase mapping dicts in `games.py`, `pages.py` (twice), and `narrative.py`. Adding a new season status requires updating all four manually.

**Source:** Pattern Recognition

### 28. Auth context construction duplicated

`_auth_context()` in `pages.py` and `admin_auth_context()` in `auth/deps.py` compute overlapping fields. Unify.

**Source:** Pattern Recognition

### 29. Missing index on `players.team_id`

No index on `players.team_id` or `box_scores.team_id`. Queries like `get_players_for_team()` will table-scan.

**Files:** `src/pinwheel/db/models.py` (line 236)
**Source:** Data Integrity Guardian

### 30. `dict` return types on repository methods

Many repository methods return `dict` instead of Pydantic models. Every consumer must know the dict's shape by reading the implementation.

**Source:** Python Reviewer

---

## What the Codebase Does Well

The agents unanimously highlighted these strengths:

- **Pure simulation engine** — deterministic, seed-controlled, no side effects, no I/O
- **Event-sourced governance** — append-only event store, state derived from events, history never lost
- **AI sandboxing** — multi-layer defense (sanitization -> classifier -> sandboxed interpreter -> Pydantic validation -> human confirmation -> community vote)
- **Test discipline** — 45K lines of tests vs 40K lines of source (1.12:1 ratio)
- **Near-zero `Any` usage** — only 1 bare `Any` in 94 source files
- **Multi-session game loop** — correctly releases SQLite write lock during slow AI calls
- **Pydantic model design** — validated bounds, clear discriminators, models ARE the spec
- **NarrativeContext as read-only aggregation** — computed once, threaded through all outputs
- **Proper WAL mode and busy timeout** for SQLite concurrency
- **Commit message quality** — conventional commits, descriptive, reads as a development narrative
- **Test-to-source ratio of 1.12:1** — more test code than source code

---

## Scalability Projections

| Metric | Current (4 teams) | 10x (40 teams) | 100x (400 teams) |
|--------|-------------------|-----------------|-------------------|
| Games per round | 2 | 20 | 200 |
| Possessions per round | ~200 | ~2,000 | ~20,000 |
| `current_attributes` calls/round | ~8,000 | ~80,000 | ~800,000 |
| AI calls per round | ~10 | ~50+ | ~500+ |
| AI wall-clock (sequential) | 40-200s | 200-1000s | impossible |
| AI wall-clock (parallel) | 5-10s | 10-30s | 30-60s |

---

## Recommended Action Order

### Immediate (before next production deploy)
1. Add auth to `POST /api/seasons` and `POST /api/pace`
2. Add `nh3.clean()` to `_prose_to_html` filter
3. Add `PRAGMA foreign_keys=ON` to `_set_sqlite_pragmas` (after integrity check)

### Short-term (next 1-2 sessions)
4. Parallelize AI calls with `asyncio.gather()`
5. Cache `current_attributes` with stamina-based invalidation
6. Replace `model_dump()` with `getattr()` in `check_gate()`
7. Fix buggy `_get_active_season_id` in admin modules
8. Remove dead mirror subsystem (~639 lines)
9. Fix `_row_to_team` typing (`object` -> `TeamRow`)

### Medium-term (dedicated refactoring sessions)
10. Split `pages.py` into page-group routers
11. Split `bot.py` into command-group modules
12. Extract domain logic from Repository into core services
13. Decouple `deferred_interpreter` from Discord via EventBus
14. Complete Agent-to-Hooper rename (remove 25+ aliases)
15. Centralize test fixtures in `conftest.py`
