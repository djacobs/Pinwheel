"""Tests for application configuration."""

from pinwheel.config import Settings


class TestProductionReplayMode:
    def test_production_forces_replay(self) -> None:
        """In production, presentation_mode should be forced to 'replay'."""
        settings = Settings(
            pinwheel_env="production",
            session_secret_key="test-secret-key-for-production-tests",
            pinwheel_presentation_mode="instant",
            database_url="sqlite+aiosqlite:///:memory:",
        )
        assert settings.pinwheel_presentation_mode == "replay"

    def test_development_allows_instant(self) -> None:
        """In development, 'instant' mode should remain unchanged."""
        settings = Settings(
            pinwheel_env="development",
            pinwheel_presentation_mode="instant",
            database_url="sqlite+aiosqlite:///:memory:",
        )
        assert settings.pinwheel_presentation_mode == "instant"

    def test_development_allows_replay(self) -> None:
        """In development, 'replay' mode should remain unchanged."""
        settings = Settings(
            pinwheel_env="development",
            pinwheel_presentation_mode="replay",
            database_url="sqlite+aiosqlite:///:memory:",
        )
        assert settings.pinwheel_presentation_mode == "replay"

    def test_production_replay_stays_replay(self) -> None:
        """In production, 'replay' mode should remain unchanged."""
        settings = Settings(
            pinwheel_env="production",
            session_secret_key="test-secret-key-for-production-tests",
            pinwheel_presentation_mode="replay",
            database_url="sqlite+aiosqlite:///:memory:",
        )
        assert settings.pinwheel_presentation_mode == "replay"
