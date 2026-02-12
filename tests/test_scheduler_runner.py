"""Tests for the scheduler_runner tick_round function."""

import logging

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from pinwheel.core.event_bus import EventBus
from pinwheel.core.scheduler import generate_round_robin
from pinwheel.core.scheduler_runner import tick_round
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import Base
from pinwheel.db.repository import Repository


@pytest.fixture
async def engine() -> AsyncEngine:
    eng = create_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


def _agent_attrs() -> dict:
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


async def _setup_season(engine: AsyncEngine) -> str:
    """Create a league, season, 4 teams with 3 agents each, and a schedule.

    Returns the season ID.
    """
    async with get_session(engine) as session:
        repo = Repository(session)
        league = await repo.create_league("Test League")
        season = await repo.create_season(
            league.id,
            "Season 1",
            starting_ruleset={"quarter_possessions": 10},
        )

        team_ids = []
        for i in range(4):
            team = await repo.create_team(
                season.id,
                f"Team {i + 1}",
                venue={"name": f"Arena {i + 1}", "capacity": 5000},
            )
            team_ids.append(team.id)
            for j in range(3):
                await repo.create_agent(
                    team_id=team.id,
                    season_id=season.id,
                    name=f"Agent-{i + 1}-{j + 1}",
                    archetype="sharpshooter",
                    attributes=_agent_attrs(),
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

    return season.id


class TestTickRound:
    async def test_advances_round(self, engine: AsyncEngine):
        """tick_round should execute round 1 when no games exist yet."""
        season_id = await _setup_season(engine)
        event_bus = EventBus()

        await tick_round(engine, event_bus)

        async with get_session(engine) as session:
            repo = Repository(session)
            games = await repo.get_games_for_round(season_id, 1)
            assert len(games) == 2  # 4 teams -> 2 games per round

    async def test_advances_consecutive_rounds(self, engine: AsyncEngine):
        """Successive tick_round calls should increment the round number."""
        season_id = await _setup_season(engine)
        event_bus = EventBus()

        await tick_round(engine, event_bus)
        await tick_round(engine, event_bus)

        async with get_session(engine) as session:
            repo = Repository(session)
            r1_games = await repo.get_games_for_round(season_id, 1)
            r2_games = await repo.get_games_for_round(season_id, 2)
            assert len(r1_games) == 2
            assert len(r2_games) == 2

    async def test_skips_when_no_season(self, engine: AsyncEngine):
        """tick_round should do nothing when no season exists."""
        event_bus = EventBus()

        # Should not raise
        await tick_round(engine, event_bus)

    async def test_errors_do_not_propagate(
        self, engine: AsyncEngine, caplog: pytest.LogCaptureFixture
    ):
        """If step_round raises, tick_round should log the error and not re-raise."""
        event_bus = EventBus()

        # Dispose the engine so any DB operation inside tick_round will fail
        await engine.dispose()

        with caplog.at_level(logging.ERROR, logger="pinwheel.core.scheduler_runner"):
            # This should NOT raise despite the broken engine
            await tick_round(engine, event_bus)

        assert "tick_round_error" in caplog.text

    async def test_generates_mirrors(self, engine: AsyncEngine):
        """tick_round should produce mirrors as part of running a round."""
        season_id = await _setup_season(engine)
        event_bus = EventBus()

        await tick_round(engine, event_bus)

        async with get_session(engine) as session:
            repo = Repository(session)
            mirrors = await repo.get_mirrors_for_round(season_id, 1)
            # At least simulation + governance mirrors
            assert len(mirrors) >= 2
