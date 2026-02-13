"""Team and standings API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from pinwheel.api.deps import RepoDep

router = APIRouter(prefix="/api/teams", tags=["teams"])


@router.get("")
async def list_teams(season_id: str, repo: RepoDep) -> dict:
    """List all teams for a season."""
    teams = await repo.get_teams_for_season(season_id)
    return {
        "data": [
            {
                "id": t.id,
                "name": t.name,
                "color": t.color,
                "motto": t.motto,
                "venue": t.venue,
                "hooper_count": len(t.hoopers),
            }
            for t in teams
        ],
    }


@router.get("/{team_id}")
async def get_team(team_id: str, repo: RepoDep) -> dict:
    """Get a single team with its hoopers."""
    team = await repo.get_team(team_id)
    if not team:
        raise HTTPException(404, "Team not found")
    return {
        "data": {
            "id": team.id,
            "name": team.name,
            "color": team.color,
            "motto": team.motto,
            "venue": team.venue,
            "hoopers": [
                {
                    "id": h.id,
                    "name": h.name,
                    "archetype": h.archetype,
                    "attributes": h.attributes,
                    "is_active": h.is_active,
                }
                for h in team.hoopers
            ],
        },
    }
