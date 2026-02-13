# Pinwheel Fates: Viewer Experience & Live Commentary

## Overview

Pinwheel Fates is a game about governance — but if watching the games isn't fun, nobody will care about governing. The viewer experience is the gateway. The Arena is the stadium. AI commentary is the broadcast booth. Bot search is the stats desk. Every surface must be fast, beautiful, and alive.

**Core principle:** The simulation computes a full game in milliseconds, but fans experience it over 20-30 minutes. Because the presenter knows the full story before it starts telling it, the AI commentary can build tension, foreshadow drama, and narrate the absurd rule-modified reality the governors have created. This foreknowledge is a superpower — Opus 4.6 is an omniscient narrator pretending to watch live.

## The Three Viewer Surfaces

| Surface | What | Where | Update Model |
|---------|------|-------|-------------|
| **The Arena** | Live multi-game dashboard. All 4 games at once. The Red Zone of Pinwheel. | Web (HTMX + SSE) | Real-time SSE push |
| **Single Game** | Deep dive into one game. Full play-by-play, box score, commentary. | Web (HTMX + SSE) | Real-time SSE push |
| **Bot Search** | Natural language queries for stats, standings, box scores, history. | Discord (bot) | On-demand via Opus 4.6 |

All three surfaces read from the same API. The Arena and Single Game views receive real-time updates via SSE. Bot Search makes API calls and uses Opus 4.6 to format responses conversationally.

## The Arena

The Arena is the primary live viewing experience. During a round, all 4 games run simultaneously. The Arena shows all of them at once — a 2x2 grid of live game panels, each streaming play-by-play via SSE.

### Layout

```
┌─────────────────────────────────────────────────────┐
│                    PINWHEEL FATES                    │
│               ★ Round 14 — Live Now ★               │
├──────────────────────┬──────────────────────────────┤
│  THORNS  42          │  BREAKERS  38                │
│  WOLVES  39          │  MONARCHS  35                │
│  Q3 — Poss 11/15     │  Q3 — Poss 8/15             │
│                      │                              │
│  ▸ Nakamura pulls up │  ▸ Moon drives the lane...   │
│    from 25 feet...   │    contact! And-one!         │
│    BANG! Three-point  │                              │
│    dagger! 🔥        │                              │
│                      │                              │
├──────────────────────┼──────────────────────────────┤
│  IRON HORSES  51     │  RAVENS  33                  │
│  FOXES  44           │  DRIFT  31                   │
│  ★ ELAM — Target: 58 │  Q2 — Poss 6/15             │
│                      │                              │
│  ▸ Elam Ending is    │  ▸ Quiet game in the Drift's │
│    ON. Iron Horses   │    tiny gym. Altitude might   │
│    need 7 more.      │    be getting to the Ravens.  │
│    Foxes need 14.    │                              │
│                      │                              │
├──────────────────────┴──────────────────────────────┤
│  📊 Standings  │  📋 Rules  │  🗳️ Governance  │  🪞 Reports │
└─────────────────────────────────────────────────────┘
```

### Arena Features

- **2x2 game grid.** Each panel shows: team names + scores, period/possession progress, last play with commentary, active Moves highlighted.
- **Elam countdown.** When a game enters the Elam Ending, the panel lights up — target score displayed, points-to-go for each team, tension meter.
- **Dramatic moment alerts.** The presenter flags key moments (lead changes, Move triggers, clutch shots, Elam approach). These get visual treatment — color flash, expanded text, optional sound cue.
- **Auto-focus.** When a dramatic moment happens in one game, that panel briefly expands or highlights. The Arena breathes — it draws your eye to where the action is, like a sports broadcast cutting to the hottest game.
- **Scoreboard ticker.** Running score ticker across the top or bottom with all games, clickable to expand any game to Single Game view.
- **Commentary stream.** Each game panel shows the latest line of AI commentary. Short, punchy — one sentence per possession at most. Full commentary available in Single Game view.

### Arena SSE Events

The Arena subscribes to the main SSE stream and filters for game events:

```
game.possession    → Update the relevant game panel
game.move          → Highlight the Move in the panel
game.highlight     → Trigger dramatic moment treatment
game.commentary    → Update the commentary line for that game
game.quarter_end   → Update period display
game.elam_start    → Transform panel to Elam mode
game.result        → Final score, link to full box score
```

Each event carries a `game_id` so the Arena routes it to the correct panel.

### Between Rounds

When no games are live, the Arena transforms into a **lobby view**:
- Next round countdown
- Current standings
- Active governance proposals (if a governance window is open)
- Recent reports (summaries, linked to full text)
- Upcoming matchup preview cards

## Single Game View

Click any game in the Arena (or navigate directly) to get the full single-game experience.

### Layout

```
┌──────────────────────────────────────────────────────────┐
│  ROSE CITY THORNS  vs  BURNSIDE BREAKERS                 │
│  Home: The Thorn Garden (18,000 seats, 50ft altitude)    │
│                                                          │
│  ┌─────────────────────────────────────┐                 │
│  │   THORNS  42  ——  BREAKERS  38     │                 │
│  │   Q3 — Possession 11/15            │                 │
│  │   Game Clock: 16:24                 │                 │
│  └─────────────────────────────────────┘                 │
│                                                          │
│  ┌─ PLAY-BY-PLAY ────────────────────────────────────┐   │
│  │ Q3-11  Thorns ball.                               │   │
│  │        Nakamura receives from Baptiste.           │   │
│  │        Pulls up from 25 feet — CONTESTED by Moon. │   │
│  │        ★ Move: Heat Check activated!              │   │
│  │        MAKES IT. Three-pointer. Thorns +5.        │   │
│  │                                                   │   │
│  │ 🎙️ "She had no business taking that shot. Moon   │   │
│  │    was RIGHT there. But that's the Heat Check —   │   │
│  │    Nakamura doesn't care about defense when she's │   │
│  │    feeling it. And she's been feeling it since     │   │
│  │    governance moved the three-point line back to   │   │
│  │    25 feet. The Thorns' governors knew what they   │   │
│  │    were doing."                                   │   │
│  └───────────────────────────────────────────────────┘   │
│                                                          │
│  ┌─ BOX SCORE ────────┐  ┌─ QUARTER SCORES ──────────┐  │
│  │ Nakamura  18pts 4a  │  │ Q1:  12-14               │  │
│  │ Baptiste   9pts 6r  │  │ Q2:  15-11               │  │
│  │ Okafor    11pts 3s  │  │ Q3:  15-13 (in progress) │  │
│  │ ---                 │  │                           │  │
│  │ Moon      14pts 3a  │  │                           │  │
│  │ ...                 │  │                           │  │
│  └─────────────────────┘  └───────────────────────────┘  │
│                                                          │
│  ┌─ RULE CONTEXT ─────────────────────────────────────┐  │
│  │ Active rules that differ from defaults:            │  │
│  │   three_point_value: 3 → 4 (Proposal #12, Rd 8)   │  │
│  │   three_point_distance: 22.15 → 25.0 (Prop #19)   │  │
│  │   elam_margin: 13 → 10 (Proposal #23, Rd 12)      │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

### Single Game Features

- **Full play-by-play.** Every possession with structured data AND AI commentary interspersed.
- **Live box score.** Updates after each possession. Shooting splits, plus/minus, fouls.
- **Quarter scores.** Period-by-period breakdown.
- **Rule context panel.** Shows which rules differ from defaults and which governance proposals changed them. Connects governance to the game you're watching.
- **Venue info.** Home team, capacity, altitude, surface. Venue modifiers displayed (crowd boost %, altitude penalty, travel fatigue).
- **Game timeline.** Visual timeline showing lead changes, runs, Moves triggered, and the Elam target line. Click any moment to jump to that possession.
- **Replay.** After a game ends, the full game is replayable from the stored GameResult. Same commentary, same pacing (or faster).

## AI Commentary Engine

This is the heart of the viewer experience. Opus 4.6 generates live commentary for every game — not as a post-hoc summary, but as a real-time narration layered onto the presenter's event stream.

### Architecture

```
┌────────────┐      ┌────────────┐      ┌─────────────┐      ┌──────────┐
│ Simulation │      │ Presenter  │      │ Commentary  │      │ Clients  │
│            │      │            │      │ Engine      │      │          │
│ simulate() │      │ Paces out  │      │             │      │ Arena    │
│ → GameResult├────►│ possessions├────►│ Opus 4.6    ├────►│ Single   │
│            │      │ via timer  │      │ generates   │      │ Discord  │
│ (instant)  │      │ (~1/min)   │      │ commentary  │      │          │
└────────────┘      └────────────┘      └─────────────┘      └──────────┘
```

**Key insight:** The commentary engine receives the *entire* GameResult up front. It knows who wins, when the lead changes, which Moves trigger, when the Elam Ending activates. It is an omniscient narrator. This means it can:

- Build tension before a big play: *"Nakamura's been quiet all third quarter. The Breakers think they've figured her out. They haven't."*
- Foreshadow the Elam Ending: *"The Thorns are up 6. In about four possessions, that lead becomes the Elam target. The Breakers need to close this gap NOW."*
- Connect governance to gameplay: *"Remember Proposal #19 — the one that moved the three-point line back to 25 feet? The Thorns' governors drafted that in their private channel. Nakamura is 4-for-5 from beyond the new arc tonight."*
- Narrate Moves with personality: *"Heat Check activated. You know what that means. She just made one from deep and now she's pulling up again — contested, off-balance, absolutely disrespectful. ... It goes in."*
- Track momentum: *"That's a 9-2 Breakers run. The crowd at Thorn Garden is getting nervous."*

### Commentary Generation

Commentary is generated **per-possession batch** — not one API call per possession (too expensive and slow), but in chunks that align with the presenter's pacing.

**Approach: Sliding window with lookahead.**

```python
class CommentaryEngine:
    """Generates live commentary for a pre-computed game."""

    def __init__(self, game_result: GameResult, rules: RuleSet):
        self.game = game_result
        self.rules = rules
        self.commentary_cache: dict[int, str] = {}  # possession_index → commentary

    async def generate_batch(
        self,
        possession_start: int,
        possession_end: int,
    ) -> list[CommentaryLine]:
        """Generate commentary for a batch of possessions.

        Called by the presenter ahead of the current display position.
        The engine sees the full game but generates commentary that
        reads as if it's being called live.
        """
        # Build context for Opus 4.6
        context = CommentaryContext(
            game=self.game,
            rules=self.rules,
            current_range=(possession_start, possession_end),
            # Full game is available — AI can reference future events
            # without spoiling them (building tension)
            prior_commentary=[
                self.commentary_cache[i]
                for i in range(possession_start)
                if i in self.commentary_cache
            ],
        )
        # One API call per batch (e.g., 5-8 possessions)
        lines = await generate_commentary(context)
        for i, line in enumerate(lines):
            self.commentary_cache[possession_start + i] = line
        return lines
```

**Batch sizing:** The presenter streams ~1 possession per minute in production mode. Commentary is generated in batches of 5-8 possessions, requested ~5 minutes ahead of the current display position. This gives Opus 4.6 time to respond and creates a buffer.

**Cost management:**
- 4 games per round × ~15 batches per game × 1 API call per batch = ~60 API calls per round for commentary.
- Commentary batches are short (small prompt, small response). Use the fastest model tier available.
- In dev/staging, commentary can be disabled or generated at lower frequency (every 3 possessions instead of every 1).
- Commentary is cached with the GameResult — replay doesn't require new API calls.

### Commentary Context

What Opus 4.6 receives for each commentary batch:

```python
class CommentaryContext(BaseModel):
    # The full game (Opus knows the outcome)
    game: GameResult

    # Current rules (especially deviations from defaults)
    rules: RuleSet
    rule_changes: list[RuleChange]     # which proposals changed which params

    # Which possessions to commentate
    current_range: tuple[int, int]

    # Prior commentary (for continuity)
    prior_commentary: list[str]

    # Agent personalities and backstories
    agents: list[AgentProfile]

    # Season context
    standings: Standings               # where these teams sit
    series_context: SeriesContext | None  # if this is a playoff game
    head_to_head: HeadToHead           # prior matchups this season
```

### Commentary System Prompt

```
You are the play-by-play commentator for Pinwheel Fates, a 3v3 basketball
league where the rules are set by the fans.

You are calling this game LIVE — but you secretly know the full result.
Use this foreknowledge to build tension, not to spoil. Foreshadow big
moments. Build toward dramatic plays. Let the audience feel the momentum
shifts before they see the score change.

STYLE:
- Conversational, not robotic. You're a broadcaster, not a stat sheet.
- Short lines. One to three sentences per possession, max.
- Reference rule changes and governance when relevant — connect the rules
  the governors made to what's happening on the floor.
- Know the agents. Use their names, personalities, signature moves.
  Reference their backstories when it fits naturally.
- React to Moves with energy. Moves are the spectacle.
- During the Elam Ending, increase intensity. Every possession matters.
- Vary your energy. Not every possession is dramatic. Some are routine.
  Let the rhythm breathe so the big moments land.
- You may address the audience directly.
- NO play-by-play for every single action within a possession. Focus on
  the key moment — the shot, the steal, the Move trigger.

DO NOT:
- Spoil the outcome before it happens.
- Use generic sports cliches excessively.
- Commentate identically on similar plays. Find variety.
- Reference anything outside the game world (no real NBA, no real players).

RULE CONTEXT: The following rules differ from league defaults. Reference
these when they affect play — they are the fingerprints of governance
on this game.
{rule_changes}

AGENTS:
{agent_profiles}

STANDINGS CONTEXT:
{standings_summary}
```

### Commentary Types

Not every possession gets the same treatment:

| Situation | Commentary Style | Example |
|-----------|-----------------|---------|
| Routine possession | Brief, factual | *"Baptiste with the easy deuce inside. Thorns by 3."* |
| Move trigger | Energetic, personality | *"HEAT CHECK! She's unconscious!"* |
| Lead change | Momentum language | *"And just like that, the Breakers are on top. That's a 7-0 run."* |
| Entering Elam | Scene-setting | *"The horn sounds. Target score: 58. Thorns need 7. Foxes need 14. Every possession from here on out could end it."* |
| Game-winning basket | Peak energy | *"Nakamura... from the logo... IT'S GOOD! THORNS WIN! The Thorn Garden ERUPTS!"* |
| Governance callback | Analytical | *"That three-pointer is worth 4 points tonight, thanks to Proposal #12. The Wolves' governors pushed for that change — and it's their opponents benefiting."* |
| Blowout | Casual, look-ahead | *"This one's getting away from the Ravens. Let's check in on the Foxes game — that one's heading to the Elam Ending."* |

### Commentary SSE Event

```python
class CommentaryEvent(BaseModel):
    game_id: str
    possession_index: int
    quarter: int
    commentary: str
    energy: Literal["low", "medium", "high", "peak"]  # drives visual treatment
    tags: list[str]  # ["move", "lead_change", "elam", "governance", "clutch"]
```

The `energy` field tells the frontend how to render the commentary — low energy gets normal text, peak energy gets animation/emphasis. Tags enable filtering (show only Move commentary, show only governance callbacks).

## Discord Bot Search

The Discord bot is primarily a governance tool (see PLAYER.md), but it's also a stats desk. Governors and spectators can query game data conversationally.

### How It Works

A user asks a natural language question in any channel where the bot is active. The bot uses Opus 4.6 to:

1. Parse the question into one or more API calls
2. Fetch the data from the FastAPI backend
3. Format the response conversationally

```
User: How did the Thorns do last round?

🤖 Pinwheel: The Thorns went 1-0 in Round 14.
Beat the Breakers 58-52 at home. Nakamura had 24 points
on 8-12 shooting (4-5 from three). The Thorn Garden
crowd was loud — +5% crowd boost in a packed house.

They're 15-6 on the season, sitting at #2 behind
the Iron Horses. Next up: @ Foxes in Round 15.
```

```
User: Who leads the league in steals?

🤖 Pinwheel: League steal leaders through Round 14:

1. Jax "The Phantom" Rivera (Foxes) — 42 steals (2.0/game)
2. Okafor (Thorns) — 38 steals (1.8/game)
3. Moon (Breakers) — 35 steals (1.7/game)

Rivera's been on a tear since governance banned press
defense in Q1-Q3 (Proposal #15). Turns out, limiting
the scheme that generates the MOST steals made the
players who steal in man-to-man even more valuable.
The reporter noticed that pattern three rounds ago.
```

```
User: Show me Thorns vs Breakers box score from Round 14

🤖 Pinwheel: 📊 Round 14 — Thorns 58, Breakers 52
Venue: The Thorn Garden (home)

THORNS
Nakamura    24pts  8-12 FG  4-5 3PT  4a  1s  2f
Baptiste     9pts  4-9  FG  0-0 3PT  6r  2a  3f
Okafor      18pts  7-11 FG  1-3 3PT  3s  4r  1f
Chen (bench) 7pts  3-5  FG  1-2 3PT  1a  0f

BREAKERS
Moon        14pts  5-13 FG  2-6 3PT  3a  1s  4f
...

Elam target: 55 (set after Q3: Thorns 48 + 7)
Elam possessions: 8
Game-winner: Nakamura 3PT (contested by Moon — Heat Check)
```

### Bot Query Architecture

```
Discord message
    │
    ▼
Bot receives text
    │
    ▼
Opus 4.6 (query parser context)
    │  "Parse this into API calls. Available endpoints: ..."
    │
    ▼
Structured API calls
    │  GET /api/games/{round}/results
    │  GET /api/teams/{team_id}/stats
    │  GET /api/players/{player_id}/season
    │
    ▼
API returns JSON
    │
    ▼
Opus 4.6 (response formatter context)
    │  "Format this data conversationally for Discord."
    │  "Reference governance context when relevant."
    │
    ▼
Bot posts formatted response
```

**Two-call pattern:** The bot makes two Opus 4.6 calls per query:
1. **Parse:** Natural language → structured API calls. Small, fast. Uses the API schema as context.
2. **Format:** Raw API data → conversational Discord message. Adds personality, governance context, report-like observations.

This separation means the parse step can be cached (same question structure → same API calls) and the format step can be tuned for tone independently.

### Queryable Data

The bot can answer questions about:

| Category | Example Queries |
|----------|----------------|
| **Game results** | "Who won Thorns vs Breakers?", "What happened in Round 12?" |
| **Box scores** | "Show me the box score", "How did Nakamura play?" |
| **Standings** | "Who's in first?", "What's the playoff picture?" |
| **Season stats** | "League leaders in scoring", "Who has the best 3PT%?" |
| **Agent profiles** | "Tell me about Indigo Moon", "What moves does Nakamura have?" |
| **Rule history** | "What rules have changed?", "Who proposed the three-point change?" |
| **Governance** | "How did the last vote go?", "What's up for vote?" |
| **Head-to-head** | "Thorns vs Breakers this season", "Who owns the matchup?" |
| **Venue** | "What's the altitude at Iron Horse Arena?", "Biggest venue?" |
| **Upcoming** | "When's the next game?", "Who do the Thorns play next?" |

### Bot Search Limits

- **Rate limited.** The bot won't respond to rapid-fire queries from one user. One query at a time, with a brief cooldown.
- **Public data only.** Bot search never reveals private reports, team strategies, or hidden votes. Even if you ask.
- **No predictions.** The bot will not predict game outcomes or recommend governance actions. It reports data. The reporter interprets. The governors decide.

## API Endpoints (Viewer-Facing)

All viewer surfaces read from these endpoints. The API is the single source of truth.

### Real-Time (SSE)

```
GET /api/events/stream
    Query params:
      ?games=true        → game events (possession, move, highlight, result)
      ?commentary=true   → AI commentary events
      ?governance=true   → governance events
      ?reports=true      → report events
      ?game_id={id}      → filter to a single game
      ?team_id={id}      → filter to a team's games

    Returns: SSE stream with typed JSON events
```

Single SSE endpoint with query param filtering. Clients subscribe once. The Arena subscribes to `?games=true&commentary=true`. Single Game view adds `?game_id={id}`. The governance panel subscribes to `?governance=true`.

### REST (Historical/Static)

```
Game Data
  GET /api/games/live                    → Currently presenting games
  GET /api/games/{game_id}               → Full GameResult + commentary
  GET /api/games/{game_id}/boxscore      → Box score only
  GET /api/games/{game_id}/play-by-play  → Full possession log
  GET /api/games/{game_id}/commentary    → Cached commentary

Round Data
  GET /api/rounds/current                → Current round info
  GET /api/rounds/{round_number}         → Round results
  GET /api/rounds/{round_number}/games   → All games in a round

Season Data
  GET /api/standings                     → Current standings
  GET /api/stats/leaders                 → League stat leaders
  GET /api/stats/leaders/{stat}          → Leaders for a specific stat
  GET /api/stats/teams                   → Team-level stats
  GET /api/playoffs/bracket              → Playoff bracket (when active)

Team Data
  GET /api/teams                         → All teams
  GET /api/teams/{team_id}               → Team detail (roster, venue, record)
  GET /api/teams/{team_id}/schedule      → Team schedule + results
  GET /api/teams/{team_id}/stats         → Team aggregate stats

Agent Data
  GET /api/agents/{agent_id}             → Agent profile + attributes + moves
  GET /api/agents/{agent_id}/stats       → Season stats
  GET /api/agents/{agent_id}/gamelog     → Per-game stats

Head-to-Head
  GET /api/matchups/{team_a}/{team_b}    → Season series, head-to-head stats

Governance (Public)
  GET /api/rules/current                 → Current ruleset
  GET /api/rules/history                 → Timeline of rule changes
  GET /api/governance/proposals          → Active + past proposals
  GET /api/governance/proposals/{id}     → Proposal detail + votes (if revealed)

Reports (Public)
  GET /api/reports/latest                → Most recent reports by type
  GET /api/reports/{type}/{round}        → Specific report
```

### Response Format

All REST endpoints return Pydantic-serialized JSON. Responses include:
- `data`: The requested resource
- `meta`: Pagination, timestamps, links to related resources
- `governance_context`: When relevant, which rule changes affect this data

```json
{
  "data": { ... },
  "meta": {
    "round": 14,
    "timestamp": "2026-02-10T18:30:00Z",
    "links": {
      "boxscore": "/api/games/g-14-1/boxscore",
      "commentary": "/api/games/g-14-1/commentary"
    }
  },
  "governance_context": [
    {
      "parameter": "three_point_value",
      "value": 4,
      "default": 3,
      "changed_by": "proposal-12",
      "round_enacted": 8
    }
  ]
}
```

## Dashboard Pages

Beyond the Arena and Single Game views, the web dashboard has these pages:

### Standings

Live-updating league table. Columns: rank, team, W-L, win%, last 5, streak, home/away splits. Clicking a team goes to the Team Page.

### Team Page

Team profile: roster (agent cards with attributes, moves, stats), venue details, record, schedule with results, governance history (proposals by this team's governors), team-level reports.

### Agent Page

Individual agent profile: attributes radar chart, moves list, season stats, game log, narrative bio (AI-generated). Notable moments (Move triggers, clutch shots, Fate events).

### Rules Page

Current ruleset with visual diff from defaults. Timeline view showing when each rule changed, who proposed it, how the vote went. Click any rule change to see the proposal and debate thread.

### Reports Page

Archive of all public reports, organized by type and round. Searchable. Each report links to the games/governance actions it references.

### Season History

After a season ends: full narrative (season report), final standings, playoff bracket with results, awards, stat leaders, rule evolution timeline. The permanent record.

## Presentation Pacing

The presenter controls timing. All viewer surfaces consume the presenter's output.

### Pace Modes

| Mode | Possession Interval | Game Duration | Use Case |
|------|-------------------|---------------|----------|
| **Production** | ~60 seconds | 20-30 minutes | Live season play |
| **Fast** | ~15 seconds | 5-8 minutes | Demo, hackathon video |
| **Instant** | 0 (all at once) | Immediate | Testing, stats analysis |
| **Replay** | Variable (user controls) | User-controlled | Rewatching past games |

In production mode, the presenter drips out possessions at ~1 per minute. Commentary is generated ahead and cached. The Arena feels live — even though the outcome is predetermined.

### Dramatic Pacing

The presenter doesn't space possessions evenly. It adjusts pace for drama:

- **Routine possessions:** Normal interval
- **Run in progress** (one team on a 5-0+ run): Slightly faster — momentum feels urgent
- **Lead change:** Brief pause before — let the audience register the stakes
- **Entering Elam:** Longer pause. Commentary sets the scene. Target score displayed.
- **Final Elam possessions:** Slower. Every play gets space. Commentary builds.
- **Game-winning shot:** Pause before resolution. The commentary foreshadows. Then the result drops.

This is possible because the presenter has the full GameResult. It knows where the drama is and can choreograph the reveal.

## Implementation Priority

1. **SSE infrastructure** — Single endpoint, typed events, client subscription management. This is the backbone everything else builds on.
2. **Arena layout** — 2x2 game grid, live score updates, possession-by-possession streaming. Minimum viable: scores updating, last play displayed as text.
3. **Single Game view** — Full play-by-play, live box score, quarter scores. Commentary placeholder (static text until AI is wired up).
4. **REST API endpoints** — Game data, standings, stats, teams, agents. All Pydantic models. FastAPI auto-docs.
5. **Commentary engine** — Opus 4.6 integration for live commentary. Batch generation, caching, SSE delivery.
6. **Bot search** — Two-call pattern (parse + format). Start with standings/scores/box scores, expand to stats and governance queries.
7. **Dashboard pages** — Standings, team pages, agent pages, rules, reports. Static pages with HTMX partial updates.
8. **Dramatic pacing** — Presenter pace modulation based on game state. The difference between "data updating on a screen" and "a game you're watching."
9. **Replay** — Replayable games from stored GameResults + cached commentary.

## Decisions

1. **Commentary is pre-generated in batches, not per-possession.** Cost and latency make per-possession API calls impractical. Batch of 5-8 possessions, generated ahead of the presenter's current position.
2. **The AI commentator knows the outcome.** This is a feature, not a bug. Omniscient narration enables foreshadowing, tension-building, and dramatic pacing that live commentary can't achieve.
3. **Bot search uses two Opus calls (parse + format).** Separating question parsing from response formatting allows caching at the parse layer and tone control at the format layer.
4. **Single SSE endpoint with filtering.** Simpler than multiple SSE endpoints. Clients subscribe once and filter by query params.
5. **Commentary cached with GameResult.** Replay doesn't require new API calls. Commentary is part of the game's permanent record.

## Open Questions

1. **Commentary model tier:** Should commentary use Opus 4.6 (best quality, highest cost) or a faster/cheaper model (Sonnet, Haiku)? Commentary quality is highly visible — it's the voice of the game. But 60 API calls per round is significant. Could use Opus for key moments (Elam, Moves, game-winner) and a faster model for routine possessions.
2. **Arena visual design:** The 2x2 grid is functional but the aesthetic matters enormously. The retro sports broadcast aesthetic is the north star. Should we invest in a designer, or can the HTMX + CSS approach achieve the feel?
3. **Commentary in Discord:** Should live commentary stream to the #game-day channel during games? This would make Discord a viewing surface too, not just a governance surface. But it could be noisy.
4. **Spoiler protection:** If a user loads the Arena mid-game, should they see the current state (spoiler) or start from the beginning? Option: show live state by default, offer "watch from start" button using replay.
