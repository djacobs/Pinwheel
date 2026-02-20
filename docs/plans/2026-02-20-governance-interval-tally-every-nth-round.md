# Governance Interval: Tally Every Nth Round

## Context

The governance system has two problems:

1. **Dead code path**: `step_round()` checks for `window.opened` events to decide when to tally, but no code ever *writes* `window.opened` events. The `window_id` on proposals is always `""`. Governance tallying never actually runs in production.

2. **Wrong timing**: The `governance.window_closed` event fires instantly during simulation, before presentation finishes — spoiling results to Discord viewers (same bug we fixed for game results in Session 34).

**User's request:** Players can `/propose` and `/vote` anytime (already works). Tallying happens every Nth round (default 3, configurable). Governance notification fires after presentation.

## Changes

### 1. Add config setting

**File:** `src/pinwheel/config.py`

```python
pinwheel_governance_interval: int = 3  # Tally governance every N rounds
```

### 2. Add `tally_governance()` in governance.py

**File:** `src/pinwheel/core/governance.py`

New function — same logic as `close_governance_window()` but takes `season_id` directly instead of a `GovernanceWindow` object. Skips the `window.closed` event (no window concept).

```python
async def tally_governance(
    repo: Repository,
    season_id: str,
    proposals: list[Proposal],
    votes_by_proposal: dict[str, list[Vote]],
    current_ruleset: RuleSet,
    round_number: int,
) -> tuple[RuleSet, list[VoteTally]]:
```

Refactor `close_governance_window()` to delegate to this internally.

### 3. Rewrite governance section in `step_round()`

**File:** `src/pinwheel/core/game_loop.py` (lines 315-409)

Replace window-based logic with interval check:

- Add `governance_interval: int = 3` parameter to `step_round()`
- Check: `if round_number % governance_interval == 0:`
- Gather all `proposal.confirmed` events that have no matching `proposal.passed`/`proposal.failed` (i.e., unresolved)
- Deduplicate (submitted + confirmed events produce duplicates)
- Gather their votes, call `tally_governance()`
- Remove the `governance.window_closed` EventBus publish (moves to scheduler_runner)

### 4. Wire governance_interval through the call chain

**File:** `src/pinwheel/core/scheduler_runner.py` — `tick_round()`

Add `governance_interval` parameter, pass to `step_round()`.

**File:** `src/pinwheel/main.py` — scheduler job kwargs

Pass `governance_interval=settings.pinwheel_governance_interval`.

### 5. Fix governance notification timing

**File:** `src/pinwheel/core/game_loop.py`

Add governance summary to `RoundResult`:

```python
class RoundResult:
    def __init__(self, ..., governance_summary: dict | None = None):
        self.governance_summary = governance_summary
```

Populate when tallying runs (proposals_count, rules_changed, round).

**File:** `src/pinwheel/core/scheduler_runner.py`

In replay mode: publish `governance.window_closed` inside `_present_and_clear()` after `present_round()` finishes (alongside the existing `presentation_state_cleared` log).

In instant mode: publish alongside `presentation.game_finished` / `presentation.round_finished` (same block, lines 154-166).

### 6. Update `/vote` to skip resolved proposals

**File:** `src/pinwheel/discord/bot.py` — `_handle_vote()` (lines 1145-1160)

Currently finds the latest `proposal.confirmed` regardless of status. Change to filter out proposals that already have `proposal.passed`/`proposal.failed` events:

```python
confirmed = await repo.get_events_by_type(season_id, ["proposal.confirmed"])
resolved = await repo.get_events_by_type(season_id, ["proposal.passed", "proposal.failed"])
resolved_ids = {e.aggregate_id for e in resolved}
pending = [c for c in confirmed
           if c.payload.get("proposal_id", c.aggregate_id) not in resolved_ids]
```

## Files Modified

1. `src/pinwheel/config.py` — `pinwheel_governance_interval`
2. `src/pinwheel/core/governance.py` — `tally_governance()`, refactor `close_governance_window()`
3. `src/pinwheel/core/game_loop.py` — interval-based tallying, governance_summary in RoundResult
4. `src/pinwheel/core/scheduler_runner.py` — pass interval, publish governance notification after presentation
5. `src/pinwheel/main.py` — pass governance_interval to scheduler
6. `src/pinwheel/discord/bot.py` — `/vote` filters to pending proposals
7. Tests: `test_game_loop.py`, `test_scheduler_runner.py`, `test_governance.py`

## Verification

1. `uv run pytest -x -q` — all tests pass
2. `PINWHEEL_GOVERNANCE_INTERVAL=3`: governance tallies on rounds 3, 6, 9...
3. `PINWHEEL_GOVERNANCE_INTERVAL=1`: tallies every round (backward compatible)
4. `/vote` only targets unresolved proposals
5. Discord governance notification fires after presentation, not during simulation
