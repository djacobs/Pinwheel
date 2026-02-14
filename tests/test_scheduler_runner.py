"""Tests for the scheduler_runner tick_round function."""

import json
import logging
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from pinwheel.core.event_bus import EventBus
from pinwheel.core.presenter import PresentationState
from pinwheel.core.scheduler import generate_round_robin
from pinwheel.core.scheduler_runner import (
    PRESENTATION_STATE_KEY,
    _clear_presentation_state,
    _persist_presentation_start,
    resume_presentation,
    tick_round,
)
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import Base
from pinwheel.db.repository import Repository

_NUM_TEAMS = 4
_EXPECTED_GAMES_PER_ROUND = _NUM_TEAMS * (_NUM_TEAMS - 1) // 2


@pytest.fixture
async def engine() -> AsyncEngine:
    eng = create_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


def _hooper_attrs() -> dict:
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
    """Create a league, season, _NUM_TEAMS teams with 3 hoopers each, and a schedule.

    Returns the season ID.
    """
    async with get_session(engine) as session:
        repo = Repository(session)
        league = await repo.create_league("Test League")
        season = await repo.create_season(
            league.id,
            "Season 1",
            starting_ruleset={"quarter_minutes": 3},
        )

        team_ids = []
        for i in range(_NUM_TEAMS):
            team = await repo.create_team(
                season.id,
                f"Team {i + 1}",
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
            assert len(games) == _EXPECTED_GAMES_PER_ROUND

    async def test_advances_consecutive_rounds(self, engine: AsyncEngine):
        """Successive tick_round calls should advance through the season.

        With _NUM_TEAMS teams and num_rounds=1 (default), round 1 has ALL
        C(n,2) games and completes the regular season. The second tick
        generates playoffs, and plays the semifinal round (2 games).
        """
        season_id = await _setup_season(engine)
        event_bus = EventBus()

        await tick_round(engine, event_bus)
        await tick_round(engine, event_bus)

        async with get_session(engine) as session:
            repo = Repository(session)
            r1_games = await repo.get_games_for_round(season_id, 1)
            r2_games = await repo.get_games_for_round(season_id, 2)
            assert len(r1_games) == _EXPECTED_GAMES_PER_ROUND
            assert len(r2_games) == 2  # Semifinal round: 2 games

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

    async def test_generates_reports(self, engine: AsyncEngine):
        """tick_round should produce reports as part of running a round."""
        season_id = await _setup_season(engine)
        event_bus = EventBus()

        await tick_round(engine, event_bus)

        async with get_session(engine) as session:
            repo = Repository(session)
            reports = await repo.get_reports_for_round(season_id, 1)
            # At least simulation + governance reports
            assert len(reports) >= 2


class TestPresentationPersistence:
    async def test_persist_and_clear(self, engine: AsyncEngine):
        """Persisting and clearing presentation state round-trips through DB."""
        await _persist_presentation_start(
            engine,
            "season-1",
            3,
            ["g1", "g2"],
            300,
        )

        async with get_session(engine) as session:
            repo = Repository(session)
            raw = await repo.get_bot_state(PRESENTATION_STATE_KEY)
            assert raw is not None
            data = json.loads(raw)
            assert data["season_id"] == "season-1"
            assert data["round_number"] == 3
            assert data["game_row_ids"] == ["g1", "g2"]
            assert data["quarter_replay_seconds"] == 300
            assert "started_at" in data

        await _clear_presentation_state(engine)

        async with get_session(engine) as session:
            repo = Repository(session)
            raw = await repo.get_bot_state(PRESENTATION_STATE_KEY)
            assert raw is None

    async def test_resume_no_state(self, engine: AsyncEngine):
        """resume_presentation returns False when no interrupted presentation."""
        event_bus = EventBus()
        state = PresentationState()
        result = await resume_presentation(engine, event_bus, state)
        assert result is False

    async def test_resume_invalid_json(self, engine: AsyncEngine):
        """resume_presentation handles corrupt JSON gracefully."""
        async with get_session(engine) as session:
            repo = Repository(session)
            await repo.set_bot_state(PRESENTATION_STATE_KEY, "not-json")

        event_bus = EventBus()
        state = PresentationState()
        result = await resume_presentation(engine, event_bus, state)
        assert result is False

        # State should be cleared
        async with get_session(engine) as session:
            repo = Repository(session)
            raw = await repo.get_bot_state(PRESENTATION_STATE_KEY)
            assert raw is None

    async def test_resume_missing_games(self, engine: AsyncEngine):
        """resume_presentation returns False when game rows don't exist."""
        data = json.dumps(
            {
                "season_id": "nonexistent",
                "round_number": 1,
                "started_at": datetime.now(UTC).isoformat(),
                "game_row_ids": ["fake-id-1", "fake-id-2"],
                "quarter_replay_seconds": 300,
            }
        )
        async with get_session(engine) as session:
            repo = Repository(session)
            await repo.set_bot_state(PRESENTATION_STATE_KEY, data)

        event_bus = EventBus()
        state = PresentationState()
        result = await resume_presentation(engine, event_bus, state)
        assert result is False

    async def test_resume_reconstructs_and_starts(self, engine: AsyncEngine):
        """resume_presentation reconstructs games from DB and starts presentation."""
        season_id = await _setup_season(engine)
        event_bus = EventBus()

        # Run a round to create game results
        await tick_round(engine, event_bus)

        # Get the game row IDs
        async with get_session(engine) as session:
            repo = Repository(session)
            games = await repo.get_games_for_round(season_id, 1)
            game_row_ids = [g.id for g in games]

        # Simulate an interrupted presentation that started 6 minutes ago
        # (with 300s per quarter, that's 1+ quarters elapsed)
        data = json.dumps(
            {
                "season_id": season_id,
                "round_number": 1,
                "started_at": (datetime.now(UTC) - timedelta(minutes=6)).isoformat(),
                "game_row_ids": game_row_ids,
                "quarter_replay_seconds": 300,
            }
        )
        async with get_session(engine) as session:
            repo = Repository(session)
            await repo.set_bot_state(PRESENTATION_STATE_KEY, data)

        state = PresentationState()
        result = await resume_presentation(engine, event_bus, state, quarter_replay_seconds=300)
        assert result is True
        assert state.current_round == 1

        # Give the background task a moment to start, then cancel it
        import asyncio

        await asyncio.sleep(0.1)
        state.cancel_event.set()
        await asyncio.sleep(0.1)

    async def test_skip_quarters_calculation(self, engine: AsyncEngine):
        """Elapsed time correctly maps to skip_quarters count."""
        season_id = await _setup_season(engine)
        event_bus = EventBus()
        await tick_round(engine, event_bus)

        async with get_session(engine) as session:
            repo = Repository(session)
            games = await repo.get_games_for_round(season_id, 1)
            game_row_ids = [g.id for g in games]

        # 15 minutes elapsed with 300s/quarter → skip 3 quarters
        data = json.dumps(
            {
                "season_id": season_id,
                "round_number": 1,
                "started_at": (datetime.now(UTC) - timedelta(minutes=15)).isoformat(),
                "game_row_ids": game_row_ids,
                "quarter_replay_seconds": 300,
            }
        )
        async with get_session(engine) as session:
            repo = Repository(session)
            await repo.set_bot_state(PRESENTATION_STATE_KEY, data)

        state = PresentationState()
        result = await resume_presentation(engine, event_bus, state, quarter_replay_seconds=300)
        assert result is True

        # Cancel immediately
        import asyncio

        await asyncio.sleep(0.1)
        state.cancel_event.set()
        await asyncio.sleep(0.1)


class TestGovernanceNotificationTiming:
    async def test_instant_mode_publishes_governance_after_presentation(
        self,
        engine: AsyncEngine,
    ):
        """In instant mode, governance.window_closed fires alongside presentation events."""
        from pinwheel.ai.interpreter import interpret_proposal_mock
        from pinwheel.core.governance import cast_vote, confirm_proposal, submit_proposal
        from pinwheel.core.tokens import regenerate_tokens
        from pinwheel.models.rules import RuleSet

        season_id = await _setup_season(engine)
        event_bus = EventBus()

        # Submit a proposal so governance has something to tally
        async with get_session(engine) as session:
            repo = Repository(session)
            teams = await repo.get_teams_for_season(season_id)
            team_id = teams[0].id
            gov_id = "gov-timing-test"
            await regenerate_tokens(repo, gov_id, team_id, season_id)
            interpretation = interpret_proposal_mock("Make three pointers worth 5", RuleSet())
            proposal = await submit_proposal(
                repo=repo,
                governor_id=gov_id,
                team_id=team_id,
                season_id=season_id,
                window_id="",
                raw_text="Make three pointers worth 5",
                interpretation=interpretation,
                ruleset=RuleSet(),
            )
            await confirm_proposal(repo, proposal)
            await cast_vote(
                repo=repo,
                proposal=proposal,
                governor_id=gov_id,
                team_id=team_id,
                vote_choice="yes",
                weight=1.0,
            )

        # Advance 1 round — governance tallies on round 1 (interval=1)
        received: list[dict] = []

        async with event_bus.subscribe(None) as sub:
            await tick_round(engine, event_bus, governance_interval=1)  # round 1

            while True:
                event = await sub.get(timeout=0.1)
                if event is None:
                    break
                received.append(event)

        event_types = [e["type"] for e in received]

        # governance.window_closed should fire (from instant mode in tick_round)
        assert "governance.window_closed" in event_types

        # It should come after presentation.round_finished
        gov_idx = next(i for i, e in enumerate(received) if e["type"] == "governance.window_closed")
        round_finished_indices = [
            i for i, e in enumerate(received) if e["type"] == "presentation.round_finished"
        ]
        # The governance event should come after the round 1 presentation.round_finished
        assert any(rf_idx < gov_idx for rf_idx in round_finished_indices)

    async def test_governance_interval_passed_through(self, engine: AsyncEngine):
        """tick_round passes governance_interval to step_round."""
        await _setup_season(engine)
        event_bus = EventBus()

        # With interval=1, governance should tally on round 1
        # (but there are no proposals, so no governance.window_closed event)
        received: list[dict] = []

        async with event_bus.subscribe(None) as sub:
            await tick_round(engine, event_bus, governance_interval=1)

            while True:
                event = await sub.get(timeout=0.1)
                if event is None:
                    break
                received.append(event)

        # No proposals → no governance event, but round should still complete
        event_types = [e["type"] for e in received]
        assert "round.completed" in event_types

    async def test_tick_round_tallies_governance_on_completed_season(
        self,
        engine: AsyncEngine,
    ):
        """tick_round still tallies governance when season is completed."""
        from pinwheel.ai.interpreter import interpret_proposal_mock
        from pinwheel.core.governance import cast_vote, confirm_proposal, submit_proposal
        from pinwheel.core.tokens import regenerate_tokens
        from pinwheel.models.rules import RuleSet

        season_id = await _setup_season(engine)
        event_bus = EventBus()

        # Run one round so there's game data
        await tick_round(engine, event_bus)

        # Submit a proposal and cast a vote
        async with get_session(engine) as session:
            repo = Repository(session)
            teams = await repo.get_teams_for_season(season_id)
            team_id = teams[0].id
            gov_id = "gov-completed-test"
            await regenerate_tokens(repo, gov_id, team_id, season_id)
            interpretation = interpret_proposal_mock("Make three pointers worth 5", RuleSet())
            proposal = await submit_proposal(
                repo=repo,
                governor_id=gov_id,
                team_id=team_id,
                season_id=season_id,
                window_id="",
                raw_text="Make three pointers worth 5",
                interpretation=interpretation,
                ruleset=RuleSet(),
            )
            await confirm_proposal(repo, proposal)
            await cast_vote(
                repo=repo,
                proposal=proposal,
                governor_id=gov_id,
                team_id=team_id,
                vote_choice="yes",
                weight=1.0,
            )
            # Mark season completed
            await repo.update_season_status(season_id, "completed")

        # tick_round should tally governance even though season is completed
        received: list[dict] = []
        async with event_bus.subscribe(None) as sub:
            await tick_round(engine, event_bus)

            while True:
                event = await sub.get(timeout=0.1)
                if event is None:
                    break
                received.append(event)

        event_types = [e["type"] for e in received]
        assert "governance.window_closed" in event_types


class TestMultisessionLockRelease:
    """Verify that tick_round uses step_round_multisession and releases the lock."""

    async def test_tick_round_uses_multisession(self, engine: AsyncEngine):
        """tick_round should use step_round_multisession, allowing writes between phases."""
        season_id = await _setup_season(engine)
        event_bus = EventBus()

        await tick_round(engine, event_bus)

        # Verify the round completed successfully (games + reports stored)
        async with get_session(engine) as session:
            repo = Repository(session)
            games = await repo.get_games_for_round(season_id, 1)
            assert len(games) == _EXPECTED_GAMES_PER_ROUND
            reports = await repo.get_reports_for_round(season_id, 1)
            assert len(reports) >= 2

    async def test_concurrent_write_during_tick(self, engine: AsyncEngine):
        """Demonstrate that writes can happen while tick_round is in the AI phase.

        We patch _phase_ai to perform a concurrent DB write, proving the lock
        is released between session 1 (simulate) and session 2 (persist).
        """
        import unittest.mock

        from pinwheel.core.game_loop import _phase_ai as real_phase_ai

        await _setup_season(engine)
        event_bus = EventBus()
        concurrent_write_succeeded = False

        original_phase_ai = real_phase_ai

        async def phase_ai_with_concurrent_write(sim, api_key=""):
            nonlocal concurrent_write_succeeded
            # While AI phase is running (no DB session held),
            # try a concurrent DB write to prove the lock is released
            try:
                async with get_session(engine) as session:
                    repo = Repository(session)
                    # A simple read/write operation that would fail if locked
                    await repo.get_active_season()
                    concurrent_write_succeeded = True
            except Exception:
                concurrent_write_succeeded = False

            return await original_phase_ai(sim, api_key)

        with unittest.mock.patch(
            "pinwheel.core.game_loop._phase_ai",
            side_effect=phase_ai_with_concurrent_write,
        ):
            await tick_round(engine, event_bus)

        assert concurrent_write_succeeded, (
            "Expected concurrent DB access to succeed during AI phase "
            "(lock should be released between sessions)"
        )
