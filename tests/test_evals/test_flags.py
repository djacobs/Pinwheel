"""Tests for scenario flag detection."""

import pytest

from pinwheel.evals.flags import (
    detect_all_flags,
    detect_blowout,
    detect_governance_stagnation,
    detect_participation_collapse,
    detect_suspicious_unanimity,
)


def test_detect_blowout_no_blowout():
    games = [{"home_score": 45, "away_score": 42, "home_team": "A", "away_team": "B"}]
    flags = detect_blowout(games, round_number=1)
    assert len(flags) == 0


def test_detect_blowout():
    games = [{"home_score": 80, "away_score": 30, "home_team": "A", "away_team": "B"}]
    flags = detect_blowout(games, round_number=1, elam_margin=13)
    assert len(flags) == 1
    assert flags[0].flag_type == "blowout_game"
    assert flags[0].severity == "warning"


def test_detect_blowout_threshold():
    # Differential of 26 (exactly 2x elam_margin=13) — not flagged (must be >)
    games = [{"home_score": 56, "away_score": 30, "home_team": "A", "away_team": "B"}]
    flags = detect_blowout(games, round_number=1, elam_margin=13)
    assert len(flags) == 0


def test_detect_blowout_just_over():
    # Differential of 27 (> 2x13=26) — flagged
    games = [{"home_score": 57, "away_score": 30, "home_team": "A", "away_team": "B"}]
    flags = detect_blowout(games, round_number=1, elam_margin=13)
    assert len(flags) == 1


@pytest.mark.asyncio
async def test_detect_suspicious_unanimity_none(repo):
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")
    flags = await detect_suspicious_unanimity(repo, season.id, 1)
    assert len(flags) == 0


@pytest.mark.asyncio
async def test_detect_governance_stagnation_none(repo):
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")
    flags = await detect_governance_stagnation(repo, season.id, 1)
    assert len(flags) == 0


@pytest.mark.asyncio
async def test_detect_participation_collapse_no_governors(repo):
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")
    flags = await detect_participation_collapse(repo, season.id, 1)
    assert len(flags) == 0


@pytest.mark.asyncio
async def test_detect_participation_collapse(repo):
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    # Create activity in round 1 for multiple governors
    for i in range(4):
        await repo.append_event(
            event_type="vote.cast",
            aggregate_id=f"p-{i}",
            aggregate_type="proposal",
            season_id=season.id,
            payload={"vote": "yes"},
            round_number=1,
            governor_id=f"gov-{i}",
        )

    # Only 1 governor active in round 2 (25% participation)
    await repo.append_event(
        event_type="vote.cast",
        aggregate_id="p-x",
        aggregate_type="proposal",
        season_id=season.id,
        payload={"vote": "yes"},
        round_number=2,
        governor_id="gov-0",
    )

    flags = await detect_participation_collapse(repo, season.id, 2)
    assert len(flags) == 1
    assert flags[0].flag_type == "participation_collapse"


@pytest.mark.asyncio
async def test_detect_all_flags(repo):
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    games = [{"home_score": 80, "away_score": 20, "home_team": "A", "away_team": "B"}]
    flags = await detect_all_flags(repo, season.id, 1, games)
    # Should at least have the blowout flag
    blowouts = [f for f in flags if f.flag_type == "blowout_game"]
    assert len(blowouts) >= 1
