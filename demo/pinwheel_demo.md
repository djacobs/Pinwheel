# Pinwheel Fates -- Full Cycle Demo

*2026-06-12T21:23:00Z by Showboat 0.6.1*
<!-- showboat-id: 1d69ff26-d754-4876-875b-cfe2f3ae759e -->

**Pinwheel Fates** is a simulated 3v3 basketball league with human-driven, AI-interpreted governance and rules. Starts out as basketball, finishes as ???. The AI serves as a reporter -- surfacing patterns in gameplay and governance that players cannot see from inside the system.

This document proves the full **Govern > Simulate > Observe > Reflect** cycle works end-to-end. Every command below was executed live; every screenshot was captured from the running application.

## Step 1: Seed the League

Create 4 Portland-themed teams with 3 agents each and generate a round-robin schedule.

```bash
uv run python scripts/demo_seed.py seed
```

```output
League seeded: 4 teams, 18 scheduled games
Season ID: b156e004-d711-4534-bd12-e8b66fe89df3
  Rose City Thorns: b2a710fd-df1a-40ec-9c3b-9c5287880bec
  Burnside Breakers: c85db237-ef23-481e-a4e4-cca98be906b3
  St. Johns Herons: 4a3046ce-ebb0-4210-b0fc-16e16db74850
  Hawthorne Hammers: fc5d405e-f1b7-403e-8142-4f93100def0e
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

![89159966-2026-06-12](89159966-2026-06-12.png)

## Step 4: Simulate Round 1

Run the first round of games. The simulation engine resolves every possession probabilistically based on agent attributes and the current ruleset.

```bash
uv run python scripts/demo_seed.py step 1
```

```output
Round 1: 2 games, 2 reports
  Rose City Thorns 56 - 25 Burnside Breakers (HOME) [ELAM]
  St. Johns Herons 70 - 38 Hawthorne Hammers (HOME) [ELAM]
  Report (simulation): St. Johns Herons demolished Hawthorne Hammers 70-38. The 32-point margin speaks ...
  Report (governance): Round 1 was quiet on the governance front -- no proposals filed....
```

## Step 5: The Arena

Game results appear in the Arena across multiple rounds with vivid Elam banner narration and per-round simulation reports. Each game panel shows the final score, possession count, and Elam Ending status.

```bash {image}
demo/02_arena.png
```

![6579d183-2026-06-12](6579d183-2026-06-12.png)

## Step 6: Standings

The league table updates after each round. Win/Loss, Points For/Against, Differential.

```bash {image}
demo/03_standings.png
```

![0ba88151-2026-06-12](0ba88151-2026-06-12.png)

## Step 7: Game Detail

Click into a game for box scores and rich narrated play-by-play with player names and defenders. Every possession is recorded.

```bash {image}
demo/04_game_detail.png
```

![1937469d-2026-06-12](1937469d-2026-06-12.png)

## Step 8: Advance the Season

Run 2 more rounds to build up standings and report data.

```bash
uv run python scripts/demo_seed.py step 2
```

```output
Round 2: 2 games, 2 reports
  Rose City Thorns 57 - 25 Hawthorne Hammers (HOME) [ELAM]
  Burnside Breakers 64 - 15 St. Johns Herons (HOME) [ELAM]
  Report (simulation): Burnside Breakers demolished St. Johns Herons 64-15. The 49-point margin speaks ...
  Report (governance): Round 2 was quiet on the governance front -- no proposals filed....
Round 3: 2 games, 2 reports
  Rose City Thorns 71 - 21 St. Johns Herons (HOME) [ELAM]
  Hawthorne Hammers 70 - 15 Burnside Breakers (HOME) [ELAM]
  Report (simulation): Hawthorne Hammers demolished Burnside Breakers 70-15. The 55-point margin speaks...
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
Rose City Thorns            3   0  1.000   184    71 + 113
Hawthorne Hammers           1   2  0.333   133   142   -9
Burnside Breakers           1   2  0.333   104   141  -37
St. Johns Herons            1   2  0.333   106   173  -67
```

```bash {image}
demo/05_standings_r3.png
```

![5569f336-2026-06-12](5569f336-2026-06-12.png)

## Step 10: AI Reports

Narrative reports that reference specific teams and game details. The reporting system reflects on gameplay and governance. AI-generated observations describe patterns -- they never prescribe actions.

```bash {image}
demo/06_reports.png
```

![5b8b393c-2026-06-12](5b8b393c-2026-06-12.png)

## Step 11: Governance -- Submit a Proposal

A governor proposes a rule change in natural language. The AI interprets it into structured parameters.

```bash
uv run python scripts/demo_seed.py propose Make three-pointers worth 5 points
```

```output
Proposal submitted: p-b0cd5330
  Text: Make three-pointers worth 5 points
```

```bash {image}
demo/07_governance.png
```

![93d56485-2026-06-12](93d56485-2026-06-12.png)

## Step 12: Current Ruleset

The rules page shows all current parameters and highlights changes from defaults.

```bash {image}
demo/08_rules.png
```

![aaa6fe31-2026-06-12](aaa6fe31-2026-06-12.png)

## Step 13: Team Profile

Each team has a profile with roster, agent attributes (visualized as bars), and venue info.

```bash {image}
demo/09_team.png
```

![be249440-2026-06-12](be249440-2026-06-12.png)

## Step 14: Evals Dashboard

The admin-facing evals dashboard shows aggregate report quality metrics, scenario flags, and AI rule evaluation. No individual report text is ever displayed -- only counts, rates, and composite scores.

```bash {image}
demo/10_evals.png
```

![4dda8dff-2026-06-12](4dda8dff-2026-06-12.png)

## Verification

All 408 tests pass. Zero lint errors. The demo above was captured live from a running instance.

```bash
uv run pytest --tb=short -q 2>&1 | tail -3
```

```output

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
2657 passed, 190 warnings in 84.90s (0:01:24)
```
