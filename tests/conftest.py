"""Shared test fixtures."""

import pytest

from pinwheel.config import Settings


@pytest.fixture
def settings() -> Settings:
    """Test settings with defaults."""
    return Settings(pinwheel_env="development", database_url="sqlite+aiosqlite:///:memory:")
