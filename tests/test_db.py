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
