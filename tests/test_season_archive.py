"""Tests for season archiving -- creation, retrieval, and status changes."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from pinwheel.config import Settings
from pinwheel.core.game_loop import step_round
from pinwheel.core.scheduler import generate_round_robin
from pinwheel.core.season import archive_season
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import Base
from pinwheel.db.repository import Repository
from pinwheel.main import create_app


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


NUM_TEAMS = 4


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


async def _seed_season_with_games(repo: Repository) -> tuple[str, list[str]]:
    """Create a league with NUM_TEAMS teams, schedule, and run all rounds (complete round-robin)."""
    league = await repo.create_league("Test League")
    season = await repo.create_season(
        league.id,
        "Season 1",
        starting_ruleset={
            "quarter_minutes": 3,
            "playoff_semis_best_of": 1,
            "playoff_finals_best_of": 1,
        },
    )

    team_ids = []
    colors = ["#aaa", "#bbb", "#ccc", "#ddd", "#eee", "#fff", "#abc", "#def"]
    for i in range(NUM_TEAMS):
        team = await repo.create_team(
            season.id,
            f"Team {i + 1}",
            color=colors[i % len(colors)],
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

    # Run all ticks (complete round-robin) plus playoffs.
    # 4 teams = 3 ticks for regular season, then 2 more for best-of-1 playoffs
    # (tick 4 = semis, tick 5 = finals).
    ticks_per_cycle = NUM_TEAMS - 1 if NUM_TEAMS % 2 == 0 else NUM_TEAMS
    total_ticks = ticks_per_cycle + 2  # +2 for semis + finals (best-of-1)
    for rn in range(1, total_ticks + 1):
        await step_round(repo, season.id, round_number=rn)

    return season.id, team_ids


class TestArchiveCreation:
    """Test that archive_season captures correct data."""

    async def test_archive_captures_standings(self, repo: Repository):
        """Archive should contain final standings with team names."""
        season_id, team_ids = await _seed_season_with_games(repo)

        archive = await archive_season(repo, season_id)

        assert archive.season_id == season_id
        assert archive.season_name == "Season 1"
        assert isinstance(archive.final_standings, list)
        assert len(archive.final_standings) == NUM_TEAMS
        # Each standing should have team info
        for s in archive.final_standings:
            assert "team_id" in s
            assert "wins" in s
            assert "losses" in s
            assert "team_name" in s

    async def test_archive_captures_game_count(self, repo: Repository):
        """Archive should count total games correctly."""
        season_id, _ = await _seed_season_with_games(repo)

        archive = await archive_season(repo, season_id)

        # regular-season games = C(NUM_TEAMS, 2) + playoff games (2 semis + 1 final)
        regular_games = NUM_TEAMS * (NUM_TEAMS - 1) // 2
        playoff_games = 3  # 2 semis + 1 final
        assert archive.total_games == regular_games + playoff_games

    async def test_archive_has_champion(self, repo: Repository):
        """Archive should identify the champion (top of standings)."""
        season_id, _ = await _seed_season_with_games(repo)

        archive = await archive_season(repo, season_id)

        assert archive.champion_team_id is not None
        assert archive.champion_team_name is not None
        # Champion should be one of the teams
        standing_ids = [s["team_id"] for s in archive.final_standings]
        assert archive.champion_team_id in standing_ids

    async def test_archive_captures_ruleset(self, repo: Repository):
        """Archive should capture the final ruleset."""
        season_id, _ = await _seed_season_with_games(repo)

        archive = await archive_season(repo, season_id)

        assert isinstance(archive.final_ruleset, dict)
        assert archive.final_ruleset.get("quarter_minutes") == 3

    async def test_archive_with_no_proposals(self, repo: Repository):
        """Archive should handle seasons with zero proposals."""
        season_id, _ = await _seed_season_with_games(repo)

        archive = await archive_season(repo, season_id)

        assert archive.total_proposals == 0
        assert archive.total_rule_changes == 0
        assert isinstance(archive.rule_change_history, list)
        assert len(archive.rule_change_history) == 0

    async def test_archive_with_rule_changes(self, repo: Repository):
        """Archive should capture rule change events."""
        season_id, _ = await _seed_season_with_games(repo)

        # Add a rule.enacted event
        await repo.append_event(
            event_type="rule.enacted",
            aggregate_id="rule-1",
            aggregate_type="rule",
            season_id=season_id,
            payload={
                "parameter": "three_point_value",
                "old_value": 3,
                "new_value": 4,
                "round_enacted": 1,
            },
            round_number=1,
        )

        archive = await archive_season(repo, season_id)

        assert archive.total_rule_changes == 1
        assert len(archive.rule_change_history) == 1
        assert archive.rule_change_history[0]["parameter"] == "three_point_value"

    async def test_archive_nonexistent_season_raises(self, repo: Repository):
        """Archiving a nonexistent season should raise ValueError."""
        with pytest.raises(ValueError, match="not found"):
            await archive_season(repo, "nonexistent-id")


class TestArchiveRetrieval:
    """Test archive retrieval methods."""

    async def test_get_season_archive(self, repo: Repository):
        """Should retrieve archive by season_id."""
        season_id, _ = await _seed_season_with_games(repo)
        await archive_season(repo, season_id)

        archive = await repo.get_season_archive(season_id)

        assert archive is not None
        assert archive.season_id == season_id
        assert archive.season_name == "Season 1"

    async def test_get_season_archive_not_found(self, repo: Repository):
        """Should return None for non-archived season."""
        archive = await repo.get_season_archive("nonexistent")
        assert archive is None

    async def test_get_all_archives_empty(self, repo: Repository):
        """Should return empty list when no archives exist."""
        archives = await repo.get_all_archives()
        assert archives == []

    async def test_get_all_archives(self, repo: Repository):
        """Should return all archived seasons."""
        season_id, _ = await _seed_season_with_games(repo)
        await archive_season(repo, season_id)

        archives = await repo.get_all_archives()

        assert len(archives) == 1
        assert archives[0].season_id == season_id


class TestSeasonStatus:
    """Test that archiving sets correct season status."""

    async def test_season_marked_completed(self, repo: Repository):
        """Archiving should set season status to 'completed'."""
        season_id, _ = await _seed_season_with_games(repo)

        # Before archive — status is regular_season_complete because
        # step_round on round 1 with num_rounds=1 completes the regular season
        season = await repo.get_season(season_id)
        assert season.status != "completed"

        await archive_season(repo, season_id)

        # After archive
        season = await repo.get_season(season_id)
        assert season.status == "completed"

    async def test_season_has_completed_at(self, repo: Repository):
        """Archiving should set completed_at timestamp."""
        season_id, _ = await _seed_season_with_games(repo)

        # Before archive
        season = await repo.get_season(season_id)
        assert season.completed_at is None

        await archive_season(repo, season_id)

        # After archive
        season = await repo.get_season(season_id)
        assert season.completed_at is not None


class TestArchivePages:
    """Test that season archive web pages render correctly."""

    @pytest.fixture
    async def app_client(self):
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

    async def test_archives_list_empty(self, app_client):
        """Archives list page renders with no archives."""
        client, _ = app_client
        r = await client.get("/seasons/archive")
        assert r.status_code == 200
        assert "No Archives Yet" in r.text

    async def test_archives_list_with_data(self, app_client):
        """Archives list page renders with archived seasons."""
        client, engine = app_client

        async with get_session(engine) as session:
            repo = Repository(session)
            season_id, _ = await _seed_season_with_games(repo)
            await archive_season(repo, season_id)
            await session.commit()

        r = await client.get("/seasons/archive")
        assert r.status_code == 200
        assert "Season 1" in r.text
        assert "Season Archives" in r.text

    async def test_archive_detail_page(self, app_client):
        """Archive detail page renders with correct data."""
        client, engine = app_client

        async with get_session(engine) as session:
            repo = Repository(session)
            season_id, _ = await _seed_season_with_games(repo)
            await archive_season(repo, season_id)
            await session.commit()

        r = await client.get(f"/seasons/archive/{season_id}")
        assert r.status_code == 200
        assert "Season 1" in r.text
        assert "Final Standings" in r.text
        assert "Champion" in r.text

    async def test_archive_detail_404(self, app_client):
        """Non-existent archive returns 404."""
        client, _ = app_client
        r = await client.get("/seasons/archive/nonexistent")
        assert r.status_code == 404
