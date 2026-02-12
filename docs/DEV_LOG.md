# Pinwheel Dev Log — 2026-02-12

Previous logs: [DEV_LOG_2026-02-10.md](DEV_LOG_2026-02-10.md) (Sessions 1-5), [DEV_LOG_2026-02-11.md](DEV_LOG_2026-02-11.md) (Sessions 6-16)

## Where We Are

- **432 tests**, zero lint errors
- **Days 1-6 complete:** simulation engine, governance + AI interpretation, mirrors + game loop, web dashboard + Discord bot + OAuth + evals framework, APScheduler, presenter pacing, AI commentary, UX overhaul, security hardening
- **Day 7 complete:** Production fixes, player pages overhaul, simulation tuning, home page redesign
- **Live at:** https://pinwheel.fly.dev
- **Latest commit:** Session 20 (home page living dashboard redesign)

## Today's Agenda (Day 7: Player Experience + Polish)

### Production fixes
- [x] Fix play-by-play truncation — `[:50]` was hiding Elam winning plays (Session 17)
- [x] Harden team.html venue dict access (Session 17)
- [x] Re-seed production DB with narrative mirrors (Session 17)

### Player pages overhaul
- [x] Spider charts for player attributes — SVG nonagons, server-computed (Session 18)
- [x] Individual agent pages with bio, spider chart, game log, season averages (Session 18)
- [x] Player bio section editable by team governors via HTMX (Session 18)
- [x] Line scores for each game participated in (Session 18)
- [x] Season averages (PPG, APG, SPG, TOPG, FG%, 3P%, FT%) (Session 18)

### Simulation tuning
- [x] Shot clock violation mechanic — real 15-second clock enforcement (Session 19)
- [x] Scoring rebalance — 34 pts/team → 55-64 pts/team, matching Unrivaled range (Session 19)
- [x] Stamina management — reduced drain, raised floor, inter-quarter recovery (Session 19)
- [x] Elam display fix — "Elam" → "Q4" in quarter headers (Session 19)

### Home page redesign
- [x] Living dashboard with hero, latest scores, standings, mirror, upcoming (Session 20)
- [x] Production re-seed with tuned simulation parameters (Session 20)

### Governance refinements
- [ ] Player trades: only the two teams' governors vote on trades

### Discord server infrastructure
- [ ] `/join` command — team enrollment with season-lock
- [ ] Channel setup on bot ready
- [ ] Event routing — game results to channels

---

## Session 17 — Production Fixes + Re-Seed

**What was asked:** Three production issues: (1) game page play-by-play showed plays after the winning score, (2) team pages potentially blank, (3) mirrors showed old generic text.

**What was built:**

### Play-by-play truncation fix
- **Root cause:** `game_loop.py` stored only `possession_log[:50]`. With 15 possessions/quarter × 3 quarters = 45 regular plays, Elam possessions pushed past 50. The winning Elam play was truncated — making it look like the game continued after the last visible score.
- **Fix:** Removed `[:50]` limit. All possessions now stored. Small JSON objects, no meaningful storage impact.

### Team page template hardening
- **Root cause:** `team.venue.name` / `team.venue.capacity` used mixed Jinja2 `is defined` + `.get()` patterns. Changed to `team.venue['name']` with `is mapping` guard.
- Venue capacity now displays with thousands separator.

### Production re-seed
- Previous deployment had emptied the DB (volume detach during deploy).
- Re-ran `demo_seed.py seed` + `demo_seed.py step 3` on production.
- Mirrors now show narrative text: "Rose City Thorns survived St. Johns Herons by 1 — a 34-33 grinder..."
- All pages verified working: arena, standings, game detail (with Elam target + full play-by-play), team profiles.

**Files modified (2):** `core/game_loop.py`, `templates/pages/team.html`

**408 tests, zero lint errors.** Deployed to https://pinwheel.fly.dev.

---

## Session 18 — Spider Charts + Individual Agent Pages

**What was asked:** Implement the player pages overhaul plan — spider charts replacing bar charts, individual player pages with bio/spider chart/game log/season averages, HTMX bio editing gated to team governors.

**What was built:**

### Spider charts (SVG, server-computed)
- Created `src/pinwheel/api/charts.py` — pure geometry functions for nonagon spider charts
- 9 axes (scoring, passing, defense, speed, stamina, iq, ego, chaotic_alignment, fate) at 40° intervals
- League-average shadow polygon (dashed, low opacity) behind agent's colored polygon
- Color-coded vertex dots and 3-char labels with values
- Created `templates/components/spider_chart.html` — Jinja2 macro rendering inline SVG

### Individual agent pages (`/agents/{agent_id}`)
- Header with name, archetype badge, team link with color dot
- Bio section with HTMX edit button (governor-only, uses existing `backstory` field)
- Two-column: spider chart (left) + season averages card (right)
- Game log table: RD, OPP, PTS, FG, 3P, FT, AST, STL, TO (rows link to game pages)
- Special moves section

### Team page upgrade
- Replaced horizontal bar charts with spider chart macro calls (220px)
- Agent names are now clickable links to `/agents/{agent_id}`

### Repository additions
- `get_box_scores_for_agent()` — JOIN box_scores + game_results, ordered by round
- `get_league_attribute_averages()` — average each of 9 attributes across all agents
- `update_agent_backstory()` — update agent's backstory text

**Files created (4):** `api/charts.py`, `templates/components/spider_chart.html`, `templates/pages/agent.html`, `tests/test_charts.py` (15 unit tests)

**Files modified (5):** `db/repository.py`, `api/pages.py`, `templates/pages/team.html`, `static/css/pinwheel.css`, `tests/test_pages.py` (8 new integration tests)

**431 tests, zero lint errors.** Deployed to https://pinwheel.fly.dev.

---

## Session 19 — Simulation Tuning

**What was asked:** Three issues with game simulation: (a) no shot clock turnovers despite 15-second clock, (b) scoring too low (34 pts/team vs Unrivaled's 64-90 range), (c) Elam display said "1/2/3/Elam" instead of "1/2/3/4".

**What was built:**

### Shot clock violation mechanic
- Added `check_shot_clock_violation()` to `possession.py` — probability based on defensive scheme pressure, handler fatigue, and IQ
- Base rate 2%, scaled by scheme (press 2×, man_tight 1.5×, zone 0.5×), fatigue, IQ
- Capped at 0.5%–12%
- Added narration templates for shot clock violations in `narrate.py`

### Scoring rebalance
- **Root cause:** Stamina collapsed to 0.1 floor by mid-game, destroying PPP. Elam period then ground through 100+ possessions at ~0.39 PPP.
- **Stamina fixes:** Base drain 0.012→0.007, offense drain 0.005→0.003, floor 0.1→0.15, recovery rate improved
- **Rule defaults:** quarter_possessions 15→25, shot_clock 12→15, elam_margin 13→25, halftime_recovery 0.25→0.40
- **Added inter-quarter recovery:** `quarter_break_stamina_recovery=0.15` (Q1/Q3 breaks)
- **Result:** 20-game verification → avg 65-67 pts/team (132 total), ~124 possessions. Matches Unrivaled range.

### Elam display fix
- Arena and game detail templates: changed "Elam" → "Q4" in quarter headers

**Files modified (7):** `models/rules.py`, `core/possession.py`, `core/simulation.py`, `core/narrate.py`, `templates/pages/arena.html`, `templates/pages/game.html`, `tests/test_models.py`

**431 tests, zero lint errors.** Deployed to https://pinwheel.fly.dev.

---

## Session 20 — Home Page Living Dashboard + Production Re-seed

**What was asked:** (1) Re-seed production from round 1 with tuned simulation parameters. (2) Redesign home page — communicate this is a living game/league/community, not just a giant nav. Show hero text, latest events, live games, what's coming up.

**What was built:**

### Production re-seed
- Cleared all game data (game_results, box_scores, mirrors, eval_results) via SQLite on Fly.io volume
- Re-ran `demo_seed.py step 3` — new scores: 40-64 per team (much better than old 34-33)
- Round 1: Thorns 58-48 Breakers, Hammers 55-40 Herons
- Round 2: Thorns 57-50 Hammers, Breakers 64-52 Herons
- Round 3: Herons 58-56 Thorns, Hammers 61-52 Breakers

### Home page redesign
Replaced static nav-card layout with a living league dashboard:
1. **Hero** — "PINWHEEL FATES" with animated green pulse dot showing "Season 1 · Round 3 · 6 games played"
2. **Latest Results** — Score cards with team color dots, final scores, game-winning play narration, Elam target badge
3. **Two-column** — Mini standings (rank, color dot, name, W-L, +/-) + "The AI Sees" mirror card
4. **Coming Up** — Next round's scheduled matchups with team colors
5. **How Pinwheel Works** — 4-card explainer for new visitors: Games Simulate, You Govern, AI Reflects, Rules Evolve
6. **Explore** — Compact icon grid linking to Arena, Governance, Rules, Mirrors

**Files modified (4):** `api/pages.py` (enriched home_page route), `templates/pages/home.html` (full rewrite), `static/css/pinwheel.css` (~300 lines new styles), `tests/test_pages.py` (+1 test)

**432 tests, zero lint errors.** Deployed to https://pinwheel.fly.dev.
