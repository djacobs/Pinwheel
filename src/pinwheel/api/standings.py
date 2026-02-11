"""Standings API endpoint."""

from __future__ import annotations

from fastapi import APIRouter

from pinwheel.api.deps import RepoDep
from pinwheel.core.scheduler import compute_standings

router = APIRouter(prefix="/api", tags=["standings"])


@router.get("/standings")
async def get_standings(season_id: str, repo: RepoDep) -> dict:
    """Get current standings for a season."""
    # Collect all game results across all rounds
    # For now, iterate through rounds 1-50 (arbitrary max)
    all_results: list[dict] = []
    for round_num in range(1, 50):
        games = await repo.get_games_for_round(season_id, round_num)
        if not games:
            break
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

    # Enrich with team names
    for s in standings:
        team = await repo.get_team(s["team_id"])
        if team:
            s["team_name"] = team.name

    return {"data": standings}
