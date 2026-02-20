# Plan: Fix Playoff Progression Pipeline

## Context

Three related bugs prevent playoffs from completing:
- **Issue A**: Season stays stuck in `"regular_season_complete"` after playoffs — no transition to `"completed"`, causing `tick_round` to loop forever on empty rounds
- **Issue B**: 4-team finals never created — `generate_playoff_bracket()` stores semis in DB but finals are only a `"TBD"` placeholder dict, never persisted
- **Issue C**: No playoff completion detection — `_check_season_complete()` only checks `phase="regular"` schedule

The fix completes the season lifecycle: `active → regular_season_complete → playoffs → completed → archived`

## Files to modify

| File | Changes |
|------|---------|
| `src/pinwheel/core/game_loop.py` | Add 3 helper functions, extend Section 8 of `step_round()`, add 2 fields to `RoundResult` |
| `tests/test_game_loop.py` | Add `TestPlayoffProgression` class (~8 tests) |

No changes needed to `repository.py`, `scheduler.py`, `scheduler_runner.py`, or `season.py` — existing methods are sufficient.

## Implementation

### 1. Add helper functions to `game_loop.py` (after `_check_season_complete`, ~line 94)

**`_determine_semifinal_winners(repo, season_id, semi_round_number) -> list[str]`**
- Get games for the semi round via `repo.get_games_for_round()`
- Sort by `matchup_index` to maintain bracket ordering (#1v#4 = index 0, #2v#3 = index 1)
- Return `[g.winner_team_id for g in games_sorted]`

**`_create_finals_entry(repo, season_id, semi_round_number, winner_team_ids) -> dict | None`**
- `finals_round = semi_round_number + 1`
- Home = winner of matchup 0 (higher seed semi), Away = winner of matchup 1
- Call `repo.create_schedule_entry(... phase="playoff")` — actually persists it (unlike the current TBD placeholder)
- Return the finals matchup dict

**`_check_all_playoffs_complete(repo, season_id) -> bool`**
- Get all playoff schedule entries via `repo.get_full_schedule(season_id, phase="playoff")`
- Get all games via `repo.get_all_games(season_id)`
- Compare `{(round_number, matchup_index)}` sets — all scheduled playoffs must have a matching game
- Return True when every playoff entry has been played

### 2. Add fields to `RoundResult` (line ~799)

```python
playoffs_complete: bool = False
finals_matchup: dict | None = None
```

### 3. Extend Section 8 of `step_round()` (line ~703)

Currently only handles `season.status not in ("regular_season_complete", "playoffs", "completed")`. Add an `elif` branch for `status in ("regular_season_complete", "playoffs")`:

```
if status is "active":
    # EXISTING: regular season completion detection (unchanged)
    _check_season_complete() → set "regular_season_complete", generate bracket

elif status in ("regular_season_complete", "playoffs"):
    # NEW: playoff progression
    1. Check _check_all_playoffs_complete() FIRST
       → If True: set status "completed", determine champion from finals game,
         publish "season.playoffs_complete" event
    2. Else check if semis are done and finals don't exist yet:
       → Get playoff schedule, find semi round (min round), finals round (semi + 1)
       → If only 1 playoff round exists (2-team bracket), skip semi logic
       → If semi round games all played AND no finals entries exist:
         determine winners, create finals entry, set status "playoffs",
         publish "season.semifinals_complete" event
```

Key design choices:
- Check `_check_all_playoffs_complete()` **before** semi-check — handles 2-team bracket cleanly (single finals round) and is defensive against rapid progression
- Guard `if not finals_playoff` prevents duplicate finals creation
- The 2-team case skips the semi branch entirely since `generate_playoff_bracket()` already stores a real finals entry for 2 teams

### 4. Update `round_completed_data` and `RoundResult` return

Add `playoffs_complete` and `finals_matchup` to both the event data dict and the RoundResult constructor call.

## Existing functions reused (no changes needed)

- `repo.get_full_schedule(season_id, phase="playoff")` — already supports phase filter
- `repo.get_games_for_round(season_id, round_number)` — returns `GameResultRow` with `.winner_team_id`, `.matchup_index`
- `repo.get_all_games(season_id)` — all game results for a season
- `repo.create_schedule_entry(... phase="playoff")` — already supports phase
- `repo.update_season_status(season_id, "completed")` — already auto-sets `completed_at`
- `compute_standings_from_repo()` — already in game_loop.py

## Tests (`tests/test_game_loop.py`)

New class `TestPlayoffProgression` using existing `_setup_season_with_teams()` (4 teams, 3 regular rounds):

1. **`test_semifinals_create_finals_entry`** — Play regular season + semis → verify `result.finals_matchup` has real team IDs, DB has 3 playoff entries (2 semis + 1 finals), season status is `"playoffs"`
2. **`test_finals_complete_season`** — Play through finals → verify `result.playoffs_complete == True`, season status `"completed"`, `completed_at` set
3. **`test_two_team_bracket_completes`** — 2-team playoff bracket → after finals, season is `"completed"` (no semi step needed)
4. **`test_semifinals_complete_event_published`** — Verify `"season.semifinals_complete"` event with `finals_matchup` and `semifinal_winners`
5. **`test_playoffs_complete_event_published`** — Verify `"season.playoffs_complete"` event with `champion_team_id`
6. **`test_no_finals_before_semis_done`** — Play 1 regular round, verify no premature playoff progression
7. **`test_season_not_active_after_completion`** — After full lifecycle, `get_active_season()` returns None (confirms Issue A fix)
8. **`test_check_all_playoffs_complete`** — Unit test: False before all playoff games, True after

## Verification

1. `uv run pytest tests/test_game_loop.py -x -q` — all tests pass (including new ones)
2. `uv run pytest -x -q` — full suite passes
3. `uv run ruff check src/ tests/` — no lint errors
