# Pinwheel Fates -- Full Cycle Demo

*2026-02-13T01:17:07Z*

**Pinwheel Fates** is a simulated 3v3 basketball league with human-driven, AI-interpreted governance and rules. Starts out as basketball, finishes as ???. The AI serves as a social mirror -- surfacing patterns in gameplay and governance that players cannot see from inside the system.

This document proves the full **Govern > Simulate > Observe > Reflect** cycle works end-to-end. Every command below was executed live; every screenshot was captured from the running application.

## Step 1: Seed the League

Create 4 Portland-themed teams with 3 agents each and generate a round-robin schedule.

```bash
uv run python scripts/demo_seed.py seed
```

```output
League seeded: 4 teams, 6 scheduled games
Season ID: 340fc2a1-0da7-48e4-80d0-c37b54550916
  Rose City Thorns: 3795ac4f-812e-4310-81bd-e446fada12a4
  Burnside Breakers: d27940a1-aa4a-4ccb-bc05-cc1b6b04d037
  St. Johns Herons: 1d4bf260-622c-487e-af0b-26bc4d6c2c6b
  Hawthorne Hammers: e4af1bb8-bc17-4144-afb3-ca0b2372f387
```

## Step 2: Start the Web Dashboard

Launch the FastAPI server. The dashboard renders with HTMX + Jinja2 -- no JS build step.

```bash
curl -s http://localhost:8765/health | python3 -m json.tool
```

```output
{
    "status": "ok",
    "env": "development"
}
```

## Step 3: The Dashboard

The home page with navigation cards. Dark theme, retro sports broadcast aesthetic.

```bash {image}
uvx rodney screenshot demo/01_home.png -w 1280 -h 900
```

![cbf5bf71-2026-02-13](cbf5bf71-2026-02-13.png)

## Step 4: Simulate Round 1

Run the first round of games. The simulation engine resolves every possession probabilistically based on agent attributes and the current ruleset.

```bash
uv run python scripts/demo_seed.py step 1
```

```output
Round 1: 2 games, 2 mirrors
  Rose City Thorns 64 - 47 Burnside Breakers (HOME) [ELAM]
  St. Johns Herons 48 - 44 Hawthorne Hammers (HOME) [ELAM]
  Mirror (simulation): St. Johns Herons edged Hawthorne Hammers 48-44. Neither team blinked until the E...
  Mirror (governance): Round 1 was quiet on the governance front — no proposals filed....
```

## Step 5: The Arena

Game results appear in the Arena across multiple rounds with vivid Elam banner narration and per-round simulation mirrors. Each game panel shows the final score, possession count, and Elam Ending status.

```bash {image}
uvx rodney screenshot demo/02_arena.png -w 1280 -h 1400
```

![1737dbd0-2026-02-13](1737dbd0-2026-02-13.png)

## Step 6: Standings

The league table updates after each round. Win/Loss, Points For/Against, Differential.

```bash {image}
uvx rodney screenshot demo/03_standings.png -w 1280 -h 900
```

![4021542a-2026-02-13](4021542a-2026-02-13.png)

## Step 7: Game Detail

Click into a game for box scores and rich narrated play-by-play with player names and defenders. Every possession is recorded.

```bash {image}
uvx rodney screenshot demo/04_game_detail.png -w 1280 -h 1200
```

![89d8d9a6-2026-02-13](89d8d9a6-2026-02-13.png)

## Step 8: Advance the Season

Run 2 more rounds to build up standings and mirror data.

```bash
uv run python scripts/demo_seed.py step 2
```

```output
Round 2: 2 games, 2 mirrors
  Rose City Thorns 59 - 54 Hawthorne Hammers (HOME) [ELAM]
  Burnside Breakers 52 - 46 St. Johns Herons (HOME) [ELAM]
  Mirror (simulation): Rose City Thorns and Hawthorne Hammers traded buckets all game. Final: 59-54. Th...
  Mirror (governance): Round 2 was quiet on the governance front — no proposals filed....
Round 3: 2 games, 2 mirrors
  Rose City Thorns 52 - 58 St. Johns Herons (AWAY) [ELAM]
  Hawthorne Hammers 58 - 67 Burnside Breakers (AWAY) [ELAM]
  Mirror (simulation): Rose City Thorns and St. Johns Herons traded buckets all game. Final: 52-58. The...
  Mirror (governance): Round 3 was quiet on the governance front — no proposals filed....
```

## Step 9: Standings After 3 Rounds

```bash
uv run python scripts/demo_seed.py status
```

```output
Season: Season 1 | Rounds played: 3
Team                        W   L    PCT    PF    PA  DIFF
-------------------------------------------------------
Rose City Thorns            2   1  0.667   175   159 +  16
St. Johns Herons            2   1  0.667   152   148 +   4
Burnside Breakers           2   1  0.667   166   168   -2
Hawthorne Hammers           0   3  0.000   156   174  -18
```

```bash {image}
uvx rodney screenshot demo/05_standings_r3.png -w 1280 -h 900
```

![9e6f7ca2-2026-02-13](9e6f7ca2-2026-02-13.png)

## Step 10: AI Mirrors

Narrative mirrors that reference specific teams and game details. The mirror system reflects on gameplay and governance. AI-generated observations describe patterns -- they never prescribe actions.

```bash {image}
uvx rodney screenshot demo/06_mirrors.png -w 1280 -h 1200
```

![a688f4e9-2026-02-13](a688f4e9-2026-02-13.png)

## Step 11: Governance -- Submit a Proposal

A governor proposes a rule change in natural language. The AI interprets it into structured parameters.

```bash
uv run python scripts/demo_seed.py propose Make three-pointers worth 5 points
```

```output
Proposal submitted: p-2478a359
  Text: Make three-pointers worth 5 points
```

```bash {image}
uvx rodney screenshot demo/07_governance.png -w 1280 -h 900
```

![3a3084d1-2026-02-13](3a3084d1-2026-02-13.png)

## Step 12: Current Ruleset

The rules page shows all current parameters and highlights changes from defaults.

```bash {image}
uvx rodney screenshot demo/08_rules.png -w 1280 -h 900
```

![716f554f-2026-02-13](716f554f-2026-02-13.png)

## Step 13: Team Profile

Each team has a profile with roster, agent attributes (visualized as bars), and venue info.

```bash {image}
uvx rodney screenshot demo/09_team.png -w 1280 -h 1200
```

![f1df36d3-2026-02-13](f1df36d3-2026-02-13.png)

## Step 14: Evals Dashboard

The admin-facing evals dashboard shows aggregate mirror quality metrics, scenario flags, and AI rule evaluation. No individual mirror text is ever displayed -- only counts, rates, and composite scores.
