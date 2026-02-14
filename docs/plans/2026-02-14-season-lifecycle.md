# Season Lifecycle Implementation Plan

## The Problem

The current season model jumps from PLAYOFFS directly to COMPLETED with no ceremony, no awards, no offseason governance, and no carry-forward vote. The narrative arc ends abruptly. When the season completes, governance commands break because there's no "active" season.

## Target State: 8-Phase Lifecycle

```
SETUP → ACTIVE → TIEBREAKER_CHECK → TIEBREAKERS → PLAYOFFS → CHAMPIONSHIP → OFFSEASON → COMPLETE
```

### What each phase means for players

**SETUP** — League seeded, teams created, schedule generated. Admin starts the season. (Already works.)

**ACTIVE** — Rounds tick on the cron, games simulate, governance tallies every Nth round. This is the heartbeat. (Already works.)

**TIEBREAKER_CHECK** (new, transient) — After the final regular-season round, check standings for ties at the playoff cutoff. Transitions immediately to TIEBREAKERS or PLAYOFFS. Players see: "Regular season complete. Checking standings..."

**TIEBREAKERS** (new) — If ties exist at the playoff cutoff, an extra governance round opens, then tiebreaker games are played. Players see: "Tied for the 4th seed! Extra governance round before the tiebreaker."

**PLAYOFFS** — Semifinal and finals games. Keep current single-elimination but add championship phase after.

**CHAMPIONSHIP** (new) — After the finals game, before marking complete. Season gets narrative closure: season report, awards, stats compilation. Time-bounded (30 min production, 2 min fast mode). Players see: championship ceremony in Discord, awards announced.

**OFFSEASON** (new) — Extended governance window where players vote on rule carry-forward. System auto-creates a "carry forward rules?" proposal. Time-bounded (60 min production, 5 min fast mode). Players vote with existing tokens.

**COMPLETE** — Season archived with full snapshot. New season can be started.

## Design Decisions

### Time-based vs round-based offseason
**Time-based.** The offseason has no games to simulate — it's a governance-only window with a real-clock deadline. The scheduler watches the clock and transitions when the window expires. Deadlines stored in `SeasonRow.config` JSON (column already exists, currently unused).

### How carry-forward vote works
When the season enters OFFSEASON, the system auto-creates a proposal: "Carry forward the current ruleset to the next season." Uses standard proposal/vote machinery — no special-case code. When the offseason window closes, `tally_governance()` resolves the vote. Result stored in season config: `{"carry_forward_approved": true/false}`.

### What triggers new season
The offseason deadline passes → tally pending proposals → generate offseason report → archive season → mark COMPLETE. New season is NOT auto-created — admin uses `/new-season` which reads the carry-forward decision.

### Awards
Two categories:

**Gameplay awards** (from stats):
- MVP: highest points per game
- Defensive Player of the Season: highest steals per game
- Most Efficient: best field goal percentage (min 20 attempts)

**Governance awards** (from event log):
- Most Active Governor: most proposals + votes
- Coalition Builder: most token trades
- Rule Architect: highest proposal pass rate

All awards include AI-generated narrative context.

## Implementation Phases

### Phase 1: Season Phase Enum and Status Transitions (Foundation)

Add `SeasonPhase` enum to `core/season.py`. Validate transitions in `update_season_status()`. Update `get_active_season()` to include new active phases.

**Files:** `db/models.py`, `db/repository.py`, `core/season.py`

### Phase 2: Championship Phase (Narrative Closure)

After playoffs complete → CHAMPIONSHIP with awards, season report, ceremony. Time-bounded window, then transition to next phase.

**Files:** `core/season.py`, `core/game_loop.py` (~L896), `ai/report.py`, `core/scheduler_runner.py`, `config.py`, `discord/bot.py`

**Key change:** Replace `update_season_status(season_id, "completed")` at game_loop.py ~L897 with `enter_championship()`.

### Phase 3: Offseason Phase (Governance Carry-Forward)

After championship → OFFSEASON with extended governance window, carry-forward proposal, token regeneration.

**Files:** `core/season.py`, `core/scheduler_runner.py`, `ai/report.py`, `config.py`, `discord/bot.py`

### Phase 4: Tiebreakers (Edge Case Handling)

After regular season → check for ties at playoff cutoff → extra governance + tiebreaker games.

**Files:** `core/season.py`, `core/game_loop.py` (~L804), `core/scheduler_runner.py`

### Dependencies
```
Phase 1 (Foundation) ──┐
                       ├── Phase 2 (Championship) ── Phase 3 (Offseason)
                       └── Phase 4 (Tiebreakers)     [depends on 2]
                            [independent of 2 & 3]
```

## Migration Path

- No schema changes needed — `SeasonRow.status` is `String(20)`, `SeasonRow.config` is `JSON, nullable=True`
- Completed Season 1 stays "completed" — it's already past championship/offseason
- New lifecycle only activates for Season 2+
- `/new-season` still works to create Season 2 from completed Season 1 data

## Key Code Locations

| Change | File | Line/Function |
|--------|------|---------------|
| Playoff complete → championship | `game_loop.py` | `step_round()` ~L896 |
| Regular season → tiebreaker check | `game_loop.py` | `step_round()` ~L804 |
| Scheduler handles new phases | `scheduler_runner.py` | `tick_round()` ~L353 |
| Season report AI generation | `ai/report.py` | after L248 |
| Discord championship events | `discord/bot.py` | `_dispatch_event()` ~L1020 |
| Phase duration config | `config.py` | Settings class ~L108 |
| Carry-forward proposal | `core/season.py` | `enter_offseason()` |

## Function Signatures

```python
# core/season.py

class SeasonPhase(str, Enum):
    SETUP = "setup"
    ACTIVE = "active"
    TIEBREAKER_CHECK = "tiebreaker_check"
    TIEBREAKERS = "tiebreakers"
    PLAYOFFS = "playoffs"
    CHAMPIONSHIP = "championship"
    OFFSEASON = "offseason"
    COMPLETE = "complete"

async def transition_season(repo, season_id, to_phase, event_bus=None)
async def compute_awards(repo, season_id) -> list[dict]
async def enter_championship(repo, season_id, champion_team_id, duration_seconds=1800, api_key="", event_bus=None)
async def enter_offseason(repo, season_id, duration_seconds=3600, event_bus=None)
async def close_offseason(repo, season_id, api_key="", event_bus=None)
async def check_and_handle_tiebreakers(repo, season_id, event_bus=None) -> bool
```
