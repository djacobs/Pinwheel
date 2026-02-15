# Pinwheel Fates: Documentation Table of Contents

## Root Files

| File | Purpose | Status |
|------|---------|--------|
| [CLAUDE.md](../CLAUDE.md) | Project constitution. Architecture principles, tech stack, code standards, project structure, development workflow, resolved design questions. The single source of truth for how we build. | Living document |
| [README.md](../README.md) | Quick start: requirements, local dev setup, Fly.io deployment. A stranger should clone and run in under 5 minutes. | Living document |
| [fly.toml](../fly.toml) | Fly.io deployment configuration. Region, machine size, health checks, release command. | Ready |
| Dockerfile | Multi-stage Docker build for Fly.io deployment. | **Not yet created** |
| pyproject.toml | Python project configuration, dependencies, dev extras. | **Not yet created** |

## Product Documents (`product/`)

Player-facing and product-level docs that define *what* Pinwheel Fates is.

| Document | Purpose | Key Contents |
|----------|---------|-------------|
| [VISION.md](product/VISION.md) | Philosophy, goals, resonant computing alignment, hackathon track justification | 5 goals, 5 resonant computing principles, the David Lynch diving bell metaphor, "the game where AI doesn't play — it helps you see" |
| [PRODUCT_OVERVIEW.md](product/PRODUCT_OVERVIEW.md) | User journey walkthrough, PM analysis, gap register, metrics coverage matrix | 7 user journey phases (Discovery → Spectator), 10 identified gaps with decision deadlines and fallbacks, open questions decision table, success criteria for each goal |
| [RUN_OF_PLAY.md](product/RUN_OF_PLAY.md) | The gameplay loop from a player's perspective — what happens each day | Governance pipeline (7 steps), token economy, 4-tier rule space, AI interpretation pipeline, amendment mechanic, three-layer feed topology, conflict resolution, "What Makes This Fun" |
| [PLAYER.md](product/PLAYER.md) | Governor and spectator experience, Discord integration | Two-surface architecture (web + Discord), Discord server structure, all bot commands, vote normalization, governor lifecycle, bot personality, report delivery, web↔Discord integration |
| [VIEWER.md](product/VIEWER.md) | Web dashboard, Arena, AI commentary, API spec | The Arena (2x2 live grid), Single Game view, AI Commentary Engine (omniscient narrator, batch generation, system prompt), Discord bot search, ~30 REST API endpoints, SSE filtering, presentation pacing, dramatic pacing |
| [GLOSSARY.md](product/GLOSSARY.md) | Canonical naming authority — the definitive term list for code, docs, API, and Discord | ~25 terms with definitions, acceptable aliases, and forbidden alternatives. Use Term column for code, Aliases for player-facing text. |
| [GAME_MOMENTS.md](product/GAME_MOMENTS.md) | Dramatic context checklist for player-facing outputs | Situations, rivalries, milestones that all output systems should reference |
| [NEW_GOVERNOR_GUIDE.md](product/NEW_GOVERNOR_GUIDE.md) | Onboarding guide for new players | Step-by-step guide for joining and governing |
| [ACCEPTANCE_CRITERIA.md](product/ACCEPTANCE_CRITERIA.md) | Testable acceptance criteria for every feature | Per-feature criteria organized by hackathon day, automation feasibility, coverage of all user journey phases |
| [PLAN.md](product/PLAN.md) | 5-day hackathon plan with daily goals, risk register, success criteria | Check off items as completed |
| [COLOPHON.md](product/COLOPHON.md) | Credits and acknowledgments | Tools, inspirations, contributors |

## Technical Documents

Architecture and engineering docs that define *how* the system works.

| Document | Purpose | Key Contents |
|----------|---------|-------------|
| [SIMULATION.md](SIMULATION.md) | The basketball simulation engine specification | 9 attributes, 9 archetypes (360-point budget), possession model, defensive model (4 schemes), scoring resolution, Moves system, Elam Ending, venue/home court, rule expressiveness (3-layer: Parameters, Game Effects, League Effects), safety boundaries, 21 decisions log |
| [GAME_LOOP.md](GAME_LOOP.md) | Game loop, scheduler, season structure | Three clocks model (game, governance, report), state machine, game presenter architecture, SSE event taxonomy (~25 types), seed generation, season structure (21 rounds + playoffs + offseason), dev/staging vs. production mode |
| [INTERFACE_CONTRACTS.md](INTERFACE_CONTRACTS.md) | Single source for everything shared across backend, frontend, presenter, and Discord | ID formats, 22 SSE event types, 18 governance event store types, API response envelope, ~30 API endpoints, Pydantic model index, ~22 behavioral tracking events |
| [SECURITY.md](SECURITY.md) | Prompt injection defense plan | 5-layer defense (input sanitization → sandboxed interpretation → output validation → human-in-the-loop → monitoring), 6 attack vector analyses, privilege model per AI context, implementation checklist |
| [INSTRUMENTATION.md](INSTRUMENTATION.md) | Profiling and measurement strategy | Three targets: gameplay joy (player behavior events, derived metrics, alarms), UX performance (latency targets, profiling, admin dashboard), token costs (per-call accounting, cost model, 6 optimization strategies) |
| [DEMO_MODE.md](DEMO_MODE.md) | All environment-specific behavior in one place | 3 environments, timing table, 4 pace modes, dev season config, seed data strategy, feature flags by env, hackathon demo script, environment variables |
| [OPS.md](OPS.md) | Operations, deployment, monitoring | Fly.io architecture, machine sizing, SQLite volume setup, environment variables, deploy/rollback workflow, Dockerfile strategy, health endpoint, cost estimates, SSE scaling path, Discord bot deployment, backup/recovery |
| [LIFECYCLE.svg](LIFECYCLE.svg) | Comprehensive system lifecycle diagram | Season creation, player join flow, game loop, governance flow, token economy, AI integration, API routes |

## Dev Log (`dev_log/`)

| Document | Purpose | Update Frequency |
|----------|---------|-----------------|
| [DEV_LOG.md](dev_log/DEV_LOG.md) | Running log of all decisions, reasoning, and outcomes — the project's memory | After each major task or decision |
| [UX_NOTES.md](dev_log/UX_NOTES.md) | Visual and interaction change record | After each UI change |
| [DEV_LOG_2026-02-10.md](dev_log/DEV_LOG_2026-02-10.md) | Sessions 1-5 (archived) | Archived |
| [DEV_LOG_2026-02-11.md](dev_log/DEV_LOG_2026-02-11.md) | Sessions 6-16 (archived) | Archived |
| [DEV_LOG_2026-02-12.md](dev_log/DEV_LOG_2026-02-12.md) | Sessions 17-33 (archived) | Archived |
| [DEV_LOG_2026-02-13.md](dev_log/DEV_LOG_2026-02-13.md) | Sessions 34-47 (archived) | Archived |

## Implementation Plans (`plans/`)

These define *how* we build each component. Created via the `/workflows:plan` cycle.

| Plan | Purpose | Day | Key Decisions |
|------|---------|-----|---------------|
| [League Configuration](plans/2026-02-10-feat-league-configuration-plan.md) | Seeding and editing league settings | Day 1 | Configuration hierarchy (League → Season → Team → Player), YAML seeding, AI-generated seeding, Pydantic models, 360-point attribute budget, 9 archetypes |
| [Database Schema](plans/2026-02-11-database-schema-plan.md) | SQLAlchemy schema and event store | Day 1 | Event store for governance, direct tables for games/teams/reports, 17 governance event types, read projections, SQLite compatibility |
| [Simulation Extensibility](plans/2026-02-11-simulation-extensibility-plan.md) | Hook system for Game Effects | Day 1-2 | HookPoint enum, GameEffect protocol, `_fire_hooks()`, Day 1: empty hooks, Day 2: effects plug in, Fate events follow same protocol |
| [Discord Bot](plans/2026-02-11-discord-bot-plan.md) | discord.py integration with FastAPI | Day 2-3 | In-process with FastAPI, slash command registration, Discord user → governor mapping, full command flows |
| [Presenter](plans/2026-02-11-presenter-plan.md) | Game presentation and pacing system | Day 3 | EventBus (async pub/sub), GamePresenter (asyncio task per game), dramatic pacing algorithm, commentary integration, late-join/catch-up, replay |
| [Season Lifecycle](plans/2026-02-11-season-lifecycle-plan.md) | Season state machine | Day 3 | 8 states (SETUP → COMPLETE), transition logic, playoff bracket management, dev mode compression |
| [Frontend](plans/2026-02-11-frontend-plan.md) | HTMX patterns and visual design | Day 4 | SSE-driven updates, server-rendered fragments, retro sports broadcast aesthetic, color system, typography, Discord OAuth |
| [Page Designs](plans/2026-02-11-page-designs.md) | Page-level UX wireframes | Day 4 | ASCII wireframes for 6 page types (Game Preview, Live Game, Game Summary, Team, Agent, Season), global nav, component reuse |
| [Day 1 Implementation](plans/2026-02-11-day1-implementation-plan.md) | Concrete Day 1 build plan | Day 1 | 7 phases, ~40-60 tests target, file inventory, time estimates (scaffolding 30min → models 1hr → simulation 3-4hr → seeding 1.5hr → DB 1hr → scheduler+API 1hr → observe 30min) |

## Document Dependency Graph

Understanding which docs feed into others helps when updating decisions:

```
product/GLOSSARY.md (canonical naming — everything references this)

product/VISION.md (philosophy, goals)
  └──► product/PRODUCT_OVERVIEW.md (user journey, measurable success criteria, decision table)
        └──► product/ACCEPTANCE_CRITERIA.md (testable feature criteria)
              └──► product/PLAN.md (daily build plan)

CLAUDE.md (architecture, code standards)
  ├──► SIMULATION.md (engine spec)
  │     └──► plans/simulation-extensibility (hooks for effects)
  │     └──► plans/league-configuration (seeding, attributes)
  ├──► GAME_LOOP.md (scheduler, season)
  │     └──► plans/season-lifecycle (state machine)
  │     └──► plans/presenter (pacing, commentary)
  ├──► product/PLAYER.md (governance UX, Discord)
  │     └──► plans/discord-bot (implementation)
  ├──► product/VIEWER.md (dashboard, API, Arena)
  │     └──► plans/frontend (HTMX patterns)
  │     └──► plans/page-designs (wireframes + data contracts)
  ├──► SECURITY.md (prompt injection defense)
  ├──► INSTRUMENTATION.md (metrics, costs)
  └──► OPS.md (deployment, Fly.io)
        └──► fly.toml (deployment config)

INTERFACE_CONTRACTS.md (SSE events, API endpoints, models, event store)
  └──► consolidates from product/VIEWER.md, GAME_LOOP.md, INSTRUMENTATION.md, database-schema-plan

DEMO_MODE.md (environments, pacing, demo script)
  └──► consolidates from OPS.md, GAME_LOOP.md, season-lifecycle-plan, day1-implementation-plan

product/RUN_OF_PLAY.md (gameplay loop)
  └──► references SIMULATION.md, product/PLAYER.md, product/VIEWER.md, GAME_LOOP.md
```

## Reading Order

For a new contributor, the recommended reading order is:

1. **product/VISION.md** — Why this exists (5 min)
2. **product/RUN_OF_PLAY.md** — What the game is (10 min)
3. **product/PRODUCT_OVERVIEW.md** — Who the users are and what they need (15 min)
4. **CLAUDE.md** — How we build (10 min)
5. **product/PLAN.md** — What we're building this week (5 min)
6. Then dive into whatever you're working on: SIMULATION.md for engine work, product/PLAYER.md for Discord, product/VIEWER.md for frontend, etc.
