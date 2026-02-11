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
                "agent_count": len(t.agents),
            }
            for t in teams
        ],
    }


@router.get("/{team_id}")
async def get_team(team_id: str, repo: RepoDep) -> dict:
    """Get a single team with its agents."""
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
            "agents": [
                {
                    "id": a.id,
                    "name": a.name,
                    "archetype": a.archetype,
                    "attributes": a.attributes,
                    "is_active": a.is_active,
                }
                for a in team.agents
            ],
        },
    }
