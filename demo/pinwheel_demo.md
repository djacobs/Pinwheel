# Pinwheel Fates -- Full Cycle Demo

*2026-02-15T18:08:43Z by Showboat 0.5.0*

**Pinwheel Fates** is a simulated 3v3 basketball league with human-driven, AI-interpreted governance and rules. Starts out as basketball, finishes as ???. The AI serves as a reporter -- surfacing patterns in gameplay and governance that players cannot see from inside the system.

This document proves the full **Govern > Simulate > Observe > Reflect** cycle works end-to-end. Every command below was executed live; every screenshot was captured from the running application.

## Step 1: Seed the League

Create 4 Portland-themed teams with 3 agents each and generate a round-robin schedule.

```bash
uv run python scripts/demo_seed.py seed
```

```output
League seeded: 4 teams, 18 scheduled games
Season ID: 4ab5c6ee-dd7b-407c-b749-0bee9a220502
  Rose City Thorns: f95b7bc7-6232-453b-be20-a314bc977a6a
  Burnside Breakers: 8f1758da-5f6c-4190-bdb9-ca9535692a5e
  St. Johns Herons: 9c86a156-df5e-4c61-b9ba-edcfd933469d
  Hawthorne Hammers: a7c4855a-b08e-43c7-b905-915c3f79ac0d
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
