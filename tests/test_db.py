"""Tests for database layer: engine, ORM models, repository round-trips."""

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import Base
from pinwheel.db.repository import Repository


@pytest.fixture
async def engine() -> AsyncEngine:
    """Create an in-memory SQLite engine with all tables."""
    eng = create_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def repo(engine: AsyncEngine) -> Repository:
    """Yield a repository with a session bound to the in-memory database."""
    async with get_session(engine) as session:
        yield Repository(session)


class TestTableCreation:
    async def test_all_tables_created(self, engine: AsyncEngine):
        async with engine.connect() as conn:
            tables = await conn.run_sync(
                lambda sync_conn: sync_conn.dialect.get_table_names(sync_conn)
            )
        expected = {
            "leagues",
            "seasons",
            "teams",
            "hoopers",
            "game_results",
            "box_scores",
            "governance_events",
            "schedule",
            "reports",
            "season_archives",
        }
        assert expected.issubset(set(tables))


class TestLeagueSeason:
    async def test_create_league(self, repo: Repository):
        league = await repo.create_league("Test League")
        assert league.id is not None
        assert league.name == "Test League"

    async def test_create_season(self, repo: Repository):
        league = await repo.create_league("Test League")
        season = await repo.create_season(
            league.id, "Season 1", starting_ruleset={"three_point_value": 3}
        )
        assert season.id is not None
        assert season.league_id == league.id
        assert season.current_ruleset == {"three_point_value": 3}


class TestTeamHooperRoundTrip:
    async def test_create_and_retrieve_team(self, repo: Repository):
        league = await repo.create_league("L")
        season = await repo.create_season(league.id, "S1")
        team = await repo.create_team(
            season.id,
            "Rose City Thorns",
            color="#CC0000",
            motto="Bloom",
            venue={"name": "Garden", "capacity": 5000},
        )
        retrieved = await repo.get_team(team.id)
        assert retrieved is not None
        assert retrieved.name == "Rose City Thorns"
        assert retrieved.color == "#CC0000"
        assert retrieved.venue["name"] == "Garden"

    async def test_create_and_retrieve_hooper(self, repo: Repository):
        league = await repo.create_league("L")
        season = await repo.create_season(league.id, "S1")
        team = await repo.create_team(season.id, "Team A")
        hooper = await repo.create_hooper(
            team_id=team.id,
            season_id=season.id,
            name="Sharpshooter-1",
            archetype="sharpshooter",
            attributes={"scoring": 80, "passing": 40, "defense": 25},
            moves=[{"name": "Heat Check", "trigger": "made_three"}],
        )
        retrieved = await repo.get_hooper(hooper.id)
        assert retrieved is not None
        assert retrieved.name == "Sharpshooter-1"
        assert retrieved.attributes["scoring"] == 80
        assert retrieved.moves[0]["name"] == "Heat Check"

    async def test_team_hoopers_relationship(self, repo: Repository):
        league = await repo.create_league("L")
        season = await repo.create_season(league.id, "S1")
        team = await repo.create_team(season.id, "Team A")
        for i in range(4):
            await repo.create_hooper(
                team.id, season.id, f"Hooper-{i}", "sharpshooter", {"scoring": 50}
            )
        teams = await repo.get_teams_for_season(season.id)
        assert len(teams) == 1
        assert len(teams[0].hoopers) == 4


class TestGameResultRoundTrip:
    async def test_store_and_retrieve_game(self, repo: Repository):
        league = await repo.create_league("L")
        season = await repo.create_season(league.id, "S1")
        home = await repo.create_team(season.id, "Home")
        away = await repo.create_team(season.id, "Away")

        game = await repo.store_game_result(
            season_id=season.id,
            round_number=1,
            matchup_index=0,
            home_team_id=home.id,
            away_team_id=away.id,
            home_score=42,
            away_score=38,
            winner_team_id=home.id,
            seed=42,
            total_possessions=80,
            quarter_scores=[{"q": 1, "home": 10, "away": 8}],
            elam_target=45,
        )
        retrieved = await repo.get_game_result(game.id)
        assert retrieved is not None
        assert retrieved.home_score == 42
        assert retrieved.away_score == 38
        assert retrieved.winner_team_id == home.id
        assert retrieved.elam_target == 45

    async def test_store_box_scores(self, repo: Repository):
        league = await repo.create_league("L")
        season = await repo.create_season(league.id, "S1")
        home = await repo.create_team(season.id, "Home")
        away = await repo.create_team(season.id, "Away")
        hooper = await repo.create_hooper(home.id, season.id, "A1", "sharpshooter", {"scoring": 50})

        game = await repo.store_game_result(
            season_id=season.id,
            round_number=1,
            matchup_index=0,
            home_team_id=home.id,
            away_team_id=away.id,
            home_score=42,
            away_score=38,
            winner_team_id=home.id,
            seed=42,
            total_possessions=80,
        )
        bs = await repo.store_box_score(game.id, hooper.id, home.id, points=15, assists=3, steals=2)
        assert bs.points == 15

        loaded_game = await repo.get_game_result(game.id)
        assert len(loaded_game.box_scores) == 1
        assert loaded_game.box_scores[0].points == 15

    async def test_get_games_for_round(self, repo: Repository):
        league = await repo.create_league("L")
        season = await repo.create_season(league.id, "S1")
        t1 = await repo.create_team(season.id, "T1")
        t2 = await repo.create_team(season.id, "T2")
        t3 = await repo.create_team(season.id, "T3")
        t4 = await repo.create_team(season.id, "T4")

        await repo.store_game_result(season.id, 1, 0, t1.id, t2.id, 40, 35, t1.id, 42, 75)
        await repo.store_game_result(season.id, 1, 1, t3.id, t4.id, 38, 42, t4.id, 43, 78)
        games = await repo.get_games_for_round(season.id, 1)
        assert len(games) == 2


class TestGovernanceEvents:
    async def test_append_and_retrieve_events(self, repo: Repository):
        league = await repo.create_league("L")
        season = await repo.create_season(league.id, "S1")

        await repo.append_event(
            event_type="proposal.submitted",
            aggregate_id="prop-1",
            aggregate_type="proposal",
            season_id=season.id,
            payload={"raw_text": "Make 3-pointers worth 4", "tier": 1},
            round_number=1,
            governor_id="gov-1",
        )
        await repo.append_event(
            event_type="vote.cast",
            aggregate_id="prop-1",
            aggregate_type="proposal",
            season_id=season.id,
            payload={"vote": "yes", "governor_id": "gov-2"},
            round_number=1,
        )
        events = await repo.get_events_for_aggregate("proposal", "prop-1")
        assert len(events) == 2
        assert events[0].event_type == "proposal.submitted"
        assert events[1].event_type == "vote.cast"
        assert events[0].sequence_number < events[1].sequence_number


class TestHooperBackstory:
    async def test_update_hooper_backstory(self, repo: Repository):
        league = await repo.create_league("L")
        season = await repo.create_season(league.id, "S1")
        team = await repo.create_team(season.id, "Team A")
        hooper = await repo.create_hooper(
            team.id,
            season.id,
            "Star Player",
            "sharpshooter",
            {"scoring": 80},
        )
        assert hooper.backstory == ""

        updated = await repo.update_hooper_backstory(hooper.id, "Born to ball.")
        assert updated is not None
        assert updated.backstory == "Born to ball."

        # Verify persistence via fresh query
        retrieved = await repo.get_hooper(hooper.id)
        assert retrieved is not None
        assert retrieved.backstory == "Born to ball."

    async def test_update_hooper_backstory_nonexistent(self, repo: Repository):
        result = await repo.update_hooper_backstory("nonexistent-id", "text")
        assert result is None

    async def test_update_hooper_backstory_empty(self, repo: Repository):
        league = await repo.create_league("L")
        season = await repo.create_season(league.id, "S1")
        team = await repo.create_team(season.id, "Team A")
        hooper = await repo.create_hooper(
            team.id,
            season.id,
            "Star Player",
            "sharpshooter",
            {"scoring": 80},
        )
        await repo.update_hooper_backstory(hooper.id, "Some bio")
        await repo.update_hooper_backstory(hooper.id, "")
        retrieved = await repo.get_hooper(hooper.id)
        assert retrieved.backstory == ""


class TestGovernorActivity:
    """Tests for get_governor_activity and get_all_proposals."""

    async def test_pending_review_status_detected(self, repo: Repository):
        """Proposals in pending_review should show that status, not 'pending'."""
        league = await repo.create_league("L")
        season = await repo.create_season(league.id, "S1")
        team = await repo.create_team(season.id, "Team A")
        player = await repo.get_or_create_player("12345", "TestGov")
        player.team_id = team.id
        player.enrolled_season_id = season.id
        await repo.session.flush()

        proposal_id = "prop-001"
        await repo.append_event(
            event_type="proposal.submitted",
            aggregate_id=proposal_id,
            aggregate_type="proposal",
            season_id=season.id,
            governor_id=player.id,
            team_id=team.id,
            payload={
                "id": proposal_id,
                "raw_text": "Wild proposal text",
                "tier": 5,
                "status": "submitted",
            },
        )
        await repo.append_event(
            event_type="proposal.pending_review",
            aggregate_id=proposal_id,
            aggregate_type="proposal",
            season_id=season.id,
            governor_id=player.id,
            payload={"id": proposal_id},
        )

        activity = await repo.get_governor_activity(player.id, season.id)
        assert activity["proposals_submitted"] == 1
        assert activity["proposal_list"][0]["status"] == "pending_review"

    async def test_rejected_status_detected(self, repo: Repository):
        league = await repo.create_league("L")
        season = await repo.create_season(league.id, "S1")
        team = await repo.create_team(season.id, "Team A")
        player = await repo.get_or_create_player("12345", "TestGov")
        player.team_id = team.id
        player.enrolled_season_id = season.id
        await repo.session.flush()

        proposal_id = "prop-002"
        await repo.append_event(
            event_type="proposal.submitted",
            aggregate_id=proposal_id,
            aggregate_type="proposal",
            season_id=season.id,
            governor_id=player.id,
            team_id=team.id,
            payload={
                "id": proposal_id,
                "raw_text": "Rejected proposal",
                "tier": 5,
                "status": "submitted",
            },
        )
        await repo.append_event(
            event_type="proposal.rejected",
            aggregate_id=proposal_id,
            aggregate_type="proposal",
            season_id=season.id,
            governor_id=player.id,
            payload={"id": proposal_id, "rejection_reason": "Too wild"},
        )

        activity = await repo.get_governor_activity(player.id, season.id)
        assert activity["proposal_list"][0]["status"] == "rejected"

    async def test_get_all_proposals(self, repo: Repository):
        league = await repo.create_league("L")
        season = await repo.create_season(league.id, "S1")
        team = await repo.create_team(season.id, "Team A")
        player = await repo.get_or_create_player("12345", "Gov1")
        player.team_id = team.id
        player.enrolled_season_id = season.id
        await repo.session.flush()

        # Submit two proposals, one confirmed, one pending_review
        for i, status_event in enumerate(
            ["proposal.confirmed", "proposal.pending_review"]
        ):
            pid = f"prop-{i}"
            await repo.append_event(
                event_type="proposal.submitted",
                aggregate_id=pid,
                aggregate_type="proposal",
                season_id=season.id,
                governor_id=player.id,
                team_id=team.id,
                payload={
                    "id": pid,
                    "raw_text": f"Proposal {i}",
                    "tier": 1 if i == 0 else 5,
                    "status": "submitted",
                },
            )
            payload = (
                {"proposal_id": pid}
                if status_event == "proposal.confirmed"
                else {"id": pid}
            )
            await repo.append_event(
                event_type=status_event,
                aggregate_id=pid,
                aggregate_type="proposal",
                season_id=season.id,
                governor_id=player.id,
                payload=payload,
            )

        proposals = await repo.get_all_proposals(season.id)
        assert len(proposals) == 2
        statuses = {p["id"]: p["status"] for p in proposals}
        assert statuses["prop-0"] == "confirmed"
        assert statuses["prop-1"] == "pending_review"

    async def test_get_all_seasons(self, repo: Repository):
        league = await repo.create_league("L")
        s1 = await repo.create_season(league.id, "Season 1")
        s2 = await repo.create_season(league.id, "Season 2")

        seasons = await repo.get_all_seasons()
        ids = {s.id for s in seasons}
        assert s1.id in ids
        assert s2.id in ids

    async def test_get_all_players(self, repo: Repository):
        p1 = await repo.get_or_create_player("111", "Alice")
        p2 = await repo.get_or_create_player("222", "Bob")

        players = await repo.get_all_players()
        ids = {p.id for p in players}
        assert p1.id in ids
        assert p2.id in ids


class TestSchedule:
    async def test_create_and_retrieve_schedule(self, repo: Repository):
        league = await repo.create_league("L")
        season = await repo.create_season(league.id, "S1")
        t1 = await repo.create_team(season.id, "T1")
        t2 = await repo.create_team(season.id, "T2")

        entry = await repo.create_schedule_entry(season.id, 1, 0, t1.id, t2.id, "regular")
        assert entry.status == "scheduled"

        schedule = await repo.get_schedule_for_round(season.id, 1)
        assert len(schedule) == 1
        assert schedule[0].home_team_id == t1.id
