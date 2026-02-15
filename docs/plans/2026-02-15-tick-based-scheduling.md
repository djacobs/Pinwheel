# Plan: Tick-Based Scheduling — No Team Plays Twice Per Tick

## Context

The scheduler currently puts an entire round-robin cycle (6 games for 4 teams) into a single `round_number`. Every team plays 3 games simultaneously. This violates the core invariant: **no team plays more than one game at once**.

The fix: each `round_number` becomes a **tick** — one set of simultaneous games where no team appears twice. With 4 teams, a tick is 2 games. A round (complete round-robin) takes 3 ticks. Default 3 rounds = 9 ticks, 18 total games.

Product definitions (already written to `docs/product/RUN_OF_PLAY.md`):
- **Round:** Every team plays every other team exactly once.
- **Tick:** One firing of the scheduler. Each tick plays one set of simultaneous games where no team appears twice.
- **Series:** A playoff matchup played until progression criteria are met.

## Changes

### 1. Scheduler — split slots into separate round_numbers
**File:** `src/pinwheel/core/scheduler.py`

Move `round_num` increment inside the `_slot` loop instead of the `cycle` loop. Each slot becomes its own `round_number`. Reset `match_idx` per slot.

Before: 3 round_numbers × 6 games = 18 games
After: 9 round_numbers × 2 games = 18 games

Update module docstring to use round/tick/cycle vocabulary.

### 2. Governance interval — tally every tick (no change needed)
**File:** `src/pinwheel/core/game_loop.py` (line 1288)

RUN_OF_PLAY.md says: "After each set of games, votes and proposals are tallied." This means tally every tick. `governance_interval=1` with 9 ticks = 9 tallies per season. This is correct — more governance windows = more responsive governance. The deferral system (two-cycle pattern) is round-number-agnostic and continues to work.

No code change needed.

### 3. Season-complete check — already works
**File:** `src/pinwheel/core/game_loop.py` (line 154)

`_check_season_complete()` compares sets of scheduled vs played round_numbers. Works with any count. No change needed.

### 4. Playoff bracket generation — already works
**File:** `src/pinwheel/core/game_loop.py` (line 575)

`playoff_round_start = max(scheduled, played) + 1`. Playoffs start at round 10 instead of 4. No change needed.

### 5. Playoff series advancement — already works
**File:** `src/pinwheel/core/game_loop.py` (line 284)

`next_round = sim_round_number + 1`. Each series game gets its own round_number. Two semifinal games can share a round (no team overlap). No change needed.

### 6. Schedule times — already works
**File:** `src/pinwheel/core/schedule_times.py`

`compute_round_start_times(cron_expression, round_count)` takes a count and returns cron fire times. Callers pass the actual unplayed round count from the schedule query. No change needed.

### 7. Update stale docs/comments referencing old round semantics
**Files:**
- `src/pinwheel/core/schedule_times.py` (module docstring — remove "may contain more games than can play simultaneously")
- `docs/DEMO_MODE.md`, `docs/OPS.md`, `docs/GAME_LOOP.md` — update governance_interval default references from 3 to 1

### 8. Fix tests
Tests that assert `total_rounds == 3` or `games_per_round == 6` need updating to reflect 9 ticks with 2 games each. Key test files:
- `tests/test_game_loop.py` — round count assertions
- `tests/test_narrative.py` — `ctx.total_rounds`
- `tests/test_discord.py` — `ctx["total_rounds"]`
- `tests/test_onboarding.py` — `ctx.games_played`
- `tests/test_api/test_e2e.py` — round-robin structure assertions

Run `uv run pytest -x -q` after each change to catch cascading failures.

## What does NOT change
- `tick_round()` in scheduler_runner.py (already does `max(round_number) + 1`)
- `round_robins_per_season` semantics (still means "number of complete round-robin cycles")
- Governance tally logic (modulo check works with any round_number sequence)
- Playoff series logic (already one game per team per round)
- Presentation/replay logic (already iterates games within a round_number)

## Verification
1. `uv run pytest -x -q` — all tests pass
2. `uv run ruff check src/ tests/` — clean
3. Manual verification: `generate_round_robin(['A','B','C','D'], num_rounds=3)` produces 9 round_numbers with 2 games each, max team appearances per round = 1
