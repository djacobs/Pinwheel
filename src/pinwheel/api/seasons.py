"""Season management API endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from pinwheel.api.deps import RepoDep
from pinwheel.auth.deps import require_api_admin

router = APIRouter(prefix="/api/seasons", tags=["seasons"])


class CreateSeasonRequest(BaseModel):
    """Request body for creating a new season."""

    league_id: str
    name: str
    carry_forward_rules: bool = True
    previous_season_id: str | None = None


@router.post("")
async def create_season_endpoint(
    body: CreateSeasonRequest,
    repo: RepoDep,
    _: Annotated[None, Depends(require_api_admin)],
) -> dict:
    """Admin endpoint to start a new season.

    Creates a new season with either default rules or carried-forward rules
    from a previous season. Teams, hoopers, and governor enrollments are
    carried over. Tokens are regenerated for all governors.
    """
    from pinwheel.core.season import start_new_season

    try:
        new_season = await start_new_season(
            repo=repo,
            league_id=body.league_id,
            season_name=body.name,
            carry_forward_rules=body.carry_forward_rules,
            previous_season_id=body.previous_season_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    teams = await repo.get_teams_for_season(new_season.id)

    return {
        "data": {
            "id": new_season.id,
            "league_id": new_season.league_id,
            "name": new_season.name,
            "status": new_season.status,
            "starting_ruleset": new_season.starting_ruleset,
            "current_ruleset": new_season.current_ruleset,
            "team_count": len(teams),
        },
    }
