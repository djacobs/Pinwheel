# Plan: Separate Regular Season and Playoff Standings on Home Page

## Context

The home page mini-standings section shows a single combined standings table that mixes regular-season and playoff game results. During the playoffs, this is confusing — a team's displayed record includes playoff wins/losses mixed in with their regular-season record.

The user wants regular-season and playoff standings displayed separately on the front page.

## Current State

- `_get_standings()` in `pages.py:140` calls `repo.get_all_games(season_id)` which returns ALL games (regular + playoff)
- `GameResultRow` has a `phase` column (`"regular"`, `"playoff"`, `"semifinal"`, `"finals"`, or `None` for older data)
- `_get_season_phase()` already detects if we're in playoffs/championship
- The home page template (`home.html:121-156`) renders one standings block
- A separate `/playoffs` page already shows bracket data

## Changes

### 1. Split `_get_standings()` to filter by phase (`pages.py`)

- Add a `phase_filter: str | None = None` parameter to `_get_standings()`
- When set, filter games: include only those where `game.phase` matches (or is `None` for old data during regular season)
- For regular-season filter: include games where `phase is None` or `phase == "regular"`
- For playoff filter: include games where `phase in ("playoff", "semifinal", "finals")`

### 2. Pass both standings lists to the home template (`pages.py` home route)

- During playoffs (`season_phase` in `("playoffs", "championship")`):
  - Compute `standings` with `phase_filter="regular"` (regular-season record)
  - Compute `playoff_standings` with `phase_filter="playoff"` (playoff record only)
  - Pass both to the template
- During regular season: compute `standings` as before (no filter needed since no playoff games exist)

### 3. Update the home template (`home.html`)

- During playoffs, show two sections:
  1. **"Playoff Record"** — playoff-only W-L (compact, above regular standings)
  2. **"Regular Season"** — regular-season W-L (existing format, labeled)
- During regular season, show the existing single "Standings" section unchanged
- Use `season_phase` (already passed to template) to toggle between layouts

### Files Modified

- `src/pinwheel/api/pages.py` — `_get_standings()` gains phase filter; home route passes both standings
- `templates/pages/home.html` — conditional layout for playoff vs regular standings

### Verification

- `uv run pytest -x -q` — all tests pass
- Manually verify on localhost during a playoff-phase season: both sections show correct, separate records
- Regular-season view: unchanged single standings block
