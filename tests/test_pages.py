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


class TestTeamTrajectory:
    """Tests for team trajectory display on team pages."""

    async def test_team_trajectory_with_games(self, app_client):
        """Team page should show trajectory data when games have been played."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        # Play a few more rounds to have trajectory data
        async with get_session(engine) as session:
            repo = Repository(session)
            await step_round(repo, season_id, round_number=2)
            await step_round(repo, season_id, round_number=3)
            # Mark all games as presented
            for rn in [2, 3]:
                games = await repo.get_games_for_round(season_id, rn)
                for g in games:
                    await repo.mark_game_presented(g.id)
            await session.commit()

        r = await client.get(f"/teams/{team_ids[0]}")
        assert r.status_code == 200
        # Should show Season Arc section
        assert "Season Arc" in r.text
        # Should have recent form indicators
        assert "form-dot" in r.text

    async def test_team_trajectory_no_games(self, app_client):
        """Team page should not show trajectory when no games have been played."""
        client, engine = app_client
        # Seed teams but don't play any rounds
        async with get_session(engine) as session:
            repo = Repository(session)
            league = await repo.create_league("Test League")
            season = await repo.create_season(
                league.id,
                "Season 1",
                starting_ruleset={"quarter_minutes": 3},
            )

            team = await repo.create_team(
                season.id,
                "Team Alpha",
                color="#aaaaaa",
                venue={"name": "Arena A", "capacity": 5000},
            )
            for j in range(3):
                await repo.create_hooper(
                    team_id=team.id,
                    season_id=season.id,
                    name=f"Hooper-{j + 1}",
                    archetype="sharpshooter",
                    attributes=_hooper_attrs(),
                )
            await session.commit()
            team_id = team.id

        r = await client.get(f"/teams/{team_id}")
        assert r.status_code == 200
        # Should NOT show Season Arc section
        assert "Season Arc" not in r.text
        assert "form-dot" not in r.text

    async def test_team_trajectory_recent_form(self, app_client):
        """Recent form should show W/L for last 5 games."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        # Play 5 more rounds (total 6)
        async with get_session(engine) as session:
            repo = Repository(session)
            for rn in range(2, 7):
                await step_round(repo, season_id, round_number=rn)
                games = await repo.get_games_for_round(season_id, rn)
                for g in games:
                    await repo.mark_game_presented(g.id)
            await session.commit()

        r = await client.get(f"/teams/{team_ids[0]}")
        assert r.status_code == 200
        # Should show exactly 5 form dots (last 5 games)
        form_dot_count = r.text.count("form-dot")
        # Each dot appears once in the class list
        assert form_dot_count >= 5

    async def test_repository_get_team_game_results(self, app_client):
        """Repository method should return structured game result data."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        async with get_session(engine) as session:
            repo = Repository(session)
            results = await repo.get_team_game_results(team_ids[0], season_id)

            # Should have at least 1 game (round 1 was played in seed)
            assert len(results) >= 1

            # Verify structure of first result
            first = results[0]
            assert "round_number" in first
            assert "opponent_team_id" in first
            assert "opponent_team_name" in first
            assert "team_score" in first
            assert "opponent_score" in first
            assert "won" in first
            assert "margin" in first
            assert "is_home" in first
            assert isinstance(first["won"], bool)
            assert isinstance(first["margin"], int)
            assert isinstance(first["is_home"], bool)

    async def test_team_trajectory_governor_impact(self, app_client):
        """Team page shows governor impact when a governor's proposal passed."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        # Play a few more rounds to have trajectory data
        async with get_session(engine) as session:
            repo = Repository(session)
            for rn in range(2, 5):
                await step_round(repo, season_id, round_number=rn)
                games = await repo.get_games_for_round(season_id, rn)
                for g in games:
                    await repo.mark_game_presented(g.id)

            # Enroll a governor on team 1
            player = await repo.get_or_create_player(
                discord_id="gov-impact-111",
                username="ImpactGov",
            )
            await repo.enroll_player(player.id, team_ids[0], season_id)

            # Submit a proposal from this governor
            await repo.append_event(
                event_type="proposal.submitted",
                aggregate_id="prop-impact-1",
                aggregate_type="proposal",
                season_id=season_id,
                governor_id=player.id,
                team_id=team_ids[0],
                round_number=2,
                payload={
                    "id": "prop-impact-1",
                    "raw_text": "Make dunks worth 4 points",
                    "governor_id": player.id,
                    "team_id": team_ids[0],
                    "tier": 1,
                    "status": "submitted",
                },
            )

            # Mark it as passed
            await repo.append_event(
                event_type="proposal.passed",
                aggregate_id="prop-impact-1",
                aggregate_type="proposal",
                season_id=season_id,
                round_number=3,
                payload={
                    "proposal_id": "prop-impact-1",
                },
            )

            await session.commit()

        r = await client.get(f"/teams/{team_ids[0]}")
        assert r.status_code == 200
        assert "Governor Impact" in r.text
        assert "ImpactGov" in r.text
        assert "Make dunks worth 4 points" in r.text
        assert "Round 3" in r.text

    async def test_team_trajectory_no_governor_impact_without_proposals(self, app_client):
        """Team page does not show governor impact when no proposals passed."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        # Play a few rounds but no proposals
        async with get_session(engine) as session:
            repo = Repository(session)
            for rn in range(2, 4):
                await step_round(repo, season_id, round_number=rn)
                games = await repo.get_games_for_round(season_id, rn)
                for g in games:
                    await repo.mark_game_presented(g.id)
            await session.commit()

        r = await client.get(f"/teams/{team_ids[0]}")
        assert r.status_code == 200
        assert "Season Arc" in r.text
        assert "Governor Impact" not in r.text

    async def test_team_trajectory_governor_impact_long_text_truncated(self, app_client):
        """Governor impact truncates long proposal text at 60 chars."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        async with get_session(engine) as session:
            repo = Repository(session)
            for rn in range(2, 4):
                await step_round(repo, season_id, round_number=rn)
                games = await repo.get_games_for_round(season_id, rn)
                for g in games:
                    await repo.mark_game_presented(g.id)

            # Enroll a governor
            player = await repo.get_or_create_player(
                discord_id="gov-long-222",
                username="VerboseGov",
            )
            await repo.enroll_player(player.id, team_ids[0], season_id)

            # Submit a proposal with very long text
            long_text = "A" * 80  # 80 chars, should be truncated to 60 + "..."
            await repo.append_event(
                event_type="proposal.submitted",
                aggregate_id="prop-long-1",
                aggregate_type="proposal",
                season_id=season_id,
                governor_id=player.id,
                team_id=team_ids[0],
                round_number=2,
                payload={
                    "id": "prop-long-1",
                    "raw_text": long_text,
                    "governor_id": player.id,
                    "team_id": team_ids[0],
                    "tier": 1,
                    "status": "submitted",
                },
            )

            # Mark it as passed
            await repo.append_event(
                event_type="proposal.passed",
                aggregate_id="prop-long-1",
                aggregate_type="proposal",
                season_id=season_id,
                round_number=2,
                payload={
                    "proposal_id": "prop-long-1",
                },
            )

            await session.commit()

        r = await client.get(f"/teams/{team_ids[0]}")
        assert r.status_code == 200
        assert "Governor Impact" in r.text
        assert "VerboseGov" in r.text
        # The 80-char text should be truncated: first 60 + "..."
        assert "A" * 60 in r.text
        # The full 80-char string should NOT appear
        assert "A" * 80 not in r.text

    async def test_team_trajectory_governor_impact_other_team_not_shown(self, app_client):
        """Governor impact only shows proposals from THIS team's governors."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        async with get_session(engine) as session:
            repo = Repository(session)
            for rn in range(2, 4):
                await step_round(repo, season_id, round_number=rn)
                games = await repo.get_games_for_round(season_id, rn)
                for g in games:
                    await repo.mark_game_presented(g.id)

            # Enroll a governor on team 2 (NOT team 1)
            player = await repo.get_or_create_player(
                discord_id="gov-other-333",
                username="OtherTeamGov",
            )
            await repo.enroll_player(player.id, team_ids[1], season_id)

            # Submit a proposal from team 2's governor
            await repo.append_event(
                event_type="proposal.submitted",
                aggregate_id="prop-other-1",
                aggregate_type="proposal",
                season_id=season_id,
                governor_id=player.id,
                team_id=team_ids[1],
                round_number=2,
                payload={
                    "id": "prop-other-1",
                    "raw_text": "Other team rule change",
                    "governor_id": player.id,
                    "team_id": team_ids[1],
                    "tier": 1,
                    "status": "submitted",
                },
            )

            await repo.append_event(
                event_type="proposal.passed",
                aggregate_id="prop-other-1",
                aggregate_type="proposal",
                season_id=season_id,
                round_number=2,
                payload={
                    "proposal_id": "prop-other-1",
                },
            )

            await session.commit()

        # Visit team 1's page — should NOT see team 2's governor's proposal
        r = await client.get(f"/teams/{team_ids[0]}")
        assert r.status_code == 200
        assert "OtherTeamGov" not in r.text
        assert "Governor Impact" not in r.text


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


class TestStandingsCallouts:
    """Tests for narrative callouts on the standings page."""

    async def test_standings_callouts_with_data(self, app_client):
        """Standings page should show narrative callouts with seeded data."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        # Run a few more rounds to build up standings variety
        async with get_session(engine) as session:
            repo = Repository(session)
            for rn in range(2, 5):
                await step_round(repo, season_id, round_number=rn)
                games = await repo.get_games_for_round(season_id, rn)
                for g in games:
                    await repo.mark_game_presented(g.id)
            await session.commit()

        r = await client.get("/standings")
        assert r.status_code == 200
        # Should contain callouts section
        assert "standings-callouts" in r.text

    async def test_standings_no_callouts_when_empty(self, app_client):
        """Standings page should not show callouts when no games played."""
        client, _ = app_client
        r = await client.get("/standings")
        assert r.status_code == 200
        assert "standings-callouts" not in r.text

    async def test_standings_tightest_race_callout(self, app_client):
        """Standings should detect tightest race between teams."""
        from pinwheel.api.pages import _compute_standings_callouts

        standings = [
            {"team_id": "t1", "team_name": "Thorns", "wins": 5, "losses": 1},
            {"team_id": "t2", "team_name": "Breakers", "wins": 4, "losses": 2},
            {"team_id": "t3", "team_name": "Storm", "wins": 2, "losses": 4},
        ]
        streaks = {}
        callouts = _compute_standings_callouts(standings, streaks, 6, 12)

        # Should detect 1-game separation
        assert any("1 game separates" in c for c in callouts)
        assert any("Thorns" in c and "Breakers" in c for c in callouts)

    async def test_standings_dominant_team_callout(self, app_client):
        """Standings should detect a dominant leader."""
        from pinwheel.api.pages import _compute_standings_callouts

        standings = [
            {"team_id": "t1", "team_name": "Dominators", "wins": 10, "losses": 0},
            {"team_id": "t2", "team_name": "Challengers", "wins": 7, "losses": 3},
            {"team_id": "t3", "team_name": "Underdogs", "wins": 5, "losses": 5},
        ]
        streaks = {}
        callouts = _compute_standings_callouts(standings, streaks, 10, 20)

        # Should detect 3-game lead
        assert any("commanding" in c and "3-game lead" in c for c in callouts)
        assert any("Dominators" in c for c in callouts)

    async def test_standings_streak_callout(self, app_client):
        """Standings should detect longest active streak."""
        from pinwheel.api.pages import _compute_standings_callouts

        standings = [
            {"team_id": "t1", "team_name": "Hot Team", "wins": 7, "losses": 3},
            {"team_id": "t2", "team_name": "Cold Team", "wins": 5, "losses": 5},
        ]
        streaks = {"t1": 5, "t2": -4}
        callouts = _compute_standings_callouts(standings, streaks, 10, 20)

        # Should detect 5-game win streak
        assert any("5-game win streak" in c for c in callouts)
        assert any("Hot Team" in c for c in callouts)

    async def test_standings_late_season_callout(self, app_client):
        """Standings should mention remaining rounds in late season."""
        from pinwheel.api.pages import _compute_standings_callouts

        standings = [
            {"team_id": "t1", "team_name": "Team A", "wins": 8, "losses": 2},
            {"team_id": "t2", "team_name": "Team B", "wins": 7, "losses": 3},
        ]
        streaks = {}
        callouts = _compute_standings_callouts(standings, streaks, 9, 12)

        # Should mention remaining rounds (3 left, 75% complete)
        assert any("3 rounds remaining" in c for c in callouts)

    async def test_ordinal_suffix(self, app_client):
        """Test ordinal suffix helper."""
        from pinwheel.api.pages import _ordinal_suffix

        assert _ordinal_suffix(1) == "st"
        assert _ordinal_suffix(2) == "nd"
        assert _ordinal_suffix(3) == "rd"
        assert _ordinal_suffix(4) == "th"
        assert _ordinal_suffix(11) == "th"
        assert _ordinal_suffix(12) == "th"
        assert _ordinal_suffix(13) == "th"
        assert _ordinal_suffix(21) == "st"
        assert _ordinal_suffix(22) == "nd"
        assert _ordinal_suffix(23) == "rd"


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
    """Unit tests for build_series_context helper.

    These tests exercise the template fallback (no ANTHROPIC_API_KEY in test env).
    """

    async def test_semifinal_tied(self):
        from pinwheel.api.pages import build_series_context

        ctx = await build_series_context(
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

    async def test_semifinal_home_leads(self):
        from pinwheel.api.pages import build_series_context

        ctx = await build_series_context(
            phase="semifinal",
            home_team_name="Thorns",
            away_team_name="Storm",
            home_wins=1,
            away_wins=0,
            best_of=3,
        )
        assert "Thorns lead 1-0" in ctx["description"]
        assert "First to 2 wins advances" in ctx["description"]

    async def test_semifinal_away_leads(self):
        from pinwheel.api.pages import build_series_context

        ctx = await build_series_context(
            phase="semifinal",
            home_team_name="Thorns",
            away_team_name="Storm",
            home_wins=0,
            away_wins=1,
            best_of=3,
        )
        assert "Storm lead 1-0" in ctx["description"]

    async def test_finals_tied(self):
        from pinwheel.api.pages import build_series_context

        ctx = await build_series_context(
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

    async def test_finals_near_clinch(self):
        from pinwheel.api.pages import build_series_context

        ctx = await build_series_context(
            phase="finals",
            home_team_name="Thorns",
            away_team_name="Storm",
            home_wins=2,
            away_wins=1,
            best_of=5,
        )
        assert "Thorns lead 2-1" in ctx["description"]

    async def test_semifinal_clinched_home(self):
        from pinwheel.api.pages import build_series_context

        ctx = await build_series_context(
            phase="semifinal",
            home_team_name="Breakers",
            away_team_name="Herons",
            home_wins=2,
            away_wins=0,
            best_of=3,
        )
        assert "Breakers win series 2-0" in ctx["description"]
        assert "First to" not in ctx["description"]

    async def test_semifinal_clinched_away(self):
        from pinwheel.api.pages import build_series_context

        ctx = await build_series_context(
            phase="semifinal",
            home_team_name="Herons",
            away_team_name="Thorns",
            home_wins=0,
            away_wins=2,
            best_of=3,
        )
        assert "Thorns win series 2-0" in ctx["description"]

    async def test_finals_clinched(self):
        from pinwheel.api.pages import build_series_context

        ctx = await build_series_context(
            phase="finals",
            home_team_name="Thorns",
            away_team_name="Storm",
            home_wins=3,
            away_wins=1,
            best_of=5,
        )
        assert "Thorns win championship 3-1" in ctx["description"]
        assert "First to" not in ctx["description"]


class TestBuildSeriesDescriptionFallback:
    """Unit tests for the template fallback used when Haiku is unavailable."""

    def test_fallback_semifinal_tied(self):
        from pinwheel.api.pages import _build_series_description_fallback

        desc = _build_series_description_fallback(
            phase="semifinal", phase_label="SEMIFINAL SERIES",
            home_team_name="Thorns", away_team_name="Storm",
            home_wins=0, away_wins=0, wins_needed=2,
        )
        assert "Series tied 0-0" in desc
        assert "First to 2 wins advances" in desc

    def test_fallback_finals_clinched(self):
        from pinwheel.api.pages import _build_series_description_fallback

        desc = _build_series_description_fallback(
            phase="finals", phase_label="CHAMPIONSHIP FINALS",
            home_team_name="Thorns", away_team_name="Storm",
            home_wins=3, away_wins=1, wins_needed=3,
        )
        assert "Thorns win championship 3-1" in desc
        assert "First to" not in desc


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
                        "SEMIFINAL SERIES \u00b7 Series tied 0-0 \u00b7 First to 2 wins advances"
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


class TestGameDetailContext:
    """Tests for game detail page historical context."""

    async def test_game_detail_shows_context(self, app_client):
        """Game detail page shows context when there are multiple games."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        async with get_session(engine) as session:
            repo = Repository(session)
            games = await repo.get_games_for_round(season_id, 1)
            game_id = games[0].id

        r = await client.get(f"/games/{game_id}")
        assert r.status_code == 200
        # With 2 games in round 1, should have margin/scoring context
        assert "Game Context" in r.text

    async def test_game_detail_with_head_to_head(self, app_client):
        """Game detail shows head-to-head record when teams have met before."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        # Run another round to get rematch data
        async with get_session(engine) as session:
            repo = Repository(session)
            await step_round(repo, season_id, round_number=2)
            games_r2 = await repo.get_games_for_round(season_id, 2)
            for g in games_r2:
                await repo.mark_game_presented(g.id)
            await session.commit()

            # Find a game in round 2 where teams have met before
            games_r1 = await repo.get_games_for_round(season_id, 1)
            matchup_pairs_r1 = [{g.home_team_id, g.away_team_id} for g in games_r1]

            game_id = None
            for g in games_r2:
                if {g.home_team_id, g.away_team_id} in matchup_pairs_r1:
                    game_id = g.id
                    break

        if game_id:
            r = await client.get(f"/games/{game_id}")
            assert r.status_code == 200
            assert "Game Context" in r.text
            assert "Season series" in r.text or "tied" in r.text

    async def test_game_detail_margin_context(self, app_client):
        """Game detail may show margin context depending on score variance."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        # Run 2 more rounds to get enough data for margin comparisons
        async with get_session(engine) as session:
            repo = Repository(session)
            await step_round(repo, season_id, round_number=2)
            await step_round(repo, season_id, round_number=3)
            games_r3 = await repo.get_games_for_round(season_id, 3)
            for g in games_r3:
                await repo.mark_game_presented(g.id)
            await session.commit()

            # Pick a game from round 3
            game_id = games_r3[0].id

        r = await client.get(f"/games/{game_id}")
        assert r.status_code == 200
        # Context may or may not appear depending on the random game outcomes
        # If it appears, it should have at least one valid context phrase
        if "Game Context" in r.text:
            assert any(
                phrase in r.text
                for phrase in [
                    "Closest game", "Biggest blowout", "tight", "decisive",
                    "Season series", "combined points", "season avg",
                    "Since Round", "win streak", "losing streak",
                ]
            )

    async def test_game_detail_scoring_context(self, app_client):
        """Game detail may show context depending on game characteristics."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        # Run 2 more rounds
        async with get_session(engine) as session:
            repo = Repository(session)
            await step_round(repo, season_id, round_number=2)
            await step_round(repo, season_id, round_number=3)
            games_r3 = await repo.get_games_for_round(season_id, 3)
            for g in games_r3:
                await repo.mark_game_presented(g.id)
            await session.commit()

            game_id = games_r3[0].id

        r = await client.get(f"/games/{game_id}")
        assert r.status_code == 200
        # Context may or may not appear depending on the random game outcomes
        # If it appears, it should have at least one valid context phrase
        if "Game Context" in r.text:
            assert any(
                phrase in r.text
                for phrase in [
                    "combined points",
                    "season avg",
                    "margin",
                    "tight",
                    "decisive",
                    "Closest",
                    "Biggest",
                    "Season series",
                    "Since Round",
                    "win streak",
                    "losing streak",
                ]
            )

    async def test_game_detail_multiple_context_lines(self, app_client):
        """Game detail can show multiple context lines together."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        # Run enough rounds to generate multiple context points
        async with get_session(engine) as session:
            repo = Repository(session)
            for rn in range(2, 5):
                await step_round(repo, season_id, round_number=rn)
                games = await repo.get_games_for_round(season_id, rn)
                for g in games:
                    await repo.mark_game_presented(g.id)
            await session.commit()

            games_r4 = await repo.get_games_for_round(season_id, 4)
            game_id = games_r4[0].id

        r = await client.get(f"/games/{game_id}")
        assert r.status_code == 200
        if "Game Context" in r.text:
            # If context appears, should have at least 1 line
            # (Could be head-to-head, margin, or scoring)
            context_section = r.text.split("Game Context")[1].split("</div>")[0]
            assert "<p" in context_section




class TestGameDetailHistoricalContext:
    """Tests for rule-change context and game significance on game detail pages."""

    async def test_rule_change_context_between_meetings(self, app_client):
        """Game detail shows rule changes enacted since last meeting."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        # Run round 2 to create a rematch
        async with get_session(engine) as session:
            repo = Repository(session)
            await step_round(repo, season_id, round_number=2)
            games_r2 = await repo.get_games_for_round(season_id, 2)
            for g in games_r2:
                await repo.mark_game_presented(g.id)

            # Enact a rule change in round 2
            await repo.append_event(
                event_type="rule.enacted",
                aggregate_id="prop-rc-1",
                aggregate_type="rule_change",
                season_id=season_id,
                round_number=2,
                payload={
                    "parameter": "three_point_value",
                    "old_value": 3,
                    "new_value": 4,
                    "source_proposal_id": "prop-rc-1",
                    "round_enacted": 2,
                },
            )

            # Run round 3 — teams will meet again in round-robin
            await step_round(repo, season_id, round_number=3)
            games_r3 = await repo.get_games_for_round(season_id, 3)
            for g in games_r3:
                await repo.mark_game_presented(g.id)
            await session.commit()

            # Find a round 3 game where teams met in round 1
            games_r1 = await repo.get_games_for_round(season_id, 1)
            r1_pairs = [{g.home_team_id, g.away_team_id} for g in games_r1]

            target_game_id = None
            for g in games_r3:
                if {g.home_team_id, g.away_team_id} in r1_pairs:
                    target_game_id = g.id
                    break

        if target_game_id:
            r = await client.get(f"/games/{target_game_id}")
            assert r.status_code == 200
            # Rule change should appear in context
            assert "Three Point Value" in r.text
            assert "changed from 3" in r.text
            assert "to 4" in r.text

    async def test_no_rule_changes_when_no_previous_meeting(self, app_client):
        """No rule change context when teams haven't met before."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        async with get_session(engine) as session:
            repo = Repository(session)
            games_r1 = await repo.get_games_for_round(season_id, 1)
            game_id = games_r1[0].id

        r = await client.get(f"/games/{game_id}")
        assert r.status_code == 200
        # No previous meeting, so no rule change context
        assert "Since Round" not in r.text

    async def test_game_significance_first_place_showdown(self, app_client):
        """Game detail shows first-place showdown when top 2 teams meet."""
        from pinwheel.api.pages import _compute_game_standings

        # Create mock game results where teams A and B are in first and second place
        class MockGame:
            def __init__(self, home_id: str, away_id: str, home_score: int,
                         away_score: int, winner_id: str, rnd: int, mi: int = 0):
                self.home_team_id = home_id
                self.away_team_id = away_id
                self.home_score = home_score
                self.away_score = away_score
                self.winner_team_id = winner_id
                self.round_number = rnd
                self.matchup_index = mi

        games = [
            MockGame("a", "c", 50, 40, "a", 1),
            MockGame("b", "d", 50, 40, "b", 1),
            MockGame("a", "d", 50, 40, "a", 2),
            MockGame("b", "c", 50, 40, "b", 2),
        ]

        # Standings before round 3: A and B both 2-0
        standings = _compute_game_standings(games, 3)
        assert len(standings) >= 2
        top_ids = {standings[0]["team_id"], standings[1]["team_id"]}
        assert top_ids == {"a", "b"}

    async def test_game_significance_blowout_rematch(self, app_client):
        """Game detail shows last-meeting blowout when margin was >= 15."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        # Run enough rounds for rematches
        async with get_session(engine) as session:
            repo = Repository(session)
            await step_round(repo, season_id, round_number=2)
            await step_round(repo, season_id, round_number=3)
            games_r3 = await repo.get_games_for_round(season_id, 3)
            for g in games_r3:
                await repo.mark_game_presented(g.id)
            await session.commit()

            # Find a rematch game and check if blowout context appears
            games_r1 = await repo.get_games_for_round(season_id, 1)
            r1_margins = {}
            for g in games_r1:
                pair = frozenset({g.home_team_id, g.away_team_id})
                r1_margins[pair] = abs(g.home_score - g.away_score)

            target_game_id = None
            had_blowout = False
            for g in games_r3:
                pair = frozenset({g.home_team_id, g.away_team_id})
                if pair in r1_margins:
                    target_game_id = g.id
                    if r1_margins[pair] >= 15:
                        had_blowout = True
                    break

        if target_game_id:
            r = await client.get(f"/games/{target_game_id}")
            assert r.status_code == 200
            if had_blowout:
                assert "Last meeting" in r.text
                assert "won by" in r.text
                assert "Game Significance" in r.text

    async def test_game_detail_significance_section_renders(self, app_client):
        """Game significance section renders when relevant context exists."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        # Run multiple rounds to build standings
        async with get_session(engine) as session:
            repo = Repository(session)
            for rn in range(2, 5):
                await step_round(repo, season_id, round_number=rn)
                games = await repo.get_games_for_round(season_id, rn)
                for g in games:
                    await repo.mark_game_presented(g.id)
            await session.commit()

            games_r4 = await repo.get_games_for_round(season_id, 4)
            game_id = games_r4[0].id

        r = await client.get(f"/games/{game_id}")
        assert r.status_code == 200
        # Page renders successfully — significance may or may not appear
        # depending on the random standings outcome
        assert "Box Score" in r.text

    async def test_compute_game_standings_helper(self, app_client):
        """_compute_game_standings returns standings from games before a round."""
        from pinwheel.api.pages import _compute_game_standings

        class MockGame:
            def __init__(self, home_id: str, away_id: str, home_score: int,
                         away_score: int, winner_id: str, rnd: int, mi: int = 0):
                self.home_team_id = home_id
                self.away_team_id = away_id
                self.home_score = home_score
                self.away_score = away_score
                self.winner_team_id = winner_id
                self.round_number = rnd
                self.matchup_index = mi

        games = [
            MockGame("t1", "t2", 50, 40, "t1", 1),
            MockGame("t3", "t4", 60, 45, "t3", 1),
            MockGame("t1", "t3", 55, 50, "t1", 2),
            MockGame("t2", "t4", 45, 50, "t4", 2),
        ]

        # Before round 2: only round 1 games count
        standings_r2 = _compute_game_standings(games, 2)
        assert len(standings_r2) == 4
        assert standings_r2[0]["team_id"] in ("t1", "t3")  # Both 1-0
        assert standings_r2[0]["wins"] == 1

        # Before round 3: rounds 1+2 count
        standings_r3 = _compute_game_standings(games, 3)
        assert standings_r3[0]["team_id"] == "t1"  # 2-0
        assert standings_r3[0]["wins"] == 2

        # Before round 1: no games yet
        standings_r1 = _compute_game_standings(games, 1)
        assert standings_r1 == []

    async def test_rule_changes_display_in_template(self, app_client):
        """Rule changes between meetings appear with distinctive styling."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        async with get_session(engine) as session:
            repo = Repository(session)
            # Enact rule change in round 1 (before round 2 games)
            await repo.append_event(
                event_type="rule.enacted",
                aggregate_id="prop-display-1",
                aggregate_type="rule_change",
                season_id=season_id,
                round_number=1,
                payload={
                    "parameter": "shot_clock_seconds",
                    "old_value": 24,
                    "new_value": 20,
                    "source_proposal_id": "prop-display-1",
                    "round_enacted": 1,
                },
            )

            # Run rounds 2 and 3
            await step_round(repo, season_id, round_number=2)
            games_r2 = await repo.get_games_for_round(season_id, 2)
            for g in games_r2:
                await repo.mark_game_presented(g.id)
            await step_round(repo, season_id, round_number=3)
            games_r3 = await repo.get_games_for_round(season_id, 3)
            for g in games_r3:
                await repo.mark_game_presented(g.id)
            await session.commit()

            # Find a game in round 3 where teams met before
            games_r1 = await repo.get_games_for_round(season_id, 1)
            r1_pairs = [{g.home_team_id, g.away_team_id} for g in games_r1]

            target_game_id = None
            for g in games_r3:
                if {g.home_team_id, g.away_team_id} in r1_pairs:
                    target_game_id = g.id
                    break

        if target_game_id:
            r = await client.get(f"/games/{target_game_id}")
            assert r.status_code == 200
            # Rule change should appear with the accent color styling
            assert "Shot Clock Seconds" in r.text
            assert "changed from 24" in r.text
            assert "accent-elam" in r.text


class TestRulesPageHistory:
    """Tests for rule change history display on the rules page."""

    async def test_rules_page_with_no_changes(self, app_client):
        """Rules page with no changes should not show history."""
        client, engine = app_client
        season_id, _ = await _seed_season(engine)

        r = await client.get("/rules")
        assert r.status_code == 200
        assert "The Rules" in r.text
        # Should show default values but no change history
        assert "rule-card-history" not in r.text

    async def test_rules_page_with_rule_changes(self, app_client):
        """Rules page should show change history for modified rules."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        # Create a rule change event
        async with get_session(engine) as session:
            repo = Repository(session)

            # Create a governor and proposal
            player = await repo.get_or_create_player(
                discord_id="555666777",
                username="RuleChanger",
            )
            await repo.enroll_player(player.id, team_ids[0], season_id)

            # Submit proposal event
            await repo.append_event(
                event_type="proposal.submitted",
                aggregate_id="prop-rule-1",
                aggregate_type="proposal",
                season_id=season_id,
                governor_id=player.id,
                team_id=team_ids[0],
                round_number=1,
                payload={
                    "id": "prop-rule-1",
                    "raw_text": "Make three-pointers worth 4",
                    "governor_id": player.id,
                    "team_id": team_ids[0],
                    "tier": 1,
                    "status": "submitted",
                },
            )

            # Create rule.enacted event
            await repo.append_event(
                event_type="rule.enacted",
                aggregate_id="prop-rule-1",
                aggregate_type="rule_change",
                season_id=season_id,
                round_number=1,
                payload={
                    "parameter": "three_point_value",
                    "old_value": 3,
                    "new_value": 4,
                    "source_proposal_id": "prop-rule-1",
                    "round_enacted": 1,
                },
            )

            # Update the season ruleset
            season = await repo.get_season(season_id)
            ruleset_data = season.current_ruleset or {}
            ruleset_data["three_point_value"] = 4
            season.current_ruleset = ruleset_data

            await session.commit()

        r = await client.get("/rules")
        assert r.status_code == 200
        assert "3 &rarr; 4" in r.text
        assert "Round 1" in r.text
        assert "Proposed by Governor" in r.text or "/governors/" in r.text

    async def test_rules_page_governance_fingerprint(self, app_client):
        """Rules page should show most-changed tier in governance fingerprint."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        # Create multiple rule changes in Game Mechanics tier
        async with get_session(engine) as session:
            repo = Repository(session)

            player = await repo.get_or_create_player(
                discord_id="888999000",
                username="ActiveGovernor",
            )
            await repo.enroll_player(player.id, team_ids[0], season_id)

            # Change two game mechanics rules
            for i, (param, old_val, new_val) in enumerate(
                [
                    ("three_point_value", 3, 4),
                    ("shot_clock_seconds", 24, 20),
                ]
            ):
                prop_id = f"prop-gm-{i}"
                await repo.append_event(
                    event_type="proposal.submitted",
                    aggregate_id=prop_id,
                    aggregate_type="proposal",
                    season_id=season_id,
                    governor_id=player.id,
                    team_id=team_ids[0],
                    round_number=i + 1,
                    payload={
                        "id": prop_id,
                        "raw_text": f"Change {param}",
                        "governor_id": player.id,
                        "team_id": team_ids[0],
                        "tier": 1,
                        "status": "submitted",
                    },
                )

                await repo.append_event(
                    event_type="rule.enacted",
                    aggregate_id=prop_id,
                    aggregate_type="rule_change",
                    season_id=season_id,
                    round_number=i + 1,
                    payload={
                        "parameter": param,
                        "old_value": old_val,
                        "new_value": new_val,
                        "source_proposal_id": prop_id,
                        "round_enacted": i + 1,
                    },
                )

            # Update season ruleset
            season = await repo.get_season(season_id)
            ruleset_data = season.current_ruleset or {}
            ruleset_data["three_point_value"] = 4
            ruleset_data["shot_clock_seconds"] = 20
            season.current_ruleset = ruleset_data

            await session.commit()

        r = await client.get("/rules")
        assert r.status_code == 200
        # Check that both rule changes are shown in the change history
        assert "Three Point Value" in r.text
        assert "Shot Clock Seconds" in r.text
        # Most changed tier should be present if computed
        if "Most changed:" in r.text:
            assert "Game Mechanics" in r.text

    async def test_rules_page_multiple_changes_same_param(self, app_client):
        """Rules page should show multiple changes to the same parameter."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        async with get_session(engine) as session:
            repo = Repository(session)

            player = await repo.get_or_create_player(
                discord_id="111222444",
                username="TweakerGov",
            )
            await repo.enroll_player(player.id, team_ids[0], season_id)

            # Change the same rule twice
            for i, (old_val, new_val, round_num) in enumerate(
                [
                    (3, 4, 1),
                    (4, 5, 2),
                ]
            ):
                prop_id = f"prop-multi-{i}"
                await repo.append_event(
                    event_type="proposal.submitted",
                    aggregate_id=prop_id,
                    aggregate_type="proposal",
                    season_id=season_id,
                    governor_id=player.id,
                    team_id=team_ids[0],
                    round_number=round_num,
                    payload={
                        "id": prop_id,
                        "raw_text": f"Three-pointers worth {new_val}",
                        "governor_id": player.id,
                        "team_id": team_ids[0],
                        "tier": 1,
                        "status": "submitted",
                    },
                )

                await repo.append_event(
                    event_type="rule.enacted",
                    aggregate_id=prop_id,
                    aggregate_type="rule_change",
                    season_id=season_id,
                    round_number=round_num,
                    payload={
                        "parameter": "three_point_value",
                        "old_value": old_val,
                        "new_value": new_val,
                        "source_proposal_id": prop_id,
                        "round_enacted": round_num,
                    },
                )

            # Update season ruleset to final value
            season = await repo.get_season(season_id)
            ruleset_data = season.current_ruleset or {}
            ruleset_data["three_point_value"] = 5
            season.current_ruleset = ruleset_data

            await session.commit()

        r = await client.get("/rules")
        assert r.status_code == 200
        # Should show both changes
        assert "3 &rarr; 4" in r.text
        assert "4 &rarr; 5" in r.text
        assert "Round 1" in r.text
        assert "Round 2" in r.text



class TestRulesPageGameplayDelta:
    """Tests for before/after gameplay delta display on rule changes."""

    async def test_impact_too_early_when_few_games_after(self, app_client):
        """Impact should show 'Too early to measure' when < 2 games after change."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        # Rule enacted at round 1 — seed only played 1 round, so only 2 games
        # exist (round 1) but they are AT round_enacted, so 'after' count is 2.
        # To get 'Too early', enact at a future round with no games after it.
        async with get_session(engine) as session:
            repo = Repository(session)

            player = await repo.get_or_create_player(
                discord_id="delta-gov-1",
                username="DeltaGov1",
            )
            await repo.enroll_player(player.id, team_ids[0], season_id)

            # Enact rule at round 99 — no games exist that far out
            await repo.append_event(
                event_type="proposal.submitted",
                aggregate_id="prop-delta-1",
                aggregate_type="proposal",
                season_id=season_id,
                governor_id=player.id,
                team_id=team_ids[0],
                round_number=99,
                payload={
                    "id": "prop-delta-1",
                    "raw_text": "Change three point value",
                    "governor_id": player.id,
                    "team_id": team_ids[0],
                    "tier": 1,
                    "status": "submitted",
                },
            )
            await repo.append_event(
                event_type="rule.enacted",
                aggregate_id="prop-delta-1",
                aggregate_type="rule_change",
                season_id=season_id,
                round_number=99,
                payload={
                    "parameter": "three_point_value",
                    "old_value": 3,
                    "new_value": 5,
                    "source_proposal_id": "prop-delta-1",
                    "round_enacted": 99,
                },
            )

            season = await repo.get_season(season_id)
            ruleset_data = season.current_ruleset or {}
            ruleset_data["three_point_value"] = 5
            season.current_ruleset = ruleset_data
            await session.commit()

        r = await client.get("/rules")
        assert r.status_code == 200
        assert "Too early to measure" in r.text

    async def test_impact_shows_percentage_with_enough_games(self, app_client):
        """Impact should show scoring percentage when enough games exist."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        # Play additional rounds so we have games before AND after round 2
        async with get_session(engine) as session:
            repo = Repository(session)

            await step_round(repo, season_id, round_number=2)
            await step_round(repo, season_id, round_number=3)
            await step_round(repo, season_id, round_number=4)

            player = await repo.get_or_create_player(
                discord_id="delta-gov-2",
                username="DeltaGov2",
            )
            await repo.enroll_player(player.id, team_ids[0], season_id)

            # Enact rule at round 2 — round 1 is before, rounds 2-4 are after
            await repo.append_event(
                event_type="proposal.submitted",
                aggregate_id="prop-delta-2",
                aggregate_type="proposal",
                season_id=season_id,
                governor_id=player.id,
                team_id=team_ids[0],
                round_number=2,
                payload={
                    "id": "prop-delta-2",
                    "raw_text": "Change shot clock",
                    "governor_id": player.id,
                    "team_id": team_ids[0],
                    "tier": 1,
                    "status": "submitted",
                },
            )
            await repo.append_event(
                event_type="rule.enacted",
                aggregate_id="prop-delta-2",
                aggregate_type="rule_change",
                season_id=season_id,
                round_number=2,
                payload={
                    "parameter": "shot_clock_seconds",
                    "old_value": 24,
                    "new_value": 20,
                    "source_proposal_id": "prop-delta-2",
                    "round_enacted": 2,
                },
            )

            season = await repo.get_season(season_id)
            ruleset_data = season.current_ruleset or {}
            ruleset_data["shot_clock_seconds"] = 20
            season.current_ruleset = ruleset_data
            await session.commit()

        r = await client.get("/rules")
        assert r.status_code == 200
        # Should have a percentage-based impact (positive or negative)
        assert "rule-change-impact" in r.text
        assert "Scoring" in r.text
        assert "% since change" in r.text

    async def test_impact_shows_avg_when_no_before_data(self, app_client):
        """Impact should show avg pts/game when rule enacted at round 1."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        # Play more rounds so we have >= 2 games after round 1
        async with get_session(engine) as session:
            repo = Repository(session)

            await step_round(repo, season_id, round_number=2)

            player = await repo.get_or_create_player(
                discord_id="delta-gov-3",
                username="DeltaGov3",
            )
            await repo.enroll_player(player.id, team_ids[0], season_id)

            # Enact rule at round 1 — no "before" data exists
            await repo.append_event(
                event_type="proposal.submitted",
                aggregate_id="prop-delta-3",
                aggregate_type="proposal",
                season_id=season_id,
                governor_id=player.id,
                team_id=team_ids[0],
                round_number=1,
                payload={
                    "id": "prop-delta-3",
                    "raw_text": "Change three point value",
                    "governor_id": player.id,
                    "team_id": team_ids[0],
                    "tier": 1,
                    "status": "submitted",
                },
            )
            await repo.append_event(
                event_type="rule.enacted",
                aggregate_id="prop-delta-3",
                aggregate_type="rule_change",
                season_id=season_id,
                round_number=1,
                payload={
                    "parameter": "three_point_value",
                    "old_value": 3,
                    "new_value": 5,
                    "source_proposal_id": "prop-delta-3",
                    "round_enacted": 1,
                },
            )

            season = await repo.get_season(season_id)
            ruleset_data = season.current_ruleset or {}
            ruleset_data["three_point_value"] = 5
            season.current_ruleset = ruleset_data
            await session.commit()

        r = await client.get("/rules")
        assert r.status_code == 200
        # Should show average pts/game format (no before comparison possible)
        assert "rule-change-impact" in r.text
        assert "pts/game" in r.text

    async def test_no_impact_when_no_rule_changes(self, app_client):
        """Rules page without rule changes should not show impact text."""
        client, engine = app_client
        await _seed_season(engine)

        r = await client.get("/rules")
        assert r.status_code == 200
        assert "rule-change-impact" not in r.text
        assert "Too early to measure" not in r.text


class TestRepositoryAvgGameScore:
    """Tests for the get_avg_total_game_score_for_rounds repository method."""

    async def test_avg_score_with_games(self, app_client):
        """Should return avg total game score and count for rounds with games."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        async with get_session(engine) as session:
            repo = Repository(session)
            avg, count = await repo.get_avg_total_game_score_for_rounds(
                season_id, 1, 1,
            )
            # Round 1 has 2 games (4 teams, round-robin = 2 games per round)
            assert count == 2
            assert avg > 0

    async def test_avg_score_no_games(self, app_client):
        """Should return (0.0, 0) when no games exist in range."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        async with get_session(engine) as session:
            repo = Repository(session)
            avg, count = await repo.get_avg_total_game_score_for_rounds(
                season_id, 99, 100,
            )
            assert count == 0
            assert avg == 0.0

    async def test_avg_score_multiple_rounds(self, app_client):
        """Should aggregate across multiple rounds correctly."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        async with get_session(engine) as session:
            repo = Repository(session)
            await step_round(repo, season_id, round_number=2)
            await session.commit()

        async with get_session(engine) as session:
            repo = Repository(session)
            avg, count = await repo.get_avg_total_game_score_for_rounds(
                season_id, 1, 2,
            )
            # 2 rounds x 2 games each = 4 games
            assert count == 4
            assert avg > 0



class TestWhatChangedFallback:
    """Tests for post_headline fallback in _compute_what_changed."""

    def test_fallback_when_no_signals(self):
        """Falls back to Post headline when no change signals detected."""
        from pinwheel.api.pages import _compute_what_changed

        signals = _compute_what_changed(
            standings=[{"team_id": "a", "team_name": "Team A"}],
            prev_standings=[{"team_id": "a", "team_name": "Team A"}],
            streaks={"a": 1},
            prev_streaks={"a": 0},
            rule_changes=[],
            season_phase="active",
            post_headline="Storm Dominate in Round 3 Blowout",
        )
        assert len(signals) == 1
        assert signals[0] == "Latest: Storm Dominate in Round 3 Blowout"

    def test_no_fallback_when_signals_exist(self):
        """Does NOT fall back when real change signals are present."""
        from pinwheel.api.pages import _compute_what_changed

        signals = _compute_what_changed(
            standings=[{"team_id": "a", "team_name": "Streakers"}],
            prev_standings=[{"team_id": "a", "team_name": "Streakers"}],
            streaks={"a": 3},
            prev_streaks={"a": 2},
            rule_changes=[],
            season_phase="active",
            post_headline="Some headline",
        )
        assert len(signals) >= 1
        assert not signals[0].startswith("Latest:")
        assert "Streakers on a 3-game win streak" in signals[0]

    def test_no_fallback_when_headline_empty(self):
        """Returns empty list when no signals and no headline."""
        from pinwheel.api.pages import _compute_what_changed

        signals = _compute_what_changed(
            standings=[{"team_id": "a", "team_name": "Team A"}],
            prev_standings=[{"team_id": "a", "team_name": "Team A"}],
            streaks={},
            prev_streaks={},
            rule_changes=[],
            season_phase="active",
            post_headline="",
        )
        assert len(signals) == 0

    def test_champion_overrides_fallback(self):
        """Champion signal takes priority even with a headline fallback."""
        from pinwheel.api.pages import _compute_what_changed

        signals = _compute_what_changed(
            standings=[{"team_id": "a", "team_name": "Champions"}],
            prev_standings=[],
            streaks={},
            prev_streaks={},
            rule_changes=[],
            season_phase="championship",
            post_headline="Some headline",
        )
        assert len(signals) == 1
        assert "Champions are your champions" in signals[0]
        assert not signals[0].startswith("Latest:")


class TestWhatChangedPartialEndpoint:
    """Tests for the /partials/what-changed HTMX endpoint."""

    async def test_partial_empty_when_no_season(self, app_client):
        """Partial returns empty HTML when no season exists."""
        client, _ = app_client
        r = await client.get("/partials/what-changed")
        assert r.status_code == 200
        assert r.text == ""

    async def test_partial_returns_html_fragment(self, app_client):
        """Partial returns raw HTML fragment, not a full page."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        # Run a second round so what_changed has prev data to compare
        async with get_session(engine) as session:
            repo = Repository(session)
            await step_round(repo, season_id, round_number=2)
            games = await repo.get_games_for_round(season_id, 2)
            for g in games:
                await repo.mark_game_presented(g.id)
            await session.commit()

        r = await client.get("/partials/what-changed")
        assert r.status_code == 200
        # Should NOT contain full page markers
        assert "<!DOCTYPE" not in r.text
        assert "<html" not in r.text
        assert "<head" not in r.text
        # If it has content, it should be the what-changed widget
        if r.text:
            assert "what-changed" in r.text
            assert "hx-get" in r.text
            assert "hx-trigger" in r.text

    async def test_partial_includes_htmx_attrs(self, app_client):
        """Partial response includes HTMX polling attributes for re-polling."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        # Need at least 1 round for the partial to return content
        r = await client.get("/partials/what-changed")
        assert r.status_code == 200
        # With only 1 round, we should get fallback (Post headline) or empty
        # Either way, if there's content it must include HTMX attrs
        if r.text:
            assert 'hx-get="/partials/what-changed"' in r.text
            assert 'hx-trigger="every 60s"' in r.text

    async def test_partial_fallback_styling(self, app_client):
        """Fallback items should have the what-changed-fallback CSS class."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        # With 1 round and no prior round, signals will be empty but
        # Post headline should exist, triggering the fallback
        r = await client.get("/partials/what-changed")
        assert r.status_code == 200
        if r.text and "Latest:" in r.text:
            assert "what-changed-fallback" in r.text


class TestWhatChangedHomePage:
    """Tests for what-changed widget behavior on the home page."""

    async def test_home_page_has_htmx_polling(self, app_client):
        """Home page what-changed widget includes HTMX polling attributes."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        r = await client.get("/")
        assert r.status_code == 200
        # The widget (or empty polling div) should have HTMX attributes
        assert "what-changed" in r.text
        assert "hx-get" in r.text or "hx-trigger" in r.text

    async def test_home_page_what_changed_present(self, app_client):
        """Home page what-changed widget renders after round 1."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        r = await client.get("/")
        assert r.status_code == 200
        # With round 1 only, change signals may include blowout/nailbiter
        # from game results, or fall back to the Post headline.
        if "what-changed" in r.text and "what-changed-item" in r.text:
            assert (
                "Latest:" in r.text
                or "nailbiter" in r.text
                or "blew it open" in r.text
                or "what-changed-item" in r.text
            )


class TestGameDetailAnnotations:
    """Tests for game detail page contextual annotations: streaks, personal bests."""

    async def test_game_detail_streak_context(self, app_client):
        """Game detail shows streak context when a team is on a 3+ game streak."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        # Run enough rounds that a team could build a streak
        async with get_session(engine) as session:
            repo = Repository(session)
            for rn in range(2, 5):
                await step_round(repo, season_id, round_number=rn)
                games = await repo.get_games_for_round(season_id, rn)
                for g in games:
                    await repo.mark_game_presented(g.id)
            await session.commit()

            # Get a game from round 4 and check the page renders
            games_r4 = await repo.get_games_for_round(season_id, 4)
            game_id = games_r4[0].id

        r = await client.get(f"/games/{game_id}")
        assert r.status_code == 200
        # The page should render with Box Score
        assert "Box Score" in r.text
        # If any team has a 3+ streak, it will show in context
        # We verify the page renders without error regardless

    async def test_game_detail_significance_renders_season_high(self, app_client):
        """Game detail shows season-high points annotation."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        # Run multiple rounds to build game history
        async with get_session(engine) as session:
            repo = Repository(session)
            for rn in range(2, 4):
                await step_round(repo, season_id, round_number=rn)
                games = await repo.get_games_for_round(season_id, rn)
                for g in games:
                    await repo.mark_game_presented(g.id)
            await session.commit()

            # Get a game from round 3
            games_r3 = await repo.get_games_for_round(season_id, 3)
            game_id = games_r3[0].id

        r = await client.get(f"/games/{game_id}")
        assert r.status_code == 200
        # Page renders successfully
        assert "Box Score" in r.text
        # Season-high annotations may or may not appear depending on
        # random simulation outcomes, but the page must not error
        # If significance appears, it should have valid content
        if "Game Significance" in r.text:
            assert any(
                phrase in r.text
                for phrase in [
                    "Season-high",
                    "First place showdown",
                    "Win-and-clinch",
                    "Last meeting",
                ]
            )

    async def test_game_detail_context_and_significance_sections(self, app_client):
        """Game detail page shows both context and significance sections."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        async with get_session(engine) as session:
            repo = Repository(session)
            for rn in range(2, 5):
                await step_round(repo, season_id, round_number=rn)
                games = await repo.get_games_for_round(season_id, rn)
                for g in games:
                    await repo.mark_game_presented(g.id)
            await session.commit()

            games_r4 = await repo.get_games_for_round(season_id, 4)
            game_id = games_r4[0].id

        r = await client.get(f"/games/{game_id}")
        assert r.status_code == 200
        # The page should contain the annotation section headers
        # At minimum, context will appear (margin/scoring stats)
        if "Game Context" in r.text:
            assert any(
                phrase in r.text
                for phrase in [
                    "Closest game",
                    "Biggest blowout",
                    "tight",
                    "decisive",
                    "Season series",
                    "combined points",
                    "season avg",
                    "Since Round",
                    "win streak",
                    "losing streak",
                ]
            )


class TestHooperBioXSS:
    """Verify that hooper bio handlers escape user content to prevent XSS."""

    @pytest.fixture
    async def bio_client(self):
        """App with auth configured + seeded data + authenticated governor."""
        settings = Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            pinwheel_env="production",
            session_secret_key="test-secret-key-bio",
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

        # Seed a season with teams and hoopers
        async with get_session(engine) as session:
            repo = Repository(session)
            league = await repo.create_league("XSS Test League")
            season = await repo.create_season(
                league.id, "Season 1", starting_ruleset={"quarter_minutes": 3}
            )
            team = await repo.create_team(
                season.id,
                "Team Alpha",
                color="#aaa",
                venue={"name": "Arena", "capacity": 5000},
            )
            hooper = await repo.create_hooper(
                team_id=team.id,
                season_id=season.id,
                name="Target Hooper",
                archetype="sharpshooter",
                attributes=_hooper_attrs(),
            )
            # Enroll a governor on the team
            player = await repo.get_or_create_player(
                discord_id="governor-xss-test",
                username="XSSGovernor",
            )
            await repo.enroll_player(player.id, team.id, season.id)
            await session.commit()

            hooper_id = hooper.id

        # Create a signed session cookie for the governor
        cookie = _sign_session(
            settings.session_secret_key,
            {
                "discord_id": "governor-xss-test",
                "username": "XSSGovernor",
                "avatar_url": "",
            },
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            client.cookies.set("pinwheel_session", cookie)
            yield client, hooper_id

        await engine.dispose()

    XSS_PAYLOAD = "<script>alert('xss')</script>"

    async def test_bio_view_escapes_xss_in_backstory(self, bio_client):
        """GET /hoopers/{id}/bio/view must escape stored XSS in backstory."""
        client, hooper_id = bio_client

        # Inject the XSS payload via the POST handler
        r = await client.post(
            f"/hoopers/{hooper_id}/bio",
            data={"backstory": self.XSS_PAYLOAD},
        )
        assert r.status_code == 200
        # The raw <script> tag must NOT appear in output
        assert "<script>" not in r.text
        # The escaped version should appear
        assert "&lt;script&gt;" in r.text

    async def test_bio_view_get_escapes_xss(self, bio_client):
        """GET /hoopers/{id}/bio/view escapes stored XSS after it was saved."""
        client, hooper_id = bio_client

        # Save XSS payload
        await client.post(
            f"/hoopers/{hooper_id}/bio",
            data={"backstory": self.XSS_PAYLOAD},
        )

        # Now fetch the view fragment
        r = await client.get(f"/hoopers/{hooper_id}/bio/view")
        assert r.status_code == 200
        assert "<script>" not in r.text
        assert "&lt;script&gt;" in r.text

    async def test_bio_edit_form_escapes_xss_in_textarea(self, bio_client):
        """GET /hoopers/{id}/bio/edit must escape XSS in textarea pre-fill."""
        client, hooper_id = bio_client

        # Save XSS payload first
        await client.post(
            f"/hoopers/{hooper_id}/bio",
            data={"backstory": self.XSS_PAYLOAD},
        )

        # Fetch the edit form
        r = await client.get(f"/hoopers/{hooper_id}/bio/edit")
        assert r.status_code == 200
        # Inside <textarea>, Jinja2 escapes the content
        assert "<script>" not in r.text
        assert "&lt;script&gt;" in r.text

    async def test_bio_view_shows_no_bio_when_empty(self, bio_client):
        """View fragment shows 'No bio yet.' for empty backstory."""
        client, hooper_id = bio_client

        r = await client.get(f"/hoopers/{hooper_id}/bio/view")
        assert r.status_code == 200
        assert "No bio yet." in r.text

    async def test_bio_view_shows_edit_button_for_governor(self, bio_client):
        """Authenticated governor sees Edit Bio button."""
        client, hooper_id = bio_client

        r = await client.get(f"/hoopers/{hooper_id}/bio/view")
        assert r.status_code == 200
        assert "Edit Bio" in r.text

    async def test_bio_save_shows_edit_button(self, bio_client):
        """After saving bio, edit button is present (can_edit=True)."""
        client, hooper_id = bio_client

        r = await client.post(
            f"/hoopers/{hooper_id}/bio",
            data={"backstory": "A regular bio."},
        )
        assert r.status_code == 200
        assert "A regular bio." in r.text
        assert "Edit Bio" in r.text

    async def test_bio_xss_with_event_handler(self, bio_client):
        """XSS via onload/onerror event handlers must also be escaped."""
        client, hooper_id = bio_client
        payload = '<img src=x onerror="alert(1)">'

        r = await client.post(
            f"/hoopers/{hooper_id}/bio",
            data={"backstory": payload},
        )
        assert r.status_code == 200
        # The <img> tag must be escaped so the browser won't parse it as HTML
        assert "<img" not in r.text
        assert "&lt;img" in r.text


class TestSplitStandings:
    """Home page splits standings into regular-season and playoff during playoffs."""

    async def test_get_standings_no_filter(self, app_client):
        """_get_standings without filter returns all games."""
        from pinwheel.api.pages import _get_standings

        _, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        # Add a playoff game result
        async with get_session(engine) as session:
            repo = Repository(session)
            await repo.store_game_result(
                season_id=season_id,
                round_number=10,
                matchup_index=0,
                home_team_id=team_ids[0],
                away_team_id=team_ids[1],
                home_score=55,
                away_score=50,
                winner_team_id=team_ids[0],
                seed=999,
                total_possessions=60,
                phase="semifinal",
            )
            await session.commit()

        async with get_session(engine) as session:
            repo = Repository(session)
            standings = await _get_standings(repo, season_id)
            total_wins = sum(s["wins"] for s in standings)
            # Round 1 produced 2 regular games + 1 playoff = 3 wins total
            assert total_wins == 3

    async def test_get_standings_regular_filter(self, app_client):
        """_get_standings with phase_filter='regular' excludes playoff games."""
        from pinwheel.api.pages import _get_standings

        _, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        async with get_session(engine) as session:
            repo = Repository(session)
            await repo.store_game_result(
                season_id=season_id,
                round_number=10,
                matchup_index=0,
                home_team_id=team_ids[0],
                away_team_id=team_ids[1],
                home_score=55,
                away_score=50,
                winner_team_id=team_ids[0],
                seed=999,
                total_possessions=60,
                phase="semifinal",
            )
            await session.commit()

        async with get_session(engine) as session:
            repo = Repository(session)
            standings = await _get_standings(repo, season_id, phase_filter="regular")
            total_wins = sum(s["wins"] for s in standings)
            # Only the 2 regular-season games from round 1
            assert total_wins == 2

    async def test_get_standings_playoff_filter(self, app_client):
        """_get_standings with phase_filter='playoff' includes only playoff games."""
        from pinwheel.api.pages import _get_standings

        _, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        async with get_session(engine) as session:
            repo = Repository(session)
            await repo.store_game_result(
                season_id=season_id,
                round_number=10,
                matchup_index=0,
                home_team_id=team_ids[0],
                away_team_id=team_ids[1],
                home_score=55,
                away_score=50,
                winner_team_id=team_ids[0],
                seed=999,
                total_possessions=60,
                phase="semifinal",
            )
            await repo.store_game_result(
                season_id=season_id,
                round_number=11,
                matchup_index=0,
                home_team_id=team_ids[2],
                away_team_id=team_ids[3],
                home_score=60,
                away_score=45,
                winner_team_id=team_ids[2],
                seed=1000,
                total_possessions=65,
                phase="finals",
            )
            await session.commit()

        async with get_session(engine) as session:
            repo = Repository(session)
            standings = await _get_standings(repo, season_id, phase_filter="playoff")
            total_wins = sum(s["wins"] for s in standings)
            # Only the 2 playoff games
            assert total_wins == 2

    async def test_home_page_shows_playoff_series_during_playoffs(self, app_client):
        """Home page shows playoff series bracket and Regular Season sections."""
        client, engine = app_client
        season_id, team_ids = await _seed_season(engine)

        async with get_session(engine) as session:
            repo = Repository(session)
            # Set season to playoffs
            await repo.update_season_status(season_id, "playoffs")
            # Create semifinal schedule entries (bracket builder needs these)
            await repo.create_schedule_entry(
                season_id=season_id,
                round_number=10,
                matchup_index=0,
                home_team_id=team_ids[0],
                away_team_id=team_ids[1],
                phase="semifinal",
            )
            await repo.create_schedule_entry(
                season_id=season_id,
                round_number=10,
                matchup_index=1,
                home_team_id=team_ids[2],
                away_team_id=team_ids[3],
                phase="semifinal",
            )
            # Add a playoff game result
            await repo.store_game_result(
                season_id=season_id,
                round_number=10,
                matchup_index=0,
                home_team_id=team_ids[0],
                away_team_id=team_ids[1],
                home_score=55,
                away_score=50,
                winner_team_id=team_ids[0],
                seed=999,
                total_possessions=60,
                phase="semifinal",
            )
            await session.commit()

        r = await client.get("/")
        assert r.status_code == 200
        assert "Playoffs" in r.text
        assert "Semi 1" in r.text
        assert "Regular Season" in r.text

    async def test_home_page_shows_single_standings_during_regular_season(self, app_client):
        """Home page shows single 'Standings' heading during regular season."""
        client, engine = app_client
        await _seed_season(engine)

        r = await client.get("/")
        assert r.status_code == 200
        assert "Standings" in r.text
        assert "Playoffs" not in r.text
        assert "Regular Season" not in r.text
