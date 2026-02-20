"""SSE (Server-Sent Events) endpoint for real-time game streaming."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from pinwheel.core.event_bus import EventBus

router = APIRouter(prefix="/api/events", tags=["events"])
logger = logging.getLogger(__name__)

_HEARTBEAT_INTERVAL = 15  # seconds

# ---------------------------------------------------------------------------
# Security: connection limit
# ---------------------------------------------------------------------------

# Maximum number of concurrent SSE connections allowed.  Anonymous clients
# can hold connections open indefinitely, so an unbounded pool is a resource-
# exhaustion vector.  100 concurrent streams is generous for a game dashboard.
_MAX_SSE_CONNECTIONS = 100
_connection_semaphore = asyncio.Semaphore(_MAX_SSE_CONNECTIONS)

# ---------------------------------------------------------------------------
# Security: event-type allowlist
# ---------------------------------------------------------------------------
# Derived from every bus.publish() call in the codebase.  Any value not in
# this set is rejected with 400 — prevents probing for internal event names
# and keeps the filter surface well-defined.

ALLOWED_EVENT_TYPES: frozenset[str] = frozenset(
    {
        # Game lifecycle
        "game.completed",
        # Round lifecycle
        "round.completed",
        # Season lifecycle
        "season.phase_changed",
        "season.regular_season_complete",
        "season.semifinals_complete",
        "season.playoffs_complete",
        "season.championship_started",
        "season.offseason_started",
        "season.offseason_closed",
        "season.tiebreaker_games_generated",
        "season.memorial_generated",
        # Governance
        "governance.window_closed",
        # Reports / reflections
        "report.generated",
        # Hooper milestones
        "hooper.milestone_reached",
        # Presentation / replay mode
        "presentation.game_starting",
        "presentation.game_finished",
        "presentation.round_finished",
        "presentation.possession",
        "presentation.suspense",
    }
)


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
        event_type: optional filter — must be one of the known event types
                    (e.g. "game.completed", "report.generated").
                    If omitted, receives all events.

    Returns an SSE stream that stays open until the client disconnects.
    Sends an initial comment to flush proxy buffers and periodic heartbeats
    to keep the connection alive through reverse proxies.

    Errors:
        400 — unknown event_type value
        429 — global connection limit reached
    """
    # Validate event_type against the allowlist
    if event_type is not None and event_type not in ALLOWED_EVENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown event_type {event_type!r}. "
                f"Valid values: {sorted(ALLOWED_EVENT_TYPES)}"
            ),
        )

    # Enforce global connection cap — locked is True when all slots are taken.
    # asyncio.Semaphore.locked() is the public API for checking availability.
    if _connection_semaphore.locked():
        raise HTTPException(
            status_code=429,
            detail=(
                f"Too many concurrent SSE connections "
                f"(limit: {_MAX_SSE_CONNECTIONS}). Try again later."
            ),
        )

    bus = _get_bus(request)

    async def generate():
        async with _connection_semaphore:
            # Immediately flush a comment through the proxy so the browser
            # transitions from "connecting" to "open" state.
            yield ": connected\n\n"

            async with bus.subscribe(event_type) as sub:
                while True:
                    if await request.is_disconnected():
                        break
                    event = await sub.get(timeout=_HEARTBEAT_INTERVAL)
                    if event is None:
                        # No event within the heartbeat window — send keep-alive
                        yield ": heartbeat\n\n"
                        continue
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
    """Check EventBus health, subscriber count, and SSE connection stats."""
    bus = _get_bus(request)
    return {
        "status": "ok",
        "subscribers": bus.subscriber_count,
        "active_sse_connections": _MAX_SSE_CONNECTIONS - _connection_semaphore._value,  # noqa: SLF001
        "max_sse_connections": _MAX_SSE_CONNECTIONS,
    }
