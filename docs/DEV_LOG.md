# Pinwheel Dev Log — 2026-02-12

Previous logs: [DEV_LOG_2026-02-10.md](DEV_LOG_2026-02-10.md) (Sessions 1-5, Days 1-4), Day 5 below (Sessions 6-10)

## Where We Are

- **401 tests**, zero lint errors
- **Days 1-5 complete:** simulation engine, governance + AI interpretation, mirrors + game loop, web dashboard + Discord bot + OAuth + evals framework, APScheduler, presenter pacing, AI commentary engine
- **Day 6 starting:** P1/P2 fixes, Discord server infrastructure, deploy, playtest, pitch
- **Latest commit:** `9c17bce` (P1/P2 priority list)

## Today's Agenda (Day 6: Harden + Deploy + Pitch)

### P1 — Must fix first
- [x] Fix `session_secret_key` hardcoded default — reject in production or auto-generate (Session 12)
- [x] Add auth gate to `/admin/evals` route (not just nav hiding) (Session 12)

### P2 — Fix before broader exposure
- [x] OAuth cookies: add `secure=True`, `samesite="lax"` in production (Session 12)
- [x] OAuth callback: wrap Discord API errors in try/except with graceful redirect (Session 12)

### Discord server infrastructure
- [ ] `/join` command — team enrollment with season-lock
- [ ] Channel setup on bot ready (`#how-to-play`, `#play-by-play`, `#big-plays`, per-team channels)
- [ ] Welcome message in `#how-to-play`
- [ ] Event routing — game results to `#play-by-play`, highlights to `#big-plays`

### Polish + Deploy
- [ ] CLAUDE.md accuracy pass (stale project structure, env vars, commands) ← done Session 11
- [ ] Fly.io deployment
- [ ] End-to-end playtest (Discord + web, full govern→simulate→observe→reflect cycle)
- [ ] Sharpen pitch narrative + demo script

## Day 5 Complete (Sessions 6-10, 2026-02-11)

---

## Session 6 — Demo Infrastructure (Showboat + Rodney)

**What was asked:** Integrate Simon Willison's Showboat (executable markdown demo builder) and Rodney (Chrome automation) into a demo workflow that proves the full govern→simulate→observe→reflect cycle works end-to-end.

**What was built:**
- `scripts/demo_seed.py` — Python CLI with 4 commands: `seed`, `step [N]`, `status`, `propose TEXT`. Seeds 4 Portland-themed teams (Rose City Thorns, Burnside Breakers, St. Johns Herons, Hawthorne Hammers) with 3 named agents each, distinct archetypes (sharpshooter, playmaker, enforcer, glass_cannon, floor_general, chaos_agent, two_way_star) and attribute distributions.
- `scripts/run_demo.sh` — 14-step Showboat-orchestrated demo script with Rodney screenshots. Produces a self-documenting Markdown artifact (`demo/pinwheel_demo.md`) with 9 screenshots proving the full cycle works.

**Demo captures:**
1. Home page (navigation cards, Blaseball aesthetic)
2. Arena (game panels with Elam Ending, quarter scores, possession count)
3. Standings (league table with W/L/PCT/PF/PA/DIFF)
4. Game detail (box scores, play-by-play, 100+ possessions)
5. Updated standings after 3 rounds
6. Mirrors archive (simulation + governance mirrors per round)
7. Governance (submitted proposal)
8. Rules page (full parameter list)
9. Team profile (roster with attribute bars, venue, record)

**Issues resolved:**
- Bash 3.2 (macOS default) doesn't support heredocs inside `"$(cat <<'DELIM'...)"` with single quotes in content — rewrote to use variables
- JS `querySelector` with `a[href*=/path/]` needs quoted attribute values — switched to direct URL navigation via Python DB queries for reliability
- `append_event` API signature had changed (added `aggregate_id`, `aggregate_type` required args) — updated demo_seed.py
- Rebuilt venv (`uv venv --seed && uv sync --extra dev`) to fix editable install .pth processing issue

**240 tests, zero lint errors.**

---

## Session 7 — Evals Framework (Proposal S + Proposal M)

**What was asked:** Implement the full evals framework covering both "are mirrors basically working?" (S) and "are mirrors improving governance?" (M). Core constraint: mirror content is for the player, not the developer.

**What was built:**

New package `src/pinwheel/evals/` with 12 modules:

- **`models.py`** — Pydantic models for all eval types. `RubricScore.mirror_type` is `Literal["simulation", "governance"]` — Pydantic rejects `"private"` at the type level.
- **`prescriptive.py`** (S.2c) — Regex scan for directive language ("should", "must", "needs to"). Returns count only, never matched text.
- **`grounding.py`** (S.2b) — Entity reference validation against known teams, agents, rule params. Content never stored in result.
- **`behavioral.py`** (S.2a) — Governance action shift detection. Never reads `MirrorRow.content` — only queries `GovernanceEventRow` and `MirrorRow.governor_id`. Computes Mirror Impact Rate.
- **`rubric.py`** (S.1) — Manual scoring for PUBLIC mirrors only. CSV export for offline analysis.
- **`golden.py`** (M.1) — 20 eval cases (8 sim, 7 gov, 5 private). Private cases have `structural_only=True`. Runner works with mock mirrors.
- **`ab_compare.py`** (M.2) — Dual-prompt comparison. `ABVariant.content` is `None` for private mirrors in review context.
- **`attribution.py`** (M.3) — Treatment/control random assignment. Reports aggregate delta only.
- **`gqi.py`** (M.4) — Governance Quality Index: Shannon entropy (proposal diversity), inverted Gini (participation breadth), keyword overlap (consequence awareness), normalized time-to-vote (deliberation).
- **`flags.py`** (M.6) — Scenario flagging: blowout games, suspicious unanimity, governance stagnation, participation collapse, rule backfire.
- **`rule_evaluator.py`** (M.7) — Opus-powered admin-facing analysis. The "expansive" AI — where mirrors constrain themselves, the evaluator explores freely. Mock fallback when no API key.

**Integration:**
- `EvalResultRow` added to `db/models.py` with indexes on `(season_id, round_number)` and `eval_type`
- `store_eval_result()` / `get_eval_results()` added to `repository.py`
- `_run_evals()` hook in `game_loop.py` — runs after mirrors, non-blocking (try/except), gated on `settings.pinwheel_evals_enabled`
- Variant B prompts added to `ai/mirror.py` with `generate_mirror_with_prompt()` helper
- Dashboard at `GET /admin/evals` — aggregate stats in cards/tables, scenario flags, AI rule evaluation. No mirror text.
- Nav link in `base.html` (dev/staging only, hidden in production)

**Privacy model verified:**
- `RubricScore(mirror_type="private")` → `ValidationError`
- `ABVariant.content` is `None` for private mirrors
- Behavioral shift never reads `MirrorRow.content`
- Dashboard shows aggregate stats only
- Nav link hidden in production (template checks `pinwheel_env`)

**Files modified (8):** `db/models.py`, `db/repository.py`, `config.py`, `core/game_loop.py`, `ai/mirror.py`, `main.py`, `api/pages.py`, `templates/base.html`

**Files created (25):** 12 eval modules, dashboard route + template, 13 test files

**87 new tests (327 total), zero lint errors.**

---

## Session 8 — Demo Script Update

**What was asked:** Update `run_demo.sh` to include the evals dashboard and correct the test count.

**What was built:**
- Added Step 14 (Evals Dashboard) to `scripts/run_demo.sh` — navigates to `/admin/evals`, captures `10_evals.png` screenshot showing aggregate mirror quality metrics, scenario flags, and AI rule evaluation
- Renumbered verification to Step 15, updated test count from 240 → 327
- Demo now has 15 steps and 10 screenshots

**No code changes to `demo_seed.py`** — evals run automatically via `_run_evals()` hook in `game_loop.py` when `step_round()` is called.

---

## Session 9 — Discord Governance Commands + Web Audit Trail

**What was asked:** Wire all governance slash commands to the service layer. Build out the full Discord interaction surface: `/propose` with AI interpretation + confirm/revise/cancel, `/vote` with hidden votes, `/tokens`, `/trade` with DM accept/reject, `/strategy` with confirm/cancel. Add private mirror DM delivery. Then: auditable governance trails on the web behind auth, showing vote totals but not individual votes.

**What was built:**

### Phase 1: Foundation (helpers, embeds, views)

- **`src/pinwheel/discord/helpers.py`** (new) — `GovernorInfo` frozen dataclass, `GovernorNotFound` exception, `get_governor()` auth lookup, `get_current_season_id()`, `db_session()` context manager. Consistent governor auth for all commands.
- **`src/pinwheel/discord/embeds.py`** — 4 new embed builders: `build_interpretation_embed`, `build_token_balance_embed`, `build_trade_offer_embed`, `build_strategy_embed`.
- **`src/pinwheel/discord/views.py`** (new) — Full discord.py View/Modal classes:
  - `ProposalConfirmView` (Confirm/Revise/Cancel buttons, calls `submit_proposal` + `confirm_proposal`)
  - `ReviseProposalModal` (text input, re-interprets via AI, updates parent view)
  - `TradeOfferView` (Accept/Reject buttons, calls `accept_trade` or appends `trade.rejected`)
  - `StrategyConfirmView` (Confirm/Cancel, appends `strategy.set` event)
- **`src/pinwheel/core/game_loop.py`** — Fixed private mirror event publishing (captured `mirror_row` return, added `event_bus.publish("mirror.generated", ...)` with `mirror_type: "private"`, `governor_id`, `mirror_id`).

### Phase 2: Command wiring

Rewrote/added 5 governance commands in `src/pinwheel/discord/bot.py`:

| Command | Flow |
|---------|------|
| `/propose <text>` | Auth → token check → defer → AI interpret (real or mock) → ephemeral ProposalConfirmView |
| `/vote yes\|no [boost]` | Auth → find latest confirmed proposal → duplicate check → vote weight from team size → cast (hidden) |
| `/tokens` | Auth → derive balance from event log → show PROPOSE/AMEND/BOOST |
| `/trade @user <offer> <request>` | Auth both → balance check → create trade → DM target with Accept/Reject |
| `/strategy <text>` | Auth → show StrategyConfirmView (Confirm/Cancel) |

**Private mirror DMs:** `_send_private_mirror()` looks up governor's Discord ID from PlayerRow, DMs them the mirror embed.

### Phase 3: Web governance audit trail

- **`/governance` page auth-gated:** Redirects to `/auth/login` when Discord OAuth is configured. In dev mode without OAuth, accessible directly.
- **Vote totals displayed:** Visual tally bar (green/red) with weighted yes/no, vote count, approval percentage. Individual votes never exposed.
- **Proposal outcome tracking:** Status derived from actual events (submitted → confirmed → passed/failed).
- **New CSS:** `.vote-tally`, `.tally-bar`, `.tally-yes`, `.tally-numbers`, `.status-confirmed`, `.status-submitted`.

**Files modified (5):** `discord/bot.py`, `discord/embeds.py`, `core/game_loop.py`, `api/pages.py`, `templates/pages/governance.html`, `static/css/pinwheel.css`

**Files created (2):** `discord/helpers.py`, `discord/views.py`

**Issues resolved:**
- `season.current_ruleset` can be `None` (not just `{}`) — used `(season.current_ruleset or {})` pattern
- `.env` file has Discord OAuth creds, so test Settings inherit them — updated governance page test to expect 302 when OAuth is enabled
- ruff SIM105: replaced `try/except/pass` with `contextlib.suppress(discord.Forbidden)` for trade DM fallback

**31 new tests (358 total), zero lint errors.**

---

## Session 10 — APScheduler + Presenter Pacing + AI Commentary

**What was asked:** Execute three features in parallel: (1) APScheduler integration for automatic round advancement, (2) presenter pacing modes for the 20-30 min demo experience, (3) AI commentary engine for broadcaster-style game narratives.

**What was built:**

### APScheduler Integration

- **`src/pinwheel/core/scheduler_runner.py`** (new) — `tick_round(engine, event_bus, api_key)` async function. Finds active season, determines next round from `max(GameResultRow.round_number) + 1`, calls `step_round()`. All exceptions caught and logged — scheduler never interrupted.
- **`src/pinwheel/main.py`** — APScheduler wired into FastAPI lifespan. `AsyncIOScheduler` with `CronTrigger.from_crontab()`. Gated on `pinwheel_auto_advance=True` and `effective_game_cron() is not None`. Clean shutdown on teardown.
- **`src/pinwheel/config.py`** — Added `pinwheel_auto_advance: bool = True`.

### Presenter Pacing

- **`src/pinwheel/config.py`** — `PACE_CRON_MAP` (fast=`*/1`, normal=`*/5`, slow=`*/15`, manual=`None`), `VALID_PACES` frozenset, `pinwheel_presentation_pace: str = "fast"`, `effective_game_cron()` method (explicit cron overrides pace, pace derives cron, manual → None).
- **`src/pinwheel/api/pace.py`** (new) — `GET /api/pace` returns current pace/cron/auto_advance. `POST /api/pace` changes pace in memory (demo convenience, not persisted). Validates against `VALID_PACES`.
- **`src/pinwheel/main.py`** — Registered `pace_router`.

### AI Commentary Engine

- **`src/pinwheel/ai/commentary.py`** (new) — Full commentary module with 4 functions:
  - `generate_game_commentary()` — Claude Sonnet-powered broadcaster-style commentary. Builds rich context from box scores, possession log, Elam status. Prompt: energetic, dramatic, Blaseball energy.
  - `generate_game_commentary_mock()` — Template-based fallback. Margin-aware openers (nail-biter/blowout/standard), Elam paragraph, star performer paragraph. References real names and scores.
  - `generate_highlight_reel()` — Sonnet-powered round summary. One punchy sentence per game + overall narrative.
  - `generate_highlight_reel_mock()` — Template-based fallback with Elam/blowout/close-game awareness + total points summary.
- **`src/pinwheel/core/game_loop.py`** — Commentary integrated into game loop:
  - Per-game commentary generated after each game result (non-blocking try/except). Added to `summary["commentary"]`.
  - Round highlight reel generated after all games. Included in `round.completed` event data.
- **`src/pinwheel/discord/embeds.py`** — Added `build_commentary_embed()` for Discord display.

**Files modified (4):** `config.py`, `main.py`, `game_loop.py`, `discord/embeds.py`

**Files created (5):** `core/scheduler_runner.py`, `api/pace.py`, `ai/commentary.py`, `tests/test_scheduler_runner.py`, `tests/test_pace.py`, `tests/test_commentary.py`

**43 new tests (401 total), zero lint errors.**

---

## Session 12 — P1/P2 Security Hardening

**What was asked:** Fix all 4 security issues before inviting players/testers into the Discord.

**What was built:**

### P1 #1: Session secret key
- `config.py` — Removed hardcoded default. Added `model_validator(mode="after")`:
  - Production: raises `ValueError` if `SESSION_SECRET_KEY` is empty
  - Development: auto-generates a random `secrets.token_urlsafe(32)`

### P1 #2: Evals dashboard auth gate
- `api/eval_dashboard.py` — Added the same auth redirect pattern as `/governance`: checks `current_user is None and oauth_enabled`, redirects to `/auth/login`.

### P2 #3: Secure OAuth cookies
- `auth/oauth.py` — Both `set_cookie()` calls (state cookie and session cookie) now include `secure=is_prod` where `is_prod = settings.pinwheel_env == "production"`.

### P2 #4: Graceful OAuth callback errors
- `auth/oauth.py` — Both `_exchange_code()` and `_fetch_user()` calls wrapped in try/except. Discord API errors now redirect to `/` instead of returning raw 500s.

**Files modified (3):** `config.py`, `api/eval_dashboard.py`, `auth/oauth.py`

**7 new tests (408 total), zero lint errors.**

---

## Session 11 — CLAUDE.md Accuracy Audit (Day 6 Start)

**What was asked:** Begin Day 6. Review CLAUDE.md for accuracy. Identify what's missing or unnecessary.

**What was found:**

CLAUDE.md had significant drift from reality after 5 days of rapid development:
- **Project Structure** was fiction — listed nonexistent files (`api/router.py`, `api/tokens.py`, `core/events.py`, `ai/client.py`, `ai/prompts.py`), missed entire modules (`auth/`, `discord/`, `evals/` — 20+ real files)
- **Tech Stack** claimed Alembic (not used — we use `Base.metadata.create_all()`)
- **Architecture** referenced numpy (not used)
- **Compound Engineering plugin** references were stale (plugin doesn't work)
- **Environment Variables** listed 5 of 13+ real variables
- **Common Commands** used bare `pytest` instead of `uv run pytest`

**What was fixed:**
- Rewrote Project Structure to match actual 59 source files across 8 modules
- Replaced Alembic reference with actual schema approach
- Replaced numpy reference with actual approach (pure Python + stdlib random)
- Replaced Compound Engineering workflow with actual Plan → Build → Test → Commit workflow
- Updated Environment Variables to list all 13 real variables
- Updated Common Commands to use `uv run` prefix and include demo seeding

**401 tests (unchanged), zero lint errors.**

---
