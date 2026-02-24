"""Tests for the performance dashboard at /admin/perf."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from pinwheel.ai.usage import compute_cost
from pinwheel.api.admin_perf import _compute_percentiles, _format_duration
from pinwheel.config import Settings
from pinwheel.core.event_bus import EventBus
from pinwheel.db.engine import create_engine
from pinwheel.db.models import (
    AIUsageLogRow,
    Base,
    GameResultRow,
    GovernanceEventRow,
    LeagueRow,
    ReportRow,
    SeasonRow,
    TeamRow,
)
from pinwheel.main import create_app

# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------


class TestComputePercentiles:
    """Test the percentile computation helper."""

    def test_empty_list(self) -> None:
        result = _compute_percentiles([])
        assert result == {"p50": 0.0, "p95": 0.0, "p99": 0.0}

    def test_single_value(self) -> None:
        result = _compute_percentiles([42.0])
        assert result["p50"] == 42.0
        assert result["p95"] == 42.0
        assert result["p99"] == 42.0

    def test_two_values(self) -> None:
        result = _compute_percentiles([10.0, 20.0])
        # With only 2 values, P50 maps to index 0, P95/P99 also map to index 0
        assert result["p50"] == 10.0
        # Verify all percentiles return valid values from the input
        assert result["p95"] in (10.0, 20.0)
        assert result["p99"] in (10.0, 20.0)

    def test_hundred_values(self) -> None:
        """With 100 sequential values, percentiles should land at expected positions."""
        values = [float(i) for i in range(100)]
        result = _compute_percentiles(values)
        # P50 of 0..99 should be ~49
        assert 48.0 <= result["p50"] <= 50.0
        # P95 should be ~94
        assert 93.0 <= result["p95"] <= 95.0
        # P99 should be ~98
        assert 97.0 <= result["p99"] <= 99.0

    def test_unsorted_input(self) -> None:
        """Function sorts internally, so unsorted input should work."""
        values = [100.0, 1.0, 50.0, 25.0, 75.0]
        result = _compute_percentiles(values)
        assert result["p50"] == 50.0


class TestFormatDuration:
    """Test the duration formatting helper."""

    def test_seconds(self) -> None:
        assert _format_duration(30) == "30s"

    def test_minutes(self) -> None:
        assert _format_duration(120) == "2m"

    def test_hours(self) -> None:
        assert _format_duration(7200) == "2.0h"

    def test_days(self) -> None:
        assert _format_duration(172800) == "2.0d"


# ---------------------------------------------------------------------------
# Dashboard route tests
# ---------------------------------------------------------------------------


@pytest.fixture
async def app_client():
    """Create a test app with in-memory database and httpx client (no OAuth)."""
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        pinwheel_env="development",
        discord_client_id="",
        discord_client_secret="",
    )
    app = create_app(settings)

    engine = create_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    app.state.event_bus = EventBus()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, engine

    await engine.dispose()


@pytest.mark.asyncio
async def test_perf_dashboard_empty(app_client: tuple) -> None:
    """Dashboard renders with no data."""
    client, engine = app_client
    resp = await client.get("/admin/perf")
    assert resp.status_code == 200
    assert "Performance Dashboard" in resp.text
    assert "No performance data yet" in resp.text


@pytest.mark.asyncio
async def test_perf_dashboard_with_data(app_client: tuple) -> None:
    """Dashboard renders with game and AI usage data."""
    client, engine = app_client

    from pinwheel.db.engine import get_session

    async with get_session(engine) as session:
        league = LeagueRow(name="Test League")
        session.add(league)
        await session.flush()

        season = SeasonRow(league_id=league.id, name="Season 1", status="active")
        session.add(season)
        await session.flush()
        season_id = season.id

        # Create two teams
        team_a = TeamRow(season_id=season_id, name="Team A")
        team_b = TeamRow(season_id=season_id, name="Team B")
        session.add_all([team_a, team_b])
        await session.flush()

        # Insert game results for 2 rounds
        for rn in range(1, 3):
            game = GameResultRow(
                season_id=season_id,
                round_number=rn,
                matchup_index=0,
                home_team_id=team_a.id,
                away_team_id=team_b.id,
                home_score=30,
                away_score=25,
                winner_team_id=team_a.id,
                seed=42,
                total_possessions=60,
            )
            session.add(game)

        # Insert AI usage records with varying latency
        for i in range(5):
            row = AIUsageLogRow(
                call_type="report.simulation" if i < 3 else "commentary.game",
                model="claude-sonnet-4-5-20250929",
                input_tokens=1000 * (i + 1),
                output_tokens=500 * (i + 1),
                cache_read_tokens=0,
                latency_ms=500.0 + i * 200,
                cost_usd=compute_cost(
                    "claude-sonnet-4-5-20250929",
                    1000 * (i + 1),
                    500 * (i + 1),
                ),
                season_id=season_id,
                round_number=(i % 2) + 1,
            )
            session.add(row)

        # Insert a report
        report = ReportRow(
            season_id=season_id,
            round_number=1,
            report_type="simulation",
            content="Test report content",
        )
        session.add(report)

        # Insert a governance event
        gov = GovernanceEventRow(
            season_id=season_id,
            round_number=1,
            event_type="proposal_submitted",
            aggregate_id="prop-1",
            aggregate_type="proposal",
            governor_id="test-gov",
            payload={"text": "test proposal"},
            sequence_number=1,
        )
        session.add(gov)

        await session.flush()

    resp = await client.get("/admin/perf")
    assert resp.status_code == 200
    text = resp.text

    # Should show summary data
    assert "Performance Dashboard" in text
    assert "Games Played" in text
    assert "Rounds Completed" in text
    assert "AI Calls" in text
    assert "Reports Generated" in text
    assert "Uptime" in text

    # Should show AI latency section
    assert "AI Call Latency" in text
    assert "P50" in text
    assert "P95" in text
    assert "P99" in text

    # Should show latency by type
    assert "AI Latency by Call Type" in text
    assert "report.simulation" in text
    assert "commentary.game" in text

    # Should show SSE section
    assert "SSE Connections" in text

    # Should show round timing table
    assert "Recent Rounds" in text
    assert "Round 1" in text
    assert "Round 2" in text


@pytest.mark.asyncio
async def test_perf_dashboard_auth_redirect() -> None:
    """With OAuth configured, unauthenticated users get redirected."""
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        pinwheel_env="staging",
        discord_client_id="test-id",
        discord_client_secret="test-secret",
        session_secret_key="test-key",
    )
    app = create_app(settings)

    engine = create_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    app.state.event_bus = EventBus()

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        follow_redirects=False,
    ) as oauth_client:
        resp = await oauth_client.get("/admin/perf")
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers.get("location", "")

    await engine.dispose()


@pytest.mark.asyncio
async def test_perf_dashboard_games_only(app_client: tuple) -> None:
    """Dashboard renders when there are games but no AI usage data."""
    client, engine = app_client

    from pinwheel.db.engine import get_session

    async with get_session(engine) as session:
        league = LeagueRow(name="Test League")
        session.add(league)
        await session.flush()

        season = SeasonRow(league_id=league.id, name="Season 1", status="active")
        session.add(season)
        await session.flush()
        season_id = season.id

        team_a = TeamRow(season_id=season_id, name="Team A")
        team_b = TeamRow(season_id=season_id, name="Team B")
        session.add_all([team_a, team_b])
        await session.flush()

        game = GameResultRow(
            season_id=season_id,
            round_number=1,
            matchup_index=0,
            home_team_id=team_a.id,
            away_team_id=team_b.id,
            home_score=30,
            away_score=25,
            winner_team_id=team_a.id,
            seed=42,
            total_possessions=60,
        )
        session.add(game)
        await session.flush()

    resp = await client.get("/admin/perf")
    assert resp.status_code == 200
    text = resp.text
    assert "Performance Dashboard" in text
    assert "Games Played" in text
    # No AI latency section since no AI data
    assert "No performance data yet" not in text


@pytest.mark.asyncio
async def test_perf_dashboard_ai_only(app_client: tuple) -> None:
    """Dashboard renders when there are AI calls but no games."""
    client, engine = app_client

    from pinwheel.db.engine import get_session

    async with get_session(engine) as session:
        league = LeagueRow(name="Test League")
        session.add(league)
        await session.flush()

        season = SeasonRow(league_id=league.id, name="Season 1", status="active")
        session.add(season)
        await session.flush()
        season_id = season.id

        row = AIUsageLogRow(
            call_type="report.simulation",
            model="claude-sonnet-4-5-20250929",
            input_tokens=1000,
            output_tokens=500,
            cache_read_tokens=0,
            latency_ms=750.0,
            cost_usd=compute_cost("claude-sonnet-4-5-20250929", 1000, 500),
            season_id=season_id,
            round_number=1,
        )
        session.add(row)
        await session.flush()

    resp = await client.get("/admin/perf")
    assert resp.status_code == 200
    text = resp.text
    assert "Performance Dashboard" in text
    assert "AI Calls" in text
    assert "No performance data yet" not in text
