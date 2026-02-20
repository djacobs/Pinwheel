# Pinwheel Fates -- Full Cycle Demo

*2026-02-20T22:34:25Z by Showboat 0.6.0*
<!-- showboat-id: 907be32f-bc8e-4272-ad7f-22288a6b17f3 -->

**Pinwheel Fates** is a simulated 3v3 basketball league with human-driven, AI-interpreted governance and rules. Starts out as basketball, finishes as ???. The AI serves as a reporter -- surfacing patterns in gameplay and governance that players cannot see from inside the system.

This document proves the full **Govern > Simulate > Observe > Reflect** cycle works end-to-end. Every command below was executed live; every screenshot was captured from the running application.

## Step 1: Seed the League

Create 4 Portland-themed teams with 3 agents each and generate a round-robin schedule.

```bash
uv run python scripts/demo_seed.py seed
```

```output
League seeded: 4 teams, 18 scheduled games
Season ID: 51586e9b-89db-4ce2-9045-06fc542d009e
  Rose City Thorns: 41776746-0e48-4d4c-b210-2ef56ada8cd2
  Burnside Breakers: ca59dec0-2a02-4645-b115-f0de74815157
  St. Johns Herons: 3a1016eb-e261-465e-b65d-a09abe1a1059
  Hawthorne Hammers: d4cbc134-449c-49cc-b65a-239f30484a86
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

![fd8432f4-2026-02-20](fd8432f4-2026-02-20.png)

## Step 4: Simulate Round 1

Run the first round of games. The simulation engine resolves every possession probabilistically based on agent attributes and the current ruleset.

```bash
uv run python scripts/demo_seed.py step 1
```

```output
Round 1: 2 games, 2 reports
  Rose City Thorns 52 - 50 Burnside Breakers (HOME) [ELAM]
  St. Johns Herons 48 - 70 Hawthorne Hammers (AWAY) [ELAM]
  Report (simulation): Hawthorne Hammers demolished St. Johns Herons 70-48. The 22-point margin speaks ...
  Report (governance): Round 1 was quiet on the governance front -- no proposals filed....
```

## Step 5: The Arena

Game results appear in the Arena across multiple rounds with vivid Elam banner narration and per-round simulation reports. Each game panel shows the final score, possession count, and Elam Ending status.

```bash {image}
demo/02_arena.png
```

![b6c5f98e-2026-02-20](b6c5f98e-2026-02-20.png)

## Step 6: Standings

The league table updates after each round. Win/Loss, Points For/Against, Differential.

```bash {image}
demo/03_standings.png
```

![77ec83fd-2026-02-20](77ec83fd-2026-02-20.png)

## Step 7: Game Detail

Click into a game for box scores and rich narrated play-by-play with player names and defenders. Every possession is recorded.

```bash {image}
demo/04_game_detail.png
```

![5ee82650-2026-02-20](5ee82650-2026-02-20.png)

## Step 8: Advance the Season

Run 2 more rounds to build up standings and report data.

```bash
uv run python scripts/demo_seed.py step 2
```

```output
Round 2: 2 games, 2 reports
  Rose City Thorns 57 - 37 Hawthorne Hammers (HOME) [ELAM]
  Burnside Breakers 74 - 51 St. Johns Herons (HOME) [ELAM]
  Report (simulation): Burnside Breakers demolished St. Johns Herons 74-51. The 23-point margin speaks ...
  Report (governance): Round 2 was quiet on the governance front -- no proposals filed....
Round 3: 2 games, 2 reports
  Rose City Thorns 43 - 62 St. Johns Herons (AWAY) [ELAM]
  Hawthorne Hammers 47 - 64 Burnside Breakers (AWAY) [ELAM]
  Report (simulation): St. Johns Herons shocked Rose City Thorns 62-43. The standings didn't predict th...
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
Burnside Breakers           2   1  0.667   188   150 +  38
Rose City Thorns            2   1  0.667   152   149 +   3
Hawthorne Hammers           1   2  0.333   154   169  -15
St. Johns Herons            1   2  0.333   161   187  -26
```

```bash {image}
demo/05_standings_r3.png
```

![e7036d74-2026-02-20](e7036d74-2026-02-20.png)

## Step 10: AI Reports

Narrative reports that reference specific teams and game details. The reporting system reflects on gameplay and governance. AI-generated observations describe patterns -- they never prescribe actions.

```bash {image}
demo/06_reports.png
```

![3544b6eb-2026-02-20](3544b6eb-2026-02-20.png)

## Step 11: Governance -- Submit a Proposal

A governor proposes a rule change in natural language. The AI interprets it into structured parameters.

```bash
uv run python scripts/demo_seed.py propose Make three-pointers worth 5 points
```

```output
Proposal submitted: p-b5bba97a
  Text: Make three-pointers worth 5 points
```

```bash {image}
demo/07_governance.png
```

![c5befe90-2026-02-20](c5befe90-2026-02-20.png)

## Step 12: Current Ruleset

The rules page shows all current parameters and highlights changes from defaults.

```bash {image}
demo/08_rules.png
```

![fc3fb9e3-2026-02-20](fc3fb9e3-2026-02-20.png)

## Step 13: Team Profile

Each team has a profile with roster, agent attributes (visualized as bars), and venue info.

```bash {image}
demo/09_team.png
```

![c69b4189-2026-02-20](c69b4189-2026-02-20.png)

## Step 14: Evals Dashboard

The admin-facing evals dashboard shows aggregate report quality metrics, scenario flags, and AI rule evaluation. No individual report text is ever displayed -- only counts, rates, and composite scores.

```bash {image}
demo/10_evals.png
```

![4d6d6b9c-2026-02-20](4d6d6b9c-2026-02-20.png)

## Verification

All 408 tests pass. Zero lint errors. The demo above was captured live from a running instance.

```bash
uv run pytest --tb=short -q 2>&1 | tail -3
```

```output
........................................................................ [ 96%]
...............................................................          [100%]
2079 passed in 64.42s (0:01:04)
```
