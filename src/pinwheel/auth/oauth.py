"""Discord OAuth2 login/callback/logout routes.

The flow:
1. ``/auth/login``    — redirects the browser to Discord's OAuth consent page.
2. ``/auth/callback`` — Discord redirects back here with a code; we exchange
   it for an access token, fetch the user profile, create/update a
   ``PlayerRow`` in the DB, and set a signed session cookie.
3. ``/auth/logout``   — clears the session cookie.

OAuth is gracefully disabled when ``DISCORD_CLIENT_ID`` is not configured.
"""

from __future__ import annotations

import logging
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from itsdangerous import URLSafeTimedSerializer

from pinwheel.api.deps import RepoDep
from pinwheel.auth.deps import SESSION_COOKIE_NAME, SESSION_MAX_AGE
from pinwheel.config import Settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

DISCORD_AUTHORIZE_URL = "https://discord.com/api/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"
DISCORD_USER_URL = "https://discord.com/api/users/@me"
DISCORD_SCOPES = "identify"


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _oauth_enabled(request: Request) -> bool:
    """Return True when Discord OAuth credentials are configured."""
    s = _settings(request)
    return bool(s.discord_client_id and s.discord_client_secret)


def _serializer(request: Request) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        _settings(request).session_secret_key, salt="pinwheel-session"
    )


# ---- Routes ---------------------------------------------------------------


@router.get("/login")
async def login(request: Request) -> RedirectResponse:
    """Redirect to Discord OAuth2 consent page."""
    if not _oauth_enabled(request):
        return RedirectResponse(url="/", status_code=302)

    s = _settings(request)

    # Generate a CSRF-prevention state token and store it in a short-lived cookie.
    state = secrets.token_urlsafe(32)
    params = {
        "client_id": s.discord_client_id,
        "redirect_uri": s.discord_redirect_uri,
        "response_type": "code",
        "scope": DISCORD_SCOPES,
        "state": state,
    }
    redirect_url = f"{DISCORD_AUTHORIZE_URL}?{urlencode(params)}"
    response = RedirectResponse(url=redirect_url, status_code=302)
    is_prod = s.pinwheel_env == "production"
    response.set_cookie(
        "pinwheel_oauth_state",
        state,
        max_age=300,
        httponly=True,
        samesite="lax",
        secure=is_prod,
    )
    return response


@router.get("/callback")
async def callback(
    request: Request,
    repo: RepoDep,
    code: str = "",
    state: str = "",
) -> RedirectResponse:
    """Handle the OAuth2 callback from Discord."""
    if not _oauth_enabled(request):
        return RedirectResponse(url="/", status_code=302)

    # Validate CSRF state
    expected_state = request.cookies.get("pinwheel_oauth_state", "")
    if not state or not expected_state or state != expected_state:
        logger.warning("OAuth state mismatch — possible CSRF")
        return RedirectResponse(url="/", status_code=302)

    s = _settings(request)

    # Exchange the code for an access token.
    try:
        token_data = await _exchange_code(
            code=code,
            client_id=s.discord_client_id,
            client_secret=s.discord_client_secret,
            redirect_uri=s.discord_redirect_uri,
        )
    except Exception:
        logger.exception("Discord token exchange error")
        return RedirectResponse(url="/", status_code=302)

    access_token = token_data.get("access_token")
    if not access_token:
        logger.error("Discord token exchange failed: %s", token_data)
        return RedirectResponse(url="/", status_code=302)

    # Fetch the Discord user profile.
    try:
        user_info = await _fetch_user(access_token)
    except Exception:
        logger.exception("Discord user fetch error")
        return RedirectResponse(url="/", status_code=302)

    discord_id = user_info.get("id", "")
    username = user_info.get("username", "")
    avatar_hash = user_info.get("avatar", "")

    if not discord_id:
        logger.error("Discord user fetch returned no id: %s", user_info)
        return RedirectResponse(url="/", status_code=302)

    # Build avatar URL (Discord CDN).
    avatar_url = ""
    if avatar_hash:
        avatar_url = f"https://cdn.discordapp.com/avatars/{discord_id}/{avatar_hash}.png"

    # Create or update the player in the database.
    await repo.get_or_create_player(
        discord_id=discord_id,
        username=username,
        avatar_url=avatar_url,
    )

    # Sign a session cookie.
    serializer = _serializer(request)
    session_payload = serializer.dumps(
        {"discord_id": discord_id, "username": username, "avatar_url": avatar_url}
    )

    is_prod = s.pinwheel_env == "production"
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_payload,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=is_prod,
    )
    # Clear the one-time state cookie.
    response.delete_cookie("pinwheel_oauth_state")
    return response


@router.get("/logout")
async def logout() -> RedirectResponse:
    """Clear the session cookie and redirect home."""
    response = RedirectResponse(url="/", status_code=302)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


# ---- Internal helpers (httpx calls to Discord API) -------------------------


async def _exchange_code(
    *,
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> dict[str, str]:
    """Exchange an authorization code for an access token."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            DISCORD_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        result: dict[str, str] = resp.json()
        return result


async def _fetch_user(access_token: str) -> dict[str, str]:
    """Fetch the authenticated user's Discord profile."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            DISCORD_USER_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        result: dict[str, str] = resp.json()
        return result
