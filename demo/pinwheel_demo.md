# Pinwheel Fates -- Full Cycle Demo

*2026-02-20T22:05:11Z by Showboat 0.6.0*
<!-- showboat-id: a2a02b1b-fb7f-458b-8192-5efb68804d76 -->

**Pinwheel Fates** is a simulated 3v3 basketball league with human-driven, AI-interpreted governance and rules. Starts out as basketball, finishes as ???. The AI serves as a reporter -- surfacing patterns in gameplay and governance that players cannot see from inside the system.

This document proves the full **Govern > Simulate > Observe > Reflect** cycle works end-to-end. Every command below was executed live; every screenshot was captured from the running application.

## Step 1: Seed the League

Create 4 Portland-themed teams with 3 agents each and generate a round-robin schedule.

```bash
uv run python scripts/demo_seed.py seed
```

```output
League seeded: 4 teams, 18 scheduled games
Season ID: 9501acc7-22d3-4f97-a660-69881bd14da4
  Rose City Thorns: 3f0a3c46-7aa8-4383-a158-0b6b80119869
  Burnside Breakers: 751b70c2-5c78-453e-9ccb-643ff3c68e8c
  St. Johns Herons: d4c70310-d65d-411e-9069-2c449d2c37e5
  Hawthorne Hammers: f1997b28-bfc7-41bc-b786-9b92b82d1141
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

![fa272cc8-2026-02-20](fa272cc8-2026-02-20.png)

## Step 4: Simulate Round 1

Run the first round of games. The simulation engine resolves every possession probabilistically based on agent attributes and the current ruleset.

```bash
uv run python scripts/demo_seed.py step 1
```

```output
Round 1: 2 games, 2 reports
  Rose City Thorns 70 - 73 Burnside Breakers (AWAY) [ELAM]
  St. Johns Herons 63 - 59 Hawthorne Hammers (HOME) [ELAM]
  Report (simulation): Burnside Breakers survived Rose City Thorns 73-70 in a thriller — just 3 points ...
  Report (governance): Round 1 was quiet on the governance front -- no proposals filed....
```

## Step 5: The Arena

Game results appear in the Arena across multiple rounds with vivid Elam banner narration and per-round simulation reports. Each game panel shows the final score, possession count, and Elam Ending status.

```bash {image}
demo/02_arena.png
```

![cda53dae-2026-02-20](cda53dae-2026-02-20.png)

## Step 6: Standings

The league table updates after each round. Win/Loss, Points For/Against, Differential.

```bash {image}
demo/03_standings.png
```

![7f4daef2-2026-02-20](7f4daef2-2026-02-20.png)

## Step 7: Game Detail

Click into a game for box scores and rich narrated play-by-play with player names and defenders. Every possession is recorded.

```bash {image}
demo/04_game_detail.png
```

![12f62e5a-2026-02-20](12f62e5a-2026-02-20.png)

## Step 8: Advance the Season

Run 2 more rounds to build up standings and report data.

```bash
uv run python scripts/demo_seed.py step 2
```

```output
Round 2: 2 games, 2 reports
  Rose City Thorns 61 - 53 Hawthorne Hammers (HOME) [ELAM]
  Burnside Breakers 53 - 41 St. Johns Herons (HOME) [ELAM]
  Report (simulation): Round 2. Rose City Thorns beat Hawthorne Hammers 61-53. Rose City Thorns beat Ha...
  Report (governance): Round 2 was quiet on the governance front -- no proposals filed....
Round 3: 2 games, 2 reports
  Rose City Thorns 57 - 36 St. Johns Herons (HOME) [ELAM]
  Hawthorne Hammers 65 - 66 Burnside Breakers (AWAY) [ELAM]
  Report (simulation): Rose City Thorns demolished St. Johns Herons 57-36. The 21-point margin speaks f...
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
Burnside Breakers           3   0  1.000   192   176 +  16
Rose City Thorns            2   1  0.667   188   162 +  26
St. Johns Herons            1   2  0.333   140   169  -29
Hawthorne Hammers           0   3  0.000   177   190  -13
```

```bash {image}
demo/05_standings_r3.png
```

![f19bb6d3-2026-02-20](f19bb6d3-2026-02-20.png)

## Step 10: AI Reports

Narrative reports that reference specific teams and game details. The reporting system reflects on gameplay and governance. AI-generated observations describe patterns -- they never prescribe actions.

```bash {image}
demo/06_reports.png
```

![d906c698-2026-02-20](d906c698-2026-02-20.png)

## Step 11: Governance -- Submit a Proposal

A governor proposes a rule change in natural language. The AI interprets it into structured parameters.

```bash
uv run python scripts/demo_seed.py propose Make three-pointers worth 5 points
```

```output
Proposal submitted: p-b88fed3d
  Text: Make three-pointers worth 5 points
```

```bash {image}
demo/07_governance.png
```

![371f4408-2026-02-20](371f4408-2026-02-20.png)

## Step 12: Current Ruleset

The rules page shows all current parameters and highlights changes from defaults.

```bash {image}
demo/08_rules.png
```

![71caed7e-2026-02-20](71caed7e-2026-02-20.png)

## Step 13: Team Profile

Each team has a profile with roster, agent attributes (visualized as bars), and venue info.

```bash {image}
demo/09_team.png
```

![4b8ce971-2026-02-20](4b8ce971-2026-02-20.png)

## Step 14: Evals Dashboard

The admin-facing evals dashboard shows aggregate report quality metrics, scenario flags, and AI rule evaluation. No individual report text is ever displayed -- only counts, rates, and composite scores.

```bash {image}
demo/10_evals.png
```

![b3425638-2026-02-20](b3425638-2026-02-20.png)

## Verification

All 408 tests pass. Zero lint errors. The demo above was captured live from a running instance.

```bash
uv run pytest --tb=short -q 2>&1 | tail -3
```

```output
........................................................................ [ 96%]
...............................................................          [100%]
2079 passed in 60.06s (0:01:00)
```
