"""Presenter pacing API — demo convenience for controlling game cadence."""

from __future__ import annotations

import asyncio

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


class AdvanceResponse(BaseModel):
    """Response model for advance trigger."""

    status: str
    round: int | None = None


class PaceStatusResponse(BaseModel):
    """Response model for presentation status."""

    is_active: bool
    current_round: int
    current_game_index: int


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


@router.post("/advance", response_model=AdvanceResponse)
async def advance_round(
    request: Request, quarter_seconds: int = 15, game_gap_seconds: int = 5,
) -> AdvanceResponse:
    """Trigger a round advance within the server process.

    Forces presentation_mode="replay" with demo-friendly timing defaults.
    Returns 409 if a presentation is already active.
    """
    from pinwheel.core.presenter import PresentationState
    from pinwheel.core.scheduler_runner import tick_round

    presentation_state: PresentationState = request.app.state.presentation_state
    if presentation_state.is_active:
        raise HTTPException(
            status_code=409,
            detail="A presentation is already active. Wait for it to finish.",
        )

    engine = request.app.state.engine
    event_bus = request.app.state.event_bus
    settings = _get_settings(request)

    asyncio.create_task(
        tick_round(
            engine=engine,
            event_bus=event_bus,
            api_key=settings.anthropic_api_key,
            presentation_state=presentation_state,
            presentation_mode="replay",
            game_interval_seconds=game_gap_seconds,
            quarter_replay_seconds=quarter_seconds,
        )
    )

    return AdvanceResponse(status="started")


@router.get("/status", response_model=PaceStatusResponse)
async def pace_status(request: Request) -> PaceStatusResponse:
    """Return current presentation state."""
    from pinwheel.core.presenter import PresentationState

    presentation_state: PresentationState = request.app.state.presentation_state
    return PaceStatusResponse(
        is_active=presentation_state.is_active,
        current_round=presentation_state.current_round,
        current_game_index=presentation_state.current_game_index,
    )
