# UX Polish, Richer Reports, and Presentation Cycle

## Context

Three interconnected changes requested by the user:

1. **UX fixes**: Remove "Rules" from nav (page stays accessible via URL), migrate the "wild card" copy and key game parameters onto the Play page, remove the AI sentence from the hero
2. **Richer reports**: Make simulation and governance reports more verbose — mention specific rule changes with old/new values, governance outcomes, timing of next governance window
3. **Real-time presentation cycle**: New architecture for dripping game events over real time instead of dumping results instantly. Target cadence: 4-5 min/quarter, games every 30 min, 4 games/round (~2 hours), governance between rounds, 2 rounds/season for testing

## Work Item 1: UX Fixes (templates + pages.py)

### 1a. Remove "Rules" from nav

**File:** `templates/base.html` (line 29)
- Delete the Rules nav link: `<a href="/rules" ...>Rules</a>`
- The `/rules` route stays — just not in the nav. FAQ still links to it.

### 1b. Remove AI sentence from Play page hero

**File:** `templates/pages/play.html` (lines 8-13)
- Remove: "The AI watches and reflects &mdash; but every decision is yours."
- Keep the rest of the hero paragraph.

### 1c. Add wild card section + key game parameters to Play page

**File:** `templates/pages/play.html`
- After the "The Rhythm" section (line 103), add a new section "Beyond the Numbers" — copy the self-contained wild card block from `templates/pages/rules.html` lines 21-68 (the `rules-wildcard` div with examples and flow strip)
- After that, add a "Current Game Parameters" section showing a compact summary of key rules. Not the full rules dump — just the most interesting parameters (shot_clock, three_point_value, elam_target_margin, quarter_minutes, etc.) in a small card grid.

**File:** `src/pinwheel/api/pages.py` — `play_page()` (line 199)
- Load the current `RuleSet` from the repo (same pattern as `rules_page()` uses)
- Pass a `key_params` list to the template (label, value pairs for ~6 key parameters)
- Pass `community_changes` count for context

### 1d. Tests
- Existing `test_pages.py` covers page routes — add assertion that `/play` returns 200 and contains "Beyond the Numbers" text

## Work Item 2: Richer Reports

### 2a. Enrich governance data passed to reports

**File:** `src/pinwheel/core/game_loop.py` (lines 362-368)
- Currently `governance_data["rules_changed"]` contains `VoteTally.model_dump()` — which has proposal_id, vote counts, and passed flag but **no parameter/old_value/new_value**
- After governance closes, also fetch `rule.enacted` events from the repo to get the `RuleChange` data (parameter, old_value, new_value, round_enacted)
- Build enriched `rules_changed` list that includes both tally info AND the actual parameter changes
- Add `next_governance_window_minutes` to governance_data (from settings.pinwheel_gov_window)

### 2b. Update report prompts for more verbose output

**File:** `src/pinwheel/ai/report.py`

**Simulation report prompt** (line 25):
- Change "2-4 paragraphs" to "3-5 paragraphs"
- Add instruction: "If rules changed recently, analyze how the new rules affected gameplay outcomes this round. Reference specific parameter changes and their visible effects."
- Add: "Mention the next governance window and what patterns governors might want to pay attention to."

**Governance report prompt** (line 43):
- Change "2-3 paragraphs" to "3-5 paragraphs"
- Add instruction: "For each rule that changed, state the parameter, old value, and new value explicitly (e.g., 'three_point_value went from 3 to 4')."
- Add: "Note the outcome of the governance window: how many proposals were filed, how many passed, how many failed."
- Add: "Mention when the next governance window opens."

**Private report prompt** (line 60):
- Change "1-2 paragraphs" to "2-3 paragraphs"

### 2c. Increase max_tokens

**File:** `src/pinwheel/ai/report.py` (line 228)
- Change `max_tokens=800` to `max_tokens=1500`

### 2d. Update mock generators

**File:** `src/pinwheel/ai/report.py` — mock functions (lines 241-425)
- `generate_governance_report_mock`: Use the enriched `rules_changed` data to mention parameter/old_value/new_value in the mock output
- `generate_simulation_report_mock`: Add lines about recent rule changes if available in round_data
- Both mocks should produce slightly longer output to match the new prompt expectations

### 2e. Tests
- Update `test_reports.py` to verify mocks reference rule change details when provided
- Verify governance mock mentions parameter names and old/new values

## Work Item 3: Real-Time Presentation Cycle

### 3a. New config fields

**File:** `src/pinwheel/config.py`
- Add to Settings:
  ```python
  pinwheel_presentation_mode: str = "instant"  # "instant" or "replay"
  pinwheel_game_interval_seconds: int = 1800    # 30 min between games
  pinwheel_quarter_replay_seconds: int = 300    # 5 min per quarter replay
  ```

### 3b. New presenter module

**File:** `src/pinwheel/core/presenter.py` (NEW)

The presenter is a thin replay layer. The simulation engine stays pure and instant. The presenter takes the already-computed `GameResult` and drips its possession logs over real time via the existing `EventBus`.

```python
@dataclass
class PresentationState:
    """Tracks active presentation for re-entry guard."""
    is_active: bool = False
    current_round: int = 0
    current_game_index: int = 0
    cancel_event: asyncio.Event | None = None

async def present_round(
    game_results: list[GameResult],
    event_bus: EventBus,
    state: PresentationState,
    game_interval_seconds: int = 1800,
    quarter_replay_seconds: int = 300,
) -> None:
    """Replay a round's games over real time. Called after step_round()."""
```

- For each game in the round:
  - Publish `game.starting` event
  - For each possession in the game's play-by-play:
    - Calculate delay based on quarter_replay_seconds / possessions_per_quarter
    - `await asyncio.sleep(delay)`
    - Publish `possession.played` event with the possession data
  - Publish `game.finished` event
  - If not the last game, `await asyncio.sleep(game_interval_seconds)`
- Support cancellation via `state.cancel_event`
- If `cancel_event` is set, break out cleanly

### 3c. Wire presenter into scheduler

**File:** `src/pinwheel/core/scheduler_runner.py`
- After `step_round()` completes, if `presentation_mode == "replay"`:
  - Create a background task for `present_round()`
  - Store the `PresentationState` on `app.state` for re-entry guard
  - If a presentation is already active, skip (don't double-present)
- If `presentation_mode == "instant"`:
  - Existing behavior — publish all events immediately (current behavior)

**File:** `src/pinwheel/main.py`
- Initialize `PresentationState` on `app.state` during lifespan
- Pass presentation config to scheduler job kwargs

### 3d. Tests

**File:** `tests/test_presenter.py` (NEW)
- Test `present_round()` with a mock EventBus, verify events are published in order
- Test cancellation: set cancel_event mid-presentation, verify clean exit
- Test that `PresentationState.is_active` prevents re-entry
- Use short delays (0.01s) in tests for speed

**File:** `tests/test_scheduler_runner.py`
- Add test verifying replay mode triggers `present_round()`
- Add test verifying instant mode skips presenter

## Execution Order

1. **Work Item 1** (UX Fixes) — no dependencies, can run first
2. **Work Item 2** (Richer Reports) — no dependencies on 1, can run in parallel
3. **Work Item 3** (Presentation Cycle) — independent, can run in parallel

All three can be implemented in parallel background agents.

## Verification

1. `uv run pytest -x -q` — all tests pass (expect ~475+ tests)
2. `uv run ruff check src/ tests/` — zero lint
3. Visual: `/play` page shows wild card section and key parameters, no "Rules" in nav
4. Visual: `/rules` page still accessible via direct URL
5. Mock reports mention specific rule changes with old/new values
6. Presenter tests verify event ordering and cancellation
