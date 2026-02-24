"""Tests for the instrumentation layer — middleware, AI logging, event bus, and phase timing."""

import logging

import pytest
from httpx import ASGITransport, AsyncClient

from pinwheel.ai.usage import compute_cost, record_ai_usage
from pinwheel.config import Settings
from pinwheel.core.event_bus import EventBus
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import Base
from pinwheel.main import create_app

# ---------------------------------------------------------------------------
# 1. Request Timing Middleware
# ---------------------------------------------------------------------------


@pytest.fixture
async def app_client():
    """Create a test app with in-memory database and httpx client."""
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        pinwheel_env="development",
    )
    app = create_app(settings)

    engine = create_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine

    from pinwheel.core.presenter import PresentationState

    app.state.event_bus = EventBus()
    app.state.presentation_state = PresentationState()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    await engine.dispose()


async def test_request_timing_middleware_logs(
    app_client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    """The timing middleware logs method, path, status, and duration for each request."""
    with caplog.at_level(logging.INFO, logger="pinwheel.middleware.timing"):
        response = await app_client.get("/health")

    assert response.status_code == 200

    timing_logs = [r for r in caplog.records if "http_request" in r.message]
    assert len(timing_logs) >= 1, "Expected at least one timing log entry"

    log_msg = timing_logs[0].message
    assert "method=GET" in log_msg
    assert "path=/health" in log_msg
    assert "status=200" in log_msg
    assert "duration_ms=" in log_msg


async def test_static_requests_not_logged(
    app_client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    """Static file requests should be excluded from timing logs to reduce noise."""
    with caplog.at_level(logging.INFO, logger="pinwheel.middleware.timing"):
        # This will 404 since we have no actual static files in test, but that's fine —
        # the middleware still runs and should skip the log.
        await app_client.get("/static/nonexistent.css")

    timing_logs = [r for r in caplog.records if "http_request" in r.message]
    static_logs = [r for r in timing_logs if "/static/" in r.message]
    assert len(static_logs) == 0, "Static asset requests should not be logged"


# ---------------------------------------------------------------------------
# 2. AI Call Tracking — structured log output
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_session():
    """In-memory DB session for AI usage tests."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with get_session(engine) as session:
        yield session

    await engine.dispose()


async def test_record_ai_usage_logs_structured_info(
    db_session: object, caplog: pytest.LogCaptureFixture
) -> None:
    """record_ai_usage() should emit a structured log line with all context."""
    with caplog.at_level(logging.INFO, logger="pinwheel.ai.usage"):
        await record_ai_usage(
            session=db_session,  # type: ignore[arg-type]
            call_type="commentary.game",
            model="claude-sonnet-4-6",
            input_tokens=1000,
            output_tokens=200,
            cache_read_tokens=50,
            cache_creation_tokens=0,
            latency_ms=1234.5,
            season_id="s-test",
            round_number=3,
        )

    ai_logs = [r for r in caplog.records if "ai_call" in r.message]
    assert len(ai_logs) == 1, "Expected exactly one ai_call log entry"

    msg = ai_logs[0].message
    assert "call_type=commentary.game" in msg
    assert "model=claude-sonnet-4-6" in msg
    assert "input_tokens=1000" in msg
    assert "output_tokens=200" in msg
    assert "cache_read_tokens=50" in msg
    assert "latency_ms=1234.5" in msg
    assert "season=s-test" in msg
    assert "round=3" in msg


async def test_record_ai_usage_log_cost(
    db_session: object, caplog: pytest.LogCaptureFixture
) -> None:
    """The log line should include the computed cost."""
    with caplog.at_level(logging.INFO, logger="pinwheel.ai.usage"):
        await record_ai_usage(
            session=db_session,  # type: ignore[arg-type]
            call_type="report.simulation",
            model="claude-sonnet-4-6",
            input_tokens=5000,
            output_tokens=500,
        )

    ai_logs = [r for r in caplog.records if "ai_call" in r.message]
    assert len(ai_logs) == 1
    assert "cost_usd=" in ai_logs[0].message


def test_compute_cost_known_model() -> None:
    """Cost computation for a known model uses correct rates."""
    cost = compute_cost(
        "claude-sonnet-4-6",
        input_tokens=1_000_000,
        output_tokens=0,
    )
    # $3.00 per million input tokens for Sonnet
    assert abs(cost - 3.0) < 0.01


# ---------------------------------------------------------------------------
# 3. Player Behavior Events — event bus publishing
# ---------------------------------------------------------------------------


async def test_event_bus_governor_vote_cast() -> None:
    """The governor.vote_cast event should include all required fields."""
    bus = EventBus()
    received: list[dict] = []

    async with bus.subscribe("governor.vote_cast") as sub:
        await bus.publish(
            "governor.vote_cast",
            {
                "governor_id": "gov-1",
                "team_id": "team-1",
                "proposal_id": "prop-1",
                "choice": "yes",
                "boost_used": False,
                "season_id": "s-1",
            },
        )
        event = await sub.get(timeout=1.0)
        assert event is not None
        received.append(event)

    assert len(received) == 1
    data = received[0]["data"]
    assert data["governor_id"] == "gov-1"
    assert data["choice"] == "yes"
    assert data["proposal_id"] == "prop-1"


async def test_event_bus_governor_proposal_submitted() -> None:
    """The governor.proposal_submitted event includes governor and proposal info."""
    bus = EventBus()

    async with bus.subscribe("governor.proposal_submitted") as sub:
        await bus.publish(
            "governor.proposal_submitted",
            {
                "governor_id": "gov-2",
                "team_id": "team-2",
                "proposal_id": "prop-2",
                "tier": 1,
                "token_cost": 1,
                "season_id": "s-1",
            },
        )
        event = await sub.get(timeout=1.0)

    assert event is not None
    assert event["data"]["governor_id"] == "gov-2"
    assert event["data"]["tier"] == 1


async def test_event_bus_governor_token_spent() -> None:
    """The governor.token_spent event includes token type and amount."""
    bus = EventBus()

    async with bus.subscribe("governor.token_spent") as sub:
        await bus.publish(
            "governor.token_spent",
            {
                "governor_id": "gov-1",
                "team_id": "team-1",
                "token_type": "boost",
                "amount": 1,
                "reason": "vote_boost",
                "season_id": "s-1",
            },
        )
        event = await sub.get(timeout=1.0)

    assert event is not None
    assert event["data"]["token_type"] == "boost"


async def test_event_bus_governor_strategy_set() -> None:
    """The governor.strategy_set event includes governor and team info."""
    bus = EventBus()

    async with bus.subscribe("governor.strategy_set") as sub:
        await bus.publish(
            "governor.strategy_set",
            {
                "governor_id": "gov-3",
                "team_id": "team-3",
                "season_id": "s-1",
            },
        )
        event = await sub.get(timeout=1.0)

    assert event is not None
    assert event["data"]["governor_id"] == "gov-3"
    assert event["data"]["team_id"] == "team-3"


# ---------------------------------------------------------------------------
# 4. Simulation Timing — phase logging
# ---------------------------------------------------------------------------


async def test_phase_timing_logs(caplog: pytest.LogCaptureFixture) -> None:
    """step_round should emit per-phase timing logs."""
    from pinwheel.core.game_loop import step_round
    from pinwheel.core.scheduler import generate_round_robin
    from pinwheel.db.repository import Repository

    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with get_session(engine) as session:
        repo = Repository(session)

        # Create league and season
        league = await repo.create_league("Test League")
        season = await repo.create_season(league.id, "Test Season")

        # Create two teams with 3 hoopers each
        attrs = {
            "scoring": 50, "passing": 40, "defense": 35,
            "speed": 45, "stamina": 40, "iq": 50,
            "ego": 30, "chaotic_alignment": 40, "fate": 30,
        }
        team_ids: list[str] = []
        for i, tname in enumerate(["Alpha", "Beta"]):
            team = await repo.create_team(
                season_id=season.id,
                name=tname,
                color="#FF0000" if i == 0 else "#0000FF",
                venue={"name": f"{tname} Arena", "capacity": 5000},
            )
            team_ids.append(team.id)
            for j in range(3):
                await repo.create_hooper(
                    name=f"{tname} Player {j}",
                    team_id=team.id,
                    season_id=season.id,
                    archetype="balanced",
                    attributes=attrs,
                )

        # Generate schedule
        schedule = generate_round_robin(team_ids)
        for entry in schedule:
            await repo.create_schedule_entry(
                season_id=season.id,
                round_number=entry.round_number,
                matchup_index=entry.matchup_index,
                home_team_id=entry.home_team_id,
                away_team_id=entry.away_team_id,
            )
        await session.commit()

        bus = EventBus()
        with caplog.at_level(logging.INFO, logger="pinwheel.core.game_loop"):
            await step_round(
                repo=repo,
                season_id=season.id,
                round_number=1,
                event_bus=bus,
                api_key="",  # mock mode
            )

    await engine.dispose()

    # Verify phase timing logs exist
    phase_logs = [r for r in caplog.records if "phase_timing" in r.message]
    assert len(phase_logs) >= 3, (
        f"Expected at least 3 phase_timing logs, got {len(phase_logs)}: "
        + str([r.message for r in phase_logs])
    )

    phase_names = [r.message for r in phase_logs]
    assert any("simulate_and_govern" in m for m in phase_names)
    assert any("ai_generation" in m for m in phase_names)
    assert any("persist_and_finalize" in m for m in phase_names)

    # Verify round_timing summary log
    round_timing_logs = [r for r in caplog.records if "round_timing" in r.message]
    assert len(round_timing_logs) >= 1, "Expected a round_timing summary log"
    summary = round_timing_logs[0].message
    assert "phase1_ms=" in summary
    assert "phase2_ms=" in summary
    assert "phase3_ms=" in summary
    assert "total_ms=" in summary
