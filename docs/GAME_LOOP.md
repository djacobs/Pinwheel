# Pinwheel Fates: Game Loop & Scheduler Architecture

## Overview

Pinwheel Fates runs continuously. Games simulate on a cron schedule. Governance tallies every Nth round. AI reports generate after each phase. The system breathes — simulate, govern, reflect, repeat.

This document defines how those pieces coordinate.

## The Two Clocks

Pinwheel has two schedules: one explicit (game rounds) and one derived (governance tallying tied to the round cadence).

### 1. Game Clock (`PINWHEEL_PRESENTATION_PACE`)

The presentation pace determines how often rounds advance. Pace modes map to cron intervals:

| Pace | Cron | Round cadence |
|------|------|---------------|
| `fast` | `*/1 * * * *` | Every 1 minute |
| `normal` | `*/5 * * * *` | Every 5 minutes |
| `slow` | `*/15 * * * *` | Every 15 minutes |
| `manual` | (none) | Admin-triggered only |

An explicit cron can override via `PINWHEEL_GAME_CRON`. When the game clock fires (`tick_round()`):

1. **Call `step_round()`** — the core orchestrator in `core/game_loop.py`. This:
   1. **Snapshots the current ruleset.** Rules are immutable for the duration of a simulation block.
   2. **Generates matchups.** The scheduler produces pairings for this round.
   3. **Simulates all games.** Each game is an independent pure function call: `simulate_game(home, away, rules, seed)`. Games within a round can run in parallel (no shared state).
   4. **Stores results.** Batch insert all GameResults into the database.
   5. **Tallies governance (if interval round).** See below.
   6. **Regenerates tokens** for all governors (on governance tally rounds only).
   7. **Generates reports.** Simulation report, and governance/private reports on tally rounds.
   8. **Runs evals** (if enabled).
   9. **Generates AI commentary** for each game.
2. **Present the round.** In replay mode (`PINWHEEL_PRESENTATION_MODE=replay`), the presenter streams play-by-play to fans via SSE over real time. In instant mode, results are available immediately.
3. **Publish events.** After presentation finishes (not during simulation), publish `presentation.game_finished`, `presentation.round_finished`, and `governance.window_closed` events. Discord notifications and standings updates fire from these events.

### 2. Governance Tallying (Interval-Based, Not Window-Based)

Governance is **not** a separate clock. It's integrated into `step_round()` and triggers every Nth round, controlled by `PINWHEEL_GOVERNANCE_INTERVAL` (default 3).

**How it works:**

Proposals, amendments, votes, and trades happen asynchronously via Discord commands at any time — there is no "open" or "close" window. When a tally round arrives (`round_number % governance_interval == 0`), `step_round()`:

1. **Gathers unresolved proposals.** Finds all `proposal.confirmed` events with no matching `proposal.passed` or `proposal.failed`.
2. **Tallies votes.** Weighted voting with per-team normalization. BOOST tokens double a governor's vote weight.
3. **Enacts passed proposals.** Validated rule changes applied to the RuleSet.
4. **Regenerates tokens.** 2 PROPOSE, 2 AMEND, 2 BOOST per governor per tally cycle.
5. **Generates governance + private reports.** AI analyzes voting patterns, coalitions, and individual behavior.

The governance interval is itself a governable parameter (`governance_rounds_interval` in Tier 4). Players can vote to make tallying more or less frequent.

### 3. Report Clock (Event-Driven, Not Scheduled)

Reports don't run on their own clock. They're triggered by the other two clocks and by season-level transitions. Opus 4.6's role is not just to report stats — it's to surface the social dynamics, governance patterns, and emergent narratives that players can't see from inside the system. Every report connects individual actions to the collective story.

| Trigger | Report Type | What It Analyzes |
|---------|-------------|-----------------|
| Game block completes | **Simulation report** (shared) | Game outcomes in context of recent rule changes. Did the rule change do what its proponents claimed? Who benefited? Which teams are rising/falling and why? Emerging matchup narratives. |
| Governance tally round | **Governance report** (shared) | Voting patterns, coalitions, power dynamics. Who voted together? Who traded tokens and why? How do governance actions connect to game outcomes? Is the social contract evolving or calcifying? Are some players gaming the system? |
| Governance tally round | **Private reports** (per-player) | Individual governance behavior, trading patterns, blind spots. "You voted for X, which benefited team Y — was that intentional?" Connects your actions to their consequences. Only you see this. |
| Tiebreaker game + governance | **Tiebreaker report** (shared) | The extra governance round before a tiebreaker — what did players change and why? The tiebreaker game result in context of those changes. |
| Playoff series ends | **Series report** (shared) | The arc of the series: how did governance between games shift the dynamics? Did the losing team try to rule-change their way to a win? What was the turning point? |
| Championship decided | **Season report** (shared) | The definitive season narrative. How the rules evolved from opening day. Which coalitions formed and broke. What governance strategies worked. The arc of the social contract. Awards (MVP, most chaotic, etc.) with narrative context. |
| Offseason governance closes | **Offseason report** (shared) | What carried forward, what was reset, and what that says about the community. Did the winners entrench their advantage or did the league vote for balance? |
| Every 7 rounds (1 RR) | **State of the League** (shared) | Periodic zoom-out. League-wide trends, power balance, rule evolution trajectory, emerging storylines. The AI as beat reporter. |

Report generation is I/O-bound (Opus 4.6 API calls). All reports for a trigger event can be generated in parallel. Private reports for all players can also be generated in parallel since they're independent.

## State Machine

Each round follows a sequential pipeline within `step_round()`, then hands off to the presenter:

```
                    ┌──────────────┐
                    │   IDLE       │
                    │ (waiting for │
                    │  cron tick)  │
                    └──────┬───────┘
                           │
                           ▼
                    ┌──────────────┐
                    │ SIMULATING   │
                    │ Run games,   │
                    │ store results│
                    └──────┬───────┘
                           │
                  (if tally round)
                           │
                    ┌──────┴───────┐
                    │ TALLYING     │
                    │ Resolve votes│
                    │ Enact rules  │
                    │ Regen tokens │
                    └──────┬───────┘
                           │
                    ┌──────┴───────┐
                    │ REPORTING    │
                    │ AI calls     │
                    │ (parallel)   │
                    └──────┬───────┘
                           │
                    ┌──────┴───────┐
                    │ PRESENTING   │
                    │ Stream play- │
                    │ by-play via  │
                    │ SSE          │
                    └──────┬───────┘
                           │
                    ┌──────┴───────┐
                    │ NOTIFY       │
                    │ Discord +    │
                    │ standings    │
                    └──────┬───────┘
                           │
                    ┌──────┴───────┐
                    │   IDLE       │
                    └──────────────┘
```

Simulation, tallying, and reporting are sequential within `step_round()`. Presentation runs after `step_round()` returns. Discord notifications fire after presentation completes (not during simulation) to avoid spoiling results. The only hard constraints:
- **Rule enactment happens atomically during the tally step, before the next simulation.**
- **Presentation starts after simulation completes** — you can't stream a game that hasn't been computed yet.
- **Discord notifications fire after presentation** — fans see the live show before getting spoilers.

## Implementation Architecture

### Background Task Runner

The game loop is a long-running background process, not a request handler. Options:

**Option A: FastAPI BackgroundTasks + APScheduler**
- APScheduler handles cron scheduling. FastAPI BackgroundTasks handle one-off async work (report generation, SSE broadcast).
- Lightweight. No external infrastructure. Runs in the same process as the API.
- Risk: if the process dies, the loop stops. Fine for hackathon, needs a process supervisor for production.

**Option B: Separate Worker Process**
- A dedicated `pinwheel.worker` process runs the game loop. Communicates with the API via the database and/or a message queue.
- Better separation of concerns. API can restart without interrupting simulation.
- Heavier. Requires inter-process coordination.

**Recommendation for hackathon: Option A.** APScheduler with AsyncIOScheduler, running inside the FastAPI process. Upgrade to Option B post-hackathon if needed.

### Game Presenter

The simulation computes a full game in milliseconds. Fans experience it over 20-30 minutes. These are two different systems.

**Simulator** (instant): `simulate_game(home, away, rules, seed) → GameResult`
- Runs when the game clock fires
- Produces the complete GameResult with full play-by-play
- Stores the result in the database immediately

**Presenter** (paced): `present_game(game_result, pace) → SSE stream`
- Takes a pre-computed GameResult and streams it to clients over time
- Drips out possession-by-possession events at a configurable pace
- Knows the whole story — can build tension toward dramatic moments
- Can highlight Moves that triggered, clutch Ego moments, Chaotic Alignment swings
- Pace is configurable: demo mode (faster), production mode (20-30 min per game)

```
Simulation (instant)          Presentation (paced)
┌──────────────┐             ┌──────────────────────┐
│ Game Clock   │             │ Presenter            │
│ fires        │             │                      │
│              │  GameResult │ Possession 1 → SSE   │
│ simulate()   ├────────────►│ Possession 2 → SSE   │
│              │             │ ...                   │
│ Store result │             │ [Move triggered!]     │
│ in DB        │             │ ...                   │
│              │             │ Final score → SSE     │
└──────────────┘             └──────────────────────┘
      ~100ms                       ~20-30 min
```

This separation means:
- Multiple games from a simulation block can be presented simultaneously or sequentially
- A game can be "replayed" later by re-presenting the stored GameResult
- The presenter can be upgraded independently (better animations, more drama) without touching the simulation
- Fans who join late can catch up or watch from the beginning

The presenter works in concert with the **Commentary Engine** (`ai/commentary.py`), which uses Opus 4.6 to generate live play-by-play narration. The commentary engine receives the full GameResult up front — it's an omniscient narrator pretending to watch live. Commentary is generated in batches ahead of the presenter's current position, cached with the GameResult for replay. See `docs/VIEWER.md` for the full commentary architecture, Arena layout, and API endpoint design.

### SSE Architecture

Server-Sent Events push real-time updates to connected clients. The presenter is the primary producer of game-related SSE events. The EventBus (`core/event_bus.py`) is the central pub/sub system — all events flow through it.

**Actually implemented events** (as of Session 40):

```
EventBus (in-process pub/sub)
    │
    ├── presentation.possession      → Possession result (paced by presenter)
    ├── presentation.game_starting   → Game presentation begins
    ├── presentation.game_finished   → Game presentation complete (triggers Discord)
    ├── presentation.round_finished  → Round presentation complete (triggers Discord)
    │
    ├── game.completed               → Game simulation finished (internal, before presentation)
    ├── round.completed              → Round simulation finished (internal)
    │
    ├── governance.window_closed     → Governance tally complete, results available
    │
    ├── report.generated             → Report (sim, gov, or private) generated
    │
    └── season.regular_season_complete → All scheduled rounds played
```

The SSE endpoint (`/api/events/stream`) subscribes to the EventBus and forwards events to connected web clients. Discord bot handlers subscribe to `presentation.*` events (not `game.*`) to avoid spoiling results before the live show finishes.

**Key design decision:** Discord notifications fire from `presentation.game_finished` and `presentation.round_finished`, not from `game.completed` or `round.completed`. In instant mode (no presenter), `tick_round()` publishes the presentation events directly so Discord still works.

### Seed Generation

Each game needs a deterministic seed. Seeds are derived from:

```
seed = hash(season_id, round_number, matchup_index, ruleset_hash)
```

This means: the same matchup under the same rules in the same round always produces the same game. Change any input — different game. This enables:
- Replay: re-simulate any historical game
- A/B testing: re-run a game under a different ruleset to see what would have changed
- Auditability: anyone can verify a game result by re-running the simulation

## Season Structure

A season is the complete competitive arc: regular season → tiebreakers → playoffs → championship → offseason governance. The season is the unit of narrative — rules evolve, rivalries deepen, and the AI reporter tracks the whole story.

### Regular Season

**Format:** 3 full round-robins across 21 rounds.

With 8 teams, each round has 4 simultaneous games (every team plays once per round). A full round-robin = 7 rounds (each team faces every other team once). 3 round-robins = 21 rounds, 21 games per team, 3 games against each opponent. Home/away alternates — venue matters.

```
Round-Robin 1 (Rounds 1-7)     Round-Robin 2 (Rounds 8-14)    Round-Robin 3 (Rounds 15-21)
Every team plays every other   Repeat, flipped home/away      Repeat, original home/away
team once. 4 games per round.  assignments.                    assignments.

  ↕ governance tally every       ↕ governance tally every        ↕ governance tally every
  N rounds (default 3,           N rounds                        N rounds
  configurable)
```

**Governance frequency:** How often governance tallies is controlled by `PINWHEEL_GOVERNANCE_INTERVAL` (default 3, governable via `governance_rounds_interval`). At interval 1, there are 21 tallies per season. At interval 3, there are 7. Players can vote to make tallying more or less frequent — more frequent means more reactive, chaotic play. Less frequent means more strategic, deliberate governance.

**Rhythm:** Each round = 1 simulation block. Proposals, votes, and trades happen asynchronously between rounds. When a tally round arrives, unresolved proposals are tallied and enacted. The cycle:

```
SIMULATE round N → TALLY (if N % interval == 0) → REPORT → PRESENT 4 games → NOTIFY Discord
  ↑                                                                                    │
  └────────────────────────────────────────────────────────────────────────────────────┘
```

This is the heartbeat. Early season: players explore the rule space, test proposals, see what happens. Mid season: coalitions form, targeted rule changes, strategic trading. Late season: playoff positioning, high-stakes governance, desperate moves.

### Standings & Tiebreakers

**Standings:** Win percentage, updated after each round.

**Tiebreakers:** If two or more teams are tied in win % at end of regular season:

1. **Head-to-head tiebreaker game.** The tied teams play a single game to determine seeding.
2. **Extra governance round** before the tiebreaker game. Players get one more window to adjust rules — a tiebreaker game under modified rules is a dramatic moment.
3. If 3+ teams are tied, tiebreaker games are played round-robin among the tied teams.

The tiebreaker game uses the current ruleset (including any changes from the extra governance round). Venue is the higher-seeded team's home court based on point differential as the secondary tiebreak.

### Playoffs

**Top 4 teams** qualify from the 8-team league. Seeds 1-4 by standings.

**Bracket:**

```
SEMIFINALS (Best-of-5)                    CHAMPIONSHIP (Best-of-7)
┌─────────────────┐
│ #1 Seed         │
│   vs.           ├───┐
│ #4 Seed         │   │
└─────────────────┘   │     ┌─────────────────┐
                      ├────►│ Champion        │
┌─────────────────┐   │     │   vs.           │
│ #2 Seed         │   │     │ Champion        │
│   vs.           ├───┘     └─────────────────┘
│ #3 Seed         │              Best-of-7
└─────────────────┘
     Best-of-5
```

**Home court:** Higher seed has home court advantage (more games at their venue). In a best-of-5: games 1, 2, 5 at higher seed's venue. In a best-of-7: games 1, 2, 5, 7 at higher seed's venue.

**Governance during playoffs:** Active. This is where Pinwheel gets truly strange — the rules can change between playoff games. A governance window opens between each game in a series. Your opponent's sharpshooter is destroying you? Propose moving the 3-point line back. Your team has high stamina? Propose longer quarters. The stakes are higher, the changes are more targeted, and the AI reporter tracks every move.

### Championship & End of Season

When the finals conclude:

1. **Champion crowned.** Final standings: champion, runner-up, semifinalists, non-qualifiers.
2. **Season awards.** MVP, best defender, most improved, most chaotic — the reporter generates these with narrative context, not just stats.
3. **Full-season report.** Opus 4.6 writes the definitive season narrative: how the rules evolved, which coalitions formed, what governance strategies worked, the arc from opening day to championship.
4. **Stats compilation.** Complete statistical record for every agent, team, and governance action.

### Offseason Governance

Between seasons, a special governance session opens. This is where the meta-game lives:

- **Ruleset carry-forward:** Do current rules persist into next season, or reset to defaults? This is a governance vote.
- **Roster changes:** Trades, draft picks, free agency — the mechanisms depend on what governance has enacted.
- **New agents:** If roster expansion or player retirement has been governed, new agents are generated.
- **Season parameters:** Players can vote on next season's structure — number of round-robins, playoff format, governance window frequency.
- **Rule reset scope:** Maybe some tiers reset and others don't. Tier 1 (game mechanics) might reset while Tier 4 (meta-governance) persists. This is itself a governance decision.

The offseason governance window is longer than regular windows. It's the constitutional convention between seasons.

### Season Timeline (Production Mode)

```
REGULAR SEASON (21 rounds × ~1 hour = ~21 hours)
│
├── Round 1:  4 games → reports
├── Round 2:  4 games → reports
├── Round 3:  4 games → governance tally → token regen → reports (tally round)
├── ...
├── Round 21: 4 games → governance tally → reports (tally round)
│
├── STANDINGS FINALIZED
│   └── Tiebreakers if needed (extra governance round + tiebreaker game)
│
├── PLAYOFFS
│   ├── Semifinals: Best-of-5 (up to 10 games, governance between each)
│   └── Finals: Best-of-7 (up to 7 games, governance between each)
│
├── CHAMPIONSHIP
│   └── Season reports, awards, narrative
│
└── OFFSEASON GOVERNANCE
    └── Extended window: carry-forward vote, roster changes, next season params
```

Total season duration in production mode: ~25-30 hours of active play (21 rounds + up to 17 playoff games). Spread across days, this means a season could run Monday through Friday with games every hour during active periods.

## Dev/Staging vs. Production Mode

| Parameter | Dev/Staging | Production |
|-----------|-------------|------------|
| `PINWHEEL_PRESENTATION_PACE` | `fast` (1 min rounds) | `slow` (15 min rounds) |
| `PINWHEEL_GOVERNANCE_INTERVAL` | `3` (every 3 rounds) | `3` (every 3 rounds, governable) |
| `PINWHEEL_PRESENTATION_MODE` | `instant` or `replay` | `replay` |
| Teams | 4-8 | 8 |
| Round-robins per season | 1 (7 rounds) | 3 (21 rounds) |
| Games per round | 2-4 (every team plays once) | 4 (every team plays once) |
| Playoffs | Best-of-1 semis, best-of-3 finals | Best-of-5 semis, best-of-7 finals |
| Report latency target | <5s | <15s (can batch) |
| Token regen | Every tally round | Every tally round |
| Full season duration | ~30 minutes | ~25-30 hours |

Dev/staging mode runs a complete season (regular season → tiebreakers → playoffs → championship → offseason governance) in roughly 30 minutes. Good for testing the full arc, validating governance interactions, and producing video content for the hackathon.

## Error Handling

- **Simulation failure:** If a game throws an exception, log it, skip that matchup, continue the block. Never let one bad game stop the league.
- **AI call failure:** If report generation fails, log it and serve stale reports with a "report unavailable" indicator. The game continues without reports.
- **SSE disconnect:** Clients that disconnect and reconnect get a catch-up payload of events since their last received event ID.
- **Rule enactment failure:** If a passed proposal produces an invalid ruleset (should be caught by validation, but belt-and-suspenders), reject the enactment, log the error, notify players, and keep the previous ruleset.

## Resolved Questions

1. ~~**Governance window timing:**~~ **Resolved (Session 37).** Governance is interval-based, not window-based. Tallying happens every Nth round (`PINWHEEL_GOVERNANCE_INTERVAL`, default 3). No separate governance clock.
2. **Concurrent simulation blocks:** If a game clock fires while the previous block is still simulating, should we queue, skip, or run concurrently? Queue is safest.
3. **Report priority:** If report generation is slow, should simulation reports take priority over private reports? Shared content serves more players.
