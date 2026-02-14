# Tech Architecture Document — Plan

## Status: PLAN
## Date: 2026-02-14
## Priority: P1

---

## Document Structure (12 Sections)

### 1. Simulation Engine

**What to document:** The pure-function basketball simulation pipeline.

**Content:**

**Pipeline:** `simulate_game()` → `_run_quarter()` (×4) → `_run_elam()` (optional) → final result

**`simulate_game()` (`core/simulation.py`):**
- Pure function: teams, ruleset, seed → deterministic `GameResult`
- Seeds Python `random.Random` for reproducibility
- 4 quarters via `_run_quarter()`, optional Elam Ending via `_run_elam()`
- Returns `GameResult` with scores, play-by-play, box scores, quarter scores, seed

**Quarter simulation (`_run_quarter()`):**
- `max_possessions = quarter_minutes * possessions_per_minute`
- Each possession: `resolve_possession()` with offense/defense matchup
- Stamina drain per possession, recovery between quarters
- Shot clock enforcement, foul tracking per quarter

**Possession resolution (`core/possession.py`):**
1. `select_action()` — weighted choice: two_pointer, three_pointer, drive, post_up
2. Action-specific resolution (shot probability from hooper attributes + modifiers)
3. Foul check → free throws if fouled
4. Rebound on miss → offensive rebound chance
5. Turnover/steal check
6. Assist attribution
7. Returns `PossessionResult` with all events

**Elam Ending (`core/scoring.py`):**
- Activated when quarter 4 reaches `elam_threshold_minutes` remaining
- Target = leading score + `elam_target_margin`
- Untimed play until a team reaches target
- No clock, no fouls (configurable), pure scoring race

**Hook system (`core/hooks.py`):**
- `HookPoint` enum with 11 values
- `GameEffect` protocol: `should_fire()` + `apply()`
- `fire_hooks()`: iterates effects list per hook point
- Currently scaffolded — effects list is empty (effects system planned)

**Key design decisions:**
- Pure functions with seeded RNG for deterministic replay
- No side effects — simulation never touches DB
- Strategy integration: `TeamStrategy` modifies shot biases, defensive intensity, pace
- Home court advantage: configurable shooting boost

**Source files:** `core/simulation.py`, `core/possession.py`, `core/scoring.py`, `core/defense.py`, `core/moves.py`, `core/state.py`, `core/hooks.py`

---

### 2. Season Lifecycle

**What to document:** The 8-phase state machine from SETUP to COMPLETE.

**Phases (defined in `core/season.py`):**
```
SETUP → ACTIVE → TIEBREAKER_CHECK → TIEBREAKERS → PLAYOFFS → CHAMPIONSHIP → OFFSEASON → COMPLETE
```

**Phase transitions:**
- **SETUP → ACTIVE:** `start_new_season()` creates teams, hoopers, schedule, initial ruleset
- **ACTIVE → TIEBREAKER_CHECK:** All regular season rounds completed
- **TIEBREAKER_CHECK → TIEBREAKERS or PLAYOFFS:** `check_tiebreakers()` — if any teams tied for playoff spots, generate tiebreaker games
- **TIEBREAKERS → PLAYOFFS:** Tiebreakers resolved
- **PLAYOFFS → CHAMPIONSHIP:** Finals completed, champion determined
- **CHAMPIONSHIP → OFFSEASON → COMPLETE:** Awards ceremony, season archival

**Playoff bracket generation (`generate_playoff_bracket()`):**
- Top 4 teams by record (configurable)
- Semifinal matchups: 1v4 and 2v3
- Single-elimination (configurable series length)

**Awards system (`compute_awards()`):**
- 6 awards computed from box scores and governance events:
  - MVP (highest PPG), Defensive Player (highest SPG), Most Efficient (highest FG%)
  - Most Active Governor, Coalition Builder, Rule Architect

**Season archival (`archive_season()`):**
- Creates `SeasonArchiveRow` with frozen standings, ruleset, rule change history, champion info
- Multiseason: `start_new_season()` can carry forward rules from previous season

**Legacy status normalization:** Old status values (active, complete) mapped to new 8-phase model for backward compatibility.

**Source files:** `core/season.py`, `core/scheduler.py`

---

### 3. Presenter System (Simulation/Presentation Split)

**What to document:** How instant simulation decouples from real-time replay.

**Core concept:** Simulation runs instantly (CPU-bound, deterministic). Presentation replays stored results over real time. Players experience games "live" while results are already computed and stored.

**Two modes (`PINWHEEL_PRESENTATION_MODE`):**
- **instant:** Games appear immediately after simulation. Used in development.
- **replay:** Games replay over `PINWHEEL_QUARTER_REPLAY_SECONDS` per quarter (default 300s = 5min). Used in production.

**`present_round()` (`core/presenter.py`):**
- Takes `RoundResult` with all `GameResult` objects
- Replays each game's play-by-play events over wall-clock time
- Emits SSE events: `presentation.game_starting`, `presentation.possession`, `presentation.game_finished`, `presentation.round_finished`
- Concurrent replay of all games in a round via `asyncio.gather`

**`present_game()` inner loop:**
- Divides play-by-play events into quarters
- Each quarter replays over `quarter_replay_seconds`
- Inter-event delay calculated from remaining quarter time / remaining events
- Live score tracking via `LiveGameState`

**Deploy recovery (`_resume_presentation()` in `scheduler_runner.py`):**
- On restart, checks `bot_state` for `presentation_active` flag
- If active: loads the round's game results, reconstructs presentation, resumes replay
- Prevents duplicate or missed game presentations on deploy

**SSE consumption:** Arena page subscribes to EventBus events for live updates. `LiveGameState` is also server-rendered so page reloads get current scores.

**Source files:** `core/presenter.py`, `core/narrate.py`, `core/scheduler_runner.py`

---

### 4. AI Systems

**What to document:** All five AI subsystems with their models, prompts, and constraints.

**4.1 Interpreter** (`ai/interpreter.py`)
- Takes raw proposal text + current RuleSet, outputs `RuleInterpretation`
- Uses Sonnet (`claude-sonnet-4-5-20250929`) with max_tokens=500
- Sandboxed: sees ONLY proposal text and parameter definitions with ranges
- `_build_parameter_description()` generates parameter context
- Also handles strategy interpretation: `interpret_strategy()` maps natural language to `TeamStrategy`
- Mock fallback: `interpret_proposal_mock()` uses regex pattern matching

**4.2 Classifier** (`ai/classifier.py`)
- Pre-flight prompt injection detection using Haiku (`claude-haiku-4-5-20251001`)
- Three-way classification: "legitimate", "suspicious", "injection"
- Returns `ClassificationResult` with classification, confidence (0-1), reason
- **Fail-open design:** on error, returns "legitimate" with confidence=0

**4.3 Reporter** (`ai/report.py`)
- Three report types: simulation, governance, private
- Uses Sonnet with max_tokens=1500
- **Describe-don't-prescribe constraint:** Reports never contain directive language. Measured by S.2c eval.
- Mock fallbacks for all three types

**4.4 Mirror System** (`ai/mirror.py`)
- 8 mirror types defined in `MirrorType`
- A/B testing infrastructure: Variant A (verbose) vs Variant B (terse)
- `generate_mirror_with_prompt()` accepts arbitrary prompt template for testing

**4.5 Commentary** (`ai/commentary.py`)
- Per-game commentary and per-round highlight reel
- System prompt: "energetic, dramatic, slightly absurd sports broadcaster"
- Playoff-aware: special instructions for semifinal vs finals

**4.6 Rule Evaluator** (`evals/rule_evaluator.py`)
- Admin-facing, uses Opus (`claude-opus-4-6`) for deep reasoning
- Unlike reporters, the evaluator explicitly prescribes: suggests experiments, identifies stale parameters
- Outputs `RuleEvaluation`: suggested_experiments, stale_parameters, equilibrium_notes, flagged_concerns

---

### 5. Governance Pipeline

**Full flow:**
1. **Submit:** Governor types natural language via Discord `/propose`
2. **Sanitize:** `sanitize_text()` strips invisible Unicode, HTML tags, prompt injection markers
3. **Classify:** `classify_injection()` (Haiku) — fail-open
4. **Interpret:** `interpret_proposal()` (Sonnet) → `RuleInterpretation`
5. **Confirm:** Governor reviews via Discord interactive view (`ProposalConfirm`)
6. **Flag (optional):** If tier >= 5 or confidence < 0.5, emits `proposal.flagged_for_review`
7. **Vote:** Other governors vote yes/no via `/vote`. Optional BOOST token doubles weight.
8. **Veto (optional):** Admin can veto before tally. Refunds PROPOSE token.
9. **Tally:** `tally_governance()` runs on governance interval. Strictly-greater-than threshold (ties fail).
10. **Enact:** If passed, `apply_rule_change()` creates new RuleSet via Pydantic validation.

**Token economy:**
- Three types: PROPOSE, AMEND, BOOST
- Balances **never stored as mutable state** — derived from event log
- `get_token_balance()` computes from events on read

**Tier system:**
- Tier 1-2: Core mechanics (simple majority)
- Tier 3-4: League structure / meta-governance (60%)
- Tier 5-6: Game effects (67%)
- Tier 7+: Extreme changes (75%)

**Vote weight:** Each team's total weight = 1.0, divided equally among active governors. BOOST doubles a vote's weight.

**Event sourcing:** All governance state in `governance_events` table (append-only). 16+ event types.

**Source files:** `core/governance.py`, `core/tokens.py`

---

### 6. Hook System / Effects

**Current GameEffect Protocol:**
- `HookPoint` enum with 11 values
- `GameEffect` protocol: `should_fire()` + `apply()`
- `fire_hooks()`: iterates effects list
- Currently empty — Day 1 scaffold

**Upcoming Proposal Effects System** (see `docs/plans/2026-02-14-proposal-effects-system.md`):
- Replaces enum-based hooks with string-based hierarchical hooks (e.g., "sim.shot.post", "round.pre")
- New `Effect` protocol with `effect_id`, `hook_points`, `lifetime`
- `HookContext`: unified context for all effects
- `HookResult`: mutations returned by effects
- `EffectRegistry` for registration/deregistration
- `MetaStore`: in-memory read/write cache for `meta` JSON columns
- Meta columns planned for 7 tables
- Effects persisted as append-only governance events

---

### 7. Database Schema

**12 Tables:**

| Table | ORM Class | Purpose |
|-------|-----------|---------|
| `leagues` | `LeagueRow` | Top-level league container |
| `seasons` | `SeasonRow` | Season within a league. 8 phases, starting/current ruleset, config JSON |
| `teams` | `TeamRow` | Belongs to season. Name, colors, motto, venue JSON |
| `hoopers` | `HooperRow` | Belongs to team+season. Attributes JSON, moves JSON, backstory |
| `game_results` | `GameResultRow` | Per-game result. Scores, seed, ruleset snapshot, play-by-play JSON |
| `box_scores` | `BoxScoreRow` | Per-hooper-per-game stats. Full stat line |
| `governance_events` | `GovernanceEventRow` | **Append-only event store.** Source of truth for all governance |
| `reports` | `ReportRow` | AI-generated reports. Content as Text |
| `players` | `PlayerRow` | Discord-authenticated player identity |
| `schedule` | `ScheduleRow` | Round-robin and playoff schedule |
| `bot_state` | `BotStateRow` | Key-value store for Discord bot state |
| `season_archives` | `SeasonArchiveRow` | Frozen snapshot of completed season |
| `eval_results` | `EvalResultRow` | Eval results. **Never contains private report content.** |

**Inline migrations** (in `main.py` lifespan):
- `game_results.presented` (BOOLEAN)
- `teams.color_secondary` (VARCHAR)
- `players.team_id`, `players.enrolled_season_id` (VARCHAR)
- `hoopers.is_active` (BOOLEAN)

**Planned:** `meta` JSON columns on 7 tables (effects system).

---

### 8. Environment Variables

| Variable | Default | System | Description |
|----------|---------|--------|-------------|
| `ANTHROPIC_API_KEY` | `""` | AI | Claude API key. Mock fallback if unset. |
| `DISCORD_BOT_TOKEN` | `""` | Discord | Bot token |
| `DISCORD_GUILD_ID` | `""` | Discord | Target guild ID |
| `DISCORD_CHANNEL_ID` | `""` | Discord | Primary channel ID |
| `DISCORD_ENABLED` | `false` | Discord | Master toggle |
| `DISCORD_CLIENT_ID` | `""` | Auth | OAuth2 client ID |
| `DISCORD_CLIENT_SECRET` | `""` | Auth | OAuth2 client secret |
| `DISCORD_REDIRECT_URI` | `http://localhost:8000/auth/callback` | Auth | OAuth2 callback URL |
| `SESSION_SECRET_KEY` | auto-generated (dev) | Auth | Session signing key. Required in production. |
| `DATABASE_URL` | `sqlite+aiosqlite:///pinwheel.db` | Database | SQLAlchemy connection string |
| `PINWHEEL_ENV` | `development` | All | development / staging / production |
| `PINWHEEL_PRESENTATION_PACE` | `slow` | Presenter | fast/normal/slow/manual |
| `PINWHEEL_PRESENTATION_MODE` | `replay` | Presenter | instant/replay. Forced to replay in production. |
| `PINWHEEL_GOVERNANCE_INTERVAL` | `1` | Game Loop | Tally governance every N rounds |
| `PINWHEEL_GOV_WINDOW` | `900` | Governance | Window duration in seconds |
| `PINWHEEL_EVALS_ENABLED` | `true` | Evals | Run evals after each round |
| `PINWHEEL_ADMIN_DISCORD_ID` | `""` | Governance | Admin Discord user ID |
| `PINWHEEL_LOG_LEVEL` | `INFO` | All | Python logging level |

**Pace-to-cron mapping:** fast=1min, normal=5min, slow=15min, manual=None

---

### 9. Eval Framework

**Purpose:** Measure report quality and governance health without violating privacy. Report content is for the player, not the developer.

**S-series (per-round, automated):**

| Eval | Module | What It Measures |
|------|--------|------------------|
| S.1 | `rubric.py` | Manual scoring of PUBLIC reports only. 5 dimensions, scale 1-5. |
| S.2a | `behavioral.py` | Governance action shift detection. Never reads report content. |
| S.2b | `grounding.py` | Entity reference validation. Content never stored. |
| S.2c | `prescriptive.py` | Directive language scan. 12 regex patterns. Returns count only. |
| M.6 | `flags.py` | Scenario flagging. 5 detectors (blowout, unanimity, stagnation, collapse, backfire). |

**M-series (periodic, deeper):**

| Eval | Module | What It Measures |
|------|--------|------------------|
| M.1 | `golden.py` | 20 test cases (8 sim, 7 gov, 5 private). |
| M.2 | `ab_compare.py` | Dual-prompt comparison. Variant A vs B scoring. |
| M.3 | `attribution.py` | Treatment/control report delivery. Aggregate delta only. |
| M.4 | `gqi.py` | Governance Quality Index. 4 sub-metrics weighted 25% each. |
| M.7 | `rule_evaluator.py` | Opus-powered admin analysis. Prescriptive (unlike reports). |

---

### 10. Round Orchestration

**`step_round()` (game_loop.py):** The core round executor.
1. Load season + ruleset
2. Load schedule for this round
3. Load teams (with hoopers) into `teams_cache`
4. Load team strategies
5. Simulate each game
6. Store game results and box scores
7. Generate per-game commentary
8. Generate highlight reel
9. Tally governance (if round % interval == 0)
10. Regenerate tokens
11. Generate reports: simulation, governance, private
12. Run evals (non-blocking)
13. Check season completion → playoff progression
14. Publish `round.completed` event
15. Return `RoundResult`

**`tick_round()` (scheduler_runner.py):** APScheduler wrapper.
1. Skip if presentation still active
2. Find active season
3. Handle championship/completed phases
4. Determine next round number
5. Call `step_round()` with `suppress_spoiler_events=True` for replay mode
6. In instant mode: mark games presented, publish events
7. In replay mode: launch background task `_present_and_clear()`

---

### 11. Event Bus

- `EventBus` class: in-memory async pub/sub using `asyncio.Queue`
- `publish(event_type, data)`: wraps in envelope, puts on matching + wildcard queues
- `subscribe(event_type, max_size=100)`: returns `Subscription` (async context manager + iterator)
- Fire-and-forget: events dropped if no subscribers
- Used by SSE endpoints, Discord bot, presenter

**Source files:** `core/event_bus.py`

---

### 12. Key Design Decisions

1. **Season lifecycle as 8-phase state machine** — Models the full arc with explicit transitions and backward-compatible status normalization.

2. **Simulation/Presentation split** — Simulation is instant and pure. Presentation replays over real time. Deploy recovery reconstructs from stored results.

3. **Prompt injection classification as separate layer** — Cheap Haiku pre-flight, fail-open. Defense in depth with classifier + interpreter + sanitization.

4. **Awards system** — 6 awards spanning gameplay + governance. Reflects the game's dual nature.

5. **Mirror A/B testing** — Variant A (verbose) vs B (terse). Empirical prompt improvement.

6. **Governance is append-only events** — All state derived from event stream. Token balances computed on read.

7. **Rules are parameterized (changing with effects system)** — Currently ~30 parameters. Upcoming effects system enables "every team gets swagger."

8. **Token balances from events, never stored** — Event-sourcing choice. No race conditions, full audit trail.

9. **Report content privacy boundary** — Private reports never exposed through evals. Pydantic `Literal` type restriction. Prescriptive eval returns counts only.

10. **In-memory EventBus (not Redis)** — Acceptable for single-process hackathon architecture. Fire-and-forget.

11. **Inline migrations** — `_add_column_if_missing()` for schema evolution.

12. **Describe-don't-prescribe as measurable constraint** — Not just a prompt instruction but an eval (S.2c with 12 regex patterns).

---

## Cross-References

- `CLAUDE.md` — Developer workflow and code standards
- `docs/RUN_OF_PLAY.md` — Game rules from the player perspective
- `docs/SIMULATION.md` — Simulation engine details
- `docs/GAME_LOOP.md` — Game loop documentation
- `docs/INSTRUMENTATION.md` — Observability spec
- `docs/plans/2026-02-14-proposal-effects-system.md` — Upcoming effects architecture
- `docs/plans/2026-02-14-season-lifecycle.md` — Season lifecycle design
- `docs/plans/2026-02-14-api-architecture-doc.md` — API architecture companion doc
