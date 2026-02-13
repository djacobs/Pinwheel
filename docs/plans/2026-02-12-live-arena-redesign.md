# Live Arena Redesign

## Context

The "simulate instantly, replay later" architecture has fundamental problems:
- Page reload during a live game shows nothing (state is only in the browser DOM)
- Server restart (deploy) kills the presentation and leaks final scores
- The `presented` column is a band-aid that adds complexity everywhere

**User directive:** "Consider work done a sunk cost. What is the best path forward?"

**User preference:** Games should feel genuinely live. Two concurrent games, ~20 minutes each. Players need to meet hoopers through the live interface — box scores, leaders, linked player names.

## Architecture: Server-Rendered Live State

The key insight: **the arena page should render the current state of live games server-side in the Jinja2 template.** No separate snapshot endpoint. No client-only state. The server knows what's happening and tells the page on every load.

**How it works:**
1. Simulation runs instantly (keep `simulate_game()` pure — it's tested and correct)
2. The presenter drips possessions over time (keep existing presenter logic)
3. **NEW:** The presenter writes running state to `PresentationState` as it goes
4. **NEW:** The `arena_page()` route reads `PresentationState` and passes live game data to the template
5. **NEW:** The template renders live games server-side (scores, quarter, recent plays, leaders)
6. SSE updates the DOM after page load (existing JS, mostly unchanged)
7. On page reload, the server re-renders with current state — no gap

**What this replaces:**
- No snapshot endpoint needed (the page IS the snapshot)
- `presented` column stays for filtering completed games but is no longer the fragile linchpin
- `PresentationState` becomes the source of truth for live games

## Changes

### 1. Enrich `PresentationState` with per-game live state

**File:** `src/pinwheel/core/presenter.py`

Add `LiveGameState` dataclass tracking everything the template needs:
- `game_index`, `game_id`, `home/away_team_id`, `home/away_team_name`
- `home_score`, `away_score`, `quarter`, `game_clock`
- `status`: `"live"` | `"final"`
- `recent_plays`: list of last 30 play dicts (narration, clock, points, handler_id, handler_name)
- `box_scores`: list of hooper box score dicts (from pre-computed GameResult)
- `home_leader` / `away_leader`: top scorer per team (updated as plays come in — or use pre-computed)

Add to `PresentationState`:
- `live_games: dict[int, LiveGameState]` — keyed by game_index
- `game_results: list[GameResult]` — the pre-computed results (for box score data)
- `name_cache: dict[str, str]` — for resolving IDs to names

Update `_present_full_game()`:
- On `game_starting`: create `LiveGameState` entry in `state.live_games`
- On each possession: update scores, quarter, clock, append to recent_plays
- On `game_finished`: set status to `"final"`, compute game leaders

Update `reset()`: clear `live_games`, `game_results`, `name_cache`.

### 2. Arena route passes live game state to template

**File:** `src/pinwheel/api/pages.py`

In `arena_page()`:
- Read `request.app.state.presentation_state`
- If `is_active` and `live_games` is non-empty, build a `live_round` dict:
  ```python
  live_round = {
      "round_number": presentation_state.current_round,
      "games": [
          {
              "game_index": gs.game_index,
              "home_team_name": gs.home_team_name,
              "away_team_name": gs.away_team_name,
              "home_score": gs.home_score,
              "away_score": gs.away_score,
              "quarter": gs.quarter,
              "game_clock": gs.game_clock,
              "status": gs.status,
              "recent_plays": gs.recent_plays[-20:],
              "leaders": _compute_leaders(gs),
          }
          for gs in presentation_state.live_games.values()
      ],
  }
  ```
- Pass `live_round` to the template context (or `None` if no active presentation)

### 3. Arena template renders live games server-side

**File:** `templates/pages/arena.html`

Add a server-rendered live section ABOVE the completed rounds:

```html
{% if live_round %}
<div id="live-container" class="live-container">
  <div class="section-title">Live — Round {{ live_round.round_number }}</div>
  <div class="arena-grid">
    {% for game in live_round.games %}
    <div class="live-zone" id="live-game-{{ game.game_index }}">
      <div class="live-zone-header">
        <span class="live-badge">{{ 'FINAL' if game.status == 'final' else 'LIVE' }}</span>
        <span class="live-quarter" data-g="{{ game.game_index }}">Q{{ game.quarter }}</span>
        <span class="live-clock" data-g="{{ game.game_index }}">{{ game.game_clock }}</span>
      </div>
      <div class="live-zone-scoreboard">
        <!-- team names, scores, etc. from server data -->
      </div>
      <div class="live-leaders" data-g="{{ game.game_index }}">
        <!-- top scorer per team, linked to hooper pages -->
      </div>
      <div class="live-plays" data-g="{{ game.game_index }}">
        {% for play in game.recent_plays %}
        <div class="live-play-line">
          <span class="live-play-clock">{{ play.game_clock }}</span>
          {{ play.narration }}
          {% if play.points_scored > 0 %}<span class="live-play-pts">+{{ play.points_scored }}</span>{% endif %}
        </div>
        {% endfor %}
      </div>
    </div>
    {% endfor %}
  </div>
</div>
{% else %}
<div id="live-container" class="live-container" style="display:none;"></div>
{% endif %}
```

When `live_round` is present, the page loads with scores, plays, and leaders already visible. SSE then updates from that point forward. When it's absent, the hidden container is ready for SSE to populate dynamically (for users who load the page before games start).

### 4. Simplify JS — SSE only updates, doesn't create

**File:** `templates/pages/arena.html` (script block)

The JS logic simplifies:
- `getOrCreateZone()` still creates zones dynamically (for users already on the page when games start)
- But it also checks if a server-rendered zone already exists and reuses it
- Possession handler updates scores/plays as before
- `game_finished` handler updates badge and shows leaders
- `round_finished` handler reloads the page

### 5. Startup recovery for crashed presentations

**File:** `src/pinwheel/main.py`

In `lifespan()`, after creating `PresentationState`:
- Query for games in the latest round where `presented=False`
- If found, mark them `presented=True` immediately
- Log: "Startup recovery: marked N games as presented (round M)"

This handles the deploy-during-game case. The live experience is lost but results appear correctly.

### 6. Force replay mode in production

**File:** `src/pinwheel/config.py`

Add a model validator: if `pinwheel_env == "production"`, force `pinwheel_presentation_mode = "replay"`. This ensures games are never stored-and-shown-instantly in production.

**File:** `src/pinwheel/core/scheduler_runner.py`

In `tick_round()`, when `presentation_mode != "replay"` (instant mode, dev only): mark all games presented immediately after storing so they appear on the arena.

### 7. Live game design: leaders, hooper links, box scores

**File:** `src/pinwheel/core/presenter.py`

In `game_finished` event payload, include top scorer per team:
```python
"home_leader": {"hooper_id": ..., "hooper_name": ..., "points": ...},
"away_leader": {"hooper_id": ..., "hooper_name": ..., "points": ...},
```

**File:** `templates/pages/arena.html`

- Leaders section in each live zone shows top scorers with links to `/hoopers/{id}`
- Play-by-play lines: clicking a line navigates to the handler's hooper page (via `data-handler` attribute + click handler, since narration text has the name baked in)

**File:** `templates/pages/game.html`

- Box score table: wrap hooper names in `<a href="/hoopers/{{ p.hooper_id }}">{{ p.hooper_name }}</a>`
- Play-by-play: same linking pattern
- Add `hooper_id` to player dicts in `game_page()` route handler

### 8. Foul outcome narration

**File:** `src/pinwheel/core/narrate.py`

The foul branch doesn't differentiate between made and missed free throws when `points == 0`. Fix:
- `points > 0`: "hits N from the stripe"
- `points == 0`: "misses from the stripe"

Add variety to foul narration templates.

### 9. Discord "Elam Ending Activated" copy

**File:** `src/pinwheel/discord/embeds.py`

In `build_game_result_embed()`: Remove `"\nElam Ending activated!"` — it's always true, therefore meaningless. Replace with Elam target score if useful, or just remove.

**File:** `src/pinwheel/discord/bot.py`

In `_dispatch_event()`: Remove `is_elam` from the big-plays channel condition. Since Elam always activates, every game was going to `#big-plays`. Fix to only route genuinely notable games (blowouts, buzzer-beaters).

### 10. Upcoming schedule on arena

**File:** `src/pinwheel/api/pages.py`

In `arena_page()`, query the next unplayed round's schedule entries. Pass `upcoming_games` to template.

**File:** `templates/pages/arena.html`

Add "Up Next" section below live games showing the next round's matchups with team names.

### 11. Tests

**Update existing:**
- `test_presenter.py` — verify `live_games` dict is populated during presentation, scores track correctly, status transitions to "final", recent_plays accumulate
- `test_pages.py` — verify arena renders live games from PresentationState, verify game_page hooper links

**New tests:**
- `test_presenter.py` additions: snapshot state accumulates, leaders computed on game_finished
- `test_pages.py` additions: arena with active presentation shows live section, game_page accessible during live presentation
- `test_narrate.py` or `test_commentary.py`: foul with 0 points shows "misses"
- `test_config.py`: production forces replay mode

## Execution Order

1. **Presenter enrichment** — `LiveGameState`, enrich `PresentationState`, update `_present_full_game` / `_present_game`
2. **Arena route + template** — pass live state, server-render live games, simplify JS
3. **Startup recovery + production safeguards** — `main.py` recovery, config validator, instant mode fix
4. **Game design** — leaders in game_finished event, hooper links in game.html, foul narration
5. **Discord fixes** — remove Elam noise, fix big-plays routing
6. **Schedule** — upcoming games section on arena
7. **Tests** — update existing, add new
8. Deploy + verify

## Verification

1. `uv run pytest -x -q` — all tests pass
2. Start local server, advance a round
3. Open `/arena` — live games appear with scores, plays, leaders
4. **Reload mid-game** — page re-renders with current state, SSE resumes updates
5. After round finishes, page reloads with games in completed section
6. Game detail pages have linked hooper names
7. Discord embeds don't mention Elam
