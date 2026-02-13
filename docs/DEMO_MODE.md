# Pinwheel Fates: Demo Mode & Environment Configuration

One place for all environment-specific behavior. Consolidates config scattered across OPS.md, GAME_LOOP.md, season-lifecycle-plan, day1-implementation-plan, and ACCEPTANCE_CRITERIA.md.

---

## 1. Three Environments

| Environment | Purpose | Database | AI Calls | Audience |
|-------------|---------|----------|----------|----------|
| **development** | Local dev, fast iteration, tests | SQLite (aiosqlite) | Mocked in tests, real for seeding | Developer |
| **staging** | Pre-deploy validation, demo rehearsal | SQLite or Postgres | Real (Opus 4.6) | Team, demo prep |
| **production** | Live league with real governors | PostgreSQL (asyncpg) | Real (Opus 4.6) | Players |

Set via `PINWHEEL_ENV` environment variable. Defaults to `development`.

---

## 2. Timing Table

| Parameter | Development | Staging | Production |
|-----------|-------------|---------|------------|
| `PINWHEEL_PRESENTATION_PACE` | `fast` (1 min) | `normal` (5 min) | `slow` (15 min) |
| `PINWHEEL_PRESENTATION_MODE` | `instant` | `replay` | `replay` |
| `PINWHEEL_GOVERNANCE_INTERVAL` | `3` | `3` | `3` |
| `PINWHEEL_AUTO_ADVANCE` | `true` | `true` | `true` |

---

## 3. Pace Modes & Presentation

Two settings control how rounds advance and how games are shown:

**`PINWHEEL_PRESENTATION_PACE`** — controls round cadence (cron interval):

| Mode | Cron | Round Interval | Use Case |
|------|------|---------------|----------|
| `fast` | `*/1 * * * *` | 1 min | Local dev, rapid iteration |
| `normal` | `*/5 * * * *` | 5 min | Demos, staging |
| `slow` | `*/15 * * * *` | 15 min | Production play |
| `manual` | (none) | Admin-triggered | Controlled demos |

An explicit cron override is available via `PINWHEEL_GAME_CRON`.

**`PINWHEEL_PRESENTATION_MODE`** — controls how games are shown:

| Mode | Behavior | Use Case |
|------|----------|----------|
| `instant` | Results available immediately, no play-by-play streaming | Tests, batch seeding |
| `replay` | Presenter streams play-by-play via SSE over real time | Live viewing, demos |

---

## 4. Dev Season Config

A compressed season for development and demos. Full competitive arc in ~25-30 minutes.

| Parameter | Dev/Staging | Production |
|-----------|-------------|------------|
| Teams | 8 | 8 |
| Round-robins | 1 (7 rounds) | 3 (21 rounds) |
| Games per round | 4 | 4 |
| Total regular season games | 28 | 84 |
| Governance interval | Every 3 rounds (configurable) | Every 3 rounds (governable) |
| Playoff semis | Best-of-1 | Best-of-5 |
| Playoff finals | Best-of-3 | Best-of-7 |
| Offseason | Disabled | Enabled |
| Token regen | Every tally round | Every tally round |
| **Total duration** | **~25-30 min** | **~25-30 hours** |

**The math (dev season at `fast` pace):**
- 7 rounds x (4 games x 5 min presentation + 2 min governance) = ~168 min... but games present simultaneously (4 at once), so: 7 rounds x (5 min + 2 min) = **~49 min for regular season**
- Playoffs: 2 semi games (5 min each) + up to 3 finals games (5 min each) = **~25 min**
- **Total: ~74 min at `fast` pace, ~25 min at `demo` pace**

```python
DEV_SEASON_CONFIG = SeasonConfig(
    rounds_per_round_robin=7,
    governance_rounds_interval=1,
    playoff_semis_best_of=1,
    playoff_finals_best_of=3,
    offseason_enabled=False,
)

PROD_SEASON_CONFIG = SeasonConfig(
    rounds_per_round_robin=21,
    governance_rounds_interval=1,
    playoff_semis_best_of=5,
    playoff_finals_best_of=7,
    offseason_enabled=True,
)
```

---

## 5. Seed Data

| Environment | Seed Strategy | Determinism |
|-------------|--------------|-------------|
| **development** | Auto-generate on first run via `python -m pinwheel.seed --generate`. 8 teams, AI-generated names/backstories, archetype-based attributes. | Fixed seed (`--seed 42`) for reproducible league |
| **staging** | Load from checked-in YAML (`league.yaml`). Hand-tuned for demo quality. | Fixed seeds for repeatable demo narrative |
| **production** | Load from YAML. AI-generated, then hand-edited for quality. | Random seeds per round (deterministic per game via `compute_seed(league_id, round, matchup)`) |

**Seeding workflow:**
```bash
# Generate a league (AI creates teams, archetypes assign attributes)
python -m pinwheel.seed --generate --output league.yaml --seed 42

# Edit league.yaml by hand if desired...

# Seed into database
python -m pinwheel.seed --config league.yaml
```

---

## 6. Feature Flags by Environment

| Feature | Development | Staging | Production |
|---------|-------------|---------|------------|
| Commentary caching | Disabled (regenerate each run) | Enabled | Enabled |
| Error pages (styled) | Disabled (stack traces) | Enabled | Enabled |
| OpenAPI docs (`/docs`) | Enabled | Enabled | Disabled |
| Auto-seed on startup | Enabled | Disabled | Disabled |
| Report staleness tolerance | Infinite (never stale) | 1 round | 1 round |
| Admin endpoints | Enabled (no auth) | Enabled (basic auth) | Enabled (admin auth) |
| Structured logging (JSON) | Disabled (human-readable) | Enabled | Enabled |
| AI call tracking | Enabled (logged) | Enabled (logged + metered) | Enabled (logged + metered + alarmed) |

---

## 7. Hackathon Demo Script

A repeatable 5-minute live demo. Pre-seeded league, deterministic seeds ensure the same narrative beats every run.

### Pre-Demo Setup

```bash
# Reset and seed a fresh demo league
PINWHEEL_ENV=staging python -m pinwheel.seed --config demo_league.yaml --seed 42

# Pre-simulate 3 rounds so standings exist
PINWHEEL_ENV=staging python -m pinwheel.admin simulate-rounds 1 3 --seed 42

# Open a governance window with 2 pre-loaded proposals
PINWHEEL_ENV=staging python -m pinwheel.admin open-window --proposals demo_proposals.yaml
```

### Demo Sequence (~5 minutes)

| Time | Action | What the Audience Sees |
|------|--------|----------------------|
| 0:00 | Open Arena page | 2x2 grid with standings, team records from 3 completed rounds |
| 0:15 | Navigate to Season page | Standings, rule evolution (no changes yet — defaults), stat leaders |
| 0:30 | Navigate to Governance | 2 active proposals. Show AI interpretation of natural language rule change. |
| 1:00 | Submit a live proposal | Type a rule change in natural language. Show the AI interpretation pipeline. Confirm. |
| 1:30 | Cast votes on proposals | Show vote normalization, token economy. Close the window. |
| 2:00 | Trigger Round 4 simulation | 4 games simulate instantly. Presenter begins pacing at `demo` (5s) pace. |
| 2:15 | Watch live game | SSE-driven updates: play-by-play scrolls, box score ticks, commentary narrates. |
| 3:00 | Elam Ending activates | Scoreboard transforms. Progress bars. Commentary intensity ramps. |
| 3:30 | Game ends on made basket | Game-winner highlight. Commentary peak energy. |
| 3:45 | Show Game Summary | AI game recap, governance fingerprints ("rules in effect"), box score. |
| 4:00 | Show Report delivery | Simulation report posts. Show governance report connecting rules to outcomes. |
| 4:30 | Show private report (DM) | The thesis moment: "Here's what the AI sees about YOUR governance pattern." |
| 5:00 | Close | Return to Season page. Rule evolution timeline now shows the enacted change. |

### Demo Seeds

Fixed seeds ensure these narrative beats are repeatable:

- **Round 4, Game 1:** Close game that goes to Elam. Home team wins on a contested three (Heat Check move). Seed: `compute_seed("demo", 4, 1)`.
- **Proposals:** One Tier 1 change (three_point_value: 3 -> 4) and one Tier 2 change (home_crowd_boost: 0.05 -> 0.08). Both pass.
- **Report:** Governance report references the sharpshooter-favoring rule changes. Private report to the proposing governor notes their pattern.

---

## 8. Environment Variables

Full reference. All variables, all environments.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | Yes (staging/prod) | `""` | Claude API key for Opus 4.6 calls |
| `DATABASE_URL` | No | `sqlite+aiosqlite:///pinwheel.db` | Database connection string. PostgreSQL for production (`postgresql+asyncpg://...`). |
| `DISCORD_BOT_TOKEN` | Yes (when Discord enabled) | — | Discord bot token |
| `DISCORD_GUILD_ID` | Yes (when Discord enabled) | — | Discord server ID |
| `PINWHEEL_ENV` | No | `development` | Environment: `development`, `staging`, `production` |
| `PINWHEEL_PRESENTATION_PACE` | No | `fast` | Pace mode: `fast`, `normal`, `slow`, `manual` |
| `PINWHEEL_PRESENTATION_MODE` | No | `instant` | Presentation mode: `instant`, `replay` |
| `PINWHEEL_GAME_CRON` | No | (derived from pace) | Explicit cron override for simulation blocks |
| `PINWHEEL_GOVERNANCE_INTERVAL` | No | `3` | Tally governance every N rounds |
| `PINWHEEL_GOV_WINDOW` | No | `900` | Seconds per governance window (used for GQI vote deliberation calculation) |
| `PINWHEEL_AUTO_ADVANCE` | No | `true` | APScheduler auto-advance toggle |
| `PINWHEEL_LOG_LEVEL` | No | `INFO` | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

Source: `docs/OPS.md`, `docs/plans/2026-02-11-day1-implementation-plan.md`, `CLAUDE.md`
