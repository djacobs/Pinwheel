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

---

## Completed — Session 21 (Governance + Rules + Copy)

### 23. [DONE] Governance page auth gate removed
**Problem:** `/governance` redirected all unauthenticated visitors to Discord OAuth login. The governance audit trail — proposals, votes, outcomes — was invisible to anyone not logged in. This contradicts the transparency principle: the whole point is that governance is visible.

**Fix:** Removed the auth gate (`if current_user is None and oauth_enabled: return RedirectResponse`). Page is now publicly viewable. Proposing and voting still require Discord auth via bot slash commands. Added a hero section explaining the governance flow.

### 24. [DONE] Rules page redesigned from config dump to tiered card layout
**Problem:** Rules page was iterating `ruleset.items()` and displaying raw snake_case parameter names (e.g., `quarter_possessions: 25`). No context, no descriptions, no sense of what's changeable or what the ranges are. This is the raison d'etre of the project — the rules players can change — and it looked like a config file.

**Fix:** Created `RULE_TIERS` metadata mapping all 29 RuleSet parameters to human-readable labels, descriptions, and 4 tiers:
1. **Game Mechanics** (13 rules) — "The core numbers that define how basketball works"
2. **Agent Behavior** (9 rules) — "How players interact with the court and crowd"
3. **League Structure** (5 rules) — "Season format, scheduling, and playoffs"
4. **Meta-Governance** (2 rules) — "The rules about rules"

Each rule card shows: label, current value (mono font, accent color), description, valid range (extracted from Pydantic field metadata at runtime). Changed rules get a highlighted border and accent. "Changed by the Community" strip at top when any rules differ from defaults. Change history timeline at bottom. CTA linking to the play page.

### 25. [DONE] Player-centric copy across all pages
**Problem:** Copy was AI-centric and passive. User feedback: "This copy is terrible!!!! The players rewrite the rules." The taglines positioned the AI as the protagonist instead of the players.

**Fix:** Rewrote all taglines and explanatory text to center the player:
- **Home:** "3-on-3 basketball where the players rewrite the rules. Propose changes. Vote with your team. Shape the game. The AI is your mirror — but the fates are yours."
- **Rules:** "Every number below shapes how this league plays. Between rounds, you propose changes in plain English and vote with your team."
- **Governance:** "Every rule change starts with a proposal from a player."
- **How It Works cards:** Player as subject of every sentence.

---

## Completed — Session 23 ("How to Play" Onboarding)

### 26. [DONE] "How to Play" page — the most important page for conversion
**Problem:** A visitor who thinks "This is cool, how do I play?" has nowhere to go. The site explains *what* Pinwheel is but never explains *how to participate*, *when games happen*, or *what to expect*. No onboarding path exists.

**Fix:** Created `/play` page with comprehensive onboarding:

**The Rhythm** — 4-step cycle explaining the round structure: Games Play Out → Governance Window Opens → Mirror Reflects → Rules Change. Each step gets a numbered card with a detailed description. Shows current pace ("Rounds advance every 5 minutes") and governance window duration.

**What You Do** — 4-card grid answering "what's my job?": Watch (follow games, study agents), Propose (use /propose in Discord), Vote (team votes, strategy matters), Reflect (read the mirrors).

**Discord Commands** — Reference list: `/join`, `/propose`, `/vote`, `/strategy`, `/tokens`, `/trade`. Each with one-line description. This is the practical "how do I actually do things" section.

**FAQ** — 5 questions that real visitors would ask: What's the Elam Ending? What happens when a proposal passes? Can I break the game? What does the AI actually do? Is this free?

**League status bar** — Live stats (rounds played, teams, agents, games) pulled from the database. Makes it feel alive.

**Discord join buttons** — Appear when `discord_invite_url` is configured. Top and bottom of page.

### 27. [DONE] "Play" link in navigation
**Problem:** No way to find the onboarding page from the nav.

**Fix:** Added "Play" as the first nav link, styled in cyan (`var(--accent-governance)`) to visually distinguish it from other nav items. Hover gets a subtle cyan background.

### 28. [DONE] Home page join CTA
**Problem:** Home page had a "How Pinwheel Works" explainer and an "Explore" grid but no call to action. A visitor who understands the game but wants to join has to figure it out themselves.

**Fix:** Added a join CTA card between "How It Works" and "Explore":
- Gradient top border (highlight → governance → score) for visual prominence
- "Want to play?" heading
- Brief description: "Join the Discord, pick a team, and start rewriting the rules."
- Discord join button (when configured) + "Learn how to play" link
- "How It Works" section now links to `/play` via "How to play →"

### 29. [DONE] `discord_invite_url` configuration
**Problem:** No way to configure a Discord server invite link. The join buttons need somewhere to point.

**Fix:** Added `discord_invite_url` setting to `Settings` (empty default, configured via `DISCORD_INVITE_URL` env var). Passed to all templates via `_auth_context()`. When set, Discord join buttons appear on the play page and home page CTA. When empty, the CTA gracefully degrades to showing explore links instead.
