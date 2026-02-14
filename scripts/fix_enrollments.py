"""Re-enroll players whose team assignments were lost during a season transition.

Finds all players with a team_id pointing to an old season's team,
maps them to the matching team (by name) in the active season, and
updates their enrolled_season_id + team_id.

Safe to run multiple times (idempotent). Does not affect players
already enrolled in the active season.

Usage:
    # Dry run (default — shows what would change, changes nothing):
    python scripts/fix_enrollments.py

    # Apply changes:
    python scripts/fix_enrollments.py --apply

    # On Fly.io production:
    flyctl ssh console -C "python scripts/fix_enrollments.py --apply"
"""

from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import select

from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import PlayerRow, SeasonRow, TeamRow
from pinwheel.db.repository import Repository


async def fix_enrollments(apply: bool = False) -> None:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set.")
        sys.exit(1)

    engine = create_engine(db_url)

    async with get_session(engine) as session:
        repo = Repository(session)

        # 1. Find the active season
        season = await repo.get_active_season()
        if not season:
            print("No active season found.")
            await engine.dispose()
            return

        print(f"Active season: {season.name} ({season.id}) [status={season.status}]")

        # 2. Get active season's teams (name -> team row)
        active_teams = await repo.get_teams_for_season(season.id)
        team_by_name: dict[str, TeamRow] = {t.name: t for t in active_teams}
        print(f"Active season teams: {', '.join(team_by_name.keys())}")

        # 3. Build a lookup of ALL team IDs -> team names (across all seasons)
        all_teams_result = await session.execute(select(TeamRow))
        all_teams = list(all_teams_result.scalars().all())
        team_name_by_id: dict[str, str] = {t.id: t.name for t in all_teams}

        # 4. Get ALL players
        all_players_result = await session.execute(select(PlayerRow))
        all_players = list(all_players_result.scalars().all())
        print(f"Total players in database: {len(all_players)}")

        # 5. Find and fix mismatched enrollments
        already_enrolled = 0
        no_team = 0
        fixed = 0
        unmatchable = 0

        for player in all_players:
            if player.enrolled_season_id == season.id and player.team_id is not None:
                # Already correctly enrolled
                already_enrolled += 1
                continue

            if player.team_id is None and player.enrolled_season_id is None:
                # Never joined a team
                no_team += 1
                continue

            # Player has a team_id but wrong/missing enrolled_season_id
            old_team_name = team_name_by_id.get(player.team_id or "")
            if old_team_name is None:
                # team_id points to a deleted team — check enrolled_season_id
                # for any clue, but likely unrecoverable without Discord roles
                print(
                    f"  SKIP {player.username} (discord={player.discord_id}): "
                    f"team_id={player.team_id} not found in any season"
                )
                unmatchable += 1
                continue

            # Find matching team in active season
            new_team = team_by_name.get(old_team_name)
            if new_team is None:
                print(
                    f"  SKIP {player.username}: was on '{old_team_name}' "
                    f"but no matching team in active season"
                )
                unmatchable += 1
                continue

            action = "FIXING" if apply else "WOULD FIX"
            print(
                f"  {action} {player.username} (discord={player.discord_id}): "
                f"'{old_team_name}' — "
                f"team_id {player.team_id} -> {new_team.id}, "
                f"enrolled_season_id {player.enrolled_season_id} -> {season.id}"
            )

            if apply:
                player.team_id = new_team.id
                player.enrolled_season_id = season.id
                await session.flush()

            fixed += 1

        if apply and fixed > 0:
            await session.commit()

        print()
        print(f"Already enrolled:  {already_enrolled}")
        print(f"No team (skip):    {no_team}")
        print(f"{'Fixed' if apply else 'Would fix'}:  {fixed}")
        print(f"Unmatchable:       {unmatchable}")

        if not apply and fixed > 0:
            print(f"\nRun with --apply to commit these {fixed} changes.")

    await engine.dispose()


def main() -> None:
    apply = "--apply" in sys.argv
    asyncio.run(fix_enrollments(apply=apply))


if __name__ == "__main__":
    main()
