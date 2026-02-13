"""Tests for Governance Quality Index."""

from datetime import UTC, datetime, timedelta

import pytest

from pinwheel.evals.gqi import (
    _gini_coefficient,
    _shannon_entropy,
    compute_gqi,
    compute_vote_deliberation,
    store_gqi,
)


def test_shannon_entropy_uniform():
    """Uniform distribution has entropy 1.0."""
    assert _shannon_entropy([1, 1, 1, 1]) == pytest.approx(1.0)


def test_shannon_entropy_single():
    """Single category has entropy 0.0."""
    assert _shannon_entropy([5]) == pytest.approx(0.0)


def test_shannon_entropy_empty():
    assert _shannon_entropy([]) == 0.0


def test_shannon_entropy_skewed():
    """Skewed distribution has entropy < 1.0."""
    result = _shannon_entropy([10, 1, 1])
    assert 0.0 < result < 1.0


def test_gini_equal():
    """Equal values have Gini 0."""
    assert _gini_coefficient([1.0, 1.0, 1.0]) == pytest.approx(0.0)


def test_gini_unequal():
    """Unequal values have positive Gini."""
    result = _gini_coefficient([0.0, 0.0, 10.0])
    assert result > 0.0


def test_gini_empty():
    assert _gini_coefficient([]) == 0.0


@pytest.mark.asyncio
async def test_compute_gqi_empty(repo):
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    result = await compute_gqi(repo, season.id, 1)
    assert result.composite >= 0.0
    assert result.season_id == season.id
    assert result.round_number == 1


@pytest.mark.asyncio
async def test_store_gqi(repo):
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    result = await compute_gqi(repo, season.id, 1)
    await store_gqi(repo, season.id, 1, result)

    stored = await repo.get_eval_results(season.id, eval_type="gqi")
    assert len(stored) == 1


@pytest.mark.asyncio
async def test_gqi_with_activity(repo):
    """GQI with some governance activity."""
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    # Add a proposal event
    await repo.append_event(
        event_type="proposal.submitted",
        aggregate_id="p-1",
        aggregate_type="proposal",
        season_id=season.id,
        payload={
            "interpretation": {"parameter": "elam_margin"},
            "raw_text": "Change elam margin",
        },
        round_number=1,
        governor_id="gov-1",
    )
    await repo.append_event(
        event_type="vote.cast",
        aggregate_id="p-1",
        aggregate_type="proposal",
        season_id=season.id,
        payload={"vote": "yes"},
        round_number=1,
        governor_id="gov-2",
    )

    result = await compute_gqi(repo, season.id, 1)
    assert result.participation_breadth > 0


@pytest.mark.asyncio
async def test_vote_deliberation_no_proposals(repo):
    """No confirmed proposals returns neutral 0.5."""
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    result = await compute_vote_deliberation(repo, season.id, 1)
    assert result == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_vote_deliberation_with_delay(repo):
    """Votes cast 60s after confirmation with 120s window → 0.5 deliberation."""
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    base_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    # Proposal confirmed at base_time
    confirmed_event = await repo.append_event(
        event_type="proposal.confirmed",
        aggregate_id="p-1",
        aggregate_type="proposal",
        season_id=season.id,
        payload={"proposal_id": "p-1"},
        round_number=1,
    )
    confirmed_event.created_at = base_time
    await repo.session.flush()

    # Vote cast 60s later
    vote_event = await repo.append_event(
        event_type="vote.cast",
        aggregate_id="p-1",
        aggregate_type="proposal",
        season_id=season.id,
        payload={"proposal_id": "p-1", "vote": "yes"},
        round_number=1,
        governor_id="gov-1",
    )
    vote_event.created_at = base_time + timedelta(seconds=60)
    await repo.session.flush()

    result = await compute_vote_deliberation(repo, season.id, 1, window_duration_seconds=120.0)
    assert result == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_vote_deliberation_instant_vote(repo):
    """Vote cast immediately after confirmation → 0.0 deliberation."""
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    base_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    confirmed_event = await repo.append_event(
        event_type="proposal.confirmed",
        aggregate_id="p-1",
        aggregate_type="proposal",
        season_id=season.id,
        payload={"proposal_id": "p-1"},
        round_number=1,
    )
    confirmed_event.created_at = base_time
    await repo.session.flush()

    vote_event = await repo.append_event(
        event_type="vote.cast",
        aggregate_id="p-1",
        aggregate_type="proposal",
        season_id=season.id,
        payload={"proposal_id": "p-1", "vote": "yes"},
        round_number=1,
        governor_id="gov-1",
    )
    vote_event.created_at = base_time  # Same timestamp — no delay
    await repo.session.flush()

    result = await compute_vote_deliberation(repo, season.id, 1)
    assert result == pytest.approx(0.0)
