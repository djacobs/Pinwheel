# Pinwheel Dev Log — 2026-02-12

Previous logs: [DEV_LOG_2026-02-10.md](DEV_LOG_2026-02-10.md) (Sessions 1-5), [DEV_LOG_2026-02-11.md](DEV_LOG_2026-02-11.md) (Sessions 6-16)

## Where We Are

- **480 tests**, zero lint errors
- **Days 1-6 complete:** simulation engine, governance + AI interpretation, mirrors + game loop, web dashboard + Discord bot + OAuth + evals framework, APScheduler, presenter pacing, AI commentary, UX overhaul, security hardening
- **Day 7 complete:** Production fixes, player pages overhaul, simulation tuning, home page redesign
- **Live at:** https://pinwheel.fly.dev
- **Latest commit:** Session 28 (Substitution logic + Agent→Hooper rename)

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

### Governance & rules UX
- [x] Governance page — removed auth gate, now publicly viewable (Session 21)
- [x] Rules page — redesigned from config dump to tiered card layout (Session 21)
- [x] Copy rewrite — player-centric language across home, rules, governance (Session 21)

### Onboarding
- [x] "How to Play" page — full onboarding: rhythm, what you do, Discord commands, FAQ (Session 23)
- [x] "Play" nav link — highlighted in cyan, first position in nav (Session 23)
- [x] Home page join CTA — gradient-topped card with Discord link + learn more (Session 23)
- [x] `discord_invite_url` config setting for Discord join link (Session 23)

### Governance refinements
- [x] Player trades: only the two teams' governors vote on trades (Session 24)

### Discord server infrastructure
- [x] `/join` command — team enrollment with season-lock, autocomplete, welcome DM (Session 24)
- [x] Channel setup on bot ready — idempotent, persisted via BotStateRow (Session 24)
- [x] Event routing — game results to team channels with win/loss framing (Session 24)

### Safety & rules
- [x] Rules page "propose anything" rewrite — wild card section (Session 24)
- [x] Haiku injection classifier — fail-open pre-flight on proposals (Session 24)

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

---

## Session 21 — Governance Fix + Rules Redesign + Copy Rewrite

**What was asked:** (1) `/governance` page doesn't work (was auth-gated, redirecting all visitors). (2) Rules page lost its design — showing a raw config dump instead of meaningful content. (3) Copy across the site was "terrible" — needed player-centric language.

**What was built:**

### Governance page made public
- Removed auth gate that redirected unauthenticated visitors to Discord OAuth login
- Proposing and voting still require Discord auth (via bot slash commands)
- Page now loads for everyone, showing the audit trail of proposals and votes

### Rules page redesigned
- Replaced raw `ruleset.items()` config dump with `RULE_TIERS` metadata
- 29 rules organized into 4 tiers: Game Mechanics (13), Agent Behavior (9), League Structure (5), Meta-Governance (2)
- Each rule has human-readable label, description, current value, valid range, and changed-by-community flag
- "Changed by the Community" highlight strip at top when rules differ from defaults
- Change history timeline at bottom
- Hero section with player-empowering tagline

### Copy rewrite (critical user feedback)
User feedback: "This copy is terrible!!!! The players rewrite the rules."

Fixed all taglines:
- **Home hero:** "3-on-3 basketball where the players rewrite the rules. Propose changes. Vote with your team. Shape the game. The AI is your mirror — but the fates are yours."
- **Rules hero:** "Every number below shapes how this league plays. Between rounds, you propose changes in plain English and vote with your team."
- **Governance hero:** "Every rule change starts with a proposal from a player. Write it in plain English. The AI translates it. Your team votes. Pass it, and you've rewritten the game."
- **How It Works cards:** Rewritten to center the player as protagonist

**Files modified (5):** `api/pages.py`, `templates/pages/home.html`, `templates/pages/rules.html`, `templates/pages/governance.html`, `static/css/pinwheel.css` (~180 lines new rules styles), `tests/test_pages.py` (updated assertions)

**432 tests, zero lint errors.** Deployed to https://pinwheel.fly.dev.

---

## Session 23 — "How to Play" Onboarding Page

**What was asked:** "If someone says 'This is cool, how do I play?' What do we tell them? When are the games? What should they expect? These are the most important UX_Notes yet."

**What was built:**

### New `/play` page — full onboarding experience
- **Hero:** "Join the League" with Discord join button (when `discord_invite_url` is configured)
- **League status bar:** Current round, teams, agents, games played (live from DB)
- **The Rhythm:** 4-step cycle with detailed descriptions — Games Play Out → Governance Window Opens → Mirror Reflects → Rules Change. Shows current pace and governance window duration.
- **What You Do:** 4-card grid — Watch, Propose, Vote, Reflect
- **Discord Commands:** Reference list of `/join`, `/propose`, `/vote`, `/strategy`, `/tokens`, `/trade`
- **FAQ:** 5 questions — What's the Elam Ending? What happens when a proposal passes? Can I break the game? What does the AI actually do? Is this free?
- **Bottom CTA:** Discord join button + contextual message

### Navigation update
- "Play" link added as first item in nav bar, styled in cyan to stand out
- Links to `/play` from home page "How It Works" section

### Home page join CTA
- New card below "How It Works" with gradient top border (highlight → governance → score)
- "Want to play?" heading with Discord join link and "Learn how to play" link
- Appears for all visitors

### Config update
- Added `discord_invite_url` setting (empty default, configurable via env var)
- Passed to all page templates via `_auth_context()`

**Files created (1):** `templates/pages/play.html`
**Files modified (6):** `config.py`, `api/pages.py`, `templates/base.html`, `templates/pages/home.html`, `templates/pages/rules.html`, `static/css/pinwheel.css` (~250 lines new styles), `tests/test_pages.py` (+3 tests)

**435 tests, zero lint errors.**

---

## Session 24 — Discord Infrastructure + Safety

**What was asked:** Implement the remaining Discord infrastructure: player trades (scoped voting), `/join` hardening, channel setup idempotency, event routing to team channels. Also: rules page needs to make clear players can propose *anything*, and add Haiku injection classifier. Finally, create a `/post-commit` Claude Code skill for session-end housekeeping.

**What was built:**

### Channel setup idempotency (Step 3 of plan)
- `_setup_server()` rewritten to check for existing channels/roles by name before creating
- `BotStateRow` table for persisting channel IDs across restarts
- `_load_persisted_channel_ids()` loads from DB on startup
- Channel permissions hardened: team channels deny `@everyone`, grant team role

### `/join` hardening (Step 2)
- Team parameter made optional — no arg shows team list with governor counts
- `_team_autocomplete` with `@app_commands.autocomplete`
- Welcome DM after enrollment (team name, roster, quick-start commands)
- "Ride or die" season-lock messaging
- `get_governor_counts_by_team()` repository method
- `build_welcome_embed()` and `build_team_list_embed()` embed builders

### Event routing (Step 4)
- Game results now post to both team channels with win/loss framing
- `build_team_game_result_embed()` — team-specific "Victory!"/"Defeat." embeds
- `_get_team_channel()` and `_send_to_team_channel()` helpers
- `home_team_id`/`away_team_id` added to game.completed event payload
- Governance window closed events post to all team channels

### Agent trades (Step 1)
- `AgentTrade` model with scoped voting (both teams' governors must approve)
- `/trade-agent` command with autocomplete for agent names on both teams
- `AgentTradeView` — approve/reject buttons, auth-scoped to `required_voters`
- Domain functions: `propose_agent_trade()`, `vote_agent_trade()`, `tally_agent_trade()`, `execute_agent_trade()`
- `swap_agent_team()` and `get_governors_for_team()` repository methods
- `build_agent_trade_embed()` embed builder

### Rules page "propose anything" (Step 5)
- "Beyond the Numbers" wild card section with 6 example proposals ("Make the floor lava", "Maximum height rule", "Switch to baseball")
- Flow strip: You propose → AI interprets → Your team votes → The game changes
- Bridge text reframing parameter tiers as "starting point"

### Haiku injection classifier (Step 6)
- `ai/classifier.py` — `classify_injection()` using `claude-haiku-4-5-20251001`
- Fail-open design: any error returns `legitimate` with confidence 0.0
- Wired into `api_submit_proposal()` — blocks high-confidence injections, annotates suspicious ones
- 16 tests covering all classification paths and failure modes

### `/post-commit` skill
- Created `.claude/skills/post-commit/SKILL.md` for session-end housekeeping
- Steps: run tests, run Rodney/Showboat, update dev log (one per day), update UX notes

**Files created (3):** `ai/classifier.py`, `tests/test_classifier.py`, `.claude/skills/post-commit/SKILL.md`

**Files modified (12):** `discord/bot.py`, `discord/views.py`, `discord/embeds.py`, `models/tokens.py`, `core/tokens.py`, `core/game_loop.py`, `db/models.py`, `db/repository.py`, `api/governance.py`, `templates/pages/rules.html`, `static/css/pinwheel.css`, `tests/test_discord.py`

**465 tests, zero lint errors.**

**What could have gone better:** Hit the hourly rate limit mid-session with 3 background agents running simultaneously. Two agents had already written their code but couldn't self-correct lint/test issues. Manual cleanup of 3 lint errors and 4 test failures from agent-written code. Lesson: run fewer parallel agents, or use API key from the start.

---

## Session 25 — Clock-Based Quarters

**What was asked:** Replace possession-count quarters with game-clock quarters. Each quarter is `quarter_minutes` (default 10) on a game clock. Each possession consumes `play_time + dead_time` seconds. The `shot_clock_seconds` parameter now drives actual clock consumption.

**What was built:**

### Clock-based quarter model
- **`models/rules.py`:** Replaced `quarter_possessions: int = Field(default=25, ge=5, le=50)` with `quarter_minutes: int = Field(default=10, ge=3, le=20)`
- **`core/state.py`:** Added `game_clock_seconds: float = 0.0` to `GameState`
- **`core/possession.py`:** Added `DEAD_TIME_SECONDS = 9.0` module constant, `time_used: float` field on `PossessionResult`, and `compute_possession_duration(rules, rng)` function. Clock RNG draw happens at the start of `resolve_possession()` for consistent RNG position. All return paths carry `time_used`.
- **`core/simulation.py`:** `_run_quarter()` now sets `game_clock_seconds = quarter_minutes * 60` at start and loops `while game_clock_seconds > 0` instead of counting possessions. Each possession decrements the clock and accumulates `minutes` on all on-court agents. `_build_box_scores()` now wires `agent_state.minutes` into `AgentBoxScore`.

### Governance + AI integration
- **`core/governance.py`:** Updated tier1 parameter set: `quarter_possessions` → `quarter_minutes`
- **`ai/interpreter.py`:** Replaced `"quarter possessions"` keyword with `"quarter length"` and `"quarter minutes"` mappings
- **`api/pages.py`:** Updated `_GAME_MECHANICS_RULES` display entry from "Possessions per Quarter" to "Quarter Length"

### Evals
- **`evals/rule_evaluator.py`:** Updated mock evaluation text to reference `quarter_minutes`

### Time model
Each possession costs: `play_time = shot_clock_seconds * rng.uniform(0.4, 1.0)` (6–15s, avg ~10.5s) + `DEAD_TIME_SECONDS = 9.0` (inbound, transitions) ≈ 15–24s per possession (avg ~19.5s). With defaults (10 min quarter, 15s shot clock): ~30 possessions/quarter, ~90 regulation.

**Files modified (14):** `models/rules.py`, `core/state.py`, `core/possession.py`, `core/simulation.py`, `api/pages.py`, `core/governance.py`, `ai/interpreter.py`, `evals/rule_evaluator.py`, `tests/test_models.py`, `tests/test_game_loop.py`, `tests/test_pages.py`, `tests/test_scheduler_runner.py`, `tests/test_commentary.py`, `tests/test_evals/test_rule_evaluator.py`

**465 tests, zero lint errors.**

**What could have gone better:** Clean implementation — no issues.

---

## Session 26 — Game Clock Display in Play-by-Play

**What was asked:** Show the game clock in play-by-play display. Each possession now consumes real clock time (from Session 25's clock-based quarters), but the play-by-play only shows "Q1", "Q2", etc. It should show the time remaining like real basketball: "Q1 9:32". Elam ending (Q4) is untimed, so no clock there.

**What was built:**
- Added `game_clock: str = ""` field to `PossessionLog` model — empty default means Elam possessions naturally display no clock
- In `_run_quarter()`, after decrementing `game_clock_seconds`, format remaining time as `M:SS` and set on `result.log.game_clock` before appending to the log
- Updated `game.html` template to conditionally show clock: `Q1 9:32` for timed quarters, just `Q4` for Elam
- Widened `.pbp-quarter` CSS from `min-width: 2rem` to `min-width: 5rem` to fit clock text
- Added 2 tests: timed quarters have non-empty M:SS game_clock, Elam possessions have empty game_clock

**Files modified (5):** `models/game.py`, `core/simulation.py`, `templates/pages/game.html`, `static/css/pinwheel.css`, `tests/test_simulation.py`

**467 tests, zero lint errors.**

**What could have gone better:** Clean implementation — no issues.

---

## Session 27 — UX Polish, Richer Mirrors, Presentation Cycle

**What was asked:** Three interconnected changes: (1) Remove "Rules" from nav, remove AI sentence from play hero, migrate wild card copy + key game params to play page. (2) Make mirrors more verbose — mention specific rule changes with old/new values, governance outcomes, next governance window timing. (3) Build real-time presentation cycle — new presenter module that replays pre-computed game results over wall-clock time via EventBus.

**What was built:**

### UX fixes
- Removed "Rules" nav link (page still accessible via URL)
- Removed "The AI watches and reflects — but every decision is yours." from play page hero
- Migrated "Beyond the Numbers" wild card section from rules page to play page
- Added "Current Game Parameters" section to play page — 6 key rules in card grid (shot clock, three-point value, quarter length, Elam margin, free throw value, foul limit)
- Loaded RuleSet in `play_page()`, community change count passed to template

### Richer mirrors
- Updated all 3 prompt templates: simulation (3-5 paragraphs), governance (3-5 paragraphs), private (2-3 paragraphs)
- Added prompt rules for referencing specific parameter changes, governance window outcomes, and next window timing
- Increased `max_tokens` from 800 to 1500
- Enriched `governance_data["rules_changed"]` in game_loop.py with `rule.enacted` event data (parameter, old_value, new_value)
- Added `governance_window_minutes` to governance data
- Updated mock generators: governance mock shows "Param Label moved from X to Y", simulation mock notes rule changes

### Real-time presentation cycle
- Added 3 config fields: `presentation_mode` (instant/replay), `game_interval_seconds` (1800), `quarter_replay_seconds` (300)
- Created `core/presenter.py`: `PresentationState` dataclass + `present_round()` async function
  - Groups possessions by quarter, drips events with calculated delays
  - Re-entry guard prevents double-presentation
  - Cancellation via `asyncio.Event` for clean shutdown
- Added `game_results` list to `RoundResult` so GameResult objects flow through
- Wired presenter into `scheduler_runner.py` — replay mode creates background task after `step_round()`
- Initialized `PresentationState` on `app.state` in `main.py`
- 6 new tests: event ordering, cancellation, re-entry guard, empty results, state reset, multi-game

**Files created (2):** `core/presenter.py`, `tests/test_presenter.py`

**Files modified (10):** `templates/base.html`, `templates/pages/play.html`, `api/pages.py`, `ai/mirror.py`, `core/game_loop.py`, `core/scheduler_runner.py`, `main.py`, `config.py`, `tests/test_mirrors.py`

**473 tests, zero lint errors.**

**What could have gone better:** Background agents couldn't write files due to permission denials — had to implement all 3 work items directly. The agents did valuable research (correct field names on GameResult/PossessionLog) even though they couldn't write code.

---

## Session 28 — Substitution Logic + Agent→Hooper Rename

**What was asked:** Implement a two-part plan: (1) Add bench player substitution mechanics (foul-out and fatigue triggers). (2) System-wide rename from "Agent" to "Hooper" (basketball slang) — ~400+ occurrences across models, DB, API, templates, tests, docs. Also: change governance window default to 15 minutes, update /play page copy.

**What was built:**

### Substitution logic (Work Item 1)
- `on_court: bool` field on `HooperState`, initialized from `is_starter`
- `home_active`/`away_active`/`home_bench`/`away_bench` properties on `GameState`
- `substitute(out, in_)` method on `GameState`
- `_check_substitution(game_state, rules, log, reason)` in `simulation.py` — two triggers:
  - **Foul-out:** Scans all players for ejected+on_court, subs in highest-stamina bench player
  - **Fatigue:** At quarter breaks, swaps lowest-stamina active player if below `substitution_stamina_threshold`
- `substitution_stamina_threshold: float = 0.35` added to `RuleSet` (Tier 1, governable)
- Substitution logged as `PossessionLog` entries with `action="substitution"`
- 7 new tests: foul-out sub, fatigue sub, no bench plays short-handed, bench stamina, log entries

### Agent→Hooper rename (Work Item 2)
Bottom-up through the full stack:
- **Models:** `Hooper`, `HooperBoxScore`, `HooperTrade`, `HooperTradeStatus` (backward-compat aliases kept)
- **State:** `HooperState` dataclass, `AgentState()` factory function for backward-compat
- **Core:** `possession.py`, `defense.py`, `scoring.py`, `moves.py`, `hooks.py` — all `HooperState` types, `.hooper.*` property access
- **Tokens:** `propose_hooper_trade()`, `vote_hooper_trade()`, `execute_hooper_trade()`
- **DB:** `HooperRow`, table `hoopers`, `create_hooper`/`get_hooper`/`swap_hooper_team`
- **Seeding:** `hoopers_per_team`, `Hooper()` constructor
- **Game loop:** `_row_to_team` builds `Hooper` objects, `hoopers=` kwarg
- **API routes:** `/hoopers/{hooper_id}` (was `/agents/{agent_id}`), bio edit/view endpoints
- **Templates:** `agent.html` → `hooper.html`, team/game/play/rules/home templates updated
- **CSS:** `.agent-*` → `.hooper-*` selectors
- **Discord:** `bot.py`, `embeds.py`, `views.py` — trade commands, embeds, autocomplete
- **Tests:** 12 test files updated
- **Scripts:** `demo_seed.py` — `"hoopers"` keys, `create_hooper`

### Config & copy changes
- `pinwheel_gov_window` default: 120 → 900 (15 minutes)
- Play/rules page: "You are not limited to tweaking config values. You can propose anything." → "Want to change the rules? Propose new ones — anything."

**Files modified (~50):** Full stack — models, state, simulation, possession, defense, scoring, moves, hooks, tokens, seeding, game_loop, db/models, db/repository, api/pages, api/games, api/teams, config, discord/bot, discord/embeds, discord/views, templates (7), css, demo_seed, 12 test files

**480 tests, zero lint errors.**

**What could have gone better:** The rename required careful coordination across ~50 files. Parallel subagents (templates, discord, tests) worked well — the bottleneck was the sequential core rename that had to flow bottom-up through the dependency chain. The backward-compat aliases were essential for keeping tests green during the incremental rename.
