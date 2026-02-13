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
    """Events should include: game_starting, possessions, game_finished, round_finished."""
    bus = MockEventBus()
    state = PresentationState()
    game = _make_game()

    await present_round([game], bus, state, quarter_replay_seconds=0.01)

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
async def test_present_round_multiple_games_concurrent():
    """Multiple games should run concurrently and all produce events."""
    bus = MockEventBus()
    state = PresentationState()

    games = [_make_game(), _make_game()]

    await present_round(
        games, bus, state,
        quarter_replay_seconds=0.01,
    )

    event_types = [e[0] for e in bus.events]
    assert event_types.count("presentation.game_starting") == 2
    assert event_types.count("presentation.game_finished") == 2
    assert event_types.count("presentation.round_finished") == 1
    assert not state.is_active


@pytest.mark.asyncio
async def test_present_round_possession_events_have_game_index():
    """Possession events should include game_index for frontend routing."""
    bus = MockEventBus()
    state = PresentationState()

    games = [_make_game(), _make_game()]

    await present_round(
        games, bus, state,
        quarter_replay_seconds=0.01,
    )

    possessions = [e for e in bus.events if e[0] == "presentation.possession"]
    game_indices = {p[1]["game_index"] for p in possessions}
    assert game_indices == {0, 1}


@pytest.mark.asyncio
async def test_present_round_enriched_with_names():
    """Presenter should include team and player names from name_cache."""
    bus = MockEventBus()
    state = PresentationState()
    game = _make_game()

    name_cache = {
        "team-a": "Thunderbolts",
        "team-b": "Storm",
        "hooper-1": "Flash Johnson",
    }

    await present_round(
        [game], bus, state,
        quarter_replay_seconds=0.01,
        name_cache=name_cache,
    )

    # Check game_starting has team names
    starting = [e for e in bus.events if e[0] == "presentation.game_starting"]
    assert len(starting) == 1
    assert starting[0][1]["home_team_name"] == "Thunderbolts"
    assert starting[0][1]["away_team_name"] == "Storm"

    # Check game_finished has team names
    finished = [e for e in bus.events if e[0] == "presentation.game_finished"]
    assert len(finished) == 1
    assert finished[0][1]["home_team_name"] == "Thunderbolts"
    assert finished[0][1]["away_team_name"] == "Storm"

    # Check possession events have narration and names
    possessions = [e for e in bus.events if e[0] == "presentation.possession"]
    assert len(possessions) == 3
    for p in possessions:
        assert "narration" in p[1]
        assert len(p[1]["narration"]) > 0
        assert p[1]["ball_handler_name"] == "Flash Johnson"
        assert p[1]["offense_team_name"] == "Thunderbolts"


@pytest.mark.asyncio
async def test_present_round_on_game_finished_callback():
    """on_game_finished should be called for each game (order may vary with concurrency)."""
    bus = MockEventBus()
    state = PresentationState()

    callback_indices: list[int] = []

    async def track_callback(game_index: int) -> None:
        callback_indices.append(game_index)

    games = [_make_game(), _make_game()]

    await present_round(
        games, bus, state,
        quarter_replay_seconds=0.01,
        on_game_finished=track_callback,
    )

    assert sorted(callback_indices) == [0, 1]


@pytest.mark.asyncio
async def test_present_round_callback_error_does_not_break():
    """If on_game_finished raises, presentation should continue."""
    bus = MockEventBus()
    state = PresentationState()

    async def failing_callback(game_index: int) -> None:
        raise RuntimeError("callback exploded")

    games = [_make_game(), _make_game()]

    await present_round(
        games, bus, state,
        quarter_replay_seconds=0.01,
        on_game_finished=failing_callback,
    )

    # Both games should still complete despite callback failures
    event_types = [e[0] for e in bus.events]
    assert event_types.count("presentation.game_finished") == 2
    assert event_types.count("presentation.round_finished") == 1


@pytest.mark.asyncio
async def test_present_round_without_name_cache():
    """Without name_cache, IDs should be used as fallback names."""
    bus = MockEventBus()
    state = PresentationState()
    game = _make_game()

    await present_round(
        [game], bus, state,
        quarter_replay_seconds=0.01,
    )

    starting = [e for e in bus.events if e[0] == "presentation.game_starting"]
    assert starting[0][1]["home_team_name"] == "team-a"
    assert starting[0][1]["away_team_name"] == "team-b"


@pytest.mark.asyncio
async def test_live_games_populated_during_presentation():
    """state.live_games should be populated while presentation runs."""
    bus = MockEventBus()
    state = PresentationState()
    game = _make_game()

    await present_round([game], bus, state, quarter_replay_seconds=0.01)

    # After presentation, live_games should have the game entry
    assert 0 in state.live_games
    live = state.live_games[0]
    assert live.game_id == "g-1-0"
    assert live.home_team_id == "team-a"
    assert live.away_team_id == "team-b"


@pytest.mark.asyncio
async def test_live_games_scores_track_correctly():
    """LiveGameState scores should reflect the last possession's scores."""
    bus = MockEventBus()
    state = PresentationState()
    possessions = [
        _make_possession(quarter=1, home_score=2, away_score=0),
        _make_possession(quarter=1, home_score=4, away_score=0),
        _make_possession(quarter=2, home_score=4, away_score=3),
    ]
    game = _make_game(possessions=possessions)

    await present_round([game], bus, state, quarter_replay_seconds=0.01)

    live = state.live_games[0]
    assert live.home_score == 4
    assert live.away_score == 3


@pytest.mark.asyncio
async def test_live_games_status_transitions_to_final():
    """LiveGameState status should be 'final' after game finishes."""
    bus = MockEventBus()
    state = PresentationState()
    game = _make_game()

    await present_round([game], bus, state, quarter_replay_seconds=0.01)

    live = state.live_games[0]
    assert live.status == "final"


@pytest.mark.asyncio
async def test_live_games_recent_plays_accumulate():
    """LiveGameState should accumulate recent plays."""
    bus = MockEventBus()
    state = PresentationState()
    game = _make_game()

    await present_round([game], bus, state, quarter_replay_seconds=0.01)

    live = state.live_games[0]
    assert len(live.recent_plays) == 3  # 3 possessions in the game
    assert "narration" in live.recent_plays[0]


@pytest.mark.asyncio
async def test_game_finished_includes_leaders():
    """game_finished event should include home_leader and away_leader."""
    from pinwheel.models.game import HooperBoxScore

    bus = MockEventBus()
    state = PresentationState()
    game = _make_game()
    game.box_scores = [
        HooperBoxScore(
            hooper_id="hooper-1", hooper_name="Flash", team_id="team-a", points=20,
        ),
        HooperBoxScore(
            hooper_id="hooper-2", hooper_name="Thunder", team_id="team-b", points=15,
        ),
    ]

    name_cache = {
        "team-a": "Thunderbolts",
        "team-b": "Storm",
        "hooper-1": "Flash Johnson",
        "hooper-2": "Thunder Bolt",
    }

    await present_round(
        [game], bus, state,
        quarter_replay_seconds=0.01,
        name_cache=name_cache,
    )

    finished = [e for e in bus.events if e[0] == "presentation.game_finished"]
    assert len(finished) == 1
    data = finished[0][1]
    assert data["home_leader"]["hooper_id"] == "hooper-1"
    assert data["home_leader"]["hooper_name"] == "Flash Johnson"
    assert data["home_leader"]["points"] == 20
    assert data["away_leader"]["hooper_id"] == "hooper-2"
    assert data["away_leader"]["points"] == 15


@pytest.mark.asyncio
async def test_presentation_state_reset_clears_live_games():
    """reset() should clear live_games, game_results, and name_cache."""
    state = PresentationState()
    state.live_games = {0: object()}  # type: ignore
    state.game_results = [object()]  # type: ignore
    state.name_cache = {"a": "b"}

    state.reset()

    assert state.live_games == {}
    assert state.game_results == []
    assert state.name_cache == {}


@pytest.mark.asyncio
async def test_skip_quarters_skips_early_possessions():
    """skip_quarters should fast-forward through early quarters without publishing."""
    q1_poss = [_make_possession(quarter=1, home_score=i * 2) for i in range(3)]
    q2_poss = [_make_possession(quarter=2, home_score=6 + i * 2) for i in range(3)]
    q3_poss = [_make_possession(quarter=3, home_score=12 + i * 2) for i in range(3)]
    all_poss = q1_poss + q2_poss + q3_poss
    game = _make_game(possessions=all_poss)

    bus = MockEventBus()
    state = PresentationState()

    await present_round(
        [game],
        bus,
        state,
        quarter_replay_seconds=0,  # instant replay for test speed
        skip_quarters=2,           # skip Q1 and Q2
    )

    # Should have game_starting, only Q3 possessions, game_finished, round_finished
    possession_events = [e for e in bus.events if e[0] == "presentation.possession"]
    # Only Q3 possessions should be published (3 of them)
    assert len(possession_events) == 3
    for ev in possession_events:
        assert ev[1]["quarter"] == 3


@pytest.mark.asyncio
async def test_skip_quarters_all_skipped():
    """When skip_quarters >= total quarters, game still finishes properly."""
    q1_poss = [_make_possession(quarter=1, home_score=2)]
    q2_poss = [_make_possession(quarter=2, home_score=4)]
    game = _make_game(possessions=q1_poss + q2_poss)

    bus = MockEventBus()
    state = PresentationState()

    await present_round(
        [game],
        bus,
        state,
        quarter_replay_seconds=0,
        skip_quarters=5,  # more than total quarters
    )

    # No possession events should be published
    possession_events = [e for e in bus.events if e[0] == "presentation.possession"]
    assert len(possession_events) == 0

    # But game_finished and round_finished should still fire
    finished = [e for e in bus.events if e[0] == "presentation.game_finished"]
    assert len(finished) == 1
    round_done = [e for e in bus.events if e[0] == "presentation.round_finished"]
    assert len(round_done) == 1


@pytest.mark.asyncio
async def test_skip_quarters_zero_means_normal():
    """skip_quarters=0 should present all quarters normally."""
    q1_poss = [_make_possession(quarter=1, home_score=2)]
    q2_poss = [_make_possession(quarter=2, home_score=4)]
    game = _make_game(possessions=q1_poss + q2_poss)

    bus = MockEventBus()
    state = PresentationState()

    await present_round(
        [game],
        bus,
        state,
        quarter_replay_seconds=0,
        skip_quarters=0,
    )

    possession_events = [e for e in bus.events if e[0] == "presentation.possession"]
    assert len(possession_events) == 2  # One from each quarter
