---
title: "design: Page-Level UX & Implementation Notes"
type: design
date: 2026-02-11
---

# Page-Level UX & Implementation Notes

Every page described below. Wireframes in ASCII. Implementation notes for HTMX/SSE behavior. Each page has three states: loading, live (SSE-connected), and static (historical data, no SSE).

---

## Data Contracts

Each page's data dependencies mapped to endpoints, models, and SSE events. Full schemas in `docs/INTERFACE_CONTRACTS.md`.

### Summary

| Page | Endpoints | Models | SSE Events |
|------|-----------|--------|------------|
| Game Preview | 5 | 5 | 0 |
| Live Game | 1 initial + SSE stream | 6 | 7 (`game.*`, `game.commentary`) |
| Game Summary | 3 | 5 | 0 |
| Team Page | 4 | 5 | 1 optional (`standings.update`) |
| Agent Page | 3 | 4 | 0 |
| Season Page (during) | 5 | 6 | 2 (`standings.update`, `governance.*`) |
| Season Page (archive) | 5 | 6 | 0 |

### Game Preview

| Component | Endpoint | Models |
|-----------|----------|--------|
| Teams & standings | `GET /api/teams/{team_id}` (x2) | `Team`, `TeamStanding` |
| Matchup preview (agents) | `GET /api/agents/{agent_id}/stats` (x8) | `Agent`, `AgentSeasonStats` |
| Head-to-head history | `GET /api/matchups/{team_a}/{team_b}` | `MatchupHistory` |
| Rules in effect | `GET /api/rules/current` | `RuleSet` |
| Mirror quote | `GET /api/mirrors/latest` | `Mirror` |

### Live Game

| Component | Endpoint / SSE | Models |
|-----------|---------------|--------|
| Initial state (late join) | `GET /api/games/{id}/state` | `GameState` |
| Scoreboard updates | SSE: `game.possession`, `game.quarter_end`, `game.elam_start` | `PossessionEvent`, `QuarterEndEvent`, `ElamStartEvent` |
| Play-by-play | SSE: `game.possession` | `PossessionEvent` |
| Box score | SSE: `game.boxscore` | `BoxScoreEvent` |
| Commentary | SSE: `game.commentary` | `CommentaryEvent` |
| Highlights / moves | SSE: `game.highlight`, `game.move` | `HighlightEvent`, `MoveEvent` |
| Final result | SSE: `game.result` | `GameResultEvent` |

SSE connection: `GET /api/events/stream?game_id={id}&games=true&commentary=true`

### Game Summary

| Component | Endpoint | Models |
|-----------|----------|--------|
| Full result + game story | `GET /api/games/{game_id}` | `GameResult` |
| Box score | `GET /api/games/{game_id}/boxscore` | `list[AgentBoxScore]` |
| Play-by-play archive | `GET /api/games/{game_id}/play-by-play` (lazy per quarter) | `list[PossessionLog]` |
| Rules in effect | embedded in `GameResult.governance_context` | `RuleSet` |
| Commentary | `GET /api/games/{game_id}/commentary` | `list[CommentaryLine]` |

### Team Page

| Component | Endpoint | Models |
|-----------|----------|--------|
| Team identity + roster | `GET /api/teams/{team_id}` | `Team`, `Agent` |
| Schedule & results | `GET /api/teams/{team_id}/schedule` | `list[ScheduleEntry]` |
| Team stats | `GET /api/teams/{team_id}/stats` | `TeamStats` |
| Governance footprint | `GET /api/governance/proposals?team_id={id}` | `list[Proposal]` |
| Standings (optional SSE) | SSE: `standings.update` | `StandingsEvent` |

### Agent Page

| Component | Endpoint | Models |
|-----------|----------|--------|
| Profile + attributes + moves | `GET /api/agents/{agent_id}` | `Agent`, `PlayerAttributes`, `Move` |
| Season stats + shooting zones | `GET /api/agents/{agent_id}/stats` | `AgentSeasonStats` |
| Game log | `GET /api/agents/{agent_id}/gamelog` | `list[AgentGameLine]` |

### Season Page (during)

| Component | Endpoint / SSE | Models |
|-----------|---------------|--------|
| Standings | `GET /api/standings` | `list[TeamStanding]` |
| Rule evolution | `GET /api/rules/history` | `list[RuleChange]` |
| Current ruleset vs defaults | `GET /api/rules/current` | `RuleSet` |
| Stat leaders | `GET /api/stats/leaders` | `StatLeaders` |
| Season narrative | `GET /api/mirrors/latest` | `Mirror` |
| Standings update | SSE: `standings.update` | `StandingsEvent` |
| Governance updates | SSE: `governance.open`, `governance.close` | `WindowOpenEvent`, `WindowCloseEvent` |

### Season Page (archive)

| Component | Endpoint | Models |
|-----------|----------|--------|
| Final standings | `GET /api/standings` | `list[TeamStanding]` |
| Playoff bracket | `GET /api/playoffs/bracket` | `PlayoffBracket` |
| Rule evolution (final) | `GET /api/rules/history` | `list[RuleChange]` |
| Season stat leaders | `GET /api/stats/leaders` | `StatLeaders` |
| Season mirror + awards | `GET /api/mirrors/season/{season_id}` | `Mirror` |

---

## 1. Game Preview

**URL:** `/games/{game_id}/preview`
**When:** Before a scheduled game has been simulated. Visible from the schedule, team page, or the Arena lobby between rounds.
**Data source:** Schedule table + team/agent data. No GameResult yet.

### What It Communicates

"Here's what's coming next." Builds anticipation. Shows the matchup, the stakes, and the governance context that will shape the game.

### Wireframe

```
┌────────────────────────────────────────────────────────────────┐
│                      ROUND 15 — GAME 3                         │
│                    Starts in 12m 34s                            │
├────────────────────────────────┬───────────────────────────────┤
│                                │                               │
│  ROSE CITY THORNS              │           BURNSIDE BREAKERS   │
│  ⬤ #2 in standings (15-6)     │    #5 in standings (11-10) ⬤  │
│                                │                               │
│  HOME                          │                         AWAY  │
│  The Thorn Garden              │           Breaker Bay Arena   │
│  18,000 seats · 50ft alt       │     6,200 seats · 25ft alt   │
│                                │                               │
├────────────────────────────────┴───────────────────────────────┤
│                                                                │
│  ┌─ MATCHUP PREVIEW ────────────────────────────────────────┐  │
│  │                                                          │  │
│  │  STARTERS                                                │  │
│  │  ┌──────────────────────┐   ┌──────────────────────┐     │  │
│  │  │ Nakamura             │   │ Moon                 │     │  │
│  │  │ Sharpshooter         │   │ Wildcard             │     │  │
│  │  │ SCR 82 · DEF 27      │   │ CHA 85 · EGO 82     │     │  │
│  │  │ 21.3 PPG · .445 3PT  │   │ 16.1 PPG · 2.1 SPG  │     │  │
│  │  └──────────────────────┘   └──────────────────────┘     │  │
│  │  ┌──────────────────────┐   ┌──────────────────────┐     │  │
│  │  │ Baptiste             │   │ Rivera               │     │  │
│  │  │ Lockdown             │   │ Slasher              │     │  │
│  │  │ DEF 83 · STA 47      │   │ SPD 84 · SCR 52     │     │  │
│  │  │ 8.2 PPG · 6.1 RPG    │   │ 14.7 PPG · 3.2 APG  │     │  │
│  │  └──────────────────────┘   └──────────────────────┘     │  │
│  │  ┌──────────────────────┐   ┌──────────────────────┐     │  │
│  │  │ Okafor               │   │ Vasquez              │     │  │
│  │  │ Floor General         │   │ Savant               │     │  │
│  │  │ PAS 78 · IQ 57       │   │ IQ 82 · PAS 53      │     │  │
│  │  │ 12.4 PPG · 5.8 APG   │   │ 10.0 PPG · 4.1 APG  │     │  │
│  │  └──────────────────────┘   └──────────────────────┘     │  │
│  │                                                          │  │
│  │  BENCH                                                   │  │
│  │  Chen (Iron Horse)          Kato (The Closer)            │  │
│  │                                                          │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                │
│  ┌─ HEAD TO HEAD ─────────────────────────────────────────┐    │
│  │  Season series: Thorns lead 2-1                        │    │
│  │  Rd 1: Thorns 54, Breakers 48 (@ Thorns)              │    │
│  │  Rd 8: Breakers 61, Thorns 55 (@ Breakers)            │    │
│  │  Rd 14: Thorns 58, Breakers 52 (@ Thorns)             │    │
│  └────────────────────────────────────────────────────────┘    │
│                                                                │
│  ┌─ RULES IN EFFECT ─────────────────────────────────────┐    │
│  │  three_point_value: 4 (was 3) — Proposal #12, Rd 8    │    │
│  │  three_point_distance: 25.0ft (was 22.15) — Prop #19  │    │
│  │  elam_margin: 10 (was 13) — Proposal #23, Rd 12       │    │
│  │                                                        │    │
│  │  "The three-point line moved back and got more         │    │
│  │   valuable. Nakamura's governors wrote both rules."    │    │
│  │   — Simulation Mirror, Rd 14                           │    │
│  └────────────────────────────────────────────────────────┘    │
│                                                                │
│  ┌─ VENUE MODIFIERS ─────────────────────────────────────┐    │
│  │  Crowd boost: +4.5% shooting (home, 18K capacity)     │    │
│  │  Crowd pressure: Ego check ±3% (home boost/away pen)  │    │
│  │  Altitude: negligible (25ft differential)              │    │
│  │  Travel fatigue: -0.8% stamina (112 miles)             │    │
│  └────────────────────────────────────────────────────────┘    │
└────────────────────────────────────────────────────────────────┘
```

### Implementation Notes

- **Countdown timer:** Pure CSS animation or minimal JS (`setInterval`). Counts down to the next game clock cron fire.
- **Stat lines:** Pulled from agent season stats via `GET /api/agents/{id}/stats`.
- **Head-to-head:** `GET /api/matchups/{team_a}/{team_b}`.
- **Rules in effect:** `GET /api/rules/current` filtered to non-default values.
- **Mirror quote:** Latest simulation mirror excerpt referencing these teams. Adds narrative texture.
- **Venue modifiers:** Computed server-side from the two teams' venues + current Tier 2 params. Displayed as readable sentences, not raw numbers.
- **Transition:** When the game starts simulating, this page morphs into the Live Game page via HTMX swap. The countdown hits zero, the page gets an SSE event, and the content swaps to the live view. No full-page reload.

---

## 2. Live Game

**URL:** `/games/{game_id}` (same URL as game summary — content changes based on game state)
**When:** Game is currently being presented (simulation complete, presenter is streaming possessions).
**Data source:** SSE stream from the presenter. Full GameResult exists in DB; presenter is pacing it out.

### What It Communicates

"You are watching this game happen." Every element updates in real time. The play-by-play scrolls. The box score ticks up. The commentary narrates. This is the stadium experience.

### Wireframe

```
┌────────────────────────────────────────────────────────────────┐
│  ROSE CITY THORNS  vs  BURNSIDE BREAKERS         ← Arena ←    │
│  The Thorn Garden · 18,000 seats · Round 15                    │
│                                                                │
│  ╔════════════════════════════════════════════════════════════╗ │
│  ║       THORNS  48        ——        BREAKERS  42            ║ │
│  ║              Q3 — Possession 14/15                        ║ │
│  ║                Game Clock: 17:36                           ║ │
│  ╚════════════════════════════════════════════════════════════╝ │
│                                                                │
│  ┌─ GAME TIMELINE ──────────────────────────────────────────┐  │
│  │ Q1        Q2        HALF    Q3                    ELAM   │  │
│  │ ●─────●─────●────────●─────●─────●─────●─────●─── ·····  │  │
│  │ 12-14  27-25               33-30  42-37 48-42     ?      │  │
│  │     ↑         ↑                      ↑                   │  │
│  │  lead chg   Baptiste       ★ Nakamura Heat Check         │  │
│  │             5 reb Q2         3PT from logo               │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                │
│  ┌─ PLAY-BY-PLAY (live) ──────┐  ┌─ BOX SCORE (live) ──────┐  │
│  │                             │  │                          │  │
│  │ Q3-14  Thorns ball.         │  │ THORNS          PTS  AST │  │
│  │   Okafor to Nakamura.       │  │ Nakamura ●●●●   22   4  │  │
│  │   Nakamura drives —         │  │ Baptiste         9    2  │  │
│  │   FOUL on Rivera!           │  │ Okafor           13   6  │  │
│  │   Free throws coming.       │  │ Chen (bench)     4    1  │  │
│  │                             │  │                          │  │
│  │ 🎙️ "Rivera can't stay out  │  │ BREAKERS         PTS  AST│  │
│  │    of foul trouble. That's  │  │ Moon ●●          14   3  │  │
│  │    his 4th. One more and    │  │ Rivera           12   1  │  │
│  │    the Breakers lose their  │  │ Vasquez          10   5  │  │
│  │    best athlete."           │  │ Kato (bench)     6    0  │  │
│  │                             │  │                          │  │
│  │ Q3-13  Breakers ball.       │  │ ● = foul (5 = ejection) │  │
│  │   Moon isolates...          │  │                          │  │
│  │   Contested three — MISS.   │  ├──────────────────────────┤  │
│  │                             │  │ QUARTER SCORES           │  │
│  │ 🎙️ "Moon's been cold all   │  │ Q1: THO 12  BRK 14      │  │
│  │    second half. The chaos   │  │ Q2: THO 15  BRK 11      │  │
│  │    giveth and the chaos     │  │ Q3: THO 21  BRK 17 ●    │  │
│  │    taketh away."            │  │                          │  │
│  │                             │  │ ● = in progress          │  │
│  │  ▾ scroll for earlier plays │  │                          │  │
│  └─────────────────────────────┘  └──────────────────────────┘  │
│                                                                │
│  ┌─ DEFENSIVE SCHEMES ─────┐  ┌─ RULES IN EFFECT ──────────┐  │
│  │ Thorns: MAN-TIGHT       │  │ 3PT value: 4 (Prop #12)    │  │
│  │   Nakamura ← Moon       │  │ 3PT distance: 25ft (#19)   │  │
│  │   Baptiste ← Rivera     │  │ Elam margin: 10 (#23)      │  │
│  │   Okafor ← Vasquez      │  │                            │  │
│  │                          │  │                            │  │
│  │ Breakers: ZONE           │  │                            │  │
│  │   (saving stamina)       │  │                            │  │
│  └──────────────────────────┘  └────────────────────────────┘  │
└────────────────────────────────────────────────────────────────┘
```

### Elam Mode Transformation

When the Elam Ending activates, the scoreboard transforms:

```
╔══════════════════════════════════════════════════════════════╗
║  ★ ★ ★  ELAM ENDING  ★ ★ ★                                 ║
║  TARGET SCORE: 55                                            ║
║                                                              ║
║  THORNS  48  ████████████████████░░░░░░░  7 to go            ║
║  BREAKERS 42 ████████████████░░░░░░░░░░░  13 to go           ║
║                                                              ║
║  Next basket could change everything.                        ║
╚══════════════════════════════════════════════════════════════╝
```

Progress bars fill toward the target. The border pulses. Commentary intensity ramps up.

### Implementation Notes

- **SSE connection:** `hx-ext="sse" sse-connect="/api/events/stream?game_id={id}&games=true&commentary=true"`. Each SSE event swaps into the correct target element.
- **Play-by-play scroll:** New possessions prepend to the top. Old possessions scroll down. Auto-scroll stays at top (latest play) unless the user has manually scrolled up to read history — then pin to their position.
- **Box score updates:** Each `game.possession` SSE event includes updated stats. The box score component re-renders.
- **Game timeline:** SVG or CSS-drawn horizontal line. Lead changes and highlights are dots. Updates as possessions arrive. Click a dot to jump to that possession in the play-by-play.
- **Defensive scheme panel:** Updates each possession. Shows current scheme and matchup assignments. Visually connects who's guarding whom.
- **Foul dots:** Each agent shows filled dots for fouls (like a loading bar toward ejection). 4 of 5 filled = foul trouble, visually urgent.
- **Commentary energy:** The `energy` field from `CommentaryEvent` drives CSS class — `energy-low` (normal text), `energy-medium` (slightly larger), `energy-high` (bold, accent color), `energy-peak` (large, animated, full-width callout).
- **Elam transition:** When `game.elam_start` SSE event arrives, the scoreboard component swaps to the Elam variant via HTMX. Progress bars animate via CSS transitions.
- **Late join:** On page load, fetch current state via `GET /api/games/{id}/state`, render the current snapshot, then connect SSE for live updates.

---

## 3. Game Summary

**URL:** `/games/{game_id}` (same URL as live game — content changes when game is complete)
**When:** Game is finished. The permanent record.
**Data source:** Stored GameResult + cached commentary. Static page with no SSE.

### What It Communicates

"Here's what happened." The full story of the game, told through stats, the play-by-play archive, and AI commentary. Every game summary connects back to governance — what rules shaped this outcome?

### Wireframe

```
┌────────────────────────────────────────────────────────────────┐
│  FINAL · Round 15                                              │
│  The Thorn Garden · 18,000 seats                               │
│                                                                │
│  ╔════════════════════════════════════════════════════════════╗ │
│  ║       THORNS  55        ——        BREAKERS  52            ║ │
│  ║                                                           ║ │
│  ║  Q1: 12-14    Q2: 15-11    Q3: 21-17    Elam: 7-10       ║ │
│  ║  Elam target: 55 · Elam possessions: 12                  ║ │
│  ║  Game-winner: Nakamura 3PT (contested) — Heat Check       ║ │
│  ╚════════════════════════════════════════════════════════════╝ │
│                                                                │
│  ┌─ GAME STORY ─────────────────────────────────────────────┐  │
│  │                                                          │  │
│  │  "The Breakers led wire-to-wire through the first        │  │
│  │   quarter behind Moon's 10 early points. Then the        │  │
│  │   Thorns switched to man-tight on Moon and she went      │  │
│  │   cold — 1-for-7 the rest of the way. The Elam Ending   │  │
│  │   was the Nakamura show: she scored 9 of the Thorns'    │  │
│  │   final 12, capped by a contested Heat Check three       │  │
│  │   that hit target score with Moon draped all over her.   │  │
│  │   The Thorn Garden faithful are still recovering."       │  │
│  │                                   — AI Game Recap        │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                │
│  ┌─ GAME TIMELINE ──────────────────────────────────────────┐  │
│  │ Q1        Q2        HALF    Q3           ELAM     FINAL  │  │
│  │ ●─────●─────●────────●─────●─────●══════●═══●═══●═══★   │  │
│  │ 12-14  27-25               33-30         48-42       55  │  │
│  │  ↑        ↑                  ↑               ↑    ★ WIN  │  │
│  │ Moon   Baptiste          Nakamura         Rivera         │  │
│  │ 10pts  5 reb Q2          Heat Check       4th foul       │  │
│  │                          3PT from logo                   │  │
│  │                                                          │  │
│  │ Click any moment to read the play-by-play from there ▸   │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                │
│  ┌─ FULL BOX SCORE ─────────────────────────────────────────┐  │
│  │                                                          │  │
│  │ ROSE CITY THORNS                                         │  │
│  │ Player        MIN  PTS  FG     3PT    FT    REB AST STL  │  │
│  │ Nakamura      32   28   10-18  5-9    3-4   2   4   1   │  │
│  │ Baptiste      30   9    4-9    0-0    1-2   8   2   0   │  │
│  │ Okafor        28   13   5-10   1-3    2-2   3   6   2   │  │
│  │ Chen          10   5    2-4    1-2    0-0   1   1   0   │  │
│  │ TOTAL              55   21-41  7-14   6-8   14  13  3   │  │
│  │                                                          │  │
│  │ BURNSIDE BREAKERS                                        │  │
│  │ Player        MIN  PTS  FG     3PT    FT    REB AST STL  │  │
│  │ Moon          34   14   5-16   2-8    2-2   3   3   1   │  │
│  │ Rivera        28   16   6-12   1-4    3-4   4   1   2   │  │
│  │ Vasquez       30   14   5-11   2-5    2-2   5   5   0   │  │
│  │ Kato          8    8    3-5    0-1    2-2   1   0   0   │  │
│  │ TOTAL              52   19-44  5-18   9-10  13  9   3   │  │
│  │                                                          │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                │
│  ┌─ PLAY-BY-PLAY ARCHIVE ──┐  ┌─ RULES IN EFFECT ──────────┐  │
│  │                          │  │                            │  │
│  │  ▸ Q1 (14 possessions)  │  │ 3PT value: 4 (Prop #12)   │  │
│  │  ▸ Q2 (15 possessions)  │  │ 3PT dist: 25ft (#19)      │  │
│  │  ▸ Q3 (15 possessions)  │  │ Elam margin: 10 (#23)     │  │
│  │  ▸ Elam (12 possessions)│  │                            │  │
│  │                          │  │ "Nakamura's 28 points are  │  │
│  │  Expand any quarter to   │  │  worth 6 more than they'd  │  │
│  │  read full play-by-play  │  │  be under default rules.   │  │
│  │  with commentary.        │  │  Governance fingerprints." │  │
│  │                          │  │                            │  │
│  │  ▸ Watch Replay          │  │                            │  │
│  └──────────────────────────┘  └────────────────────────────┘  │
│                                                                │
│  ┌─ VENUE ───────────────────────────────────────────────────┐ │
│  │ The Thorn Garden · Portland, OR · 18,000 seats · 50ft    │ │
│  │ Crowd boost: +4.5% · Travel fatigue: -0.8% (Breakers)    │ │
│  └───────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────┘
```

### Implementation Notes

- **Game story:** AI-generated recap. Produced by the commentary engine as a final summary after the last possession. 3-5 sentences covering the arc. Stored with the game. Not a mirror — it's the game's lede.
- **Play-by-play archive:** Collapsible by quarter. Click a quarter heading to expand. Each possession shows the structured play + commentary. HTMX `hx-get` fetches the quarter's plays on expand (lazy load — don't send 60+ possessions on page load).
- **Game timeline:** Same SVG component as the live game, but complete. All dots filled. The game-winning shot gets a star. Click any dot to jump to that play-by-play entry and auto-expand the quarter.
- **Replay button:** Links to `/games/{id}?replay=true`. Same page, but reconnects SSE to a replay presenter (fast pace, cached commentary). HTMX swaps the static content for the live view.
- **Box score:** Full splits. Sortable by any column (HTMX `hx-get` with sort param). Leader in each column is bold.
- **Governance fingerprints:** The "rules in effect" panel includes a short AI-generated line connecting governance to the outcome. Stored as metadata with the game.

---

## 4. Team Page

**URL:** `/teams/{team_id}`
**Data source:** Team + agents + game results + governance events. Mix of static and SSE (standings update, live game indicator).

### What It Communicates

"This is who we are." Team identity, roster, performance, venue, and governance footprint. For governors: this is your team. For opponents: this is who you're up against.

### Wireframe

```
┌────────────────────────────────────────────────────────────────┐
│                                                                │
│  ⬤ ROSE CITY THORNS                              #CC0000      │
│  "Bloom Where They Plant You"                                  │
│  #2 in standings · 15-6 · W3 streak                            │
│                                                                │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  ┌─ THE THORN GARDEN ───────────────────────────────────────┐  │
│  │  Portland, OR · 18,000 seats · Hardwood · 50ft altitude  │  │
│  │                                                          │  │
│  │  Home record: 9-2      Away record: 6-4                  │  │
│  │  Crowd boost: +4.5%    Home win rate: 82%                │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                │
│  ┌─ ROSTER ─────────────────────────────────────────────────┐  │
│  │                                                          │  │
│  │  ┌─ STARTERS ──────────────────────────────────────────┐ │  │
│  │  │                                                     │ │  │
│  │  │  Kaia "Deadeye" Nakamura · Sharpshooter · ★ starter │ │  │
│  │  │  ┌────────────┐                                     │ │  │
│  │  │  │  SCR ████░  │  21.3 PPG · 5.1 APG · .445 3PT%   │ │  │
│  │  │  │  PAS ██░░░  │  Moves: Heat Check, Court Vision   │ │  │
│  │  │  │  DEF █░░░░  │                                    │ │  │
│  │  │  │  SPD ██░░░  │  "Lives behind the arc. Lethal     │ │  │
│  │  │  │  STA ██░░░  │   when open, invisible on defense. │ │  │
│  │  │  │  IQ  ███░░  │   The governors moved the line     │ │  │
│  │  │  │  EGO ██░░░  │   back for her — and she's         │ │  │
│  │  │  │  CHA █░░░░  │   rewarding them."                 │ │  │
│  │  │  │  FAT ██░░░  │                                    │ │  │
│  │  │  └────────────┘  → View full profile                │ │  │
│  │  │                                                     │ │  │
│  │  │  DJ "The Wall" Baptiste · Lockdown · ★ starter      │ │  │
│  │  │  [attribute bars]  8.2 PPG · 6.1 RPG · 1.8 BPG     │ │  │
│  │  │  Moves: Lockdown Stance                              │ │  │
│  │  │  → View full profile                                │ │  │
│  │  │                                                     │ │  │
│  │  │  Senna Okafor · Floor General · ★ starter           │ │  │
│  │  │  [attribute bars]  12.4 PPG · 5.8 APG               │ │  │
│  │  │  Moves: No-Look Pass, Court Vision                   │ │  │
│  │  │  → View full profile                                │ │  │
│  │  │                                                     │ │  │
│  │  └─────────────────────────────────────────────────────┘ │  │
│  │                                                          │  │
│  │  ┌─ BENCH ─────────────────────────────────────────────┐ │  │
│  │  │  Riley "Jet" Park Chen · Iron Horse                 │ │  │
│  │  │  [attribute bars]  4.8 PPG · 2.1 RPG                │ │  │
│  │  │  → View full profile                                │ │  │
│  │  └─────────────────────────────────────────────────────┘ │  │
│  │                                                          │  │
│  │  Team attribute average:                                 │  │
│  │  SCR 52 · PAS 48 · DEF 45 · SPD 47 · STA 42            │  │
│  │  IQ 50 · EGO 38 · CHA 30 · FAT 38                      │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                │
│  ┌─ SCHEDULE & RESULTS ─────────────────────────────────────┐  │
│  │                                                          │  │
│  │  Rd 14  vs Breakers (H)    W  58-52   Nakamura 24pts    │  │
│  │  Rd 13  vs Iron Horses (A) L  49-55   Okafor 18pts      │  │
│  │  Rd 12  vs Ravens (H)      W  61-44   Baptiste 8reb     │  │
│  │  Rd 11  vs Foxes (A)       W  53-50   Nakamura 22pts    │  │
│  │  Rd 10  vs Drift (H)       W  59-41   Okafor 7ast       │  │
│  │  ...                                                     │  │
│  │                                                          │  │
│  │  Next: Rd 15 vs Breakers (A) — in 12m 34s               │  │
│  │                                                          │  │
│  │  ▸ View full schedule                                    │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                │
│  ┌─ GOVERNANCE FOOTPRINT ───────────────────────────────────┐  │
│  │                                                          │  │
│  │  Proposals submitted: 8 (4 passed, 2 failed, 2 active)  │  │
│  │  Amendments: 3                                           │  │
│  │  Token trades: 12 (6 intra-team, 6 cross-team)          │  │
│  │                                                          │  │
│  │  Recent:                                                 │  │
│  │  ✓ Prop #19: Move 3PT line to 25ft (Rd 10)              │  │
│  │  ✗ Prop #21: Ban press defense (Rd 11)                   │  │
│  │  ✓ Prop #23: Reduce Elam margin to 10 (Rd 12)           │  │
│  │                                                          │  │
│  │  "The Thorns' governors have shaped the rule space       │  │
│  │   more than any other team. Every change has favored     │  │
│  │   their sharpshooter."                                   │  │
│  │   — Governance Mirror, Rd 14                             │  │
│  └──────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────┘
```

### Implementation Notes

- **Attribute bars:** CSS `background: linear-gradient(...)` sized proportionally. 5 segments (each 20 of 100). Colored by team accent. Pure CSS, no JS, no canvas.
- **Roster cards:** Reusable `agent_card.html` component. Shows compact view on the team page; links to full agent page.
- **Schedule list:** Most recent first, last 5 shown by default. "View full schedule" expands via `hx-get`. Next game links to the game preview page.
- **Governance footprint:** Summarizes this team's governance activity. The mirror quote adds narrative texture. Only visible if governance has started (hidden in early rounds).
- **Live indicator:** If the team has a game currently presenting, a pulsing dot appears next to the team name with a link to the live game.

---

## 5. Player (Agent) Page

**URL:** `/agents/{agent_id}`
**Data source:** Agent profile + season stats + game log. Static page.

### What It Communicates

"This is who I am." The full identity of an agent — attributes, personality, performance, and the narrative that's emerged through play. Agents are characters, not stat sheets.

### Wireframe

```
┌────────────────────────────────────────────────────────────────┐
│                                                                │
│  KAIA "DEADEYE" NAKAMURA                                       │
│  Sharpshooter · Rose City Thorns                               │
│                                                                │
│  "Lives behind the arc. Lethal when open, invisible on         │
│   defense. She doesn't play basketball — she plays geometry.   │
│   The ball, the arc, the angle. Everything else is noise."     │
│                                                                │
├──────────────────────────────┬─────────────────────────────────┤
│                              │                                 │
│  ┌─ ATTRIBUTES ────────────┐ │  ┌─ SEASON STATS ────────────┐  │
│  │                         │ │  │                            │  │
│  │        SCR (82)         │ │  │  GP   21                   │  │
│  │         ████████░░      │ │  │  PPG  21.3                 │  │
│  │                         │ │  │  APG  5.1                  │  │
│  │  FAT(35)           PAS  │ │  │  RPG  2.1                  │  │
│  │  ███░░         (42)     │ │  │  SPG  1.2                  │  │
│  │               ████░     │ │  │  FG%  .478                 │  │
│  │                         │ │  │  3PT% .445                 │  │
│  │  CHA(27)          DEF   │ │  │  FT%  .856                 │  │
│  │  ██░░░        (27)      │ │  │  +/-  +4.2                 │  │
│  │               ██░░░     │ │  │                            │  │
│  │                         │ │  │  SHOOTING ZONES            │  │
│  │  EGO(32)          SPD   │ │  │  At rim:    .621 (18/29)   │  │
│  │  ███░░        (37)      │ │  │  Mid-range: .412 (21/51)   │  │
│  │               ███░░     │ │  │  Three-pt:  .445 (57/128)  │  │
│  │                         │ │  │                            │  │
│  │  IQ (57)     STA(37)    │ │  │  CLUTCH (Elam period)      │  │
│  │  █████░      ███░░      │ │  │  PPG: 6.8  FG%: .524      │  │
│  │                         │ │  │  Game-winners: 3            │  │
│  └─────────────────────────┘ │  └────────────────────────────┘  │
│                              │                                 │
├──────────────────────────────┴─────────────────────────────────┤
│                                                                │
│  ┌─ MOVES ──────────────────────────────────────────────────┐  │
│  │                                                          │  │
│  │  ★ HEAT CHECK                          Source: Archetype │  │
│  │  Trigger: Made a 3-pointer last possession               │  │
│  │  Effect: +15% on next 3-point attempt, IQ modifier       │  │
│  │          ignored                                         │  │
│  │  Gate: Ego 60+ (Nakamura: 32 — gate met via move grant)  │  │
│  │  Activations this season: 34 (18 made, 16 missed)        │  │
│  │  "When she's hot, she's a flamethrower. When she's       │  │
│  │   not... she still shoots." — Commentary, Rd 9           │  │
│  │                                                          │  │
│  │  ★ COURT VISION                        Source: Archetype │  │
│  │  Trigger: Half court setup                               │  │
│  │  Effect: Ball handler sees optimal pass; assist window   │  │
│  │          doubled                                         │  │
│  │  Gate: IQ 75+, Passing 60+                               │  │
│  │  Activations this season: 41                             │  │
│  │                                                          │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                │
│  ┌─ GAME LOG ───────────────────────────────────────────────┐  │
│  │                                                          │  │
│  │  Rd  Opponent       PTS  FG     3PT    AST  REB  +/-     │  │
│  │  14  vs Breakers    24   8-18   4-9    4    2    +6  ▸   │  │
│  │  13  @ Iron Horses  16   6-15   3-8    3    1    -6  ▸   │  │
│  │  12  vs Ravens      22   8-14   5-8    5    3    +17 ▸   │  │
│  │  11  @ Foxes        20   7-16   4-10   2    2    +3  ▸   │  │
│  │  10  vs Drift       26   10-17  6-10   6    1    +18 ▸   │  │
│  │  ...                                                     │  │
│  │                                                          │  │
│  │  ▸ = link to game summary                                │  │
│  │  Season highs: 28 PTS (Rd 3) · 8 AST (Rd 6)             │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                │
│  ┌─ NOTABLE MOMENTS ────────────────────────────────────────┐  │
│  │                                                          │  │
│  │  Rd 14  Game-winning Heat Check 3PT vs Breakers          │  │
│  │         "From the logo... IT'S GOOD!"                    │  │
│  │                                                          │  │
│  │  Rd 9   5-for-5 from three in Q2 vs Wolves               │  │
│  │         "She's not even looking at the basket anymore.   │  │
│  │          She's looking at the governors who moved the    │  │
│  │          line back."                                     │  │
│  │                                                          │  │
│  │  Rd 3   Season-high 28 points (8-12 3PT) vs Monarchs    │  │
│  │                                                          │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                │
│  ┌─ RIVALRIES ──────────────────────────────────────────────┐  │
│  │                                                          │  │
│  │  vs Indigo Moon (Breakers) — "The Chaos vs. The Angle"   │  │
│  │  Head-to-head: Nakamura 22.0 PPG, Moon 15.3 PPG         │  │
│  │  Moon's Chaotic Alignment makes her the one defender     │  │
│  │  Nakamura can't predict. 3 of their 4 games have gone   │  │
│  │  to the final Elam possession.                           │  │
│  │                                                          │  │
│  └──────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────┘
```

### Implementation Notes

- **Attributes display:** Two options — (a) radar/spider chart via inline SVG (9 axes, one per attribute), or (b) horizontal bar chart as shown. Bar chart is simpler and more readable at a glance. Radar chart is more visually striking. **Recommendation: bar chart for compact views (team page), radar chart for the full agent page.** Radar chart rendered as an inline SVG in the Jinja2 template — 9 points on a polygon, no JS library needed.
- **Shooting zones:** Simple table for Day 1. Post-hackathon: half-court SVG with hot/cold zones.
- **Clutch stats:** Filtered to Elam period only. Shows the agent's performance when it matters most.
- **Move activations:** Tracked in the game result. Displayed as a season total with made/missed for scoring moves.
- **Notable moments:** AI-curated from commentary cache. The commentary engine flags `energy: "peak"` moments. The agent page pulls their peak moments. These are the character's highlight reel.
- **Rivalries:** Defined at league generation time (AI-generated backstories include rivalry targets). Enriched with head-to-head stats computed from game results.
- **Game log:** Sortable by any stat column. Each row links to the game summary. Season highs highlighted.

---

## 6. Season Page

**URL:** `/seasons/{season_id}` (or `/season` for current)
**When:** During the season (live dashboard) and after (permanent archive).
**Data source:** Standings, schedule, governance history, mirrors, stats. Mix of SSE (during season) and static (after).

### What It Communicates

During a season: "Here's where we are — the current state of the league, the rules, and the story so far." After a season: "Here's what happened — the complete narrative arc."

### Wireframe (During Season)

```
┌────────────────────────────────────────────────────────────────┐
│                                                                │
│  PINWHEEL · SEASON 1                                           │
│  Round 15 of 21 · Regular Season                               │
│  Next games: 12m 34s · Next governance window: 47m 12s         │
│                                                                │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  ┌─ STANDINGS ──────────────────────────────────────────────┐  │
│  │  #   Team              W-L    %     L5    Strk   GB     │  │
│  │  1.  Iron Horses      16-5   .762  4-1   W2     —      │  │
│  │  2.  Thorns           15-6   .714  4-1   W3     1      │  │
│  │  3.  Wolves           13-8   .619  3-2   L1     3      │  │
│  │  4.  Monarchs         12-9   .571  2-3   L2     4      │  │
│  │  ─── playoff cutoff ──────────────────────────────────  │  │
│  │  5.  Breakers         11-10  .524  3-2   W1     5      │  │
│  │  6.  Foxes            9-12   .429  2-3   L1     7      │  │
│  │  7.  Ravens           7-14   .333  1-4   L3     9      │  │
│  │  8.  Drift            4-17   .190  0-5   L5     12     │  │
│  │                                                          │  │
│  │  Clinched playoff: Iron Horses                           │  │
│  │  Eliminated: Drift                                       │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                │
│  ┌─ RULE EVOLUTION ─────────────────────────────────────────┐  │
│  │                                                          │  │
│  │  TIMELINE                                                │  │
│  │  Rd 1 ─── Rd 4 ─── Rd 8 ─── Rd 10 ── Rd 12 ── Rd 14    │  │
│  │           ▲        ▲        ▲         ▲                  │  │
│  │           │        │        │         │                  │  │
│  │           │        3PT→4    3PT line   Elam              │  │
│  │           Foul     (Prop    →25ft     →10                │  │
│  │           limit    #12)     (Prop     (Prop              │  │
│  │           6→5               #19)      #23)               │  │
│  │                                                          │  │
│  │  CURRENT RULESET vs DEFAULTS                             │  │
│  │  three_point_value:    4  (default: 3)   ▲ +1            │  │
│  │  three_point_distance: 25 (default: 22)  ▲ +2.85ft       │  │
│  │  personal_foul_limit:  5  (default: 5)   ● unchanged     │  │
│  │  elam_margin:          10 (default: 13)  ▼ -3            │  │
│  │                                                          │  │
│  │  4 of 60 parameters changed · 23 proposals submitted     │  │
│  │  13 passed · 7 failed · 3 active                         │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                │
│  ┌─ SEASON NARRATIVE ───────────────────────────────────────┐  │
│  │                                                          │  │
│  │  ★ STATE OF THE LEAGUE — Round 14                        │  │
│  │                                                          │  │
│  │  "Fifteen rounds in, two stories dominate: the Thorns'   │  │
│  │   methodical reshaping of the three-point game and the   │  │
│  │   Iron Horses' quiet dominance through stamina.          │  │
│  │                                                          │  │
│  │   The Thorns' governors have passed 3 of the 4 rule     │  │
│  │   changes this season — all benefiting Nakamura. The     │  │
│  │   other teams are starting to notice. The Breakers and   │  │
│  │   Wolves voted together on the last 4 proposals. A       │  │
│  │   counter-coalition is forming.                          │  │
│  │                                                          │  │
│  │   Meanwhile, nobody's talking about the Drift. They're   │  │
│  │   4-17 and their governors have stopped proposing.       │  │
│  │   Their last token trade was Round 8. The private        │  │
│  │   mirrors are asking questions nobody wants to answer."  │  │
│  │                                                          │  │
│  │  ▸ Read all mirrors                                      │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                │
│  ┌─ STAT LEADERS ───────────────────────────────────────────┐  │
│  │                                                          │  │
│  │  Scoring             Assists             Steals          │  │
│  │  1. Nakamura  21.3   1. Okafor  5.8      1. Rivera 2.0  │  │
│  │  2. Rivera    17.1   2. Vasquez 4.1      2. Okafor 1.8  │  │
│  │  3. Moon      16.1   3. Moon    3.4      3. Moon   1.7  │  │
│  │                                                          │  │
│  │  Rebounds            +/-                 3PT%            │  │
│  │  1. Baptiste  6.1    1. Nakamura +4.2    1. Nakamura .445│  │
│  │  2. Kruger    5.8    2. Stone    +3.8    2. Sokolov  .412│  │
│  │  3. Stone     5.5    3. Okafor   +3.1    3. Moon     .389│  │
│  │                                                          │  │
│  │  ▸ Full stat leaders                                     │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                │
│  ┌─ UPCOMING ───────────────────────────────────────────────┐  │
│  │                                                          │  │
│  │  ROUND 15                                                │  │
│  │  Thorns @ Breakers    Iron Horses vs Foxes               │  │
│  │  Wolves vs Monarchs   Ravens @ Drift                     │  │
│  │                                                          │  │
│  │  ▸ Full schedule                                         │  │
│  └──────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────┘
```

### Wireframe (After Season — Archive)

```
┌────────────────────────────────────────────────────────────────┐
│                                                                │
│  PINWHEEL · SEASON 1 — COMPLETE                                │
│  Champion: Rose City Thorns                                    │
│                                                                │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  ┌─ THE SEASON ─────────────────────────────────────────────┐  │
│  │                                                          │  │
│  │  "Season 1 began as a democracy and ended as a          │  │
│  │   three-point oligarchy. The Thorns' governors           │  │
│  │   reshaped the league in Nakamura's image — moving the   │  │
│  │   three-point line, increasing its value, and tightening │  │
│  │   the Elam margin so their sharpshooter could close      │  │
│  │   games faster. The counter-coalition of Breakers,       │  │
│  │   Wolves, and Monarchs formed too late. By Round 16,     │  │
│  │   the rules were the Thorns' constitution.               │  │
│  │                                                          │  │
│  │   The Finals told a different story. The Iron Horses —   │  │
│  │   quiet all season, stamina-rich, zone-defense oriented  │  │
│  │   — took the Thorns to 7 games. Nakamura shot 31% in    │  │
│  │   Games 5 and 6. The Thorns' governors scrambled,        │  │
│  │   passing a rule change between Games 6 and 7 that       │  │
│  │   increased crowd pressure (favoring home teams in the   │  │
│  │   Elam). Game 7 was at The Thorn Garden. Nakamura       │  │
│  │   scored 34. The crowd built a fortress around her.      │  │
│  │                                                          │  │
│  │   Was it good governance or home cooking? The mirror     │  │
│  │   says both. The governors played the game as written.   │  │
│  │   Whether the game should have been written that way     │  │
│  │   is a question for Season 2."                           │  │
│  │                                        — Season Mirror   │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                │
│  ┌─ FINAL STANDINGS ──┐  ┌─ PLAYOFF BRACKET ────────────────┐  │
│  │  1. Iron Horses 18-3│  │                                 │  │
│  │  2. Thorns     16-5 │  │  Semi: (1)IH vs (4)Mon ─ IH 3-1│  │
│  │  3. Wolves     14-7 │  │  Semi: (2)THO vs (3)WOL─THO 3-2│  │
│  │  4. Monarchs   13-8 │  │                                 │  │
│  │  ─── cutoff ─────── │  │  Finals: IH vs THO             │  │
│  │  5. Breakers   11-10│  │    G1: IH 55-48 (@ IH)         │  │
│  │  6. Foxes      10-11│  │    G2: IH 52-50 (@ IH)         │  │
│  │  7. Ravens      6-15│  │    G3: THO 61-44 (@ THO)       │  │
│  │  8. Drift       3-18│  │    G4: THO 58-55 (@ THO)       │  │
│  └─────────────────────┘  │    G5: IH 49-47 (@ IH)         │  │
│                           │    G6: THO 53-51 (@ THO)       │  │
│                           │    G7: THO 62-55 (@ THO) ★     │  │
│                           │                                 │  │
│                           │  CHAMPION: ROSE CITY THORNS     │  │
│                           └─────────────────────────────────┘  │
│                                                                │
│  ┌─ AWARDS ─────────────────────────────────────────────────┐  │
│  │                                                          │  │
│  │  MVP:  Kaia Nakamura (Thorns) — 21.3 PPG, .445 3PT%     │  │
│  │  "Every rule change orbited her. She was the sun."       │  │
│  │                                                          │  │
│  │  Defensive Player: DJ Baptiste (Thorns) — 6.1 RPG       │  │
│  │  Most Improved: Jax Rivera (Foxes) — 17.1 PPG (was 12)  │  │
│  │  Most Chaotic: Indigo Moon (Breakers) — 85 CHA attr      │  │
│  │  Best Governor: [governor name] — 5 proposals passed     │  │
│  │  The Oracle Award: [dormant — no Fate events this season]│  │
│  │                                                          │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                │
│  ┌─ RULE EVOLUTION (FINAL) ─────────────────────────────────┐  │
│  │  [Same timeline as during-season, but complete]          │  │
│  │  23 proposals · 13 passed · 4 parameters changed         │  │
│  │  Most active governance period: Rd 10-14 (counter-       │  │
│  │  coalition formed, 8 proposals in 5 rounds)              │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                │
│  ┌─ SEASON STAT LEADERS ────────────────────────────────────┐  │
│  │  [Same as during-season, but final]                      │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                │
│  ┌─ ALL MIRRORS ────────────────────────────────────────────┐  │
│  │  ▸ Simulation Mirrors (21)                               │  │
│  │  ▸ Governance Mirrors (21)                               │  │
│  │  ▸ State of the League (3)                               │  │
│  │  ▸ Series Mirrors (3)                                    │  │
│  │  ▸ Season Mirror (1)                                     │  │
│  └──────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────┘
```

### Implementation Notes

- **During season vs. archive:** Same URL, different rendering. The template checks `season.status` — if `active` or `playoffs`, show live standings with SSE updates and countdowns. If `complete`, show the archive layout with the season mirror as the hero.
- **Standings SSE:** `hx-ext="sse" sse-connect="/api/events/stream?governance=true"` — standings update when governance windows close and games complete.
- **Rule evolution timeline:** SVG timeline rendered server-side. Each rule change is a dot on the timeline. Hover (or click on mobile) reveals the proposal detail. The timeline is the visual history of governance.
- **Playoff bracket:** Rendered as nested `<div>` elements styled with CSS grid. Lines connect matchups. Completed games show scores. Active series pulse. SVG lines for the bracket connectors.
- **Season narrative:** The season mirror is the hero content on the archive page. It's the definitive story. Generated by Opus 4.6 after the championship, with full context of every game, every rule change, every governance action.
- **Awards:** AI-generated with narrative context, not just stat leaders. "MVP" includes a one-line justification that connects the player's performance to the governance landscape.
- **Mirror archive:** Collapsible sections by mirror type. Each mirror shows round number, a 1-line excerpt, and expands to full text. Links to the games/governance actions referenced.

---

## Cross-Page Navigation

### Global Nav Bar

Present on every page. Provides context at a glance.

```
┌────────────────────────────────────────────────────────────────┐
│ 🏀 PINWHEEL                                                    │
│                                                                │
│ Arena · Standings · Teams · Governance · Rules · Mirrors       │
│                                                                │
│ ┌─ TICKER ────────────────────────────────────────────────┐    │
│ │ THO 48 WOL 42 (Q3) · BRK 38 MON 35 (Q3) · IH 51 FOX  │    │
│ │ 44 (ELAM ★) · RAV 33 DRI 31 (Q2) ···                   │    │
│ └─────────────────────────────────────────────────────────┘    │
│                                                                │
│ 🗳️ Governance window open · 2 proposals active  │  🔑 Log In  │
└────────────────────────────────────────────────────────────────┘
```

- **Score ticker:** Horizontal scrolling ticker showing live game scores. CSS animation. Clicks through to the game. Updates via SSE.
- **Governance indicator:** Shows when a governance window is open and how many proposals are active. Links to the governance page.
- **Login:** Discord OAuth. When logged in, shows governor name, team badge, and token balances.

### Page Transitions

All navigation uses HTMX partial swaps (`hx-target="#main" hx-push-url="true"`). The nav bar, ticker, and SSE connection persist across page changes. Only the main content area swaps. This means:
- No full page reload on navigation
- SSE connection stays alive
- Score ticker keeps updating
- Governance indicator stays current

---

## Component Reuse

| Component | Used On |
|---|---|
| `game_card.html` | Arena (compact), schedule lists, team page |
| `agent_card.html` | Team page (compact), roster lists |
| `box_score.html` | Live game, game summary |
| `standings_table.html` | Season page, standings page, Arena lobby |
| `proposal_card.html` | Governance page, team page governance footprint |
| `mirror_card.html` | Mirrors page, season page, team page |
| `rule_change.html` | Rules page, game summary rule context, season page timeline |
| `commentary.html` | Live game, game summary, Arena panels |
| `possession.html` | Live game play-by-play, game summary archive |
| `attribute_bars.html` | Team page agent cards, agent page |
| `venue_card.html` | Game preview, game summary, team page |
