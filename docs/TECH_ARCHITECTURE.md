# Tech Architecture

Reference documentation for all technical systems in Pinwheel Fates.

**Last updated:** 2026-02-15

---

## System Overview

Pinwheel Fates is a single-process Python application that combines four subsystems:

```
                     +----------------+
                     |    FastAPI     |
                     | (HTTP + SSE)   |
                     +-------+--------+
                             |
          +------------------+------------------+
          |                  |                  |
  +-------+------+   +------+------+   +-------+------+
  | discord.py   |   |  APScheduler |   |   Claude AI  |
  | (in-process) |   | (cron ticks) |   | (Anthropic)  |
  +-------+------+   +------+------+   +-------+------+
          |                  |                  |
          +------------------+------------------+
                             |
                     +-------+--------+
                     |    SQLite      |
                     | (aiosqlite)    |
                     +----------------+
```

- **FastAPI** serves REST API endpoints, server-rendered HTML pages (Jinja2), and SSE event streams
- **discord.py** runs in-process on FastAPI's event loop, providing 15 slash commands and real-time channel updates
- **APScheduler** triggers `tick_round()` on a configurable cron schedule to advance the game automatically
- **Claude AI** (via Anthropic API) interprets proposals, generates reports, produces commentary, classifies injections, and evaluates rules
- **SQLite** (via SQLAlchemy 2.0 async + aiosqlite) stores all persistent state

All four subsystems share a single Python process, a single event loop, and a single database connection pool. There is no message queue, no Redis, no external cache.

---

## Request Lifecycle

### HTTP Request Flow

```
Browser/Client
    |
    v
FastAPI Route Handler  (src/pinwheel/api/*.py)
    |  - Thin handler: extracts deps, delegates, returns
    |  - Uses RepoDep for database access
    |  - Uses OptionalUser for auth context
    v
Core Domain Logic  (src/pinwheel/core/*.py)
    |  - Business rules, governance, simulation
    |  - Pure functions where possible
    v
Repository  (src/pinwheel/db/repository.py)
    |  - All database queries in one class
    |  - Never imported by route handlers directly (via DI)
    v
SQLAlchemy AsyncSession  (src/pinwheel/db/engine.py)
    |  - Auto-commit on success, rollback on error
    v
SQLite (WAL mode, 15s busy timeout)
```

### Dependency Injection Chain

```python
# In api/deps.py:
get_engine(request) -> AsyncEngine       # from app.state
get_session(engine) -> AsyncSession      # auto-commit context manager
get_repo(session)   -> Repository        # data access wrapper
RepoDep = Annotated[Repository, Depends(get_repo)]
```

Every route handler receives a `RepoDep` which provides a `Repository` bound to a fresh `AsyncSession`. The session auto-commits when the handler returns successfully, or rolls back on exception.

---

## Simulation Engine

**Source:** `src/pinwheel/core/simulation.py`, `possession.py`, `scoring.py`, `defense.py`, `moves.py`, `state.py`

The simulation is a **pure function**: `simulate_game(home, away, rules, seed) -> GameResult`. It has no side effects, no database access, no API calls. Given the same inputs and seed, it produces identical output.

### Pipeline

```
simulate_game()
  |
  +-> _build_hooper_states()     # Mutable state for each player
  |
  +-> for q in range(1, elam_trigger_quarter + 1):
  |     _run_quarter(q)          # Timed quarter with clock
  |     _check_substitution()    # Fatigue/foul-out subs
  |     stamina recovery         # Quarter break or halftime
  |
  +-> _run_elam()                # Target-score period (no clock)
  |
  +-> GameResult                 # Scores, play-by-play, box scores
```

### Possession Resolution

Each possession flows through:
1. **Action selection** -- weighted choice based on hooper attributes and team strategy
2. **Defensive matchup** -- defender assignment based on defensive scheme
3. **Shot resolution** -- logistic probability curve modified by defense, IQ, stamina
4. **Foul check** -- based on defensive intensity and aggression
5. **Rebound** -- on miss, offensive vs defensive rebound chance
6. **Turnover/steal** -- based on ball handler's passing vs defender's speed
7. **Assist attribution** -- based on team passing tendencies

### Determinism

The simulation seeds a `random.Random` instance. Any game can be exactly replayed by providing the same seed. Seeds are stored in `GameResultRow` for audit and debugging.

---

## AI Pipeline

All AI calls go through the `src/pinwheel/ai/` module. Route handlers and core logic never call the Anthropic API directly.

### Five AI Subsystems

| System | Module | Model | Purpose | When Called |
|--------|--------|-------|---------|------------|
| Interpreter | `ai/interpreter.py` | Sonnet | Proposal text -> structured `RuleInterpretation` | On `/propose` or `POST /api/governance/proposals` |
| Classifier | `ai/classifier.py` | Haiku | Pre-flight injection detection | Before interpretation, on every proposal |
| Reporter | `ai/report.py` | Sonnet | Simulation, governance, and private reports | After each round (in `step_round`) |
| Commentary | `ai/commentary.py` | Sonnet | Per-game commentary and highlight reels | After each game simulation |
| Rule Evaluator | `evals/rule_evaluator.py` | Opus | Admin-facing rule analysis | After each round (in evals) |

### Context Provided to Claude

**Interpreter:** Receives only the proposal text and a generated parameter description (names, types, ranges, current values). The interpreter is sandboxed -- it sees nothing about game state, other players, or system internals.

**Reporter:** Receives game results, standings, governance outcomes, team/hooper names, rule changes, and (for private reports) individual governor activity. Reports follow a strict **describe-don't-prescribe** constraint -- they never contain directive language (measured by the S.2c eval).

**Commentary:** Receives game result, box scores, play-by-play, team names, and playoff context. The prompt instructs an "energetic, dramatic, slightly absurd sports broadcaster" voice.

### Mock Fallbacks

Every AI subsystem has a corresponding mock function (`interpret_proposal_mock`, `generate_simulation_report_mock`, `generate_game_commentary_mock`, etc.) that runs when `ANTHROPIC_API_KEY` is not set. Mocks return structurally valid output with placeholder content, allowing the full system to run without API access.

### Usage Tracking

All AI calls are tracked via `ai/usage.py::record_ai_usage()` which logs to the `ai_usage_log` table: model, call type, token counts (input/output/cache read), latency, and estimated cost. The `/admin/costs` dashboard surfaces this data.

---

## Event Bus

**Source:** `src/pinwheel/core/event_bus.py`

The EventBus is an in-memory async pub/sub system using `asyncio.Queue`. It is the communication backbone connecting the game loop, presenter, SSE endpoints, and Discord bot.

### Architecture

```
Publishers                    EventBus                    Subscribers
+-----------+                +--------+                  +-----------+
| game_loop |---publish()--->|        |---queue.put()--->| SSE /stream|
| presenter |---publish()--->|  typed |---queue.put()--->| Discord bot|
| season.py |---publish()--->|  subs  |                  +-----------+
+----------+                 |        |
                             |wildcard|---queue.put()--->| Discord bot|
                             |  subs  |                  | (all events)|
                             +--------+
```

### Key Properties

- **Fire-and-forget:** If no subscribers are listening, events are silently dropped
- **Typed subscriptions:** Subscribe to a specific event type (e.g., `"game.completed"`)
- **Wildcard subscriptions:** Subscribe to all events (Discord bot uses this)
- **Backpressure:** Each queue has a `max_size` (default 100). Slow subscribers get events dropped with a warning
- **No persistence:** Events are transient signals, not stored. Persistent state lives in the governance event store
- **In-process only:** No Redis, no external pub/sub. Acceptable for single-process deployment

### Usage Pattern

```python
# Publisher (game loop):
await bus.publish("game.completed", {"game_id": "g-1", "winner": "team-a"})

# Subscriber (SSE endpoint):
async with bus.subscribe("game.completed") as sub:
    async for event in sub:
        yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
```

---

## Scheduler System

**Source:** `src/pinwheel/core/scheduler_runner.py`

APScheduler (`AsyncIOScheduler`) drives automatic round advancement on a configurable cron schedule.

### Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `PINWHEEL_PRESENTATION_PACE` | `slow` | Determines cron: fast=1min, normal=5min, slow=15min, manual=disabled |
| `PINWHEEL_GAME_CRON` | derived | Explicit cron override (takes priority over pace) |
| `PINWHEEL_AUTO_ADVANCE` | `true` | Master toggle for the scheduler |

### tick_round() Flow

`tick_round()` is the scheduler's entry point. It runs as a single invocation per cron tick:

```
tick_round()
  |
  +-> Skip if presentation still active
  +-> Acquire distributed lock (BotStateRow)
  |
  +-> Pre-flight session:
  |     - Find active season
  |     - Handle championship/offseason/completed phases (governance-only)
  |     - Determine next round number
  |
  +-> step_round_multisession()     # Main round execution
  |     - Simulation phase (own session)
  |     - AI phase (own session -- reports, commentary)
  |     - Governance phase (own session)
  |     - Eval phase (own session)
  |
  +-> Post-round:
  |     - Instant mode: mark games presented, publish events
  |     - Replay mode: launch background presentation task
  |
  +-> Release distributed lock
```

### Multi-Session Architecture

`step_round_multisession()` breaks the round into separate database sessions to minimize SQLite write lock contention. Each phase opens its own session, commits, and closes before the next phase begins. This prevents the 5+ second transactions that caused "database is locked" errors during Discord command processing.

### Distributed Lock

A database-level lock (`BotStateRow` with key `tick_round_lock`) prevents multiple Fly.io machines from executing `tick_round` simultaneously. The lock includes a machine ID and timestamp. Stale locks (>300 seconds) are automatically recovered.

### Season Phase Handling

`tick_round()` handles non-active season phases:

| Phase | Behavior |
|-------|----------|
| `championship` | Check if window expired, then transition to offseason |
| `offseason` | Tally governance on each tick, close when window expires |
| `completed` / `complete` | Governance-only ticks (no simulation) |
| `active` / `setup` | Normal round execution |

---

## Database Layer

**Source:** `src/pinwheel/db/engine.py`, `db/models.py`, `db/repository.py`

### Engine Configuration

SQLite with WAL journal mode, NORMAL synchronous, and 15-second busy timeout. These pragmas are set on every new connection via SQLAlchemy event listener.

```python
PRAGMA journal_mode=WAL
PRAGMA synchronous=NORMAL
PRAGMA busy_timeout=15000
```

### Schema (14 Tables)

| Table | ORM Class | Purpose |
|-------|-----------|---------|
| `leagues` | `LeagueRow` | Top-level league container |
| `seasons` | `SeasonRow` | Season within a league (8 phases, rulesets, config JSON) |
| `teams` | `TeamRow` | Team belonging to a season (name, colors, motto, venue JSON) |
| `hoopers` | `HooperRow` | Hooper belonging to team+season (attributes JSON, moves JSON, backstory) |
| `game_results` | `GameResultRow` | Per-game result (scores, seed, ruleset snapshot, play-by-play JSON) |
| `box_scores` | `BoxScoreRow` | Per-hooper-per-game stats (full stat line) |
| `governance_events` | `GovernanceEventRow` | Append-only event store (source of truth for governance) |
| `reports` | `ReportRow` | AI-generated reports (content as Text) |
| `players` | `PlayerRow` | Discord-authenticated player identity |
| `schedule` | `ScheduleRow` | Round-robin and playoff schedule entries |
| `bot_state` | `BotStateRow` | Key-value store for Discord bot state and locks |
| `season_archives` | `SeasonArchiveRow` | Frozen snapshot of completed seasons |
| `eval_results` | `EvalResultRow` | Eval results (never contains private report content) |
| `ai_usage_log` | `AIUsageLogRow` | AI API call tracking (tokens, cost, latency) |

### Repository Pattern

`Repository` is the single point of database access. It wraps an `AsyncSession` and provides named methods for every query the application needs. Domain logic (`core/`) and route handlers (`api/`) never import SQLAlchemy or write raw queries.

### Auto-Migration

At startup, `auto_migrate_schema()` compares ORM models against the live SQLite schema:

1. `PRAGMA table_info()` to get existing columns
2. Compare against model column definitions
3. For missing columns that are nullable or have a scalar default: `ALTER TABLE ADD COLUMN`
4. For missing NOT NULL columns without a default: log a warning and skip (unsafe)

This handles additive schema changes without Alembic. Destructive changes (renames, type changes, drops) require manual `ALTER TABLE` scripts tested against a copy of production data.

---

## Discord Integration

**Source:** `src/pinwheel/discord/bot.py`, `views.py`, `embeds.py`, `helpers.py`

### In-Process Bot

The Discord bot runs inside FastAPI's event loop via `start_discord_bot()` called during app lifespan startup. It shares the same database engine, EventBus, and settings. No separate process or message queue.

### 15 Slash Commands

| Command | Purpose |
|---------|---------|
| `/join` | Enroll on a team as a governor |
| `/propose` | Submit a rule change proposal |
| `/vote` | Vote on a proposal |
| `/tokens` | Check Floor token balance |
| `/trade` | Offer a token trade |
| `/trade-hooper` | Propose trading hoopers |
| `/strategy` | Set team strategic direction |
| `/bio` | Write hooper backstory |
| `/standings` | View league standings |
| `/schedule` | View upcoming schedule |
| `/reports` | View latest AI reports |
| `/profile` | View governor profile |
| `/proposals` | View all proposals |
| `/roster` | View enrolled governors |
| `/new-season` | Start a new season (admin only) |

### Interactive Views

| View | Purpose | Timeout |
|------|---------|---------|
| `ProposalConfirmView` | Confirm/Revise/Cancel for proposals | 300s |
| `ReviseProposalModal` | Text input for revising proposal | -- |
| `TradeOfferView` | Accept/Reject token trades | 1 hour |
| `StrategyConfirmView` | Confirm/Cancel strategy | 300s |
| `HooperTradeView` | Approve/Reject hooper trades | 1 hour |
| `AdminReviewView` | Clear/Veto wild proposals (DM to admin) | 24 hours |
| `AdminVetoReasonModal` | Text input for veto reason | -- |

### Event Bus Listener

The bot subscribes to ALL events (wildcard subscription) and routes them to appropriate Discord channels:

| Event | Channel | Behavior |
|-------|---------|----------|
| `presentation.game_finished` | play-by-play, big-plays, team channels | Game result embed; big-plays for blowouts/buzzer-beaters |
| `presentation.round_finished` | play-by-play | Round summary embed |
| `report.generated` (private) | DM to governor | Private report via direct message |
| `report.generated` (public) | play-by-play | Public report embed |
| `governance.window_closed` | main, team channels | "The Floor Has Spoken" + vote tally embeds |
| `season.championship_started` | main, team channels | Championship ceremony with awards |
| `season.phase_changed` | main | Phase transition announcement |

### Server Setup

On startup, `_setup_server()` creates:
- "PINWHEEL FATES" category
- Shared channels: `how-to-play`, `play-by-play`, `big-plays`
- Per-team channels and roles with proper permission overwrites
- Channel IDs persisted in `bot_state` for cross-restart stability
- `_sync_role_enrollments()` self-heals role assignments on restart

---

## Effects System

**Source:** `src/pinwheel/core/hooks.py`, `core/effects.py`, `core/meta.py`

The effects system has a dual architecture:

### Legacy Hook System

- `HookPoint` enum with 11 values (PRE_POSSESSION through GAME_END)
- `GameEffect` protocol with `should_fire()` and `apply()` methods
- `fire_hooks()` iterates a list of effects at each hook point

### New Effects System

- String-based hierarchical hooks (e.g., `sim.possession.pre`, `sim.elam.start`, `sim.game.end`)
- `HookContext` dataclass providing game state, rules, RNG, and MetaStore
- `HookResult` dataclass for structured mutations (shot probability modifiers, narrative injections, etc.)
- `fire_effects()` evaluates registered effects against hook points
- `apply_hook_results()` applies returned mutations to the context
- `EffectRegistry` for loading, querying, and expiring effects
- `MetaStore` for in-memory read/write cache of `meta` JSON columns on database rows

Effects are persisted as governance events (`effect.registered`, `effect.expired`) and loaded from the event store at round start via `load_effect_registry()`.

---

## Presenter System

**Source:** `src/pinwheel/core/presenter.py`, `core/narrate.py`

The presenter decouples instant simulation from real-time replay. Simulations run immediately (CPU-bound, deterministic). The presenter replays stored results over wall-clock time so players experience games "live."

### Two Modes

| Mode | Setting | Behavior |
|------|---------|----------|
| `instant` | `PINWHEEL_PRESENTATION_MODE=instant` | Games appear immediately after simulation |
| `replay` | `PINWHEEL_PRESENTATION_MODE=replay` | Games replay over `quarter_replay_seconds` per quarter |

### Replay Flow

1. `present_round()` takes all `GameResult` objects for the round
2. Replays all games concurrently via `asyncio.gather`
3. Each game's play-by-play events are divided into quarters
4. Each quarter replays over `quarter_replay_seconds` (default 300s = 5 min)
5. Inter-event delay = remaining quarter time / remaining events
6. SSE events emitted: `presentation.game_starting`, `presentation.possession`, `presentation.game_finished`, `presentation.round_finished`
7. After each game finishes, a callback marks it as "presented" in the database

### Deploy Recovery

On startup, `resume_presentation()` checks for an interrupted presentation:
1. Reads `presentation_active` key from `BotStateRow`
2. If found: calculates elapsed time, computes `skip_quarters`
3. Reconstructs `GameResult` objects from database rows
4. Launches `present_round()` with `skip_quarters` to fast-forward

This prevents duplicate or missed game presentations on deploy.

---

## Round Orchestration

**Source:** `src/pinwheel/core/game_loop.py`

`step_round()` (and its multi-session variant `step_round_multisession()`) is the core round executor.

### Phase Sequence

```
1. Load season + ruleset
2. Load schedule for this round
3. Load teams (with hoopers) into teams_cache
4. Load team strategies from governance events
5. Load effect registry for this season
6. Simulate each game (pure function)
7. Store game results and box scores
8. Generate per-game commentary (AI)
9. Generate highlight reel (AI)
10. Tally governance (if round % interval == 0)
11. Regenerate tokens
12. Generate reports: simulation, governance, private (AI)
13. Run evals (non-blocking)
14. Check season completion -> playoff progression
15. Publish round.completed event
16. Return RoundResult
```

### Domain Model Loading

`_row_to_team()` converts database rows (TeamRow + HooperRows) into domain models (Team + Hooper). It deserializes:
- `attributes` JSON into `PlayerAttributes`
- `moves` JSON into `Move` objects
- `venue` JSON into `Venue`

---

## Eval Framework

**Source:** `src/pinwheel/evals/`

The eval framework measures report quality and governance health. It runs after each round (when `PINWHEEL_EVALS_ENABLED=true`) and stores results in `EvalResultRow`. **No individual report text is ever stored in eval results.**

### S-Series (Per-Round, Automated)

| Eval | Module | What It Measures |
|------|--------|------------------|
| S.1 | `rubric.py` | Manual scoring of PUBLIC reports. 5 dimensions, 1-5 scale |
| S.2a | `behavioral.py` | Governance action shift detection. Never reads report content |
| S.2b | `grounding.py` | Entity reference validation. Content never stored |
| S.2c | `prescriptive.py` | Directive language scan. 12 regex patterns. Returns count only |
| M.6 | `flags.py` | Scenario flagging: blowout, unanimity, stagnation, collapse, backfire |

### M-Series (Periodic, Deeper)

| Eval | Module | What It Measures |
|------|--------|------------------|
| M.1 | `golden.py` | 20 test cases (8 sim, 7 gov, 5 private) |
| M.2 | `ab_compare.py` | Dual-prompt comparison. Variant A vs B |
| M.3 | `attribution.py` | Treatment/control report delivery. Aggregate delta only |
| M.4 | `gqi.py` | Governance Quality Index. 4 sub-metrics weighted 25% each |
| M.7 | `rule_evaluator.py` | Opus-powered admin analysis. Prescriptive (unlike reports) |

---

## Cross-References

| Document | Content |
|----------|---------|
| `docs/API_ARCHITECTURE.md` | All API endpoints, pages, SSE, auth |
| `docs/GOVERNANCE_EVENTS.md` | Event store schema and all event types |
| `docs/ELAM_ENDING.md` | Elam Ending game mechanics |
| `docs/SIMULATION.md` | Simulation engine details |
| `docs/GAME_LOOP.md` | Game loop documentation |
| `docs/INSTRUMENTATION.md` | Observability spec |
| `docs/product/RUN_OF_PLAY.md` | Game rules from the player perspective |
| `CLAUDE.md` | Developer workflow and code standards |
