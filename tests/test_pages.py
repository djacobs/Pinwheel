"""Tests for page routes — verify HTML rendering works."""

import pytest
from httpx import ASGITransport, AsyncClient

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
        assert "Governance" in r.text

    async def test_rules_empty(self, app_client):
        client, _ = app_client
        r = await client.get("/rules")
        assert r.status_code == 200
        assert "The Rules" in r.text
        assert "Game Mechanics" in r.text

    async def test_mirrors_empty(self, app_client):
        client, _ = app_client
        r = await client.get("/mirrors")
        assert r.status_code == 200
        assert "No Mirrors Yet" in r.text

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

    async def test_mirrors_with_data(self, app_client):
        client, engine = app_client
        await _seed_season(engine)

        r = await client.get("/mirrors")
        assert r.status_code == 200
        # Should have simulation mirror from step_round
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
        assert "Governance" in r.text

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
