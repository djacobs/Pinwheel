"""Re-seed production database with fresh Season 2 data.

Usage:
    # Interactive (prompts for confirmation):
    python scripts/prod_reseed.py

    # Non-interactive (for fly ssh console):
    python scripts/prod_reseed.py --force

After running, restart the app. The Discord bot's self-heal mechanism
(_sync_role_enrollments) will automatically re-enroll players who have
team roles, matching by team name.
"""

from __future__ import annotations

import asyncio
import os
import sys

from pinwheel.core.scheduler import generate_round_robin
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import Base
from pinwheel.db.repository import Repository
from pinwheel.models.rules import DEFAULT_RULESET

# Portland-themed teams (identical to demo_seed.py)
TEAMS = [
    {
        "name": "Rose City Thorns",
        "color": "#e94560",
        "color_secondary": "#1a1a2e",
        "venue": {"name": "Thorn Garden", "capacity": 4000},
        "hoopers": [
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
            (
                "Fern Wilder",
                "iron_horse",
                {
                    "scoring": 30,
                    "passing": 30,
                    "defense": 40,
                    "speed": 35,
                    "stamina": 70,
                    "iq": 35,
                    "ego": 20,
                    "chaotic_alignment": 15,
                    "fate": 30,
                },
            ),
        ],
    },
    {
        "name": "Burnside Breakers",
        "color": "#53d8fb",
        "color_secondary": "#0a2540",
        "venue": {"name": "Burnside Courts", "capacity": 3500},
        "hoopers": [
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
            (
                "Reed Calloway",
                "lockdown",
                {
                    "scoring": 20,
                    "passing": 25,
                    "defense": 60,
                    "speed": 40,
                    "stamina": 65,
                    "iq": 40,
                    "ego": 15,
                    "chaotic_alignment": 10,
                    "fate": 35,
                },
            ),
        ],
    },
    {
        "name": "St. Johns Herons",
        "color": "#b794f4",
        "color_secondary": "#1e1033",
        "venue": {"name": "Cathedral Park Arena", "capacity": 3000},
        "hoopers": [
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
            (
                "Lark Holloway",
                "savant",
                {
                    "scoring": 30,
                    "passing": 40,
                    "defense": 25,
                    "speed": 30,
                    "stamina": 60,
                    "iq": 65,
                    "ego": 15,
                    "chaotic_alignment": 20,
                    "fate": 40,
                },
            ),
        ],
    },
    {
        "name": "Hawthorne Hammers",
        "color": "#f0c040",
        "color_secondary": "#2a1f00",
        "venue": {"name": "The Forge", "capacity": 4500},
        "hoopers": [
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
            (
                "Cinder Holt",
                "slasher",
                {
                    "scoring": 40,
                    "passing": 25,
                    "defense": 25,
                    "speed": 60,
                    "stamina": 65,
                    "iq": 30,
                    "ego": 35,
                    "chaotic_alignment": 25,
                    "fate": 20,
                },
            ),
        ],
    },
]


async def reseed(force: bool = False) -> None:
    """Drop all tables and re-seed production with Season 2."""
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL environment variable is not set.")
        print("This script must be run with a real DATABASE_URL.")
        sys.exit(1)

    print(f"Target database: {db_url[:40]}...")

    if not force:
        print()
        answer = input(
            "This will DROP ALL TABLES in the production database.\n"
            "All existing data (seasons, teams, games, governors, proposals) will be lost.\n"
            "Type 'yes' to continue: "
        )
        if answer.strip().lower() != "yes":
            print("Aborted.")
            return

    # -- 1. Connect and drop/recreate schema --------------------------------
    print("\n[1/6] Dropping all tables...")
    engine = create_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    print("       Tables dropped.")

    print("[2/6] Recreating schema...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("       Schema created.")

    # -- 3. Seed league, season, teams, hoopers, schedule -------------------
    async with get_session(engine) as session:
        repo = Repository(session)

        print("[3/6] Creating league...")
        league = await repo.create_league("Portland Pinwheel League")
        print(f"       League: {league.name} ({league.id})")

        print("[4/6] Creating Season 2...")
        ruleset_dict = DEFAULT_RULESET.model_dump()
        season = await repo.create_season(
            league.id,
            "Season 2",
            starting_ruleset=ruleset_dict,
        )
        season.status = "active"
        await session.flush()
        print(f"       Season: {season.name} ({season.id}) [status={season.status}]")

        print("[5/6] Creating teams and hoopers...")
        team_ids: list[str] = []
        for t in TEAMS:
            team = await repo.create_team(
                season.id,
                t["name"],
                color=t["color"],
                color_secondary=t.get("color_secondary", "#ffffff"),
                venue=t["venue"],
            )
            team_ids.append(team.id)
            for name, archetype, attrs in t["hoopers"]:
                await repo.create_hooper(
                    team_id=team.id,
                    season_id=season.id,
                    name=name,
                    archetype=archetype,
                    attributes=attrs,
                )
            print(f"       {t['name']}: {len(t['hoopers'])} hoopers")

        print("[6/6] Generating round-robin schedule...")
        matchups = generate_round_robin(
            team_ids, num_rounds=DEFAULT_RULESET.round_robins_per_season
        )
        for m in matchups:
            await repo.create_schedule_entry(
                season_id=season.id,
                round_number=m.round_number,
                matchup_index=m.matchup_index,
                home_team_id=m.home_team_id,
                away_team_id=m.away_team_id,
            )
        print(f"       {len(matchups)} games scheduled.")

        await session.commit()

    await engine.dispose()

    # -- Summary ------------------------------------------------------------
    print("\n" + "=" * 50)
    print("Production reseed complete!")
    print(f"  League:  Portland Pinwheel League")
    print(f"  Season:  Season 2 (active)")
    print(f"  Teams:   {len(TEAMS)}")
    print(f"  Hoopers: {sum(len(t['hoopers']) for t in TEAMS)}")
    print(f"  Games:   {len(matchups)}")
    print("=" * 50)
    print("\nRestart the app. Discord bot self-heal will re-enroll governors.")


def main() -> None:
    force = "--force" in sys.argv
    asyncio.run(reseed(force=force))


if __name__ == "__main__":
    main()
