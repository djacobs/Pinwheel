# Team Color Schemes

## Context

Teams have a single `color` field (hex string) but it's only used as tiny dots on the home page. The arena scoreboards, game detail pages, live zones, and standings are all monochrome — no visual team identity. The user wants **two complementary colors per team** to make these surfaces more interesting.

## Current State

**Production teams:**
- Rose City Thorns: `#e94560` (red)
- Burnside Breakers: `#53d8fb` (cyan)
- St. Johns Herons: `#b794f4` (purple)
- Hawthorne Hammers: `#f0c040` (gold)

**DB schema:** `teams` table has `color VARCHAR(7)`, no secondary color column.

**Where colors ARE used:** Home page score cards (small dots), home page standings (small dots), team page header (dot), hooper page (dot), spider charts (polygon fill).

**Where colors are NOT used:** Arena game cards, arena live zones, game detail page, standings page, upcoming schedule.

## Changes

### 1. Add `color_secondary` column to teams

**File:** `src/pinwheel/db/models.py`
- Add `color_secondary: Mapped[str] = mapped_column(String(7), default="#ffffff")`

**File:** `src/pinwheel/main.py`
- Add inline migration: `_add_column_if_missing(conn, "teams", "color_secondary", "VARCHAR(7) DEFAULT '#ffffff'")`

**File:** `src/pinwheel/models/team.py`
- Add `color_secondary: str = "#ffffff"` to `Team` model

**Production data — complementary pairs:**
- Rose City Thorns: `#e94560` / `#1a1a2e` (red on dark navy)
- Burnside Breakers: `#53d8fb` / `#0a2540` (cyan on deep blue)
- St. Johns Herons: `#b794f4` / `#1e1033` (purple on dark plum)
- Hawthorne Hammers: `#f0c040` / `#2a1f00` (gold on dark brown)

### 2. Flow colors through arena page

**File:** `src/pinwheel/api/pages.py` — `arena_page()`

Build a `team_colors` dict alongside the existing `team_names` dict:
```python
team_colors: dict[str, tuple[str, str]] = {}
# When fetching teams:
team_colors[tid] = (t.color or "#888", t.color_secondary or "#1a1a2e")
```

Add to each game dict:
```python
"home_color": team_colors.get(g.home_team_id, ("#888", "#1a1a2e"))[0],
"home_color2": team_colors.get(g.home_team_id, ("#888", "#1a1a2e"))[1],
"away_color": team_colors.get(g.away_team_id, ("#888", "#1a1a2e"))[0],
"away_color2": team_colors.get(g.away_team_id, ("#888", "#1a1a2e"))[1],
```

Add to live_round games and upcoming_games dicts too.

### 3. Flow colors through game detail page

**File:** `src/pinwheel/api/pages.py` — `game_page()`

Already fetches `home_team` and `away_team`. Just extract and pass:
```python
"home_color": home_team.color if home_team else "#888",
"home_color2": home_team.color_secondary if home_team else "#1a1a2e",
"away_color": away_team.color if away_team else "#888",
"away_color2": away_team.color_secondary if away_team else "#1a1a2e",
```

### 4. Flow colors through LiveGameState and SSE events

**File:** `src/pinwheel/core/presenter.py`

Add to `LiveGameState`:
```python
home_team_color: str = "#888"
home_team_color2: str = "#1a1a2e"
away_team_color: str = "#888"
away_team_color2: str = "#1a1a2e"
```

In `_present_full_game()`, when creating LiveGameState, populate colors from `name_cache` (extend name_cache to carry colors, or add a separate `color_cache` param).

Add `home_team_color`/`away_team_color` to `game_starting` SSE event payload so JS can apply them dynamically.

### 5. Arena template — colored game cards

**File:** `templates/pages/arena.html`

**Completed game cards:** Add team color accents. Use primary color as a left-border or background tint on team name rows. Winner's color is vivid, loser's is muted.

```html
<div class="team-score-block" style="border-left: 3px solid {{ game.home_color }};">
  <div class="team-name ...">{{ game.home_name }}</div>
  <div class="team-score ...">{{ game.home_score }}</div>
</div>
```

**Live zone (server-rendered):** Color-coded team names and score backgrounds.

```html
<span class="live-team-name" style="color: {{ game.home_color }};" ...>
```

**Live zone (JS-created):** Apply colors from SSE `game_starting` event data.

**Upcoming games:** Color dots like home page.

### 6. Game detail template — colored header and box scores

**File:** `templates/pages/game.html`

**Game header:** Each team's section gets a subtle background tint using their secondary color, with primary color for the team name and score.

```html
<div class="game-team" style="background: {{ home_color2 }}; border-left: 4px solid {{ home_color }};">
  <div class="game-team-name" style="color: {{ home_color }};">{{ home_name }}</div>
  <div class="game-team-score">{{ game.home_score }}</div>
</div>
```

**Box score tables:** Team section headers use primary color as accent.

**Quarter score table:** Winning team's quarter score uses their primary color.

### 7. Standings template — team colors

**File:** `templates/pages/standings.html`

Add color dots next to team names (same pattern as home page mini standings). Pass colors from `standings_page()`.

### 8. Update production DB

Run SQL to set secondary colors for existing teams:
```sql
UPDATE teams SET color_secondary = '#1a1a2e' WHERE name = 'Rose City Thorns';
UPDATE teams SET color_secondary = '#0a2540' WHERE name = 'Burnside Breakers';
UPDATE teams SET color_secondary = '#1e1033' WHERE name = 'St. Johns Herons';
UPDATE teams SET color_secondary = '#2a1f00' WHERE name = 'Hawthorne Hammers';
```

### 9. Seeding — update demo_seed.py

**File:** `scripts/demo_seed.py`

Add `color_secondary` to the team definitions so new seeds get both colors.

## Verification

1. `uv run pytest -x -q` — all tests pass
2. Start local server, seed data, verify:
   - Arena page: game cards have colored left borders per team
   - Game detail: header has team-colored sections
   - Live zones: team names in team colors
   - Standings: color dots next to names
3. Deploy, verify on production
