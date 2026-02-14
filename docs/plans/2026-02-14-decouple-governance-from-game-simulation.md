# Plan: Decouple Governance from Game Simulation

## Context

The core gameplay loop is **propose → vote → enact**. Currently, governance tallying (the "enact" step) is embedded inside `step_round()` in `game_loop.py`, which only runs when `tick_round()` advances a game round. When a season completes, `tick_round()` exits immediately (line 353), so governance tallying never runs. Carl and other governors can submit proposals and cast votes, but votes are never tallied and rules are never enacted.

This is a structural bug: governance is coupled to game simulation when it should be independent.

## Changes

### 1. Extract `tally_pending_governance()` from `step_round()`

**File:** `src/pinwheel/core/game_loop.py`

Create a new standalone async function by extracting the governance logic from `step_round()` lines 548-670:

```python
async def tally_pending_governance(
    repo: Repository,
    season_id: str,
    round_number: int,
    ruleset: RuleSet,
    event_bus: EventBus | None = None,
) -> tuple[RuleSet, list[VoteTally], dict]:
    """Tally all pending proposals and enact passing rule changes.

    Standalone function — can run with or without game simulation.
    Returns (updated_ruleset, tallies, governance_data).
    """
```

This function:
- Gathers confirmed but unresolved proposals
- Reconstructs Proposal objects from submitted events
- Gathers votes per proposal
- Calls `tally_governance()`
- Updates season ruleset if changed
- Returns the new ruleset, tallies, and governance data dict

Then refactor `step_round()` to call `tally_pending_governance()` instead of inlining the logic. The `governance_interval` check (`round_number % governance_interval == 0`) stays in `step_round()` — `tally_pending_governance()` always tallies when called.

### 2. Add governance-only path in `tick_round()`

**File:** `src/pinwheel/core/scheduler_runner.py`

Change the completed-season early return (line 353) to run governance-only:

```python
if season.status in ("completed", "archived"):
    # Governance-only tick: tally pending proposals without simulating games
    ruleset = RuleSet(**(season.current_ruleset or {}))
    last_round = ...  # query max round_number from game results
    new_ruleset, tallies, gov_data = await tally_pending_governance(
        repo, season.id, last_round, ruleset, event_bus,
    )
    if tallies:
        # Publish governance notification so Discord gets notified
        ...
    return
```

Key detail: skip the `governance_interval` modulo check — for completed seasons, tally immediately whenever there are pending proposals.

### 3. Clean up interaction mock pattern in tests

**File:** `tests/test_discord.py`

Create a helper function at the top of the test file:

```python
def make_interaction(**overrides) -> AsyncMock:
    """Build a fully-configured Discord interaction mock."""
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.user = MagicMock()
    interaction.user.id = overrides.get("user_id", 12345)
    interaction.user.display_name = overrides.get("display_name", "TestGovernor")
    interaction.channel = AsyncMock()
    return interaction
```

Replace the 15+ scattered `interaction.followup = AsyncMock()` lines with calls to `make_interaction()`.

### 4. Add lifecycle integration tests

**File:** `tests/test_governance.py` (add new test class at end)

```python
class TestGovernanceLifecycleAcrossSeasonCompletion:
    """Governance must work regardless of season status."""

    async def test_tally_pending_on_completed_season(self, repo):
        """Proposals submitted on a completed season get tallied."""
        # 1. Create season, mark completed
        # 2. Submit and confirm a proposal
        # 3. Cast votes
        # 4. Call tally_pending_governance()
        # 5. Assert proposal passed and ruleset updated

    async def test_governance_with_null_ruleset(self, repo):
        """Governance works even when current_ruleset is NULL."""

    async def test_no_pending_proposals_is_noop(self, repo):
        """tally_pending_governance returns empty when nothing to tally."""
```

Also add a test in `tests/test_scheduler_runner.py`:

```python
async def test_tick_round_tallies_governance_on_completed_season(self, ...):
    """tick_round still tallies governance when season is completed."""
```

## Files Modified

| File | Change |
|------|--------|
| `src/pinwheel/core/game_loop.py` | Extract `tally_pending_governance()`, refactor `step_round()` to use it |
| `src/pinwheel/core/scheduler_runner.py` | Add governance-only path for completed seasons in `tick_round()` |
| `tests/test_discord.py` | Replace scattered mock setup with `make_interaction()` helper |
| `tests/test_governance.py` | Add lifecycle integration tests |
| `tests/test_scheduler_runner.py` | Add test for governance on completed season |

## Verification

1. `uv run pytest -x -q` — all existing tests pass
2. New tests prove:
   - `tally_pending_governance()` works standalone
   - `tick_round()` tallies governance on completed seasons
   - Governance works with null ruleset
   - The full propose → vote → enact cycle works on a completed season
3. `uv run ruff check src/ tests/` — no lint errors
