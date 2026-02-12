---
title: "feat: League Configuration & Seeding"
type: feat
date: 2026-02-10
---

# League Configuration & Seeding

## Overview

Pinwheel needs the ability to seed a new league and edit its configuration at three levels: **season**, **team**, and **player**. This is the admin/setup layer that exists before the governance loop begins — the initial state of the world that players then govern.

## Problem Statement

Before any game can simulate or any governance window can open, someone needs to:
1. Create a league with teams and players
2. Assign attributes to each player
3. Configure the starting ruleset
4. Generate an initial schedule
5. Set up governance parameters (token balances, window timing)

This configuration has a hierarchy: some settings are season-wide defaults, some are team-level overrides, and some are player-specific. The system needs to support both initial seeding (creating a league from scratch) and ongoing editing (adjusting mid-season, between seasons).

## Configuration Hierarchy

Settings cascade downward. A more specific level overrides a more general one.

```
League (persistent)
  └── Season (time-bounded)
        ├── RuleSet (Tier 1-4 parameters, starting defaults)
        ├── Schedule (format, cadence, matchups)
        ├── Governance Config (token regen rates, window timing)
        │
        └── Team (N teams per season)
              ├── Team Name, Identity
              ├── Team-level attribute modifiers (optional)
              │   e.g., "home court bonus", "team chemistry"
              │
              └── Player (M players per team)
                    ├── Name, Backstory, Archetype
                    └── Attributes (9 attrs, scored 1-100)
                          Scoring, Passing, Defense, Speed,
                          Stamina, IQ, Ego, Chaotic Alignment, Fate
```

### What lives at each level

**Season level** — applies to all teams and players unless overridden:
- Starting RuleSet (all Tier 1-4 defaults)
- Schedule format and cadence (`PINWHEEL_GAME_CRON`, games per block)
- Governance window configuration (duration, frequency, token regen rates)
- Attribute budget (total points per player, e.g., 360 across 9 attributes)
- Attribute floor/ceiling (min/max per attribute, e.g., 20-95)

**Team level** — per-team settings:
- Team name and identity (logo, color, motto — for the frontend)
- **Venue** — home court with name, capacity, altitude, surface, lat/lon location. Venue modifiers (crowd boost, travel fatigue, altitude penalty, crowd pressure) are Tier 2 rule-changeable parameters. See SIMULATION.md Venue & Home Court section.
- Roster composition (which players are on this team)
- Team-level modifiers (optional, for future use):
  - Chemistry bonus: if team has complementary archetypes, small stat boost

**Player level** — per-player settings:
- Player name and identity
- Archetype (Sharpshooter, Lockdown, Floor General, Slasher, Two-Way, Enforcer, Wildcard, etc.)
- The 9 attributes: Scoring, Passing, Defense, Speed, Stamina, IQ, Ego, Chaotic Alignment, Fate
- Attribute variance seed (for ±randomization from archetype template)

## Seeding a New League

### Option A: Config file seeding (recommended for hackathon)

A YAML or TOML file defines the entire league state. Run a CLI command to seed it:

```bash
python -m pinwheel.seed --config league.yaml
```

Example `league.yaml`:

```yaml
league:
  name: "Pinwheel League One"

season:
  name: "Season 1"
  attribute_budget: 360  # total across 9 attributes (360° like a circle, like a ball)
  attribute_range: [20, 95]
  rules:  # starting RuleSet (Tier 1-4 defaults)
    elam_trigger_score: 15
    elam_margin: 13
    shot_clock_seconds: 12
    three_point_value: 3
    two_point_value: 2
    # ... all Tier 1-4 params with defaults
  governance:
    propose_regen_rate: 2
    amend_regen_rate: 2
    boost_regen_rate: 2
    vote_threshold: 0.5
  schedule:
    format: round_robin
    games_per_round: 3

teams:
  - name: "Rose City Thorns"
    color: "#CC0000"
    venue:
      name: "The Thorn Garden"
      capacity: 8000
      altitude_ft: 50
      surface: "hardwood"
      location: [45.5152, -122.6784]  # Portland, OR
    players:
      - name: "Kaia 'Deadeye' Nakamura"
        archetype: sharpshooter
        # attributes auto-generated from archetype + budget + variance
      - name: "DJ 'The Wall' Baptiste"
        archetype: lockdown
      - name: "Senna Okafor"
        archetype: floor_general
      - name: "Riley 'Jet' Park"
        archetype: slasher  # bench

  - name: "Burnside Breakers"
    color: "#0066CC"
    players:
      - name: "Indigo Moon"
        archetype: wildcard
        # can also override specific attributes:
        attributes:
          chaotic_alignment: 90
          ego: 85
      # ...

  # ... 6 more teams (8 total)
```

### Option B: AI-generated seeding

Use Opus 4.6 to generate the entire league from a prompt:

```bash
python -m pinwheel.seed --generate --teams 6 --players-per-team 4
```

This calls Opus 4.6 with a prompt like: "Generate 6 basketball teams for a 3v3 league. Each team has 4 players (3 starters + 1 bench). Give each player a name, archetype, backstory, and attributes. Make the teams balanced but distinct. Make the names fun and the backstories brief."

**Recommendation:** Support both. Config file for deterministic, repeatable league setup (essential for testing). AI generation for creative initial seeding. The AI output gets written to a config file that can then be hand-edited.

### Option C: Admin API

REST endpoints for CRUD operations on leagues, seasons, teams, and players. Essential for the web admin interface but not needed Day 1.

```
POST   /admin/leagues
POST   /admin/seasons
POST   /admin/teams
POST   /admin/players
PUT    /admin/players/{id}/attributes
PUT    /admin/seasons/{id}/rules
POST   /admin/seasons/{id}/generate-schedule
```

**Recommendation:** Build the config file seeder first (Day 1). Add admin API endpoints when we build the frontend (Day 4). The API calls the same service layer as the seeder.

## Editing Mid-Season

Some configuration changes happen during play, through different mechanisms:

| What Changes | How It Changes | Authority |
|---|---|---|
| RuleSet (Tier 1-4) | Governance proposals + voting | Players |
| Player attributes | Not changeable mid-season (v1) | Nobody — this is the "constitution" |
| Team rosters | Agent trades (if `trade_window_open`) | Players via token trading |
| Schedule | Not changeable mid-season (v1) | Pre-generated at season start |
| Governance params (Tier 4) | Meta-governance proposals | Players |
| Token balances | Trading + regeneration | System + players |

### Future: Between-season editing

Between seasons, an admin can:
- Adjust attribute budgets (power creep or deflation)
- Add/remove teams
- Change archetype templates
- Modify attribute ranges
- Run a "draft" where teams select new players

This is post-hackathon but the data model should not prevent it.

## Attribute Budget

With 9 attributes, the budget question was resolved early:

### DECIDED: 360 budget

360 total points across 9 attributes (average 40 per attribute). 360 like degrees in a circle, like a sphere, like a ball. A pinwheel also moves in a circular motion. Creates meaningful tradeoffs — elite in 1-2 areas means weak elsewhere. The narrative attributes (Ego, Chaotic Alignment, Fate) are interesting *because* investing in them costs you something concrete.

## Archetype Updates

### DECIDED: 9 archetypes (one per attribute as signature trait)

See SIMULATION.md for the full archetype table with all 9 attribute distributions. Each archetype totals 360, with ±10 random variance per attribute. The archetypes are: Sharpshooter, Floor General, Lockdown, Slasher, Iron Horse, Savant, The Closer, Wildcard, Oracle.

## Data Model (Pydantic)

```python
class LeagueConfig(BaseModel):
    name: str
    seasons: list[SeasonConfig]

class SeasonConfig(BaseModel):
    name: str
    attribute_budget: int = 360
    attribute_range: tuple[int, int] = (20, 95)
    starting_rules: RuleSet
    governance_config: GovernanceConfig
    schedule_config: ScheduleConfig
    teams: list[TeamConfig]

class Venue(BaseModel):
    name: str
    capacity: int = Field(ge=500, le=50000)
    altitude_ft: int = Field(ge=0, le=10000, default=0)
    surface: str = "hardwood"
    location: tuple[float, float]  # lat/lon

class TeamConfig(BaseModel):
    name: str
    color: str
    motto: str | None = None
    venue: Venue
    players: list[PlayerConfig]

class PlayerConfig(BaseModel):
    name: str
    archetype: ArchetypeEnum
    backstory: str | None = None
    attribute_overrides: dict[str, int] | None = None
    # If no overrides, attributes generated from archetype + budget + variance
    moves: list[MoveRef] | None = None
    # If no moves specified, 1-2 seeded from archetype defaults

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

    @model_validator(mode='after')
    def check_budget(self) -> 'PlayerAttributes':
        total = sum(self.model_dump().values())
        # Validate against season budget
        return self
```

## Schedule Generation

Given N teams and a schedule format, generate matchups:

**Round-robin:** Every team plays every other team once per round-robin. For 8 teams, that's 7 rounds with 4 games per round (28 unique matchups per full round-robin).

**Input:** teams, format, games_per_round
**Output:** list of `(home_team_id, away_team_id, round_number)` tuples

This is a pure function in `core/scheduler.py`. The schedule is generated at season start and stored. The game loop reads from it.

## Implementation Priority

1. **Pydantic models** for LeagueConfig, SeasonConfig, TeamConfig, PlayerConfig, PlayerAttributes — these are the shared vocabulary
2. **Archetype templates** with the 9-attribute model — defines how config becomes concrete players
3. **YAML config loader** — parse league.yaml into the models
4. **Seed CLI command** — `python -m pinwheel.seed --config league.yaml` writes to database
5. **Schedule generator** — pure function, well-tested
6. **Admin API endpoints** — Day 4, thin wrappers around the service layer
7. **AI-generated seeding** — When AI layer is ready (Day 2+)

## Acceptance Criteria

- [ ] Can seed an 8-team league from a YAML config file
- [ ] Player attributes are validated against budget and range constraints
- [ ] Archetype templates produce distinct, interesting attribute distributions
- [ ] Schedule generates valid round-robin matchups for N teams
- [ ] Seeded league can immediately be used by the simulation engine
- [ ] Config is deterministic: same YAML → same league state (given same variance seed)
- [ ] Tests cover: config parsing, budget validation, archetype generation, schedule generation

## Open Questions

1. ~~**Attribute budget for 9 attributes**~~ — **DECIDED: 360** (see above).
2. ~~**Archetype redesign**~~ — **DECIDED: 9 archetypes from scratch** (see SIMULATION.md).
3. **Agent names** — Hand-authored in config, or AI-generated at seed time? Recommendation: support both. Config for deterministic testing, AI for creative seeding.
4. **Team identity** — How much team personality (colors, mottos, mascots) do we define at seed time vs. let emerge from play?
5. **Stamina across seasons** — The updated Stamina description mentions "over the course of a season." Does this mean player fatigue accumulates across games? If so, that's a scheduling concern — rest days matter.
