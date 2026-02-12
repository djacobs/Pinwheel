"""Presenter pacing API — demo convenience for controlling game cadence."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from pinwheel.config import PACE_CRON_MAP, VALID_PACES, Settings

router = APIRouter(prefix="/api/pace", tags=["pace"])


class PaceResponse(BaseModel):
    """Response model for pace queries."""

    pace: str
    cron: str | None
    auto_advance: bool


class PaceRequest(BaseModel):
    """Request model for changing pace."""

    pace: str


def _get_settings(request: Request) -> Settings:
    return request.app.state.settings


@router.get("", response_model=PaceResponse)
async def get_pace(request: Request) -> PaceResponse:
    """Return the current presentation pace and derived cron expression."""
    settings = _get_settings(request)
    cron = settings.effective_game_cron()
    return PaceResponse(
        pace=settings.pinwheel_presentation_pace,
        cron=cron,
        auto_advance=cron is not None,
    )


@router.post("", response_model=PaceResponse)
async def set_pace(body: PaceRequest, request: Request) -> PaceResponse:
    """Change the presentation pace in memory (not persisted to env).

    This is a demo convenience endpoint — not meant for production use.
    """
    if body.pace not in VALID_PACES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid pace '{body.pace}'. Must be one of: {sorted(VALID_PACES)}",
        )

    settings = _get_settings(request)
    settings.pinwheel_presentation_pace = body.pace

    cron = PACE_CRON_MAP[body.pace]
    return PaceResponse(
        pace=body.pace,
        cron=cron,
        auto_advance=cron is not None,
    )
