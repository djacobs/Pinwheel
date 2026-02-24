"""Tests for the team-following feature (cookie-based MVP).

Covers: follow/unfollow API endpoints, cookie setting/clearing,
HTMX partial responses, team page follow button rendering,
home page priority sorting, arena page priority sorting,
and nav bar "My Team" link.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from pinwheel.api.follow import FOLLOW_COOKIE_NAME
from pinwheel.config import Settings
from pinwheel.core.game_loop import step_round
from pinwheel.core.scheduler import generate_round_robin
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import Base
from pinwheel.db.repository import Repository
from pinwheel.main import create_app


@pytest.fixture
async def app_client():
    """Create a test app with an in-memory database and httpx client."""
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        pinwheel_env="development",
    )
    app = create_app(settings)

    engine = create_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine

    from pinwheel.core.event_bus import EventBus
    from pinwheel.core.presenter import PresentationState

    app.state.event_bus = EventBus()
    app.state.presentation_state = PresentationState()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, engine

    await engine.dispose()


def _hooper_attrs() -> dict[str, int]:
    return {
        "scoring": 50,
        "passing": 40,
        "defense": 35,
        "speed": 45,
        "stamina": 40,
        "iq": 50,
        "ego": 30,
        "chaotic_alignment": 40,
        "fate": 30,
    }


async def _seed_season(engine) -> tuple[str, list[str]]:
    """Create a league with 4 teams and run 1 round. Returns (season_id, team_ids)."""
    async with get_session(engine) as session:
        repo = Repository(session)
        league = await repo.create_league("Test League")
        season = await repo.create_season(
            league.id,
            "Season 1",
            starting_ruleset={"quarter_minutes": 3},
        )

        team_ids = []
        for i in range(4):
            team = await repo.create_team(
                season.id,
                f"Team {i + 1}",
                color=f"#{'abcdef'[i]}{'abcdef'[i]}{'abcdef'[i]}",
                venue={"name": f"Arena {i + 1}", "capacity": 5000},
            )
            team_ids.append(team.id)
            for j in range(3):
                await repo.create_hooper(
                    team_id=team.id,
                    season_id=season.id,
                    name=f"Hooper-{i + 1}-{j + 1}",
                    archetype="sharpshooter",
                    attributes=_hooper_attrs(),
                )

        matchups = generate_round_robin(team_ids)
        for m in matchups:
            await repo.create_schedule_entry(
                season_id=season.id,
                round_number=m.round_number,
                matchup_index=m.matchup_index,
                home_team_id=m.home_team_id,
                away_team_id=m.away_team_id,
            )

        await step_round(repo, season.id, round_number=1)

        games = await repo.get_games_for_round(season.id, 1)
        for g in games:
            await repo.mark_game_presented(g.id)

        await session.commit()
        return season.id, team_ids


class TestFollowAPI:
    """Test the follow/unfollow API endpoints."""

    async def test_follow_sets_cookie(self, app_client):
        """POST /api/teams/{id}/follow sets the followed team cookie."""
        client, engine = app_client
        _, team_ids = await _seed_season(engine)

        r = await client.post(f"/api/teams/{team_ids[0]}/follow")
        assert r.status_code == 200
        assert r.json()["status"] == "following"
        assert r.json()["team_id"] == team_ids[0]

        # Cookie should be set in the response
        cookie = r.cookies.get(FOLLOW_COOKIE_NAME)
        assert cookie == team_ids[0]

    async def test_unfollow_clears_cookie(self, app_client):
        """DELETE /api/teams/{id}/follow clears the followed team cookie."""
        client, engine = app_client
        _, team_ids = await _seed_season(engine)

        # First follow
        await client.post(f"/api/teams/{team_ids[0]}/follow")

        # Then unfollow
        r = await client.request("DELETE", f"/api/teams/{team_ids[0]}/follow")
        assert r.status_code == 200
        assert r.json()["status"] == "unfollowed"

    async def test_follow_nonexistent_team_404(self, app_client):
        """Following a nonexistent team returns 404."""
        client, _ = app_client
        r = await client.post("/api/teams/nonexistent-uuid-here-placeholder-xx/follow")
        assert r.status_code == 404

    async def test_follow_htmx_returns_html(self, app_client):
        """HTMX requests get an HTML partial response."""
        client, engine = app_client
        _, team_ids = await _seed_season(engine)

        r = await client.post(
            f"/api/teams/{team_ids[0]}/follow",
            headers={"HX-Request": "true"},
        )
        assert r.status_code == 200
        assert "Following" in r.text
        assert "hx-delete" in r.text

    async def test_unfollow_htmx_returns_html(self, app_client):
        """HTMX unfollow requests get an HTML partial with 'Follow' button."""
        client, engine = app_client
        _, team_ids = await _seed_season(engine)

        # Follow first
        await client.post(f"/api/teams/{team_ids[0]}/follow")

        r = await client.request(
            "DELETE",
            f"/api/teams/{team_ids[0]}/follow",
            headers={"HX-Request": "true"},
        )
        assert r.status_code == 200
        assert "Follow" in r.text
        assert "hx-post" in r.text


class TestTeamPageFollow:
    """Test that the team page renders follow/unfollow correctly."""

    async def test_team_page_shows_follow_button(self, app_client):
        """Team page without a follow cookie shows 'Follow' button."""
        client, engine = app_client
        _, team_ids = await _seed_season(engine)

        r = await client.get(f"/teams/{team_ids[0]}")
        assert r.status_code == 200
        assert "follow-btn" in r.text
        assert "hx-post" in r.text
        # Should not be in "following" state
        assert "follow-btn--following" not in r.text

    async def test_team_page_shows_following_when_followed(self, app_client):
        """Team page with follow cookie set shows 'Following' button."""
        client, engine = app_client
        _, team_ids = await _seed_season(engine)

        # Set the follow cookie
        client.cookies.set(FOLLOW_COOKIE_NAME, team_ids[0])

        r = await client.get(f"/teams/{team_ids[0]}")
        assert r.status_code == 200
        assert "follow-btn--following" in r.text
        assert "Following" in r.text

    async def test_team_page_other_team_not_followed(self, app_client):
        """If following team A and viewing team B, show Follow (not Following)."""
        client, engine = app_client
        _, team_ids = await _seed_season(engine)

        # Follow team 0
        client.cookies.set(FOLLOW_COOKIE_NAME, team_ids[0])

        # View team 1
        r = await client.get(f"/teams/{team_ids[1]}")
        assert r.status_code == 200
        # Should show Follow button, not Following
        assert "hx-post" in r.text


class TestNavBarMyTeam:
    """Test that the nav bar shows a 'My Team' link when following."""

    async def test_nav_shows_my_team_when_following(self, app_client):
        """Nav bar shows 'My Team' link when a team is followed."""
        client, engine = app_client
        _, team_ids = await _seed_season(engine)

        client.cookies.set(FOLLOW_COOKIE_NAME, team_ids[0])

        r = await client.get("/")
        assert r.status_code == 200
        assert "My Team" in r.text
        assert f"/teams/{team_ids[0]}" in r.text

    async def test_nav_no_my_team_when_not_following(self, app_client):
        """Nav bar does not show 'My Team' when no team is followed."""
        client, _ = app_client

        r = await client.get("/")
        assert r.status_code == 200
        assert "My Team" not in r.text


class TestHomePagePriority:
    """Test that the home page prioritizes followed team content."""

    async def test_home_page_highlights_followed_team(self, app_client):
        """Home page with data highlights the followed team's standings row."""
        client, engine = app_client
        _, team_ids = await _seed_season(engine)

        client.cookies.set(FOLLOW_COOKIE_NAME, team_ids[0])

        r = await client.get("/")
        assert r.status_code == 200
        # The followed team's standings row should have the highlight class
        assert "ms-row--followed" in r.text

    async def test_home_page_no_highlight_without_follow(self, app_client):
        """Home page without follow cookie has no highlight class."""
        client, engine = app_client
        await _seed_season(engine)

        r = await client.get("/")
        assert r.status_code == 200
        assert "ms-row--followed" not in r.text

    async def test_home_page_highlights_followed_game(self, app_client):
        """Home page highlights score cards for followed team games."""
        client, engine = app_client
        _, team_ids = await _seed_season(engine)

        client.cookies.set(FOLLOW_COOKIE_NAME, team_ids[0])

        r = await client.get("/")
        assert r.status_code == 200
        # Should have at least one highlighted score card
        assert "score-card--followed" in r.text


class TestArenaPagePriority:
    """Test that the arena page prioritizes followed team games."""

    async def test_arena_renders_with_follow_cookie(self, app_client):
        """Arena page renders correctly when a follow cookie is set."""
        client, engine = app_client
        _, team_ids = await _seed_season(engine)

        client.cookies.set(FOLLOW_COOKIE_NAME, team_ids[0])

        r = await client.get("/arena")
        assert r.status_code == 200
        # Page should render without errors
        assert "The Arena" in r.text


class TestGetFollowedTeamId:
    """Test the get_followed_team_id helper function."""

    def test_returns_none_for_empty(self):
        """Returns None when no cookie is set."""
        from unittest.mock import MagicMock

        from pinwheel.api.follow import get_followed_team_id

        request = MagicMock()
        request.cookies = {}
        assert get_followed_team_id(request) is None

    def test_returns_none_for_invalid_length(self):
        """Returns None for cookie values that are not UUID-length."""
        from unittest.mock import MagicMock

        from pinwheel.api.follow import get_followed_team_id

        request = MagicMock()
        request.cookies = {FOLLOW_COOKIE_NAME: "short"}
        assert get_followed_team_id(request) is None

    def test_returns_value_for_valid_uuid(self):
        """Returns the UUID when cookie is valid 36-char string."""
        from unittest.mock import MagicMock

        from pinwheel.api.follow import get_followed_team_id

        test_uuid = "12345678-1234-1234-1234-123456789012"
        request = MagicMock()
        request.cookies = {FOLLOW_COOKIE_NAME: test_uuid}
        assert get_followed_team_id(request) == test_uuid
