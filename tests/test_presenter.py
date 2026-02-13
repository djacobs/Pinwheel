"""Tests for the real-time presentation layer."""

from __future__ import annotations

import asyncio

import pytest

from pinwheel.core.presenter import PresentationState, present_round
from pinwheel.models.game import GameResult, PossessionLog


class MockEventBus:
    """Captures published events for assertions."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def publish(self, event_type: str, data: dict) -> int:
        self.events.append((event_type, data))
        return 1


def _make_possession(quarter: int = 1, home_score: int = 0, away_score: int = 0) -> PossessionLog:
    return PossessionLog(
        quarter=quarter,
        possession_number=1,
        offense_team_id="team-a",
        ball_handler_id="hooper-1",
        action="mid_range",
        result="made",
        points_scored=2,
        home_score=home_score,
        away_score=away_score,
        game_clock="5:00",
    )


def _make_game(possessions: list[PossessionLog] | None = None) -> GameResult:
    if possessions is None:
        possessions = [
            _make_possession(quarter=1, home_score=2),
            _make_possession(quarter=1, home_score=4),
            _make_possession(quarter=2, home_score=4, away_score=3),
        ]
    return GameResult(
        game_id="g-1-0",
        home_team_id="team-a",
        away_team_id="team-b",
        home_score=50,
        away_score=45,
        winner_team_id="team-a",
        seed=42,
        total_possessions=len(possessions),
        possession_log=possessions,
    )


@pytest.mark.asyncio
async def test_present_round_publishes_events_in_order():
    """Events should be: game_starting, possessions, game_finished, round_finished."""
    bus = MockEventBus()
    state = PresentationState()
    game = _make_game()

    await present_round([game], bus, state, game_interval_seconds=0, quarter_replay_seconds=0.01)

    event_types = [e[0] for e in bus.events]
    assert event_types[0] == "presentation.game_starting"
    assert event_types[-1] == "presentation.round_finished"
    assert "presentation.game_finished" in event_types
    assert event_types.count("presentation.possession") == 3


@pytest.mark.asyncio
async def test_present_round_cancellation():
    """Setting cancel_event should stop presentation cleanly."""
    bus = MockEventBus()
    state = PresentationState()

    # Create a game with many possessions so cancellation can interrupt
    possessions = [_make_possession(quarter=1) for _ in range(50)]
    game = _make_game(possessions=possessions)

    # Cancel almost immediately
    async def cancel_soon():
        await asyncio.sleep(0.05)
        state.cancel_event.set()

    asyncio.create_task(cancel_soon())

    await present_round(
        [game], bus, state,
        game_interval_seconds=0,
        quarter_replay_seconds=5.0,  # Long enough that cancellation interrupts
    )

    # Should have been cancelled before finishing all possessions
    possession_events = [e for e in bus.events if e[0] == "presentation.possession"]
    assert len(possession_events) < 50
    assert not state.is_active  # Should be cleaned up


@pytest.mark.asyncio
async def test_present_round_reentry_guard():
    """If already active, present_round should return immediately."""
    bus = MockEventBus()
    state = PresentationState()
    state.is_active = True  # Pretend already running

    game = _make_game()
    await present_round([game], bus, state)

    # No events should be published
    assert len(bus.events) == 0


@pytest.mark.asyncio
async def test_present_round_empty_results():
    """Empty game list should still publish round_finished."""
    bus = MockEventBus()
    state = PresentationState()

    await present_round([], bus, state)

    event_types = [e[0] for e in bus.events]
    assert "presentation.round_finished" in event_types
    assert not state.is_active


@pytest.mark.asyncio
async def test_presentation_state_reset():
    """reset() should clear all state."""
    state = PresentationState()
    state.is_active = True
    state.current_round = 5
    state.current_game_index = 3
    state.cancel_event.set()

    state.reset()

    assert not state.is_active
    assert state.current_round == 0
    assert state.current_game_index == 0
    assert not state.cancel_event.is_set()


@pytest.mark.asyncio
async def test_present_round_multiple_games():
    """Multiple games should all produce events with inter-game intervals."""
    bus = MockEventBus()
    state = PresentationState()

    games = [_make_game(), _make_game()]

    await present_round(
        games, bus, state,
        game_interval_seconds=0,  # No real delay in tests
        quarter_replay_seconds=0.01,
    )

    event_types = [e[0] for e in bus.events]
    assert event_types.count("presentation.game_starting") == 2
    assert event_types.count("presentation.game_finished") == 2
    assert event_types.count("presentation.round_finished") == 1
    assert not state.is_active
