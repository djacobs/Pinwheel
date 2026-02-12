# Plan: Documentation Consolidation — Contracts, Critical Path, Glossary, Demo Mode

## Context

The project has 12 design docs and 9 implementation plans but critical cross-cutting information is scattered: event names live in 4 docs, demo mode config in 5, open questions have no deadlines, and the Day 1 plan lacks an explicit dependency graph. This creates context-switch friction — a developer must read multiple docs to answer "what event name do I use?" or "can I start Phase 5 while Phase 3 is running?" These changes consolidate scattered information and add execution-time guardrails.

## Files to Create

### 0. `docs/GLOSSARY.md` (new)

Standalone 1-page canonical glossary. Every term that appears in code, docs, API, or Discord. This is the naming authority — when in doubt, check GLOSSARY.md.

**Format:** Alphabetical table with 4 columns:

| Term | Definition | Aliases (acceptable) | Never use |
|------|-----------|---------------------|-----------|

**Terms to include (~25):**

| Term | Definition | Aliases | Never use |
|------|-----------|---------|-----------|
| **Agent** | A simulated basketball player. Has attributes, moves, a backstory. Controlled by the simulation engine, not by humans. | — | "player" (ambiguous with Governor), "character", "bot" |
| **Amendment** | A modification to an active Proposal during a governance Window. Replaces the original on the ballot. No proposer veto. | — | "edit", "revision" |
| **Archetype** | One of 9 attribute templates that define an Agent's playstyle. Each emphasizes one attribute. (Sharpshooter, Floor General, Lockdown, Slasher, Iron Horse, Savant, The Closer, Wildcard, Oracle) | — | "class", "role" |
| **Arena** | The web dashboard's live multi-game view. 2x2 grid of simultaneous games with SSE updates. | "Arena view" | "homepage", "dashboard" |
| **Boost** | A governance token spent to increase a Proposal's visibility. Does not add a vote. | — | "upvote", "like" |
| **Box Score** | Per-Agent stat line for a single Game. Points, assists, rebounds, etc. | — | "stat sheet", "score card" |
| **Elam Ending** | End-of-game format: after Q3, a target score is set (leading score + elam_margin). First team to reach it wins on a made basket. | "Elam", "Elam period" | "overtime", "sudden death" |
| **Game** | A single simulated 3v3 basketball contest between two Teams. 4 quarters + Elam period. Deterministic given inputs + seed. | "Match" (acceptable but not preferred) | "round" (that's a different thing) |
| **Game Effect** | A conditional rule modification within a single Game. Composed of trigger × condition × action × scope × duration. Layer 2 of rule expressiveness. | "Effect" | "buff", "debuff", "modifier" |
| **Governor** | A human player. Joins a Team, proposes rules, votes, trades tokens, receives Mirrors. The primary user. | "Gov" (informal, in Discord) | "player" (ambiguous), "user" (too generic) |
| **Mirror** | An AI-generated reflection on gameplay or governance patterns. Never prescriptive. Types: simulation, governance, private, tiebreaker, series, season, offseason, State of the League. | "Reflection" | "report", "analysis", "feedback" |
| **Move** | A special ability an Agent can activate during a Possession when trigger conditions are met. Has an attribute gate. | — | "skill", "ability", "power" |
| **Possession** | One offensive sequence. The atomic unit of gameplay. Ball handler → action selection → resolution → scoring/miss → rebound. | "Play" (acceptable in commentary) | "turn" |
| **Presenter** | The server-side system that paces a pre-computed GameResult through SSE events over real time. Makes instant simulation feel like a live broadcast. | "Game Presenter" | "broadcaster", "streamer" |
| **Proposal** | A natural-language rule change submitted by a Governor. AI-interpreted into structured parameters. Goes to vote during a Window. | — | "suggestion", "request", "motion" |
| **Round** | One simulation block. Contains 4 simultaneous Games (with 8 teams). Governance Window follows each Round. | "Simulation block" | "game" (that's a single contest) |
| **Rule / RuleSet** | A governable parameter with type, range, and default. The RuleSet is the complete collection of all parameters. | "Rule change" (for a diff), "Parameter" | "setting", "config" (those are system config, not game rules) |
| **Scheme** | A team's defensive strategy for a Possession. One of: man-tight, man-switch, zone, press. | "Defensive scheme" | "formation", "play" |
| **Season** | The complete competitive arc. Regular season (21 Rounds) → tiebreakers → playoffs → championship → offseason. | — | "league" (the league persists across seasons) |
| **Seed** | (1) The random seed that makes a Game deterministic. (2) The act of populating the database with initial Teams/Agents. Context makes it clear. | — | — |
| **Series** | A best-of-N playoff matchup between two Teams. Governance Window between every Game in a Series. | "Playoff series" | "round" (that's a simulation block) |
| **Team** | A group of 4 Agents (3 starters + 1 bench). Has a Venue, color, motto. Governed by one or more Governors. | — | "squad", "roster" (roster is the list of agents on a team) |
| **Token** | Governance currency. Three types: PROPOSE (submit proposals), AMEND (modify proposals), BOOST (amplify visibility). Regenerate per Window. Tradeable. | — | "coin", "credit", "point" |
| **Venue** | A Team's home court. Has capacity, altitude, surface, location. Affects gameplay via home court modifiers. | "Home court" | "arena" (that's the web view), "stadium" |
| **Window** | A time-bounded governance period. Proposals, amendments, and votes happen during a Window. Opens after each Round. | "Governance window", "Gov window" | "session", "voting period" (too narrow — proposals and amendments also happen) |

**Footer note:** "When writing code: use the Term column for variable names, class names, and API paths. When writing for players: Aliases are acceptable in Discord bot messages and commentary. Never Use terms should be caught in code review."

### 1. `docs/INTERFACE_CONTRACTS.md` (new)

Single canonical source for everything shared across backend, frontend, presenter, and Discord.

**Sections:**
1. **Glossary reference** — "See `docs/GLOSSARY.md` for canonical naming. This document uses those terms exclusively."
2. **ID Formats** — `game_id: "g-{round}-{matchup}"`, all entities UUID, `discord_user_id: str` (snowflake). Include Pydantic validator examples.
3. **SSE Events** — All 15 event types grouped by category (game.*, governance.*, mirror.*, season.*, standings.*). Each entry: event name, payload shape (reference to Pydantic model), which SSE stream query param enables it. Consolidate from VIEWER.md, GAME_LOOP.md, presenter plan.
4. **Governance Event Store Types** — The 17 append-only event types (proposal.submitted, vote.cast, rule.enacted, etc.). Each with payload description. Consolidate from database schema plan.
5. **API Response Envelope** — The standard `{ data, meta, governance_context }` shape. One place, not repeated per endpoint.
6. **API Endpoints** — Full table of ~30 endpoints with method, path, response model, and auth requirement. Consolidate from VIEWER.md.
7. **Pydantic Model Index** — Table mapping each model to its file and consumers (who imports it). Not the model definitions themselves (those live in code) — just the registry.
8. **Behavioral Tracking Events** — The ~20 analytics events from INSTRUMENTATION.md and PRODUCT_OVERVIEW.md. Each with name and when it fires.
9. **Cross-references** to INSTRUMENTATION.md, DEMO_MODE.md, page-designs.md.

### 2. `docs/DEMO_MODE.md` (new)

One place for all environment-specific behavior. Currently scattered across OPS.md, GAME_LOOP.md, season-lifecycle-plan, day1-implementation-plan, and ACCEPTANCE_CRITERIA.md.

**Sections:**
1. **Three Environments** — development / staging / production. Purpose of each.
2. **Timing Table** — GAME_CRON, GOV_WINDOW, PRESENTATION_PACE per environment.
3. **Pace Modes** — instant (0s, tests), demo (5s, live demos), fast (15s, dev), production (60s, real league). What each feels like.
4. **Dev Season Config** — 7 rounds, single-game semis, best-of-3 finals, ~25 min total. The math.
5. **Seed Data** — Auto-generate in dev, YAML in staging/prod, fixed seeds for determinism.
6. **Feature Flags by Environment** — Commentary caching, error pages, OpenAPI docs, auto-seed, mirror staleness.
7. **Hackathon Demo Script** — The exact sequence for a 5-minute live demo: pre-seeded league, fast pace, pre-loaded governance window, one live proposal, one game with Elam, mirror delivery. Deterministic seeds ensure repeatable narrative beats.
8. **Environment Variables** — Full reference table (consolidate from OPS.md, day1 plan, config.py).

## Files to Update

### 3. `docs/plans/2026-02-11-day1-implementation-plan.md`

Insert three new sections between "Day 1 Deliverable" and "File Inventory":

**A. Critical Path & Dependency Graph**
- ASCII DAG: Phase 1 → 2 → [3 || 4 || 5] → 6 → 7
- Phase 3 internal DAG: state.py → hooks.py → [scoring || defense || moves || possession] → simulation.py
- Explicit labels: "CRITICAL PATH: 1→2→3→6→7" and "PARALLELIZABLE: 3, 4, 5 after Phase 2"

**B. Definition of Done (per phase)**
- Phase 1: 5 checks (pyproject.toml exists, dirs created, config loads, app starts, ruff passes)
- Phase 2: 5 checks (all models defined, validators work, no circular imports, example instantiation, test_models passes)
- Phase 3: 7 checks (simulate_game works, determinism, all 4 schemes, moves, Elam, 1000-game batch stats, 20+ tests)
- Phase 4: 4 checks (AI generates YAML, YAML loads, archetypes valid, seed CLI works)
- Phase 5: 4 checks (ORM models, migrations run, round-trip, test_db passes)
- Phase 6: 4 checks (round-robin valid, API endpoints 200, E2E test, test_api passes)
- Phase 7: 3 checks (1000-game distributions OK, params tuned, full suite green >80% coverage)

**C. Observability Checkpoints**
- Phase 3: structured log per game (duration_ms, possessions, score), move trigger logs
- Phase 6: request middleware (endpoint, duration, status)
- Phase 7: aggregate stats logged, distribution checks, tuning evidence in DEV_LOG.md

### 4. `docs/plans/2026-02-11-page-designs.md`

Insert a "Data Contracts" section at the top (after frontmatter, before page wireframes). For each of the 6 pages, a compact table:

| Page | Endpoints | Models | SSE Events |
|------|-----------|--------|------------|

Then per-page detail blocks mapping each major UI component to its data source:
- Game Preview: 5 endpoints, 0 SSE
- Live Game: 1 initial endpoint + SSE stream with 7 event types
- Game Summary: 3 endpoints, 0 SSE
- Team Page: 4 endpoints, optional standings SSE
- Agent Page: 3 endpoints, 0 SSE
- Season Page: 5 endpoints (during), 5 endpoints (archive), optional SSE

Cross-reference: "Full schemas in `docs/INTERFACE_CONTRACTS.md`"

### 5. `docs/PRODUCT_OVERVIEW.md`

Replace the "Gap Register" table with an enhanced version adding two columns:
- **Decision Deadline** — "Before Day 2", "Before Discord bot", "Post-hackathon", etc.
- **Default Fallback** — What happens if the decision isn't made by deadline.

Add a new subsection: **Open Questions → Decision Table** converting the scattered open questions from VIEWER.md, GAME_LOOP.md, PLAYER.md into a structured table:
- Question, Options (2-3), Recommendation, Deadline, Default if not decided.

Questions to include:
1. Mirror → action bridge (Critical, before Day 3)
2. Rule context panel interaction (before frontend, default: active highlighting)
3. Commentary model tier (before commentary engine, default: Sonnet 4.5)
4. Governance window timing (before scheduler, default: cron + admin override)
5. Concurrent simulation blocks (before game loop, default: queue for next block)
6. Mirror priority (before mirror delivery, default: private first)

### 6. Update references

- Add GLOSSARY.md, INTERFACE_CONTRACTS.md, and DEMO_MODE.md to CLAUDE.md project structure (docs section)
- Add GLOSSARY.md, INTERFACE_CONTRACTS.md, and DEMO_MODE.md to TABLE_OF_CONTENTS.md

## Execution Order

1. GLOSSARY.md (new) — naming authority, everything else references it
2. INTERFACE_CONTRACTS.md (new) — references glossary, everything else references contracts
3. DEMO_MODE.md (new) — standalone, no dependencies
4. day1-implementation-plan.md updates — critical path, DoD, observability
5. page-designs.md updates — data contract mapping
6. PRODUCT_OVERVIEW.md updates — decision deadlines
7. CLAUDE.md + TABLE_OF_CONTENTS.md reference updates

## Verification

- Every SSE event name in INTERFACE_CONTRACTS.md matches usage in VIEWER.md and GAME_LOOP.md
- Every API endpoint in INTERFACE_CONTRACTS.md matches VIEWER.md
- Every model in the index matches what's defined in SIMULATION.md and database schema plan
- Page data contracts reference only endpoints that exist in the contracts doc
- Decision deadlines in PRODUCT_OVERVIEW.md align with PLAN.md day schedule
- No content is duplicated — each doc has one authoritative source and cross-references
