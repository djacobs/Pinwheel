"""Tests for the deferred interpreter — background retry for failed AI interpretations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from pinwheel.core.deferred_interpreter import (
    expire_stale_pending,
    get_pending_interpretations,
    retry_pending_interpretation,
)
from pinwheel.models.governance import GovernanceEvent, ProposalInterpretation


def _make_event(
    *,
    event_type: str,
    aggregate_id: str = "pending-1",
    season_id: str = "season-1",
    governor_id: str = "gov-1",
    team_id: str = "team-1",
    payload: dict | None = None,
    timestamp: datetime | None = None,
) -> GovernanceEvent:
    """Build a GovernanceEvent for testing."""
    return GovernanceEvent(
        id=f"evt-{aggregate_id}-{event_type}",
        event_type=event_type,
        aggregate_id=aggregate_id,
        aggregate_type="proposal",
        season_id=season_id,
        governor_id=governor_id,
        team_id=team_id,
        timestamp=timestamp or datetime.now(UTC),
        payload=payload or {},
    )


class TestGetPendingInterpretations:
    """Test pending interpretation discovery."""

    @pytest.mark.asyncio
    async def test_returns_unresolved_pending(self) -> None:
        """Pending events without ready/expired counterparts are returned."""
        repo = AsyncMock()
        repo.get_events_by_type = AsyncMock(side_effect=[
            # pending
            [_make_event(event_type="proposal.pending_interpretation")],
            # ready
            [],
            # expired
            [],
        ])
        result = await get_pending_interpretations(repo, "season-1")
        assert len(result) == 1
        assert result[0].aggregate_id == "pending-1"

    @pytest.mark.asyncio
    async def test_excludes_already_ready(self) -> None:
        """Pending events with a matching ready event are excluded."""
        repo = AsyncMock()
        repo.get_events_by_type = AsyncMock(side_effect=[
            # pending
            [_make_event(event_type="proposal.pending_interpretation")],
            # ready
            [_make_event(event_type="proposal.interpretation_ready")],
            # expired
            [],
        ])
        result = await get_pending_interpretations(repo, "season-1")
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_excludes_already_expired(self) -> None:
        """Pending events with a matching expired event are excluded."""
        repo = AsyncMock()
        repo.get_events_by_type = AsyncMock(side_effect=[
            # pending
            [_make_event(event_type="proposal.pending_interpretation")],
            # ready
            [],
            # expired
            [_make_event(event_type="proposal.interpretation_expired")],
        ])
        result = await get_pending_interpretations(repo, "season-1")
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_no_double_processing(self) -> None:
        """Multiple pending events for different proposals are all returned."""
        repo = AsyncMock()
        repo.get_events_by_type = AsyncMock(side_effect=[
            # pending - two different proposals
            [
                _make_event(
                    event_type="proposal.pending_interpretation",
                    aggregate_id="p1",
                ),
                _make_event(
                    event_type="proposal.pending_interpretation",
                    aggregate_id="p2",
                ),
            ],
            # ready - only p1 is done
            [_make_event(
                event_type="proposal.interpretation_ready",
                aggregate_id="p1",
            )],
            # expired
            [],
        ])
        result = await get_pending_interpretations(repo, "season-1")
        assert len(result) == 1
        assert result[0].aggregate_id == "p2"


class TestRetryPendingInterpretation:
    """Test retry logic."""

    @pytest.mark.asyncio
    async def test_retry_success(self) -> None:
        """Successful retry appends interpretation_ready event."""
        repo = AsyncMock()

        pending = _make_event(
            event_type="proposal.pending_interpretation",
            payload={
                "raw_text": "Make threes worth 5",
                "ruleset": {},
                "discord_user_id": "12345",
                "token_cost": 1,
            },
        )

        mock_result = ProposalInterpretation(
            effects=[],
            impact_analysis="Threes worth 5",
            confidence=0.9,
            is_mock_fallback=False,
        )

        with patch(
            "pinwheel.ai.interpreter.interpret_proposal_v2",
            return_value=mock_result,
        ):
            success = await retry_pending_interpretation(repo, pending, "fake-key")

        assert success is True
        repo.append_event.assert_called_once()
        call_kwargs = repo.append_event.call_args.kwargs
        assert call_kwargs["event_type"] == "proposal.interpretation_ready"

    @pytest.mark.asyncio
    async def test_retry_mock_fallback_is_failure(self) -> None:
        """If retry also falls back to mock, it's not counted as success."""
        repo = AsyncMock()

        pending = _make_event(
            event_type="proposal.pending_interpretation",
            payload={
                "raw_text": "Some proposal",
                "ruleset": {},
            },
        )

        mock_result = ProposalInterpretation(
            effects=[],
            impact_analysis="Mock result",
            confidence=0.3,
            is_mock_fallback=True,
        )

        with patch(
            "pinwheel.ai.interpreter.interpret_proposal_v2",
            return_value=mock_result,
        ):
            success = await retry_pending_interpretation(repo, pending, "fake-key")

        assert success is False
        repo.append_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_retry_exception_is_failure(self) -> None:
        """API exception during retry returns False."""
        repo = AsyncMock()

        pending = _make_event(
            event_type="proposal.pending_interpretation",
            payload={"raw_text": "test", "ruleset": {}},
        )

        with patch(
            "pinwheel.ai.interpreter.interpret_proposal_v2",
            side_effect=Exception("API timeout"),
        ):
            success = await retry_pending_interpretation(repo, pending, "fake-key")

        assert success is False

    @pytest.mark.asyncio
    async def test_retry_empty_raw_text(self) -> None:
        """Missing raw_text returns False immediately."""
        repo = AsyncMock()

        pending = _make_event(
            event_type="proposal.pending_interpretation",
            payload={"raw_text": "", "ruleset": {}},
        )

        success = await retry_pending_interpretation(repo, pending, "fake-key")
        assert success is False


class TestExpireStale:
    """Test expiry and token refund."""

    @pytest.mark.asyncio
    async def test_expires_old_pending(self) -> None:
        """Pending events older than max_age are expired and tokens refunded."""
        repo = AsyncMock()
        old_time = datetime.now(UTC) - timedelta(hours=5)

        old_pending = _make_event(
            event_type="proposal.pending_interpretation",
            aggregate_id="old-1",
            timestamp=old_time,
            payload={"raw_text": "old proposal", "token_cost": 1},
        )

        # Mock get_pending_interpretations dependencies
        repo.get_events_by_type = AsyncMock(side_effect=[
            # pending
            [old_pending],
            # ready
            [],
            # expired
            [],
        ])

        expired = await expire_stale_pending(repo, "season-1", max_age_hours=4)
        assert len(expired) == 1
        assert expired[0] == "old-1"

        # Should have 2 calls: one for expired event, one for token refund
        assert repo.append_event.call_count == 2
        event_types = [c.kwargs["event_type"] for c in repo.append_event.call_args_list]
        assert "proposal.interpretation_expired" in event_types
        assert "token.regenerated" in event_types

    @pytest.mark.asyncio
    async def test_does_not_expire_recent(self) -> None:
        """Pending events within max_age are not expired."""
        repo = AsyncMock()
        recent_time = datetime.now(UTC) - timedelta(hours=1)

        recent_pending = _make_event(
            event_type="proposal.pending_interpretation",
            aggregate_id="recent-1",
            timestamp=recent_time,
            payload={"raw_text": "recent proposal", "token_cost": 1},
        )

        repo.get_events_by_type = AsyncMock(side_effect=[
            [recent_pending],
            [],
            [],
        ])

        expired = await expire_stale_pending(repo, "season-1", max_age_hours=4)
        assert len(expired) == 0
        repo.append_event.assert_not_called()


class TestIsMockFallback:
    """Test that mock fallback is properly flagged."""

    def test_mock_fallback_flag_default_false(self) -> None:
        """ProposalInterpretation defaults to is_mock_fallback=False."""
        pi = ProposalInterpretation(confidence=0.9)
        assert pi.is_mock_fallback is False

    def test_mock_fallback_v2_mock(self) -> None:
        """interpret_proposal_v2_mock produces is_mock_fallback=False (no AI failure)."""
        from pinwheel.ai.interpreter import interpret_proposal_v2_mock
        from pinwheel.models.rules import RuleSet

        result = interpret_proposal_v2_mock("make threes worth 5", RuleSet())
        # Mock interpreter is a direct call, not a fallback from failed AI
        assert result.is_mock_fallback is False

    def test_parse_json_response_helper(self) -> None:
        """_parse_json_response strips fences and parses Pydantic models."""
        from pinwheel.ai.interpreter import _parse_json_response

        fenced = '```json\n{"confidence": 0.9, "effects": []}\n```'
        result = _parse_json_response(fenced, ProposalInterpretation)
        assert isinstance(result, ProposalInterpretation)
        assert result.confidence == pytest.approx(0.9)  # type: ignore[union-attr]

    def test_parse_json_response_no_fences(self) -> None:
        """_parse_json_response works with plain JSON too."""
        from pinwheel.ai.interpreter import _parse_json_response

        plain = '{"confidence": 0.8, "effects": []}'
        result = _parse_json_response(plain, ProposalInterpretation)
        assert isinstance(result, ProposalInterpretation)
        assert result.confidence == pytest.approx(0.8)  # type: ignore[union-attr]
