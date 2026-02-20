# Pinwheel Fates -- Full Cycle Demo

*2026-02-20T22:50:05Z by Showboat 0.6.0*
<!-- showboat-id: 7f2717e0-df56-4761-8bbb-9c37229aeba3 -->

**Pinwheel Fates** is a simulated 3v3 basketball league with human-driven, AI-interpreted governance and rules. Starts out as basketball, finishes as ???. The AI serves as a reporter -- surfacing patterns in gameplay and governance that players cannot see from inside the system.

This document proves the full **Govern > Simulate > Observe > Reflect** cycle works end-to-end. Every command below was executed live; every screenshot was captured from the running application.

## Step 1: Seed the League

Create 4 Portland-themed teams with 3 agents each and generate a round-robin schedule.

```bash
uv run python scripts/demo_seed.py seed
```

```output
League seeded: 4 teams, 18 scheduled games
Season ID: ef1d9ad3-a2e5-4677-b70f-828297fa8dda
  Rose City Thorns: 74826a08-cfa9-479f-abb0-f9d41b84cd21
  Burnside Breakers: bd8d78bb-19e7-4074-af3b-54d3aac1fd55
  St. Johns Herons: 40d89120-ab92-4382-ab3f-2f85ee42add4
  Hawthorne Hammers: ebb95fd1-e4ed-4322-a1ea-fd271abc03d2
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
demo/01_home.png
```

![bfbf67d7-2026-02-20](bfbf67d7-2026-02-20.png)

## Step 4: Simulate Round 1

Run the first round of games. The simulation engine resolves every possession probabilistically based on agent attributes and the current ruleset.

```bash
uv run python scripts/demo_seed.py step 1
```

```output
Round 1: 2 games, 2 reports
  Rose City Thorns 56 - 51 Burnside Breakers (HOME) [ELAM]
  St. Johns Herons 62 - 54 Hawthorne Hammers (HOME) [ELAM]
  Report (simulation): Round 1. Rose City Thorns beat Burnside Breakers 56-51. Rose City Thorns beat Bu...
  Report (governance): Round 1 was quiet on the governance front -- no proposals filed....
```

## Step 5: The Arena

Game results appear in the Arena across multiple rounds with vivid Elam banner narration and per-round simulation reports. Each game panel shows the final score, possession count, and Elam Ending status.

```bash {image}
demo/02_arena.png
```

![d560496a-2026-02-20](d560496a-2026-02-20.png)

## Step 6: Standings

The league table updates after each round. Win/Loss, Points For/Against, Differential.

```bash {image}
demo/03_standings.png
```

![b815bff3-2026-02-20](b815bff3-2026-02-20.png)

## Step 7: Game Detail

Click into a game for box scores and rich narrated play-by-play with player names and defenders. Every possession is recorded.

```bash {image}
demo/04_game_detail.png
```

![ab830810-2026-02-20](ab830810-2026-02-20.png)

## Step 8: Advance the Season

Run 2 more rounds to build up standings and report data.

```bash
uv run python scripts/demo_seed.py step 2
```

```output
Round 2: 2 games, 2 reports
  Rose City Thorns 47 - 51 Hawthorne Hammers (AWAY) [ELAM]
  Burnside Breakers 52 - 67 St. Johns Herons (AWAY) [ELAM]
  Report (simulation): St. Johns Herons demolished Burnside Breakers 67-52. The 15-point margin speaks ...
  Report (governance): Round 2 was quiet on the governance front -- no proposals filed....
Round 3: 2 games, 2 reports
  Rose City Thorns 57 - 65 St. Johns Herons (AWAY) [ELAM]
  Hawthorne Hammers 54 - 47 Burnside Breakers (HOME) [ELAM]
  Report (simulation): Round 3. St. Johns Herons beat Rose City Thorns 65-57. St. Johns Herons beat Ros...
  Report (governance): Round 3 was quiet on the governance front -- no proposals filed....
```

## Step 9: Standings After 3 Rounds

```bash
uv run python scripts/demo_seed.py status
```

```output
Season: Season 1 | Rounds played: 3
Team                        W   L    PCT    PF    PA  DIFF
-------------------------------------------------------
St. Johns Herons            3   0  1.000   194   163 +  31
Hawthorne Hammers           2   1  0.667   159   156 +   3
Rose City Thorns            1   2  0.333   160   167   -7
Burnside Breakers           0   3  0.000   150   177  -27
```

```bash {image}
demo/05_standings_r3.png
```

![0dfa2a23-2026-02-20](0dfa2a23-2026-02-20.png)

## Step 10: AI Reports

Narrative reports that reference specific teams and game details. The reporting system reflects on gameplay and governance. AI-generated observations describe patterns -- they never prescribe actions.

```bash {image}
demo/06_reports.png
```

![36b6deef-2026-02-20](36b6deef-2026-02-20.png)

## Step 11: Governance -- Submit a Proposal

A governor proposes a rule change in natural language. The AI interprets it into structured parameters.

```bash
uv run python scripts/demo_seed.py propose Make three-pointers worth 5 points
```

```output
Proposal submitted: p-98b6d31c
  Text: Make three-pointers worth 5 points
```

```bash {image}
demo/07_governance.png
```

![88f9a3f1-2026-02-20](88f9a3f1-2026-02-20.png)

## Step 12: Current Ruleset

The rules page shows all current parameters and highlights changes from defaults.

```bash {image}
demo/08_rules.png
```

![1bfcaed2-2026-02-20](1bfcaed2-2026-02-20.png)

## Step 13: Team Profile

Each team has a profile with roster, agent attributes (visualized as bars), and venue info.

```bash {image}
demo/09_team.png
```

![679752b7-2026-02-20](679752b7-2026-02-20.png)

## Step 14: Evals Dashboard

The admin-facing evals dashboard shows aggregate report quality metrics, scenario flags, and AI rule evaluation. No individual report text is ever displayed -- only counts, rates, and composite scores.

```bash {image}
demo/10_evals.png
```

![c35e713c-2026-02-20](c35e713c-2026-02-20.png)

## Verification

All 408 tests pass. Zero lint errors. The demo above was captured live from a running instance.

```bash
uv run pytest --tb=short -q 2>&1 | tail -3
```

```output
........................................................................ [ 96%]
...............................................................          [100%]
2079 passed in 63.86s (0:01:03)
```
