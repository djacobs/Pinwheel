"""FastAPI dependencies for authentication — extracting current user from session cookie.

Also provides shared admin-access helpers so that every ``/admin/*`` route
module does not need its own copy of the auth-gate logic.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel

from pinwheel.config import APP_VERSION, Settings

logger = logging.getLogger(__name__)

# Session cookie lives for 7 days (seconds).
SESSION_MAX_AGE = 7 * 24 * 60 * 60
SESSION_COOKIE_NAME = "pinwheel_session"


class SessionUser(BaseModel):
    """Minimal user info stored in the signed session cookie."""

    discord_id: str
    username: str
    avatar_url: str


def _get_serializer(request: Request) -> URLSafeTimedSerializer:
    """Build a signer from the app's session secret key."""
    settings: Settings = request.app.state.settings
    return URLSafeTimedSerializer(settings.session_secret_key, salt="pinwheel-session")


async def get_current_user(request: Request) -> SessionUser | None:
    """Extract the current user from the signed session cookie.

    This is optional auth — returns None if the user is not logged in
    or if the cookie is invalid/expired.  Page handlers should work
    fine with ``current_user=None``.
    """
    raw = request.cookies.get(SESSION_COOKIE_NAME)
    if not raw:
        return None

    serializer = _get_serializer(request)
    try:
        data = serializer.loads(raw, max_age=SESSION_MAX_AGE)
        return SessionUser(**data)
    except BadSignature:
        logger.debug("Invalid or expired session cookie — ignoring")
        return None
    except Exception:
        logger.debug("Failed to deserialise session cookie", exc_info=True)
        return None


# Handy type alias for route handlers.
OptionalUser = Annotated[SessionUser | None, Depends(get_current_user)]


# ---------------------------------------------------------------------------
# Shared admin-access helpers
# ---------------------------------------------------------------------------


def is_admin(current_user: SessionUser | None, settings: Settings) -> bool:
    """Return True if *current_user* is the configured admin.

    Returns False when there is no user, no admin ID configured, or the
    IDs do not match.
    """
    if current_user is None:
        return False
    admin_id = settings.pinwheel_admin_discord_id
    if not admin_id:
        return False
    return current_user.discord_id == admin_id


def check_admin_access(
    current_user: SessionUser | None, request: Request
) -> RedirectResponse | HTMLResponse | None:
    """Gate admin routes.  Returns a denial response, or ``None`` if access is granted.

    Fail-closed: in production/staging, denies access when OAuth is
    misconfigured rather than falling through to unauthenticated access.
    In development mode, allows access without auth for local testing.

    Usage in a route handler::

        if (denied := check_admin_access(current_user, request)):
            return denied
    """
    settings: Settings = request.app.state.settings

    # Development mode: allow unauthenticated access for local testing.
    if settings.pinwheel_env == "development":
        return None

    # Production / staging: require authenticated admin.
    oauth_enabled = bool(settings.discord_client_id and settings.discord_client_secret)

    if not oauth_enabled:
        # Fail closed — OAuth not configured in non-dev = no admin access.
        return HTMLResponse(
            "Admin access unavailable — OAuth not configured.", status_code=503
        )

    if current_user is None:
        return RedirectResponse(url="/auth/login", status_code=302)

    if not is_admin(current_user, settings):
        return HTMLResponse("Unauthorized — admin access required.", status_code=403)

    return None


def admin_auth_context(request: Request, current_user: SessionUser | None) -> dict:
    """Build auth-related template context for admin pages.

    Returns a dict suitable for splatting into a Jinja2 template context::

        return templates.TemplateResponse(
            request,
            "pages/my_admin_page.html",
            {
                "my_data": ...,
                **admin_auth_context(request, current_user),
            },
        )
    """
    settings: Settings = request.app.state.settings
    oauth_enabled = bool(settings.discord_client_id and settings.discord_client_secret)
    return {
        "current_user": current_user,
        "oauth_enabled": oauth_enabled,
        "pinwheel_env": settings.pinwheel_env,
        "app_version": APP_VERSION,
        "is_admin": is_admin(current_user, settings),
    }
