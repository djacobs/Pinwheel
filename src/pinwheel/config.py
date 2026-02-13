"""Application settings via pydantic-settings. Loads from environment and .env file."""

from __future__ import annotations

import pathlib
import secrets

from pydantic import model_validator
from pydantic_settings import BaseSettings


def _find_project_root() -> pathlib.Path:
    """Find the project root containing templates/ and static/ directories.

    In development: src/pinwheel/config.py → up 3 levels → project root.
    In Docker: installed package is in site-packages, but templates/ is at /app/.
    """
    source_root = pathlib.Path(__file__).resolve().parent.parent.parent
    if (source_root / "templates").exists():
        return source_root
    docker_root = pathlib.Path("/app")
    if (docker_root / "templates").exists():
        return docker_root
    return source_root


PROJECT_ROOT = _find_project_root()


def _get_app_version() -> str:
    """Return a short version string for cache busting (git hash or fallback)."""
    import subprocess

    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=str(PROJECT_ROOT),
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
    except Exception:
        # In Docker or without git, use a timestamp-based fallback
        import hashlib
        import time

        return hashlib.md5(str(int(time.time() / 3600)).encode()).hexdigest()[:7]


APP_VERSION = _get_app_version()

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
    discord_invite_url: str = ""
    session_secret_key: str = ""

    # Database
    database_url: str = "sqlite+aiosqlite:///pinwheel.db"

    # Environment
    pinwheel_env: str = "development"

    # Scheduling & pacing
    pinwheel_game_cron: str = _DEFAULT_GAME_CRON
    pinwheel_auto_advance: bool = True
    pinwheel_gov_window: int = 900
    pinwheel_presentation_pace: str = "fast"
    pinwheel_presentation_mode: str = "instant"  # "instant" or "replay"
    pinwheel_game_interval_seconds: int = 1800  # 30 min between games in replay mode
    pinwheel_quarter_replay_seconds: int = 300  # 5 min per quarter in replay mode

    # Governance
    pinwheel_governance_interval: int = 3  # Tally governance every N rounds
    pinwheel_admin_discord_id: str = ""  # Discord user ID for admin review notifications

    # Seasons
    pinwheel_carry_forward_rules: bool = False  # Default: fresh rules each season

    # Evals
    pinwheel_evals_enabled: bool = True

    # Logging
    pinwheel_log_level: str = "INFO"

    model_config = {"env_prefix": "", "env_file": ".env", "extra": "ignore"}

    @model_validator(mode="after")
    def _ensure_session_secret(self) -> Settings:
        """Auto-generate session secret in dev; reject missing secret in production."""
        if not self.session_secret_key:
            if self.pinwheel_env == "production":
                msg = (
                    "SESSION_SECRET_KEY must be set in production. "
                    "Generate one with: python -c "
                    '"import secrets; print(secrets.token_urlsafe(32))"'
                )
                raise ValueError(msg)
            self.session_secret_key = secrets.token_urlsafe(32)
        return self

    @model_validator(mode="after")
    def _force_replay_in_production(self) -> Settings:
        """In production, games must use replay mode so the live arena works."""
        if self.pinwheel_env == "production" and self.pinwheel_presentation_mode != "replay":
            self.pinwheel_presentation_mode = "replay"
        return self

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
