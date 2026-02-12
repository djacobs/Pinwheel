"""Seed a Pinwheel league and run rounds for demo purposes.

Usage:
    python scripts/demo_seed.py seed          # Create league + teams + schedule
    python scripts/demo_seed.py step [N]      # Run N rounds (default 1)
    python scripts/demo_seed.py status        # Print current state
    python scripts/demo_seed.py propose TEXT  # Submit a governance proposal

Uses a local SQLite database (demo_pinwheel.db).
"""

from __future__ import annotations

import asyncio
import os
import sys

from pinwheel.core.game_loop import step_round
from pinwheel.core.scheduler import compute_standings, generate_round_robin
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import Base
from pinwheel.db.repository import Repository

DEMO_DB = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///demo_pinwheel.db")

# Portland-themed teams
TEAMS = [
    {
        "name": "Rose City Thorns",
        "color": "#e94560",
        "venue": {"name": "Thorn Garden", "capacity": 4000},
        "agents": [
            (
                "Briar Ashwood",
                "sharpshooter",
                {
                    "scoring": 65,
                    "passing": 35,
                    "defense": 30,
                    "speed": 45,
                    "stamina": 40,
                    "iq": 55,
                    "ego": 40,
                    "chaotic_alignment": 20,
                    "fate": 30,
                },
            ),
            (
                "Rosa Vex",
                "playmaker",
                {
                    "scoring": 35,
                    "passing": 65,
                    "defense": 35,
                    "speed": 50,
                    "stamina": 40,
                    "iq": 60,
                    "ego": 25,
                    "chaotic_alignment": 15,
                    "fate": 35,
                },
            ),
            (
                "Hazel Blackthorn",
                "enforcer",
                {
                    "scoring": 30,
                    "passing": 30,
                    "defense": 65,
                    "speed": 40,
                    "stamina": 55,
                    "iq": 40,
                    "ego": 45,
                    "chaotic_alignment": 30,
                    "fate": 25,
                },
            ),
        ],
    },
    {
        "name": "Burnside Breakers",
        "color": "#53d8fb",
        "venue": {"name": "Burnside Courts", "capacity": 3500},
        "agents": [
            (
                "Kai Ripley",
                "glass_cannon",
                {
                    "scoring": 70,
                    "passing": 25,
                    "defense": 20,
                    "speed": 55,
                    "stamina": 35,
                    "iq": 45,
                    "ego": 55,
                    "chaotic_alignment": 25,
                    "fate": 30,
                },
            ),
            (
                "River Stone",
                "floor_general",
                {
                    "scoring": 30,
                    "passing": 60,
                    "defense": 40,
                    "speed": 45,
                    "stamina": 45,
                    "iq": 65,
                    "ego": 20,
                    "chaotic_alignment": 15,
                    "fate": 40,
                },
            ),
            (
                "Ash Torrent",
                "two_way_star",
                {
                    "scoring": 45,
                    "passing": 40,
                    "defense": 55,
                    "speed": 45,
                    "stamina": 50,
                    "iq": 45,
                    "ego": 30,
                    "chaotic_alignment": 20,
                    "fate": 30,
                },
            ),
        ],
    },
    {
        "name": "St. Johns Herons",
        "color": "#b794f4",
        "venue": {"name": "Cathedral Park Arena", "capacity": 3000},
        "agents": [
            (
                "Wren Silvas",
                "sharpshooter",
                {
                    "scoring": 60,
                    "passing": 40,
                    "defense": 25,
                    "speed": 50,
                    "stamina": 40,
                    "iq": 50,
                    "ego": 35,
                    "chaotic_alignment": 25,
                    "fate": 35,
                },
            ),
            (
                "Crane Fisher",
                "enforcer",
                {
                    "scoring": 25,
                    "passing": 35,
                    "defense": 70,
                    "speed": 35,
                    "stamina": 60,
                    "iq": 40,
                    "ego": 40,
                    "chaotic_alignment": 25,
                    "fate": 30,
                },
            ),
            (
                "Egret Moon",
                "chaos_agent",
                {
                    "scoring": 40,
                    "passing": 45,
                    "defense": 30,
                    "speed": 55,
                    "stamina": 35,
                    "iq": 30,
                    "ego": 50,
                    "chaotic_alignment": 65,
                    "fate": 10,
                },
            ),
        ],
    },
    {
        "name": "Hawthorne Hammers",
        "color": "#f0c040",
        "venue": {"name": "The Forge", "capacity": 4500},
        "agents": [
            (
                "Steel Voss",
                "enforcer",
                {
                    "scoring": 35,
                    "passing": 30,
                    "defense": 60,
                    "speed": 40,
                    "stamina": 60,
                    "iq": 45,
                    "ego": 40,
                    "chaotic_alignment": 20,
                    "fate": 30,
                },
            ),
            (
                "Blaze Caldwell",
                "glass_cannon",
                {
                    "scoring": 70,
                    "passing": 30,
                    "defense": 15,
                    "speed": 55,
                    "stamina": 30,
                    "iq": 40,
                    "ego": 60,
                    "chaotic_alignment": 35,
                    "fate": 25,
                },
            ),
            (
                "Ember Kine",
                "playmaker",
                {
                    "scoring": 40,
                    "passing": 60,
                    "defense": 30,
                    "speed": 50,
                    "stamina": 40,
                    "iq": 55,
                    "ego": 30,
                    "chaotic_alignment": 20,
                    "fate": 35,
                },
            ),
        ],
    },
]


async def seed():
    """Create the demo league."""
    engine = create_engine(DEMO_DB)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with get_session(engine) as session:
        repo = Repository(session)
        league = await repo.create_league("Portland Pinwheel League")
        season = await repo.create_season(league.id, "Season 1")

        team_ids = []
        for t in TEAMS:
            team = await repo.create_team(season.id, t["name"], color=t["color"], venue=t["venue"])
            team_ids.append(team.id)
            for name, archetype, attrs in t["agents"]:
                await repo.create_agent(
                    team_id=team.id,
                    season_id=season.id,
                    name=name,
                    archetype=archetype,
                    attributes=attrs,
                )

        matchups = generate_round_robin(team_ids)
        for m in matchups:
            await repo.create_schedule_entry(
                season_id=season.id,
                round_number=m.round_number,
                matchup_index=m.matchup_index,
                home_team_id=m.home_team_id,
                away_team_id=m.away_team_id,
            )

        await session.commit()
        print(f"League seeded: {len(TEAMS)} teams, {len(matchups)} scheduled games")
        print(f"Season ID: {season.id}")
        for i, tid in enumerate(team_ids):
            print(f"  {TEAMS[i]['name']}: {tid}")

    await engine.dispose()


async def step(rounds: int = 1):
    """Run N rounds of simulation."""
    engine = create_engine(DEMO_DB)
    async with get_session(engine) as session:
        repo = Repository(session)

        # Find current season
        from sqlalchemy import select

        from pinwheel.db.models import SeasonRow

        result = await session.execute(select(SeasonRow).limit(1))
        season = result.scalar_one_or_none()
        if not season:
            print("No season found. Run 'seed' first.")
            return

        # Find what round we're on
        current_round = 0
        for rn in range(1, 100):
            games = await repo.get_games_for_round(season.id, rn)
            if games:
                current_round = rn
            else:
                break

        for i in range(rounds):
            rn = current_round + 1 + i
            result = await step_round(repo, season.id, round_number=rn)
            print(f"Round {rn}: {len(result.games)} games, {len(result.mirrors)} mirrors")
            for g in result.games:
                winner = "HOME" if g["winner_team_id"] == g.get("home_team_id") else "AWAY"
                elam = " [ELAM]" if g["elam_activated"] else ""
                print(
                    f"  {g['home_team']} {g['home_score']} - "
                    f"{g['away_score']} {g['away_team']} ({winner}){elam}"
                )
            for m in result.mirrors:
                if m.mirror_type != "private":
                    print(f"  Mirror ({m.mirror_type}): {m.content[:80]}...")

        await session.commit()

    await engine.dispose()


async def status():
    """Print current league state."""
    engine = create_engine(DEMO_DB)
    async with get_session(engine) as session:
        repo = Repository(session)

        from sqlalchemy import select

        from pinwheel.db.models import SeasonRow

        result = await session.execute(select(SeasonRow).limit(1))
        season = result.scalar_one_or_none()
        if not season:
            print("No season found.")
            return

        # Count rounds played
        all_results = []
        last_round = 0
        for rn in range(1, 100):
            games = await repo.get_games_for_round(season.id, rn)
            if not games:
                break
            last_round = rn
            for g in games:
                all_results.append(
                    {
                        "home_team_id": g.home_team_id,
                        "away_team_id": g.away_team_id,
                        "home_score": g.home_score,
                        "away_score": g.away_score,
                        "winner_team_id": g.winner_team_id,
                    }
                )

        standings = compute_standings(all_results)
        for s in standings:
            team = await repo.get_team(s["team_id"])
            s["team_name"] = team.name if team else s["team_id"]

        print(f"Season: {season.name} | Rounds played: {last_round}")
        print(f"{'Team':<25} {'W':>3} {'L':>3} {'PCT':>6} {'PF':>5} {'PA':>5} {'DIFF':>5}")
        print("-" * 55)
        for s in standings:
            pct = s["wins"] / max(s["wins"] + s["losses"], 1)
            diff = s["points_for"] - s["points_against"]
            sign = "+" if diff > 0 else ""
            print(
                f"{s['team_name']:<25} {s['wins']:>3} {s['losses']:>3} "
                f"{pct:>6.3f} {s['points_for']:>5} {s['points_against']:>5} {sign}{diff:>4}"
            )

    await engine.dispose()


async def propose(text: str):
    """Submit a governance proposal (demo shortcut)."""
    engine = create_engine(DEMO_DB)
    async with get_session(engine) as session:
        repo = Repository(session)

        from sqlalchemy import select

        from pinwheel.db.models import SeasonRow

        result = await session.execute(select(SeasonRow).limit(1))
        season = result.scalar_one_or_none()
        if not season:
            print("No season found.")
            return

        import uuid

        proposal_id = f"p-{uuid.uuid4().hex[:8]}"
        await repo.append_event(
            event_type="proposal.submitted",
            aggregate_id=proposal_id,
            aggregate_type="proposal",
            season_id=season.id,
            governor_id="demo-governor",
            team_id="demo-team",
            payload={
                "id": proposal_id,
                "governor_id": "demo-governor",
                "team_id": "demo-team",
                "raw_text": text,
                "status": "submitted",
                "tier": 1,
            },
        )
        await session.commit()
        print(f"Proposal submitted: {proposal_id}")
        print(f"  Text: {text}")

    await engine.dispose()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]
    if cmd == "seed":
        asyncio.run(seed())
    elif cmd == "step":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 1
        asyncio.run(step(n))
    elif cmd == "status":
        asyncio.run(status())
    elif cmd == "propose":
        if len(sys.argv) < 3:
            print("Usage: demo_seed.py propose 'your proposal text'")
            return
        asyncio.run(propose(" ".join(sys.argv[2:])))
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
