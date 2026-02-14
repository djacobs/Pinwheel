# Pinwheel Fates -- Full Cycle Demo

*2026-02-14T18:25:26Z*

**Pinwheel Fates** is a simulated 3v3 basketball league with human-driven, AI-interpreted governance and rules. Starts out as basketball, finishes as ???. The AI serves as a reporter -- surfacing patterns in gameplay and governance that players cannot see from inside the system.

This document proves the full **Govern > Simulate > Observe > Reflect** cycle works end-to-end. Every command below was executed live; every screenshot was captured from the running application.

## Step 1: Seed the League

Create 4 Portland-themed teams with 3 agents each and generate a round-robin schedule.

```bash
uv run python scripts/demo_seed.py seed
```

```output
League seeded: 4 teams, 6 scheduled games
Season ID: 1acbe051-0272-47a4-a792-39c93e876618
  Rose City Thorns: 48b7524d-ff65-45a4-a6ec-dd5abe7042cb
  Burnside Breakers: bec0f2e5-4d1a-44d7-b012-91ee3615c44d
  St. Johns Herons: a6c20760-259d-4b4a-9483-c49b13c4ff43
  Hawthorne Hammers: 988e5ccc-5a54-4d12-8173-2987e65e64e1
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

![b189781a-2026-02-14](b189781a-2026-02-14.png)

## Step 4: Simulate Round 1

Run the first round of games. The simulation engine resolves every possession probabilistically based on agent attributes and the current ruleset.

```bash
uv run python scripts/demo_seed.py step 1
```

```output
Round 1: 2 games, 2 reports
  Rose City Thorns 46 - 55 Burnside Breakers (AWAY) [ELAM]
  St. Johns Herons 57 - 74 Hawthorne Hammers (AWAY) [ELAM]
  Report (simulation): A 17-point demolition: Hawthorne Hammers 74, St. Johns Herons 57. The Elam targe...
  Report (governance): Round 1 was quiet on the governance front — no proposals filed....
```

## Step 5: The Arena

Game results appear in the Arena across multiple rounds with vivid Elam banner narration and per-round simulation reports. Each game panel shows the final score, possession count, and Elam Ending status.

```bash {image}
uvx rodney screenshot demo/02_arena.png -w 1280 -h 1400
```

![3818633a-2026-02-14](3818633a-2026-02-14.png)

## Step 6: Standings

The league table updates after each round. Win/Loss, Points For/Against, Differential.

```bash {image}
uvx rodney screenshot demo/03_standings.png -w 1280 -h 900
```

![d9ba85f6-2026-02-14](d9ba85f6-2026-02-14.png)

## Step 7: Game Detail

Click into a game for box scores and rich narrated play-by-play with player names and defenders. Every possession is recorded.

```bash {image}
uvx rodney screenshot demo/04_game_detail.png -w 1280 -h 1200
```

![48ce3565-2026-02-14](48ce3565-2026-02-14.png)

## Step 8: Advance the Season

Run 2 more rounds to build up standings and report data.

```bash
uv run python scripts/demo_seed.py step 2
```

```output
Round 2: 2 games, 2 reports
  Rose City Thorns 50 - 39 Hawthorne Hammers (HOME) [ELAM]
  Burnside Breakers 56 - 51 St. Johns Herons (HOME) [ELAM]
  Report (simulation): Rose City Thorns dismantled Hawthorne Hammers by 11. It wasn't close after the f...
  Report (governance): Round 2 was quiet on the governance front — no proposals filed....
Round 3: 2 games, 2 reports
  Rose City Thorns 55 - 48 St. Johns Herons (HOME) [ELAM]
  Hawthorne Hammers 64 - 81 Burnside Breakers (AWAY) [ELAM]
  Report (simulation): Burnside Breakers dismantled Hawthorne Hammers by 17. It wasn't close after the ...
  Report (governance): Round 3 was quiet on the governance front — no proposals filed....
```

## Step 9: Standings After 3 Rounds

```bash
uv run python scripts/demo_seed.py status
```

```output
Season: Season 1 | Rounds played: 3
Team                        W   L    PCT    PF    PA  DIFF
-------------------------------------------------------
Burnside Breakers           3   0  1.000   192   161 +  31
Rose City Thorns            2   1  0.667   151   142 +   9
Hawthorne Hammers           1   2  0.333   177   188  -11
St. Johns Herons            0   3  0.000   156   185  -29
```

```bash {image}
uvx rodney screenshot demo/05_standings_r3.png -w 1280 -h 900
```

![bc0cc5db-2026-02-14](bc0cc5db-2026-02-14.png)

## Step 10: AI Reports

Narrative reports that reference specific teams and game details. The reporting system reflects on gameplay and governance. AI-generated observations describe patterns -- they never prescribe actions.

```bash {image}
uvx rodney screenshot demo/06_reports.png -w 1280 -h 1200
```

![99816e3c-2026-02-14](99816e3c-2026-02-14.png)

## Step 11: Governance -- Submit a Proposal

A governor proposes a rule change in natural language. The AI interprets it into structured parameters.

```bash
uv run python scripts/demo_seed.py propose Make three-pointers worth 5 points
```

```output
Proposal submitted: p-4af5e273
  Text: Make three-pointers worth 5 points
```

```bash {image}
uvx rodney screenshot demo/07_governance.png -w 1280 -h 900
```

![8c9f89ad-2026-02-14](8c9f89ad-2026-02-14.png)

## Step 12: Current Ruleset

The rules page shows all current parameters and highlights changes from defaults.

```bash {image}
uvx rodney screenshot demo/08_rules.png -w 1280 -h 900
```

![bd2b5307-2026-02-14](bd2b5307-2026-02-14.png)

## Step 13: Team Profile

Each team has a profile with roster, agent attributes (visualized as bars), and venue info.

```bash {image}
uvx rodney screenshot demo/09_team.png -w 1280 -h 1200
```

![88653663-2026-02-14](88653663-2026-02-14.png)

## Step 14: Evals Dashboard

The admin-facing evals dashboard shows aggregate report quality metrics, scenario flags, and AI rule evaluation. No individual report text is ever displayed -- only counts, rates, and composite scores.
