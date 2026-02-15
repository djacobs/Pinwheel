# Colophon

## Pinwheel Fates

Pinwheel Fates is an auto-simulated 3v3 basketball league in which human governors propose and vote on natural-language rules, an AI interprets those rules into simulation parameters, and the consequences play out on a procedurally generated court. It starts out as basketball, but the players decide what it becomes. The game's thesis is that showing people how they govern — through an AI report system — changes how they choose to govern next.

Built on the principles of [Resonant Computing](https://resonantcomputing.org): private, dedicated, plural, adaptable, prosocial. The AI works exclusively for the players. No engagement optimization, no hidden agendas. Its only function is making the group's dynamics visible to the group.

The project was built solo during the Anthropic Hackathon, February 2026. The builder had torn his ACL in October, undergone surgery in February, and was sleeping roughly two hours at a stretch when the hackathon acceptance came through — a 4% admit rate. The first program he ever wrote, in 1982, was drawing an NBA court in Logo with his dad. Forty-four years later, the impulse is the same; the tools are not.

---

## Application Stack

| Layer | Technology | Role |
|-------|-----------|------|
| Backend | **Python 3.12 + FastAPI 0.115+** | Async API server, simulation orchestration, governance engine |
| Frontend | **HTMX 1.x + SSE + Jinja2** | Live-updating dashboard — standings, play-by-play, governance, reports — with no JS build step |
| Database | **SQLAlchemy 2.0 async** (aiosqlite) | SQLite on persistent volume. Schema via `Base.metadata.create_all()` — no Alembic, acceptable for hackathon pace |
| Models | **Pydantic 2.9+** | Shared vocabulary across all layers: API contracts, domain models, rule definitions, eval results |
| Deployment | **Fly.io** | Single-machine deployment (shared-cpu-2x, 1GB), SJC region, SQLite on encrypted volume |
| Scheduling | **APScheduler** (AsyncIOScheduler) | Automatic round advancement via configurable cron |
| Live Interface | **discord.py 2.4+** | In-process bot: slash commands (`/propose`, `/vote`, `/join`, `/tokens`, `/trade`, `/strategy`), EventBus subscriptions, private report DMs |
| AI Interpretation | **Anthropic API** (Claude Sonnet 4.5) | Natural-language rule → structured `RuleInterpretation` via sandboxed system prompt |
| AI Report | **Anthropic API** (Claude Sonnet 4.5) | Three report types: simulation, governance, private. Describe patterns, never prescribe actions. |
| AI Commentary | **Anthropic API** (Claude Sonnet 4.5) | Broadcaster-style game commentary and round highlight reels |
| Auth | **Authlib + itsdangerous** | Discord OAuth2 for governor identity |

## Simulation Engine

The simulation engine is a pure function: `simulate_game(home, away, rules, seed) → GameResult`. No side effects, no database access, no API calls. Input determines output. Four quarters plus an Elam Ending. Each possession resolves through a 13-step chain: defensive scheme selection → matchup assignment → ball handler selection → turnover check → shot clock violation check → action selection → move trigger → shot resolution → foul check → free throws → rebound → assist credit → stamina drain.

Nine agent attributes — Scoring, Passing, Defense, Speed, Stamina, IQ, Ego, Chaotic Alignment, and Fate — distributed across a 360-point budget per team. Four defensive schemes (man tight, man switch, zone, press) with distinct contest modifiers, turnover bonuses, and stamina costs. Agents have special moves gated by attribute thresholds that modify shot probabilities.

The RuleSet model contains 28 governable parameters across four tiers: Game Mechanics (13 params), Agent Behavior (9 params), League Structure (5 params), and Meta-Governance (1 param). Every parameter has typed ranges enforced by Pydantic field validators. Governors can change any of them through natural-language proposals.

## Governance System

All governance state is derived from an append-only event store. Every action — propose, amend, vote, trade, enact — is an immutable event. Token balances, rule snapshots, and standings are projections derived from the event log via the Repository pattern.

The proposal lifecycle: governor submits natural language → AI interpreter (sandboxed — sees only the proposal text and parameter definitions, never simulation state or player data) produces a `RuleInterpretation` → governor confirms or cancels → community votes → tally resolves with tier-appropriate threshold (simple majority through supermajority) → rule enacted or rejected. Amendments replace the interpretation on the ballot; the original proposer has no veto.

Input sanitization strips invisible Unicode, HTML tags, and prompt injection markers before any governor text reaches the AI. The interpreter includes explicit injection detection (`injection_flagged`).

Votes use weighted representation: each team's total weight is 1.0, divided equally among its active governors. BOOST tokens double a vote's weight. Vote tallies use strictly greater-than comparison — ties fail.

## AI Report System

Three report types, generated after each round:

The **simulation report** reflects on game results — statistical patterns, scoring trends, Elam activations, emergent behavior. The **governance report** reflects on proposal patterns, voting dynamics, and how the rule space is evolving. The **private report** reflects an individual governor's behavior back to them alone — voting patterns, proposal themes, token usage, consistency of philosophy.

All reports follow a single constraint: they DESCRIBE patterns, never PRESCRIBE actions. The AI observes; humans decide. This constraint is enforced in the system prompts and validated by the prescriptive language eval (S.2c).

Variant B prompts exist for A/B comparison testing (eval M.2).

## Evaluation Framework

Twelve eval modules organized into two tracks:

**Safety evals (S-track):** Prescriptive language scan (S.2c — flags reports that cross from observation to advice), entity grounding (S.2b — validates that reports reference real teams, agents, and rules), behavioral shift detection (S.2a — measures whether governor behavior changes after receiving a report).

**Measurement evals (M-track):** 20 golden eval cases (M.1), dual-prompt A/B comparison (M.2), treatment/control attribution (M.3), Governance Quality Index (M.4 — composite of Shannon entropy for proposal diversity, inverted Gini for participation breadth, keyword overlap for consequence awareness, normalized time-to-vote for deliberation), scenario flagging (M.6), and Opus-powered admin analysis (M.7).

The GQI (M.4) is the composite metric: four sub-metrics weighted equally. It measures whether governance is healthy — diverse, broad, responsive to reports, and deliberative.

## Development Tools

### AI Assistants

| Tool | Use |
|------|-----|
| **Claude Code** | Primary development partner. Architecture, implementation, testing, documentation. The CLAUDE.md file served as a living contract between human intent and AI execution. |
| **Claude Cowork** | Product management workflows — PM walkthrough, acceptance criteria generation, editorial calendar, this colophon. |
| **Anthropic Workbench** | Planned for prompt iteration on the AI interpretation and report system prompts. Not yet integrated — prompts are currently iterated in-code. |

### Planning and Writing

| Tool | Use |
|------|-----|
| **Every — Compound Engineering** | Early planning sessions. Did not work out of the box; Claude pushed back on the initial workflow, which turned out to be useful friction. |
| **Every — Sparkle** | Planning agent. Moved its working folder mid-session — frustrating but recoverable. |
| **Every — Monologue** | Voice-to-text capture for ideation and stream-of-consciousness notes. |
| **IA Writer** | Long-form document drafting and editing. |
| **BBEdit** | Text editing, grep searches across the doc suite, quick config file work. |

### Code Review

| Tool | Use |
|------|-----|
| **OpenAI Codex** | Automated code review across the codebase. |

### QA, Demo, and Operations

| Tool | Use |
|------|-----|
| **Rodney** (Simon Willison) | Headless Chrome automation for screenshots. Every page gets a Rodney capture in the demo pipeline. |
| **Showboat** (Simon Willison) | Executable markdown demo builder. Wraps `run_demo.sh` into a human-readable, runnable document. |
| **Fly.io CLI** | Deployment, scaling, log tailing, volume management. |
| **Discord Developer Portal** | Bot registration, permission scoping, gateway intent configuration. |
| **GitHub** | Version control and CI. Single point of failure — noted and accepted. |
| **uv** | Python package management and virtual environment. `uv sync`, `uv run pytest`, `uv run ruff`. |
| **ruff** | Linting and formatting (target: Python 3.12, line length 100). |
| **pytest + pytest-asyncio** | Test suite with `asyncio_mode = "auto"`. |

## Architecture Decisions

The application runs as a single process on one Fly.io machine. The FastAPI server, Discord bot, APScheduler, SSE broadcaster, and simulation engine share a process and a database connection pool. This is a deliberate choice: one process means no inter-service coordination, no message queues, no distributed state bugs.

The EventBus is an in-process pub/sub system that decouples the game loop from its consumers (SSE clients, Discord channels, eval runners). Events are fire-and-forget — a Discord posting failure never breaks a simulation round.

Discord was chosen as the governance interface because its API is well-understood and its bot framework is mature. The slash command UX (`/propose`, `/vote`, `/join`) maps cleanly to the governance lifecycle. Private reports are delivered via DM. Team channels are permission-gated to team role members only.

The interpreter is sandboxed: it receives only the proposal text and parameter definitions. It has no access to simulation state, game results, player data, or report content. This is both a security boundary and a design choice — the AI acts as a constitutional interpreter, not an omniscient advisor.

The demo pipeline (`scripts/demo_seed.py` + `scripts/run_demo.sh`) seeds four Portland-themed teams (Rose City Thorns, Burnside Breakers, St. Johns Herons, Hawthorne Hammers) with hand-tuned 360-point attribute budgets, runs rounds through the full game loop (sim → gov → reports → evals → commentary), and captures screenshots via Rodney. The output is a Showboat markdown artifact: proof the system works end-to-end.

## Build Timeline

Seven days. Twenty-three Claude Code sessions. One builder.

**Day 1 (Sessions 1–2):** CLAUDE.md alignment, frontend decision (HTMX+SSE over game engines), game loop architecture, simulation open questions resolved. The first hour was spent reading every doc and finding six misalignments in CLAUDE.md before writing a single line of code.

**Day 2 (Sessions 3–5):** League configuration, attribute model evolution (6→8→9 attributes across multiple edits), moves system, Fate mechanics, Elam Ending scoring, full defensive model (four schemes, matchup cost functions, stamina economics), season structure, venue and home court system, rule expressiveness architecture (three-layer: parameter changes → game effects → league effects), prompt injection defense plan. SIMULATION.md grew from a sketch to a 400+ line specification.

**Day 3 (Session 6):** Demo infrastructure. Integrated Rodney (headless Chrome screenshots) and Showboat (executable markdown demos) into a 15-step demo pipeline that proves the full govern→simulate→observe→reflect cycle end-to-end. Four Portland-themed teams seeded: Rose City Thorns, Burnside Breakers, St. Johns Herons, Hawthorne Hammers. 240 tests passing.

**Day 4 (Session 7):** Evals framework. Twelve eval modules across safety (S-track) and measurement (M-track). Private report privacy model verified at the type level — Pydantic rejects `report_type="private"` for rubric scoring. 327 tests.

**Day 5 (Sessions 8–10):** Discord governance commands wired to the service layer (`/propose` with AI interpretation, `/vote` with hidden ballots, `/tokens`, `/trade` with DM accept/reject, `/strategy`). APScheduler integration for automatic round advancement. AI commentary engine. Presenter pacing modes. 401 tests.

**Day 6 (Sessions 11–16):** CLAUDE.md accuracy audit (project structure had become fiction — 20+ real files missing, nonexistent files listed). Security hardening (session secrets, OAuth cookies, evals auth gate). Fly.io deployment — live at pinwheel.fly.dev. UX overhaul: Inter + JetBrains Mono typography, narration engine (60+ templates turning structured play data into vivid text), multi-round arena, narrative mock reports, mobile nav. Voice and identity pass — de-emphasized Blaseball as primary inspiration, established the game's own identity. 408 tests.

**Day 7 (Sessions 17–23):** Production fixes — play-by-play truncation hiding Elam winning plays, team page venue rendering hardened, production re-seed. Spider charts and individual agent pages — SVG nonagon spider charts with league-average shadow, full player profile pages with bio, game log, season averages, HTMX bio editing for governors. Simulation tuning — shot clock violation mechanic, scoring rebalance from 34 to 65 pts/team matching Unrivaled range, stamina management overhaul, Elam display fix. Home page redesigned as living league dashboard with hero, latest scores, standings, reports, upcoming matchups, and explainer grid. Governance page opened to public, rules page redesigned from config dump to tiered card layout, player-centric copy rewrite across all pages. "How to Play" onboarding page with rhythm section, Discord commands reference, FAQ, and join CTAs. 435 tests.

## Document System

Pinwheel Fates was designed document-first. The documents were written before or alongside the code — not after. The first hour of the build was spent reading docs and identifying misalignments, not writing code.

Core documents: VISION.md (goals and thesis), SIMULATION.md (engine specification), RUN_OF_PLAY.md (game loop and governance flow), PLAYER.md (agent attribute model), VIEWER.md (spectator experience), GAME_LOOP.md (tick-by-tick orchestration), GLOSSARY.md (shared vocabulary), INTERFACE_CONTRACTS.md (API contracts), INSTRUMENTATION.md (metrics and logging), OPS.md (deployment), DEMO_MODE.md (demo pipeline), SECURITY.md (threat model), ACCEPTANCE_CRITERIA.md (148 testable criteria, 81% automatable), PRODUCT_OVERVIEW.md (PM analysis), TABLE_OF_CONTENTS.md (master index).

The dev log, UX notes, and CLAUDE.md were maintained as distinct files throughout the build — each with a different audience and update cadence. The insight: when your co-builder is an AI, the documents *are* the product. They define the contract that Claude Code executes against.

## By the Numbers

| Metric | Value |
|--------|-------|
| Sessions | 23 |
| Days | 7 |
| Tests | 435 (zero lint errors throughout) |
| Source files | ~60 across 8 modules |
| Eval modules | 12 |
| Governable parameters | 28 |
| Agent attributes | 9 |
| Discord slash commands | 8 |
| Demo screenshots | 10 |
| Design documents | 15+ |
| Docker image size | 63 MB |

## Credits

**Builder:** David Jacobs

**AI Partners:** Claude (Anthropic) — via Claude Code, Claude Cowork, and the Claude API (Sonnet 4.5 for interpretation and reports). OpenAI Codex — code review.

**Inspirations:** Blaseball (The Game Band), David Lynch's *Catching the Big Fish*, [Resonant Computing](https://resonantcomputing.org), the Portland Trail Blazers logo (five lines spinning around a center — a pinwheel)

**Acknowledgments:** Kate Lee and the team at Every for the Compound Engineering, Sparkle, and Monologue tools. Simon Willison for Rodney and Showboat. The Fly.io team for infrastructure that a solo builder can actually operate. The Anthropic hackathon organizers for the 4% door.

---

*Built in February 2026. Set in type by machine; governed by humans.*
