"""Tests for AI cost tracking: usage recording, cost computation, and dashboard route."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pinwheel.ai.usage import PRICING, compute_cost, extract_usage, record_ai_usage
from pinwheel.config import Settings
from pinwheel.core.event_bus import EventBus
from pinwheel.db.engine import create_engine
from pinwheel.db.models import AIUsageLogRow, Base
from pinwheel.main import create_app

# ---------------------------------------------------------------------------
# Cost computation tests (pure functions, no DB)
# ---------------------------------------------------------------------------


class TestComputeCost:
    """Test the cost computation function."""

    def test_sonnet_cost(self) -> None:
        """Sonnet pricing: $3/MTok input, $15/MTok output."""
        cost = compute_cost(
            model="claude-sonnet-4-5-20250929",
            input_tokens=1000,
            output_tokens=500,
        )
        expected = (1000 * 3.00 + 500 * 15.00) / 1_000_000
        assert abs(cost - expected) < 1e-8

    def test_haiku_cost(self) -> None:
        """Haiku pricing: $0.80/MTok input, $4/MTok output."""
        cost = compute_cost(
            model="claude-haiku-4-5-20251001",
            input_tokens=2000,
            output_tokens=200,
        )
        expected = (2000 * 0.80 + 200 * 4.00) / 1_000_000
        assert abs(cost - expected) < 1e-8

    def test_cache_read_tokens(self) -> None:
        """Cache read tokens use a lower rate."""
        cost = compute_cost(
            model="claude-sonnet-4-5-20250929",
            input_tokens=500,
            output_tokens=200,
            cache_read_tokens=1000,
        )
        expected = (500 * 3.00 + 200 * 15.00 + 1000 * 0.30) / 1_000_000
        assert abs(cost - expected) < 1e-8

    def test_unknown_model_uses_default(self) -> None:
        """Unknown models fall back to default pricing."""
        cost = compute_cost(
            model="claude-unknown-model",
            input_tokens=1000,
            output_tokens=500,
        )
        # Default is same as sonnet
        expected = (1000 * 3.00 + 500 * 15.00) / 1_000_000
        assert abs(cost - expected) < 1e-8

    def test_zero_tokens(self) -> None:
        """Zero tokens should produce zero cost."""
        cost = compute_cost("claude-sonnet-4-5-20250929", 0, 0, 0)
        assert cost == 0.0


# ---------------------------------------------------------------------------
# extract_usage tests
# ---------------------------------------------------------------------------


class TestExtractUsage:
    """Test extracting token counts from API responses."""

    def test_standard_response(self) -> None:
        """Extract tokens from a normal response object."""
        response = MagicMock()
        response.usage.input_tokens = 150
        response.usage.output_tokens = 300
        response.usage.cache_read_input_tokens = 50
        response.usage.cache_creation_input_tokens = 0
        inp, out, cache, cache_create = extract_usage(response)
        assert inp == 150
        assert out == 300
        assert cache == 50
        assert cache_create == 0

    def test_no_usage_attribute(self) -> None:
        """Return zeros when response has no usage attribute."""
        response = object()  # No .usage attribute
        inp, out, cache, cache_create = extract_usage(response)
        assert (inp, out, cache, cache_create) == (0, 0, 0, 0)

    def test_missing_cache_field(self) -> None:
        """Handle missing cache_read_input_tokens gracefully."""
        response = MagicMock()
        response.usage.input_tokens = 100
        response.usage.output_tokens = 200
        response.usage.cache_read_input_tokens = None
        response.usage.cache_creation_input_tokens = None
        inp, out, cache, cache_create = extract_usage(response)
        assert inp == 100
        assert out == 200
        assert cache == 0
        assert cache_create == 0

    def test_cache_creation_tokens(self) -> None:
        """Extract cache_creation_input_tokens from first cache write."""
        response = MagicMock()
        response.usage.input_tokens = 200
        response.usage.output_tokens = 100
        response.usage.cache_read_input_tokens = 0
        response.usage.cache_creation_input_tokens = 1500
        inp, out, cache, cache_create = extract_usage(response)
        assert inp == 200
        assert out == 100
        assert cache == 0
        assert cache_create == 1500


# ---------------------------------------------------------------------------
# record_ai_usage tests (require DB)
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_session():
    """Create an in-memory DB with the AIUsageLogRow table and yield a session."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from pinwheel.db.engine import create_session_factory

    factory = create_session_factory(engine)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_record_ai_usage(db_session: AsyncSession) -> None:
    """Recording AI usage inserts a row with correct values."""
    row = await record_ai_usage(
        session=db_session,
        call_type="report.simulation",
        model="claude-sonnet-4-5-20250929",
        input_tokens=1000,
        output_tokens=500,
        cache_read_tokens=200,
        latency_ms=1234.5,
        season_id="season-1",
        round_number=3,
    )
    await db_session.commit()

    assert row.id is not None
    assert row.call_type == "report.simulation"
    assert row.model == "claude-sonnet-4-5-20250929"
    assert row.input_tokens == 1000
    assert row.output_tokens == 500
    assert row.cache_read_tokens == 200
    assert row.latency_ms == 1234.5
    assert row.season_id == "season-1"
    assert row.round_number == 3
    assert row.cost_usd > 0

    # Verify cost matches compute_cost
    expected_cost = compute_cost("claude-sonnet-4-5-20250929", 1000, 500, 200)
    assert abs(row.cost_usd - expected_cost) < 1e-8


@pytest.mark.asyncio
async def test_record_ai_usage_multiple(db_session: AsyncSession) -> None:
    """Multiple usage records can be inserted and queried."""
    for i in range(3):
        await record_ai_usage(
            session=db_session,
            call_type=f"test.call.{i}",
            model="claude-sonnet-4-5-20250929",
            input_tokens=100 * (i + 1),
            output_tokens=50 * (i + 1),
            season_id="season-1",
            round_number=i + 1,
        )
    await db_session.commit()

    result = await db_session.execute(select(AIUsageLogRow))
    rows = result.scalars().all()
    assert len(rows) == 3


@pytest.mark.asyncio
async def test_record_ai_usage_null_round(db_session: AsyncSession) -> None:
    """Round number can be None (for on-demand calls like interpreter)."""
    row = await record_ai_usage(
        session=db_session,
        call_type="interpreter.v1",
        model="claude-sonnet-4-5-20250929",
        input_tokens=500,
        output_tokens=100,
        season_id="season-1",
        round_number=None,
    )
    await db_session.commit()
    assert row.round_number is None


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
async def test_costs_dashboard_empty(app_client: tuple) -> None:
    """Dashboard renders with no data."""
    client, engine = app_client
    resp = await client.get("/admin/costs")
    assert resp.status_code == 200
    assert "AI Cost Dashboard" in resp.text
    assert "No AI usage data" in resp.text


@pytest.mark.asyncio
async def test_costs_dashboard_with_data(app_client: tuple) -> None:
    """Dashboard renders with usage data: summary and tables."""
    client, engine = app_client

    # Seed a season and usage data
    from pinwheel.db.engine import get_session
    from pinwheel.db.models import LeagueRow, SeasonRow

    async with get_session(engine) as session:
        league = LeagueRow(name="Test League")
        session.add(league)
        await session.flush()

        season = SeasonRow(league_id=league.id, name="Season 1", status="active")
        session.add(season)
        await session.flush()
        season_id = season.id

        # Insert usage records
        for i in range(3):
            row = AIUsageLogRow(
                call_type="report.simulation" if i < 2 else "commentary.game",
                model="claude-sonnet-4-5-20250929",
                input_tokens=1000 * (i + 1),
                output_tokens=500 * (i + 1),
                cache_read_tokens=0,
                latency_ms=500.0 + i * 100,
                cost_usd=compute_cost(
                    "claude-sonnet-4-5-20250929",
                    1000 * (i + 1),
                    500 * (i + 1),
                ),
                season_id=season_id,
                round_number=i + 1,
            )
            session.add(row)
        await session.flush()

    resp = await client.get("/admin/costs")
    assert resp.status_code == 200
    text = resp.text

    # Should show summary data
    assert "AI Cost Dashboard" in text
    assert "API Calls" in text
    assert "Total Tokens" in text
    assert "Spend by Call Type" in text
    assert "report.simulation" in text
    assert "commentary.game" in text
    assert "Cost per Round" in text
    assert "Round 1" in text


@pytest.mark.asyncio
async def test_costs_dashboard_auth_redirect(app_client: tuple) -> None:
    """With OAuth configured, unauthenticated users get redirected."""
    # Create app with OAuth enabled — use staging so check_admin_access
    # enforces the gate (dev mode skips it for local testing convenience).
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
        resp = await oauth_client.get("/admin/costs")
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers.get("location", "")

    await engine.dispose()


# ---------------------------------------------------------------------------
# Pricing reference test
# ---------------------------------------------------------------------------


def test_pricing_dict_has_known_models() -> None:
    """Pricing dict should include the models we actually use."""
    assert "claude-sonnet-4-5-20250929" in PRICING
    assert "claude-haiku-4-5-20251001" in PRICING
    for _model, rates in PRICING.items():
        assert "input_per_mtok" in rates
        assert "output_per_mtok" in rates
        assert "cache_read_per_mtok" in rates
