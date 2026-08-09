# Pinwheel Fates -- Full Cycle Demo

*2026-08-09T03:48:45Z by Showboat 0.6.1*
<!-- showboat-id: 13c90b65-8f85-4cb7-9500-7894fe5a2937 -->

**Pinwheel Fates** is a simulated 3v3 basketball league with human-driven, AI-interpreted governance and rules. Starts out as basketball, finishes as ???. The AI serves as a reporter -- surfacing patterns in gameplay and governance that players cannot see from inside the system.

This document proves the full **Govern > Simulate > Observe > Reflect** cycle works end-to-end. Every command below was executed live; every screenshot was captured from the running application.

## Step 1: Seed the League

Create 4 Portland-themed teams with 3 agents each and generate a round-robin schedule.

```bash
uv run python scripts/demo_seed.py seed
```

```output
League seeded: 4 teams, 18 scheduled games
Season ID: 1ab8dc97-4fc6-4675-aa6e-65dcbd0d743b
  Rose City Thorns: d663ec97-c07a-49a6-81ea-c8a01b7cbab0
  Burnside Breakers: 9c7968fe-a961-43e5-aeb7-94c9b8e76cab
  St. Johns Herons: 91cfd1fd-2879-4ca7-967f-36f1bfe32146
  Hawthorne Hammers: 29e29139-ca6f-4224-b907-1445b73a7535
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

![6d3d9bc4-2026-08-09](6d3d9bc4-2026-08-09.png)

## Step 4: Simulate Round 1

Run the first round of games. The simulation engine resolves every possession probabilistically based on agent attributes and the current ruleset.

```bash
uv run python scripts/demo_seed.py step 1
```

```output
Round 1: 2 games, 2 reports
  Rose City Thorns 74 - 38 Burnside Breakers (HOME) [ELAM]
  St. Johns Herons 74 - 22 Hawthorne Hammers (HOME) [ELAM]
  Report (simulation): St. Johns Herons demolished Hawthorne Hammers 74-22. The 52-point margin speaks ...
  Report (governance): Round 1 was quiet on the governance front -- no proposals filed....
```

## Step 5: The Arena

Game results appear in the Arena across multiple rounds with vivid Elam banner narration and per-round simulation reports. Each game panel shows the final score, possession count, and Elam Ending status.

```bash {image}
demo/02_arena.png
```

![f2e1c832-2026-08-09](f2e1c832-2026-08-09.png)

## Step 6: Standings

The league table updates after each round. Win/Loss, Points For/Against, Differential.

```bash {image}
demo/03_standings.png
```

![d6ffc4cf-2026-08-09](d6ffc4cf-2026-08-09.png)

## Step 7: Game Detail

Click into a game for box scores and rich narrated play-by-play with player names and defenders. Every possession is recorded.

```bash {image}
demo/04_game_detail.png
```

![1e165136-2026-08-09](1e165136-2026-08-09.png)

## Step 8: Advance the Season

Run 2 more rounds to build up standings and report data.

```bash
uv run python scripts/demo_seed.py step 2
```

```output
Round 2: 2 games, 2 reports
  Rose City Thorns 90 - 39 Hawthorne Hammers (HOME) [ELAM]
  Burnside Breakers 79 - 28 St. Johns Herons (HOME) [ELAM]
  Report (simulation): Rose City Thorns demolished Hawthorne Hammers 90-39. The 51-point margin speaks ...
  Report (governance): Round 2 was quiet on the governance front -- no proposals filed....
Round 3: 2 games, 2 reports
  Rose City Thorns 76 - 20 St. Johns Herons (HOME) [ELAM]
  Hawthorne Hammers 66 - 45 Burnside Breakers (HOME) [ELAM]
  Report (simulation): Hawthorne Hammers shocked Burnside Breakers 66-45. The standings didn't predict ...
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
Rose City Thorns            3   0  1.000   240    97 + 143
Burnside Breakers           1   2  0.333   162   168   -6
St. Johns Herons            1   2  0.333   122   177  -55
Hawthorne Hammers           1   2  0.333   127   209  -82
```

```bash {image}
demo/05_standings_r3.png
```

![81f12c33-2026-08-09](81f12c33-2026-08-09.png)

## Step 10: AI Reports

Narrative reports that reference specific teams and game details. The reporting system reflects on gameplay and governance. AI-generated observations describe patterns -- they never prescribe actions.

```bash {image}
demo/06_reports.png
```

![94d19783-2026-08-09](94d19783-2026-08-09.png)

## Step 11: Governance -- Submit a Proposal

A governor proposes a rule change in natural language. The AI interprets it into structured parameters.

```bash
uv run python scripts/demo_seed.py propose Make three-pointers worth 5 points
```

```output
Proposal submitted: p-78043058
  Text: Make three-pointers worth 5 points
```

```bash {image}
demo/07_governance.png
```

![a0ffec49-2026-08-09](a0ffec49-2026-08-09.png)

## Step 12: Current Ruleset

The rules page shows all current parameters and highlights changes from defaults.

```bash {image}
demo/08_rules.png
```

![6b269d4d-2026-08-09](6b269d4d-2026-08-09.png)

## Step 13: Team Profile

Each team has a profile with roster, agent attributes (visualized as bars), and venue info.

```bash {image}
demo/09_team.png
```

![ba6a9bb0-2026-08-09](ba6a9bb0-2026-08-09.png)

## Step 14: Evals Dashboard

The admin-facing evals dashboard shows aggregate report quality metrics, scenario flags, and AI rule evaluation. No individual report text is ever displayed -- only counts, rates, and composite scores.

```bash {image}
demo/10_evals.png
```

![36a5f2cd-2026-08-09](36a5f2cd-2026-08-09.png)

## Verification

All 408 tests pass. Zero lint errors. The demo above was captured live from a running instance.

```bash
uv run pytest --tb=short -q 2>&1 | tail -3
```

```output

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
2977 passed, 190 warnings in 204.17s (0:03:24)
```
