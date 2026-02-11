"""SSE (Server-Sent Events) endpoint for real-time game streaming."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from pinwheel.core.event_bus import EventBus

router = APIRouter(prefix="/api/events", tags=["events"])
logger = logging.getLogger(__name__)


def _get_bus(request: Request) -> EventBus:
    """Get the EventBus from app state."""
    return request.app.state.event_bus


@router.get("/stream")
async def sse_stream(
    request: Request,
    event_type: str | None = None,
) -> StreamingResponse:
    """Server-Sent Events stream.

    Query params:
        event_type: optional filter (e.g. "game.completed", "mirror.generated")
                    If omitted, receives all events.

    Returns an SSE stream that stays open until the client disconnects.
    """
    bus = _get_bus(request)

    async def generate():
        async with bus.subscribe(event_type) as sub:
            async for event in sub:
                if await request.is_disconnected():
                    break
                data = json.dumps(event, default=str)
                yield f"event: {event['type']}\ndata: {data}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/health")
async def events_health(request: Request) -> dict:
    """Check EventBus health and subscriber count."""
    bus = _get_bus(request)
    return {
        "status": "ok",
        "subscribers": bus.subscriber_count,
    }
