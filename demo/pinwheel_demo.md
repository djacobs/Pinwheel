# Pinwheel Fates -- Full Cycle Demo

*2026-02-13T16:52:09Z*

**Pinwheel Fates** is a simulated 3v3 basketball league with human-driven, AI-interpreted governance and rules. Starts out as basketball, finishes as ???. The AI serves as a social mirror -- surfacing patterns in gameplay and governance that players cannot see from inside the system.

This document proves the full **Govern > Simulate > Observe > Reflect** cycle works end-to-end. Every command below was executed live; every screenshot was captured from the running application.

## Step 1: Seed the League

Create 4 Portland-themed teams with 3 agents each and generate a round-robin schedule.

```bash
uv run python scripts/demo_seed.py seed
```

```output
League seeded: 4 teams, 6 scheduled games
Season ID: 03cb46f0-ee68-41ac-9ef9-eb747a2e23b1
  Rose City Thorns: 7b9ee892-7284-4eca-b88e-36795d9bddf1
  Burnside Breakers: 70f6a7b8-0bb1-406e-975e-ee6752035826
  St. Johns Herons: a2a26ad3-dd74-4817-b524-e69c20c45d97
  Hawthorne Hammers: 457021be-39ed-4d8d-bced-0ac5dbd7e29c
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

![b5d5cad2-2026-02-13](b5d5cad2-2026-02-13.png)

## Step 4: Simulate Round 1

Run the first round of games. The simulation engine resolves every possession probabilistically based on agent attributes and the current ruleset.

```bash
uv run python scripts/demo_seed.py step 1
```

```output
Round 1: 2 games, 2 mirrors
  Rose City Thorns 58 - 56 Burnside Breakers (HOME) [ELAM]
  St. Johns Herons 53 - 58 Hawthorne Hammers (AWAY) [ELAM]
  Mirror (simulation): Rose City Thorns edged Burnside Breakers 58-56. Neither team blinked until the E...
  Mirror (governance): Round 1 was quiet on the governance front — no proposals filed....
```

## Step 5: The Arena

Game results appear in the Arena across multiple rounds with vivid Elam banner narration and per-round simulation mirrors. Each game panel shows the final score, possession count, and Elam Ending status.

```bash {image}
uvx rodney screenshot demo/02_arena.png -w 1280 -h 1400
```

![39d3d5b2-2026-02-13](39d3d5b2-2026-02-13.png)

## Step 6: Standings

The league table updates after each round. Win/Loss, Points For/Against, Differential.

```bash {image}
uvx rodney screenshot demo/03_standings.png -w 1280 -h 900
```

![0e5730cd-2026-02-13](0e5730cd-2026-02-13.png)

## Step 7: Game Detail

Click into a game for box scores and rich narrated play-by-play with player names and defenders. Every possession is recorded.

```bash {image}
uvx rodney screenshot demo/04_game_detail.png -w 1280 -h 1200
```

![cc309b40-2026-02-13](cc309b40-2026-02-13.png)

## Step 8: Advance the Season

Run 2 more rounds to build up standings and mirror data.

```bash
uv run python scripts/demo_seed.py step 2
```

```output
Round 2: 2 games, 2 mirrors
  Rose City Thorns 52 - 44 Hawthorne Hammers (HOME) [ELAM]
  Burnside Breakers 61 - 53 St. Johns Herons (HOME) [ELAM]
  Mirror (simulation): Rose City Thorns and Hawthorne Hammers traded buckets all game. Final: 52-44. Th...
  Mirror (governance): Round 2 was quiet on the governance front — no proposals filed....
Round 3: 2 games, 2 mirrors
  Rose City Thorns 59 - 53 St. Johns Herons (HOME) [ELAM]
  Hawthorne Hammers 44 - 56 Burnside Breakers (AWAY) [ELAM]
  Mirror (simulation): Burnside Breakers dismantled Hawthorne Hammers by 12. It wasn't close after the ...
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
Rose City Thorns            3   0  1.000   169   153 +  16
Burnside Breakers           2   1  0.667   173   155 +  18
Hawthorne Hammers           1   2  0.333   146   161  -15
St. Johns Herons            0   3  0.000   159   178  -19
```

```bash {image}
uvx rodney screenshot demo/05_standings_r3.png -w 1280 -h 900
```

![170652e1-2026-02-13](170652e1-2026-02-13.png)

## Step 10: AI Mirrors

Narrative mirrors that reference specific teams and game details. The mirror system reflects on gameplay and governance. AI-generated observations describe patterns -- they never prescribe actions.

```bash {image}
uvx rodney screenshot demo/06_mirrors.png -w 1280 -h 1200
```

![86002a20-2026-02-13](86002a20-2026-02-13.png)

## Step 11: Governance -- Submit a Proposal

A governor proposes a rule change in natural language. The AI interprets it into structured parameters.

```bash
uv run python scripts/demo_seed.py propose Make three-pointers worth 5 points
```

```output
Proposal submitted: p-cc77837f
  Text: Make three-pointers worth 5 points
```

```bash {image}
uvx rodney screenshot demo/07_governance.png -w 1280 -h 900
```

![526c0ef9-2026-02-13](526c0ef9-2026-02-13.png)

## Step 12: Current Ruleset

The rules page shows all current parameters and highlights changes from defaults.

```bash {image}
uvx rodney screenshot demo/08_rules.png -w 1280 -h 900
```

![c1cf5640-2026-02-13](c1cf5640-2026-02-13.png)

## Step 13: Team Profile

Each team has a profile with roster, agent attributes (visualized as bars), and venue info.

```bash {image}
uvx rodney screenshot demo/09_team.png -w 1280 -h 1200
```

![7faab422-2026-02-13](7faab422-2026-02-13.png)

## Step 14: Evals Dashboard

The admin-facing evals dashboard shows aggregate mirror quality metrics, scenario flags, and AI rule evaluation. No individual mirror text is ever displayed -- only counts, rates, and composite scores.
