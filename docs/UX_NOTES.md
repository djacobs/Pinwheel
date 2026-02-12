# UX Notes — Pinwheel Fates

Collected feedback for review. Items marked [DONE] have been implemented.

## Key Files

| Layer | File | What it controls |
|-------|------|-----------------|
| **Stylesheet** | `static/css/pinwheel.css` | All CSS — layout, colors, typography, components |
| **Base template** | `templates/base.html` | Page shell, nav, footer, shared head/scripts |
| **Page templates** | `templates/pages/*.html` | Individual page markup (arena, standings, game, etc.) |
| **Page routes** | `src/pinwheel/api/pages.py` | Server-side data loading for all HTML pages |
| **Eval dashboard** | `src/pinwheel/api/eval_dashboard.py` | Admin dashboard route + data |
| **Static JS** | `static/js/` | HTMX extensions, client-side behavior |

---

## Arena Page

### 1. [DONE] Elam banner should show the winning play, not just the target
Every game gets an Elam ending, so "ELAM ENDING — TARGET: 27" is noise. What's meaningful is **who hit the winning shot and how they did it**: the player name, the play type (three-pointer, drive, mid-range), and possibly the move/call. The final score is always the target — the story is in who got there.

**Fix:** Banner now reads "GAME WINNER: Kai Ripley — three-pointer" instead of "ELAM ENDING — TARGET: 27". Falls back to old banner if no play-by-play data.

### 2. [DONE] Quarter scores should be a proper table, not a flat row
Previously rendered as: `Q1 Q2 Q3 Q4  8-7  1-0  0-7  16-13`

Now a proper table with:
- Header row: Q1, Q2, Q3, ELAM, T
- One row per team (3-letter abbreviation)
- The team with the higher score that quarter is **bold**
- Total column with winner bolded

### 3. [DONE] Typography and visual density overhaul
**Problem:** The arena page looked cramped and generic. System fonts, tight spacing, aggressive gradient Elam banners, quarter-score columns smashed together, scores not prominent enough. Compared unfavorably to reference sites like 3-on-3-fans.fly.dev in readability and information density.

**Changes (Day 6, Session 14):**

**Typography:**
- Switched from system-ui to **Inter** (body) and **JetBrains Mono** (numbers/code) via Google Fonts
- Added `-webkit-font-smoothing: antialiased` for crisp rendering
- Tuned `line-height` from 1.5 → 1.6 for body text
- Reduced heading sizes slightly (h1: 2rem → 1.75rem, h2: 1.5rem → 1.375rem) for better proportion

**Layout:**
- Narrowed `max-width` from 1200px → 960px for better line lengths and focus
- Increased container padding from 1rem → 1.5rem
- Added more page-content padding (1.5rem → 2rem top, 3rem bottom)
- Added `.page-header` component with subtitle support

**Arena game cards:**
- Elam banner: replaced loud gradient (`linear-gradient(135deg, ...)` + pulse animation) with subtle `rgba(255, 107, 53, 0.12)` background tint. Text color changed to `--accent-elam` instead of white. Removed animation.
- Elam banner text: changed from ALL CAPS to title case for readability
- Scores: increased from `1.25rem` to `1.75rem` on arena cards
- Score winner color: changed from gold (`--accent-score`) to white (`--text-primary`) — the winner should be prominent, not colored
- Score loser: faded to `--text-muted` with lighter weight
- Team name winner: now white text instead of gold
- Card internal padding: increased from `1rem` to `1.25rem–1.5rem`
- Card hover: added subtle `translateY(-1px)` lift

**Quarter scores table:**
- Increased cell padding from `0.35rem 0.6rem` to `0.4rem 0.75rem` — columns no longer touch
- Removed aggressive `border-right` between cells, replaced with `border-bottom` on header only
- Reduced header font size for better hierarchy
- "FINAL" divider → "Final" (title case, less aggressive)

**Standings table:**
- Increased cell padding from `0.5rem 0.75rem` to `0.6rem–0.75rem × 1rem`

**Global:**
- `--text-secondary` brightened from `#8888aa` to `#9898b4` for better readability
- `--text-muted` brightened from `#555577` to `#606080`
- Status badge backgrounds reduced to 0.15 opacity (from 0.2)
- Mirror content color changed to `--text-secondary` for softer feel
- Border radius increased from 6px to 8px

---

## Standings Page

### 4. [DONE] Page header with subtitle
Added `.page-header` with team count and league name subtitle.

### 5. [DONE] Multi-round arena view
**Problem:** Arena only showed the latest round. With 8+ games/day, users need to catch up on what they missed.

**Fix:** Arena now shows up to 4 recent rounds (newest first), each with its own section header ("Round 3") and simulation mirror. Data structure changed from flat `games` list to `rounds` list of `{round_number, games, mirror}`.

### 6. [DONE] Vivid Elam banner narration
**Problem:** "Game Winner: Wren Silvas — mid-range jumper" is boring. Shot descriptions should be exciting and specific.

**Fix:** Created `core/narrate.py` with `narrate_winner()` function. 15 templates across 3 shot types with move-specific flourishes. Now reads: "Wren Silvas hits the mid-range dagger from the elbow" or "Ember Kine drains the dagger three" or "Steel Voss attacks the rim — finishes through contact — leaving the defender on the floor".

### 7. [DONE] Narrative mock mirrors (not generic stats)
**Problem:** "Round 3 delivered 2 games with 128 total points. The Elam Ending activated in 2 game(s), adding dramatic tension." The Elam always activates — that's not remarkable. Generic stat summaries tell you nothing.

**Fix:** Rewrote `generate_simulation_mirror_mock()` to use team names, scores, margins. Close games get nail-biter language, blowouts get domination language. Specific and remarkable, not generic.

### 8. [DONE] Cache busting on static assets
**Problem:** Deploying CSS/JS changes didn't take effect for users with cached assets.

**Fix:** Added `APP_VERSION` to `config.py` (git short hash). All `<link>` and `<script>` tags in `base.html` now include `?v={{ app_version }}`.

---

## Completed — Session 15

### 9. [DONE] Rich play-by-play on game detail page
**Problem:** Game detail page showed raw "Three Point — MADE". Boring and impersonal.

**Fix:** Wired `narrate_play()` into `game_page()` in `pages.py`. Builds an agent-name cache from box scores, then enriches each play dict with a `narration` field using player names, defender names, action, move, and possession number as seed. Template now shows `{{ play.narration }}` instead of raw action/result.

### 10. [DONE] Home page needs more life
**Problem:** Landing page was just four card links and an optional mirror.

**Fix:** Added league snapshot to home page — shows standings leader (team name, W-L record) and total games played. Data loaded via `_get_standings()` in `home_page()`. Template displays it as a compact mono line between the tagline and nav cards.

### 11. [DONE] Team pages need win/loss record in header
**Problem:** Team page showed roster and attributes but no record.

**Fix:** `team_page()` now loads team standings and league name. Template shows W-L record + ordinal position ("1st in Portland 3v3 League") below the team name. Also added a full Record card with WINS/LOSSES/DIFF below the roster.

### 12. [DONE] Mobile nav needs hamburger menu
**Problem:** Nav links wrapped awkwardly on narrow viewports.

**Fix:** Added a `<button class="nav-toggle">` with three CSS lines (no images/unicode). Toggles `.open` class on click. Below 768px, nav links collapse into a vertical menu. `.nav-toggle` uses CSS transforms for X animation. Desktop layout unchanged (`display: contents` on nav-links wrapper).

### 13. [DONE] Game detail page — quarter table matches arena style
**Problem:** Game detail page used `box-score-table` class instead of `quarter-table`.

**Fix:** Changed to `quarter-table` class with `qt-team`, `qt-bold`, `qt-total` classes matching the arena template exactly. Added Elam header label and conditional bold styling per quarter.

### 14. [DONE] Governance page — empty state call-to-action
**Problem:** Empty state didn't explain how to submit proposals.

**Fix:** Changed empty state text to "No proposals yet. Use the `/propose` command in Discord to submit a rule change." with `/propose` in `<code>` tags.

### 15. [DONE] Mirrors page — long text formatting
**Problem:** `white-space: pre-wrap` made long AI reflections hard to read.

**Fix:** Changed `.mirror-content` to `white-space: normal` with `word-wrap: break-word` and `max-width: 720px` for comfortable reading width.

---

## Completed — Session 18 (Spider Charts + Agent Pages)

### 16. [DONE] Spider charts replace bar charts on team pages
**Problem:** Team pages showed agents with horizontal attribute bars — functional but flat. Bars don't communicate the *shape* of a player. A sharpshooter and an enforcer both have 9 colored bars, but they look the same at a glance.

**Fix:** Created SVG nonagon spider charts rendered server-side in Python (no JS dependency). 9 axes at 40° intervals: scoring, passing, defense, speed, stamina, iq, ego, chaotic_alignment, fate. Each agent gets a distinctive silhouette — sharpshooters are spiked toward scoring/speed, enforcers bulge at defense/stamina.

**Implementation:** `api/charts.py` computes geometry (angle = `i * 40 - 90`, point = `center + r*cos(angle), center + r*sin(angle)` where `r = value/100 * max_radius`). Jinja2 macro in `templates/components/spider_chart.html` renders inline SVG. League-average shadow polygon (dashed, `rgba(255,255,255,0.04)`) sits behind the agent's colored polygon. Color-coded vertex dots and 3-char labels with values. Horizontal legend below.

**Team page:** 220px spider charts per agent. Agent names are now clickable links to individual agent pages.

### 17. [DONE] Individual agent pages (`/agents/{agent_id}`)
**Problem:** No way to see an individual player's full profile — stats, game history, or backstory.

**Fix:** Full agent profile page with sections:
1. **Header** — Name, archetype badge, team link with color dot
2. **Bio** — Backstory text (italic, secondary color). HTMX edit button appears for team governors.
3. **Two-column** — Full-size spider chart (280px, with league-average shadow) + season averages card (PPG, APG, SPG, TOPG, FG%, 3P%, FT%, games played)
4. **Game log** — Table: RD, OPP, PTS, FG, 3P, FT, AST, STL, TO. Each row links to the game detail page.
5. **Moves** — Agent's special moves (name, trigger, effect)

**Bio editing:** HTMX form replaces bio text inline. `POST /agents/{agent_id}/bio` updates `AgentRow.backstory`. Auth-gated: logged-in user must be governor on agent's team (via `get_player_enrollment()`).

### 18. [DONE] Season averages computation
**Problem:** No way to see aggregated performance across games.

**Fix:** `compute_season_averages()` in `charts.py` takes a list of box score dicts and returns PPG, APG, SPG, TOPG, FG%, 3P%, FT%, games_played. Handles zero-division for shooting percentages. Displayed in a `.stats-grid` on the agent page with mono font accent color values.

---

## Completed — Session 19 (Simulation Tuning)

### 19. [DONE] Shot clock violation mechanic
**Problem:** The shot clock was cosmetic — `shot_clock_seconds=15` existed in RuleSet but was never enforced. Zero shot clock turnovers in any game.

**Fix:** Added `check_shot_clock_violation()` in `possession.py`. Probability based on defensive scheme pressure (press 2×, man_tight 1.5×, zone 0.5×), handler fatigue, and IQ. Base rate 2%, capped at 0.5%–12%. Wired into `resolve_possession()` after the turnover check. Added 4 narration templates for shot clock violations in `narrate.py`.

### 20. [DONE] Scoring rebalance to match Unrivaled range
**Problem:** Games scoring 34-33 instead of the 64-90 range seen in Unrivaled 3v3. Root cause: stamina collapsed to 0.1 floor by mid-game, destroying points-per-possession. The Elam period then ground through 100+ possessions at ~0.39 PPP.

**Fix:**
- **Stamina:** Reduced base drain 0.012→0.007, offense drain 0.005→0.003, raised floor 0.1→0.15, improved recovery rate
- **Inter-quarter recovery:** Added `quarter_break_stamina_recovery=0.15` for Q1/Q3 breaks
- **Rule defaults:** quarter_possessions 15→25, halftime_recovery 0.25→0.40, elam_margin 13→25
- **Safety cap:** 200→300 possessions
- **Result:** 20-game verification → avg 65-67 pts/team, ~124 possessions. Natural variance 40-90.

### 21. [DONE] Elam quarter label fix
**Problem:** Quarter score headers read "Q1 / Q2 / Q3 / Elam" — confusing for viewers who expect 4 quarters.

**Fix:** Changed to "Q1 / Q2 / Q3 / Q4" in both `arena.html` and `game.html`. The Elam mechanism (first to target score) still applies in Q4 — it's just not called out in the header since it always activates.

---

## Completed — Session 20 (Home Page Redesign)

### 22. [DONE] Home page redesigned as living league dashboard
**Problem:** Home page was a centered title, one-line snapshot, four nav cards, and an optional mirror. It felt like a giant nav menu — no sense that this is a living game with active competition, evolving rules, and AI observation.

**Fix:** Complete redesign as a dashboard that tells the story of the league:

1. **Hero** — "PINWHEEL FATES" with a radial glow accent. Below: descriptive tagline ("A living 3-on-3 basketball league where the players govern the rules. Every game is auto-simulated. Every rule can be changed. The AI watches everything."). Animated green pulse dot showing "Season 1 · Round 3 · 6 games played" in a pill badge.

2. **Latest Results** — Score cards for the most recent round. Each card shows team color dots, team names (winner bolded), final scores (winner in gold), the game-winning play narrated ("Briar Ashwood buries the three from deep — ballgame"), and an Elam target badge. Cards link to full game detail.

3. **Two-column layout** — Left: mini standings table (rank, color dot, team name, W-L record, +/- differential, each row links to team page). Right: "The AI Sees" — latest simulation mirror in a purple-bordered card.

4. **Coming Up** — Next round's scheduled matchups with team color dots. Appears only when there are unplayed rounds in the schedule.

5. **How Pinwheel Works** — 4-card explainer grid (numbered 01-04): "Games Simulate" (highlight), "You Govern" (cyan), "AI Reflects" (purple), "Rules Evolve" (gold). Each with a short description. Helps new visitors understand the game.

6. **Explore** — Compact icon grid: Arena, Governance, Rules, Mirrors. Each with a unicode icon, label, and one-line description. Replaces the old oversized nav cards.

**CSS additions (~300 lines):**
- `.home-hero` with `.hero-glow` (radial gradient accent)
- `.hero-pulse` pill with animated `.pulse-dot` (green blink)
- `.section-title-bar` with colored `.section-title-badge` variants
- `.score-card` with `.sc-team`, `.sc-dot`, `.sc-score`, `.sc-play` (game-winning play narration), `.sc-elam` (Elam target pill)
- `.home-columns` two-column grid (stacks on mobile)
- `.mini-standings` with `.ms-row`, `.ms-rank`, `.ms-dot`, `.ms-name`, `.ms-record`, `.ms-diff`
- `.home-mirror` with purple left border accent
- `.upcoming-strip` with `.upcoming-card`, `.uc-team`, `.uc-vs`
- `.how-grid` (4-col, 2-col on tablet, 1-col on mobile) with `.how-card`, `.how-number`, `.how-title`, `.how-desc`
- `.explore-grid` (4-col) with `.explore-card`, `.explore-icon`, `.explore-label`, `.explore-desc`
