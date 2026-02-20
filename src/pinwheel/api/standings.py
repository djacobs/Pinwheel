"""Standings API endpoint."""

from __future__ import annotations

from fastapi import APIRouter

from pinwheel.api.deps import RepoDep
from pinwheel.core.scheduler import compute_standings

router = APIRouter(prefix="/api", tags=["standings"])


@router.get("/standings")
async def get_standings(season_id: str, repo: RepoDep) -> dict:
    """Get current standings for a season.

    Fetches all game results in a single query (replaces the old
    loop over rounds 1-50 that issued one query per round).
    Team names are resolved in a second bulk query instead of one
    query per standing entry.
    """
    games = await repo.get_all_games(season_id)
    all_results: list[dict] = [
        {
            "home_team_id": g.home_team_id,
            "away_team_id": g.away_team_id,
            "home_score": g.home_score,
            "away_score": g.away_score,
            "winner_team_id": g.winner_team_id,
        }
        for g in games
    ]

    standings = compute_standings(all_results)

    # Batch-fetch team names for all standing entries in one query
    team_ids = [s["team_id"] for s in standings]
    if team_ids:
        teams = await repo.get_teams_for_season(season_id)
        team_name_map = {t.id: t.name for t in teams}
        for s in standings:
            name = team_name_map.get(s["team_id"])
            if name:
                s["team_name"] = name

    return {"data": standings}
