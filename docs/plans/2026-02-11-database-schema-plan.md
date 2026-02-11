---
title: "feat: Database Schema & Event Store"
type: feat
date: 2026-02-11
---

# Database Schema & Event Store

## Overview

SQLAlchemy 2.0 with async support (asyncpg for Postgres, aiosqlite for SQLite). Alembic for migrations. The governance layer is event-sourced; everything else is stored directly.

## Decision: Event Sourcing + Read Projections

The governance event log is the source of truth. `db/repository.py` provides read projections:

```
WRITE PATH:
  Governor action → Service layer → Append GovernanceEvent to event store

READ PATH:
  API request → Repository → Read projection (materialized from events)

  Current token balances = SUM(token events) for player
  Current ruleset = APPLY(rule change events) in order
  Proposal status = LATEST(proposal events) for proposal_id
  Vote tally = COUNT(vote events) for proposal_id
```

Game results, teams, agents, and mirrors are stored directly — they're already immutable outputs (games are pure function results, mirrors are point-in-time snapshots).

## Schema

### Core Tables

```sql
-- The event store. Append-only. Never UPDATE, never DELETE.
CREATE TABLE governance_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type VARCHAR(50) NOT NULL,  -- proposal.submitted, vote.cast, rule.enacted, etc.
    aggregate_id VARCHAR(100) NOT NULL,  -- proposal_id, trade_id, etc.
    aggregate_type VARCHAR(50) NOT NULL,  -- proposal, trade, rule_change, token
    season_id UUID NOT NULL REFERENCES seasons(id),
    round_number INTEGER,
    governance_window_id UUID,
    governor_id UUID REFERENCES governors(id),
    team_id UUID REFERENCES teams(id),
    payload JSONB NOT NULL,  -- event-specific data (proposal text, vote value, etc.)
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- For ordering within a window
    sequence_number BIGSERIAL
);
CREATE INDEX idx_gov_events_aggregate ON governance_events(aggregate_type, aggregate_id);
CREATE INDEX idx_gov_events_season_round ON governance_events(season_id, round_number);
CREATE INDEX idx_gov_events_type ON governance_events(event_type);
CREATE INDEX idx_gov_events_governor ON governance_events(governor_id);

-- League (persistent, top-level)
CREATE TABLE leagues (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Season (time-bounded)
CREATE TABLE seasons (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    league_id UUID NOT NULL REFERENCES leagues(id),
    name VARCHAR(100) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'setup',  -- setup, active, playoffs, complete
    starting_ruleset JSONB NOT NULL,  -- RuleSet snapshot at season start
    current_ruleset JSONB NOT NULL,   -- Current RuleSet (read projection, updated on rule.enacted events)
    config JSONB NOT NULL,            -- GovernanceConfig, ScheduleConfig, attribute_budget, etc.
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

-- Teams
CREATE TABLE teams (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    season_id UUID NOT NULL REFERENCES seasons(id),
    name VARCHAR(100) NOT NULL,
    color VARCHAR(7),
    motto TEXT,
    venue JSONB NOT NULL,  -- Venue model as JSON
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Agents (players in the simulation)
CREATE TABLE agents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id UUID NOT NULL REFERENCES teams(id),
    season_id UUID NOT NULL REFERENCES seasons(id),
    name VARCHAR(100) NOT NULL,
    archetype VARCHAR(30) NOT NULL,
    backstory TEXT,
    attributes JSONB NOT NULL,  -- PlayerAttributes as JSON
    moves JSONB NOT NULL DEFAULT '[]',  -- list of Move objects
    is_active BOOLEAN NOT NULL DEFAULT true,  -- on floor vs bench
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_agents_team ON agents(team_id);

-- Governors (human players)
CREATE TABLE governors (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    discord_user_id VARCHAR(20) UNIQUE NOT NULL,
    discord_username VARCHAR(100) NOT NULL,
    team_id UUID REFERENCES teams(id),
    season_id UUID REFERENCES seasons(id),
    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    left_at TIMESTAMPTZ,
    is_active BOOLEAN NOT NULL DEFAULT true
);
CREATE INDEX idx_governors_discord ON governors(discord_user_id);
CREATE INDEX idx_governors_team ON governors(team_id);
```

### Game Results (stored directly, not event-sourced)

```sql
CREATE TABLE game_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    season_id UUID NOT NULL REFERENCES seasons(id),
    round_number INTEGER NOT NULL,
    matchup_index INTEGER NOT NULL,
    home_team_id UUID NOT NULL REFERENCES teams(id),
    away_team_id UUID NOT NULL REFERENCES teams(id),
    home_score INTEGER NOT NULL,
    away_score INTEGER NOT NULL,
    winner_team_id UUID NOT NULL REFERENCES teams(id),
    seed BIGINT NOT NULL,
    ruleset_snapshot JSONB NOT NULL,  -- rules at game time
    quarter_scores JSONB NOT NULL,
    elam_target INTEGER,
    elam_possessions INTEGER,
    total_possessions INTEGER NOT NULL,
    lead_changes INTEGER NOT NULL,
    largest_lead INTEGER NOT NULL,
    metadata JSONB,
    play_by_play JSONB NOT NULL,  -- compressed possession log
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_games_season_round ON game_results(season_id, round_number);
CREATE INDEX idx_games_teams ON game_results(home_team_id, away_team_id);
CREATE UNIQUE INDEX idx_games_unique ON game_results(season_id, round_number, matchup_index);

CREATE TABLE box_scores (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    game_id UUID NOT NULL REFERENCES game_results(id),
    agent_id UUID NOT NULL REFERENCES agents(id),
    team_id UUID NOT NULL REFERENCES teams(id),
    minutes REAL NOT NULL,
    points INTEGER NOT NULL,
    field_goals_made INTEGER NOT NULL,
    field_goals_attempted INTEGER NOT NULL,
    three_pointers_made INTEGER NOT NULL,
    three_pointers_attempted INTEGER NOT NULL,
    free_throws_made INTEGER NOT NULL,
    free_throws_attempted INTEGER NOT NULL,
    rebounds INTEGER NOT NULL,
    assists INTEGER NOT NULL,
    steals INTEGER NOT NULL,
    turnovers INTEGER NOT NULL,
    fouls INTEGER NOT NULL,
    plus_minus INTEGER NOT NULL
);
CREATE INDEX idx_box_scores_game ON box_scores(game_id);
CREATE INDEX idx_box_scores_agent ON box_scores(agent_id);
```

### Mirrors (stored directly)

```sql
CREATE TABLE mirrors (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    season_id UUID NOT NULL REFERENCES seasons(id),
    round_number INTEGER,
    mirror_type VARCHAR(30) NOT NULL,  -- simulation, governance, private, series, season, league, tiebreaker, offseason
    team_id UUID REFERENCES teams(id),  -- NULL for shared mirrors
    governor_id UUID REFERENCES governors(id),  -- only for private mirrors
    content TEXT NOT NULL,
    context_snapshot JSONB,  -- what data the AI was given
    token_count_input INTEGER,
    token_count_output INTEGER,
    latency_ms INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_mirrors_season_type ON mirrors(season_id, mirror_type);
CREATE INDEX idx_mirrors_governor ON mirrors(governor_id);
```

### Commentary (cached with games)

```sql
CREATE TABLE commentary (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    game_id UUID NOT NULL REFERENCES game_results(id),
    possession_index INTEGER NOT NULL,
    quarter INTEGER NOT NULL,
    commentary_text TEXT NOT NULL,
    energy VARCHAR(10) NOT NULL,  -- low, medium, high, peak
    tags JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_commentary_game ON commentary(game_id);
CREATE UNIQUE INDEX idx_commentary_unique ON commentary(game_id, possession_index);
```

### Schedule

```sql
CREATE TABLE schedule (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    season_id UUID NOT NULL REFERENCES seasons(id),
    round_number INTEGER NOT NULL,
    matchup_index INTEGER NOT NULL,
    home_team_id UUID NOT NULL REFERENCES teams(id),
    away_team_id UUID NOT NULL REFERENCES teams(id),
    phase VARCHAR(20) NOT NULL DEFAULT 'regular',  -- regular, tiebreaker, semifinal, final
    series_id UUID,  -- for playoff games
    status VARCHAR(20) NOT NULL DEFAULT 'scheduled',  -- scheduled, simulated, presented
    game_result_id UUID REFERENCES game_results(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_schedule_season_round ON schedule(season_id, round_number);
CREATE UNIQUE INDEX idx_schedule_unique ON schedule(season_id, round_number, matchup_index);
```

## Governance Event Types

The `event_type` field in `governance_events` uses a dotted convention:

| event_type | aggregate_type | payload contains |
|---|---|---|
| `proposal.submitted` | proposal | raw_text, sanitized_text, ai_interpretation, tier, token_cost |
| `proposal.confirmed` | proposal | governor confirmed the AI interpretation |
| `proposal.cancelled` | proposal | governor or system cancelled |
| `proposal.amended` | proposal | amendment_text, new_interpretation, amending_governor_id |
| `vote.cast` | proposal | vote (yes/no), weight, boost_used |
| `vote.revealed` | proposal | all votes for this proposal (on window close) |
| `proposal.passed` | proposal | final_vote_tally, weighted_yes, weighted_no |
| `proposal.failed` | proposal | final_vote_tally |
| `rule.enacted` | rule_change | parameter, old_value, new_value, source_proposal_id |
| `rule.rolled_back` | rule_change | parameter, rolled_back_value, reason |
| `token.regenerated` | token | governor_id, token_type, amount, new_balance |
| `token.spent` | token | governor_id, token_type, amount, reason (propose/amend/boost) |
| `trade.offered` | trade | from_governor, to_governor, offered_tokens, requested_tokens |
| `trade.accepted` | trade | |
| `trade.rejected` | trade | |
| `trade.expired` | trade | |
| `window.opened` | governance_window | window_id, round_number |
| `window.closed` | governance_window | window_id, proposals_resolved |

## Read Projections (Repository Methods)

```python
class Repository:
    """Read projections from the event store + direct table access."""

    # Token balances: derived from token events
    async def get_token_balance(self, governor_id: UUID) -> TokenBalance: ...

    # Current ruleset: apply rule.enacted events in order to starting_ruleset
    async def get_current_ruleset(self, season_id: UUID) -> RuleSet: ...

    # Proposal status: latest event for aggregate_id
    async def get_proposal(self, proposal_id: UUID) -> Proposal: ...
    async def get_active_proposals(self, season_id: UUID) -> list[Proposal]: ...

    # Vote tally: aggregate vote.cast events for proposal
    async def get_vote_tally(self, proposal_id: UUID) -> VoteTally: ...

    # Direct table reads (not event-sourced)
    async def get_game_result(self, game_id: UUID) -> GameResult: ...
    async def get_standings(self, season_id: UUID) -> Standings: ...
    async def get_team(self, team_id: UUID) -> Team: ...
    async def get_agent(self, agent_id: UUID) -> Agent: ...
    async def get_mirror(self, mirror_id: UUID) -> Mirror: ...

    # Writes
    async def append_event(self, event: GovernanceEvent) -> GovernanceEvent: ...
    async def store_game_result(self, result: GameResult) -> GameResult: ...
    async def store_mirror(self, mirror: Mirror) -> Mirror: ...
```

## Performance Considerations

- **Current ruleset** is cached on the `seasons.current_ruleset` column, updated on each `rule.enacted` event. Avoids replaying the full event log on every API request.
- **Token balances** could be similarly cached in a `token_balances` materialized table, updated on token events. For hackathon scale (< 50 governors), computing from events is fast enough.
- **Game results batch insert** — a round produces 4 games + 12-16 box scores. Use `insert_many` in a single transaction.
- **Event log indexing** — the compound index on `(aggregate_type, aggregate_id)` handles most queries. Season+round index for mirror generation context.

## SQLAlchemy Model Structure

```
db/
├── engine.py          # create_async_engine, sessionmaker, get_session dependency
├── models.py          # SQLAlchemy ORM models (Table definitions)
├── repository.py      # Repository class with read projections + writes
└── migrations/        # Alembic migrations directory
    ├── env.py
    └── versions/
```

The SQLAlchemy models in `db/models.py` map to the tables above. The Pydantic models in `models/` are the API/domain layer. Repository methods translate between them.

## Migration Strategy

- Alembic `env.py` configured for async (uses `run_async` wrapper).
- `fly.toml` release command: `alembic upgrade head` runs before each deploy.
- Local dev: `alembic upgrade head` after `uv sync`.
- Initial migration creates all tables. Subsequent migrations are incremental.

## SQLite Compatibility

For local dev, SQLite differences:
- No `UUID` type — use `VARCHAR(36)` with Python-generated UUIDs.
- No `JSONB` — use `JSON` (SQLAlchemy handles this transparently with `type_coerce`).
- No `gen_random_uuid()` — generate in Python.
- No `TIMESTAMPTZ` — use `TIMESTAMP` (SQLAlchemy normalizes).
- SQLAlchemy's dialect system handles these differences. The repository code is identical for both backends.

## Acceptance Criteria

- [ ] All tables created via Alembic migration
- [ ] Repository can append governance events and read projections
- [ ] Repository can store and retrieve game results with box scores
- [ ] Repository can store and retrieve mirrors
- [ ] SQLite works for local dev, Postgres for production
- [ ] Event log is truly append-only (no UPDATE/DELETE in repository)
- [ ] Current ruleset cache updated on rule.enacted events
- [ ] Tests: event projection accuracy, concurrent event appends, SQLite compat
