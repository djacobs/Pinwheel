"""Tests for Discord OAuth2 authentication flow.

Covers:
- Login redirect (with and without OAuth configured)
- Callback flow with mocked Discord API
- Session cookie creation and validation
- Logout clears session
- Player creation/update in the database
- Optional auth dependency
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous import URLSafeTimedSerializer

from pinwheel.auth.deps import SESSION_COOKIE_NAME, SESSION_MAX_AGE
from pinwheel.config import Settings
from pinwheel.core.event_bus import EventBus
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import Base
from pinwheel.db.repository import Repository
from pinwheel.main import create_app


def _test_settings(**overrides: str) -> Settings:
    """Build test settings with in-memory DB and optional OAuth credentials."""
    defaults: dict[str, str] = {
        "database_url": "sqlite+aiosqlite:///:memory:",
        "pinwheel_env": "development",
        "session_secret_key": "test-secret-key-for-testing",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _sign_session(secret: str, data: dict) -> str:
    """Create a signed session cookie value for testing."""
    serializer = URLSafeTimedSerializer(secret, salt="pinwheel-session")
    result: str = serializer.dumps(data)
    return result


@pytest.fixture
async def oauth_app_client() -> AsyncGenerator[tuple[AsyncClient, Settings], None]:
    """Test app with OAuth configured and DB engine initialized."""
    settings = _test_settings(
        discord_client_id="test-client-id",
        discord_client_secret="test-client-secret",
        discord_redirect_uri="http://localhost:8000/auth/callback",
    )
    app = create_app(settings)

    # Manually run lifespan startup (same pattern as test_pages.py)
    engine = create_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    app.state.event_bus = EventBus()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, settings

    await engine.dispose()


@pytest.fixture
async def no_oauth_app_client() -> AsyncGenerator[AsyncClient, None]:
    """Test app without OAuth configured and DB engine initialized."""
    settings = _test_settings(
        discord_client_id="",
        discord_client_secret="",
    )
    app = create_app(settings)

    engine = create_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    app.state.event_bus = EventBus()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    await engine.dispose()


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


async def test_login_redirects_to_discord(
    oauth_app_client: tuple[AsyncClient, Settings],
) -> None:
    """When OAuth is configured, /auth/login redirects to Discord."""
    client, _settings = oauth_app_client
    resp = await client.get("/auth/login", follow_redirects=False)

    assert resp.status_code == 302
    location = resp.headers["location"]
    assert "discord.com/api/oauth2/authorize" in location
    assert "client_id=test-client-id" in location
    assert "response_type=code" in location
    assert "scope=identify" in location

    # Should set a state cookie for CSRF prevention
    state_cookie = resp.cookies.get("pinwheel_oauth_state")
    assert state_cookie is not None
    assert len(state_cookie) > 10


async def test_login_redirects_home_when_oauth_disabled(
    no_oauth_app_client: AsyncClient,
) -> None:
    """When OAuth is not configured, /auth/login just redirects home."""
    client = no_oauth_app_client
    resp = await client.get("/auth/login", follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == "/"


# ---------------------------------------------------------------------------
# Callback
# ---------------------------------------------------------------------------


async def test_callback_exchanges_code_and_sets_session(
    oauth_app_client: tuple[AsyncClient, Settings],
) -> None:
    """Successful callback creates player and sets signed session cookie."""
    client, settings = oauth_app_client

    mock_token_response = {
        "access_token": "mock-access-token",
        "token_type": "Bearer",
    }
    mock_user_response = {
        "id": "123456789",
        "username": "testgovernor",
        "discriminator": "0001",
        "avatar": "abc123hash",
    }

    with (
        patch("pinwheel.auth.oauth._exchange_code", new_callable=AsyncMock) as mock_exchange,
        patch("pinwheel.auth.oauth._fetch_user", new_callable=AsyncMock) as mock_fetch,
    ):
        mock_exchange.return_value = mock_token_response
        mock_fetch.return_value = mock_user_response

        # First, get a valid state token by visiting /auth/login
        login_resp = await client.get("/auth/login", follow_redirects=False)
        state = login_resp.cookies.get("pinwheel_oauth_state", "")

        # Now hit the callback with the matching state
        resp = await client.get(
            f"/auth/callback?code=test-auth-code&state={state}",
            follow_redirects=False,
        )

    assert resp.status_code == 302
    assert resp.headers["location"] == "/"

    # Session cookie should be set
    session_cookie = resp.cookies.get(SESSION_COOKIE_NAME)
    assert session_cookie is not None

    # Verify the session cookie contents
    serializer = URLSafeTimedSerializer(settings.session_secret_key, salt="pinwheel-session")
    data = serializer.loads(session_cookie, max_age=SESSION_MAX_AGE)
    assert data["discord_id"] == "123456789"
    assert data["username"] == "testgovernor"
    assert "cdn.discordapp.com" in data["avatar_url"]

    # Verify Discord API was called correctly
    mock_exchange.assert_called_once_with(
        code="test-auth-code",
        client_id="test-client-id",
        client_secret="test-client-secret",
        redirect_uri="http://localhost:8000/auth/callback",
    )
    mock_fetch.assert_called_once_with("mock-access-token")


async def test_callback_rejects_mismatched_state(
    oauth_app_client: tuple[AsyncClient, Settings],
) -> None:
    """Callback rejects requests where the state parameter doesn't match."""
    client, _settings = oauth_app_client

    # Get a valid state cookie (we need the side-effect on the client)
    await client.get("/auth/login", follow_redirects=False)
    # Use a *different* state in the query string
    resp = await client.get(
        "/auth/callback?code=test-code&state=wrong-state",
        follow_redirects=False,
    )

    assert resp.status_code == 302
    assert resp.headers["location"] == "/"
    # No session cookie should be set
    assert SESSION_COOKIE_NAME not in resp.cookies


async def test_callback_handles_no_access_token(
    oauth_app_client: tuple[AsyncClient, Settings],
) -> None:
    """Callback handles Discord returning an error instead of access token."""
    client, _settings = oauth_app_client

    with patch("pinwheel.auth.oauth._exchange_code", new_callable=AsyncMock) as mock_exchange:
        mock_exchange.return_value = {"error": "invalid_grant"}

        login_resp = await client.get("/auth/login", follow_redirects=False)
        state = login_resp.cookies.get("pinwheel_oauth_state", "")

        resp = await client.get(
            f"/auth/callback?code=bad-code&state={state}",
            follow_redirects=False,
        )

    assert resp.status_code == 302
    assert SESSION_COOKIE_NAME not in resp.cookies


async def test_callback_disabled_when_no_oauth(
    no_oauth_app_client: AsyncClient,
) -> None:
    """Callback redirects home when OAuth is not configured."""
    client = no_oauth_app_client
    resp = await client.get(
        "/auth/callback?code=test&state=test",
        follow_redirects=False,
    )

    assert resp.status_code == 302
    assert resp.headers["location"] == "/"


async def test_callback_creates_player_in_database(
    oauth_app_client: tuple[AsyncClient, Settings],
) -> None:
    """Callback creates a PlayerRow in the database."""
    client, _settings = oauth_app_client

    mock_token = {"access_token": "tok"}
    mock_user = {"id": "999", "username": "newplayer", "avatar": "avhash"}

    with (
        patch("pinwheel.auth.oauth._exchange_code", new_callable=AsyncMock) as mock_ex,
        patch("pinwheel.auth.oauth._fetch_user", new_callable=AsyncMock) as mock_fu,
    ):
        mock_ex.return_value = mock_token
        mock_fu.return_value = mock_user

        login_resp = await client.get("/auth/login", follow_redirects=False)
        state = login_resp.cookies.get("pinwheel_oauth_state", "")
        await client.get(
            f"/auth/callback?code=c&state={state}",
            follow_redirects=False,
        )

    # Verify the player was created by checking the DB directly
    # Access the engine from the app via the transport
    engine = client._transport.app.state.engine  # type: ignore[union-attr]
    async with get_session(engine) as session:
        repo = Repository(session)
        player = await repo.get_player_by_discord_id("999")
        assert player is not None
        assert player.username == "newplayer"
        assert player.discord_id == "999"
        assert "cdn.discordapp.com" in player.avatar_url


async def test_callback_updates_existing_player(
    oauth_app_client: tuple[AsyncClient, Settings],
) -> None:
    """Logging in again updates username, avatar, and last_login."""
    client, _settings = oauth_app_client

    mock_token = {"access_token": "tok"}
    mock_user_v1 = {"id": "555", "username": "oldname", "avatar": "old"}
    mock_user_v2 = {"id": "555", "username": "newname", "avatar": "new"}

    with (
        patch("pinwheel.auth.oauth._exchange_code", new_callable=AsyncMock) as mock_ex,
        patch("pinwheel.auth.oauth._fetch_user", new_callable=AsyncMock) as mock_fu,
    ):
        mock_ex.return_value = mock_token

        # First login
        mock_fu.return_value = mock_user_v1
        login1 = await client.get("/auth/login", follow_redirects=False)
        state1 = login1.cookies.get("pinwheel_oauth_state", "")
        await client.get(
            f"/auth/callback?code=c1&state={state1}",
            follow_redirects=False,
        )

        # Second login with updated profile
        mock_fu.return_value = mock_user_v2
        login2 = await client.get("/auth/login", follow_redirects=False)
        state2 = login2.cookies.get("pinwheel_oauth_state", "")
        await client.get(
            f"/auth/callback?code=c2&state={state2}",
            follow_redirects=False,
        )

    engine = client._transport.app.state.engine  # type: ignore[union-attr]
    async with get_session(engine) as session:
        repo = Repository(session)
        player = await repo.get_player_by_discord_id("555")
        assert player is not None
        assert player.username == "newname"
        assert "new" in player.avatar_url


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


async def test_logout_clears_session(
    oauth_app_client: tuple[AsyncClient, Settings],
) -> None:
    """Logout clears the session cookie and redirects home."""
    client, _settings = oauth_app_client
    resp = await client.get("/auth/logout", follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == "/"
    # The session cookie should be deleted (set to empty or max-age=0)
    set_cookie = resp.headers.get("set-cookie", "")
    assert SESSION_COOKIE_NAME in set_cookie


# ---------------------------------------------------------------------------
# Session dependency
# ---------------------------------------------------------------------------


async def test_session_user_from_valid_cookie(
    oauth_app_client: tuple[AsyncClient, Settings],
) -> None:
    """A valid signed session cookie is decoded into a SessionUser."""
    client, settings = oauth_app_client

    session_data = {
        "discord_id": "42",
        "username": "governor42",
        "avatar_url": "https://cdn.discordapp.com/avatars/42/hash.png",
    }
    cookie_value = _sign_session(settings.session_secret_key, session_data)

    # Set the cookie on the client and request a page
    client.cookies.set(SESSION_COOKIE_NAME, cookie_value)
    resp = await client.get("/")

    assert resp.status_code == 200
    # The username should appear in the nav
    assert "governor42" in resp.text

    # Clean up the cookie so it doesn't affect other tests
    client.cookies.delete(SESSION_COOKIE_NAME)


async def test_no_session_cookie_shows_login_button(
    oauth_app_client: tuple[AsyncClient, Settings],
) -> None:
    """Without a session cookie, the nav shows 'Login with Discord'."""
    client, _settings = oauth_app_client
    resp = await client.get("/")

    assert resp.status_code == 200
    assert "Login with Discord" in resp.text


async def test_invalid_session_cookie_treated_as_logged_out(
    oauth_app_client: tuple[AsyncClient, Settings],
) -> None:
    """A tampered session cookie is silently ignored."""
    client, _settings = oauth_app_client

    client.cookies.set(SESSION_COOKIE_NAME, "tampered-garbage-value")
    resp = await client.get("/")

    assert resp.status_code == 200
    assert "Login with Discord" in resp.text

    client.cookies.delete(SESSION_COOKIE_NAME)


async def test_no_oauth_config_hides_login_button(
    no_oauth_app_client: AsyncClient,
) -> None:
    """When OAuth is not configured, neither login nor logout appear."""
    client = no_oauth_app_client
    resp = await client.get("/")

    assert resp.status_code == 200
    assert "Login with Discord" not in resp.text
    assert "Logout" not in resp.text


# ---------------------------------------------------------------------------
# Session secret key validation (P1)
# ---------------------------------------------------------------------------


async def test_session_secret_auto_generated_in_dev() -> None:
    """In development, empty session_secret_key gets auto-generated."""
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        pinwheel_env="development",
        session_secret_key="",
    )
    assert settings.session_secret_key != ""
    assert len(settings.session_secret_key) > 20


async def test_session_secret_rejected_in_production() -> None:
    """In production, empty session_secret_key raises ValueError."""
    with pytest.raises(Exception, match="SESSION_SECRET_KEY must be set"):
        Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            pinwheel_env="production",
            session_secret_key="",
        )


async def test_session_secret_accepted_when_provided() -> None:
    """An explicit session_secret_key works in any environment."""
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        pinwheel_env="production",
        session_secret_key="my-real-production-secret",
    )
    assert settings.session_secret_key == "my-real-production-secret"


# ---------------------------------------------------------------------------
# Player without avatar
# ---------------------------------------------------------------------------


async def test_callback_handles_token_exchange_error(
    oauth_app_client: tuple[AsyncClient, Settings],
) -> None:
    """Callback redirects gracefully when Discord token exchange raises."""
    client, _settings = oauth_app_client

    with patch("pinwheel.auth.oauth._exchange_code", new_callable=AsyncMock) as mock_ex:
        mock_ex.side_effect = httpx.ConnectError("Discord API timeout")

        login_resp = await client.get("/auth/login", follow_redirects=False)
        state = login_resp.cookies.get("pinwheel_oauth_state", "")
        resp = await client.get(
            f"/auth/callback?code=bad&state={state}",
            follow_redirects=False,
        )

    assert resp.status_code == 302
    assert resp.headers["location"] == "/"
    assert SESSION_COOKIE_NAME not in resp.cookies


async def test_callback_handles_user_fetch_error(
    oauth_app_client: tuple[AsyncClient, Settings],
) -> None:
    """Callback redirects gracefully when Discord user fetch raises."""
    client, _settings = oauth_app_client

    with (
        patch("pinwheel.auth.oauth._exchange_code", new_callable=AsyncMock) as mock_ex,
        patch("pinwheel.auth.oauth._fetch_user", new_callable=AsyncMock) as mock_fu,
    ):
        mock_ex.return_value = {"access_token": "tok"}
        mock_fu.side_effect = httpx.ConnectError("Discord API error")

        login_resp = await client.get("/auth/login", follow_redirects=False)
        state = login_resp.cookies.get("pinwheel_oauth_state", "")
        resp = await client.get(
            f"/auth/callback?code=c&state={state}",
            follow_redirects=False,
        )

    assert resp.status_code == 302
    assert resp.headers["location"] == "/"
    assert SESSION_COOKIE_NAME not in resp.cookies


async def test_callback_handles_no_avatar(
    oauth_app_client: tuple[AsyncClient, Settings],
) -> None:
    """A Discord user with no avatar gets an empty avatar_url."""
    client, settings = oauth_app_client

    mock_token = {"access_token": "tok"}
    mock_user = {"id": "777", "username": "noavatar", "avatar": None}

    with (
        patch("pinwheel.auth.oauth._exchange_code", new_callable=AsyncMock) as mock_ex,
        patch("pinwheel.auth.oauth._fetch_user", new_callable=AsyncMock) as mock_fu,
    ):
        mock_ex.return_value = mock_token
        mock_fu.return_value = mock_user

        login_resp = await client.get("/auth/login", follow_redirects=False)
        state = login_resp.cookies.get("pinwheel_oauth_state", "")
        resp = await client.get(
            f"/auth/callback?code=c&state={state}",
            follow_redirects=False,
        )

    assert resp.status_code == 302
    session_cookie = resp.cookies.get(SESSION_COOKIE_NAME)
    assert session_cookie is not None

    serializer = URLSafeTimedSerializer(settings.session_secret_key, salt="pinwheel-session")
    data = serializer.loads(session_cookie, max_age=SESSION_MAX_AGE)
    assert data["avatar_url"] == ""
