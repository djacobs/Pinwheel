"""Application settings via pydantic-settings. Loads from environment and .env file."""

from pydantic_settings import BaseSettings


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
    pinwheel_game_cron: str = "*/2 * * * *"
    pinwheel_gov_window: int = 120
    pinwheel_presentation_pace: str = "fast"

    # Logging
    pinwheel_log_level: str = "INFO"

    model_config = {"env_prefix": "", "env_file": ".env", "extra": "ignore"}
