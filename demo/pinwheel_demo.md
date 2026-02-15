# Pinwheel Fates -- Full Cycle Demo

*2026-02-15T23:23:44Z by Showboat 0.5.0*

**Pinwheel Fates** is a simulated 3v3 basketball league with human-driven, AI-interpreted governance and rules. Starts out as basketball, finishes as ???. The AI serves as a reporter -- surfacing patterns in gameplay and governance that players cannot see from inside the system.

This document proves the full **Govern > Simulate > Observe > Reflect** cycle works end-to-end. Every command below was executed live; every screenshot was captured from the running application.

## Step 1: Seed the League

Create 4 Portland-themed teams with 3 agents each and generate a round-robin schedule.

```bash
uv run python scripts/demo_seed.py seed
```

```output
League seeded: 4 teams, 18 scheduled games
Season ID: f5a28c4a-118f-4d6b-af29-0f655d261cd4
  Rose City Thorns: ca166a27-c739-4b8c-99f6-8a1ea6c0bc4b
  Burnside Breakers: 2f01c7e7-b42f-43f7-9ef9-b05285d62fdb
  St. Johns Herons: 092b1f4d-fd92-4755-bba1-50146fad6480
  Hawthorne Hammers: 536e0f0e-d995-41e5-bfca-783780be3e4b
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
