# Staggered Game Start Times + Language Fix

## Context

The arena page says "simulated" when it should say "played" — we're writing for humans, not engineers. More importantly, upcoming games show matchups but no start times. Players should see *when* each game tips off. Games within a round should also be staggered in presentation so no team appears to play two games simultaneously, and viewers get a natural "early game / late game" rhythm.

The plumbing already exists: `game_interval_seconds=1800` flows from config through `tick_round` into `present_round`, but `present_round` ignores it — all games launch via `asyncio.gather()` concurrently. The fix activates what's already wired.

## Changes

### 1. Fix "simulated" language

**File:** `templates/pages/arena.html` (line 160)

Change "once the first round is simulated" to "once the first round is played."

### 2. New utility: `src/pinwheel/core/schedule_times.py`

Pure functions, no dependencies:

- `compute_game_start_times(next_fire_time, game_count, interval_seconds)` — returns a `list[datetime]` where game N starts at `fire_time + N * interval`.
- `format_game_time(dt, tz_label="ET")` — formats as `"1:00 PM ET"` using `zoneinfo.ZoneInfo("America/New_York")`.

### 3. Activate the stagger in `present_round`

**File:** `src/pinwheel/core/presenter.py`, `present_round()` (line 131-148)

Replace `asyncio.gather(*tasks)` with staggered launches when `game_interval_seconds > 0`:

```python
if game_interval_seconds > 0 and len(game_results) > 1:
    running: list[asyncio.Task] = []
    for idx, gr in enumerate(game_results):
        if idx > 0 and not state.cancel_event.is_set():
            await asyncio.sleep(game_interval_seconds)
        task = asyncio.create_task(_present_full_game(...))
        running.append(task)
    await asyncio.gather(*running)
else:
    await asyncio.gather(*tasks)  # Original concurrent behavior
```

Update the docstring from "Unused (kept for API compat)" to describe the actual stagger behavior.

### 4. Display start times on arena "Up Next" section

**File:** `src/pinwheel/api/pages.py`, `arena_page()` (~line 596)

After building `upcoming_games`, get next fire time from `request.app.state.scheduler`:

```python
scheduler = getattr(request.app.state, "scheduler", None)
if scheduler:
    job = scheduler.get_job("tick_round")
    if job and job.next_run_time and upcoming_games:
        times = compute_game_start_times(job.next_run_time, len(upcoming_games), settings.pinwheel_game_interval_seconds)
        for ug, t in zip(upcoming_games, times):
            ug["start_time"] = format_game_time(t)
```

**File:** `templates/pages/arena.html`, upcoming games section (~line 170)

Add the time above or inside each upcoming game card:
```html
{% if ug.start_time %}
<div class="game-panel-header">
  <span class="game-meta">{{ ug.start_time }}</span>
</div>
{% endif %}
```

### 5. Display start times on home page "Coming Up"

**File:** `src/pinwheel/api/pages.py`, `home_page()` (~line 259)

Same pattern — compute start times from scheduler job and inject into `upcoming_games`.

**File:** `templates/pages/home.html` (~line 136)

Add time to each `upcoming-card`:
```html
{% if game.start_time %}
<div class="uc-time">{{ game.start_time }}</div>
{% endif %}
```

### 6. Display start times in Discord `/schedule`

**File:** `src/pinwheel/discord/bot.py`, `_handle_schedule()` (~line 2753)

Compute next fire time using APScheduler's `CronTrigger`:
```python
from apscheduler.triggers.cron import CronTrigger
trigger = CronTrigger.from_crontab(effective_cron)
next_fire = trigger.get_next_fire_time(None, datetime.now(timezone.utc))
```

Pass formatted start times to the embed builder.

**File:** `src/pinwheel/discord/embeds.py`, `build_schedule_embed()` (~line 291)

Add optional `start_times: list[str] | None = None` parameter. Append time to each matchup line:
```
Team A vs Team B -- 1:00 PM ET
Team C vs Team D -- 1:30 PM ET
```

### 7. Tests

**New file:** `tests/test_schedule_times.py`

- `test_compute_start_times_two_games` — verifies offsets
- `test_compute_start_times_single_game` — no stagger needed
- `test_format_game_time` — correct ET formatting
- `test_zero_interval` — all times identical

Update `tests/test_pages.py` if there are string assertions for the "simulated" text.

## Files touched

| File | Change |
|------|--------|
| `templates/pages/arena.html` | "simulated" -> "played" + start time display |
| `templates/pages/home.html` | Start time in "Coming Up" cards |
| `src/pinwheel/core/schedule_times.py` | **NEW** — time computation utilities |
| `src/pinwheel/core/presenter.py` | Activate `game_interval_seconds` stagger |
| `src/pinwheel/api/pages.py` | Inject start times into arena + home context |
| `src/pinwheel/discord/bot.py` | Compute next fire time for `/schedule` |
| `src/pinwheel/discord/embeds.py` | Display times in schedule embed |
| `tests/test_schedule_times.py` | **NEW** — unit tests |

## What stays the same

- Simulation remains instant (all games computed in one pass for AI reports)
- No schema changes — start times are computed from cron + matchup_index, not stored
- `game_interval_seconds=0` preserves concurrent behavior for instant mode and tests
- The re-entry guard (`presentation_state.is_active`) already prevents tick_round from piling up rounds during staggered presentation

## Verification

1. `uv run pytest -x -q` — all tests pass
2. `uv run ruff check src/ tests/` — clean lint
3. Start dev server, visit `/arena` — upcoming games show start times
4. Visit `/` — "Coming Up" section shows start times
5. In replay mode, observe games start sequentially with the configured interval
