"""Tests for SSE endpoint security: event_type allowlist and connection limit.

Testing SSE streams with httpx ASGI transport is tricky because:
- The SSE generator blocks on sub.get(timeout=_HEARTBEAT_INTERVAL) — 15 s by default
- To avoid hanging, streaming tests run in a background task that is cancelled
  after the first chunk, with a short asyncio.sleep() to give the generator
  enough time to flush the initial ": connected\n\n" comment.
- 400 / 429 tests use regular (non-streaming) GET — no blocking involved.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest
from httpx import ASGITransport, AsyncClient

import pinwheel.api.events as events_module
from pinwheel.api.events import _MAX_SSE_CONNECTIONS, ALLOWED_EVENT_TYPES
from pinwheel.config import Settings
from pinwheel.core.event_bus import EventBus
from pinwheel.core.presenter import PresentationState
from pinwheel.db.engine import create_engine
from pinwheel.db.models import Base
from pinwheel.main import create_app

# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def sse_app():
    """Test app with EventBus attached, ready for SSE testing."""
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        pinwheel_env="development",
        pinwheel_auto_advance=False,
    )
    app = create_app(settings)

    engine = create_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app.state.engine = engine
    app.state.event_bus = EventBus()
    app.state.presentation_state = PresentationState()

    yield app

    await engine.dispose()


@pytest.fixture
async def sse_client(sse_app):
    """Async HTTP client bound to the test app."""
    transport = ASGITransport(app=sse_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _stream_status_and_first_chunk(
    client: AsyncClient,
    params: dict | None = None,
) -> tuple[int, bytes]:
    """Open /api/events/stream, capture status + first body chunk, then cancel.

    The stream is driven in a background task.  We publish a dummy event to
    the bus so the generator emits a data frame and unblocks.  The task is
    cancelled after a short grace period.

    Returns (http_status_code, first_chunk_or_empty).
    """
    result: dict = {"status": 0, "chunk": b""}

    async def _run():
        async with client.stream(
            "GET", "/api/events/stream", params=params or {}
        ) as resp:
            result["status"] = resp.status_code
            async for chunk in resp.aiter_bytes():
                if chunk:
                    result["chunk"] = chunk
                    return

    task = asyncio.create_task(_run())
    # Allow the task to start and the generator to yield ": connected\n\n"
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    return result["status"], result["chunk"]


# ---------------------------------------------------------------------------
# Allowlist unit tests (no I/O)
# ---------------------------------------------------------------------------


class TestAllowlist:
    """Verify the ALLOWED_EVENT_TYPES constant is well-formed."""

    def test_allowlist_is_nonempty(self) -> None:
        assert len(ALLOWED_EVENT_TYPES) > 0

    def test_known_event_types_present(self) -> None:
        """Spot-check every event type emitted by the game loop is present."""
        required = {
            "game.completed",
            "round.completed",
            "report.generated",
            "governance.window_closed",
            "season.phase_changed",
            "season.regular_season_complete",
            "season.playoffs_complete",
            "season.championship_started",
            "season.offseason_started",
            "season.offseason_closed",
            "season.semifinals_complete",
            "season.tiebreaker_games_generated",
            "season.memorial_generated",
            "hooper.milestone_reached",
            "presentation.game_starting",
            "presentation.game_finished",
            "presentation.round_finished",
            "presentation.possession",
            "presentation.suspense",
        }
        missing = required - ALLOWED_EVENT_TYPES
        assert not missing, f"Missing from allowlist: {missing}"

    def test_all_entries_use_dot_notation(self) -> None:
        for et in ALLOWED_EVENT_TYPES:
            assert "." in et, f"{et!r} does not use dot-notation"

    def test_max_connections_constant(self) -> None:
        assert _MAX_SSE_CONNECTIONS == 100


# ---------------------------------------------------------------------------
# Event-type validation — 400 for unknown types
# ---------------------------------------------------------------------------


class TestEventTypeValidation:
    """The stream endpoint must reject unknown event_type values before streaming."""

    async def test_unknown_event_type_returns_400(self, sse_client) -> None:
        resp = await sse_client.get(
            "/api/events/stream",
            params={"event_type": "internal.secret_event"},
        )
        assert resp.status_code == 400

    async def test_empty_string_event_type_returns_400(self, sse_client) -> None:
        resp = await sse_client.get(
            "/api/events/stream",
            params={"event_type": ""},
        )
        assert resp.status_code == 400

    async def test_sql_injection_attempt_returns_400(self, sse_client) -> None:
        resp = await sse_client.get(
            "/api/events/stream",
            params={"event_type": "'; DROP TABLE events; --"},
        )
        assert resp.status_code == 400

    async def test_error_detail_mentions_unknown_type(self, sse_client) -> None:
        resp = await sse_client.get(
            "/api/events/stream",
            params={"event_type": "bogus.type"},
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        # Should mention the offending value or hint at valid types
        assert "bogus.type" in detail or "valid" in detail.lower() or "unknown" in detail.lower()

    async def test_error_detail_lists_valid_values(self, sse_client) -> None:
        resp = await sse_client.get(
            "/api/events/stream",
            params={"event_type": "nope.nope"},
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "game.completed" in detail or "Valid values" in detail

    async def test_none_event_type_not_rejected(self, sse_client) -> None:
        """Omitting event_type should NOT return 400 (it's a valid wildcard)."""
        # We check purely at the HTTP level: 400 must not be returned.
        # Use a task so we can cancel without blocking on the open stream.
        status, _ = await _stream_status_and_first_chunk(sse_client)
        assert status != 400

    async def test_valid_event_type_not_rejected(self, sse_client) -> None:
        """A known event_type must not return 400."""
        status, _ = await _stream_status_and_first_chunk(
            sse_client, params={"event_type": "game.completed"}
        )
        assert status != 400

    async def test_all_allowlisted_types_not_rejected(self, sse_client) -> None:
        """Every allowlisted type must be accepted (not 400)."""
        for et in sorted(ALLOWED_EVENT_TYPES):
            status, _ = await _stream_status_and_first_chunk(
                sse_client, params={"event_type": et}
            )
            assert status != 400, f"event_type={et!r} was incorrectly rejected"


# ---------------------------------------------------------------------------
# Connection limit — 429 when semaphore is exhausted
# ---------------------------------------------------------------------------


class TestConnectionLimit:
    """429 is returned when the global connection cap is hit."""

    async def test_connection_limit_enforced(self, sse_client) -> None:
        """Hold all semaphore slots, verify the next request gets 429."""
        original_limit = events_module._MAX_SSE_CONNECTIONS
        original_semaphore = events_module._connection_semaphore

        test_limit = 2
        events_module._MAX_SSE_CONNECTIONS = test_limit
        events_module._connection_semaphore = asyncio.Semaphore(test_limit)

        try:
            for _ in range(test_limit):
                await events_module._connection_semaphore.acquire()

            resp = await sse_client.get("/api/events/stream")
            assert resp.status_code == 429
        finally:
            events_module._MAX_SSE_CONNECTIONS = original_limit
            events_module._connection_semaphore = original_semaphore

    async def test_429_detail_mentions_limit(self, sse_client) -> None:
        """The 429 body should explain the limit."""
        original_limit = events_module._MAX_SSE_CONNECTIONS
        original_semaphore = events_module._connection_semaphore

        test_limit = 1
        events_module._MAX_SSE_CONNECTIONS = test_limit
        events_module._connection_semaphore = asyncio.Semaphore(test_limit)

        try:
            await events_module._connection_semaphore.acquire()
            resp = await sse_client.get("/api/events/stream")
            assert resp.status_code == 429
            detail = resp.json()["detail"]
            assert str(test_limit) in detail or "limit" in detail.lower()
        finally:
            events_module._MAX_SSE_CONNECTIONS = original_limit
            events_module._connection_semaphore = original_semaphore

    async def test_below_limit_not_rejected(self, sse_client) -> None:
        """With at least one free slot the request must NOT return 429."""
        original_limit = events_module._MAX_SSE_CONNECTIONS
        original_semaphore = events_module._connection_semaphore

        test_limit = 3
        events_module._MAX_SSE_CONNECTIONS = test_limit
        events_module._connection_semaphore = asyncio.Semaphore(test_limit)

        try:
            # Hold 2/3 slots — one is still free
            for _ in range(2):
                await events_module._connection_semaphore.acquire()

            status, _ = await _stream_status_and_first_chunk(sse_client)
            assert status != 429
        finally:
            events_module._MAX_SSE_CONNECTIONS = original_limit
            events_module._connection_semaphore = original_semaphore

    async def test_semaphore_locked_check_works(self) -> None:
        """asyncio.Semaphore.locked() returns True when all slots are taken."""
        sem = asyncio.Semaphore(2)
        assert not sem.locked()
        await sem.acquire()
        assert not sem.locked()
        await sem.acquire()
        assert sem.locked()
        sem.release()
        assert not sem.locked()


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


class TestEventsHealth:
    """The /health endpoint should expose connection telemetry."""

    async def test_health_returns_ok(self, sse_client) -> None:
        resp = await sse_client.get("/api/events/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "subscribers" in body

    async def test_health_reports_max_connections(self, sse_client) -> None:
        resp = await sse_client.get("/api/events/health")
        assert resp.status_code == 200
        body = resp.json()
        assert "max_sse_connections" in body
        assert body["max_sse_connections"] == _MAX_SSE_CONNECTIONS

    async def test_health_reports_active_connections(self, sse_client) -> None:
        resp = await sse_client.get("/api/events/health")
        assert resp.status_code == 200
        body = resp.json()
        assert "active_sse_connections" in body
        assert isinstance(body["active_sse_connections"], int)
        assert body["active_sse_connections"] >= 0
