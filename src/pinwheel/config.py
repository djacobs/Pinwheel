"""Application settings via pydantic-settings. Loads from environment and .env file."""

from __future__ import annotations

from pydantic_settings import BaseSettings

# Default cron expression — used to detect whether the user explicitly overrode it.
_DEFAULT_GAME_CRON = "*/2 * * * *"

# Pace-to-cron mapping for presenter pacing modes.
PACE_CRON_MAP: dict[str, str | None] = {
    "fast": "*/1 * * * *",
    "normal": "*/5 * * * *",
    "slow": "*/15 * * * *",
    "manual": None,
}

VALID_PACES = frozenset(PACE_CRON_MAP.keys())


class Settings(BaseSettings):
    """Pinwheel application configuration.

    All values can be overridden via environment variables or .env file.
    See docs/DEMO_MODE.md for per-environment defaults.
    """

    # External services
    anthropic_api_key: str = ""
    discord_bot_token: str = ""
    discord_guild_id: str = ""
    discord_channel_id: str = ""
    discord_enabled: bool = False

    # Discord OAuth2
    discord_client_id: str = ""
    discord_client_secret: str = ""
    discord_redirect_uri: str = "http://localhost:8000/auth/callback"
    session_secret_key: str = "pinwheel-dev-secret-change-in-production"

    # Database
    database_url: str = "sqlite+aiosqlite:///pinwheel.db"

    # Environment
    pinwheel_env: str = "development"

    # Scheduling & pacing
    pinwheel_game_cron: str = _DEFAULT_GAME_CRON
    pinwheel_auto_advance: bool = True
    pinwheel_gov_window: int = 120
    pinwheel_presentation_pace: str = "fast"

    # Evals
    pinwheel_evals_enabled: bool = True

    # Logging
    pinwheel_log_level: str = "INFO"

    model_config = {"env_prefix": "", "env_file": ".env", "extra": "ignore"}

    def effective_game_cron(self) -> str | None:
        """Return the cron expression that should drive game scheduling.

        Resolution order:
        1. If ``pinwheel_game_cron`` was explicitly changed from its default,
           honour the user override.
        2. Otherwise, derive from ``pinwheel_presentation_pace``.
        3. If pace is ``"manual"``, return ``None`` — the scheduler should not
           start an automatic job.
        """
        if self.pinwheel_game_cron != _DEFAULT_GAME_CRON:
            return self.pinwheel_game_cron

        return PACE_CRON_MAP.get(self.pinwheel_presentation_pace)
