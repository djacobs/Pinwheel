"""FastAPI dependencies for authentication — extracting current user from session cookie."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends, Request
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel

from pinwheel.config import Settings

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
