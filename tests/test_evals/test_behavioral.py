"""Tests for behavioral shift detection."""

import pytest

from pinwheel.evals.behavioral import (
    compute_report_impact_rate,
    detect_behavioral_shift,
)


@pytest.mark.asyncio
async def test_no_shift_no_baseline(repo):
    """Governor with no history shows no shift."""
    # Create minimal season
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    result = await detect_behavioral_shift(repo, season.id, "gov-1", round_number=1)
    assert result.shifted is False
    assert result.actions_this_round == 0
    assert result.baseline_avg == 0.0


@pytest.mark.asyncio
async def test_shift_from_zero(repo):
    """Going from zero actions to any action counts as shift."""
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    # Add an action in round 3
    await repo.append_event(
        event_type="vote.cast",
        aggregate_id="p-1",
        aggregate_type="proposal",
        season_id=season.id,
        payload={"vote": "yes"},
        round_number=3,
        governor_id="gov-1",
    )

    result = await detect_behavioral_shift(repo, season.id, "gov-1", round_number=3)
    assert result.shifted is True
    assert result.actions_this_round == 1


@pytest.mark.asyncio
async def test_report_impact_rate_no_reports(repo):
    """No private reports means impact rate is 0."""
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    rate = await compute_report_impact_rate(repo, season.id, round_number=1)
    assert rate == 0.0
