# Pinwheel Fates -- Full Cycle Demo

*2026-02-12T22:25:02Z*

**Pinwheel Fates** is a simulated 3v3 basketball league with human-driven, AI-interpreted governance and rules. Starts out as basketball, finishes as ???. The AI serves as a social mirror -- surfacing patterns in gameplay and governance that players cannot see from inside the system.

This document proves the full **Govern > Simulate > Observe > Reflect** cycle works end-to-end. Every command below was executed live; every screenshot was captured from the running application.

## Step 1: Seed the League

Create 4 Portland-themed teams with 3 agents each and generate a round-robin schedule.

```bash
uv run python scripts/demo_seed.py seed
```

```output
League seeded: 4 teams, 6 scheduled games
Season ID: aca52e1e-951c-4a7a-bf9d-4cd6f1303cbb
  Rose City Thorns: af8b004f-6ca9-4a87-8d93-54659457ea76
  Burnside Breakers: 73972ee4-366b-4ab7-8ccb-d38501af3493
  St. Johns Herons: 774b0c49-4a52-4138-8b57-40e5773295f7
  Hawthorne Hammers: e003fca0-13ae-4e85-818f-af437f326793
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

![0f6eacec-2026-02-12](0f6eacec-2026-02-12.png)

## Step 4: Simulate Round 1

Run the first round of games. The simulation engine resolves every possession probabilistically based on agent attributes and the current ruleset.

```bash
uv run python scripts/demo_seed.py step 1
```

```output
Round 1: 2 games, 2 mirrors
  Rose City Thorns 70 - 68 Burnside Breakers (HOME) [ELAM]
  St. Johns Herons 70 - 61 Hawthorne Hammers (HOME) [ELAM]
  Mirror (simulation): Rose City Thorns edged Burnside Breakers 70-68. Neither team blinked until the E...
  Mirror (governance): Round 1 was quiet on the governance front — no proposals filed....
```

## Step 5: The Arena

Game results appear in the Arena across multiple rounds with vivid Elam banner narration and per-round simulation mirrors. Each game panel shows the final score, possession count, and Elam Ending status.

```bash {image}
uvx rodney screenshot demo/02_arena.png -w 1280 -h 1400
```

![351d8a2a-2026-02-12](351d8a2a-2026-02-12.png)

## Step 6: Standings

The league table updates after each round. Win/Loss, Points For/Against, Differential.

```bash {image}
uvx rodney screenshot demo/03_standings.png -w 1280 -h 900
```

![d0e1da27-2026-02-12](d0e1da27-2026-02-12.png)

## Step 7: Game Detail

Click into a game for box scores and rich narrated play-by-play with player names and defenders. Every possession is recorded.

```bash {image}
uvx rodney screenshot demo/04_game_detail.png -w 1280 -h 1200
```

![00f15502-2026-02-12](00f15502-2026-02-12.png)

## Step 8: Advance the Season

Run 2 more rounds to build up standings and mirror data.

```bash
uv run python scripts/demo_seed.py step 2
```

```output
Round 2: 2 games, 2 mirrors
  Rose City Thorns 60 - 66 Hawthorne Hammers (AWAY) [ELAM]
  Burnside Breakers 66 - 72 St. Johns Herons (AWAY) [ELAM]
  Mirror (simulation): Rose City Thorns and Hawthorne Hammers traded buckets all game. Final: 60-66. Th...
  Mirror (governance): Round 2 was quiet on the governance front — no proposals filed....
Round 3: 2 games, 2 mirrors
  Rose City Thorns 74 - 64 St. Johns Herons (HOME) [ELAM]
  Hawthorne Hammers 54 - 61 Burnside Breakers (AWAY) [ELAM]
  Mirror (simulation): Rose City Thorns dismantled St. Johns Herons by 10. It wasn't close after the fi...
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
Rose City Thorns            2   1  0.667   204   198 +   6
St. Johns Herons            2   1  0.667   206   201 +   5
Burnside Breakers           1   2  0.333   195   196   -1
Hawthorne Hammers           1   2  0.333   181   191  -10
```

```bash {image}
uvx rodney screenshot demo/05_standings_r3.png -w 1280 -h 900
```

![1af1542d-2026-02-12](1af1542d-2026-02-12.png)

## Step 10: AI Mirrors

Narrative mirrors that reference specific teams and game details. The mirror system reflects on gameplay and governance. AI-generated observations describe patterns -- they never prescribe actions.

```bash {image}
uvx rodney screenshot demo/06_mirrors.png -w 1280 -h 1200
```

![b4088a10-2026-02-12](b4088a10-2026-02-12.png)

## Step 11: Governance -- Submit a Proposal

A governor proposes a rule change in natural language. The AI interprets it into structured parameters.

```bash
uv run python scripts/demo_seed.py propose Make three-pointers worth 5 points
```

```output
Proposal submitted: p-00931d25
  Text: Make three-pointers worth 5 points
```

```bash {image}
uvx rodney screenshot demo/07_governance.png -w 1280 -h 900
```

![98f5eebe-2026-02-12](98f5eebe-2026-02-12.png)

## Step 12: Current Ruleset

The rules page shows all current parameters and highlights changes from defaults.

```bash {image}
uvx rodney screenshot demo/08_rules.png -w 1280 -h 900
```

![6a7886cf-2026-02-12](6a7886cf-2026-02-12.png)

## Step 13: Team Profile

Each team has a profile with roster, agent attributes (visualized as bars), and venue info.

```bash {image}
uvx rodney screenshot demo/09_team.png -w 1280 -h 1200
```

![8dd1d20d-2026-02-12](8dd1d20d-2026-02-12.png)

## Step 14: Evals Dashboard

The admin-facing evals dashboard shows aggregate mirror quality metrics, scenario flags, and AI rule evaluation. No individual mirror text is ever displayed -- only counts, rates, and composite scores.
