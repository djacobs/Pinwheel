# Wire Up Live Play-by-Play Streaming

## Context

The simulation engine runs instantly, but players should experience games in human time. Currently the arena page shows final scores immediately after simulation — spoiling results. The presenter layer (`presenter.py`) already drips possession events through the EventBus, and the SSE endpoint already streams them, but the frontend doesn't consume them. Also, `demo_seed.py step` runs in a separate process so its EventBus events never reach the web server.

**Key constraint from the user:** "Instant mode happens silently in the background. Players experience games in human time. We shouldn't share results of Instant mode in any circumstances."

## Changes

### 1. Add `presented` column to GameResultRow

**File:** `src/pinwheel/db/models.py`
- Add `presented: Mapped[bool] = mapped_column(Boolean, default=False)` to `GameResultRow`
- Games start hidden; only shown after the presenter finishes replaying them

**File:** `src/pinwheel/db/repository.py`
- Add `mark_game_presented(game_id)` method
- Add `presented_only: bool = False` param to `get_games_for_round()` — filters to `presented=True OR presented IS NULL` (NULL = legacy data)

### 2. Expose game_row_ids and teams_cache from step_round

**File:** `src/pinwheel/core/game_loop.py`
- Collect `game_row.id` into a list during the simulation loop
- Add `game_row_ids: list[str]` and `teams_cache: dict` to `RoundResult`
- These flow downstream to the presenter for name resolution and DB marking

### 3. Enrich presenter events with names + narration

**File:** `src/pinwheel/core/presenter.py`
- Add params: `name_cache: dict[str, str]`, `on_game_finished: Callable[[int], Awaitable[None]] | None`
- Resolve `ball_handler_id` → player name, `offense_team_id` → team name using `name_cache`
- Generate narration server-side via `narrate_play()` and include in `presentation.possession` payload
- Add `home_team_name`, `away_team_name` to `game_starting` and `game_finished` payloads
- Call `on_game_finished(game_index)` after publishing each `game_finished` event

### 4. Wire scheduler_runner to pass names + mark games presented

**File:** `src/pinwheel/core/scheduler_runner.py`
- Build `name_cache` from `round_result.teams_cache`
- Create `mark_presented(game_index)` callback that opens a DB session and calls `repo.mark_game_presented(game_row_ids[game_index])`
- Pass `name_cache` and `on_game_finished=mark_presented` to `present_round()`

### 5. Add `POST /api/pace/advance` endpoint

**File:** `src/pinwheel/api/pace.py`
- Triggers `tick_round()` within the server process (so EventBus events reach SSE clients)
- Forces `presentation_mode="replay"` regardless of config
- Demo-friendly timing defaults: `quarter_replay_seconds=15`, `game_interval_seconds=5`
- Optional query params for tuning: `?quarter_seconds=15&game_gap_seconds=5`
- Returns 409 if a presentation is already active
- Also add `GET /api/pace/status` returning `{is_active, current_round, current_game_index}`

### 6. Filter arena to only show presented games

**File:** `src/pinwheel/api/pages.py`
- Arena page: pass `presented_only=True` to `get_games_for_round()` (lines 317, 329)
- Standings: should still work from all games (standings aren't spoilers in the same way) — or also filter if user prefers

### 7. Arena live zone + JavaScript

**File:** `templates/pages/arena.html`
- Add a hidden `<div id="live-zone">` at the top with: team names, live scores, quarter indicator, game clock, and a play-by-play feed container
- Add `<script>` block (~40 lines) that opens an `EventSource('/api/events/stream')` and handles:
  - `presentation.game_starting` → show live zone, set team names, reset scores to 0
  - `presentation.possession` → update scores, quarter, clock; prepend narrated play-by-play line
  - `presentation.game_finished` → show "FINAL" status
  - `presentation.round_finished` → hide live zone, reload page to show newly-presented games
- Add an "Advance Round" button (visible in dev/staging) that POSTs to `/api/pace/advance`

**File:** `static/css/pinwheel.css`
- Live zone styles: pulsing border or "LIVE" indicator, play-by-play line styling (~15 lines)

### 8. Tests

- `test_presenter.py` — Update for new params (name_cache, on_game_finished), verify enriched payloads contain narration and names, verify callback is called
- `test_pages.py` — Verify arena filters to presented_only
- `test_pace.py` — Test advance and status endpoints (mock tick_round)

## Timing

For demo viewing, a 2-game round takes ~2 minutes:
- 4 quarters x 15 seconds = 60s per game
- 5 seconds between games
- ~125 seconds total per round

## Execution Order

1. Schema + repository (models.py, repository.py)
2. Game loop changes (game_loop.py) — expose game_row_ids + teams_cache
3. Presenter enrichment (presenter.py) — names, narration, callback
4. Scheduler runner wiring (scheduler_runner.py)
5. API endpoint (pace.py)
6. Arena page filtering (pages.py)
7. Frontend (arena.html, pinwheel.css)
8. Tests
9. Re-seed production DB, deploy, verify

## Verification

1. `uv run pytest -x -q` — all tests pass
2. Start local server, `curl -X POST localhost:8000/api/pace/advance`
3. Open `/arena` in browser — live zone appears, scores update, play-by-play streams
4. After round finishes, page reloads with presented games in the completed section
5. Games that haven't been presented never appear on the arena
