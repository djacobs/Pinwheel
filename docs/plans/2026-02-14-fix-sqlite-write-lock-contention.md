# Fix: SQLite Write Lock Contention During `/join`

## Context

Players get "Something went wrong joining the team" when `/join` coincides with `tick_round`. Root cause: `tick_round` holds a single SQLite session for 30-90 seconds while `step_round` interleaves fast DB writes with slow AI API calls (commentary, reports). SQLite allows only one writer — so `/join`, `/propose`, `/vote` all fail during that window.

**Goal:** Release the DB write lock between AI calls so Discord commands can write freely. Reduce lock hold time from ~60s to ~3s per phase.

## Approach

Extract the body of `step_round` into phase functions. Keep `step_round(repo, ...)` backward-compatible for all existing tests. Add `step_round_multisession(engine, ...)` that opens/closes separate sessions per phase. Update `tick_round` to call the multi-session variant.

### Lock timeline after fix:
```
Session 1 (~2-3s): simulate games, store results, tally governance
   [LOCK RELEASED — /join etc. can write here]
AI calls (~30-90s): commentary, highlights, reports (NO session open)
   [LOCK RELEASED — /join etc. can write here]
Session 2 (~1-2s): store reports, run evals, season progression
   [LOCK RELEASED]
```

## Files to Change

1. **`src/pinwheel/core/game_loop.py`** — extract phases, add `step_round_multisession`
2. **`src/pinwheel/core/scheduler_runner.py`** — update `tick_round` to use multi-session variant
3. **`tests/test_game_loop.py`** — add tests for phase functions + multi-session
4. **`tests/test_scheduler_runner.py`** — add lock-release test

## Steps

### Step 1: Define intermediate dataclasses in `game_loop.py`

Add after the existing `RoundResult` class:

```python
@dataclasses.dataclass
class _SimPhaseResult:
    """Data from the simulate-and-store phase (Session 1)."""
    season_id: str
    round_number: int
    ruleset: RuleSet
    teams_cache: dict[str, Team]
    game_results: list[GameResult]
    game_row_ids: list[str]
    game_summaries: list[dict]       # without commentary yet
    playoff_context: str | None
    tallies: list[VoteTally]
    governance_data: dict
    governance_summary: dict | None
    governor_activity: dict[str, dict]  # gov_id -> {proposals, votes, etc.}
    active_governor_ids: set[str]

@dataclasses.dataclass
class _AIPhaseResult:
    """AI-generated content (no DB access needed)."""
    commentaries: dict[str, str]     # game_id -> commentary text
    highlight_reel: str
    sim_report: Report
    gov_report: Report
    private_reports: list[tuple[str, Report]]  # (governor_id, report)
```

### Step 2: Extract `_phase_simulate_and_govern()` from `step_round`

Covers current lines 514-737 + 789-793 (governor activity query). Does:
- Load season, schedule, teams, strategies (reads)
- Simulate all games (CPU)
- Store game results + box scores (writes, fast)
- Publish `game.completed` events (without commentary)
- Tally governance, regenerate tokens (writes, fast)
- Query governor activity IDs (read)
- Returns `_SimPhaseResult`

**Does NOT** generate commentary, highlights, or reports.

### Step 3: Extract `_phase_ai()` — pure I/O, no DB

Takes `_SimPhaseResult` + `api_key`. Makes all AI calls:
- `generate_game_commentary()` per game
- `generate_highlight_reel()`
- `generate_simulation_report()`
- `generate_governance_report()`
- `generate_private_report()` per active governor

Returns `_AIPhaseResult`. Uses mock generators when `api_key` is empty.

Bonus: can use `asyncio.gather` to parallelize independent calls.

### Step 4: Extract `_phase_persist_and_finalize()` — fast DB writes

Takes repo + `_SimPhaseResult` + `_AIPhaseResult`. Does:
- Attach commentary to game_summaries dicts
- Store all reports via `repo.store_report()`
- Run evals via `_run_evals()`
- Season progression checks (lines 854-1072)
- Publish `round.completed` event
- Build and return `RoundResult`

### Step 5: Refactor `step_round` body to call phases

Same signature, same behavior. Body becomes:

```python
async def step_round(repo, season_id, round_number, ...):
    sim = await _phase_simulate_and_govern(repo, ...)
    if sim is None:
        return RoundResult(round_number=round_number, games=[], reports=[], tallies=[])
    ai = await _phase_ai(sim, api_key)
    return await _phase_persist_and_finalize(repo, sim, ai, ...)
```

All existing tests pass unchanged — same function, same signature, same single-session behavior.

### Step 6: Add `step_round_multisession(engine, ...)`

New function that orchestrates phases with separate sessions:

```python
async def step_round_multisession(engine, season_id, round_number, ...):
    # Session 1: simulate + govern (fast)
    async with get_session(engine) as session:
        repo = Repository(session)
        sim = await _phase_simulate_and_govern(repo, ...)
    # Session closed — lock released

    if sim is None:
        return RoundResult(...)

    # NO SESSION: AI calls (slow, 30-90s)
    ai = await _phase_ai(sim, api_key)

    # Session 2: persist + finalize (fast)
    async with get_session(engine) as session:
        repo = Repository(session)
        return await _phase_persist_and_finalize(repo, sim, ai, ...)
    # Session closed
```

### Step 7: Update `tick_round` in `scheduler_runner.py`

Split current lines 340-654 into:
1. **Pre-flight session** (~1s): get active season, handle championship/offseason/completed checks, determine next round number
2. **Call `step_round_multisession(engine, ...)`** — manages its own sessions
3. **Post-round session** (~1s): mark games presented (instant mode), publish presentation events

### Step 8: Add tests

- `test_phase_simulate_and_govern` — returns correct `_SimPhaseResult`
- `test_phase_ai_mock` — returns `_AIPhaseResult` with mock content
- `test_phase_persist_and_finalize` — stores reports, returns `RoundResult`
- `test_step_round_multisession` — produces same results as `step_round`
- `test_step_round_backward_compat` — existing `step_round` still works identically

### Step 9: Run full test suite

`uv run pytest -x -q` — all existing + new tests green.

## Behavior Changes

- **`game.completed` events fire without commentary** — commentary arrives in `round.completed` instead. This is acceptable; the presentation layer already handles deferred content.
- **Partial-round on crash** — if process dies between session 1 and session 2, games are stored but reports aren't. This is already possible today, and `tick_round` handles it by skipping to the next round.

## What This Does NOT Change

- `step_round` signature and behavior (backward compat)
- Any Discord command handlers
- Any test fixtures or existing test expectations
- Database schema
- The `get_session` context manager
