# Pinwheel Fates -- Full Cycle Demo

*2026-02-14T20:20:20Z by Showboat 0.5.0*

**Pinwheel Fates** is a simulated 3v3 basketball league with human-driven, AI-interpreted governance and rules. Starts out as basketball, finishes as ???. The AI serves as a reporter -- surfacing patterns in gameplay and governance that players cannot see from inside the system.

This document proves the full **Govern > Simulate > Observe > Reflect** cycle works end-to-end. Every command below was executed live; every screenshot was captured from the running application.

## Step 1: Seed the League

Create 4 Portland-themed teams with 3 agents each and generate a round-robin schedule.

```bash
uv run python scripts/demo_seed.py seed
```

```output
League seeded: 4 teams, 6 scheduled games
Season ID: f11f7034-71d8-4dab-b5b0-f2f694ce63c2
  Rose City Thorns: da801dfc-b4a5-49f0-9124-1055f5e6a34e
  Burnside Breakers: fe3cf682-0273-434c-badb-05b18d1f7855
  St. Johns Herons: 106ad322-6b41-419c-8a1b-40c34e93cb1f
  Hawthorne Hammers: e9c024ad-a57e-4c59-8e28-7b4cddb13a97
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
