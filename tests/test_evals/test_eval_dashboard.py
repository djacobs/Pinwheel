"""Tests for eval dashboard route and safety summary."""

import pytest
from httpx import ASGITransport, AsyncClient

from pinwheel.api.eval_dashboard import compute_safety_summary
from pinwheel.config import Settings
from pinwheel.core.event_bus import EventBus
from pinwheel.db.engine import create_engine
from pinwheel.db.models import Base
from pinwheel.main import create_app


@pytest.fixture
async def app_client():
    """Create a test app with in-memory database and httpx client (no OAuth)."""
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        pinwheel_env="development",
        pinwheel_evals_enabled=True,
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
        yield client

    await engine.dispose()


@pytest.fixture
async def oauth_app_client():
    """Create a test app with OAuth configured for auth gate testing.

    Uses staging env so check_admin_access enforces the auth gate
    (dev mode skips it for local testing convenience).
    """
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        pinwheel_env="staging",
        pinwheel_evals_enabled=True,
        discord_client_id="test-client-id",
        discord_client_secret="test-client-secret",
        session_secret_key="test-secret",
    )
    app = create_app(settings)

    engine = create_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    app.state.event_bus = EventBus()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    await engine.dispose()


@pytest.fixture
async def prod_app_client():
    """Create a production app for nav-link hiding test."""
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        pinwheel_env="production",
        session_secret_key="prod-secret-not-empty",
    )
    app = create_app(settings)

    engine = create_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    app.state.event_bus = EventBus()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    await engine.dispose()


@pytest.mark.asyncio
async def test_dashboard_empty(app_client):
    """Dashboard returns 200 even with no data."""
    resp = await app_client.get("/admin/evals")
    assert resp.status_code == 200
    assert "Evals Dashboard" in resp.text


@pytest.mark.asyncio
async def test_dashboard_no_report_text(app_client):
    """Dashboard must not contain any report text field references."""
    resp = await app_client.get("/admin/evals")
    assert "report_content" not in resp.text.lower()


@pytest.mark.asyncio
async def test_admin_nav_hidden_without_auth(app_client):
    """Admin nav link hidden when no user is authenticated."""
    resp = await app_client.get("/")
    assert 'href="/admin"' not in resp.text


@pytest.mark.asyncio
async def test_admin_nav_hidden_production(prod_app_client):
    """Admin nav link hidden in production when not authenticated."""
    resp = await prod_app_client.get("/")
    assert 'href="/admin"' not in resp.text


@pytest.mark.asyncio
async def test_dashboard_redirects_when_oauth_enabled(oauth_app_client):
    """Dashboard redirects to login when OAuth is enabled and user is not authenticated."""
    resp = await oauth_app_client.get("/admin/evals", follow_redirects=False)
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers.get("location", "")


@pytest.mark.asyncio
async def test_dashboard_accessible_without_oauth(app_client):
    """Dashboard is accessible without auth when OAuth is not configured."""
    resp = await app_client.get("/admin/evals")
    assert resp.status_code == 200


# --- compute_safety_summary unit tests ---


def _base_kwargs() -> dict:
    """Baseline kwargs for compute_safety_summary -- all clear."""
    return {
        "grounding_rate": 0.9,
        "grounding_total": 10,
        "prescriptive_flagged": 0,
        "injection_attempts": 0,
        "active_flags": [],
        "gqi_trend": [{"round": 1, "composite": 0.75}],
        "golden_pass_rate": 0.85,
    }


def test_safety_summary_green():
    """All metrics healthy produces green status."""
    result = compute_safety_summary(**_base_kwargs())
    assert result["status"] == "green"
    assert result["label"] == "All Clear"
    assert result["concerns"] == []
    assert result["total_evaluated"] == 10
    assert result["injection_attempts"] == 0
    assert result["gqi_score"] == 0.75


def test_safety_summary_yellow_warning_flag():
    """A single warning flag produces yellow status."""
    kwargs = _base_kwargs()
    kwargs["active_flags"] = [{"severity": "warning", "type": "test", "round": 1}]
    result = compute_safety_summary(**kwargs)
    assert result["status"] == "yellow"
    assert result["label"] == "Warnings Present"
    assert any("warning flag" in c for c in result["concerns"])


def test_safety_summary_yellow_injection():
    """A single injection attempt produces yellow status."""
    kwargs = _base_kwargs()
    kwargs["injection_attempts"] = 1
    result = compute_safety_summary(**kwargs)
    assert result["status"] == "yellow"
    assert any("injection" in c for c in result["concerns"])


def test_safety_summary_yellow_prescriptive():
    """Prescriptive flags produce yellow status."""
    kwargs = _base_kwargs()
    kwargs["prescriptive_flagged"] = 2
    result = compute_safety_summary(**kwargs)
    assert result["status"] == "yellow"
    assert any("prescriptive" in c for c in result["concerns"])


def test_safety_summary_yellow_low_grounding():
    """Low grounding rate produces yellow status."""
    kwargs = _base_kwargs()
    kwargs["grounding_rate"] = 0.3
    result = compute_safety_summary(**kwargs)
    assert result["status"] == "yellow"
    assert any("grounding" in c.lower() for c in result["concerns"])


def test_safety_summary_yellow_low_golden():
    """Low golden pass rate produces yellow status."""
    kwargs = _base_kwargs()
    kwargs["golden_pass_rate"] = 0.5
    result = compute_safety_summary(**kwargs)
    assert result["status"] == "yellow"
    assert any("golden" in c.lower() for c in result["concerns"])


def test_safety_summary_red_critical_flag():
    """A critical flag produces red status."""
    kwargs = _base_kwargs()
    kwargs["active_flags"] = [{"severity": "critical", "type": "test", "round": 1}]
    result = compute_safety_summary(**kwargs)
    assert result["status"] == "red"
    assert result["label"] == "Issues Detected"
    assert any("critical flag" in c for c in result["concerns"])


def test_safety_summary_red_many_injections():
    """Three or more injection attempts produce red status."""
    kwargs = _base_kwargs()
    kwargs["injection_attempts"] = 3
    result = compute_safety_summary(**kwargs)
    assert result["status"] == "red"
    assert result["label"] == "Issues Detected"


def test_safety_summary_no_gqi_data():
    """When no GQI data exists, gqi_score defaults to 0.0."""
    kwargs = _base_kwargs()
    kwargs["gqi_trend"] = []
    result = compute_safety_summary(**kwargs)
    assert result["gqi_score"] == 0.0


def test_safety_summary_eval_coverage():
    """Eval coverage reflects how many signal types have data."""
    kwargs = _base_kwargs()
    # grounding_total > 0 -> True
    # prescriptive (runs with grounding) -> True
    # gqi_trend -> True
    # golden_pass_rate > 0 -> True
    # active_flags -> False (empty)
    result = compute_safety_summary(**kwargs)
    assert result["eval_coverage_pct"] == 80.0  # 4 out of 5

    # All signals empty
    empty = compute_safety_summary(
        grounding_rate=0.0,
        grounding_total=0,
        prescriptive_flagged=0,
        injection_attempts=0,
        active_flags=[],
        gqi_trend=[],
        golden_pass_rate=0.0,
    )
    assert empty["eval_coverage_pct"] == 0.0


def test_safety_summary_no_grounding_data_skips_rate_concern():
    """When grounding_total is 0, low grounding rate is not flagged."""
    result = compute_safety_summary(
        grounding_rate=0.0,
        grounding_total=0,
        prescriptive_flagged=0,
        injection_attempts=0,
        active_flags=[],
        gqi_trend=[],
        golden_pass_rate=0.0,
    )
    assert result["status"] == "green"
    assert result["concerns"] == []


# --- Round drill-down tests ---


@pytest.fixture
async def seeded_app_client():
    """Create a test app with eval data across multiple rounds."""
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.orm import sessionmaker

    from pinwheel.db.repository import Repository

    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        pinwheel_env="development",
        pinwheel_evals_enabled=True,
        discord_client_id="",
        discord_client_secret="",
    )
    app = create_app(settings)

    engine = create_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    app.state.event_bus = EventBus()

    # Seed data: league, season, and eval results across rounds 1 and 2
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        repo = Repository(session)
        league = await repo.create_league("Test League")
        season = await repo.create_season(league.id, "Season 1")

        # Round 1: 2 grounding results (1 grounded, 1 not)
        await repo.store_eval_result(
            season_id=season.id,
            round_number=1,
            eval_type="grounding",
            score=1.0,
            details_json={"grounded": True},
        )
        await repo.store_eval_result(
            season_id=season.id,
            round_number=1,
            eval_type="grounding",
            score=0.0,
            details_json={"grounded": False},
        )

        # Round 2: 1 grounding result (grounded)
        await repo.store_eval_result(
            season_id=season.id,
            round_number=2,
            eval_type="grounding",
            score=1.0,
            details_json={"grounded": True},
        )

        # Round 1: prescriptive flag
        await repo.store_eval_result(
            season_id=season.id,
            round_number=1,
            eval_type="prescriptive",
            score=1.0,
            details_json={"flagged": True, "count": 3},
        )

        # Round 2: no prescriptive flags
        await repo.store_eval_result(
            season_id=season.id,
            round_number=2,
            eval_type="prescriptive",
            score=0.0,
            details_json={"flagged": False, "count": 0},
        )

        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    await engine.dispose()


@pytest.mark.asyncio
async def test_dashboard_no_round_shows_all(seeded_app_client):
    """Without ?round param, dashboard shows aggregate data from all rounds."""
    resp = await seeded_app_client.get("/admin/evals")
    assert resp.status_code == 200
    # Should show all 3 grounding results
    assert "3 reports checked" in resp.text
    # All Rounds label should be shown
    assert "All Rounds" in resp.text


@pytest.mark.asyncio
async def test_dashboard_round_filter(seeded_app_client):
    """With ?round=1, dashboard shows only round 1 data."""
    resp = await seeded_app_client.get("/admin/evals?round=1")
    assert resp.status_code == 200
    # Round 1 has 2 grounding results
    assert "2 reports checked" in resp.text
    # Should show "Round 1" label
    assert "Round 1" in resp.text


@pytest.mark.asyncio
async def test_dashboard_round_filter_round2(seeded_app_client):
    """With ?round=2, dashboard shows only round 2 data."""
    resp = await seeded_app_client.get("/admin/evals?round=2")
    assert resp.status_code == 200
    # Round 2 has 1 grounding result
    assert "1 reports checked" in resp.text
    assert "Round 2" in resp.text


@pytest.mark.asyncio
async def test_dashboard_round_navigation_links(seeded_app_client):
    """Round navigation shows prev/next links."""
    # On round 1, should have next but no prev
    resp = await seeded_app_client.get("/admin/evals?round=1")
    assert resp.status_code == 200
    assert "round=2" in resp.text  # next link
    assert "All Rounds" in resp.text  # back-to-all link

    # On round 2, should have prev but no next
    resp = await seeded_app_client.get("/admin/evals?round=2")
    assert resp.status_code == 200
    assert "round=1" in resp.text  # prev link


@pytest.mark.asyncio
async def test_dashboard_round_dropdown(seeded_app_client):
    """Round dropdown contains available rounds."""
    resp = await seeded_app_client.get("/admin/evals")
    assert resp.status_code == 200
    # Dropdown should have Round 1 and Round 2 options
    assert "Round 1" in resp.text
    assert "Round 2" in resp.text


@pytest.mark.asyncio
async def test_dashboard_invalid_round_param(seeded_app_client):
    """Invalid round param is ignored and shows all data."""
    resp = await seeded_app_client.get("/admin/evals?round=abc")
    assert resp.status_code == 200
    # Should show all 3 grounding results (invalid param ignored)
    assert "3 reports checked" in resp.text


@pytest.mark.asyncio
async def test_dashboard_negative_round_param(seeded_app_client):
    """Negative round param is ignored."""
    resp = await seeded_app_client.get("/admin/evals?round=-1")
    assert resp.status_code == 200
    assert "3 reports checked" in resp.text


@pytest.mark.asyncio
async def test_dashboard_nonexistent_round(seeded_app_client):
    """A valid round number with no data still returns 200."""
    resp = await seeded_app_client.get("/admin/evals?round=99")
    assert resp.status_code == 200
    # Should show 0 reports checked
    assert "0 reports checked" in resp.text


@pytest.mark.asyncio
async def test_dashboard_round_prescriptive_filter(seeded_app_client):
    """Prescriptive flags are filtered by round."""
    # Round 1 has 1 prescriptive flag
    resp = await seeded_app_client.get("/admin/evals?round=1")
    assert resp.status_code == 200
    assert "3 total directive phrases" in resp.text

    # Round 2 has 0 prescriptive flags
    resp = await seeded_app_client.get("/admin/evals?round=2")
    assert resp.status_code == 200
    assert "0 total directive phrases" in resp.text


# --- _parse_round_param unit tests ---


def test_parse_round_param_none():
    """No round param returns None."""
    from unittest.mock import MagicMock

    from pinwheel.api.eval_dashboard import _parse_round_param

    request = MagicMock()
    request.query_params = {}
    assert _parse_round_param(request) is None


def test_parse_round_param_valid():
    """Valid integer round param is parsed."""
    from unittest.mock import MagicMock

    from pinwheel.api.eval_dashboard import _parse_round_param

    request = MagicMock()
    request.query_params = {"round": "3"}
    assert _parse_round_param(request) == 3


def test_parse_round_param_invalid():
    """Non-integer round param returns None."""
    from unittest.mock import MagicMock

    from pinwheel.api.eval_dashboard import _parse_round_param

    request = MagicMock()
    request.query_params = {"round": "abc"}
    assert _parse_round_param(request) is None


def test_parse_round_param_negative():
    """Negative round param returns None."""
    from unittest.mock import MagicMock

    from pinwheel.api.eval_dashboard import _parse_round_param

    request = MagicMock()
    request.query_params = {"round": "-1"}
    assert _parse_round_param(request) is None


def test_parse_round_param_zero():
    """Zero round param is valid (round 0 can exist)."""
    from unittest.mock import MagicMock

    from pinwheel.api.eval_dashboard import _parse_round_param

    request = MagicMock()
    request.query_params = {"round": "0"}
    assert _parse_round_param(request) == 0
