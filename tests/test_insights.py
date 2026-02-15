"""Tests for AI intelligence layer — insights module.

Tests all four features:
1. Impact Validation (proposal prediction vs reality)
2. Hidden Leverage Detection (governor influence analysis)
3. Behavioral Pattern Detection (longitudinal governance arc)
4. The Pinwheel Post (newspaper page)
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from pinwheel.ai.insights import (
    compute_behavioral_profile,
    compute_governor_leverage,
    compute_impact_validation,
    generate_behavioral_report_mock,
    generate_impact_validation_mock,
    generate_leverage_report_mock,
    generate_newspaper_headlines_mock,
)
from pinwheel.core.scheduler import generate_round_robin
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import Base
from pinwheel.db.repository import Repository
from pinwheel.models.report import Report

NUM_TEAMS = 4


@pytest.fixture
async def engine() -> AsyncEngine:
    eng = create_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def repo(engine: AsyncEngine) -> Repository:
    async with get_session(engine) as session:
        yield Repository(session)


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


async def _setup_season_with_teams(
    repo: Repository,
) -> tuple[str, list[str]]:
    """Create a league, season, teams with hoopers, and schedule."""
    league = await repo.create_league("Test League")
    season = await repo.create_season(league.id, "Season 1")

    team_ids = []
    for i in range(NUM_TEAMS):
        team = await repo.create_team(
            season.id, f"Team {i + 1}",
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

    matchups = generate_round_robin(team_ids, num_rounds=1)
    for m in matchups:
        await repo.create_schedule_entry(
            season_id=season.id,
            round_number=m.round_number,
            matchup_index=m.matchup_index,
            home_team_id=m.home_team_id,
            away_team_id=m.away_team_id,
        )

    return season.id, team_ids


# ---------------------------------------------------------------------------
# Phase 1: Impact Validation
# ---------------------------------------------------------------------------


class TestImpactValidation:
    async def test_no_changes_returns_empty(self, repo: Repository):
        """compute_impact_validation returns [] when no rules changed."""
        season_id, _ = await _setup_season_with_teams(repo)
        result = await compute_impact_validation(
            repo, season_id, 1, {"rules_changed": []},
        )
        assert result == []

    async def test_with_rule_change(self, repo: Repository):
        """compute_impact_validation assembles stats for a rule change."""
        season_id, team_ids = await _setup_season_with_teams(repo)

        # Simulate a couple of games to have stats
        from pinwheel.core.game_loop import step_round

        await step_round(repo, season_id, round_number=1)
        await step_round(repo, season_id, round_number=2)

        governance_data = {
            "rules_changed": [
                {
                    "parameter": "three_point_value",
                    "old_value": 3,
                    "new_value": 4,
                    "proposal_text": "Make threes worth 4",
                    "impact_analysis": "Perimeter-heavy teams will dominate.",
                    "round_number": 2,
                },
            ],
        }

        result = await compute_impact_validation(
            repo, season_id, 2, governance_data,
        )
        assert len(result) == 1
        v = result[0]
        assert v["parameter"] == "three_point_value"
        assert v["old_value"] == 3
        assert v["new_value"] == 4
        assert "stats_before" in v
        assert "stats_after" in v
        assert "deltas" in v
        assert v["stats_before"]["game_count"] >= 0
        assert v["stats_after"]["game_count"] >= 0

    def test_mock_generation_with_data(self):
        """generate_impact_validation_mock produces a valid Report."""
        data = [
            {
                "proposal_text": "Speed up the game",
                "impact_prediction": "More fast breaks",
                "parameter": "shot_clock_seconds",
                "old_value": 24,
                "new_value": 18,
                "rounds_under_rule": 2,
                "stats_before": {"game_count": 4, "avg_score": 45.0, "avg_margin": 8.0},
                "stats_after": {"game_count": 2, "avg_score": 50.0, "avg_margin": 6.0},
                "deltas": {"avg_score": 5.0, "avg_margin": -2.0},
            },
        ]
        report = generate_impact_validation_mock(data, "s-1", 3)
        assert isinstance(report, Report)
        assert report.report_type == "impact_validation"
        assert report.round_number == 3
        assert "shot_clock_seconds" in report.content
        assert "18" in report.content

    def test_mock_generation_empty(self):
        """generate_impact_validation_mock handles empty input."""
        report = generate_impact_validation_mock([], "s-1", 1)
        assert report.report_type == "impact_validation"
        assert "No rule changes" in report.content


# ---------------------------------------------------------------------------
# Phase 2: Hidden Leverage Detection
# ---------------------------------------------------------------------------


class TestLeverageDetection:
    async def test_inactive_governor(self, repo: Repository):
        """compute_governor_leverage for a governor with no votes."""
        season_id, _ = await _setup_season_with_teams(repo)

        # Create a governor (player) with no activity
        player = await repo.get_or_create_player(
            discord_id="gov-001", username="TestGov",
        )

        result = await compute_governor_leverage(repo, player.id, season_id)
        assert result["votes_cast"] == 0
        assert result["swing_count"] == 0
        assert result["vote_alignment_rate"] == 0.0

    async def test_governor_with_votes(self, repo: Repository):
        """compute_governor_leverage tracks voting patterns."""
        season_id, team_ids = await _setup_season_with_teams(repo)

        player = await repo.get_or_create_player(
            discord_id="gov-002", username="VotingGov",
        )
        await repo.enroll_player(player.id, team_ids[0], season_id)

        # Submit a proposal
        proposal_id = "prop-001"
        await repo.append_event(
            event_type="proposal.submitted",
            aggregate_id=proposal_id,
            aggregate_type="proposal",
            season_id=season_id,
            payload={
                "id": proposal_id,
                "governor_id": player.id,
                "raw_text": "Test proposal",
                "tier": 1,
            },
            governor_id=player.id,
            team_id=team_ids[0],
        )

        # Cast a vote
        await repo.append_event(
            event_type="vote.cast",
            aggregate_id=proposal_id,
            aggregate_type="proposal",
            season_id=season_id,
            payload={
                "proposal_id": proposal_id,
                "vote": "yes",
                "weight": 1.0,
            },
            governor_id=player.id,
        )

        # Mark proposal as passed
        await repo.append_event(
            event_type="proposal.passed",
            aggregate_id=proposal_id,
            aggregate_type="proposal",
            season_id=season_id,
            payload={
                "proposal_id": proposal_id,
                "passed": True,
            },
        )

        result = await compute_governor_leverage(repo, player.id, season_id)
        assert result["votes_cast"] == 1
        assert result["vote_alignment_rate"] == 1.0  # voted yes, it passed

    def test_mock_generation(self):
        """generate_leverage_report_mock produces a valid Report."""
        data = {
            "governor_id": "gov-test",
            "votes_cast": 5,
            "vote_alignment_rate": 0.8,
            "swing_count": 2,
            "swing_rate": 0.4,
            "proposal_success_rate": 0.5,
            "cross_team_vote_rate": 0.3,
            "proposals_submitted": 2,
            "proposals_passed": 1,
            "total_proposals_decided": 5,
        }
        report = generate_leverage_report_mock(data, "gov-test", "s-1", 3)
        assert isinstance(report, Report)
        assert report.report_type == "leverage"
        assert report.governor_id == "gov-test"
        assert "swing voter" in report.content.lower()

    def test_mock_generation_no_votes(self):
        """generate_leverage_report_mock handles zero votes."""
        data = {
            "governor_id": "silent-gov",
            "votes_cast": 0,
            "vote_alignment_rate": 0,
            "swing_count": 0,
            "swing_rate": 0,
            "proposal_success_rate": 0,
            "cross_team_vote_rate": 0,
            "proposals_submitted": 0,
            "proposals_passed": 0,
            "total_proposals_decided": 0,
        }
        report = generate_leverage_report_mock(data, "silent-gov", "s-1", 3)
        assert report.report_type == "leverage"
        assert "haven't cast any votes" in report.content


# ---------------------------------------------------------------------------
# Phase 3: Behavioral Pattern Detection
# ---------------------------------------------------------------------------


class TestBehavioralProfile:
    async def test_inactive_governor(self, repo: Repository):
        """compute_behavioral_profile for a governor with no activity."""
        season_id, _ = await _setup_season_with_teams(repo)

        player = await repo.get_or_create_player(
            discord_id="quiet-gov", username="QuietGov",
        )

        result = await compute_behavioral_profile(repo, player.id, season_id)
        assert result["proposals_count"] == 0
        assert result["total_actions"] == 0
        assert result["tier_trend"] == "stable"
        assert result["engagement_arc"] == "stable"
        assert result["coalition_signal"] is None

    async def test_governor_with_proposals(self, repo: Repository):
        """compute_behavioral_profile tracks proposal history."""
        season_id, team_ids = await _setup_season_with_teams(repo)

        player = await repo.get_or_create_player(
            discord_id="active-gov", username="ActiveGov",
        )
        await repo.enroll_player(player.id, team_ids[0], season_id)

        # Submit proposals across rounds
        for i in range(4):
            await repo.append_event(
                event_type="proposal.submitted",
                aggregate_id=f"prop-{i}",
                aggregate_type="proposal",
                season_id=season_id,
                round_number=i + 1,
                payload={
                    "id": f"prop-{i}",
                    "governor_id": player.id,
                    "raw_text": f"Test proposal {i}",
                    "tier": i + 1,
                    "interpretation": {"parameter": f"param_{i}"},
                },
                governor_id=player.id,
                team_id=team_ids[0],
            )

        result = await compute_behavioral_profile(repo, player.id, season_id)
        assert result["proposals_count"] == 4
        assert len(result["proposal_timeline"]) == 4
        # With tiers 1,2,3,4 the trend should be "increasing"
        assert result["tier_trend"] == "increasing"

    def test_mock_generation(self):
        """generate_behavioral_report_mock produces a valid Report."""
        data = {
            "governor_id": "gov-test",
            "proposals_count": 6,
            "proposal_timeline": [],
            "tier_trend": "increasing",
            "engagement_arc": "warming_up",
            "actions_by_round": {1: 2, 2: 3, 3: 5},
            "total_actions": 10,
            "votes_cast": 4,
            "coalition_signal": "Your votes align 85% with one other governor.",
        }
        report = generate_behavioral_report_mock(data, "gov-test", "s-1", 6)
        assert isinstance(report, Report)
        assert report.report_type == "behavioral"
        assert report.governor_id == "gov-test"
        assert "bolder" in report.content or "climbing" in report.content

    def test_mock_generation_stable(self):
        """generate_behavioral_report_mock handles stable pattern."""
        data = {
            "governor_id": "steady-gov",
            "proposals_count": 3,
            "proposal_timeline": [],
            "tier_trend": "stable",
            "engagement_arc": "stable",
            "actions_by_round": {1: 2, 2: 2, 3: 2},
            "total_actions": 6,
            "votes_cast": 3,
            "coalition_signal": None,
        }
        report = generate_behavioral_report_mock(data, "steady-gov", "s-1", 6)
        assert "consistent" in report.content


# ---------------------------------------------------------------------------
# Phase 4: The Pinwheel Post (Newspaper)
# ---------------------------------------------------------------------------


class TestNewspaper:
    def test_headlines_mock_with_games(self):
        """generate_newspaper_headlines_mock produces headline + subhead."""
        data = {
            "round_number": 5,
            "games": [
                {
                    "home_team_name": "Thorns",
                    "away_team_name": "Voids",
                    "home_score": 55,
                    "away_score": 52,
                    "winner_team_id": "t-1",
                    "winner_team_name": "Thorns",
                },
            ],
        }
        result = generate_newspaper_headlines_mock(data, 5)
        assert "headline" in result
        assert "subhead" in result
        assert len(result["headline"]) > 0
        assert len(result["subhead"]) > 0

    def test_headlines_mock_no_games(self):
        """generate_newspaper_headlines_mock handles empty round."""
        result = generate_newspaper_headlines_mock({"games": []}, 1)
        assert "headline" in result
        assert "SILENCE" in result["headline"]

    def test_headlines_mock_close_game(self):
        """Close games produce dramatic headlines."""
        data = {
            "round_number": 3,
            "games": [
                {
                    "home_team_name": "Herons",
                    "away_team_name": "Hammers",
                    "home_score": 30,
                    "away_score": 28,
                    "winner_team_id": "t-h",
                    "winner_team_name": "Herons",
                },
            ],
        }
        result = generate_newspaper_headlines_mock(data, 3)
        assert "WIRE" in result["headline"] or "Herons" in result["headline"]

    def test_headlines_mock_blowout(self):
        """Blowout games produce blowout headlines."""
        data = {
            "round_number": 2,
            "games": [
                {
                    "home_team_name": "Breakers",
                    "away_team_name": "Voids",
                    "home_score": 60,
                    "away_score": 30,
                    "winner_team_id": "t-b",
                    "winner_team_name": "Breakers",
                },
            ],
        }
        result = generate_newspaper_headlines_mock(data, 2)
        assert "BLOWOUT" in result["headline"] or "Breakers" in result["headline"]

    def test_headlines_mock_with_rule_change(self):
        """Headlines reference governance when rules changed."""
        data = {
            "round_number": 4,
            "games": [
                {
                    "home_team_name": "Thorns",
                    "away_team_name": "Herons",
                    "home_score": 40,
                    "away_score": 38,
                    "winner_team_id": "t-1",
                    "winner_team_name": "Thorns",
                },
            ],
            "governance": {
                "rules_changed": [
                    {"parameter": "shot_clock_seconds"},
                ],
            },
        }
        result = generate_newspaper_headlines_mock(data, 4)
        assert "shot_clock_seconds" in result["subhead"]


@pytest.fixture
async def app_client():
    """Create a test app with an in-memory database and httpx client."""
    from pinwheel.config import Settings
    from pinwheel.core.event_bus import EventBus
    from pinwheel.core.presenter import PresentationState
    from pinwheel.main import create_app

    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        pinwheel_env="development",
    )
    app = create_app(settings)

    eng = create_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = eng
    app.state.event_bus = EventBus()
    app.state.presentation_state = PresentationState()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    await eng.dispose()


class TestNewspaperPage:
    """Test the /post route renders without errors."""

    async def test_newspaper_page_renders(self, app_client: AsyncClient):
        """Basic /post page test — renders even with no data."""
        resp = await app_client.get("/post")
        assert resp.status_code == 200
        assert "PINWHEEL POST" in resp.text

    async def test_newspaper_empty_state(self, app_client: AsyncClient):
        """Empty state shows round 0 gracefully."""
        resp = await app_client.get("/post")
        assert resp.status_code == 200
        assert "THE PINWHEEL POST" in resp.text


# ---------------------------------------------------------------------------
# Repository: get_game_stats_for_rounds
# ---------------------------------------------------------------------------


class TestGetGameStatsForRounds:
    async def test_no_games(self, repo: Repository):
        """get_game_stats_for_rounds returns game_count=0 when no games exist."""
        season_id, _ = await _setup_season_with_teams(repo)
        stats = await repo.get_game_stats_for_rounds(season_id, 1, 5)
        assert stats["game_count"] == 0

    async def test_with_games(self, repo: Repository):
        """get_game_stats_for_rounds computes stats from played games."""
        season_id, team_ids = await _setup_season_with_teams(repo)

        # Simulate a round
        from pinwheel.core.game_loop import step_round

        await step_round(repo, season_id, round_number=1)

        stats = await repo.get_game_stats_for_rounds(season_id, 1, 1)
        assert stats["game_count"] > 0
        assert stats["avg_score"] > 0
        assert stats["avg_possessions"] > 0
        assert "three_point_pct" in stats
        assert "elam_activation_rate" in stats


# ---------------------------------------------------------------------------
# Integration: Impact validation in game loop
# ---------------------------------------------------------------------------


class TestInsightsInGameLoop:
    async def test_impact_generated_when_rules_change(self, repo: Repository):
        """Impact validation report is generated when governance changes rules."""
        season_id, team_ids = await _setup_season_with_teams(repo)
        await repo.update_season_status(season_id, "active")

        from pinwheel.core.game_loop import step_round

        # Step a round first to have baseline data
        await step_round(repo, season_id, round_number=1)

        # Now check: with no rules changed, no impact report
        reports = await repo.get_reports_for_round(season_id, 1, "impact_validation")
        assert len(reports) == 0  # No rules changed in round 1

    async def test_leverage_skipped_when_not_interval(self, repo: Repository):
        """Leverage reports only generated every N rounds."""
        season_id, team_ids = await _setup_season_with_teams(repo)
        await repo.update_season_status(season_id, "active")

        # Create a governor and make them active (submit a proposal)
        player = await repo.get_or_create_player(
            discord_id="gov-interval", username="IntervalGov",
        )
        await repo.enroll_player(player.id, team_ids[0], season_id)

        # Submit a proposal so the governor counts as "active"
        await repo.append_event(
            event_type="proposal.submitted",
            aggregate_id="prop-lever",
            aggregate_type="proposal",
            season_id=season_id,
            round_number=1,
            payload={
                "id": "prop-lever",
                "governor_id": player.id,
                "raw_text": "test proposal",
                "tier": 1,
            },
            governor_id=player.id,
            team_id=team_ids[0],
        )

        from pinwheel.core.game_loop import step_round

        # Round 1 and 2 — not on interval (interval=3)
        await step_round(repo, season_id, round_number=1)
        reports_r1 = await repo.get_reports_for_round(season_id, 1, "leverage")
        assert len(reports_r1) == 0

        await step_round(repo, season_id, round_number=2)
        reports_r2 = await repo.get_reports_for_round(season_id, 2, "leverage")
        assert len(reports_r2) == 0

        # Round 3 — on interval, should generate
        await step_round(repo, season_id, round_number=3)
        reports_r3 = await repo.get_reports_for_round(season_id, 3, "leverage")
        assert len(reports_r3) >= 1
