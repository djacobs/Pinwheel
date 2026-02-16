"""Tests for shared admin-access helpers in pinwheel.auth.deps.

Covers:
- is_admin: True/False for various user/settings combos
- check_admin_access: dev mode allows all, prod fail-closed, prod redirect,
  prod 403, prod admin granted
- admin_auth_context: dict contents for admin and non-admin users
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from pinwheel.auth.deps import (
    SessionUser,
    admin_auth_context,
    check_admin_access,
    is_admin,
)
from pinwheel.config import APP_VERSION, Settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ADMIN_DISCORD_ID = "111222333"

_admin_user = SessionUser(
    discord_id=ADMIN_DISCORD_ID,
    username="admin_gov",
    avatar_url="https://cdn.discordapp.com/avatars/111/hash.png",
)

_regular_user = SessionUser(
    discord_id="999888777",
    username="regular_gov",
    avatar_url="https://cdn.discordapp.com/avatars/999/hash.png",
)


def _make_settings(
    *,
    env: str = "development",
    admin_id: str = ADMIN_DISCORD_ID,
    client_id: str = "cid",
    client_secret: str = "csec",
) -> Settings:
    """Build a Settings object with controllable OAuth and admin fields."""
    return Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        pinwheel_env=env,
        session_secret_key="test-secret",
        discord_client_id=client_id,
        discord_client_secret=client_secret,
        pinwheel_admin_discord_id=admin_id,
    )


def _fake_request(settings: Settings) -> Request:
    """Build a minimal Request-like object whose ``app.state.settings`` is set.

    FastAPI's ``Request`` reads ``app.state.settings`` — we use a
    lightweight namespace so tests don't need a running ASGI app.
    """
    state = SimpleNamespace(settings=settings)
    app = SimpleNamespace(state=state)
    scope = {"type": "http", "method": "GET", "headers": [], "app": app}
    return Request(scope)


# ---------------------------------------------------------------------------
# is_admin
# ---------------------------------------------------------------------------


class TestIsAdmin:
    """Unit tests for is_admin()."""

    def test_returns_true_for_matching_admin(self) -> None:
        settings = _make_settings()
        assert is_admin(_admin_user, settings) is True

    def test_returns_false_when_user_is_none(self) -> None:
        settings = _make_settings()
        assert is_admin(None, settings) is False

    def test_returns_false_when_admin_id_empty(self) -> None:
        settings = _make_settings(admin_id="")
        assert is_admin(_admin_user, settings) is False

    def test_returns_false_when_ids_differ(self) -> None:
        settings = _make_settings()
        assert is_admin(_regular_user, settings) is False


# ---------------------------------------------------------------------------
# check_admin_access
# ---------------------------------------------------------------------------


class TestCheckAdminAccess:
    """Unit tests for check_admin_access()."""

    # -- Development mode ---------------------------------------------------

    def test_dev_mode_allows_anonymous(self) -> None:
        """In development, even unauthenticated access is allowed."""
        settings = _make_settings(env="development", client_id="", client_secret="")
        req = _fake_request(settings)
        result = check_admin_access(None, req)
        assert result is None

    def test_dev_mode_allows_non_admin(self) -> None:
        """In development, non-admin users are allowed."""
        settings = _make_settings(env="development")
        req = _fake_request(settings)
        result = check_admin_access(_regular_user, req)
        assert result is None

    def test_dev_mode_allows_admin(self) -> None:
        """In development, admin users are allowed (obviously)."""
        settings = _make_settings(env="development")
        req = _fake_request(settings)
        result = check_admin_access(_admin_user, req)
        assert result is None

    # -- Production: OAuth not configured (fail-closed) ---------------------

    def test_prod_no_oauth_returns_503(self) -> None:
        """Production without OAuth config returns 503 — fail closed."""
        settings = _make_settings(env="production", client_id="", client_secret="")
        req = _fake_request(settings)
        result = check_admin_access(_admin_user, req)
        assert isinstance(result, HTMLResponse)
        assert result.status_code == 503
        assert b"OAuth not configured" in result.body

    def test_prod_no_oauth_anonymous_returns_503(self) -> None:
        """Production without OAuth config returns 503 even for anonymous."""
        settings = _make_settings(env="production", client_id="", client_secret="")
        req = _fake_request(settings)
        result = check_admin_access(None, req)
        assert isinstance(result, HTMLResponse)
        assert result.status_code == 503

    # -- Production: OAuth configured, no user (redirect) -------------------

    def test_prod_oauth_no_user_redirects(self) -> None:
        """Production with OAuth but no session redirects to login."""
        settings = _make_settings(env="production")
        req = _fake_request(settings)
        result = check_admin_access(None, req)
        assert isinstance(result, RedirectResponse)
        assert result.status_code == 302
        # Check Location header
        location_headers = [
            v.decode() for k, v in result.raw_headers if k.decode().lower() == "location"
        ]
        assert any("/auth/login" in loc for loc in location_headers)

    # -- Production: OAuth configured, non-admin user (403) -----------------

    def test_prod_oauth_non_admin_returns_403(self) -> None:
        """Production with a logged-in non-admin returns 403."""
        settings = _make_settings(env="production")
        req = _fake_request(settings)
        result = check_admin_access(_regular_user, req)
        assert isinstance(result, HTMLResponse)
        assert result.status_code == 403
        assert b"admin access required" in result.body

    # -- Production: OAuth configured, admin user (granted) -----------------

    def test_prod_oauth_admin_granted(self) -> None:
        """Production with a logged-in admin returns None (access granted)."""
        settings = _make_settings(env="production")
        req = _fake_request(settings)
        result = check_admin_access(_admin_user, req)
        assert result is None

    # -- Staging behaves like production ------------------------------------

    def test_staging_same_as_production(self) -> None:
        """Staging is not development — it requires auth."""
        settings = _make_settings(env="staging")
        req = _fake_request(settings)
        # Anonymous user with OAuth configured should redirect
        result = check_admin_access(None, req)
        assert isinstance(result, RedirectResponse)
        assert result.status_code == 302

    def test_staging_no_oauth_fails_closed(self) -> None:
        """Staging without OAuth fails closed, same as production."""
        settings = _make_settings(env="staging", client_id="", client_secret="")
        req = _fake_request(settings)
        result = check_admin_access(None, req)
        assert isinstance(result, HTMLResponse)
        assert result.status_code == 503

    # -- Edge: only one OAuth field set (partial config) --------------------

    def test_prod_partial_oauth_config_fails_closed(self) -> None:
        """If only client_id is set (no secret), treat as unconfigured."""
        settings = _make_settings(env="production", client_id="cid", client_secret="")
        req = _fake_request(settings)
        result = check_admin_access(_admin_user, req)
        assert isinstance(result, HTMLResponse)
        assert result.status_code == 503


# ---------------------------------------------------------------------------
# admin_auth_context
# ---------------------------------------------------------------------------


class TestAdminAuthContext:
    """Unit tests for admin_auth_context()."""

    def test_context_keys_present(self) -> None:
        """The returned dict contains all expected keys."""
        settings = _make_settings()
        req = _fake_request(settings)
        ctx = admin_auth_context(req, _admin_user)
        assert set(ctx.keys()) == {
            "current_user",
            "oauth_enabled",
            "pinwheel_env",
            "app_version",
            "is_admin",
        }

    def test_admin_user_context(self) -> None:
        """Context for an admin user has is_admin=True."""
        settings = _make_settings()
        req = _fake_request(settings)
        ctx = admin_auth_context(req, _admin_user)
        assert ctx["current_user"] is _admin_user
        assert ctx["oauth_enabled"] is True
        assert ctx["pinwheel_env"] == "development"
        assert ctx["app_version"] == APP_VERSION
        assert ctx["is_admin"] is True

    def test_regular_user_context(self) -> None:
        """Context for a non-admin user has is_admin=False."""
        settings = _make_settings()
        req = _fake_request(settings)
        ctx = admin_auth_context(req, _regular_user)
        assert ctx["current_user"] is _regular_user
        assert ctx["is_admin"] is False

    def test_anonymous_context(self) -> None:
        """Context for an anonymous visitor has current_user=None, is_admin=False."""
        settings = _make_settings()
        req = _fake_request(settings)
        ctx = admin_auth_context(req, None)
        assert ctx["current_user"] is None
        assert ctx["is_admin"] is False

    def test_no_oauth_context(self) -> None:
        """When OAuth is not configured, oauth_enabled is False."""
        settings = _make_settings(client_id="", client_secret="")
        req = _fake_request(settings)
        ctx = admin_auth_context(req, None)
        assert ctx["oauth_enabled"] is False

    def test_production_env_reflected(self) -> None:
        """The pinwheel_env from settings is passed through."""
        settings = _make_settings(env="production")
        req = _fake_request(settings)
        ctx = admin_auth_context(req, _admin_user)
        assert ctx["pinwheel_env"] == "production"
