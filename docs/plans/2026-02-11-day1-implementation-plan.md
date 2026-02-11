---
title: "feat: Day 1 Implementation — The Engine"
type: feat
date: 2026-02-11
---

# Day 1 Implementation Plan

## Goal

By end of Day 1: `simulate_game(home, away, rules, seed)` works with the full defensive model, a round-robin produces standings, results are stored in SQLite, and a basic API serves game data. Agent generation uses Opus 4.6 with YAML override.

## Phase 1: Project Scaffolding (30 min)

### 1.1 pyproject.toml

```toml
[project]
name = "pinwheel"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "pydantic>=2.9",
    "pydantic-settings>=2.5",
    "sqlalchemy[asyncio]>=2.0",
    "aiosqlite>=0.20",        # SQLite async driver (local dev)
    "asyncpg>=0.30",          # Postgres async driver (production)
    "alembic>=1.14",
    "httpx>=0.27",            # Async HTTP client (for AI calls, testing)
    "anthropic>=0.42",        # Anthropic SDK
    "apscheduler>=3.10",      # Background scheduling
    "pyyaml>=6.0",            # League config files
    "sse-starlette>=2.1",     # SSE support for FastAPI
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "pytest-cov>=5.0",
    "ruff>=0.8",
    "httpx",                  # test client
]
discord = [
    "discord.py>=2.4",
]

[tool.ruff]
target-version = "py312"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

### 1.2 Directory Structure

Create the full directory tree from CLAUDE.md. Every `__init__.py` in place. Empty modules with docstrings describing their purpose.

```bash
mkdir -p src/pinwheel/{api,core,ai,models,db}
mkdir -p tests/test_api
mkdir -p templates/{components,pages,admin,errors}
mkdir -p static/{css,js,assets/fonts,assets/icons}
```

### 1.3 Config

```python
# src/pinwheel/config.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    anthropic_api_key: str = ""
    database_url: str = "sqlite+aiosqlite:///pinwheel.db"
    pinwheel_env: str = "development"
    pinwheel_game_cron: str = "*/2 * * * *"
    pinwheel_gov_window: int = 120
    pinwheel_log_level: str = "INFO"
    pinwheel_presentation_pace: str = "fast"

    model_config = {"env_prefix": "", "env_file": ".env"}
```

### 1.4 FastAPI App

```python
# src/pinwheel/main.py
from fastapi import FastAPI
from pinwheel.config import Settings

def create_app() -> FastAPI:
    settings = Settings()
    app = FastAPI(title="Pinwheel", version="0.1.0")
    app.state.settings = settings
    # Register routers, lifespan, middleware
    return app

app = create_app()
```

**Commit point:** `feat: project scaffolding with pyproject.toml and directory structure`

---

## Phase 2: Pydantic Models (1 hour)

Build models in dependency order. These are the shared vocabulary.

### 2.1 models/rules.py — RuleSet

The central model. All tier 1-4 parameters with types, ranges, defaults.

```python
class RuleSet(BaseModel):
    """The complete set of governable parameters."""
    # Tier 1: Game Mechanics
    quarter_possessions: int = Field(default=15, ge=5, le=30)
    possession_duration_seconds: int = Field(default=24, ge=10, le=60)
    shot_clock_seconds: int = Field(default=12, ge=10, le=60)
    three_point_value: int = Field(default=3, ge=1, le=10)
    two_point_value: int = Field(default=2, ge=1, le=10)
    free_throw_value: int = Field(default=1, ge=1, le=5)
    personal_foul_limit: int = Field(default=5, ge=3, le=10)
    team_foul_bonus_threshold: int = Field(default=4, ge=3, le=10)
    three_point_distance: float = Field(default=22.15, ge=15.0, le=30.0)
    elam_trigger_quarter: int = Field(default=3, ge=1, le=4)
    elam_margin: int = Field(default=13, ge=5, le=25)
    halftime_stamina_recovery: float = Field(default=0.25, ge=0.0, le=0.5)
    safety_cap_possessions: int = Field(default=200, ge=50, le=500)

    # Tier 2: Agent Behavior
    max_shot_share: float = Field(default=1.0, ge=0.2, le=1.0)
    min_pass_per_possession: int = Field(default=0, ge=0, le=5)
    max_minutes_share: float = Field(default=1.0, ge=0.5, le=1.0)
    home_court_enabled: bool = True
    home_crowd_boost: float = Field(default=0.05, ge=0.0, le=0.15)
    away_fatigue_factor: float = Field(default=0.02, ge=0.0, le=0.10)
    crowd_pressure: float = Field(default=0.03, ge=0.0, le=0.10)
    altitude_stamina_penalty: float = Field(default=0.01, ge=0.0, le=0.05)
    travel_fatigue_enabled: bool = True
    travel_fatigue_per_mile: float = Field(default=0.001, ge=0.0, le=0.005)
    # ...etc
```

### 2.2 models/team.py — Team, Agent, Attributes

```python
class PlayerAttributes(BaseModel):
    scoring: int = Field(ge=1, le=100)
    passing: int = Field(ge=1, le=100)
    defense: int = Field(ge=1, le=100)
    speed: int = Field(ge=1, le=100)
    stamina: int = Field(ge=1, le=100)
    iq: int = Field(ge=1, le=100)
    ego: int = Field(ge=1, le=100)
    chaotic_alignment: int = Field(ge=1, le=100)
    fate: int = Field(ge=1, le=100)

    def total(self) -> int:
        return sum(self.model_dump().values())

class Move(BaseModel):
    name: str
    trigger: str           # when can this move activate
    effect: str            # what it modifies
    attribute_gate: dict[str, int]  # minimum attributes required
    source: Literal["archetype", "earned", "governed"]

class Venue(BaseModel):
    name: str
    capacity: int = Field(ge=500, le=50000)
    altitude_ft: int = Field(ge=0, le=10000, default=0)
    surface: str = "hardwood"
    location: tuple[float, float]

class Agent(BaseModel):
    id: str
    name: str
    team_id: str
    archetype: str
    backstory: str
    attributes: PlayerAttributes
    moves: list[Move]
    is_starter: bool = True

class Team(BaseModel):
    id: str
    name: str
    color: str
    motto: str
    venue: Venue
    agents: list[Agent]
```

### 2.3 models/game.py — GameResult, BoxScore, PossessionLog

All the output types from SIMULATION.md. GameResult, AgentBoxScore, PossessionLog, QuarterScore.

### 2.4 models/governance.py — Proposal, Vote, GovernanceEvent

Event types and payload structures for the event store.

### 2.5 models/tokens.py — TokenBalance, Trade

### 2.6 models/mirror.py — Mirror, MirrorUpdate

**Commit point:** `feat: Pydantic models for all domain types`

---

## Phase 3: Simulation Engine (3-4 hours)

This is the core of Day 1. Build in this order:

### 3.1 core/state.py — Mutable Game State

`GameState`, `AgentState`, `PossessionState`. The mutable state that the simulation operates on. See simulation-extensibility-plan.md.

### 3.2 core/hooks.py — Hook System

`HookPoint` enum, `_fire_hooks()`, `GameEffect` protocol. Day 1: effects list is empty. Hooks are in the code for Day 2.

### 3.3 core/scoring.py — Shot Probability

Logistic curves for base probability, contest modifier, IQ modifier, stamina modifier, rule modifier. The math from SIMULATION.md "Scoring Resolution."

```python
def compute_shot_probability(
    shooter: AgentState,
    defender: AgentState,
    shot_type: ShotType,
    scheme: DefensiveScheme,
    rules: RuleSet,
) -> float:
    base = logistic(shooter.current_attributes.scoring, midpoint=..., steepness=...)
    contest = compute_contest(defender, shot_type, scheme)
    iq_mod = compute_iq_modifier(shooter.current_attributes.iq)
    stamina_mod = compute_stamina_modifier(shooter.current_stamina)
    return clamp(base * contest * iq_mod * stamina_mod, 0.01, 0.99)
```

**Test immediately:** Unit tests with known inputs → expected probability ranges. Verify probabilities stay in [0, 1]. Verify that high defense reduces probability. Verify stamina degradation effects.

### 3.4 core/defense.py — Full Defensive Model

All 4 schemes (man-tight, man-switch, zone, press). Scheme selection logic. Matchup assignment via cost function. This is the most complex subsystem.

```python
def select_scheme(
    offense: list[AgentState],
    defense: list[AgentState],
    game_state: GameState,
    rules: RuleSet,
    rng: random.Random,
) -> DefensiveScheme: ...

def assign_matchups(
    offense: list[AgentState],
    defense: list[AgentState],
    scheme: DefensiveScheme,
    game_state: GameState,
    rng: random.Random,
) -> dict[str, str]:  # defender_id → attacker_id
    ...
```

**Test:** Verify scheme selection responds to lineup attributes. Verify matchup assignment minimizes cost. Verify variance exists (not always optimal). Verify high-IQ teams make better selections.

### 3.5 core/moves.py — Move System

Move definitions, trigger checking, application. Day 1: 8 moves from SIMULATION.md, seeded from archetypes.

```python
def check_moves(
    agent: AgentState,
    action: Action,
    result: ActionResult,
    game_state: GameState,
) -> list[Move]: ...

def apply_move(
    move: Move,
    result: ActionResult,
    agent: AgentState,
    rng: random.Random,
) -> ActionResult: ...
```

### 3.6 core/possession.py — Possession Model

The full possession tree from SIMULATION.md. Action selection → resolution → scoring → rebounds → fouls → stamina drain.

```python
def resolve_possession(
    offense: list[AgentState],
    defense: list[AgentState],
    scheme: DefensiveScheme,
    matchups: dict[str, str],
    game_state: GameState,
    rules: RuleSet,
    rng: random.Random,
) -> PossessionResult: ...
```

**Test:** Run 1000 possessions with known teams. Verify scoring rates are basketball-like (~1.0-1.1 points per possession). Verify turnovers happen. Verify fouls happen. Verify moves trigger.

### 3.7 core/simulation.py — Top-Level simulate_game()

Ties it all together. 4 quarters, halftime, Elam Ending, game state management.

```python
def simulate_game(
    home: Team,
    away: Team,
    rules: RuleSet,
    seed: int,
    effects: list[GameEffect] | None = None,
) -> GameResult: ...
```

**Test with seeds:**
- Same inputs + same seed → identical output (determinism)
- Different seeds → different outcomes
- Run 100 games between two teams → verify win distribution is reasonable
- Verify rule changes affect outcomes (e.g., higher three_point_value → more points)
- Verify Elam Ending activates and games end on a made basket
- Verify venue modifiers affect results (home team advantage)

**Commit point:** `feat: simulation engine with full defensive model, moves, and Elam Ending`

---

## Phase 4: League Seeding (1.5 hours)

### 4.1 Archetype Templates

Hardcoded archetype definitions from SIMULATION.md. The 9 archetypes with their attribute distributions.

```python
ARCHETYPES: dict[str, PlayerAttributes] = {
    "sharpshooter": PlayerAttributes(scoring=80, passing=40, defense=25, ...),
    "floor_general": PlayerAttributes(scoring=45, passing=80, defense=30, ...),
    # ... all 9
}

ARCHETYPE_MOVES: dict[str, list[Move]] = {
    "sharpshooter": [HEAT_CHECK],
    "lockdown": [LOCKDOWN_STANCE],
    # ...
}
```

### 4.2 AI Agent Generation

Use Opus 4.6 to generate team and agent narratives. Output a YAML file that can be hand-edited.

```python
async def generate_league(
    num_teams: int = 8,
    agents_per_team: int = 4,
    archetypes: list[str] | None = None,
) -> LeagueConfig:
    """Generate a full league using Opus 4.6 for narrative, archetypes for attributes."""

    prompt = """Generate {num_teams} basketball teams for a 3v3 league called Pinwheel.
    Each team has {agents_per_team} players (3 starters + 1 bench).

    For each team, provide:
    - Team name (creative, Portland-inspired)
    - Team color (hex)
    - Team motto
    - Venue (name, capacity, altitude, surface, lat/lon in Portland area)

    For each player, provide:
    - Full name with optional nickname
    - Archetype (assigned from: {archetypes})
    - Backstory (2-3 sentences, personality-driven)
    - Rivalries (name 1-2 other players they have tension with)

    Make the teams balanced but distinct. Make the names fun and the backstories brief.
    The vibe is Blaseball — slightly unhinged, deeply human, community-first.

    Output as YAML matching this schema: ...
    """

    # Call Opus 4.6
    response = await ai_client.generate(prompt, response_format="yaml")

    # Parse into LeagueConfig
    config = parse_league_yaml(response)

    # Apply archetype attributes + variance
    for team in config.teams:
        for player in team.players:
            base = ARCHETYPES[player.archetype]
            player.attributes = apply_variance(base, rng, variance=10)
            player.moves = ARCHETYPE_MOVES[player.archetype]

    return config
```

### 4.3 YAML Config Loading

Parse hand-authored or AI-generated YAML into `LeagueConfig`. Validate against Pydantic models.

### 4.4 Seed CLI

```bash
# AI-generate a league, output to YAML for editing
python -m pinwheel.seed --generate --output league.yaml

# Edit league.yaml by hand if desired...

# Seed from YAML into database
python -m pinwheel.seed --config league.yaml
```

**Test:** Verify YAML round-trips (generate → write → read → identical models). Verify attribute budgets are validated. Verify all archetypes produce valid agents.

**Commit point:** `feat: league seeding with AI generation and YAML config`

---

## Phase 5: Database Layer (1 hour)

### 5.1 SQLAlchemy Models

`db/models.py` — ORM table definitions from the database schema plan.

### 5.2 Engine Setup

```python
# db/engine.py
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

async def get_engine(database_url: str):
    return create_async_engine(database_url, echo=False)

async def get_session(engine):
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
```

### 5.3 Repository

`db/repository.py` — Core read/write operations. Focus on what Day 1 needs:
- Store/retrieve teams and agents
- Store/retrieve game results with box scores
- Store/retrieve schedule
- Get current ruleset

Governance event store methods can be stubbed for Day 2.

### 5.4 Initial Alembic Migration

```bash
alembic init db/migrations
alembic revision --autogenerate -m "initial schema"
alembic upgrade head
```

**Test:** Verify round-trip: create team → store → retrieve → identical. Same for game results.

**Commit point:** `feat: database layer with SQLAlchemy 2.0 and initial migration`

---

## Phase 6: Scheduler + Basic API (1 hour)

### 6.1 core/scheduler.py — Round-Robin Generation

```python
def generate_round_robin(teams: list[Team], num_round_robins: int = 3) -> list[RoundMatchup]:
    """Generate a full round-robin schedule for N teams."""
    # Standard round-robin algorithm (rotate all but one team)
    ...
```

**Test:** 8 teams → 7 rounds per RR. Every team plays every other team once. 4 games per round. Home/away alternates.

### 6.2 Basic API Endpoints

Minimal endpoints to verify the system works:

```python
# api/games.py
GET /api/games/{game_id}          → GameResult
GET /api/games/{game_id}/boxscore → list[AgentBoxScore]

# api/teams.py
GET /api/teams                    → list[Team]
GET /api/teams/{team_id}          → Team

# api/standings
GET /api/standings                → Standings

# api/health
GET /health                       → {"status": "ok", ...}
```

### 6.3 End-to-End Test

The Day 1 validation test:

```python
async def test_full_round_robin():
    """Seed a league, run a round-robin, verify standings."""
    # 1. Seed 8 teams from config
    league = await seed_from_yaml("test_league.yaml")

    # 2. Generate schedule
    schedule = generate_round_robin(league.teams, num_round_robins=1)

    # 3. Simulate all 28 games (7 rounds × 4 games)
    for round_num in range(1, 8):
        matchups = schedule.get_round(round_num)
        for matchup in matchups:
            result = simulate_game(
                home=matchup.home_team,
                away=matchup.away_team,
                rules=DEFAULT_RULESET,
                seed=compute_seed(league.id, round_num, matchup.index),
            )
            await repository.store_game_result(result)

    # 4. Compute standings
    standings = await repository.get_standings(league.season_id)

    # 5. Verify
    assert len(standings) == 8
    assert sum(s.wins for s in standings) == sum(s.losses for s in standings)
    assert all(s.games_played == 7 for s in standings)
    # Win distribution should be reasonable (no team 0-7 or 7-0 with balanced rosters)
```

**Commit point:** `feat: scheduler, basic API, and end-to-end round-robin test`

---

## Phase 7: Run and Observe (30 min)

### 7.1 Run 1000 Games

```python
# Quick validation script
results = []
for seed in range(1000):
    result = simulate_game(home_team, away_team, DEFAULT_RULESET, seed)
    results.append(result)

# Check distributions
avg_score = mean(r.home_score + r.away_score for r in results)
avg_possessions = mean(r.total_possessions for r in results)
home_win_pct = mean(1 for r in results if r.winner == home_team) / 1000

print(f"Avg total score: {avg_score}")       # Should be ~80-120
print(f"Avg possessions: {avg_possessions}")  # Should be ~60-80
print(f"Home win %: {home_win_pct}")          # Should be ~52-58%
```

### 7.2 Tune if Necessary

If scoring is too high/low, adjust logistic curve midpoints. If home advantage is too strong, reduce venue modifier defaults. This is the "run 1000 games and check" step from SIMULATION.md.

**Commit point:** `fix: tuning simulation parameters from 1000-game batch`

---

## Day 1 Deliverable

> You can hit an API and get box scores from auto-simulated 3v3 basketball games with full defensive schemes, agent moves, venue modifiers, and Elam Endings. Agent narratives are AI-generated. The simulation is deterministic, tested, and ready for governance on Day 2.

## File Inventory (End of Day 1)

```
src/pinwheel/
├── __init__.py
├── main.py                    # FastAPI app factory
├── config.py                  # Settings
├── api/
│   ├── __init__.py
│   ├── router.py              # Top-level router
│   ├── games.py               # GET /games, /games/{id}, /games/{id}/boxscore
│   ├── teams.py               # GET /teams, /teams/{id}
│   └── health.py              # GET /health
├── core/
│   ├── __init__.py
│   ├── simulation.py          # simulate_game() top-level
│   ├── possession.py          # Possession model
│   ├── defense.py             # Scheme selection, matchup assignment
│   ├── scoring.py             # Shot probability, scoring resolution
│   ├── hooks.py               # HookPoint, _fire_hooks, GameEffect protocol
│   ├── moves.py               # Move definitions, trigger/apply
│   ├── state.py               # GameState, AgentState, PossessionState
│   ├── rules.py               # Rule space definitions, validation
│   ├── scheduler.py           # Round-robin generation
│   ├── archetypes.py          # 9 archetype templates + move assignments
│   └── standings.py           # Standings computation
├── ai/
│   ├── __init__.py
│   ├── client.py              # Anthropic API client wrapper
│   └── generator.py           # AI league generation
├── models/
│   ├── __init__.py
│   ├── rules.py               # RuleSet
│   ├── game.py                # GameResult, BoxScore, PossessionLog
│   ├── team.py                # Team, Agent, Attributes, Venue, Move
│   ├── governance.py          # (stub for Day 2)
│   ├── tokens.py              # (stub for Day 2)
│   └── mirror.py              # (stub for Day 2)
├── db/
│   ├── __init__.py
│   ├── engine.py              # Async engine setup
│   ├── models.py              # SQLAlchemy ORM models
│   └── repository.py          # Data access layer
└── seed.py                    # CLI seeding tool

tests/
├── __init__.py
├── conftest.py                # Fixtures: teams, agents, rules, db session
├── test_simulation.py         # Determinism, scoring, defensive model
├── test_scoring.py            # Shot probability math
├── test_defense.py            # Scheme selection, matchup assignment
├── test_moves.py              # Move triggers, applications
├── test_scheduler.py          # Round-robin generation
├── test_standings.py          # Standings computation
├── test_seeding.py            # YAML parsing, attribute validation
└── test_api/
    ├── __init__.py
    └── test_games_api.py      # API integration tests
```

## Test Count Target

~40-60 tests by end of Day 1. The simulation engine alone should have 20+ tests covering:
- Determinism (same seed → same result)
- Scoring probability bounds
- Each defensive scheme's effects
- Matchup assignment quality
- Move triggers and applications
- Elam Ending activation and resolution
- Venue modifier application
- Stamina degradation
- Foul tracking and ejection
- Quarter structure and halftime
