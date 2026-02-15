"""Tests for page routes — verify HTML rendering works."""

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous import URLSafeTimedSerializer

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

    # Manually run lifespan startup
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


def _hooper_attrs():
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


async def _seed_season(engine):
    """Create a league with 4 teams and run 1 round."""
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

        # Run 1 round
        await step_round(repo, season.id, round_number=1)

        # Mark games as presented so they appear on arena/home
        games = await repo.get_games_for_round(season.id, 1)
        for g in games:
            await repo.mark_game_presented(g.id)

        await session.commit()

        return season.id, team_ids


class TestEmptyPages:
    """Pages should render without errors even with no data."""

    async def test_home(self, app_client):
        client, _ = app_client
        r = await client.get("/")
        assert r.status_code == 200
        assert "PINWHEEL" in r.text

    async def test_arena_empty(self, app_client):
        client, _ = app_client
        r = await client.get("/arena")
        assert r.status_code == 200
        assert "No Games Yet" in r.text

    async def test_standings_empty(self, app_client):
        client, _ = app_client
        r = await client.get("/standings")
        assert r.status_code == 200
        assert "No Standings Yet" in r.text

    async def test_governance_public(self, app_client):
        """Governance page is publicly viewable (no auth required)."""
        client, _ = app_client
        r = await client.get("/governance")
        assert r.status_code == 200
        assert "The Floor" in r.text

    async def test_rules_empty(self, app_client):
        client, _ = app_client
        r = await client.get("/rules")
        assert r.status_code == 200
        assert "The Rules" in r.text
        assert "Game Mechanics" in r.text

    async def test_reports_empty(self, app_client):
        client, _ = app_client
        r = await client.get("/reports")
        assert r.status_code == 200
        assert "No Reports Yet" in r.text

    async def test_play_page(self, app_client):
        """Play page renders with onboarding content."""
        client, _ = app_client
        r = await client.get("/play")
        assert r.status_code == 200
        assert "Join the League" in r.text
        assert "The Rhythm" in r.text
        assert "What You Do" in r.text
        assert "Discord Commands" in r.text


class TestPopulatedPages:
    """Pages with seeded data should render game results."""

    async def test_home_with_data(self, app_client):
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        r = await client.get("/")
        assert r.status_code == 200
        assert "Latest Results" in r.text
        assert "Standings" in r.text
        assert "How It Works" in r.text

    async def test_arena_with_games(self, app_client):
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        r = await client.get("/arena")
        assert r.status_code == 200
        assert "Final" in r.text
        assert "Team 1" in r.text or "Team 2" in r.text

    async def test_standings_with_data(self, app_client):
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        r = await client.get("/standings")
        assert r.status_code == 200
        assert "Team" in r.text

    async def test_game_detail(self, app_client):
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        # Get a game ID from the arena
        async with get_session(engine) as session:
            repo = Repository(session)
            games = await repo.get_games_for_round(season_id, 1)
            game_id = games[0].id

        r = await client.get(f"/games/{game_id}")
        assert r.status_code == 200
        assert "Box Score" in r.text
        assert "Play-by-Play" in r.text

    async def test_game_404(self, app_client):
        client, _ = app_client
        r = await client.get("/games/nonexistent")
        assert r.status_code == 404

    async def test_team_page(self, app_client):
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        r = await client.get(f"/teams/{team_ids[0]}")
        assert r.status_code == 200
        assert "Team 1" in r.text
        assert "Roster" in r.text
        assert "Hooper-1-1" in r.text

    async def test_team_page_has_spider_charts(self, app_client):
        """Team page should render SVG spider charts for each hooper."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        r = await client.get(f"/teams/{team_ids[0]}")
        assert r.status_code == 200
        assert "<svg" in r.text
        assert "polygon" in r.text

    async def test_team_page_has_hooper_links(self, app_client):
        """Team page should have links to individual hooper pages."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        r = await client.get(f"/teams/{team_ids[0]}")
        assert r.status_code == 200
        assert "/hoopers/" in r.text

    async def test_team_404(self, app_client):
        client, _ = app_client
        r = await client.get("/teams/nonexistent")
        assert r.status_code == 404

    async def test_reports_with_data(self, app_client):
        client, engine = app_client
        await _seed_season(engine)

        r = await client.get("/reports")
        assert r.status_code == 200
        # Should have simulation report from step_round
        assert "Simulation" in r.text or "simulation" in r.text

    async def test_play_page_with_data(self, app_client):
        """Play page shows league stats when data exists."""
        client, engine = app_client
        await _seed_season(engine)

        r = await client.get("/play")
        assert r.status_code == 200
        assert "Rounds Played" in r.text
        assert "Teams" in r.text
        assert "hoopers" in r.text

    async def test_home_has_join_cta(self, app_client):
        """Home page should have a join/play CTA."""
        client, _ = app_client
        r = await client.get("/")
        assert r.status_code == 200
        assert "Want to play?" in r.text
        assert "/play" in r.text

    async def test_nav_present(self, app_client):
        client, _ = app_client
        r = await client.get("/")
        assert "Play" in r.text
        assert "Arena" in r.text
        assert "Standings" in r.text
        assert "The Floor" in r.text

    async def test_static_css(self, app_client):
        client, _ = app_client
        r = await client.get("/static/css/pinwheel.css")
        assert r.status_code == 200
        assert "bg-primary" in r.text

    async def test_static_htmx(self, app_client):
        client, _ = app_client
        r = await client.get("/static/js/htmx.min.js")
        assert r.status_code == 200


class TestArenaLive:
    """Tests for the live arena section when a presentation is active."""

    async def test_arena_with_live_games(self, app_client):
        """Arena should render live games from PresentationState."""
        client, engine = app_client
        from pinwheel.core.presenter import LiveGameState

        # Set up a fake active presentation
        pstate = client._transport.app.state.presentation_state  # type: ignore
        pstate.is_active = True
        pstate.current_round = 5
        pstate.live_games = {
            0: LiveGameState(
                game_index=0,
                game_id="g-5-0",
                home_team_id="team-a",
                away_team_id="team-b",
                home_team_name="Thunderbolts",
                away_team_name="Storm",
                home_score=25,
                away_score=20,
                quarter=2,
                game_clock="3:45",
                status="live",
            ),
        }

        r = await client.get("/arena")
        assert r.status_code == 200
        assert "LIVE" in r.text
        assert "Thunderbolts" in r.text
        assert "Storm" in r.text
        assert "25" in r.text
        assert "Q2" in r.text

        # Clean up
        pstate.reset()

    async def test_arena_no_live_games_shows_hidden_container(self, app_client):
        """Arena without live games should have a hidden live container."""
        client, _ = app_client
        r = await client.get("/arena")
        assert r.status_code == 200
        assert 'style="display:none;"' in r.text


class TestGameDetailHooperLinks:
    """Tests for hooper links in game detail page."""

    async def test_game_detail_has_hooper_links(self, app_client):
        """Game detail page should have links to hooper pages."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        async with get_session(engine) as session:
            repo = Repository(session)
            games = await repo.get_games_for_round(season_id, 1)
            game_id = games[0].id

        r = await client.get(f"/games/{game_id}")
        assert r.status_code == 200
        assert "/hoopers/" in r.text


class TestHooperPages:
    """Tests for individual hooper pages."""

    async def test_hooper_page_renders(self, app_client):
        """Hooper page should render with the hooper's name."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        # Get a hooper ID
        async with get_session(engine) as session:
            repo = Repository(session)
            team = await repo.get_team(team_ids[0])
            hooper_id = team.hoopers[0].id
            hooper_name = team.hoopers[0].name

        r = await client.get(f"/hoopers/{hooper_id}")
        assert r.status_code == 200
        assert hooper_name in r.text

    async def test_hooper_page_has_spider_chart(self, app_client):
        """Hooper page should contain an SVG spider chart."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        async with get_session(engine) as session:
            repo = Repository(session)
            team = await repo.get_team(team_ids[0])
            hooper_id = team.hoopers[0].id

        r = await client.get(f"/hoopers/{hooper_id}")
        assert r.status_code == 200
        assert "<svg" in r.text
        assert "polygon" in r.text

    async def test_hooper_page_has_game_log(self, app_client):
        """After running a round, hooper page should show game log."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        async with get_session(engine) as session:
            repo = Repository(session)
            team = await repo.get_team(team_ids[0])
            hooper_id = team.hoopers[0].id

        r = await client.get(f"/hoopers/{hooper_id}")
        assert r.status_code == 200
        assert "Game Log" in r.text

    async def test_hooper_page_has_season_averages(self, app_client):
        """Hooper page should show season averages after games are played."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        async with get_session(engine) as session:
            repo = Repository(session)
            team = await repo.get_team(team_ids[0])
            hooper_id = team.hoopers[0].id

        r = await client.get(f"/hoopers/{hooper_id}")
        assert r.status_code == 200
        assert "Season Averages" in r.text
        assert "PPG" in r.text

    async def test_hooper_page_has_team_link(self, app_client):
        """Hooper page should link back to the team."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        async with get_session(engine) as session:
            repo = Repository(session)
            team = await repo.get_team(team_ids[0])
            hooper_id = team.hoopers[0].id

        r = await client.get(f"/hoopers/{hooper_id}")
        assert r.status_code == 200
        assert f"/teams/{team_ids[0]}" in r.text

    async def test_hooper_page_404(self, app_client):
        """Nonexistent hooper ID should return 404."""
        client, _ = app_client
        r = await client.get("/hoopers/nonexistent")
        assert r.status_code == 404


class TestGovernorPages:
    """Tests for governor profile pages."""

    async def test_governor_page_renders(self, app_client):
        """Governor page should render with the governor's name and stats."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        # Create a player/governor enrolled on a team
        async with get_session(engine) as session:
            repo = Repository(session)
            player = await repo.get_or_create_player(
                discord_id="111222333",
                username="TestGovernor",
            )
            await repo.enroll_player(player.id, team_ids[0], season_id)
            await session.commit()
            player_id = player.id

        r = await client.get(f"/governors/{player_id}")
        assert r.status_code == 200
        assert "TestGovernor" in r.text
        assert "Governor" in r.text
        assert "Floor Record" in r.text
        assert "Team 1" in r.text

    async def test_governor_page_shows_stats(self, app_client):
        """Governor page should show proposal and vote counts."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        async with get_session(engine) as session:
            repo = Repository(session)
            player = await repo.get_or_create_player(
                discord_id="444555666",
                username="ActiveGovernor",
            )
            await repo.enroll_player(player.id, team_ids[0], season_id)

            # Submit a proposal event
            await repo.append_event(
                event_type="proposal.submitted",
                aggregate_id="prop-1",
                aggregate_type="proposal",
                season_id=season_id,
                governor_id=player.id,
                team_id=team_ids[0],
                round_number=1,
                payload={
                    "id": "prop-1",
                    "raw_text": "Make three-pointers worth 5 points",
                    "governor_id": player.id,
                    "team_id": team_ids[0],
                    "tier": 1,
                    "status": "submitted",
                },
            )

            # Submit a vote event
            await repo.append_event(
                event_type="vote.cast",
                aggregate_id="vote-1",
                aggregate_type="vote",
                season_id=season_id,
                governor_id=player.id,
                team_id=team_ids[0],
                payload={
                    "proposal_id": "prop-1",
                    "vote": "yes",
                    "weight": 1.0,
                },
            )

            await session.commit()
            player_id = player.id

        r = await client.get(f"/governors/{player_id}")
        assert r.status_code == 200
        assert "ActiveGovernor" in r.text
        assert "Proposal History" in r.text
        assert "three-pointers" in r.text

    async def test_governor_page_404(self, app_client):
        """Nonexistent governor ID should return 404."""
        client, _ = app_client
        r = await client.get("/governors/nonexistent")
        assert r.status_code == 404

    async def test_team_page_shows_governor_links(self, app_client):
        """Team page should show governor names as links when governors exist."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        # Enroll a governor on team 1
        async with get_session(engine) as session:
            repo = Repository(session)
            player = await repo.get_or_create_player(
                discord_id="777888999",
                username="LinkedGovernor",
            )
            await repo.enroll_player(player.id, team_ids[0], season_id)
            await session.commit()
            player_id = player.id

        r = await client.get(f"/teams/{team_ids[0]}")
        assert r.status_code == 200
        assert "Governors" in r.text
        assert "LinkedGovernor" in r.text
        assert f"/governors/{player_id}" in r.text


@pytest.fixture
async def admin_client():
    """Create a test app without OAuth for admin page testing."""
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        pinwheel_env="development",
        discord_client_id="",
        discord_client_secret="",
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


class TestAdminRoster:
    """Tests for the /admin/roster page."""

    async def test_admin_roster_empty(self, admin_client):
        """Admin roster page renders with no data."""
        client, _ = admin_client
        r = await client.get("/admin/roster")
        assert r.status_code == 200
        assert "Governor Roster" in r.text
        assert "No Governors Yet" in r.text

    async def test_admin_roster_with_governors(self, admin_client):
        """Admin roster shows enrolled governors after seeding."""
        client, engine = admin_client
        season_id, team_ids = await _seed_season(engine)

        # Enroll a governor
        async with get_session(engine) as session:
            repo = Repository(session)
            player = await repo.get_or_create_player(
                discord_id="111222333",
                username="RosterGovernor",
            )
            await repo.enroll_player(player.id, team_ids[0], season_id)
            await session.commit()

        r = await client.get("/admin/roster")
        assert r.status_code == 200
        assert "RosterGovernor" in r.text
        assert "Team 1" in r.text
        assert "Governor Roster" in r.text

    async def test_admin_roster_shows_multiple_governors(self, admin_client):
        """Admin roster shows all enrolled governors."""
        client, engine = admin_client
        season_id, team_ids = await _seed_season(engine)

        async with get_session(engine) as session:
            repo = Repository(session)
            for i, (disc_id, name) in enumerate(
                [("111", "Gov1"), ("222", "Gov2"), ("333", "Gov3")]
            ):
                player = await repo.get_or_create_player(
                    discord_id=disc_id,
                    username=name,
                )
                await repo.enroll_player(player.id, team_ids[i % len(team_ids)], season_id)
            await session.commit()

        r = await client.get("/admin/roster")
        assert r.status_code == 200
        assert "Gov1" in r.text
        assert "Gov2" in r.text
        assert "Gov3" in r.text
        assert "3 Governor" in r.text


class TestAdminSeason:
    """Tests for the /admin/season page."""

    async def test_admin_season_empty(self, admin_client):
        """Admin season page renders with no data."""
        client, _ = admin_client
        r = await client.get("/admin/season")
        assert r.status_code == 200
        assert "Season Admin" in r.text
        assert "No Active Season" in r.text

    async def test_admin_season_with_data(self, admin_client):
        """Admin season page shows current season info after seeding."""
        client, engine = admin_client
        await _seed_season(engine)

        r = await client.get("/admin/season")
        assert r.status_code == 200
        assert "Season Admin" in r.text
        assert "Season 1" in r.text
        assert "Runtime Configuration" in r.text
        assert "SLOW" in r.text or "slow" in r.text.lower()

    async def test_admin_season_shows_runtime_config(self, admin_client):
        """Admin season page shows pace, auto-advance, and other settings."""
        client, engine = admin_client
        await _seed_season(engine)

        r = await client.get("/admin/season")
        assert r.status_code == 200
        assert "Pace" in r.text
        assert "Auto-Advance" in r.text
        assert "Governance Interval" in r.text
        assert "Evals" in r.text

    async def test_admin_season_shows_history(self, admin_client):
        """Admin season page shows season history table."""
        client, engine = admin_client
        await _seed_season(engine)

        r = await client.get("/admin/season")
        assert r.status_code == 200
        assert "Season History" in r.text
        assert "CURRENT" in r.text

    async def test_admin_season_shows_quick_actions(self, admin_client):
        """Admin season page shows pace control buttons."""
        client, engine = admin_client
        await _seed_season(engine)

        r = await client.get("/admin/season")
        assert r.status_code == 200
        assert "Quick Actions" in r.text
        assert "FAST" in r.text
        assert "NORMAL" in r.text
        assert "SLOW" in r.text
        assert "MANUAL" in r.text


def _sign_session(secret_key: str, data: dict) -> str:
    """Create a signed session cookie value for testing."""
    serializer = URLSafeTimedSerializer(secret_key, salt="pinwheel-session")
    return serializer.dumps(data)


ADMIN_DISCORD_ID = "999888777"
NON_ADMIN_DISCORD_ID = "111222333"


@pytest.fixture
async def admin_auth_client():
    """App with admin Discord ID set + signed session cookie for admin user."""
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        pinwheel_env="production",
        pinwheel_admin_discord_id=ADMIN_DISCORD_ID,
        session_secret_key="test-secret-key",
        discord_client_id="",
        discord_client_secret="",
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
        yield client, settings

    await engine.dispose()


class TestAdminLandingPage:
    """Tests for the /admin landing page."""

    async def test_admin_page_renders_for_admin(self, admin_auth_client):
        """Admin landing page renders for authenticated admin user."""
        client, settings = admin_auth_client
        cookie = _sign_session(
            settings.session_secret_key,
            {
                "discord_id": ADMIN_DISCORD_ID,
                "username": "TheAdmin",
                "avatar_url": "",
            },
        )
        client.cookies.set("pinwheel_session", cookie)

        r = await client.get("/admin")
        assert r.status_code == 200
        assert "Admin" in r.text
        assert "Season" in r.text
        assert "Governors" in r.text
        assert "Evals" in r.text

    async def test_admin_page_403_for_non_admin(self, admin_auth_client):
        """Non-admin authenticated user gets 403."""
        client, settings = admin_auth_client
        cookie = _sign_session(
            settings.session_secret_key,
            {
                "discord_id": NON_ADMIN_DISCORD_ID,
                "username": "RegularUser",
                "avatar_url": "",
            },
        )
        client.cookies.set("pinwheel_session", cookie)

        r = await client.get("/admin")
        assert r.status_code == 403

    async def test_admin_page_redirect_unauthenticated(self, admin_auth_client):
        """Unauthenticated user with OAuth enabled gets redirected to login."""
        client, settings = admin_auth_client
        # Enable OAuth so redirect kicks in
        settings.discord_client_id = "fake-client-id"
        settings.discord_client_secret = "fake-secret"

        r = await client.get("/admin", follow_redirects=False)
        assert r.status_code == 302
        assert "/auth/login" in r.headers["location"]

    async def test_admin_page_403_unauthenticated_no_oauth(self, admin_auth_client):
        """Unauthenticated user without OAuth gets 403."""
        client, _ = admin_auth_client
        r = await client.get("/admin")
        assert r.status_code == 403

    async def test_admin_nav_visible_for_admin(self, admin_auth_client):
        """Admin nav link appears on any page for admin users."""
        client, settings = admin_auth_client
        cookie = _sign_session(
            settings.session_secret_key,
            {
                "discord_id": ADMIN_DISCORD_ID,
                "username": "TheAdmin",
                "avatar_url": "",
            },
        )
        client.cookies.set("pinwheel_session", cookie)

        r = await client.get("/")
        assert r.status_code == 200
        assert 'href="/admin"' in r.text

    async def test_admin_nav_hidden_for_non_admin(self, admin_auth_client):
        """Admin nav link is NOT visible for non-admin users."""
        client, settings = admin_auth_client
        cookie = _sign_session(
            settings.session_secret_key,
            {
                "discord_id": NON_ADMIN_DISCORD_ID,
                "username": "RegularUser",
                "avatar_url": "",
            },
        )
        client.cookies.set("pinwheel_session", cookie)

        r = await client.get("/")
        assert r.status_code == 200
        assert 'href="/admin"' not in r.text

    async def test_admin_nav_hidden_when_logged_out(self, admin_auth_client):
        """Admin nav link is NOT visible when no user is logged in."""
        client, _ = admin_auth_client
        r = await client.get("/")
        assert r.status_code == 200
        assert 'href="/admin"' not in r.text
