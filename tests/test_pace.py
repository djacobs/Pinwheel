"""Tests for presenter pacing modes — pace-to-cron mapping, effective_game_cron, and API."""

import pytest
from httpx import ASGITransport, AsyncClient

from pinwheel.config import _DEFAULT_GAME_CRON, PACE_CRON_MAP, VALID_PACES, Settings
from pinwheel.main import create_app

# ---------------------------------------------------------------------------
# Pace-to-cron mapping
# ---------------------------------------------------------------------------


class TestPaceCronMap:
    """Verify the static PACE_CRON_MAP has the expected entries."""

    def test_fast_maps_to_every_minute(self) -> None:
        assert PACE_CRON_MAP["fast"] == "*/1 * * * *"

    def test_normal_maps_to_every_5_minutes(self) -> None:
        assert PACE_CRON_MAP["normal"] == "*/5 * * * *"

    def test_slow_maps_to_every_15_minutes(self) -> None:
        assert PACE_CRON_MAP["slow"] == "*/15 * * * *"

    def test_manual_maps_to_none(self) -> None:
        assert PACE_CRON_MAP["manual"] is None

    def test_valid_paces_contains_all_keys(self) -> None:
        assert {"fast", "normal", "slow", "manual"} == VALID_PACES


# ---------------------------------------------------------------------------
# effective_game_cron()
# ---------------------------------------------------------------------------


class TestEffectiveGameCron:
    """Test the Settings.effective_game_cron resolution logic."""

    def test_default_cron_with_fast_pace(self) -> None:
        s = Settings(pinwheel_presentation_pace="fast")
        assert s.effective_game_cron() == "*/1 * * * *"

    def test_default_cron_with_normal_pace(self) -> None:
        s = Settings(pinwheel_presentation_pace="normal")
        assert s.effective_game_cron() == "*/5 * * * *"

    def test_default_cron_with_slow_pace(self) -> None:
        s = Settings(pinwheel_presentation_pace="slow")
        assert s.effective_game_cron() == "*/15 * * * *"

    def test_default_cron_with_manual_pace(self) -> None:
        s = Settings(pinwheel_presentation_pace="manual")
        assert s.effective_game_cron() is None

    def test_explicit_cron_overrides_pace(self) -> None:
        """When the user explicitly sets pinwheel_game_cron, it wins."""
        s = Settings(
            pinwheel_game_cron="0 */3 * * *",
            pinwheel_presentation_pace="fast",
        )
        assert s.effective_game_cron() == "0 */3 * * *"

    def test_explicit_cron_same_as_default_uses_pace(self) -> None:
        """If cron is still the default, pace takes precedence."""
        s = Settings(
            pinwheel_game_cron=_DEFAULT_GAME_CRON,
            pinwheel_presentation_pace="slow",
        )
        assert s.effective_game_cron() == "*/15 * * * *"

    def test_unknown_pace_returns_none(self) -> None:
        """An unrecognised pace falls through dict.get → None."""
        s = Settings(pinwheel_presentation_pace="turbo")
        assert s.effective_game_cron() is None


# ---------------------------------------------------------------------------
# /api/pace endpoints
# ---------------------------------------------------------------------------


@pytest.fixture
def test_app() -> "AsyncClient":
    """Create a lightweight test app with auto_advance=False to skip APScheduler."""
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        pinwheel_auto_advance=False,
        pinwheel_presentation_pace="fast",
    )
    return create_app(settings)


class TestPaceAPI:
    """Test the GET and POST /api/pace endpoints."""

    async def test_get_pace_returns_current(self, test_app) -> None:
        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/pace")
            assert resp.status_code == 200
            data = resp.json()
            assert data["pace"] == "fast"
            assert data["cron"] == "*/1 * * * *"
            assert data["auto_advance"] is True

    async def test_post_pace_changes_to_normal(self, test_app) -> None:
        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/pace", json={"pace": "normal"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["pace"] == "normal"
            assert data["cron"] == "*/5 * * * *"
            assert data["auto_advance"] is True

    async def test_post_pace_manual_disables_auto_advance(self, test_app) -> None:
        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/pace", json={"pace": "manual"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["pace"] == "manual"
            assert data["cron"] is None
            assert data["auto_advance"] is False

    async def test_post_pace_persists_in_memory(self, test_app) -> None:
        """POST changes are reflected in subsequent GET."""
        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/api/pace", json={"pace": "slow"})
            resp = await client.get("/api/pace")
            assert resp.status_code == 200
            assert resp.json()["pace"] == "slow"
            assert resp.json()["cron"] == "*/15 * * * *"

    async def test_post_invalid_pace_returns_422(self, test_app) -> None:
        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/pace", json={"pace": "turbo"})
            assert resp.status_code == 422
            assert "turbo" in resp.json()["detail"]

    async def test_post_missing_pace_field_returns_422(self, test_app) -> None:
        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/pace", json={})
            assert resp.status_code == 422
