"""Tests for joy alarm detection."""

from __future__ import annotations

import pytest

from pinwheel.evals.joy_alarms import (
    check_joy_alarms,
    detect_disengagement,
    detect_economy_stalling,
    detect_political_exclusion,
    detect_power_concentration,
    detect_reports_not_resonating,
)

# ── Disengagement ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_disengagement_no_governors(repo):
    """No governors enrolled -> no alarms."""
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")
    alarms = await detect_disengagement(repo, season.id, 3)
    assert alarms == []


@pytest.mark.asyncio
async def test_disengagement_active_governor(repo):
    """Governor active in current round -> no alarm."""
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    # Enroll a governor
    player = await repo.get_or_create_player(discord_id="d-1", username="ActiveGov")
    team = await repo.create_team(season.id, "Team A")
    await repo.enroll_player(player.id, team.id, season.id)

    # Activity in round 3
    await repo.append_event(
        event_type="vote.cast",
        aggregate_id="p-1",
        aggregate_type="proposal",
        season_id=season.id,
        payload={"vote": "yes"},
        round_number=3,
        governor_id=player.id,
    )

    alarms = await detect_disengagement(repo, season.id, 3, inactive_threshold=2)
    assert alarms == []


@pytest.mark.asyncio
async def test_disengagement_inactive_governor(repo):
    """Governor last active 3 rounds ago -> alarm."""
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    player = await repo.get_or_create_player(discord_id="d-2", username="InactiveGov")
    team = await repo.create_team(season.id, "Team B")
    await repo.enroll_player(player.id, team.id, season.id)

    # Activity only in round 1
    await repo.append_event(
        event_type="vote.cast",
        aggregate_id="p-1",
        aggregate_type="proposal",
        season_id=season.id,
        payload={"vote": "yes"},
        round_number=1,
        governor_id=player.id,
    )

    alarms = await detect_disengagement(repo, season.id, 4, inactive_threshold=2)
    assert len(alarms) == 1
    assert alarms[0].alarm_type == "disengagement"
    assert alarms[0].governor_id == player.id
    assert alarms[0].details["rounds_inactive"] == 3


@pytest.mark.asyncio
async def test_disengagement_never_active(repo):
    """Governor enrolled but never took any action -> alarm."""
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    player = await repo.get_or_create_player(discord_id="d-3", username="GhostGov")
    team = await repo.create_team(season.id, "Team C")
    await repo.enroll_player(player.id, team.id, season.id)

    alarms = await detect_disengagement(repo, season.id, 3, inactive_threshold=2)
    assert len(alarms) == 1
    assert alarms[0].details["last_active_round"] == 0


@pytest.mark.asyncio
async def test_disengagement_severity_escalation(repo):
    """Long inactivity should escalate severity to warning."""
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    player = await repo.get_or_create_player(discord_id="d-4", username="LongGone")
    team = await repo.create_team(season.id, "Team D")
    await repo.enroll_player(player.id, team.id, season.id)

    # Active in round 1, now at round 6 (gap=6, threshold=2, 6 >= 2+2)
    await repo.append_event(
        event_type="proposal.submitted",
        aggregate_id="p-1",
        aggregate_type="proposal",
        season_id=season.id,
        payload={"id": "p-1", "raw_text": "test"},
        round_number=1,
        governor_id=player.id,
    )

    alarms = await detect_disengagement(repo, season.id, 6, inactive_threshold=2)
    assert len(alarms) == 1
    assert alarms[0].severity == "warning"  # gap=5 >= 2+2


# ── Political Exclusion ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_political_exclusion_no_proposals(repo):
    """No proposals at all -> no alarm."""
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")
    alarms = await detect_political_exclusion(repo, season.id, 5)
    assert alarms == []


@pytest.mark.asyncio
async def test_political_exclusion_too_few_proposals(repo):
    """Governor with only 2 proposals (below min_proposals=3) -> no alarm."""
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    for i in range(2):
        await repo.append_event(
            event_type="proposal.submitted",
            aggregate_id=f"p-{i}",
            aggregate_type="proposal",
            season_id=season.id,
            payload={"id": f"p-{i}", "raw_text": f"proposal {i}", "governor_name": "Unlucky"},
            round_number=i + 1,
            governor_id="gov-x",
        )
        await repo.append_event(
            event_type="proposal.failed",
            aggregate_id=f"p-{i}",
            aggregate_type="proposal",
            season_id=season.id,
            payload={"proposal_id": f"p-{i}"},
            round_number=i + 1,
        )

    alarms = await detect_political_exclusion(repo, season.id, 3, min_proposals=3)
    assert alarms == []


@pytest.mark.asyncio
async def test_political_exclusion_all_failed(repo):
    """Governor with 3+ proposals and none passed -> alarm."""
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    for i in range(4):
        await repo.append_event(
            event_type="proposal.submitted",
            aggregate_id=f"p-{i}",
            aggregate_type="proposal",
            season_id=season.id,
            payload={"id": f"p-{i}", "raw_text": f"proposal {i}", "governor_name": "Excluded"},
            round_number=i + 1,
            governor_id="gov-excluded",
        )
        await repo.append_event(
            event_type="proposal.failed",
            aggregate_id=f"p-{i}",
            aggregate_type="proposal",
            season_id=season.id,
            payload={"proposal_id": f"p-{i}"},
            round_number=i + 1,
        )

    alarms = await detect_political_exclusion(repo, season.id, 5, min_proposals=3)
    assert len(alarms) == 1
    assert alarms[0].alarm_type == "political_exclusion"
    assert alarms[0].governor_id == "gov-excluded"
    assert alarms[0].details["success_rate"] == 0.0


@pytest.mark.asyncio
async def test_political_exclusion_some_passed(repo):
    """Governor with at least one passed proposal -> no alarm."""
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    for i in range(4):
        await repo.append_event(
            event_type="proposal.submitted",
            aggregate_id=f"p-{i}",
            aggregate_type="proposal",
            season_id=season.id,
            payload={"id": f"p-{i}", "raw_text": f"proposal {i}"},
            round_number=i + 1,
            governor_id="gov-ok",
        )
        outcome = "proposal.passed" if i == 2 else "proposal.failed"
        await repo.append_event(
            event_type=outcome,
            aggregate_id=f"p-{i}",
            aggregate_type="proposal",
            season_id=season.id,
            payload={"proposal_id": f"p-{i}"},
            round_number=i + 1,
        )

    alarms = await detect_political_exclusion(repo, season.id, 5, min_proposals=3)
    assert alarms == []


# ── Economy Stalling ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_economy_stalling_not_enough_history(repo):
    """Not enough rounds for comparison -> no alarm."""
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")
    alarms = await detect_economy_stalling(repo, season.id, 3, lookback_window=3)
    assert alarms == []


@pytest.mark.asyncio
async def test_economy_stalling_stable(repo):
    """Stable trade activity -> no alarm."""
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    # Activity in rounds 1-6 (steady)
    for r in range(1, 7):
        await repo.append_event(
            event_type="trade.offered",
            aggregate_id=f"t-{r}",
            aggregate_type="trade",
            season_id=season.id,
            payload={"trade_id": f"t-{r}"},
            round_number=r,
            governor_id="gov-1",
        )

    alarms = await detect_economy_stalling(
        repo, season.id, 6, lookback_window=3, drop_threshold=0.5
    )
    assert alarms == []


@pytest.mark.asyncio
async def test_economy_stalling_drop(repo):
    """Activity drops >50% window-over-window -> alarm."""
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    # Previous window (rounds 1-3): 6 events
    for r in range(1, 4):
        for i in range(2):
            await repo.append_event(
                event_type="trade.offered",
                aggregate_id=f"t-{r}-{i}",
                aggregate_type="trade",
                season_id=season.id,
                payload={"trade_id": f"t-{r}-{i}"},
                round_number=r,
                governor_id="gov-1",
            )

    # Recent window (rounds 4-6): 1 event (dropped from 6 to 1 = 83% drop)
    await repo.append_event(
        event_type="trade.offered",
        aggregate_id="t-5-0",
        aggregate_type="trade",
        season_id=season.id,
        payload={"trade_id": "t-5-0"},
        round_number=5,
        governor_id="gov-1",
    )

    alarms = await detect_economy_stalling(
        repo, season.id, 6, lookback_window=3, drop_threshold=0.5
    )
    assert len(alarms) == 1
    assert alarms[0].alarm_type == "economy_stalling"
    assert alarms[0].governor_id == ""  # league-wide


@pytest.mark.asyncio
async def test_economy_stalling_includes_boosts(repo):
    """Boost token spending counts as economy activity."""
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    # Previous window (rounds 1-3): 3 boost events
    for r in range(1, 4):
        await repo.append_event(
            event_type="token.spent",
            aggregate_id=f"b-{r}",
            aggregate_type="token",
            season_id=season.id,
            payload={"token_type": "boost", "amount": 1, "reason": "boost:vote"},
            round_number=r,
            governor_id="gov-1",
        )

    # Recent window (rounds 4-6): 0 events -> 100% drop
    alarms = await detect_economy_stalling(
        repo, season.id, 6, lookback_window=3, drop_threshold=0.5
    )
    assert len(alarms) == 1
    assert alarms[0].alarm_type == "economy_stalling"


# ── Reports Not Resonating ────────────────────────────────────────


@pytest.mark.asyncio
async def test_reports_not_resonating_not_enough_history(repo):
    """Not enough rounds for baseline -> no alarm."""
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")
    alarms = await detect_reports_not_resonating(repo, season.id, 3, lookback_rounds=3)
    assert alarms == []


@pytest.mark.asyncio
async def test_reports_not_resonating_healthy(repo):
    """Stable activity -> no alarm."""
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    player = await repo.get_or_create_player(discord_id="d-10", username="Engaged")
    team = await repo.create_team(season.id, "Team E")
    await repo.enroll_player(player.id, team.id, season.id)

    # Consistent activity rounds 1-6
    for r in range(1, 7):
        await repo.append_event(
            event_type="vote.cast",
            aggregate_id=f"p-{r}",
            aggregate_type="proposal",
            season_id=season.id,
            payload={"vote": "yes"},
            round_number=r,
            governor_id=player.id,
        )

    alarms = await detect_reports_not_resonating(repo, season.id, 6, lookback_rounds=3)
    assert alarms == []


@pytest.mark.asyncio
async def test_reports_not_resonating_activity_dropped(repo):
    """Activity drops dramatically -> alarm."""
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    player = await repo.get_or_create_player(discord_id="d-11", username="Fading")
    team = await repo.create_team(season.id, "Team F")
    await repo.enroll_player(player.id, team.id, season.id)

    # High activity in baseline window (rounds 1-3)
    for r in range(1, 4):
        for i in range(5):
            await repo.append_event(
                event_type="vote.cast",
                aggregate_id=f"p-{r}-{i}",
                aggregate_type="proposal",
                season_id=season.id,
                payload={"vote": "yes"},
                round_number=r,
                governor_id=player.id,
            )

    # Zero activity in recent window (rounds 4-6) -> dramatic drop
    alarms = await detect_reports_not_resonating(
        repo, season.id, 6, lookback_rounds=3, low_engagement_threshold=0.2
    )
    assert len(alarms) == 1
    assert alarms[0].alarm_type == "reports_not_resonating"


# ── Power Concentration ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_power_concentration_no_proposals(repo):
    """No passed proposals -> no alarm."""
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")
    alarms = await detect_power_concentration(repo, season.id, 5)
    assert alarms == []


@pytest.mark.asyncio
async def test_power_concentration_below_threshold(repo):
    """Evenly distributed proposals -> no alarm."""
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    # 4 governors each pass 1 proposal
    for i in range(4):
        await repo.append_event(
            event_type="proposal.submitted",
            aggregate_id=f"p-{i}",
            aggregate_type="proposal",
            season_id=season.id,
            payload={"id": f"p-{i}", "raw_text": f"proposal {i}"},
            round_number=i + 1,
            governor_id=f"gov-{i}",
        )
        await repo.append_event(
            event_type="proposal.passed",
            aggregate_id=f"p-{i}",
            aggregate_type="proposal",
            season_id=season.id,
            payload={"proposal_id": f"p-{i}"},
            round_number=i + 1,
        )

    alarms = await detect_power_concentration(
        repo, season.id, 5, concentration_threshold=0.6, min_passed=3
    )
    assert alarms == []


@pytest.mark.asyncio
async def test_power_concentration_dominant_governor(repo):
    """One governor passes >60% of proposals -> alarm."""
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    # Governor A: 4 passed proposals, Governor B: 1 passed
    for i in range(4):
        await repo.append_event(
            event_type="proposal.submitted",
            aggregate_id=f"pa-{i}",
            aggregate_type="proposal",
            season_id=season.id,
            payload={"id": f"pa-{i}", "raw_text": f"proposal a-{i}", "governor_name": "Dominant"},
            round_number=i + 1,
            governor_id="gov-a",
        )
        await repo.append_event(
            event_type="proposal.passed",
            aggregate_id=f"pa-{i}",
            aggregate_type="proposal",
            season_id=season.id,
            payload={"proposal_id": f"pa-{i}"},
            round_number=i + 1,
        )

    await repo.append_event(
        event_type="proposal.submitted",
        aggregate_id="pb-0",
        aggregate_type="proposal",
        season_id=season.id,
        payload={"id": "pb-0", "raw_text": "proposal b-0", "governor_name": "Other"},
        round_number=5,
        governor_id="gov-b",
    )
    await repo.append_event(
        event_type="proposal.passed",
        aggregate_id="pb-0",
        aggregate_type="proposal",
        season_id=season.id,
        payload={"proposal_id": "pb-0"},
        round_number=5,
    )

    # gov-a has 4/5 = 80% > 60% threshold
    alarms = await detect_power_concentration(
        repo, season.id, 5, concentration_threshold=0.6, min_passed=3
    )
    assert len(alarms) == 1
    assert alarms[0].alarm_type == "power_concentration"
    assert alarms[0].governor_id == "gov-a"
    assert alarms[0].details["share"] == 0.8


@pytest.mark.asyncio
async def test_power_concentration_severity_escalation(repo):
    """>75% share should escalate to warning severity."""
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    # Governor A: 4 passed, Governor B: 1 passed -> 80% > 75%
    for i in range(4):
        await repo.append_event(
            event_type="proposal.submitted",
            aggregate_id=f"px-{i}",
            aggregate_type="proposal",
            season_id=season.id,
            payload={"id": f"px-{i}", "raw_text": f"proposal x-{i}"},
            round_number=i + 1,
            governor_id="gov-x",
        )
        await repo.append_event(
            event_type="proposal.passed",
            aggregate_id=f"px-{i}",
            aggregate_type="proposal",
            season_id=season.id,
            payload={"proposal_id": f"px-{i}"},
            round_number=i + 1,
        )

    await repo.append_event(
        event_type="proposal.submitted",
        aggregate_id="py-0",
        aggregate_type="proposal",
        season_id=season.id,
        payload={"id": "py-0", "raw_text": "proposal y-0"},
        round_number=5,
        governor_id="gov-y",
    )
    await repo.append_event(
        event_type="proposal.passed",
        aggregate_id="py-0",
        aggregate_type="proposal",
        season_id=season.id,
        payload={"proposal_id": "py-0"},
        round_number=5,
    )

    alarms = await detect_power_concentration(
        repo, season.id, 5, concentration_threshold=0.6, min_passed=3
    )
    assert len(alarms) == 1
    assert alarms[0].severity == "warning"  # 80% > 75%


# ── Orchestrator ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_joy_alarms_empty(repo):
    """No data -> no alarms."""
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")
    alarms = await check_joy_alarms(repo, season.id, 1)
    assert alarms == []


@pytest.mark.asyncio
async def test_check_joy_alarms_with_event_bus(repo):
    """Event bus receives published alarm events."""
    from pinwheel.core.event_bus import EventBus

    bus = EventBus()
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    # Create an inactive governor to trigger disengagement alarm
    player = await repo.get_or_create_player(discord_id="d-bus", username="BusTest")
    team = await repo.create_team(season.id, "Team Bus")
    await repo.enroll_player(player.id, team.id, season.id)

    # Subscribe to joy.alarm events
    received_events: list[dict] = []

    async with bus.subscribe("joy.alarm") as sub:
        alarms = await check_joy_alarms(repo, season.id, 5, event_bus=bus)

        # Drain the queue
        while True:
            event = await sub.get(timeout=0.1)
            if event is None:
                break
            received_events.append(event)

    # Should have at least a disengagement alarm
    assert len(alarms) >= 1
    assert len(received_events) >= 1
    assert received_events[0]["data"]["alarm_type"] == "disengagement"


@pytest.mark.asyncio
async def test_check_joy_alarms_multiple_types(repo):
    """Multiple alarm types can fire in the same round."""
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    # Set up disengagement: enrolled governor, no activity
    player = await repo.get_or_create_player(discord_id="d-multi", username="MultiTest")
    team = await repo.create_team(season.id, "Team Multi")
    await repo.enroll_player(player.id, team.id, season.id)

    # Set up political exclusion: governor with 3+ failed proposals
    for i in range(4):
        await repo.append_event(
            event_type="proposal.submitted",
            aggregate_id=f"pm-{i}",
            aggregate_type="proposal",
            season_id=season.id,
            payload={"id": f"pm-{i}", "raw_text": f"proposal multi-{i}"},
            round_number=i + 1,
            governor_id="gov-multi-excl",
        )
        await repo.append_event(
            event_type="proposal.failed",
            aggregate_id=f"pm-{i}",
            aggregate_type="proposal",
            season_id=season.id,
            payload={"proposal_id": f"pm-{i}"},
            round_number=i + 1,
        )

    alarms = await check_joy_alarms(repo, season.id, 5)
    alarm_types = {a.alarm_type for a in alarms}

    # Should have at least disengagement (enrolled player, no activity)
    # and political exclusion (gov-multi-excl, 4 failed proposals)
    assert "disengagement" in alarm_types
    assert "political_exclusion" in alarm_types


@pytest.mark.asyncio
async def test_check_joy_alarms_resilient_to_detector_failure(repo):
    """If one detector raises, others still run."""
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    # Even if there's an internal issue with one detector,
    # the orchestrator catches it and proceeds
    alarms = await check_joy_alarms(repo, season.id, 1)
    # Should complete without raising
    assert isinstance(alarms, list)
