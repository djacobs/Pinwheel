"""Tests for eval dashboard route."""

import pytest
from httpx import ASGITransport, AsyncClient

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
    """Create a test app with OAuth configured for auth gate testing."""
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        pinwheel_env="development",
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
async def test_dashboard_no_mirror_text(app_client):
    """Dashboard must not contain any mirror text field references."""
    resp = await app_client.get("/admin/evals")
    assert "mirror_content" not in resp.text.lower()


@pytest.mark.asyncio
async def test_nav_link_dev(app_client):
    """Evals nav link appears in dev environment."""
    resp = await app_client.get("/")
    assert "/admin/evals" in resp.text


@pytest.mark.asyncio
async def test_nav_link_production(prod_app_client):
    """Evals nav link hidden in production."""
    resp = await prod_app_client.get("/")
    assert "/admin/evals" not in resp.text


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
