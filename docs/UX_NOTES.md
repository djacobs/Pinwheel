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

---

## Completed — Session 24 (Discord Infrastructure + Safety)

### 30. [DONE] Rules page "propose anything" wild card section
**Problem:** The rules page made it seem like players are just tweaking config values — change shot clock from 15 to 12, etc. It didn't communicate the game's real promise: you can propose *anything*. Make the floor lava. Introduce a maximum height rule. Switch to baseball. The AI interprets whatever you write.

**Fix:** Added "Beyond the Numbers" section below the tiered rule cards:
- 6 example wild proposals in a 2-column responsive grid (e.g., "Make the floor lava", "Add a maximum height rule", "Switch to baseball", "Players can only score with their off hand")
- Flow strip showing the 4-step process as horizontal pills: "You propose → AI interprets → Your team votes → The game changes"
- Bridge text: "The parameters above are the starting point. But proposals aren't limited to what's already on the board."
- New CSS: `.rules-wildcard`, `.wildcard-inner`, `.wildcard-examples` (2-col grid), `.wildcard-flow` (horizontal pill steps)

### 31. [DONE] Discord bot UX — team-specific game result framing
**Problem:** Game results posted to `#play-by-play` used neutral framing ("Team A 58 - Team B 48"). In team channels, results should feel personal — your team won or lost.

**Fix:** Added `build_team_game_result_embed()` — win/loss framing per team. Victory embeds are green with "Victory! [Team] wins!", defeat embeds are red with "Defeat. [Team] falls." Score always shows your team first. Posted to both team channels after every game via `_send_to_team_channel()`.

### 32. [DONE] Discord bot UX — `/join` shows team list when no argument given
**Problem:** `/join` with no team name gave an error. New players don't know what teams exist.

**Fix:** Made the team parameter optional. No argument shows an embed listing all teams with governor counts and "needs players!" markers for teams below capacity. Added autocomplete for team names so players can tab-complete.

---

## Completed — Session 26 (Game Clock Display)

### 33. [DONE] Game clock shown in play-by-play
**Problem:** Play-by-play display showed only "Q1", "Q2", etc. With clock-based quarters now implemented, each possession consumes real game time — but the viewer couldn't see it. Real basketball broadcasts always show the clock.

**Fix:** Added `game_clock` field to `PossessionLog`. Timed quarter possessions display as "Q1 9:32" (minutes:seconds remaining). Elam ending (Q4) is untimed, so those possessions show just "Q4" with no clock. Widened `.pbp-quarter` from `min-width: 2rem` to `min-width: 5rem` to fit the clock text. Template uses `{% if play.game_clock %}` conditional to only render the clock when present.

### 34. [DONE] Remove "Rules" from nav
**Problem:** Rules page was a nav item, but its content was dense parameter tables — not something players need in the main navigation. Better to surface the interesting parts (wild card proposals, key parameters) on the Play page and keep the full rules accessible via direct URL.

**Fix:** Removed the Rules link from `base.html` nav. The `/rules` route still works — FAQ and other pages still link to it. The nav is now: Play, Arena, Standings, Governance, Mirrors.

### 35. [DONE] Play page hero cleanup + wild card + key params
**Problem:** Play page hero had "The AI watches and reflects — but every decision is yours." — centering the AI when the page should center the player. Also, the best copy about proposing anything was buried on the rules page.

**Fix:** Removed the AI sentence. Migrated the "Beyond the Numbers" wild card section (6 example proposals + flow strip) from rules page to play page. Added a "Current Game Parameters" section showing 6 key rules (shot clock, three-point value, quarter length, Elam margin, free throw value, foul limit) in the existing `rule-card` grid. Shows community change count when rules have been modified. `play_page()` now loads the current RuleSet and passes `key_params` to the template.

### 36. [DONE] Richer mirror output
**Problem:** Mirrors were too brief (2-3 paragraphs) and didn't mention specific rule changes, governance outcomes, or timing of next governance window. Governors couldn't easily see what changed and what it meant.

**Fix:** Updated all 3 mirror prompt templates: simulation (3-5 paragraphs), governance (3-5 paragraphs), private (2-3 paragraphs). Added prompt rules for referencing specific parameter changes with old/new values, summarizing governance window outcomes, and mentioning next window timing. Increased `max_tokens` from 800 to 1500. Enriched `governance_data["rules_changed"]` with actual `RuleChange` data from `rule.enacted` events. Updated mock generators to show detailed parameter changes.

### 37. [DONE] Agent → Hooper rename across all UI
**Problem:** "Agent" reads as "AI agent" in an AI project, confusing visitors. These are simulated basketball players, not AI actors. "Hooper" is basketball slang — unambiguous, fun, domain-appropriate.

**Fix:** System-wide rename across all user-facing surfaces: route `/agents/{id}` → `/hoopers/{id}`, template `agent.html` → `hooper.html`, all template variables (`{{ agent.name }}` → `{{ hooper.name }}`), CSS classes (`.agent-card` → `.hooper-card`, `.agent-name` → `.hooper-name`, etc.), Discord bot commands (`/trade-agent` → `/trade-hooper`), embed titles ("Agent Trade Proposal" → "Hooper Trade Proposal"), rules tier label ("Agent Behavior" → "Hooper Behavior"), and all user-facing text referencing "agents" now says "hoopers". Box score API key `agent_id` → `hooper_id`.

### 38. [DONE] Play/rules page "propose anything" copy
**Problem:** "You are not limited to tweaking config values. You can propose anything." felt awkward and indirect — too much preamble before the exciting part.

**Fix:** Changed to "Want to change the rules? Propose new ones — anything." Shorter, more direct, puts the action first. Updated on both `/play` and `/rules` pages.

---

## Completed — Session 29 (Live Play-by-Play Streaming)

### 39. [DONE] Arena live zone — real-time game streaming via SSE
**Problem:** The simulation engine ran instantly and showed final scores immediately — spoiling results. Players never experienced the drama of a game unfolding in real time. The presenter layer existed but the frontend didn't consume its events.

**Fix:** Added a hidden `<div id="live-zone">` at the top of the arena page that activates during live presentations. JavaScript opens an `EventSource('/api/events/stream')` and handles four event types:
- `presentation.game_starting` — shows the live zone with team names, resets scores to 0-0
- `presentation.possession` — updates live scores, quarter indicator, game clock; prepends narrated play-by-play lines
- `presentation.game_finished` — shows "FINAL" status with final score
- `presentation.round_finished` — hides live zone, auto-reloads page after 2 seconds

**Visual design:** Pulsing cyan border (`@keyframes live-pulse`), blinking "LIVE" badge (`@keyframes live-blink`) in red, large mono scoreboard with team names, scrolling play-by-play feed with quarter/clock labels. "Advance Round" button visible in dev/staging only.

### 40. [DONE] Presenter enrichment — names and narration in SSE events
**Problem:** Presenter events only contained entity IDs (`ball_handler_id`, `offense_team_id`). The frontend would have needed a separate lookup to show player/team names.

**Fix:** Added `name_cache` parameter to `present_round()` and `_present_game()`. The scheduler runner builds a name cache from `teams_cache` (mapping team IDs and hooper IDs to display names). Each `presentation.possession` event now includes `ball_handler_name`, `offense_team_name`, and a `narration` field generated server-side via `narrate_play()`. Game starting/finished events include `home_team_name` and `away_team_name`.

### 41. [DONE] In-process round advance endpoint
**Problem:** `demo_seed.py step` ran in a separate process, so its EventBus events never reached SSE clients connected to the web server. No way to trigger a round and see it stream live.

**Fix:** Added `POST /api/pace/advance` endpoint that triggers `tick_round()` within the server process via `asyncio.create_task()`. Forces `presentation_mode="replay"` with demo-friendly timing (15s per quarter, 5s between games). Returns 409 if a presentation is already active. Added `GET /api/pace/status` for polling presentation state.

---

## Completed — Session 30 (SSE Heartbeat Fix)

### 42. [DONE] SSE stream frozen behind Fly.io reverse proxy
**Problem:** The arena live zone never appeared on the deployed site. The `EventSource` connection opened but no events reached the browser — the page was completely static during live presentations. The SSE generator yielded nothing until the first EventBus event, and Fly.io's proxy buffered the response waiting for body data.

**Fix:** Added an immediate `: connected\n\n` SSE comment when the stream opens, flushing bytes through the proxy so the browser transitions from "connecting" to "open" state. Added 15-second `: heartbeat\n\n` keep-alive comments to prevent proxy timeout during quiet periods. Added `es.onopen` and `es.onerror` console logging to the frontend for future debugging.

---

## Completed — Session 31 (Live Arena Redesign)

### 43. [DONE] Server-rendered live games on arena page
**Problem:** Page reload during a live game showed nothing — all live state was client-side only (in the browser DOM via SSE). Server restart (deploy) killed the presentation entirely. Users who arrived mid-game saw a static page with no indication games were in progress.

**Fix:** Arena page now server-renders live game state from `PresentationState`. When a presentation is active, the template renders a "Live — Round N" section with per-game cards showing: LIVE/FINAL badge, quarter indicator (Q1-Q4), game clock, team names and scores, recent narrated plays, and top scorers per team with links to hooper pages. On page reload, the full current state appears immediately — SSE then picks up from that point. When no presentation is active, a hidden container sits ready for SSE to populate dynamically.

### 44. [DONE] Game leaders in live arena zones
**Problem:** Live game cards showed scores and play-by-play but no player stats. Viewers couldn't see who was having a big game or discover standout hoopers during live action.

**Fix:** Added leaders section to each live game zone showing the top scorer per team. Leaders are computed from pre-calculated box scores via `_compute_leaders()` and included in the `game_finished` event payload. Each leader name links to `/hoopers/{id}`. The JS `game_finished` handler also renders leaders with hooper links for users who were watching live.

### 45. [DONE] Hooper links in game detail box score and play-by-play
**Problem:** Game detail pages showed hooper names as plain text in the box score table and play-by-play. No way to navigate from a standout performance to the hooper's profile page.

**Fix:** Box score hooper names are now `<a href="/hoopers/{{ p.hooper_id }}">` links. Play-by-play lines have a `data-handler` attribute with the handler's hooper ID, and a click handler navigates to the hooper page. Both changes make the game detail page a discovery surface for meeting individual hoopers.

### 46. [DONE] "Up Next" schedule section on arena
**Problem:** Arena page showed completed games and live games but gave no preview of what's coming. Players couldn't see upcoming matchups to plan their viewing.

**Fix:** Added "Up Next — Round N" section below live/completed games showing the next unplayed round's matchups. Each matchup displays team color dots and team names in a horizontal strip. Query added to `arena_page()` to fetch the next round's schedule entries.

### 47. [DONE] Foul narration distinguishes made vs missed free throws
**Problem:** All foul outcomes used the same narration template regardless of whether free throws were made or missed. "Flash draws the foul" was the same whether they scored 2 or 0.

**Fix:** Foul narration now branches: `points > 0` produces "hits N from the stripe", `points == 0` produces "misses from the stripe". Added 2 new foul template variations for variety.

### 48. [DONE] Discord Elam noise removed, big-plays routing fixed
**Problem:** Discord embeds showed "Elam Ending activated!" on every game — since Elam always activates, this was pure noise. The big-plays channel routing used `is_elam` as its condition, meaning every single game went to `#big-plays`, defeating the purpose of having a highlights channel.

**Fix:** Replaced "Elam Ending activated!" with "Elam Target: {score}" (actually informative). Changed big-plays routing from `is_elam` (always true) to margin-based: blowouts (margin >15) and buzzer-beaters (margin <=2) go to `#big-plays`, everything else to `#play-by-play` only.

---

## Completed — Session 32 (Team Color Schemes)

### 49. [DONE] Team color accents on arena game cards
**Problem:** Arena game cards were monochrome — no visual team identity. All team names and scores looked the same, making it hard to quickly identify which teams played.

**Fix:** Each team score block gets a 3px left-border in the team's primary color. The winning team's name is rendered in their team color. Colors flow from a `team_colors` dict built alongside the existing `team_names` cache, carrying `(primary, secondary)` tuples per team ID.

### 50. [DONE] Team-colored game detail header
**Problem:** Game detail page header was plain — both teams looked identical in white text on dark background. No visual identity for each team's section.

**Fix:** Each team's header section gets their secondary color as background and primary color as a 4px left-border. Team names render in their primary color. The effect is subtle but creates distinct visual zones — the Rose City Thorns section has a dark navy tint with red accents, while the Burnside Breakers get deep blue with cyan.

### 51. [DONE] Team-colored box score headers
**Problem:** Box score team header rows in game detail were plain text with no visual distinction between the two teams' sections.

**Fix:** Team header rows get a 3px left-border in their primary color. The `box_score_groups` tuple was extended with a fourth element (team color) to carry this through to the template.

### 52. [DONE] Color dots on standings page
**Problem:** Standings table listed team names but had no color indicators — unlike the home page mini standings which had small dots.

**Fix:** Added 10px circular color dots (inline `border-radius: 50%` spans) next to each team name in the standings table, matching the pattern used in the home page mini standings. Colors passed from `_get_standings()` which now sets both `color` and `color_secondary` on each standings entry.

### 53. [DONE] Team colors in live arena zones
**Problem:** Live game zones during presentations used default text colors — team names were indistinguishable in the scoreboard area.

**Fix:** Server-rendered live zones apply team primary colors to team name spans via inline `style="color: {{ game.home_color }}"`. For JS-created zones (via SSE `game_starting` event), the `getOrCreateZone()` function reads `home_team_color` and `away_team_color` from the event payload and applies them as inline styles.

### 54. [DONE] Team color borders on upcoming games
**Problem:** "Up Next" section on the arena showed upcoming matchups as plain text — no visual team identity.

**Fix:** Each upcoming game card's team score blocks get a 3px left-border in the team's primary color, matching the completed game card pattern. Colors passed from a separate `team_colors_sched` dict built while resolving upcoming schedule entries.

---

## Completed — Session 33 (Live Zone Fixes + Elam Target + Commentary)

### 55. [DONE] Live container layout fix — section-title outside grid
**Problem:** The "Live — Round N" `.section-title` was a direct child of `.live-container`, which uses CSS grid `repeat(auto-fit, minmax(380px, 1fr))`. At desktop widths, the title consumed one grid column (~480px) while the `.arena-grid` wrapper got the other, wasting half the viewport. The live zones were also double-nested inside `.arena-grid` (which had its own `1fr 1fr` grid), further cramping them.

**Fix:** Moved `.section-title` outside `.live-container` so it doesn't participate in the grid. Removed the `.arena-grid` wrapper — `.live-zone` elements are now direct children of `.live-container`. This matches how JS creates zones (already appends directly to `#live-container`). Two 380px+ zones now sit side by side at 960px with no wasted column.

### 56. [DONE] Team-colored play-by-play lines in live arena
**Problem:** Every play in the live feed used `var(--text-secondary)` regardless of which team was on offense. Team colors were available in the presenter's color cache but never connected to individual play lines.

**Fix:** Added `offense_color` to the presenter's `play_dict`, derived from `colors.get(possession.offense_team_id)`. Server-rendered play lines get `style="color: {{ play.offense_color }}"`. JS-created play lines (via SSE) apply `line.style.color = d.offense_color`. Updated `.live-play-line:first-child` CSS to remove the `color: var(--text-primary)` override (which would fight the team color), keeping only `font-weight: 600`.

### 57. [DONE] Elam target shown in live zone instead of "0:00"
**Problem:** During Elam ending (Q4), the live zone clock showed "0:00" or was blank. The whole excitement of the Elam Ending is the target score — it should be front and center.

**Fix:** Added `elam_target` field to `LiveGameState`. In `_present_game()`, when `game_clock` is empty and `elam_target` exists, substitutes `"Target: {target}"` as the clock display. Included `elam_target` in the SSE play dict. JS handler shows "Target: N" when no game clock is present but elam_target is. Both server-rendered and JS paths now display the Elam target during Q4.

### 58. [DONE] Nav label: "Arena" → "Games"
**Problem:** Nav said "Arena" which is internal project language, not user-friendly.

**Fix:** Changed the nav link text from "Arena" to "Games" in `base.html`. Route remains `/arena`.

### 59. [DONE] Commentary variance — simulation mirror mock
**Problem:** The simulation mirror mock had only 2 hardcoded templates: "The courts ran hot" for high-scoring rounds (>=60 PPG) and "Someone tightened the screws" for low-scoring rounds (<=40 PPG). Mid-range scoring (41-59 PPG) produced no commentary at all. Every high-scoring round showed identical text.

**Fix:** Replaced with randomized variant arrays using the existing seeded RNG: 5 high-scoring variants, 4 low-scoring variants, and 3 mid-range variants. Examples: "Pace was relentless", "Buckets fell at an N-point clip", "Defense locked in", "Every bucket earned", "The meta feels unsettled". Deterministic per round (seeded by round number) but varied between rounds.

### 60. [DONE] Discord notifications spoil game results
**Problem:** Discord messages with game scores fired immediately when the simulation completed, before the live presentation replayed the games for viewers on the arena page. Anyone watching Discord would see results before the live show finished.

**Fix:** Switched the Discord bot from `game.completed`/`round.completed` events (fired by simulation) to `presentation.game_finished`/`presentation.round_finished` (fired by the presenter after each game's live replay completes). In instant mode (no presenter), the scheduler runner now publishes presentation events directly.

### 61. [DONE] Turnover narration missing defender name
**Problem:** Turnover play-by-play showed only the ball handler's name: "Rosa Vex coughs it up — with the steal" — no indication of who stole the ball. The `defender_id` was never set on turnover possession logs despite the stealer being tracked for stats.

**Fix:** Set `defender_id=stealer.hooper.id` on turnover logs. Rewrote all 4 turnover templates to clearly name both players (e.g., "Kai Swift strips Rosa Vex — stolen", "Rosa Vex loses the handle — Kai Swift with the steal"). Added 4 fallback templates for the rare case where defender is missing.

### 62. [DONE] Elam target label: "Target:" → "Target score:"
**Problem:** During the Elam Ending (untimed Q4), the clock display showed "Target: 67" which was ambiguous — could mean anything.

**Fix:** Changed to "Target score: 67" in both the presenter (server-rendered) and arena JS (SSE live updates).

### 63. [DONE] Substitutions not appearing in games
**Problem:** No bench substitutions ever occurred despite the substitution mechanic being implemented. Every hooper defaulted to `is_starter=True` because `_row_to_team()` in the game loop never set `is_starter` — all 4 hoopers were starters, bench was always empty.

**Fix:** `_row_to_team()` now sets `is_starter=idx < 3` — first 3 hoopers are starters, 4th is bench. Fatigue-based substitutions now trigger at quarter breaks when a starter's stamina drops below the threshold.

### 64. [DONE] Doubled play-by-play lines in live arena
**Problem:** Every play appeared twice in the live play-by-play feed. The arena template had two SSE connections: an HTMX `hx-ext="sse" sse-connect` attribute (vestigial, no `sse-swap` targets) and a manual `new EventSource()` in the script block. Both received every possession event and both appended lines.

**Fix:** Removed the unused HTMX SSE attribute from the rounds wrapper div. The manual EventSource in the script block handles all live updates.

---

## Completed — Session 39 (Surface Team Identity + /bio Command)

### 65. [DONE] Team strategy shown on team page
**Problem:** Governors could set team strategies via `/strategy` in Discord, and the strategy was stored as a `strategy.set` governance event, but the team profile page never displayed it. Governors had no way to see their current strategy outside Discord.
**Fix:** `team_page()` now queries `strategy.set` governance events, finds the latest one matching the team, and passes `team_strategy` to the template. A new "Current Strategy" card renders the strategy text in italic quotes, appearing between the roster and record sections. Only shows when a strategy exists.

### 66. [DONE] Team motto in Discord welcome embed
**Problem:** Teams have a `motto` field stored in the database, and the team page template already displayed it (lines 22-24), but the Discord `/join` welcome embed did not include it. New governors missed this piece of team identity.
**Fix:** `build_welcome_embed()` now accepts a `motto` parameter. When non-empty, the motto is rendered as an italic quote below the team name in the embed description. The `/join` handler passes `motto=target_team.motto` to the builder.

### 67. [DONE] Hooper backstories shown in Discord welcome embed
**Problem:** Hoopers have a `backstory` field (editable via the web UI), but the Discord welcome embed only showed hooper name and archetype. New governors joining via Discord had no way to see hooper personality.
**Fix:** `build_welcome_embed()` now renders backstory snippets under each hooper in the roster section. Backstories longer than 100 characters are truncated with "..." to keep the embed readable. Each backstory line is block-quoted (`> snippet`). The `/join` handler now includes `backstory` in the hooper dicts passed to the builder.

### 68. [DONE] `/bio` slash command for writing hooper backstories
**Problem:** The web UI had a bio editing feature (HTMX form on the hooper page), but Discord governors — the primary interaction surface — had no way to write hooper backstories without leaving Discord.
**Fix:** Added `/bio` slash command with `hooper` (autocomplete, own team only) and `text` parameters. Validates: must be enrolled as governor, text must be non-empty, max 500 characters, hooper must be on governor's team. On success, calls `repo.update_hooper_backstory()` and returns an ephemeral confirmation embed showing the saved text. Quick Start section of the welcome embed now mentions `/bio`.

---

## Completed — Session 40 (9-Feature Parallel Build)

### 69. [DONE] "Governance" renamed to "The Floor" across all UI
**Problem:** "Governance" sounded bureaucratic and distant — the opposite of the game's energy. Players govern from the court, not a boardroom. Needed a name that's both basketball slang (the court is "the floor") and legislative language (taking "the floor" to speak).
**Fix:** Renamed all user-facing strings: nav link "Governance" → "The Floor", page title "Governance" → "The Floor", section headers, embed titles ("Governance Mirror" → "The Floor — Mirror", "Governance Tokens" → "Floor Tokens"), template text across `base.html`, `governance.html`, `home.html`, `play.html`, `mirrors.html`, `privacy.html`, `terms.html`, `eval_dashboard.html`. Internal code names (module names, function names, event types, variable names) unchanged. "The Floor Has Spoken" used for vote result announcements.

### 70. [DONE] Voting UX — proposal autocomplete, announcements, vote counts
**Problem:** `/vote` had no way to select which proposal to vote on (just grabbed the latest), no public announcement when a proposal entered voting, and vote tallies showed only weighted scores with no raw counts — opaque and confusing.
**Fix:** Three changes: (1) `/vote` now accepts an optional `proposal` parameter with autocomplete showing all pending proposals by title. (2) When a proposal is confirmed, a public announcement embed ("New Proposal on the Floor") is posted to the governance channel with proposal text and instructions. (3) `VoteTally` gained `yes_count`, `no_count`, and `total_eligible` fields; tally embeds now show "2.50 (3 votes)" instead of bare "2.50". Per-proposal results posted to Discord after tallying.

### 71. [DONE] Admin review gate for wild proposals
**Problem:** The AI interpreter handles wild proposals (Tier 5+ or low confidence), but there was no human-in-the-loop check before they entered voting. A proposal like "the floor is lava" could pass before the admin assessed whether the simulation could handle it.
**Fix:** Added `pending_review` proposal status. Proposals flagged as Tier 5+ or with confidence < 0.5 are held instead of entering voting immediately. Admin receives a DM with an `AdminReviewView` (Approve/Reject buttons, 24h timeout). Approve transitions to confirmed + enters voting. Reject fires `proposal.rejected` event with a reason modal, refunds the governance token, and notifies the proposer. Config: `PINWHEEL_ADMIN_DISCORD_ID`.

### 72. [DONE] Governor profile pages
**Problem:** No web presence for individual governors. Team pages listed hoopers but not the humans who govern them. No way to see a governor's activity history.
**Fix:** Added `/governors/{player_id}` route with profile page showing: governor name, team affiliation with color dot, join date, activity summary (proposals submitted, votes cast, trades initiated), and recent event timeline. Added `/profile` Discord command (ephemeral, shows link to web profile). Team pages now list enrolled governors with links to their profiles. New repository methods: `get_governor_activity()`, `get_events_by_governor()`.

### 73. [DONE] Season lifecycle pages — archive list and detail
**Problem:** No way to browse completed seasons or see historical records after a season ends.
**Fix:** Added `/seasons/archive` list page and `/seasons/archive/{season_id}` detail page. Archive detail shows final standings, champion, final ruleset, rule change history, and aggregate stats (games played, proposals filed, mirrors generated). `SeasonArchiveRow` table stores the snapshot. Archive pages use the same dark theme with gold accents for champion highlights.
