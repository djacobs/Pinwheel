# Pinwheel Fates -- Full Cycle Demo

*2026-02-13T05:25:43Z*

**Pinwheel Fates** is a simulated 3v3 basketball league with human-driven, AI-interpreted governance and rules. Starts out as basketball, finishes as ???. The AI serves as a social mirror -- surfacing patterns in gameplay and governance that players cannot see from inside the system.

This document proves the full **Govern > Simulate > Observe > Reflect** cycle works end-to-end. Every command below was executed live; every screenshot was captured from the running application.

## Step 1: Seed the League

Create 4 Portland-themed teams with 3 agents each and generate a round-robin schedule.

```bash
uv run python scripts/demo_seed.py seed
```

```output
League seeded: 4 teams, 6 scheduled games
Season ID: 2a35aeda-41d8-4781-bad0-2723929d4309
  Rose City Thorns: 8f52a4b7-f440-4831-b251-ce1c4ff4c01c
  Burnside Breakers: fb1a994b-bb95-4d15-9e9e-797b1899a29b
  St. Johns Herons: 8f552875-656b-4938-8729-3d52ad3682f1
  Hawthorne Hammers: 71886adc-0768-4285-83dd-a426cec61fba
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

![9ff6837e-2026-02-13](9ff6837e-2026-02-13.png)

## Step 4: Simulate Round 1

Run the first round of games. The simulation engine resolves every possession probabilistically based on agent attributes and the current ruleset.

```bash
uv run python scripts/demo_seed.py step 1
```

```output
Round 1: 2 games, 2 mirrors
  Rose City Thorns 36 - 61 Burnside Breakers (AWAY) [ELAM]
  St. Johns Herons 49 - 40 Hawthorne Hammers (HOME) [ELAM]
  Mirror (simulation): A 25-point demolition: Burnside Breakers 61, Rose City Thorns 36. The Elam targe...
  Mirror (governance): Round 1 was quiet on the governance front — no proposals filed....
```

## Step 5: The Arena

Game results appear in the Arena across multiple rounds with vivid Elam banner narration and per-round simulation mirrors. Each game panel shows the final score, possession count, and Elam Ending status.

```bash {image}
uvx rodney screenshot demo/02_arena.png -w 1280 -h 1400
```

![c47f6b61-2026-02-13](c47f6b61-2026-02-13.png)

## Step 6: Standings

The league table updates after each round. Win/Loss, Points For/Against, Differential.

```bash {image}
uvx rodney screenshot demo/03_standings.png -w 1280 -h 900
```

![216f22e5-2026-02-13](216f22e5-2026-02-13.png)

## Step 7: Game Detail

Click into a game for box scores and rich narrated play-by-play with player names and defenders. Every possession is recorded.

```bash {image}
uvx rodney screenshot demo/04_game_detail.png -w 1280 -h 1200
```

![b8e9278f-2026-02-13](b8e9278f-2026-02-13.png)

## Step 8: Advance the Season

Run 2 more rounds to build up standings and mirror data.

```bash
uv run python scripts/demo_seed.py step 2
```

```output
Round 2: 2 games, 2 mirrors
  Rose City Thorns 55 - 59 Hawthorne Hammers (AWAY) [ELAM]
  Burnside Breakers 60 - 39 St. Johns Herons (HOME) [ELAM]
  Mirror (simulation): Hawthorne Hammers edged Rose City Thorns 59-55. Neither team blinked until the E...
  Mirror (governance): Round 2 was quiet on the governance front — no proposals filed....
Round 3: 2 games, 2 mirrors
  Rose City Thorns 67 - 50 St. Johns Herons (HOME) [ELAM]
  Hawthorne Hammers 52 - 76 Burnside Breakers (AWAY) [ELAM]
  Mirror (simulation): Rose City Thorns dismantled St. Johns Herons by 17. It wasn't close after the fi...
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
Burnside Breakers           3   0  1.000   197   127 +  70
Rose City Thorns            1   2  0.333   158   170  -12
St. Johns Herons            1   2  0.333   138   167  -29
Hawthorne Hammers           1   2  0.333   151   180  -29
```

```bash {image}
uvx rodney screenshot demo/05_standings_r3.png -w 1280 -h 900
```

![2ff938fc-2026-02-13](2ff938fc-2026-02-13.png)

## Step 10: AI Mirrors

Narrative mirrors that reference specific teams and game details. The mirror system reflects on gameplay and governance. AI-generated observations describe patterns -- they never prescribe actions.

```bash {image}
uvx rodney screenshot demo/06_mirrors.png -w 1280 -h 1200
```

![7f040df2-2026-02-13](7f040df2-2026-02-13.png)

## Step 11: Governance -- Submit a Proposal

A governor proposes a rule change in natural language. The AI interprets it into structured parameters.

```bash
uv run python scripts/demo_seed.py propose Make three-pointers worth 5 points
```

```output
Proposal submitted: p-797ec16c
  Text: Make three-pointers worth 5 points
```

```bash {image}
uvx rodney screenshot demo/07_governance.png -w 1280 -h 900
```

![9ef57f14-2026-02-13](9ef57f14-2026-02-13.png)

## Step 12: Current Ruleset

The rules page shows all current parameters and highlights changes from defaults.

```bash {image}
uvx rodney screenshot demo/08_rules.png -w 1280 -h 900
```

![51788bb6-2026-02-13](51788bb6-2026-02-13.png)

## Step 13: Team Profile

Each team has a profile with roster, agent attributes (visualized as bars), and venue info.

```bash {image}
uvx rodney screenshot demo/09_team.png -w 1280 -h 1200
```

![5eb1e939-2026-02-13](5eb1e939-2026-02-13.png)

## Step 14: Evals Dashboard

The admin-facing evals dashboard shows aggregate mirror quality metrics, scenario flags, and AI rule evaluation. No individual mirror text is ever displayed -- only counts, rates, and composite scores.
