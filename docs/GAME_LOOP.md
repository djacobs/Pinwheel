# Pinwheel Fates: Game Loop & Scheduler Architecture

## Overview

Pinwheel Fates runs continuously. Games simulate on a cron schedule. Governance windows open and close on a cadence. AI mirrors generate after each phase. The system breathes — simulate, govern, reflect, repeat.

This document defines how those pieces coordinate.

## The Three Clocks

Pinwheel has three independent schedules that interleave:

### 1. Game Clock (`PINWHEEL_GAME_CRON`)

Games run at fixed times (e.g., top of every hour). When the game clock fires:

1. **Snapshot the current ruleset.** Rules are immutable for the duration of a simulation block. If a governance window passes new rules mid-block, they take effect at the *next* block.
2. **Generate matchups.** The scheduler produces pairings for this block based on the current `schedule_format` and `games_per_round` parameters.
3. **Simulate all games.** Each game is an independent pure function call: `simulate_game(home, away, rules, seed)`. Games within a block can run in parallel (no shared state).
4. **Store results.** Batch insert all GameResults into the database.
5. **Start presenting.** Hand each GameResult to the game presenter, which streams play-by-play to fans over 20-30 minutes via SSE. Standings update when each game's presentation completes (final score revealed).
6. **Trigger simulation mirror.** Queue an AI call to analyze results in context of recent rule changes. (Mirror sees the full results immediately, even while presentation is still streaming to fans.)

### 2. Governance Clock (`PINWHEEL_GOV_WINDOW`)

Governance windows open on a schedule (e.g., twice daily, or every 15 minutes in demo mode). A governance window has three phases:

**Window Open:**
- Token regeneration runs (PROPOSE, AMEND, BOOST replenished per rates).
- Proposal submission, amendment, voting, and trading are enabled.
- Players interact with the governance panel.

**Window Close:**
- Voting on active proposals resolves. Passed proposals are enacted — their structured rule changes are validated and applied to the RuleSet.
- The governance event log captures all actions as immutable events.
- Trading closes.

**Post-Window:**
- Trigger governance mirror: AI analyzes voting patterns, coalitions, power dynamics.
- Trigger private mirrors: per-player reflections on their governance behavior.
- Broadcast mirror updates via SSE.

### 3. Mirror Clock (Event-Driven, Not Scheduled)

Mirrors don't run on their own clock. They're triggered by the other two clocks and by season-level transitions. Opus 4.6's role is not just to report stats — it's to surface the social dynamics, governance patterns, and emergent narratives that players can't see from inside the system. Every mirror connects individual actions to the collective story.

| Trigger | Mirror Type | What It Analyzes |
|---------|-------------|-----------------|
| Game block completes | **Simulation mirror** (shared) | Game outcomes in context of recent rule changes. Did the rule change do what its proponents claimed? Who benefited? Which teams are rising/falling and why? Emerging matchup narratives. |
| Governance window closes | **Governance mirror** (shared) | Voting patterns, coalitions, power dynamics. Who voted together? Who traded tokens and why? How do governance actions connect to game outcomes? Is the social contract evolving or calcifying? Are some players gaming the system? |
| Governance window closes | **Private mirrors** (per-player) | Individual governance behavior, trading patterns, blind spots. "You voted for X, which benefited team Y — was that intentional?" Connects your actions to their consequences. Only you see this. |
| Tiebreaker game + governance | **Tiebreaker mirror** (shared) | The extra governance round before a tiebreaker — what did players change and why? The tiebreaker game result in context of those changes. |
| Playoff series ends | **Series mirror** (shared) | The arc of the series: how did governance between games shift the dynamics? Did the losing team try to rule-change their way to a win? What was the turning point? |
| Championship decided | **Season mirror** (shared) | The definitive season narrative. How the rules evolved from opening day. Which coalitions formed and broke. What governance strategies worked. The arc of the social contract. Awards (MVP, most chaotic, etc.) with narrative context. |
| Offseason governance closes | **Offseason mirror** (shared) | What carried forward, what was reset, and what that says about the community. Did the winners entrench their advantage or did the league vote for balance? |
| Every 7 rounds (1 RR) | **State of the League** (shared) | Periodic zoom-out. League-wide trends, power balance, rule evolution trajectory, emerging storylines. The AI as beat reporter. |

Mirror generation is I/O-bound (Opus 4.6 API calls). All mirrors for a trigger event can be generated in parallel. Private mirrors for all players can also be generated in parallel since they're independent.

## State Machine

```
                    ┌──────────────┐
                    │   IDLE       │
                    │ (waiting for │
                    │  next event) │
                    └──────┬───────┘
                           │
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
┌──────────────┐   ┌───────────┐     ┌──────────┐
│ SIMULATING   │   │ GOVERNING │     │ MIRRORING│
│              │   │           │     │          │
│ Run games    │   │ Window is │     │ AI calls │
│ (instant)    │   │ open for  │     │ running  │
│ Store results│   │ players   │     │          │
└──────┬───────┘   └─────┬─────┘     └────┬─────┘
       │                 │                │
       ▼                 ▼                │
┌──────────────┐  ┌────────────┐          │
│ PRESENTING   │  │ ENACTING   │          │
│              │  │            │          │
│ Stream play- │  │ Resolve    │          │
│ by-play via  │  │ votes,     │          │
│ SSE (20-30m) │  │ apply rules│          │
└──────┬───────┘  └─────┬──────┘          │
       │                │                 │
       └────────────────┼─────────────────┘
                        ▼
                    ┌──────────────┐
                    │   IDLE       │
                    └──────────────┘
```

These states can overlap. Simulation is instant; presentation runs long (20-30 minutes per game). A governance window can be open while games are being presented. Mirrors can generate while games are streaming. The only hard constraints:
- **Rule enactment happens atomically between simulation blocks, never during one.**
- **Presentation starts after simulation completes** — you can't stream a game that hasn't been computed yet.

## Implementation Architecture

### Background Task Runner

The game loop is a long-running background process, not a request handler. Options:

**Option A: FastAPI BackgroundTasks + APScheduler**
- APScheduler handles cron scheduling. FastAPI BackgroundTasks handle one-off async work (mirror generation, SSE broadcast).
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

Server-Sent Events push real-time updates to connected clients. The presenter is the primary producer of game-related SSE events:

```
FastAPI SSE Endpoint (/events/stream)
    │
    ├── game.possession   → Possession result (paced by presenter)
    ├── game.move         → A Move triggered during a possession
    ├── game.highlight    → Presenter-flagged dramatic moment
    ├── game.commentary   → AI-generated commentary line (see VIEWER.md)
    ├── game.quarter_end  → Quarter completed
    ├── game.elam_start   → Elam Ending activated, target score set
    ├── game.result       → Final score (game presentation complete)
    ├── game.boxscore     → Full box score available
    ├── standings.update  → Standings changed
    ├── governance.open   → Governance window opened
    ├── governance.close  → Window closed, results available
    ├── governance.proposal → New proposal submitted
    ├── governance.vote    → Vote cast (anonymized until window close)
    ├── mirror.simulation  → New simulation mirror available
    ├── mirror.governance  → New governance mirror available
    ├── mirror.private     → Private mirror updated (per-player, filtered)
    ├── mirror.series      → Playoff series mirror available
    ├── mirror.season      → Full-season narrative mirror available
    ├── mirror.league      → State of the League periodic mirror
    ├── season.round       → Round completed, standings updated
    ├── season.tiebreaker  → Tiebreaker game scheduled
    ├── season.playoffs    → Playoff bracket set
    ├── season.series      → Series update (game result within a series)
    ├── season.champion    → Championship decided
    ├── season.awards      → Season awards announced
    └── season.offseason   → Offseason governance window opened
```

Each SSE event carries a type and a JSON payload. Clients subscribe once and filter by event type. Private mirror events are filtered server-side — a player only receives their own.

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

A season is the complete competitive arc: regular season → tiebreakers → playoffs → championship → offseason governance. The season is the unit of narrative — rules evolve, rivalries deepen, and the AI mirror tracks the whole story.

### Regular Season

**Format:** 3 full round-robins across 21 rounds.

With 8 teams, each round has 4 simultaneous games (every team plays once per round). A full round-robin = 7 rounds (each team faces every other team once). 3 round-robins = 21 rounds, 21 games per team, 3 games against each opponent. Home/away alternates — venue matters.

```
Round-Robin 1 (Rounds 1-7)     Round-Robin 2 (Rounds 8-14)    Round-Robin 3 (Rounds 15-21)
Every team plays every other   Repeat, flipped home/away      Repeat, original home/away
team once. 4 games per round.  assignments.                    assignments.

  ↕ governance window            ↕ governance window             ↕ governance window
  between rounds (default:       between rounds                  between rounds
  every round, configurable)
```

**Governance frequency:** How often governance windows open is controlled by `governance_rounds_interval` (default 1 = every round, governable). At interval 1, there are 21 governance windows per season. At interval 3, there are 7. Players can vote to make governance more or less frequent — more frequent means more reactive, chaotic, Blaseball-energy play. Less frequent means more strategic, deliberate governance.

**Rhythm:** Each round = 1 simulation block. When `governance_rounds_interval` rounds have elapsed, a governance window opens. The cycle:

```
SIMULATE round N → PRESENT 4 games → MIRROR analyzes →
  if N % governance_rounds_interval == 0:
    GOVERNANCE window opens → players react → window closes → rules update →
SIMULATE round N+1 → ...
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

**Governance during playoffs:** Active. This is the most Blaseball thing about Pinwheel Fates — the rules can change between playoff games. A governance window opens between each game in a series. Your opponent's sharpshooter is destroying you? Propose moving the 3-point line back. Your team has high stamina? Propose longer quarters. The stakes are higher, the changes are more targeted, and the AI mirror tracks every move.

### Championship & End of Season

When the finals conclude:

1. **Champion crowned.** Final standings: champion, runner-up, semifinalists, non-qualifiers.
2. **Season awards.** MVP, best defender, most improved, most chaotic — the mirror generates these with narrative context, not just stats.
3. **Full-season mirror.** Opus 4.6 writes the definitive season narrative: how the rules evolved, which coalitions formed, what governance strategies worked, the arc from opening day to championship.
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
├── Round 1:  4 games → governance window → mirrors
├── Round 2:  4 games → governance window → mirrors
├── ...
├── Round 21: 4 games → governance window → mirrors
│
├── STANDINGS FINALIZED
│   └── Tiebreakers if needed (extra governance round + tiebreaker game)
│
├── PLAYOFFS
│   ├── Semifinals: Best-of-5 (up to 10 games, governance between each)
│   └── Finals: Best-of-7 (up to 7 games, governance between each)
│
├── CHAMPIONSHIP
│   └── Season mirrors, awards, narrative
│
└── OFFSEASON GOVERNANCE
    └── Extended window: carry-forward vote, roster changes, next season params
```

Total season duration in production mode: ~25-30 hours of active play (21 rounds + up to 17 playoff games). Spread across days, this means a season could run Monday through Friday with games every hour during active periods.

## Dev/Staging vs. Production Mode

| Parameter | Dev/Staging | Production |
|-----------|-------------|------------|
| `PINWHEEL_GAME_CRON` | `*/2 * * * *` (every 2 min) | `0 * * * *` (hourly) |
| `PINWHEEL_GOV_WINDOW` | 120 (2 min) | 1800 (30 min) |
| Teams | 8 | 8 |
| Round-robins per season | 1 (7 rounds) | 3 (21 rounds) |
| Games per round | 4 (every team plays once) | 4 (every team plays once) |
| `governance_rounds_interval` | 1 (every round) | 1 (every round, governable) |
| Playoffs | Best-of-1 semis, best-of-3 finals | Best-of-5 semis, best-of-7 finals |
| Presentation pace | Fast (1-2 min per game) | Full (20-30 min per game) |
| Mirror latency target | <5s | <15s (can batch) |
| Token regen | Every window | Twice daily |
| Full season duration | ~30 minutes | ~25-30 hours |

Dev/staging mode runs a complete season (regular season → tiebreakers → playoffs → championship → offseason governance) in roughly 30 minutes. Good for testing the full arc, validating governance interactions, and producing video content for the hackathon.

## Error Handling

- **Simulation failure:** If a game throws an exception, log it, skip that matchup, continue the block. Never let one bad game stop the league.
- **AI call failure:** If mirror generation fails, log it and serve stale mirrors with a "mirror unavailable" indicator. The game continues without mirrors.
- **SSE disconnect:** Clients that disconnect and reconnect get a catch-up payload of events since their last received event ID.
- **Rule enactment failure:** If a passed proposal produces an invalid ruleset (should be caught by validation, but belt-and-suspenders), reject the enactment, log the error, notify players, and keep the previous ruleset.

## Open Questions

1. **Governance window timing:** Should windows be cron-scheduled too, or opened manually by an admin during the hackathon? Cron is more autonomous; manual gives more control during demos.
2. **Concurrent simulation blocks:** If a game clock fires while the previous block is still simulating, should we queue, skip, or run concurrently? Queue is safest.
3. **Mirror priority:** If mirror generation is slow, should simulation mirrors take priority over private mirrors? Shared content serves more players.
