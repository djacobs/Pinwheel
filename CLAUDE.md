# Pinwheel Fates

Auto-simulated 3v3 basketball league where human players govern the rules through AI-interpreted natural language proposals. Starts out as basketball, finishes as ???. Built for hackathon track: "Amplify Human Judgment." Claude Opus 4.6 serves as the game's social reporter — surfacing patterns in gameplay and governance that players can't see from inside the system. The details of the game are defined in docs/product/RUN_OF_PLAY.md 

## Prior Art & Philosophy

Pinwheel is built on the principles of [Resonant Computing](https://resonantcomputing.org) — technology designed to align with human values rather than exploit attention. The five principles map directly to the game:

- **Private:** Each player's private report is theirs alone. The AI's reflection of your behavior is visible only to you. In the era of AI, whoever controls the context holds the power. In Pinwheel, players steward their own context.
- **Dedicated:** The AI works exclusively for the players. No engagement optimization, no hidden agendas. Its only function is making the group's dynamics visible to the group.
- **Plural:** No single player — and not the AI — controls the rules. Governance is genuinely distributed. The AI models consequences; humans decide.
- **Adaptable:** The rules are open-ended. Players can change game mechanics, league structure, and even the meta-rules of governance itself. The game evolves as the community evolves.
- **Prosocial:** Playing Pinwheel practices collective self-governance. The private report builds self-awareness. The governance report builds systems awareness. The game is a rehearsal space for being better stewards of shared systems.

See also: `docs/product/VISION.md` for the full philosophical grounding.

## Tech Stack

- **Backend:** Python 3.12+ / FastAPI
- **Database:** SQLite via SQLAlchemy 2.0 async (aiosqlite). Schema managed via `Base.metadata.create_all()` + `auto_migrate_schema()` for additive changes (no Alembic).
- **AI:** Claude Opus 4.6 via Anthropic API
- **Discord:** discord.py 2.x — bot runs in-process with FastAPI
- **Frontend:** HTMX + SSE + Jinja2 templates (optional Textual TUI for terminal). Technically a live-updating data dashboard — standings, play-by-play, governance panels, AI reflections — but it **must be fun**. The aesthetic is retro, bold, community-focused — joyful chaos. Full CSS control via Jinja2 templates. No JS build step.
- **Testing:** pytest, pytest-asyncio, httpx (async test client)
- **Linting/Formatting:** ruff
- **Scheduling:** APScheduler (AsyncIOScheduler) for automatic round advancement

## LIVE DATA — Handle With Care

**Production has real players.** Testers have joined teams via Discord. Any operation that drops, resets, or migrates the production database forces them to re-enroll. This is painful and erodes trust.

- **Never drop or recreate the production database** without explicit user approval. No `create_all()` on prod, no `rm pinwheel.db`, no table renames without a migration path.
- **Additive schema changes are auto-migrated.** `auto_migrate_schema()` in `db/engine.py` runs at startup: it compares ORM models against the live SQLite schema and adds missing columns automatically (nullable columns and columns with scalar defaults). No migration script needed for adding new fields. **Destructive changes** (column renames, type changes, drops, NOT NULL without default) still require manual `ALTER TABLE` scripts — test against a copy of prod data first.
- **Deploy is not reseed.** Deploying new code should not require reseeding. If a code change would break existing data, stop and flag it.
- **Back up before destructive operations.** `flyctl ssh console -C "cp /data/pinwheel.db /data/pinwheel.db.bak"` takes 2 seconds and saves hours of apologies.
- **Test locally with prod-shaped data** before deploying schema or data-layer changes. Use `demo_seed.py` to create a representative dataset and verify the migration preserves it.

## Architecture Principles

### Performance
- Simulation engine must run hundreds of games per hour. Profile hot paths.
- Use async FastAPI endpoints throughout. No blocking calls in request handlers.
- Simulation is CPU-bound: pure Python with standard library random. Profile before adding dependencies.
- Database: proper indexing, batch inserts for game results, connection pooling.
- Opus 4.6 calls are I/O-bound: use async httpx, batch where possible, cache report outputs.
- FrontEnd must be *fast* and *delightful*. This is a game is about governance but must also be joyful. 

### Open Source
- MIT license.
- Clean, readable code with docstrings on all public interfaces.
- No secrets in code. All credentials via environment variables.
- Document every public API endpoint.
- README should let a stranger clone, install, and run in under 5 minutes.

### Dependencies
- **Dependencies must earn their place.** Every dependency should solve a real problem we have now. The test: "what does this give us that justifies its weight?"
- **FastAPI and Pydantic are examples of dependencies that earn their place.** FastAPI gives more than it takes — async, OpenAPI docs, dependency injection, validation. Pydantic is the shared vocabulary across all layers. Use them freely wherever they help.
- When we add a GUI, we'll add a game engine. When we need caching, we'll add Redis. Each earns its place by solving a current problem. Just have a great reason.

### Instrumentation
- Structured logging, middleware timing, and AI call tracking from Day 1. Not optional, not bolted on later.
- Every API request timed via middleware. Every Opus 4.6 call tracked (tokens, latency, context type). Every simulation block profiled.
- Player behavior events captured for gameplay health metrics (governance participation, report engagement, token velocity).
- See `docs/INSTRUMENTATION.md` for the full spec: joy metrics, performance targets, token cost accounting.

### API/Client Abstractions
- **Strict separation:** API route handlers are thin. All game logic lives in the service/domain layer (`core/`).
- **Pydantic models** define all API contracts. Request and response models are explicit.
- **Dependency injection** for service layer access in route handlers.
- **Repository pattern** for database access. Domain logic never imports database modules directly.
- **AI layer abstraction:** All Opus 4.6 calls go through `ai/` module. Route handlers and core logic never call the Anthropic API directly. This allows swapping models, adding caching, or mocking in tests.

### Testing — NON-NEGOTIABLE
- **Every feature must have tests.** No exceptions.
- **All tests must pass before any commit.** Run `pytest` and confirm green before `git commit`.
- Unit tests for: simulation engine, governance logic, token economy, AI interpretation parsing.
- Integration tests for: API endpoints (use httpx AsyncClient), end-to-end governance flows.
- Use pytest fixtures for common setup (teams, agents, rules, game states).
- Test the simulation engine with known seeds for deterministic verification.
- Mock Opus 4.6 calls in unit tests; use real calls only in clearly-marked integration tests.
- Target: 80%+ coverage on core/ and api/ modules.

## Project Structure

```
pinwheel/
├── src/
│   └── pinwheel/
│       ├── __init__.py
│       ├── main.py              # FastAPI app factory + lifespan (APScheduler, Discord bot)
│       ├── config.py            # Settings via pydantic-settings, PACE_CRON_MAP
│       ├── api/                 # Route handlers (thin)
│       │   ├── __init__.py
│       │   ├── deps.py          # Dependency injection helpers
│       │   ├── events.py        # SSE event streaming
│       │   ├── games.py         # Game results, schedules, box scores
│       │   ├── governance.py    # Proposals, votes, amendments
│       │   ├── reports.py       # AI reflections (public only)
│       │   ├── pages.py         # HTML page routes (Jinja2)
│       │   ├── pace.py          # GET/POST /api/pace (presenter pacing)
│       │   ├── standings.py     # League standings
│       │   ├── teams.py         # Team/agent management
│       │   └── eval_dashboard.py # /admin/evals (aggregate stats, no report text)
│       ├── core/                # Domain logic
│       │   ├── __init__.py
│       │   ├── simulation.py    # Basketball sim engine (pure functions)
│       │   ├── possession.py    # Possession-level simulation
│       │   ├── scoring.py       # Shot resolution, Elam Ending
│       │   ├── defense.py       # Defensive matchup logic
│       │   ├── moves.py         # Offensive move selection
│       │   ├── state.py         # Game state dataclasses
│       │   ├── archetypes.py    # Agent archetype definitions
│       │   ├── seeding.py       # Team/agent generation
│       │   ├── governance.py    # Proposal lifecycle, voting
│       │   ├── tokens.py        # Token economy, trading
│       │   ├── scheduler.py     # Round-robin schedule generation
│       │   ├── scheduler_runner.py # APScheduler tick_round() for auto-advance
│       │   ├── game_loop.py     # step_round() orchestrator (sim → gov → reports → evals → commentary)
│       │   ├── event_bus.py     # In-process pub/sub event bus
│       │   └── hooks.py         # Event bus hook registrations
│       ├── ai/                  # Claude integration
│       │   ├── __init__.py
│       │   ├── interpreter.py   # Rule proposal → structured rule (sandboxed)
│       │   ├── report.py        # Reflection generation (sim, gov, private)
│       │   └── commentary.py    # Broadcaster-style game commentary + highlight reels
│       ├── auth/                # Discord OAuth2
│       │   ├── __init__.py
│       │   ├── deps.py          # Auth dependency injection
│       │   └── oauth.py         # OAuth2 flow + session management
│       ├── discord/             # Discord bot integration
│       │   ├── __init__.py
│       │   ├── bot.py           # Bot setup, 15 slash commands (see Discord Commands below)
│       │   ├── embeds.py        # Discord embed builders
│       │   ├── helpers.py       # Governor auth lookup, session helpers
│       │   └── views.py         # Interactive views (ProposalConfirm, TradeOffer, StrategyConfirm)
│       ├── evals/               # Evaluation framework (Proposal S + M)
│       │   ├── __init__.py
│       │   ├── models.py        # Pydantic eval models
│       │   ├── prescriptive.py  # S.2c — directive language scan
│       │   ├── grounding.py     # S.2b — entity reference validation
│       │   ├── behavioral.py    # S.2a — governance action shift detection
│       │   ├── rubric.py        # S.1 — manual scoring (public reports only)
│       │   ├── golden.py        # M.1 — 20 eval cases
│       │   ├── ab_compare.py    # M.2 — dual-prompt comparison
│       │   ├── attribution.py   # M.3 — treatment/control assignment
│       │   ├── gqi.py           # M.4 — Governance Quality Index
│       │   ├── flags.py         # M.6 — scenario flagging
│       │   └── rule_evaluator.py # M.7 — Opus-powered admin analysis
│       ├── models/              # Pydantic models (shared vocabulary)
│       │   ├── __init__.py
│       │   ├── game.py          # GameResult, BoxScore, PlayByPlay
│       │   ├── team.py          # Team, Agent, AgentAttributes
│       │   ├── rules.py         # RuleSet, RuleChange, GameEffect
│       │   ├── governance.py    # Proposal, Amendment, Vote
│       │   ├── tokens.py        # TokenBalance, Trade, TokenType
│       │   └── report.py        # Reflection, ReportUpdate
│       └── db/                  # Database layer
│           ├── __init__.py
│           ├── engine.py        # Connection setup
│           ├── models.py        # SQLAlchemy ORM models
│           └── repository.py    # Data access (repository pattern)
├── tests/
│   ├── conftest.py              # Shared fixtures
│   ├── test_simulation.py
│   ├── test_governance.py
│   ├── test_game_loop.py
│   ├── test_event_bus.py
│   ├── test_reports.py
│   ├── test_models.py
│   ├── test_seeding.py
│   ├── test_observe.py
│   ├── test_db.py
│   ├── test_auth.py
│   ├── test_discord.py
│   ├── test_pages.py
│   ├── test_pace.py
│   ├── test_scheduler_runner.py
│   ├── test_commentary.py
│   ├── test_api/
│   │   └── test_e2e.py
│   └── test_evals/              # 12 eval test files
│       ├── conftest.py
│       ├── test_prescriptive.py, test_grounding.py, test_behavioral.py
│       ├── test_rubric.py, test_golden.py, test_ab_compare.py
│       ├── test_attribution.py, test_gqi.py, test_flags.py
│       ├── test_rule_evaluator.py, test_models.py, test_eval_dashboard.py
├── templates/                   # Jinja2 HTML templates
│   ├── base.html
│   └── pages/                   # arena, standings, governance, reports, rules, teams, game detail
├── static/                      # CSS + JS (htmx)
├── scripts/
│   ├── demo_seed.py             # CLI: seed, step, status, propose
│   └── run_demo.sh              # 15-step Showboat/Rodney demo script
├── docs/
│   ├── TABLE_OF_CONTENTS.md
│   ├── SIMULATION.md, GAME_LOOP.md, INTERFACE_CONTRACTS.md
│   ├── SECURITY.md, INSTRUMENTATION.md, DEMO_MODE.md, OPS.md
│   ├── LIFECYCLE.svg
│   ├── dev_log/                 # Dev logs and UX notes
│   │   ├── DEV_LOG.md           # Running log of decisions and work
│   │   ├── DEV_LOG_2026-02-*.md # Archived daily logs
│   │   └── UX_NOTES.md          # Visual/interaction change record
│   ├── product/                 # Product and player-facing docs
│   │   ├── VISION.md, PLAN.md, RUN_OF_PLAY.md
│   │   ├── PRODUCT_OVERVIEW.md, PLAYER.md, VIEWER.md
│   │   ├── GLOSSARY.md, GAME_MOMENTS.md, NEW_GOVERNOR_GUIDE.md
│   │   ├── ACCEPTANCE_CRITERIA.md, COLOPHON.md
│   │   └── ...
│   └── plans/                   # Feature plans
├── pyproject.toml
├── fly.toml                     # Fly.io deployment config
├── Dockerfile                   # Multi-stage Docker build
├── CLAUDE.md                    # (this file)
└── README.md
```

## Development Workflow

### Plan → Build → Test → Commit

1. **Plan first** — Before writing code, research the codebase and create a plan (use Claude Code's plan mode). Plans reference existing patterns, identify affected files, and define acceptance criteria.
2. **Build with tests** — Execute the plan. Follow existing patterns. Test as you go, not at the end. Ship complete features — don't leave things 80% done.
3. **Test and lint** — `uv run pytest -x -q` and `uv run ruff check src/ tests/` must both pass before committing.
4. **Commit** — Stage specific files, write a conventional commit message, and commit the passing state.

### Session discipline — NON-NEGOTIABLE

Every work session must end by running `/post-commit`. This skill automates the session-end checklist:

1. **Tests pass.** Run `uv run pytest -x -q` and confirm green. Every new feature needs tests. Coverage should be as broad as logically possible — not just happy paths, but auth failures, empty states, edge cases. If you added code, you added tests for it.
2. **Demo artifacts.** After significant visual changes, run Rodney (`uvx rodney screenshot`) and Showboat (`bash scripts/run_demo.sh`) to capture updated screenshots for `demo/`.
3. **Dev log updated.** Update `docs/dev_log/DEV_LOG.md` with what was asked, what was built, issues resolved, and the new test count. Update the "Today's Agenda" checkboxes. This is the project's memory — future sessions depend on it.
4. **Plans archived.** Copy any Claude Code plan files from `~/.claude/plans/` into `docs/plans/` with descriptive filenames. Plans are project artifacts — they belong in the repo, not just in Claude's local state.
5. **UX notes updated.** If any visual or interaction changes were made, update `docs/dev_log/UX_NOTES.md` with numbered entries describing the problem, fix, and implementation.
6. **Code committed and pushed.** Stage the specific files you changed and commit with a conventional commit message. Push to GitHub. Never leave passing code uncommitted. Never commit failing tests.

If you're unsure whether to commit, the answer is yes — commit the passing state. Uncommitted work is lost work.

### Incremental commits during work

Commit when you have a complete, valuable unit of change — not "WIP." If you can't write a commit message that describes a complete change, wait. Run tests before every commit. Stage specific files, not `git add .`.

### Keeping docs alive

- **`docs/dev_log/DEV_LOG.md`** — Update after each session. Each entry follows the format: **What was asked**, **What was built**, **Issues resolved**, **test count + lint status**. When a session adds new features, update the "Today's Agenda" checkboxes and note the session number. The dev log is the project's memory — future sessions read it to understand where we are.
- **`docs/dev_log/UX_NOTES.md`** — **Update whenever a visual or interaction change is made.** Every UI change — new pages, redesigned layouts, component additions, style changes, narration improvements — gets a numbered entry with the problem, the fix, and implementation details. This is the design decision record. If you touched a template or CSS, you update UX_NOTES.
- **`scripts/run_demo.sh`** — When a feature adds a new page or route, add a corresponding demo step with a Rodney screenshot. Update the test count in the verification step. The demo script is the project's proof — it must reflect the current state of the application. **Run Rodney and Showboat after significant visual changes** to capture updated screenshots.
- **Design docs** (`SIMULATION.md`, `GAME_LOOP.md`, etc.) — When a design question is resolved, update the doc. Replace TODOs with decisions. Design docs should reflect the current state of the system, not the state when they were first written.
- **`CLAUDE.md`** — When a design decision is made that affects architecture, code standards, or project structure, capture it here. This file is the single source of truth for how we build.
- **Plan files** (`docs/plans/`) — Check off items as they're completed. Plans are living documents, not write-once specs.

## Code Standards

### Style
- Type hints on all function signatures. No `Any`. Use a `Protocol`, `Generic`, or `Union` instead. If none of those work, annotate with `# type: Any because <reason>` — the justification lives in the code, not in a conversation.
- Format: `ruff format .`
- Lint: `ruff check .`
- Import sort: `ruff check --select I --fix .`

### Git
- Conventional commits: `feat:`, `fix:`, `test:`, `docs:`, `refactor:`
- Small, focused commits. One concern per commit.
- Never commit failing tests. Run `pytest` before every commit.
- Branch naming: `feat/simulation-engine`, `fix/token-trading-race`, etc.

### Documentation
- Docstrings on all public functions and classes.
- API endpoints documented with FastAPI's built-in OpenAPI support.
- Complex logic gets inline comments explaining *why*, not *what*.

## Key Design Decisions

### AI interpretation is sandboxed
Player-submitted text never enters the simulation engine's context. Opus 4.6 interprets proposals in an isolated context with strict system instructions. The structured output is validated against the rule space schema before it can affect the simulation. This is both a security boundary and a gameplay feature — the AI acts as a constitutional interpreter.

### Game Richness

Every player-facing output (commentary, reports, embeds, Discord messages, web pages) should reflect the full dramatic context of the current moment. When touching any output system, audit: *does this system know about playoffs, standings, streaks, rule changes, rivalries, and milestones?*

A playoff game that reads like a regular-season game is a bug. An AI report that doesn't mention a team's 5-game win streak is a missed opportunity. A Discord embed for the championship finals that looks identical to Round 1 is broken.

The simulation has the data — make sure the outputs use it. When adding or modifying any feature that produces player-visible text, check it against `docs/product/GAME_MOMENTS.md` for dramatic context that should be included.

## Discord Commands

| Command | What It Does | Params |
|---------|-------------|--------|
| `/join TEAM` | Enroll on a team as a governor | `team` (autocomplete, required) |
| `/propose TEXT` | Submit a rule change to the Floor | `text` (natural language) |
| `/vote YES\|NO [boost] [proposal]` | Vote on a proposal | `choice`, `boost` (bool), `proposal` (autocomplete) |
| `/tokens` | Check your Floor token balance | — |
| `/trade @USER` | Offer a token trade to another governor | `target`, `offer_type`, `offer_amount`, `request_type`, `request_amount` |
| `/trade-hooper` | Propose trading hoopers between teams | `offer_hooper` (autocomplete), `request_hooper` (autocomplete) |
| `/strategy TEXT` | Set your team's strategic direction | `text` (natural language, AI-interpreted) |
| `/bio HOOPER TEXT` | Write a backstory for a hooper | `hooper` (autocomplete), `text` |
| `/standings` | View current league standings | — |
| `/schedule` | View upcoming game schedule | — |
| `/reports` | View latest AI reports | — |
| `/profile` | View your governor profile and Floor record | — |
| `/proposals [season]` | View all proposals and their status | `season` (current\|all) |
| `/roster` | View all enrolled governors | — |
| `/effects` | View all active effects in the league | — |
| `/repeal EFFECT` | Propose repealing an active effect | `effect` (autocomplete) |
| `/ask TEXT` | Ask the AI a question about the league | `text` (natural language) |
| `/status` | View current league status (round, season, pace) | — |
| `/history HOOPER` | View a hooper's game history and stats | `hooper` (autocomplete) |
| `/edit-series` | Edit playoff series parameters (admin only) | series params |
| `/new-season NAME [carry_rules]` | Start a new season (admin only) | `name`, `carry_rules` (bool, default true) |

## Environment Variables

```
ANTHROPIC_API_KEY=              # Claude API key (mock fallback if unset)
DATABASE_URL=                   # SQLite only — e.g. sqlite+aiosqlite:///pinwheel.db
PINWHEEL_ENV=development        # development | staging | production
PINWHEEL_PRESENTATION_PACE=fast # fast (1min) | normal (5min) | slow (15min) | manual
PINWHEEL_PRESENTATION_MODE=instant # instant | replay
PINWHEEL_GAME_CRON="0 * * * *" # Explicit cron override (optional, pace derives it)
PINWHEEL_AUTO_ADVANCE=true      # APScheduler auto-advance toggle
PINWHEEL_GOVERNANCE_INTERVAL=1  # Tally governance every N rounds (1 = every round)
PINWHEEL_EVALS_ENABLED=true     # Run evals after each round
PINWHEEL_ADMIN_DISCORD_ID=      # Discord user ID for admin notifications (veto flow)
SESSION_SECRET_KEY=             # ⚠️ MUST set in production (P1 issue)
DISCORD_TOKEN=                  # Discord bot token
DISCORD_GUILD_ID=               # Target guild ID
DISCORD_CLIENT_ID=              # OAuth2 client ID
DISCORD_CLIENT_SECRET=          # OAuth2 client secret
DISCORD_REDIRECT_URI=           # OAuth2 callback URL
```

## Common Commands

```bash
# Install dependencies
uv sync --extra dev

# Run tests (DO THIS BEFORE EVERY COMMIT)
uv run pytest -x -q

# Run tests with coverage
uv run pytest --cov=pinwheel --cov-report=term-missing

# Lint (must pass before commit)
uv run ruff check src/ tests/

# Format
uv run ruff format src/ tests/

# Start dev server
uv run uvicorn pinwheel.main:app --reload

# Demo seeding
uv run python scripts/demo_seed.py seed       # Create 4 teams + schedule
uv run python scripts/demo_seed.py step 3     # Advance 3 rounds
uv run python scripts/demo_seed.py status     # Show standings

# Change pace at runtime (demo convenience)
curl -X POST http://localhost:8000/api/pace -H 'Content-Type: application/json' -d '{"pace":"slow"}'
```

## Demo Pipeline

[Showboat](https://github.com/simonw/showboat) (executable markdown demo builder) and [Rodney](https://github.com/simonw/rodney) (Chrome automation) produce a self-documenting demo artifact with screenshots proving the full govern→simulate→observe→reflect cycle works end-to-end. Both are Simon Willison tools, invoked via `uvx` (no install needed).

```bash
# Run the full demo (seeds league, starts server, captures 10 screenshots)
bash scripts/run_demo.sh
# Output: demo/pinwheel_demo.md — Markdown with embedded screenshots

# Manual seeding for local dev (no Showboat/Rodney needed)
python scripts/demo_seed.py seed            # Create 4 teams + round-robin schedule
python scripts/demo_seed.py step 3          # Advance 3 rounds (sim + gov + reports + evals)
python scripts/demo_seed.py status          # Show current standings
python scripts/demo_seed.py propose TEXT    # Submit a governance proposal

# Rodney screenshots (individual page captures)
uvx rodney screenshot http://localhost:8000/ demo/home.png        # Any page → PNG
uvx rodney screenshot http://localhost:8000/arena demo/arena.png
```

### Showboat

Showboat wraps `run_demo.sh` into an executable Markdown document. The output (`demo/pinwheel_demo.md`) is both human-readable documentation and a runnable script. This is the hackathon demo artifact — the proof that the system works end-to-end.

### Rodney

Rodney is headless Chrome automation for screenshots. It captures the exact visual state of each page. Every new page or route should get a corresponding Rodney screenshot step in `run_demo.sh`. The screenshots are committed to `demo/` and embedded in the Showboat output.

**When to update the demo pipeline:**
- Added a new page? → Add a Rodney screenshot step to `run_demo.sh`
- Changed the visual layout of an existing page? → Re-run the demo to update screenshots
- Added a new feature visible in the UI? → Consider whether a demo step captures it

### run_demo.sh

A 15-step script: seed the league, start the server, capture each major page (home, arena, standings, game detail, reports, governance, rules, team profile, evals dashboard), then run the test suite as verification.

### demo_seed.py

Uses `step_round()` from the game loop directly, so all hooks (reports, evals, event bus) run automatically — no separate seeding needed for new features that integrate into the game loop.
