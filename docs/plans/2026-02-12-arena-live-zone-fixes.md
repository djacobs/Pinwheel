# Arena Live Zone Fixes: Layout + Play Colors

## Context

After deploying team color schemes, the live arena page has two visual issues:

1. **Too much white space** — The `.section-title` ("Live — Round N") is a direct child of `.live-container`, which uses CSS grid with `repeat(auto-fit, minmax(380px, 1fr))`. At desktop widths, the title consumes one grid column (~480px) and the `.arena-grid` wrapper gets the other, wasting half the viewport. The live zones are also double-nested inside `.arena-grid` (which has its own `1fr 1fr` grid), further cramping them.

2. **Play-by-play lines are all the same color** — Every play in the live feed uses `var(--text-secondary)` regardless of which team is on offense. The SSE `presentation.possession` payload already includes `offense_team_id`, and team colors are available in the presenter's `colors` dict, but they're never connected to individual play lines.

## Changes

### 1. Fix live container layout

**File:** `templates/pages/arena.html` (lines 13-63)

Remove the `.arena-grid` wrapper from inside the server-rendered live section. Put `.live-zone` elements directly inside `.live-container`. This matches how JS creates zones (it already appends directly to `#live-container`).

Move `.section-title` outside `.live-container` so it doesn't participate in the grid:

```html
{% if live_round %}
<div class="section-title">Live — Round {{ live_round.round_number }}</div>
<div id="live-container" class="live-container">
  {% for game in live_round.games %}
  <div class="live-zone" id="live-game-{{ game.game_index }}">
    ...
  </div>
  {% endfor %}
</div>
{% else %}
<div id="live-container" class="live-container" style="display:none;"></div>
{% endif %}
```

Now `.live-container` grid only contains `.live-zone` elements as direct children. At 960px, two 380px+ zones sit side by side with no wasted column.

### 2. Add offense team color to SSE possession payload

**File:** `src/pinwheel/core/presenter.py` — `_present_game()` (line 261)

Pass `colors` dict into `_present_game()`. For each possession, derive `offense_color`:

```python
# In _present_game signature, add colors parameter:
async def _present_game(
    game_idx, game_result, event_bus, state,
    quarter_replay_seconds, names,
    colors,  # NEW
) -> None:
```

In the possession loop, add to `play_dict`:
```python
"offense_color": colors.get(possession.offense_team_id, ("#888",))[0],
```

**File:** `src/pinwheel/core/presenter.py` — `_present_full_game()` (line 224)

Update the call to `_present_game()` to pass `colors`:
```python
await _present_game(
    game_idx, game_result, event_bus, state,
    quarter_replay_seconds, names, colors,
)
```

### 3. Color server-rendered play lines

**File:** `templates/pages/arena.html` — server-rendered play lines (lines 49-55)

Add inline `style="color: {{ play.offense_color }};"` to each play line:

```html
{% for play in game.recent_plays|reverse %}
<div class="live-play-line" style="color: {{ play.offense_color }};">
  <span class="live-play-clock">{{ play.game_clock }}</span>
  {{ play.narration }}
  {% if play.points_scored > 0 %}<span class="live-play-pts">+{{ play.points_scored }}</span>{% endif %}
</div>
{% endfor %}
```

### 4. Color JS-created play lines

**File:** `templates/pages/arena.html` — JS `presentation.possession` handler (lines 246-253)

Apply `offense_color` from the SSE data to dynamically created play lines:

```javascript
var line = document.createElement('div');
line.className = 'live-play-line';
if (d.offense_color) line.style.color = d.offense_color;
```

### 5. CSS: remove first-child override brightness

**File:** `static/css/pinwheel.css` — `.live-play-line:first-child` (line 2455)

Remove or update the `:first-child` rule that makes the first play brighter white. With team colors applied, this override would fight the team color. Change it to use `font-weight: 600` only (keep the emphasis but don't override color):

```css
.live-play-line:first-child {
  font-weight: 600;
}
```

## Files Modified

1. `templates/pages/arena.html` — layout fix + colored play lines (server + JS)
2. `src/pinwheel/core/presenter.py` — pass colors to `_present_game()`, add `offense_color` to `play_dict`
3. `static/css/pinwheel.css` — update `.live-play-line:first-child` rule

## Verification

1. `uv run pytest -x -q` — all tests pass
2. Start local server, seed data, advance a round, verify:
   - Live zones fill available width (no half-blank row from section-title)
   - Each play line is colored with the offense team's primary color
   - Thorns plays are red, Breakers plays are cyan, etc.
   - Colors work for both server-rendered (page reload) and JS-created (SSE) play lines
