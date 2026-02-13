# Pinwheel Fates -- Full Cycle Demo

*2026-02-13T04:33:41Z*

**Pinwheel Fates** is a simulated 3v3 basketball league with human-driven, AI-interpreted governance and rules. Starts out as basketball, finishes as ???. The AI serves as a social mirror -- surfacing patterns in gameplay and governance that players cannot see from inside the system.

This document proves the full **Govern > Simulate > Observe > Reflect** cycle works end-to-end. Every command below was executed live; every screenshot was captured from the running application.

## Step 1: Seed the League

Create 4 Portland-themed teams with 3 agents each and generate a round-robin schedule.

```bash
uv run python scripts/demo_seed.py seed
```

```output
League seeded: 4 teams, 6 scheduled games
Season ID: 894a1296-6800-40a2-9734-b95542aa694d
  Rose City Thorns: 43abbbed-de1d-47d6-92db-32a6f4c53148
  Burnside Breakers: 39919919-e790-43d4-83c0-2902c799ed55
  St. Johns Herons: 8d6f15f4-ed50-4bda-a924-637310a2bdc9
  Hawthorne Hammers: 25ed275a-96f8-4414-8043-11b8359bbb51
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

![e15b9a52-2026-02-13](e15b9a52-2026-02-13.png)

## Step 4: Simulate Round 1

Run the first round of games. The simulation engine resolves every possession probabilistically based on agent attributes and the current ruleset.

```bash
uv run python scripts/demo_seed.py step 1
```

```output
Round 1: 2 games, 2 mirrors
  Rose City Thorns 68 - 54 Burnside Breakers (HOME) [ELAM]
  St. Johns Herons 51 - 53 Hawthorne Hammers (AWAY) [ELAM]
  Mirror (simulation): Hawthorne Hammers edged St. Johns Herons 53-51. Neither team blinked until the E...
  Mirror (governance): Round 1 was quiet on the governance front — no proposals filed....
```

## Step 5: The Arena

Game results appear in the Arena across multiple rounds with vivid Elam banner narration and per-round simulation mirrors. Each game panel shows the final score, possession count, and Elam Ending status.

```bash {image}
uvx rodney screenshot demo/02_arena.png -w 1280 -h 1400
```

![8a93a95b-2026-02-13](8a93a95b-2026-02-13.png)

## Step 6: Standings

The league table updates after each round. Win/Loss, Points For/Against, Differential.

```bash {image}
uvx rodney screenshot demo/03_standings.png -w 1280 -h 900
```

![e26f3ae3-2026-02-13](e26f3ae3-2026-02-13.png)

## Step 7: Game Detail

Click into a game for box scores and rich narrated play-by-play with player names and defenders. Every possession is recorded.

```bash {image}
uvx rodney screenshot demo/04_game_detail.png -w 1280 -h 1200
```

![5a1c2a6d-2026-02-13](5a1c2a6d-2026-02-13.png)

## Step 8: Advance the Season

Run 2 more rounds to build up standings and mirror data.

```bash
uv run python scripts/demo_seed.py step 2
```

```output
Round 2: 2 games, 2 mirrors
  Rose City Thorns 44 - 68 Hawthorne Hammers (AWAY) [ELAM]
  Burnside Breakers 64 - 42 St. Johns Herons (HOME) [ELAM]
  Mirror (simulation): Hawthorne Hammers dismantled Rose City Thorns by 24. It wasn't close after the f...
  Mirror (governance): Round 2 was quiet on the governance front — no proposals filed....
Round 3: 2 games, 2 mirrors
  Rose City Thorns 62 - 64 St. Johns Herons (AWAY) [ELAM]
  Hawthorne Hammers 55 - 60 Burnside Breakers (AWAY) [ELAM]
  Mirror (simulation): St. Johns Herons survived Rose City Thorns by 2 — a 64-62 grinder that went down...
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
Hawthorne Hammers           2   1  0.667   176   155 +  21
Burnside Breakers           2   1  0.667   178   165 +  13
Rose City Thorns            1   2  0.333   174   186  -12
St. Johns Herons            1   2  0.333   157   179  -22
```

```bash {image}
uvx rodney screenshot demo/05_standings_r3.png -w 1280 -h 900
```

![981e28b3-2026-02-13](981e28b3-2026-02-13.png)

## Step 10: AI Mirrors

Narrative mirrors that reference specific teams and game details. The mirror system reflects on gameplay and governance. AI-generated observations describe patterns -- they never prescribe actions.

```bash {image}
uvx rodney screenshot demo/06_mirrors.png -w 1280 -h 1200
```

![ccb474c7-2026-02-13](ccb474c7-2026-02-13.png)

## Step 11: Governance -- Submit a Proposal

A governor proposes a rule change in natural language. The AI interprets it into structured parameters.

```bash
uv run python scripts/demo_seed.py propose Make three-pointers worth 5 points
```

```output
Proposal submitted: p-d850b4eb
  Text: Make three-pointers worth 5 points
```

```bash {image}
uvx rodney screenshot demo/07_governance.png -w 1280 -h 900
```

![54d5b76c-2026-02-13](54d5b76c-2026-02-13.png)

## Step 12: Current Ruleset

The rules page shows all current parameters and highlights changes from defaults.

```bash {image}
uvx rodney screenshot demo/08_rules.png -w 1280 -h 900
```

![8fad543c-2026-02-13](8fad543c-2026-02-13.png)

## Step 13: Team Profile

Each team has a profile with roster, agent attributes (visualized as bars), and venue info.

```bash {image}
uvx rodney screenshot demo/09_team.png -w 1280 -h 1200
```

![d9c7cbfa-2026-02-13](d9c7cbfa-2026-02-13.png)

## Step 14: Evals Dashboard

The admin-facing evals dashboard shows aggregate mirror quality metrics, scenario flags, and AI rule evaluation. No individual mirror text is ever displayed -- only counts, rates, and composite scores.
