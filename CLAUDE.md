# Pinwheel Fates

Blaseball-inspired auto-simulated 3v3 basketball league where human players govern the rules through AI-interpreted natural language proposals. Built for hackathon track: "Amplify Human Judgment." Claude Opus 4.6 serves as the game's social mirror — surfacing patterns in gameplay and governance that players can't see from inside the system.

## Prior Art & Philosophy

Pinwheel is built on the principles of [Resonant Computing](https://resonantcomputing.org) — technology designed to align with human values rather than exploit attention. The five principles map directly to the game:

- **Private:** Each player's private mirror is theirs alone. The AI's reflection of your behavior is visible only to you. In the era of AI, whoever controls the context holds the power. In Pinwheel, players steward their own context.
- **Dedicated:** The AI works exclusively for the players. No engagement optimization, no hidden agendas. Its only function is making the group's dynamics visible to the group.
- **Plural:** No single player — and not the AI — controls the rules. Governance is genuinely distributed. The AI models consequences; humans decide.
- **Adaptable:** The rules are open-ended. Players can change game mechanics, league structure, and even the meta-rules of governance itself. The game evolves as the community evolves.
- **Prosocial:** Playing Pinwheel practices collective self-governance. The private mirror builds self-awareness. The governance mirror builds systems awareness. The game is a rehearsal space for being better stewards of shared systems.

See also: `docs/VISION.md` for the full philosophical grounding.

## Tech Stack

- **Backend:** Python 3.12+ / FastAPI
- **Database:** PostgreSQL via SQLAlchemy 2.0 async (SQLite/aiosqlite for local dev, asyncpg for production). Alembic for migrations.
- **AI:** Claude Opus 4.6 via Anthropic API
- **Discord:** discord.py 2.x — bot runs in-process with FastAPI
- **Frontend:** HTMX + SSE + Jinja2 templates (optional Textual TUI for terminal). Technically a live-updating data dashboard — standings, play-by-play, governance panels, AI reflections — but it **must be fun**. The Blaseball aesthetic (retro, bold, community-focused) is the north star. Full CSS control via Jinja2 templates. No JS build step.
- **Testing:** pytest, pytest-asyncio, httpx (async test client)
- **Linting/Formatting:** ruff
- **Dev Workflow:** [Compound Engineering plugin](https://github.com/EveryInc/compound-engineering-plugin) for Claude Code. Install: `/plugin marketplace add https://github.com/EveryInc/compound-engineering-plugin` then `/plugin install compound-engineering`. Provides `/workflows:plan`, `/workflows:work`, `/workflows:review`, `/workflows:compound`. See "Development Workflow" section below.

## Architecture Principles

### Performance
- Simulation engine must run hundreds of games per hour. Profile hot paths.
- Use async FastAPI endpoints throughout. No blocking calls in request handlers.
- Simulation is CPU-bound: consider numpy/vectorized operations for game math.
- Database: proper indexing, batch inserts for game results, connection pooling.
- Opus 4.6 calls are I/O-bound: use async httpx, batch where possible, cache mirror outputs.
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
- Player behavior events captured for gameplay health metrics (governance participation, mirror engagement, token velocity).
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
│       ├── main.py              # FastAPI app factory
│       ├── config.py            # Settings via pydantic-settings
│       ├── api/                 # Route handlers (thin)
│       │   ├── __init__.py
│       │   ├── router.py        # Top-level router
│       │   ├── games.py         # Game results, schedules, box scores
│       │   ├── governance.py    # Proposals, votes, amendments
│       │   ├── teams.py         # Team/agent management
│       │   ├── tokens.py        # Token balances, trades
│       │   └── mirrors.py       # AI reflections (public + private)
│       ├── core/                # Domain logic
│       │   ├── __init__.py
│       │   ├── simulation.py    # Basketball sim engine (pure functions)
│       │   ├── governance.py    # Proposal lifecycle, voting
│       │   ├── tokens.py        # Token economy, trading
│       │   ├── rules.py         # Rule space definitions, parameter boundaries
│       │   ├── scheduler.py     # Game scheduling, round-robin generation
│       │   └── events.py        # Event types (append-only audit log)
│       ├── ai/                  # Opus 4.6 integration
│       │   ├── __init__.py
│       │   ├── client.py        # Anthropic API client wrapper
│       │   ├── interpreter.py   # Rule proposal → structured rule (sandboxed)
│       │   ├── mirror.py        # Reflection generation (sim, gov, private)
│       │   ├── commentary.py    # Live game commentary generation
│       │   └── prompts.py       # Prompt templates (version-controlled)
│       ├── models/              # Pydantic models (shared vocabulary)
│       │   ├── __init__.py
│       │   ├── game.py          # GameResult, BoxScore, PlayByPlay
│       │   ├── team.py          # Team, Agent, AgentAttributes
│       │   ├── rules.py         # RuleSet, RuleChange, GameEffect (shared vocabulary)
│       │   ├── governance.py    # Proposal, Amendment, Vote, Rule
│       │   ├── tokens.py        # TokenBalance, Trade, TokenType
│       │   └── mirror.py        # Reflection, MirrorUpdate
│       └── db/                  # Database layer
│           ├── __init__.py
│           ├── engine.py        # Connection setup
│           └── repository.py    # Data access (repository pattern)
├── tests/
│   ├── __init__.py
│   ├── conftest.py              # Shared fixtures
│   ├── test_simulation.py
│   ├── test_governance.py
│   ├── test_tokens.py
│   ├── test_rules.py
│   ├── test_scheduler.py
│   ├── test_ai_interpreter.py
│   └── test_api/
│       ├── __init__.py
│       ├── test_games_api.py
│       ├── test_governance_api.py
│       └── test_teams_api.py
├── docs/
│   ├── TABLE_OF_CONTENTS.md     # Master index of all docs and plans (start here)
│   ├── GLOSSARY.md              # Canonical naming authority — terms, aliases, forbidden names
│   ├── INTERFACE_CONTRACTS.md   # SSE events, API endpoints, Pydantic model index, event store types
│   ├── DEMO_MODE.md             # Environment config, pace modes, demo script, feature flags
│   ├── VISION.md
│   ├── PRODUCT_OVERVIEW.md      # User journey, PM analysis, gap register, metrics matrix
│   ├── RUN_OF_PLAY.md
│   ├── PLAN.md
│   ├── SIMULATION.md
│   ├── GAME_LOOP.md             # Game loop & scheduler architecture
│   ├── PLAYER.md                # Player experience, Discord integration, governance UX
│   ├── VIEWER.md                # Viewer experience, Arena, AI commentary, API endpoints
│   ├── SECURITY.md              # Prompt injection defense plan
│   ├── INSTRUMENTATION.md
│   ├── ACCEPTANCE_CRITERIA.md   # Testable acceptance criteria with automation notes
│   ├── OPS.md                   # Operations, deployment (Fly.io), monitoring
│   ├── DEV_LOG.md               # Running log of decisions and work
│   ├── plans/                   # Feature plans (/workflows:plan output)
│   └── solutions/               # Documented solutions (/workflows:compound output)
├── pyproject.toml
├── fly.toml                     # Fly.io deployment config
├── Dockerfile                   # Multi-stage Docker build
├── CLAUDE.md                    # (this file)
└── README.md
```

## Development Workflow

We use the Compound Engineering plugin. Every non-trivial feature follows this cycle:

### Plan → Work → Review → Compound

1. **`/workflows:plan`** — Before writing code, research the codebase and create a detailed plan in `docs/plans/`. Plans reference existing patterns, identify affected files, and define acceptance criteria. The plan is the thinking; the code is the typing.
2. **`/workflows:work`** — Execute the plan with task tracking, incremental commits, and continuous testing. Follow existing patterns. Test as you go, not at the end. Ship complete features — don't leave things 80% done.
3. **`/workflows:review`** — Multi-agent code review before merging. Security, performance, architecture, simplicity. P1 findings block merge.
4. **`/workflows:compound`** — After solving a non-trivial problem, document it in `docs/solutions/`. Each documented solution makes future work easier. Knowledge compounds.

### Why this order matters

The ratio is ~80% planning and review, ~20% execution. This sounds slow but is faster in practice: well-planned work executes cleanly, and documented solutions prevent re-solving the same problems. Each unit of engineering work should make subsequent units easier — not harder.

### Session discipline — NON-NEGOTIABLE

Every work session must end with these three steps, in order:

1. **Tests pass.** Run `uv run pytest -x -q` and confirm green. Every new feature needs tests. Coverage should be as broad as logically possible — not just happy paths, but auth failures, empty states, edge cases. If you added code, you added tests for it.
2. **Dev log updated.** Update `docs/DEV_LOG.md` with what was asked, what was built, issues resolved, and the new test count. Update the "Today's Agenda" checkboxes. This is the project's memory — future sessions depend on it.
3. **Code committed.** Stage the specific files you changed and commit with a conventional commit message. Never leave passing code uncommitted. Never commit failing tests.

If you're unsure whether to commit, the answer is yes — commit the passing state. Uncommitted work is lost work.

### Incremental commits during work

Commit when you have a complete, valuable unit of change — not "WIP." If you can't write a commit message that describes a complete change, wait. Run tests before every commit. Stage specific files, not `git add .`.

### Keeping docs alive

- **`docs/DEV_LOG.md`** — Update after each session. Each entry follows the format: **What was asked**, **What was built**, **Issues resolved**, **test count + lint status**. When a session adds new features, update the "Today's Agenda" checkboxes and note the session number. The dev log is the project's memory — future sessions read it to understand where we are.
- **`scripts/run_demo.sh`** — When a feature adds a new page or route, add a corresponding demo step with a Rodney screenshot. Update the test count in the verification step. The demo script is the project's proof — it must reflect the current state of the application.
- **Design docs** (`SIMULATION.md`, `GAME_LOOP.md`, etc.) — When a design question is resolved, update the doc. Replace TODOs with decisions. Design docs should reflect the current state of the system, not the state when they were first written.
- **`CLAUDE.md`** — When a design decision is made that affects architecture, code standards, or project structure, capture it here. This file is the single source of truth for how we build.
- **Plan files** (`docs/plans/`) — Check off items as they're completed during `/workflows:work`. Plans are living documents, not write-once specs.

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

### Simulation engine is pure functions
`simulate_game(teams, rules, seed) → GameResult`. No side effects, no database access, no API calls. Input determines output. This makes testing trivial, the Rust port mechanical, and the system trustworthy.

### AI interpretation is sandboxed
Player-submitted text never enters the simulation engine's context. Opus 4.6 interprets proposals in an isolated context with strict system instructions. The structured output is validated against the rule space schema before it can affect the simulation. This is both a security boundary and a gameplay feature — the AI acts as a constitutional interpreter.

### Governance is append-only events
Every governance action (propose, amend, vote, trade, enact) is an immutable event. Token balances are derived from the event log, not stored as mutable state. This gives full auditability and makes the governance mirror's job straightforward — it reads the event log.

### Rules are parameterized, not arbitrary
The rule space is a defined set of parameters with types, ranges, and validation. Players propose changes in natural language, but what actually changes are typed parameters: `shot_clock_seconds: int (range: 10-60)`, `three_point_value: int (range: 1-10)`, etc. This prevents the simulation from entering undefined states.

## Resolved Design Questions

- [x] **Event sourcing + repository pattern:** The repository pattern wraps an event store. Governance events are the source of truth (append-only, immutable). The repository provides read projections derived from the event log — current token balances, current ruleset, standings, etc. `db/repository.py` reads from and appends to the event store; it never mutates past events. Game results are stored directly (not event-sourced) since they're already immutable outputs of a pure function.
- [x] **Instrumentation is a foundational principle:** Structured logging, middleware timing, and AI call tracking are built in from Day 1, not bolted on later. See the Instrumentation principle below and `docs/INSTRUMENTATION.md` for the full spec.
- [x] **RuleSet lives in `models/rules.py`:** RuleSet is a shared Pydantic model consumed by simulation, governance, AI, and the API. It lives in `models/rules.py` alongside the other shared types. `core/rules.py` contains the rule space definitions, parameter boundaries, validation logic, and rule change application functions — the business logic that operates on the model.

## Environment Variables

```
ANTHROPIC_API_KEY=         # Claude API key
DATABASE_URL=              # PostgreSQL connection string (or sqlite:///pinwheel.db)
PINWHEEL_ENV=development    # development | staging | production
PINWHEEL_GAME_CRON="0 * * * *"  # When games run (cron syntax, default: top of every hour)
PINWHEEL_GOV_WINDOW=900     # Seconds per governance window
```

## Common Commands

```bash
# Install dependencies
uv sync

# Run tests (DO THIS BEFORE EVERY COMMIT)
pytest

# Run tests with coverage
pytest --cov=pinwheel --cov-report=term-missing

# Format and lint
ruff format . && ruff check . --fix

# Start dev server
uvicorn pinwheel.main:app --reload

# Run a single simulation (for testing)
python -m pinwheel.core.simulation
```

## Demo Pipeline

[Showboat](https://github.com/simonw/showboat) (executable markdown demo builder) and [Rodney](https://github.com/simonw/rodney) (Chrome automation) produce a self-documenting demo artifact with screenshots proving the full govern→simulate→observe→reflect cycle works end-to-end. Both are Simon Willison tools, invoked via `uvx` (no install needed).

```bash
# Run the full demo (seeds league, starts server, captures 10 screenshots)
bash scripts/run_demo.sh
# Output: demo/pinwheel_demo.md — Markdown with embedded screenshots

# Manual seeding for local dev (no Showboat/Rodney needed)
python scripts/demo_seed.py seed            # Create 4 teams + round-robin schedule
python scripts/demo_seed.py step 3          # Advance 3 rounds (sim + gov + mirrors + evals)
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

A 15-step script: seed the league, start the server, capture each major page (home, arena, standings, game detail, mirrors, governance, rules, team profile, evals dashboard), then run the test suite as verification.

### demo_seed.py

Uses `step_round()` from the game loop directly, so all hooks (mirrors, evals, event bus) run automatically — no separate seeding needed for new features that integrate into the game loop.
