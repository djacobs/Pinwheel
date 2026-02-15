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

    @pytest.mark.parametrize(
        "path, sentinel",
        [
            ("/", "PINWHEEL"),
            ("/arena", "No Games Yet"),
            ("/standings", "No Standings Yet"),
            ("/governance", "The Floor"),
            ("/rules", "The Rules"),
            ("/reports", "No Reports Yet"),
            ("/play", "Join the League"),
        ],
    )
    async def test_empty_page_renders(self, app_client, path, sentinel):
        """Each page returns 200 and contains its empty-state sentinel."""
        client, _ = app_client
        r = await client.get(path)
        assert r.status_code == 200
        assert sentinel in r.text


class TestPopulatedPages:
    """Pages with seeded data should render game results."""

    async def test_home_with_data(self, app_client):
        client, engine = app_client
        await _seed_season(engine)

        r = await client.get("/")
        assert r.status_code == 200
        assert "Latest Results" in r.text

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
        """Game detail page shows box score, play-by-play, and hooper links."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        async with get_session(engine) as session:
            repo = Repository(session)
            games = await repo.get_games_for_round(season_id, 1)
            game_id = games[0].id

        r = await client.get(f"/games/{game_id}")
        assert r.status_code == 200
        assert "Box Score" in r.text
        assert "/hoopers/" in r.text

    async def test_game_404(self, app_client):
        client, _ = app_client
        r = await client.get("/games/nonexistent")
        assert r.status_code == 404

    async def test_team_page(self, app_client):
        """Team page shows name, roster with hooper links, and spider charts."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        r = await client.get(f"/teams/{team_ids[0]}")
        assert r.status_code == 200
        assert "Team 1" in r.text
        assert "Hooper-1-1" in r.text
        assert "/hoopers/" in r.text
        assert "<svg" in r.text

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


class TestHooperPages:
    """Tests for individual hooper pages."""

    async def test_hooper_page(self, app_client):
        """Hooper page renders with name, spider chart, game log, averages, and team link."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        async with get_session(engine) as session:
            repo = Repository(session)
            team = await repo.get_team(team_ids[0])
            hooper_id = team.hoopers[0].id
            hooper_name = team.hoopers[0].name

        r = await client.get(f"/hoopers/{hooper_id}")
        assert r.status_code == 200
        assert hooper_name in r.text
        assert "<svg" in r.text
        assert "Game Log" in r.text
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

    async def test_team_page_cross_season(self, app_client):
        """Team page for an old season's team still shows standings and roster.

        When a new season is active, visiting a team page via an old-season
        game link should use the team's own season for context — not the
        active season — so that standings, hoopers, and governors are correct.
        """
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)
        old_team_id = team_ids[0]

        # Enroll a governor on team 1 (old season)
        async with get_session(engine) as session:
            repo = Repository(session)
            player = await repo.get_or_create_player(
                discord_id="111222333",
                username="OldSeasonGov",
            )
            await repo.enroll_player(player.id, old_team_id, season_id)
            await session.commit()

        # Start a new season (creates new team records)
        from pinwheel.core.season import start_new_season

        async with get_session(engine) as session:
            repo = Repository(session)
            season = await repo.get_season(season_id)
            season.status = "completed"
            await session.flush()

            await start_new_season(
                repo=repo,
                league_id=season.league_id,
                season_name="Season 2",
                carry_forward_rules=True,
                previous_season_id=season_id,
            )
            await session.commit()

        # Visit the OLD season's team page
        r = await client.get(f"/teams/{old_team_id}")
        assert r.status_code == 200
        # Must still show the team name, roster, and standings
        assert "Team 1" in r.text
        assert "Hooper-1-1" in r.text
        assert "<svg" in r.text
        # Standings should reflect old season data (team played games)
        assert "Record" in r.text


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
        """Admin season page shows season info, runtime config, and history."""
        client, engine = admin_client
        await _seed_season(engine)

        r = await client.get("/admin/season")
        assert r.status_code == 200
        assert "Season 1" in r.text
        assert "Pace" in r.text
        assert "Season History" in r.text


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


class TestGovernancePhaseContext:
    """Governance page should pass season_phase context to template."""

    async def test_governance_renders_without_phase(self, app_client):
        """Governance page renders when no season exists (no phase)."""
        client, _ = app_client
        r = await client.get("/governance")
        assert r.status_code == 200
        assert "The Floor" in r.text
        # No phase badge should appear
        assert "PLAYOFFS" not in r.text
        assert "CHAMPIONSHIP" not in r.text

    async def test_governance_with_data(self, app_client):
        """Governance page renders normally with seeded data."""
        client, engine = app_client
        await _seed_season(engine)

        r = await client.get("/governance")
        assert r.status_code == 200
        assert "The Floor" in r.text

    async def test_governance_playoff_phase_badge(self, app_client):
        """Governance page shows PLAYOFFS badge during playoff phase."""
        client, engine = app_client
        season_id, _ = await _seed_season(engine)

        # Set season status to playoffs
        async with get_session(engine) as session:
            repo = Repository(session)
            season = await repo.get_season(season_id)
            season.status = "playoffs"
            await session.commit()

        r = await client.get("/governance")
        assert r.status_code == 200
        assert "PLAYOFFS" in r.text
        assert "elimination" in r.text.lower()

    async def test_governance_championship_phase_badge(self, app_client):
        """Governance page shows CHAMPIONSHIP badge during championship."""
        client, engine = app_client
        season_id, _ = await _seed_season(engine)

        # Set season status to championship
        async with get_session(engine) as session:
            repo = Repository(session)
            season = await repo.get_season(season_id)
            season.status = "championship"
            await session.commit()

        r = await client.get("/governance")
        assert r.status_code == 200
        assert "CHAMPIONSHIP" in r.text


class TestReportsPhaseContext:
    """Reports page should tag reports with phase context."""

    async def test_reports_renders_without_phase(self, app_client):
        """Reports page renders when no season exists."""
        client, _ = app_client
        r = await client.get("/reports")
        assert r.status_code == 200
        assert "No Reports Yet" in r.text

    async def test_reports_with_data(self, app_client):
        """Reports page renders reports after seeding."""
        client, engine = app_client
        await _seed_season(engine)

        r = await client.get("/reports")
        assert r.status_code == 200
        assert "Simulation" in r.text or "simulation" in r.text


class TestBuildSeriesContext:
    """Unit tests for build_series_context helper."""

    def test_semifinal_tied(self):
        from pinwheel.api.pages import build_series_context

        ctx = build_series_context(
            phase="semifinal",
            home_team_name="Thorns",
            away_team_name="Storm",
            home_wins=0,
            away_wins=0,
            best_of=3,
        )
        assert ctx["phase"] == "semifinal"
        assert ctx["phase_label"] == "SEMIFINAL SERIES"
        assert ctx["home_wins"] == 0
        assert ctx["away_wins"] == 0
        assert ctx["best_of"] == 3
        assert ctx["wins_needed"] == 2
        assert "Series tied 0-0" in ctx["description"]
        assert "First to 2 wins advances" in ctx["description"]

    def test_semifinal_home_leads(self):
        from pinwheel.api.pages import build_series_context

        ctx = build_series_context(
            phase="semifinal",
            home_team_name="Thorns",
            away_team_name="Storm",
            home_wins=1,
            away_wins=0,
            best_of=3,
        )
        assert "Thorns lead 1-0" in ctx["description"]
        assert "First to 2 wins advances" in ctx["description"]

    def test_semifinal_away_leads(self):
        from pinwheel.api.pages import build_series_context

        ctx = build_series_context(
            phase="semifinal",
            home_team_name="Thorns",
            away_team_name="Storm",
            home_wins=0,
            away_wins=1,
            best_of=3,
        )
        assert "Storm lead 1-0" in ctx["description"]

    def test_finals_tied(self):
        from pinwheel.api.pages import build_series_context

        ctx = build_series_context(
            phase="finals",
            home_team_name="Thorns",
            away_team_name="Storm",
            home_wins=1,
            away_wins=1,
            best_of=5,
        )
        assert ctx["phase_label"] == "CHAMPIONSHIP FINALS"
        assert "Series tied 1-1" in ctx["description"]
        assert "First to 3 wins is champion" in ctx["description"]
        assert ctx["wins_needed"] == 3

    def test_finals_near_clinch(self):
        from pinwheel.api.pages import build_series_context

        ctx = build_series_context(
            phase="finals",
            home_team_name="Thorns",
            away_team_name="Storm",
            home_wins=2,
            away_wins=1,
            best_of=5,
        )
        assert "Thorns lead 2-1" in ctx["description"]


class TestArenaSeriesContextLive:
    """Tests for series context display in the live arena."""

    async def test_live_game_with_series_context(self, app_client):
        """Live game with series_context should render the series banner."""
        client, engine = app_client
        from pinwheel.core.presenter import LiveGameState

        pstate = client._transport.app.state.presentation_state  # type: ignore
        pstate.is_active = True
        pstate.current_round = 7
        pstate.live_games = {
            0: LiveGameState(
                game_index=0,
                game_id="g-7-0",
                home_team_id="team-a",
                away_team_id="team-b",
                home_team_name="Thorns",
                away_team_name="Storm",
                home_score=15,
                away_score=12,
                quarter=1,
                game_clock="5:00",
                status="live",
                series_context={
                    "phase": "semifinal",
                    "phase_label": "SEMIFINAL SERIES",
                    "home_wins": 0,
                    "away_wins": 0,
                    "best_of": 3,
                    "wins_needed": 2,
                    "description": (
                        "SEMIFINAL SERIES \u00b7 Series tied 0-0"
                        " \u00b7 First to 2 wins advances"
                    ),
                },
            ),
        }

        r = await client.get("/arena")
        assert r.status_code == 200
        assert "SEMIFINAL SERIES" in r.text
        assert "Series tied 0-0" in r.text
        assert "First to 2 wins advances" in r.text
        assert "series-context--semifinal" in r.text

        pstate.reset()

    async def test_live_game_finals_series_context(self, app_client):
        """Live finals game should render the championship series banner."""
        client, engine = app_client
        from pinwheel.core.presenter import LiveGameState

        pstate = client._transport.app.state.presentation_state  # type: ignore
        pstate.is_active = True
        pstate.current_round = 9
        pstate.live_games = {
            0: LiveGameState(
                game_index=0,
                game_id="g-9-0",
                home_team_id="team-a",
                away_team_id="team-b",
                home_team_name="Thorns",
                away_team_name="Storm",
                home_score=30,
                away_score=28,
                quarter=3,
                game_clock="2:30",
                status="live",
                series_context={
                    "phase": "finals",
                    "phase_label": "CHAMPIONSHIP FINALS",
                    "home_wins": 1,
                    "away_wins": 1,
                    "best_of": 5,
                    "wins_needed": 3,
                    "description": (
                        "CHAMPIONSHIP FINALS \u00b7"
                        " Series tied 1-1 \u00b7"
                        " First to 3 wins is champion"
                    ),
                },
            ),
        }

        r = await client.get("/arena")
        assert r.status_code == 200
        assert "CHAMPIONSHIP FINALS" in r.text
        assert "Series tied 1-1" in r.text
        assert "First to 3 wins is champion" in r.text
        assert "series-context--finals" in r.text

        pstate.reset()

    async def test_live_game_without_series_context(self, app_client):
        """Live regular season game should NOT show series context."""
        client, engine = app_client
        from pinwheel.core.presenter import LiveGameState

        pstate = client._transport.app.state.presentation_state  # type: ignore
        pstate.is_active = True
        pstate.current_round = 3
        pstate.live_games = {
            0: LiveGameState(
                game_index=0,
                game_id="g-3-0",
                home_team_id="team-a",
                away_team_id="team-b",
                home_team_name="Thorns",
                away_team_name="Storm",
                home_score=10,
                away_score=8,
                quarter=1,
                game_clock="7:00",
                status="live",
                # No series_context — regular season
            ),
        }

        r = await client.get("/arena")
        assert r.status_code == 200
        assert "SEMIFINAL SERIES" not in r.text
        assert "CHAMPIONSHIP FINALS" not in r.text
        # Hidden series-context div should still be present (for SSE)
        assert 'data-g="0"' in r.text

        pstate.reset()
