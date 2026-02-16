"""Tests for report generation (mock), report models, and private report API auth."""

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy.ext.asyncio import AsyncEngine

from pinwheel.ai.report import (
    PARAMETER_CATEGORIES,
    _compute_rule_correlations,
    _compute_rule_correlations_with_history,
    build_system_context,
    categorize_parameter,
    compute_category_distribution,
    compute_governance_velocity,
    compute_pairwise_alignment,
    compute_private_report_context,
    compute_proposal_parameter_clustering,
    detect_governance_blind_spots,
    generate_governance_report_mock,
    generate_private_report_mock,
    generate_simulation_report_mock,
)
from pinwheel.auth.deps import SESSION_COOKIE_NAME
from pinwheel.config import Settings
from pinwheel.core.event_bus import EventBus
from pinwheel.core.narrative import NarrativeContext
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import Base
from pinwheel.db.repository import Repository
from pinwheel.main import create_app
from pinwheel.models.report import Report, ReportUpdate


class TestSimulationReportMock:
    def test_basic_generation(self):
        data = {
            "round_number": 5,
            "games": [
                {
                    "game_id": "g-5-0",
                    "home_team": "Thorns",
                    "away_team": "Voids",
                    "home_score": 55,
                    "away_score": 48,
                    "elam_activated": False,
                    "total_possessions": 60,
                },
            ],
        }
        report = generate_simulation_report_mock(data, "s-1", 5)
        assert report.report_type == "simulation"
        assert report.round_number == 5
        # Narrative reports reference team names, not generic stats
        assert "Thorns" in report.content or "Voids" in report.content
        assert len(report.content) > 20

    def test_close_game_narrative(self):
        data = {
            "round_number": 3,
            "games": [
                {
                    "game_id": "g-3-0",
                    "home_team": "Herons",
                    "away_team": "Hammers",
                    "home_score": 30,
                    "away_score": 27,
                    "elam_activated": True,
                    "total_possessions": 70,
                },
            ],
        }
        report = generate_simulation_report_mock(data, "s-1", 3)
        # Close games (margin <= 4) should reference the winner
        assert "Herons" in report.content or "Hammers" in report.content

    def test_no_games(self):
        report = generate_simulation_report_mock({"games": []}, "s-1", 1)
        assert report.report_type == "simulation"
        # Empty rounds get a terse message, not "0 games with 0 points"
        assert len(report.content) > 0

    def test_blowout_narrative(self):
        data = {
            "games": [
                {
                    "home_team": "Breakers",
                    "away_team": "Thorns",
                    "home_score": 45,
                    "away_score": 30,
                    "elam_activated": False,
                },
                {
                    "home_team": "Herons",
                    "away_team": "Hammers",
                    "home_score": 25,
                    "away_score": 27,
                    "elam_activated": True,
                },
            ]
        }
        report = generate_simulation_report_mock(data, "s-1", 2)
        # Should mention at least one team name
        content = report.content
        has_team = any(name in content for name in ["Breakers", "Thorns", "Herons", "Hammers"])
        assert has_team


class TestGovernanceReportMock:
    def test_with_proposals(self):
        data = {
            "proposals": [{"id": "p-1", "raw_text": "increase 3pt"}],
            "votes": [
                {"vote": "yes"},
                {"vote": "yes"},
                {"vote": "no"},
            ],
            "rules_changed": [],
        }
        report = generate_governance_report_mock(data, "s-1", 4)
        assert report.report_type == "governance"
        assert "1 proposal" in report.content
        assert "3 votes" in report.content
        assert "2 yes" in report.content
        assert "1 no" in report.content
        # New pattern detection: split vote
        assert "split" in report.content.lower() or "coalitions" in report.content.lower()

    def test_no_activity(self):
        data = {"proposals": [], "votes": [], "rules_changed": []}
        report = generate_governance_report_mock(data, "s-1", 1)
        assert "quiet" in report.content.lower() or "no proposals" in report.content.lower()

    def test_with_rule_changes(self):
        data = {
            "proposals": [{"id": "p-1"}],
            "votes": [],
            "rules_changed": [
                {
                    "parameter": "three_point_value",
                    "old_value": 3,
                    "new_value": 4,
                    "proposal_id": "p-1",
                }
            ],
        }
        report = generate_governance_report_mock(data, "s-1", 5)
        assert "Three Point Value" in report.content
        assert "3" in report.content
        assert "4" in report.content
        # New: game impact analysis
        assert "shooting" in report.content.lower() or "perimeter" in report.content.lower()
        # New: "what the Floor is building" closing
        content_lower = report.content.lower()
        has_trajectory = "floor" in content_lower and (
            "meta" in content_lower or "reshaping" in content_lower
        )
        assert has_trajectory

    def test_id_format(self):
        data = {"proposals": [], "votes": [], "rules_changed": []}
        report = generate_governance_report_mock(data, "s-1", 7)
        assert report.id.startswith("r-gov-7-")

    def test_unanimous_vote_detection(self):
        data = {
            "proposals": [{"id": "p-1"}],
            "votes": [
                {"vote": "yes"},
                {"vote": "yes"},
                {"vote": "yes"},
            ],
            "rules_changed": [],
        }
        report = generate_governance_report_mock(data, "s-1", 3)
        assert "unanimous" in report.content.lower()
        assert "consensus" in report.content.lower()

    def test_proposal_clustering_detection(self):
        data = {
            "proposals": [
                {"id": "p-1", "parameter": "three_point_value"},
                {"id": "p-2", "parameter": "three_point_bonus"},
            ],
            "votes": [],
            "rules_changed": [],
        }
        report = generate_governance_report_mock(data, "s-1", 4)
        assert "2 proposals" in report.content
        assert "three" in report.content.lower()
        assert "focused" in report.content.lower()

    def test_governance_trajectory_with_pending(self):
        from pinwheel.core.narrative import NarrativeContext
        data = {
            "proposals": [{"id": "p-1"}],
            "votes": [],
            "rules_changed": [],
        }
        narrative = NarrativeContext(
            round_number=5,
            pending_proposals=2,
            governance_window_open=False,
            next_tally_round=6,
        )
        report = generate_governance_report_mock(data, "s-1", 5, narrative)
        # Should mention trajectory for pending proposals
        assert "not yet enacted" in report.content or "gain traction" in report.content

    def test_coalition_callout_high_agreement(self):
        """Governors with 80%+ agreement on 3+ proposals get a coalition callout."""
        votes = []
        for pid in ["p1", "p2", "p3", "p4"]:
            votes.append({"governor_id": "gov-alice", "proposal_id": pid, "vote": "yes"})
            votes.append({"governor_id": "gov-bob", "proposal_id": pid, "vote": "yes"})
        data = {
            "proposals": [{"id": f"p{i}"} for i in range(1, 5)],
            "votes": votes,
            "rules_changed": [],
        }
        report = generate_governance_report_mock(data, "s-1", 6)
        assert "gov-alice" in report.content
        assert "gov-bob" in report.content
        assert "voted together" in report.content
        assert "4 of 4" in report.content

    def test_no_coalition_callout_below_threshold(self):
        """Pairs with <80% agreement or <3 shared proposals get no callout."""
        votes = [
            {"governor_id": "gov-alice", "proposal_id": "p1", "vote": "yes"},
            {"governor_id": "gov-bob", "proposal_id": "p1", "vote": "yes"},
            {"governor_id": "gov-alice", "proposal_id": "p2", "vote": "yes"},
            {"governor_id": "gov-bob", "proposal_id": "p2", "vote": "no"},
        ]
        data = {
            "proposals": [{"id": "p1"}, {"id": "p2"}],
            "votes": votes,
            "rules_changed": [],
        }
        report = generate_governance_report_mock(data, "s-1", 6)
        assert "voted together" not in report.content

    def test_coalition_callout_exact_threshold(self):
        """80% agreement on 3+ shared proposals triggers; 75% does not."""
        # 3 agreements + 1 disagreement = 75% on 4 proposals -> no callout
        votes = []
        for pid in ["p1", "p2", "p3"]:
            votes.append({"governor_id": "gov-x", "proposal_id": pid, "vote": "yes"})
            votes.append({"governor_id": "gov-y", "proposal_id": pid, "vote": "yes"})
        votes.append({"governor_id": "gov-x", "proposal_id": "p4", "vote": "yes"})
        votes.append({"governor_id": "gov-y", "proposal_id": "p4", "vote": "no"})
        data = {
            "proposals": [{"id": f"p{i}"} for i in range(1, 5)],
            "votes": votes,
            "rules_changed": [],
        }
        report = generate_governance_report_mock(data, "s-1", 7)
        # 3 of 4 = 75%, below 80% threshold
        assert "voted together" not in report.content

        # Add a 5th proposal they agree on -> 4 of 5 = 80%, meets threshold
        votes.append({"governor_id": "gov-x", "proposal_id": "p5", "vote": "no"})
        votes.append({"governor_id": "gov-y", "proposal_id": "p5", "vote": "no"})
        data["votes"] = votes
        data["proposals"].append({"id": "p5"})
        report2 = generate_governance_report_mock(data, "s-1", 7)
        # 4 of 5 = 80%, meets threshold with 5 >= 3 shared
        assert "voted together" in report2.content


class TestComputePairwiseAlignment:
    def test_empty_votes(self):
        assert compute_pairwise_alignment([]) == []

    def test_no_shared_proposals(self):
        votes = [
            {"governor_id": "g1", "proposal_id": "p1", "vote": "yes"},
            {"governor_id": "g2", "proposal_id": "p2", "vote": "yes"},
        ]
        assert compute_pairwise_alignment(votes) == []

    def test_perfect_agreement(self):
        votes = [
            {"governor_id": "g1", "proposal_id": "p1", "vote": "yes"},
            {"governor_id": "g2", "proposal_id": "p1", "vote": "yes"},
            {"governor_id": "g1", "proposal_id": "p2", "vote": "no"},
            {"governor_id": "g2", "proposal_id": "p2", "vote": "no"},
        ]
        result = compute_pairwise_alignment(votes)
        assert len(result) == 1
        assert result[0]["governor_a"] == "g1"
        assert result[0]["governor_b"] == "g2"
        assert result[0]["shared_proposals"] == 2
        assert result[0]["agreed"] == 2
        assert result[0]["agreement_pct"] == 100.0

    def test_partial_agreement(self):
        votes = [
            {"governor_id": "g1", "proposal_id": "p1", "vote": "yes"},
            {"governor_id": "g2", "proposal_id": "p1", "vote": "no"},
            {"governor_id": "g1", "proposal_id": "p2", "vote": "yes"},
            {"governor_id": "g2", "proposal_id": "p2", "vote": "yes"},
        ]
        result = compute_pairwise_alignment(votes)
        assert len(result) == 1
        assert result[0]["shared_proposals"] == 2
        assert result[0]["agreed"] == 1
        assert result[0]["agreement_pct"] == 50.0

    def test_three_governors(self):
        votes = [
            {"governor_id": "g1", "proposal_id": "p1", "vote": "yes"},
            {"governor_id": "g2", "proposal_id": "p1", "vote": "yes"},
            {"governor_id": "g3", "proposal_id": "p1", "vote": "no"},
        ]
        result = compute_pairwise_alignment(votes)
        assert len(result) == 3  # g1-g2, g1-g3, g2-g3
        # g1-g2 agree (100%), g1-g3 disagree (0%), g2-g3 disagree (0%)
        assert result[0]["agreement_pct"] == 100.0
        assert result[0]["governor_a"] == "g1"
        assert result[0]["governor_b"] == "g2"

    def test_missing_fields_skipped(self):
        votes = [
            {"governor_id": "g1", "proposal_id": "p1", "vote": "yes"},
            {"governor_id": "", "proposal_id": "p1", "vote": "yes"},
            {"proposal_id": "p1", "vote": "yes"},
        ]
        result = compute_pairwise_alignment(votes)
        assert result == []

    def test_sorted_by_agreement_desc(self):
        votes = [
            {"governor_id": "g1", "proposal_id": "p1", "vote": "yes"},
            {"governor_id": "g2", "proposal_id": "p1", "vote": "no"},
            {"governor_id": "g3", "proposal_id": "p1", "vote": "yes"},
            {"governor_id": "g1", "proposal_id": "p2", "vote": "yes"},
            {"governor_id": "g2", "proposal_id": "p2", "vote": "yes"},
            {"governor_id": "g3", "proposal_id": "p2", "vote": "yes"},
        ]
        result = compute_pairwise_alignment(votes)
        pcts = [r["agreement_pct"] for r in result]
        assert pcts == sorted(pcts, reverse=True)


class TestPrivateReportMock:
    def test_active_governor(self):
        data = {"proposals_submitted": 2, "votes_cast": 3, "tokens_spent": 2}
        report = generate_private_report_mock(data, "gov-1", "s-1", 4)
        assert report.report_type == "private"
        assert report.governor_id == "gov-1"
        # Mock now provides context — check for activity framing
        assert "2 proposal" in report.content
        assert "3 vote" in report.content
        # Should include blind spot context or league comparison
        assert len(report.content) > 50  # Richer content now

    def test_inactive_governor(self):
        data = {"proposals_submitted": 0, "votes_cast": 0, "tokens_spent": 0}
        report = generate_private_report_mock(data, "gov-2", "s-1", 4)
        # Should note absence AND contextualize what was missed
        assert "quiet" in report.content.lower() or "absence" in report.content.lower()
        # Should mention league activity they missed
        assert "proposal" in report.content.lower() or "floor" in report.content.lower()

    def test_private_report_id(self):
        data = {"proposals_submitted": 1, "votes_cast": 0, "tokens_spent": 1}
        report = generate_private_report_mock(data, "gov-abc123", "s-1", 3)
        assert "gov-abc1" in report.id

    def test_blind_spot_surfacing(self):
        # Active governor with proposals should get blind spot context
        data = {"proposals_submitted": 2, "votes_cast": 1, "tokens_spent": 1}
        report = generate_private_report_mock(data, "gov-focused", "s-1", 5)
        # Should mention both what they focused on AND what the league focused on
        content_lower = report.content.lower()
        # Check for comparative language
        has_comparison = any(
            phrase in content_lower
            for phrase in ["meanwhile", "area you haven't", "league has seen", "focused on"]
        )
        assert has_comparison

    def test_engagement_trajectory(self):
        # Active governor should get trajectory note
        data = {"proposals_submitted": 1, "votes_cast": 2, "tokens_spent": 0}
        report = generate_private_report_mock(data, "gov-trajectory", "s-1", 6)
        # Should mention engagement trend
        content_lower = report.content.lower()
        has_trajectory = any(
            phrase in content_lower
            for phrase in ["trending", "consistent", "tapered", "engagement", "participation"]
        )
        assert has_trajectory


class TestReportModels:
    def test_report_defaults(self):
        m = Report(id="m-1", report_type="simulation", round_number=5)
        assert m.content == ""
        assert m.team_id == ""
        assert m.governor_id == ""

    def test_private_report(self):
        m = Report(
            id="m-2",
            report_type="private",
            governor_id="gov-1",
            round_number=3,
            content="Reflection text.",
        )
        assert m.governor_id == "gov-1"
        assert m.content == "Reflection text."

    def test_report_update(self):
        mu = ReportUpdate(report_id="m-1", report_type="governance", round_number=5)
        assert mu.excerpt == ""


class TestRuleCorrelations:
    """Tests for _compute_rule_correlations helper."""

    def test_basic_correlation(self) -> None:
        round_data: dict[str, object] = {
            "games": [
                {"home_score": 55, "away_score": 48},
                {"home_score": 60, "away_score": 52},
            ],
        }
        narrative = NarrativeContext(
            round_number=5,
            active_rule_changes=[
                {
                    "parameter": "three_point_value",
                    "old_value": 3,
                    "new_value": 4,
                    "round_enacted": 3,
                },
            ],
        )
        correlations = _compute_rule_correlations(round_data, narrative)
        assert len(correlations) == 1
        assert correlations[0]["parameter"] == "three_point_value"
        assert correlations[0]["avg_total_after"] == 107.5
        assert "three point value" in str(correlations[0]["summary"])
        assert "changed to 4" in str(correlations[0]["summary"])

    def test_precomputed_takes_precedence(self) -> None:
        precomputed: list[dict[str, object]] = [
            {"parameter": "x", "summary": "precomputed result"},
        ]
        round_data: dict[str, object] = {
            "games": [{"home_score": 50, "away_score": 40}],
            "rule_correlations": precomputed,
        }
        narrative = NarrativeContext(
            round_number=5,
            active_rule_changes=[
                {
                    "parameter": "x",
                    "old_value": 1,
                    "new_value": 2,
                    "round_enacted": 3,
                },
            ],
        )
        result = _compute_rule_correlations(round_data, narrative)
        assert result is precomputed

    def test_no_games_returns_empty(self) -> None:
        narrative = NarrativeContext(
            round_number=5,
            active_rule_changes=[
                {
                    "parameter": "x",
                    "old_value": 1,
                    "new_value": 2,
                    "round_enacted": 3,
                },
            ],
        )
        result = _compute_rule_correlations({"games": []}, narrative)
        assert result == []

    def test_no_rule_changes_returns_empty(self) -> None:
        narrative = NarrativeContext(round_number=5)
        result = _compute_rule_correlations(
            {"games": [{"home_score": 50, "away_score": 40}]},
            narrative,
        )
        assert result == []

    def test_future_rule_change_excluded(self) -> None:
        narrative = NarrativeContext(
            round_number=5,
            active_rule_changes=[
                {
                    "parameter": "x",
                    "old_value": 1,
                    "new_value": 2,
                    "round_enacted": 8,
                },
            ],
        )
        result = _compute_rule_correlations(
            {"games": [{"home_score": 50, "away_score": 40}]},
            narrative,
        )
        assert result == []

    def test_with_history(self) -> None:
        round_data: dict[str, object] = {
            "games": [{"home_score": 60, "away_score": 61}],
        }
        narrative = NarrativeContext(
            round_number=5,
            active_rule_changes=[
                {
                    "parameter": "three_point_value",
                    "old_value": 3,
                    "new_value": 4,
                    "round_enacted": 3,
                },
            ],
        )
        correlations = _compute_rule_correlations_with_history(
            round_data, narrative, avg_total_before=108.0,
        )
        assert len(correlations) == 1
        c = correlations[0]
        assert c["avg_total_before"] == 108.0
        assert c["avg_total_after"] == 121.0
        assert c["pct_change"] == 12
        assert "scoring up 12%" in str(c["summary"])
        assert "avg 108 -> 121" in str(c["summary"])


class TestSimReportRuleCorrelation:
    """Tests for rule correlation in the simulation report mock."""

    def test_rule_correlation_in_report(self) -> None:
        data: dict[str, object] = {
            "round_number": 5,
            "games": [
                {
                    "home_team": "Thorns",
                    "away_team": "Voids",
                    "home_score": 55,
                    "away_score": 48,
                    "home_team_id": "t1",
                    "away_team_id": "t2",
                    "winner_team_id": "t1",
                },
                {
                    "home_team": "Herons",
                    "away_team": "Hammers",
                    "home_score": 60,
                    "away_score": 52,
                    "home_team_id": "t3",
                    "away_team_id": "t4",
                    "winner_team_id": "t3",
                },
            ],
        }
        narrative = NarrativeContext(
            round_number=5,
            active_rule_changes=[
                {
                    "parameter": "three_point_value",
                    "old_value": 3,
                    "new_value": 4,
                    "round_enacted": 3,
                },
            ],
        )
        report = generate_simulation_report_mock(
            data, "s-1", 5, narrative,
        )
        assert "three point value" in report.content.lower()
        assert "changed to 4" in report.content

    def test_no_correlation_without_rule_changes(self) -> None:
        data: dict[str, object] = {
            "games": [
                {
                    "home_team": "Thorns",
                    "away_team": "Voids",
                    "home_score": 55,
                    "away_score": 48,
                    "home_team_id": "t1",
                    "away_team_id": "t2",
                    "winner_team_id": "t1",
                },
            ],
        }
        narrative = NarrativeContext(round_number=5)
        report = generate_simulation_report_mock(
            data, "s-1", 5, narrative,
        )
        assert "changed to" not in report.content

    def test_precomputed_correlation_used(self) -> None:
        data: dict[str, object] = {
            "games": [
                {
                    "home_team": "Thorns",
                    "away_team": "Voids",
                    "home_score": 60,
                    "away_score": 55,
                    "home_team_id": "t1",
                    "away_team_id": "t2",
                    "winner_team_id": "t1",
                },
            ],
            "rule_correlations": [
                {
                    "parameter": "three_point_value",
                    "old_value": 3,
                    "new_value": 4,
                    "round_enacted": 2,
                    "avg_total_before": 100.0,
                    "avg_total_after": 115.0,
                    "pct_change": 15.0,
                    "summary": (
                        "Since three point value changed to 4: "
                        "scoring up 15% (avg 100 -> 115)"
                    ),
                },
            ],
        }
        narrative = NarrativeContext(
            round_number=5,
            active_rule_changes=[
                {
                    "parameter": "three_point_value",
                    "old_value": 3,
                    "new_value": 4,
                    "round_enacted": 2,
                },
            ],
        )
        report = generate_simulation_report_mock(
            data, "s-1", 5, narrative,
        )
        assert "scoring up 15%" in report.content
        assert "avg 100 -> 115" in report.content


class TestBuildSystemContext:
    """Tests for build_system_context helper."""

    def test_empty_games(self) -> None:
        ctx = build_system_context({"games": []}, None)
        assert ctx == {}

    def test_round_avg_total_and_margin(self) -> None:
        data = {
            "games": [
                {"home_score": 50, "away_score": 40},
                {"home_score": 60, "away_score": 52},
            ],
        }
        ctx = build_system_context(data, None)
        # (90 + 112) / 2 = 101
        assert ctx["round_avg_total"] == 101
        # (10 + 8) / 2 = 9
        assert ctx["round_avg_margin"] == 9
        assert ctx["all_games_close"] is False
        assert ctx["all_games_blowout"] is False

    def test_all_games_close(self) -> None:
        data = {
            "games": [
                {"home_score": 50, "away_score": 48},
                {"home_score": 45, "away_score": 42},
            ],
        }
        ctx = build_system_context(data, None)
        assert ctx["all_games_close"] is True

    def test_all_games_blowout(self) -> None:
        data = {
            "games": [
                {"home_score": 70, "away_score": 40},
                {"home_score": 65, "away_score": 45},
            ],
        }
        ctx = build_system_context(data, None)
        assert ctx["all_games_blowout"] is True

    def test_standings_gap(self) -> None:
        data = {"games": [{"home_score": 50, "away_score": 40}]}
        narrative = NarrativeContext(
            standings=[
                {"team_id": "t1", "team_name": "Thorns", "wins": 8, "losses": 2, "rank": 1},
                {"team_id": "t2", "team_name": "Voids", "wins": 5, "losses": 5, "rank": 2},
                {"team_id": "t3", "team_name": "Herons", "wins": 2, "losses": 8, "rank": 3},
            ],
        )
        ctx = build_system_context(data, narrative)
        assert ctx["standings_gap"] == 6
        assert ctx["leader_team"] == "Thorns"
        assert ctx["trailer_team"] == "Herons"

    def test_recent_rule_changes(self) -> None:
        data = {"games": [{"home_score": 50, "away_score": 40}]}
        narrative = NarrativeContext(
            active_rule_changes=[
                {
                    "parameter": "three_point_value",
                    "old_value": 3,
                    "new_value": 4,
                    "round_enacted": 3,
                },
            ],
        )
        ctx = build_system_context(data, narrative)
        assert "recent_rule_changes" in ctx
        changes = ctx["recent_rule_changes"]
        assert isinstance(changes, list)
        assert len(changes) == 1
        assert changes[0]["parameter"] == "three_point_value"

    def test_this_round_rule_changes(self) -> None:
        data = {
            "games": [{"home_score": 50, "away_score": 40}],
            "rule_changes": [
                {"parameter": "elam_margin", "old_value": 15, "new_value": 10},
            ],
        }
        ctx = build_system_context(data, None)
        assert "this_round_rule_changes" in ctx
        assert ctx["this_round_rule_changes"][0]["parameter"] == "elam_margin"

    def test_streaks_summary(self) -> None:
        data = {"games": [{"home_score": 50, "away_score": 40}]}
        narrative = NarrativeContext(
            streaks={"t1": 5, "t2": -3, "t3": 1},
            standings=[
                {"team_id": "t1", "team_name": "Thorns", "wins": 8, "rank": 1},
                {"team_id": "t2", "team_name": "Voids", "wins": 3, "rank": 2},
                {"team_id": "t3", "team_name": "Herons", "wins": 5, "rank": 3},
            ],
        )
        ctx = build_system_context(data, narrative)
        streaks = ctx.get("streaks_summary")
        assert isinstance(streaks, list)
        # t1 (5) and t2 (-3) qualify; t3 (1) does not
        team_names = {s["team"] for s in streaks}
        assert "Thorns" in team_names
        assert "Voids" in team_names
        assert "Herons" not in team_names


class TestSimReportSystemContext:
    """Tests for system-level context in the simulation report mock."""

    def _make_games(
        self,
        scores: list[tuple[str, str, int, int]],
    ) -> list[dict[str, object]]:
        """Build game dicts from (home_name, away_name, home_score, away_score)."""
        games: list[dict[str, object]] = []
        for i, (h, a, hs, aws) in enumerate(scores):
            wid = f"t{2 * i}" if hs >= aws else f"t{2 * i + 1}"
            games.append({
                "home_team": h,
                "away_team": a,
                "home_team_id": f"t{2 * i}",
                "away_team_id": f"t{2 * i + 1}",
                "home_score": hs,
                "away_score": aws,
                "winner_team_id": wid,
            })
        return games

    def test_close_games_surface_margin_observation(self) -> None:
        """When all games are close (<= 5 margin), the report notes it."""
        games = self._make_games([
            ("Thorns", "Voids", 45, 42),
            ("Herons", "Hammers", 38, 35),
        ])
        data: dict[str, object] = {"games": games}
        report = generate_simulation_report_mock(data, "s-1", 5)
        content_lower = report.content.lower()
        assert "margin" in content_lower or "close" in content_lower

    def test_competitive_balance_compressed(self) -> None:
        """When standings gap is <= 2, the report surfaces compression."""
        games = self._make_games([
            ("Thorns", "Voids", 50, 45),
        ])
        data: dict[str, object] = {"games": games}
        narrative = NarrativeContext(
            standings=[
                {"team_id": "t0", "team_name": "Thorns", "wins": 6, "losses": 4, "rank": 1},
                {"team_id": "t1", "team_name": "Voids", "wins": 5, "losses": 5, "rank": 2},
                {"team_id": "t2", "team_name": "Herons", "wins": 5, "losses": 5, "rank": 3},
                {"team_id": "t3", "team_name": "Hammers", "wins": 4, "losses": 6, "rank": 4},
            ],
        )
        report = generate_simulation_report_mock(data, "s-1", 5, narrative)
        content_lower = report.content.lower()
        assert "compressed" in content_lower or "tight" in content_lower

    def test_competitive_balance_widening(self) -> None:
        """When standings gap is >= 6, the report notes widening gap."""
        games = self._make_games([
            ("Thorns", "Voids", 50, 45),
        ])
        data: dict[str, object] = {"games": games}
        narrative = NarrativeContext(
            standings=[
                {"team_id": "t0", "team_name": "Thorns", "wins": 10, "losses": 2, "rank": 1},
                {"team_id": "t1", "team_name": "Voids", "wins": 4, "losses": 8, "rank": 2},
            ],
        )
        report = generate_simulation_report_mock(data, "s-1", 5, narrative)
        content_lower = report.content.lower()
        assert "gap" in content_lower or "widening" in content_lower

    def test_closing_reveals_system_not_prescriptions(self) -> None:
        """Closing observation describes patterns, never prescribes actions."""
        games = self._make_games([
            ("Thorns", "Voids", 55, 48),
            ("Herons", "Hammers", 52, 50),
        ])
        data: dict[str, object] = {"games": games}
        narrative = NarrativeContext(
            round_number=8,
            total_rounds=9,
            season_arc="late",
        )
        report = generate_simulation_report_mock(data, "s-1", 8, narrative)
        content_lower = report.content.lower()
        # Should NOT contain prescriptive language
        assert "should" not in content_lower
        assert "need to" not in content_lower
        # Should contain closing observation
        assert "winding down" in content_lower or "playoff" in content_lower

    def test_closing_with_rule_correlation(self) -> None:
        """When rule correlations exist, closing notes governance in box scores."""
        games = self._make_games([
            ("Thorns", "Voids", 60, 55),
        ])
        data: dict[str, object] = {"games": games}
        narrative = NarrativeContext(
            round_number=6,
            active_rule_changes=[
                {
                    "parameter": "three_point_value",
                    "old_value": 3,
                    "new_value": 4,
                    "round_enacted": 4,
                },
            ],
        )
        report = generate_simulation_report_mock(data, "s-1", 6, narrative)
        content_lower = report.content.lower()
        # Should mention governance showing up in box scores
        assert "governance" in content_lower or "box scores" in content_lower

    def test_closing_with_pending_proposals(self) -> None:
        """When proposals are pending, closing notes the game may change."""
        games = self._make_games([
            ("Thorns", "Voids", 50, 48),
        ])
        data: dict[str, object] = {"games": games}
        narrative = NarrativeContext(
            round_number=5,
            pending_proposals=3,
        )
        report = generate_simulation_report_mock(data, "s-1", 5, narrative)
        content_lower = report.content.lower()
        # Should mention pending proposals affecting the game
        assert "3 proposals" in content_lower or "governors' vote" in content_lower

    def test_scoring_surge_detected(self) -> None:
        """High scoring (80+ avg total) is surfaced as a system observation."""
        games = self._make_games([
            ("Thorns", "Voids", 50, 40),
            ("Herons", "Hammers", 48, 38),
        ])
        data: dict[str, object] = {"games": games}
        report = generate_simulation_report_mock(data, "s-1", 3)
        content_lower = report.content.lower()
        # With avg total 88, this triggers scoring surge
        assert "scoring" in content_lower or "surged" in content_lower

    def test_defense_dominated_detected(self) -> None:
        """Low scoring (<= 35 avg total) is surfaced as defense domination."""
        games = self._make_games([
            ("Thorns", "Voids", 18, 15),
            ("Herons", "Hammers", 17, 14),
        ])
        data: dict[str, object] = {"games": games}
        report = generate_simulation_report_mock(data, "s-1", 3)
        content_lower = report.content.lower()
        assert "defense" in content_lower or "dominated" in content_lower

    def test_lede_never_generic(self) -> None:
        """The lede should never be template-filling language like 'Round N saw...'."""
        games = self._make_games([
            ("Thorns", "Voids", 50, 45),
        ])
        data: dict[str, object] = {"games": games}
        report = generate_simulation_report_mock(data, "s-1", 8)
        # The lede should NOT start with "Round 8 saw"
        assert not report.content.startswith("Round 8 saw")
        # It should name a team
        assert "Thorns" in report.content or "Voids" in report.content

    def test_report_names_teams_and_scores(self) -> None:
        """Every report should name teams and include scores."""
        games = self._make_games([
            ("Thorns", "Voids", 55, 42),
            ("Herons", "Hammers", 48, 50),
        ])
        data: dict[str, object] = {"games": games}
        report = generate_simulation_report_mock(data, "s-1", 4)
        # Should contain at least one team name and score
        has_team = any(
            name in report.content
            for name in ["Thorns", "Voids", "Herons", "Hammers"]
        )
        assert has_team
        # Should contain a score
        assert "55" in report.content or "50" in report.content or "48" in report.content


class TestComputeProposalParameterClustering:
    """Tests for compute_proposal_parameter_clustering."""

    def test_empty_proposals(self) -> None:
        result = compute_proposal_parameter_clustering([])
        assert result == []

    def test_no_parameter_key(self) -> None:
        """Proposals without a 'parameter' key are silently skipped."""
        result = compute_proposal_parameter_clustering([{"id": "p1"}])
        assert result == []

    def test_single_proposal(self) -> None:
        result = compute_proposal_parameter_clustering(
            [{"parameter": "three_point_value"}]
        )
        assert len(result) == 1
        assert result[0]["category"] == "three_point"
        assert result[0]["count"] == 1
        assert result[0]["historical_count"] == 0

    def test_multiple_same_category(self) -> None:
        """Two proposals targeting the same category prefix cluster together."""
        proposals = [
            {"parameter": "three_point_value"},
            {"parameter": "three_point_bonus"},
        ]
        result = compute_proposal_parameter_clustering(proposals)
        assert len(result) == 1
        assert result[0]["category"] == "three_point"
        assert result[0]["count"] == 2

    def test_different_categories(self) -> None:
        """Proposals targeting different categories produce separate entries."""
        proposals = [
            {"parameter": "three_point_value"},
            {"parameter": "elam_margin"},
            {"parameter": "scoring_bonus"},
        ]
        result = compute_proposal_parameter_clustering(proposals)
        assert len(result) == 3
        categories = {str(r["category"]) for r in result}
        assert categories == {"three_point", "elam", "scoring"}

    def test_sorted_by_count_descending(self) -> None:
        proposals = [
            {"parameter": "elam_margin"},
            {"parameter": "three_point_value"},
            {"parameter": "three_point_bonus"},
            {"parameter": "three_point_range"},
        ]
        result = compute_proposal_parameter_clustering(proposals)
        counts = [int(r["count"]) for r in result]
        assert counts == sorted(counts, reverse=True)
        assert result[0]["category"] == "three_point"
        assert result[0]["count"] == 3

    def test_with_history(self) -> None:
        """Historical proposals increase historical_count but not count."""
        proposals = [{"parameter": "three_point_value"}]
        history = [
            {"parameter": "three_point_bonus"},
            {"parameter": "three_point_range"},
            {"parameter": "elam_margin"},
        ]
        result = compute_proposal_parameter_clustering(proposals, history=history)
        assert len(result) == 1  # Only current round categories
        assert result[0]["category"] == "three_point"
        assert result[0]["count"] == 1
        assert result[0]["historical_count"] == 2  # two three_point in history

    def test_compound_prefix_shot_clock(self) -> None:
        result = compute_proposal_parameter_clustering(
            [{"parameter": "shot_clock_duration"}]
        )
        assert result[0]["category"] == "shot_clock"

    def test_single_word_parameter(self) -> None:
        result = compute_proposal_parameter_clustering(
            [{"parameter": "stamina"}]
        )
        assert result[0]["category"] == "stamina"

    def test_none_parameter_skipped(self) -> None:
        result = compute_proposal_parameter_clustering(
            [{"parameter": None}, {"parameter": "elam_margin"}]
        )
        assert len(result) == 1
        assert result[0]["category"] == "elam"


class TestComputeGovernanceVelocity:
    """Tests for compute_governance_velocity."""

    def test_silent_round(self) -> None:
        result = compute_governance_velocity(0, 0)
        assert result["velocity_label"] == "silent"
        assert result["proposals_this_round"] == 0
        assert result["votes_this_round"] == 0
        assert result["is_season_peak"] is False

    def test_normal_no_history(self) -> None:
        """Without history, non-zero activity defaults to normal."""
        result = compute_governance_velocity(2, 5)
        assert result["velocity_label"] == "normal"
        assert result["proposals_this_round"] == 2
        assert result["votes_this_round"] == 5
        assert result["avg_proposals_per_round"] == 0.0
        assert result["avg_votes_per_round"] == 0.0

    def test_peak_velocity(self) -> None:
        """Activity >= 2x the historical average triggers peak."""
        result = compute_governance_velocity(
            current_round_proposals=6,
            current_round_votes=10,
            season_proposals_by_round={1: 2, 2: 3, 3: 1},
            season_votes_by_round={1: 3, 2: 4, 3: 2},
        )
        # avg_proposals = 2.0, avg_votes = 3.0, avg_total = 5.0
        # current_total = 16, threshold = 10.0 -> peak
        assert result["velocity_label"] == "peak"
        assert result["is_season_peak"] is True

    def test_high_velocity(self) -> None:
        """Activity between 1.3x and 2x triggers high."""
        result = compute_governance_velocity(
            current_round_proposals=3,
            current_round_votes=5,
            season_proposals_by_round={1: 2, 2: 3, 3: 2},
            season_votes_by_round={1: 3, 2: 3, 3: 3},
        )
        # avg_proposals = 2.33, avg_votes = 3.0, avg_total = 5.33
        # current_total = 8, 5.33*1.3=6.93, 5.33*2.0=10.67
        # 8 >= 6.93 -> high
        assert result["velocity_label"] == "high"

    def test_low_velocity(self) -> None:
        """Activity <= 0.5x triggers low."""
        result = compute_governance_velocity(
            current_round_proposals=1,
            current_round_votes=0,
            season_proposals_by_round={1: 4, 2: 5, 3: 3},
            season_votes_by_round={1: 6, 2: 8, 3: 5},
        )
        # avg_proposals = 4.0, avg_votes = 6.33, avg_total = 10.33
        # current_total = 1, 10.33*0.5=5.17 -> 1 <= 5.17 -> low
        assert result["velocity_label"] == "low"

    def test_normal_velocity(self) -> None:
        """Activity between 0.5x and 1.3x is normal."""
        result = compute_governance_velocity(
            current_round_proposals=2,
            current_round_votes=3,
            season_proposals_by_round={1: 2, 2: 3, 3: 2},
            season_votes_by_round={1: 3, 2: 3, 3: 3},
        )
        # avg_proposals = 2.33, avg_votes = 3.0, avg_total = 5.33
        # current_total = 5, not >= 6.93, not <= 2.67 -> normal
        assert result["velocity_label"] == "normal"

    def test_is_season_peak_proposals(self) -> None:
        """Current proposals exceed historical max triggers is_season_peak."""
        result = compute_governance_velocity(
            current_round_proposals=5,
            current_round_votes=3,
            season_proposals_by_round={1: 2, 2: 4, 3: 3},
            season_votes_by_round={1: 3, 2: 3, 3: 3},
        )
        assert result["is_season_peak"] is True

    def test_is_season_peak_votes(self) -> None:
        """Current votes exceed historical max triggers is_season_peak."""
        result = compute_governance_velocity(
            current_round_proposals=2,
            current_round_votes=10,
            season_proposals_by_round={1: 2, 2: 3, 3: 3},
            season_votes_by_round={1: 3, 2: 4, 3: 5},
        )
        assert result["is_season_peak"] is True

    def test_avg_values_rounded(self) -> None:
        result = compute_governance_velocity(
            current_round_proposals=3,
            current_round_votes=5,
            season_proposals_by_round={1: 1, 2: 2, 3: 3},
            season_votes_by_round={1: 2, 2: 3, 3: 4},
        )
        assert result["avg_proposals_per_round"] == 2.0
        assert result["avg_votes_per_round"] == 3.0


class TestDetectGovernanceBlindSpots:
    """Tests for detect_governance_blind_spots."""

    def test_no_proposals_all_blind(self) -> None:
        """With no proposals, all default categories are blind spots."""
        result = detect_governance_blind_spots([], [])
        assert len(result) == 10  # all default categories
        assert "scoring" in result
        assert "defense" in result
        assert "three_point" in result

    def test_partial_coverage(self) -> None:
        """Targeting some categories leaves others as blind spots."""
        proposals = [
            {"parameter": "three_point_value"},
            {"parameter": "scoring_bonus"},
        ]
        result = detect_governance_blind_spots(proposals, [])
        assert "three_point" not in result
        assert "scoring" not in result
        assert "defense" in result
        assert "elam" in result

    def test_rule_changes_reduce_blind_spots(self) -> None:
        """Rule changes also count as targeted."""
        rules_changed = [{"parameter": "elam_margin"}]
        result = detect_governance_blind_spots([], rules_changed)
        assert "elam" not in result
        assert "defense" in result

    def test_full_coverage_no_blind_spots(self) -> None:
        """When all categories are targeted, no blind spots remain."""
        proposals = [
            {"parameter": "scoring_value"},
            {"parameter": "defense_bonus"},
            {"parameter": "pace_modifier"},
            {"parameter": "three_point_distance"},
            {"parameter": "elam_margin"},
            {"parameter": "foul_limit"},
            {"parameter": "stamina_drain"},
            {"parameter": "shot_clock_duration"},
            {"parameter": "rebound_weight"},
            {"parameter": "turnover_chance"},
        ]
        result = detect_governance_blind_spots(proposals, [])
        assert result == []

    def test_custom_categories(self) -> None:
        """Custom parameter categories override defaults."""
        result = detect_governance_blind_spots(
            [], [], all_parameter_categories=["alpha", "beta", "gamma"]
        )
        assert result == ["alpha", "beta", "gamma"]

    def test_custom_categories_partial_coverage(self) -> None:
        proposals = [{"parameter": "alpha_value"}]
        result = detect_governance_blind_spots(
            proposals, [], all_parameter_categories=["alpha", "beta"]
        )
        assert result == ["beta"]

    def test_case_insensitive_matching(self) -> None:
        """Parameter matching is case-insensitive."""
        proposals = [{"parameter": "Three_Point_Value"}]
        result = detect_governance_blind_spots(proposals, [])
        assert "three_point" not in result

    def test_both_proposals_and_rules(self) -> None:
        """Proposals and rule changes are combined for coverage."""
        proposals = [{"parameter": "scoring_bonus"}]
        rules_changed = [{"parameter": "defense_modifier"}]
        result = detect_governance_blind_spots(proposals, rules_changed)
        assert "scoring" not in result
        assert "defense" not in result
        assert "elam" in result


class TestGovernanceMockVelocityInsights:
    """Tests for velocity insights in the governance report mock."""

    def test_peak_velocity_mentioned(self) -> None:
        """When velocity is peak, the report mentions it."""
        data = {
            "proposals": [{"id": "p1"}, {"id": "p2"}, {"id": "p3"}],
            "votes": [
                {"vote": "yes"}, {"vote": "yes"}, {"vote": "no"},
                {"vote": "yes"}, {"vote": "no"}, {"vote": "yes"},
            ],
            "rules_changed": [],
            "season_proposals_by_round": {1: 1, 2: 1},
            "season_votes_by_round": {1: 2, 2: 1},
        }
        report = generate_governance_report_mock(data, "s-1", 5)
        assert "most active" in report.content.lower()

    def test_silent_velocity_mentioned(self) -> None:
        """When velocity is silent with history, the report notes the silence."""
        data = {
            "proposals": [],
            "votes": [],
            "rules_changed": [],
            "season_proposals_by_round": {1: 2, 2: 3},
            "season_votes_by_round": {1: 5, 2: 4},
        }
        report = generate_governance_report_mock(data, "s-1", 5)
        assert "quiet" in report.content.lower() or "silent" in report.content.lower()


class TestGovernanceMockBlindSpots:
    """Tests for blind spot insights in the governance report mock."""

    def test_blind_spots_surfaced(self) -> None:
        """When proposals touch only one category, blind spots are listed."""
        data = {
            "proposals": [
                {"id": "p1", "parameter": "three_point_value"},
                {"id": "p2", "parameter": "three_point_bonus"},
                {"id": "p3", "parameter": "scoring_modifier"},
                {"id": "p4", "parameter": "elam_margin"},
                {"id": "p5", "parameter": "defense_bonus"},
                {"id": "p6", "parameter": "pace_modifier"},
            ],
            "votes": [],
            "rules_changed": [],
        }
        report = generate_governance_report_mock(data, "s-1", 4)
        # Should mention untouched categories
        assert "untouched" in report.content.lower()

    def test_no_blind_spots_when_all_covered(self) -> None:
        """When all categories are targeted, no blind spots section appears."""
        proposals = [
            {"id": f"p{i}", "parameter": param}
            for i, param in enumerate([
                "scoring_value", "defense_bonus", "pace_modifier",
                "three_point_distance", "elam_margin", "foul_limit",
                "stamina_drain", "shot_clock_duration", "rebound_weight",
                "turnover_chance",
            ])
        ]
        data = {
            "proposals": proposals,
            "votes": [],
            "rules_changed": [],
        }
        report = generate_governance_report_mock(data, "s-1", 4)
        assert "untouched" not in report.content.lower()


class TestGovernanceMockIsolatedGovernor:
    """Tests for isolated governor detection in the governance report mock."""

    def test_isolated_governor_detected(self) -> None:
        """A governor who disagrees with everyone else is noted as isolated."""
        votes = []
        # Three governors vote on 4 proposals
        for pid in ["p1", "p2", "p3", "p4"]:
            votes.append({"governor_id": "gov-alice", "proposal_id": pid, "vote": "yes"})
            votes.append({"governor_id": "gov-bob", "proposal_id": pid, "vote": "yes"})
            votes.append({"governor_id": "gov-carol", "proposal_id": pid, "vote": "no"})
        data = {
            "proposals": [{"id": f"p{i}"} for i in range(1, 5)],
            "votes": votes,
            "rules_changed": [],
        }
        report = generate_governance_report_mock(data, "s-1", 6)
        # Carol disagrees with everyone (0% with alice and bob)
        assert "stands alone" in report.content

    def test_no_isolation_when_everyone_agrees(self) -> None:
        """When all governors agree, no isolation is noted."""
        votes = []
        for pid in ["p1", "p2", "p3", "p4"]:
            votes.append({"governor_id": "gov-alice", "proposal_id": pid, "vote": "yes"})
            votes.append({"governor_id": "gov-bob", "proposal_id": pid, "vote": "yes"})
            votes.append({"governor_id": "gov-carol", "proposal_id": pid, "vote": "yes"})
        data = {
            "proposals": [{"id": f"p{i}"} for i in range(1, 5)],
            "votes": votes,
            "rules_changed": [],
        }
        report = generate_governance_report_mock(data, "s-1", 6)
        assert "stands alone" not in report.content


class TestGovernanceMockHistoricalClustering:
    """Tests for historical clustering in the governance report mock."""

    def test_historical_count_in_clustering(self) -> None:
        """When proposal_history is provided, clustering shows season totals."""
        data = {
            "proposals": [
                {"id": "p1", "parameter": "three_point_value"},
                {"id": "p2", "parameter": "three_point_bonus"},
            ],
            "votes": [],
            "rules_changed": [],
            "proposal_history": [
                {"parameter": "three_point_range"},
                {"parameter": "three_point_distance"},
                {"parameter": "elam_margin"},
            ],
        }
        report = generate_governance_report_mock(data, "s-1", 4)
        # Should mention the total (2 this round + 2 from history = 4 total)
        assert "total this season" in report.content.lower() or "fixated" in report.content.lower()


# ---------------------------------------------------------------------------
# Parameter categorization tests
# ---------------------------------------------------------------------------


class TestCategorizeParameter:
    """Tests for categorize_parameter() — maps governance parameters to categories."""

    def test_offense_parameters(self) -> None:
        assert categorize_parameter("three_point_value") == "offense"
        assert categorize_parameter("two_point_value") == "offense"
        assert categorize_parameter("max_shot_share") == "offense"

    def test_defense_parameters(self) -> None:
        assert categorize_parameter("personal_foul_limit") == "defense"
        assert categorize_parameter("foul_rate_modifier") == "defense"
        assert categorize_parameter("crowd_pressure") == "defense"

    def test_pace_parameters(self) -> None:
        assert categorize_parameter("quarter_minutes") == "pace"
        assert categorize_parameter("shot_clock_seconds") == "pace"
        assert categorize_parameter("stamina_drain_rate") == "pace"

    def test_endgame_parameters(self) -> None:
        assert categorize_parameter("elam_trigger_quarter") == "endgame"
        assert categorize_parameter("elam_margin") == "endgame"

    def test_environment_parameters(self) -> None:
        assert categorize_parameter("home_court_enabled") == "environment"
        assert categorize_parameter("away_fatigue_factor") == "environment"

    def test_structure_parameters(self) -> None:
        assert categorize_parameter("teams_count") == "structure"
        assert categorize_parameter("playoff_teams") == "structure"

    def test_meta_governance_parameters(self) -> None:
        assert categorize_parameter("proposals_per_window") == "meta-governance"
        assert categorize_parameter("vote_threshold") == "meta-governance"

    def test_unknown_parameter(self) -> None:
        assert categorize_parameter("unknown_thing") == "other"
        assert categorize_parameter("completely_new") == "other"

    def test_none_parameter(self) -> None:
        assert categorize_parameter(None) == "other"

    def test_all_categories_covered(self) -> None:
        """Every entry in PARAMETER_CATEGORIES maps to a known category."""
        known = {
            "offense", "defense", "pace", "endgame",
            "environment", "structure", "meta-governance",
        }
        for param, cat in PARAMETER_CATEGORIES.items():
            assert cat in known, f"{param} mapped to unknown category {cat}"


class TestComputeCategoryDistribution:
    """Tests for compute_category_distribution() — counts proposals per category."""

    def test_basic_distribution(self) -> None:
        proposals = [
            {"parameter": "three_point_value"},
            {"parameter": "two_point_value"},
            {"parameter": "personal_foul_limit"},
        ]
        dist = compute_category_distribution(proposals)
        assert dist["offense"] == 2
        assert dist["defense"] == 1
        assert "pace" not in dist

    def test_sorted_by_count_descending(self) -> None:
        proposals = [
            {"parameter": "quarter_minutes"},
            {"parameter": "shot_clock_seconds"},
            {"parameter": "stamina_drain_rate"},
            {"parameter": "three_point_value"},
        ]
        dist = compute_category_distribution(proposals)
        keys = list(dist.keys())
        assert keys[0] == "pace"  # 3 count
        assert keys[1] == "offense"  # 1 count

    def test_empty_proposals(self) -> None:
        dist = compute_category_distribution([])
        assert dist == {}

    def test_unknown_parameters(self) -> None:
        proposals = [
            {"parameter": "made_up_thing"},
            {"parameter": None},
        ]
        dist = compute_category_distribution(proposals)
        assert dist["other"] == 2

    def test_mixed_known_and_unknown(self) -> None:
        proposals = [
            {"parameter": "three_point_value"},
            {"parameter": None},
            {"parameter": "elam_margin"},
        ]
        dist = compute_category_distribution(proposals)
        assert dist["offense"] == 1
        assert dist["endgame"] == 1
        assert dist["other"] == 1


# ---------------------------------------------------------------------------
# Enriched private report mock tests
# ---------------------------------------------------------------------------


class TestPrivateReportMockEnriched:
    """Tests for generate_private_report_mock with enriched context data."""

    def test_enriched_blind_spot_surfacing(self) -> None:
        """When enriched data has blind spots, report mentions them."""
        data = {
            "proposals_submitted": 2,
            "votes_cast": 3,
            "governor_proposal_categories": {"offense": 2},
            "league_rule_change_categories": {"defense": 3, "pace": 1},
            "blind_spots": ["defense", "pace"],
            "voting_outcomes": [],
            "alignment_rate": 0.0,
            "swing_votes": 0,
            "total_league_proposals": 5,
        }
        report = generate_private_report_mock(data, "gov-enrich-1", "s-1", 5)
        assert report.report_type == "private"
        # Should mention the governor's focus area and the blind spot
        content = report.content.lower()
        assert "offense" in content
        assert "defense" in content

    def test_enriched_voting_outcomes(self) -> None:
        """When enriched data has voting outcomes, report mentions alignment."""
        data = {
            "proposals_submitted": 1,
            "votes_cast": 3,
            "governor_proposal_categories": {"pace": 1},
            "league_rule_change_categories": {"pace": 2},
            "blind_spots": [],
            "voting_outcomes": [
                {
                    "vote": "yes", "outcome": "passed", "category": "pace",
                    "parameter": "quarter_minutes",
                    "proposal_text": "increase quarter time",
                },
                {
                    "vote": "yes", "outcome": "failed", "category": "offense",
                    "parameter": "three_point_value",
                    "proposal_text": "boost threes",
                },
                {
                    "vote": "no", "outcome": "passed", "category": "defense",
                    "parameter": "foul_rate_modifier",
                    "proposal_text": "reduce fouls",
                },
            ],
            "alignment_rate": 0.333,
            "swing_votes": 0,
            "total_league_proposals": 4,
        }
        report = generate_private_report_mock(data, "gov-votes-1", "s-1", 6)
        content = report.content.lower()
        # Should mention voting outcomes
        assert "voted yes" in content or "passed" in content
        # Should mention alignment rate
        assert "aligned" in content or "33%" in content

    def test_enriched_swing_votes(self) -> None:
        """When governor was a swing vote, report highlights it."""
        data = {
            "proposals_submitted": 0,
            "votes_cast": 2,
            "governor_proposal_categories": {},
            "league_rule_change_categories": {"offense": 1},
            "blind_spots": ["offense"],
            "voting_outcomes": [
                {
                    "vote": "yes", "outcome": "passed", "category": "offense",
                    "parameter": "three_point_value",
                    "proposal_text": "boost threes",
                },
            ],
            "alignment_rate": 1.0,
            "swing_votes": 1,
            "total_league_proposals": 3,
        }
        report = generate_private_report_mock(data, "gov-swing-1", "s-1", 7)
        content = report.content.lower()
        assert "swing vote" in content

    def test_enriched_no_blind_spots_same_focus(self) -> None:
        """When governor and league share focus, report notes alignment."""
        data = {
            "proposals_submitted": 2,
            "votes_cast": 1,
            "governor_proposal_categories": {"offense": 2},
            "league_rule_change_categories": {"offense": 3},
            "blind_spots": [],
            "voting_outcomes": [],
            "alignment_rate": 0.0,
            "swing_votes": 0,
            "total_league_proposals": 3,
        }
        report = generate_private_report_mock(data, "gov-aligned-1", "s-1", 4)
        content = report.content.lower()
        # Should note that governor is in the current
        assert "offense" in content
        assert "current" in content or "same" in content or "focused" in content

    def test_enriched_inactive_with_league_context(self) -> None:
        """Inactive governor with enriched data gets league context."""
        data = {
            "proposals_submitted": 0,
            "votes_cast": 0,
            "governor_proposal_categories": {},
            "league_rule_change_categories": {"defense": 2},
            "blind_spots": [],
            "voting_outcomes": [],
            "alignment_rate": 0.0,
            "swing_votes": 0,
            "total_league_proposals": 4,
        }
        report = generate_private_report_mock(data, "gov-quiet-1", "s-1", 3)
        content = report.content.lower()
        assert "quiet" in content
        # Should mention league activity they missed
        assert "4" in content or "proposal" in content

    def test_enriched_opposed_rule_that_passed(self) -> None:
        """When governor opposed rules that passed anyway, report mentions it."""
        data = {
            "proposals_submitted": 0,
            "votes_cast": 2,
            "governor_proposal_categories": {},
            "league_rule_change_categories": {"pace": 1},
            "blind_spots": ["pace"],
            "voting_outcomes": [
                {
                    "vote": "no", "outcome": "passed", "category": "pace",
                    "parameter": "quarter_minutes",
                    "proposal_text": "longer quarters",
                },
                {
                    "vote": "no", "outcome": "passed", "category": "offense",
                    "parameter": "three_point_value",
                    "proposal_text": "boost threes",
                },
            ],
            "alignment_rate": 0.0,
            "swing_votes": 0,
            "total_league_proposals": 3,
        }
        report = generate_private_report_mock(data, "gov-oppose-1", "s-1", 5)
        content = report.content.lower()
        assert "opposed" in content


# ---------------------------------------------------------------------------
# compute_private_report_context integration tests
# ---------------------------------------------------------------------------


@pytest.fixture
async def report_engine() -> AsyncEngine:
    eng = create_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng  # type: ignore[misc]
    await eng.dispose()


@pytest.fixture
async def report_repo(report_engine: AsyncEngine) -> Repository:
    async with get_session(report_engine) as session:
        yield Repository(session)  # type: ignore[misc]


async def _setup_governance_scenario(
    repo: Repository,
) -> tuple[str, str, str]:
    """Set up a minimal season with governance events for context computation.

    Returns (season_id, governor_id_a, governor_id_b).
    """
    league = await repo.create_league("Context Test League")
    season = await repo.create_season(league.id, "Season 1")

    gov_a = "gov-alice"
    gov_b = "gov-bob"

    # Governor A submits 2 offense proposals
    await repo.append_event(
        event_type="proposal.submitted",
        aggregate_id="prop-1",
        aggregate_type="proposal",
        season_id=season.id,
        round_number=1,
        governor_id=gov_a,
        payload={
            "id": "prop-1",
            "raw_text": "Make threes worth 4 points",
            "interpretation": {"parameter": "three_point_value", "new_value": 4},
            "tier": 1,
        },
    )
    await repo.append_event(
        event_type="proposal.submitted",
        aggregate_id="prop-2",
        aggregate_type="proposal",
        season_id=season.id,
        round_number=2,
        governor_id=gov_a,
        payload={
            "id": "prop-2",
            "raw_text": "Increase shot share cap",
            "interpretation": {"parameter": "max_shot_share", "new_value": 0.6},
            "tier": 1,
        },
    )

    # Governor B submits 1 defense proposal
    await repo.append_event(
        event_type="proposal.submitted",
        aggregate_id="prop-3",
        aggregate_type="proposal",
        season_id=season.id,
        round_number=1,
        governor_id=gov_b,
        payload={
            "id": "prop-3",
            "raw_text": "Reduce foul rate",
            "interpretation": {"parameter": "foul_rate_modifier", "new_value": 0.5},
            "tier": 2,
        },
    )

    # Votes: Alice votes yes on all, Bob votes no on prop-1
    await repo.append_event(
        event_type="vote.cast",
        aggregate_id="prop-1",
        aggregate_type="proposal",
        season_id=season.id,
        round_number=1,
        governor_id=gov_a,
        payload={"proposal_id": "prop-1", "vote": "yes", "weight": 1.0},
    )
    await repo.append_event(
        event_type="vote.cast",
        aggregate_id="prop-1",
        aggregate_type="proposal",
        season_id=season.id,
        round_number=1,
        governor_id=gov_b,
        payload={"proposal_id": "prop-1", "vote": "no", "weight": 1.0},
    )
    await repo.append_event(
        event_type="vote.cast",
        aggregate_id="prop-3",
        aggregate_type="proposal",
        season_id=season.id,
        round_number=1,
        governor_id=gov_a,
        payload={"proposal_id": "prop-3", "vote": "yes", "weight": 1.0},
    )
    await repo.append_event(
        event_type="vote.cast",
        aggregate_id="prop-3",
        aggregate_type="proposal",
        season_id=season.id,
        round_number=1,
        governor_id=gov_b,
        payload={"proposal_id": "prop-3", "vote": "yes", "weight": 1.0},
    )

    # Outcomes: prop-1 failed (tie at 50%), prop-3 passed
    await repo.append_event(
        event_type="proposal.failed",
        aggregate_id="prop-1",
        aggregate_type="proposal",
        season_id=season.id,
        round_number=1,
        payload={"proposal_id": "prop-1"},
    )
    await repo.append_event(
        event_type="proposal.passed",
        aggregate_id="prop-3",
        aggregate_type="proposal",
        season_id=season.id,
        round_number=1,
        payload={"proposal_id": "prop-3"},
    )

    # Rule enacted from prop-3
    await repo.append_event(
        event_type="rule.enacted",
        aggregate_id="prop-3",
        aggregate_type="proposal",
        season_id=season.id,
        round_number=1,
        payload={"parameter": "foul_rate_modifier", "new_value": 0.5},
    )

    return season.id, gov_a, gov_b


class TestComputePrivateReportContext:
    """Integration tests for compute_private_report_context against a real DB."""

    async def test_governor_proposal_categories(
        self, report_repo: Repository,
    ) -> None:
        """Governor's proposals are categorized correctly."""
        season_id, gov_a, _gov_b = await _setup_governance_scenario(report_repo)
        ctx = await compute_private_report_context(
            report_repo, gov_a, season_id, 2,
        )

        assert ctx["proposals_submitted"] == 2
        cats = ctx["governor_proposal_categories"]
        assert cats == {"offense": 2}

    async def test_league_proposal_categories(
        self, report_repo: Repository,
    ) -> None:
        """League-wide proposal categories include all governors."""
        season_id, gov_a, _gov_b = await _setup_governance_scenario(report_repo)
        ctx = await compute_private_report_context(
            report_repo, gov_a, season_id, 2,
        )

        league_cats = ctx["league_proposal_categories"]
        assert league_cats["offense"] == 2
        assert league_cats["defense"] == 1

    async def test_blind_spots_detected(
        self, report_repo: Repository,
    ) -> None:
        """Governor A never proposed defense, but defense rules changed."""
        season_id, gov_a, _gov_b = await _setup_governance_scenario(report_repo)
        ctx = await compute_private_report_context(
            report_repo, gov_a, season_id, 2,
        )

        assert "defense" in ctx["blind_spots"]

    async def test_no_blind_spots_when_covering_all(
        self, report_repo: Repository,
    ) -> None:
        """Governor B proposed defense and defense rules changed."""
        season_id, _gov_a, gov_b = await _setup_governance_scenario(report_repo)
        ctx = await compute_private_report_context(
            report_repo, gov_b, season_id, 2,
        )

        assert "defense" not in ctx["blind_spots"]

    async def test_voting_outcomes_tracked(
        self, report_repo: Repository,
    ) -> None:
        """Governor A's voting record is tracked with outcomes."""
        season_id, gov_a, _gov_b = await _setup_governance_scenario(report_repo)
        ctx = await compute_private_report_context(
            report_repo, gov_a, season_id, 2,
        )

        outcomes = ctx["voting_outcomes"]
        assert len(outcomes) == 2
        # Alice voted yes on prop-1 (failed) and yes on prop-3 (passed)
        prop1_vo = [
            vo for vo in outcomes
            if vo["proposal_text"].startswith("Make threes")
        ]
        prop3_vo = [
            vo for vo in outcomes
            if vo["proposal_text"].startswith("Reduce foul")
        ]
        assert len(prop1_vo) == 1
        assert prop1_vo[0]["vote"] == "yes"
        assert prop1_vo[0]["outcome"] == "failed"
        assert len(prop3_vo) == 1
        assert prop3_vo[0]["vote"] == "yes"
        assert prop3_vo[0]["outcome"] == "passed"

    async def test_alignment_rate_computed(
        self, report_repo: Repository,
    ) -> None:
        """Alignment rate reflects proportion of votes matching outcomes."""
        season_id, gov_a, _gov_b = await _setup_governance_scenario(report_repo)
        ctx = await compute_private_report_context(
            report_repo, gov_a, season_id, 2,
        )

        # Alice: yes on prop-1 (failed) = wrong, yes on prop-3 (passed) = right
        assert ctx["alignment_rate"] == 0.5

    async def test_swing_vote_detection(
        self, report_repo: Repository,
    ) -> None:
        """Swing votes detected when removing governor's vote flips outcome."""
        season_id, _gov_a, gov_b = await _setup_governance_scenario(report_repo)
        ctx = await compute_private_report_context(
            report_repo, gov_b, season_id, 2,
        )

        # Bob voted no on prop-1 (failed). Without Bob: only Alice yes = passed.
        # So Bob was swing vote on prop-1.
        # Bob voted yes on prop-3 (passed). Without Bob: Alice yes = passed.
        # Not a swing vote on prop-3.
        assert ctx["swing_votes"] == 1

    async def test_empty_governor_no_crash(
        self, report_repo: Repository,
    ) -> None:
        """Governor with no activity returns valid context."""
        season_id, _gov_a, _gov_b = await _setup_governance_scenario(
            report_repo,
        )
        ctx = await compute_private_report_context(
            report_repo, "gov-ghost", season_id, 2,
        )

        assert ctx["proposals_submitted"] == 0
        assert ctx["votes_cast"] == 0
        # Defense changed but ghost didn't propose
        assert "defense" in ctx["blind_spots"]
        assert ctx["alignment_rate"] == 0.0
        assert ctx["swing_votes"] == 0


# ---------------------------------------------------------------------------
# Private reports API endpoint — auth tests
# ---------------------------------------------------------------------------


def _sign_session(secret: str, data: dict) -> str:
    """Create a signed session cookie value for testing."""
    serializer = URLSafeTimedSerializer(secret, salt="pinwheel-session")
    result: str = serializer.dumps(data)
    return result


def _prod_settings(**overrides: str) -> Settings:
    """Build production-mode settings for auth testing."""
    defaults: dict[str, str] = {
        "database_url": "sqlite+aiosqlite:///:memory:",
        "pinwheel_env": "production",
        "session_secret_key": "test-secret-for-private-reports",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _dev_settings(**overrides: str) -> Settings:
    """Build development-mode settings for auth testing."""
    defaults: dict[str, str] = {
        "database_url": "sqlite+aiosqlite:///:memory:",
        "pinwheel_env": "development",
        "session_secret_key": "test-secret-for-private-reports",
    }
    defaults.update(overrides)
    return Settings(**defaults)


async def _make_client_and_engine(
    settings: Settings,
) -> tuple[AsyncClient, object]:
    """Create test app, engine, and httpx client. Caller must dispose engine."""
    app = create_app(settings)

    engine = create_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    app.state.event_bus = EventBus()

    from pinwheel.core.presenter import PresentationState

    app.state.presentation_state = PresentationState()

    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    return client, engine


async def _create_player_and_report(
    engine: object,
    discord_id: str = "111222333",
    username: str = "testgov",
) -> tuple[str, str, str]:
    """Create a player and a private report, return (player_id, season_id, report_id)."""
    async with get_session(engine) as session:  # type: ignore[arg-type]
        repo = Repository(session)
        player = await repo.get_or_create_player(
            discord_id=discord_id,
            username=username,
        )
        league = await repo.create_league("Auth Test League")
        season = await repo.create_season(league.id, "Season 1")
        report = await repo.store_report(
            season_id=season.id,
            report_type="private",
            round_number=1,
            content="Your private reflection for round 1.",
            governor_id=player.id,
        )
        return player.id, season.id, report.id


class TestPrivateReportsEndpointAuth:
    """Tests for session auth on GET /api/reports/private/{season_id}/{governor_id}."""

    async def test_unauthenticated_denied_in_production(self) -> None:
        """Without a session cookie in production, the endpoint returns 401."""
        settings = _prod_settings()
        client, engine = await _make_client_and_engine(settings)
        try:
            player_id, season_id, _ = await _create_player_and_report(engine)

            resp = await client.get(f"/api/reports/private/{season_id}/{player_id}")

            assert resp.status_code == 401
            assert "Authentication required" in resp.json()["detail"]
        finally:
            await client.aclose()
            await engine.dispose()  # type: ignore[union-attr]

    async def test_authorized_governor_can_see_own_reports(self) -> None:
        """An authenticated governor can view their own private reports."""
        settings = _prod_settings()
        client, engine = await _make_client_and_engine(settings)
        try:
            player_id, season_id, _ = await _create_player_and_report(
                engine, discord_id="999888777", username="mygov",
            )

            cookie_value = _sign_session(
                settings.session_secret_key,
                {
                    "discord_id": "999888777",
                    "username": "mygov",
                    "avatar_url": "",
                },
            )
            client.cookies.set(SESSION_COOKIE_NAME, cookie_value)

            resp = await client.get(f"/api/reports/private/{season_id}/{player_id}")

            assert resp.status_code == 200
            data = resp.json()["data"]
            assert len(data) == 1
            assert data[0]["governor_id"] == player_id
            assert "private reflection" in data[0]["content"]
        finally:
            await client.aclose()
            await engine.dispose()  # type: ignore[union-attr]

    async def test_governor_cannot_see_another_governors_reports(self) -> None:
        """A governor requesting another governor's reports gets 403."""
        settings = _prod_settings()
        client, engine = await _make_client_and_engine(settings)
        try:
            # Create the target governor (whose reports exist)
            target_player_id, season_id, _ = await _create_player_and_report(
                engine, discord_id="111000111", username="targetgov",
            )

            # Create the requesting governor (different person)
            async with get_session(engine) as session:  # type: ignore[arg-type]
                repo = Repository(session)
                await repo.get_or_create_player(
                    discord_id="222000222", username="snoopygov",
                )

            cookie_value = _sign_session(
                settings.session_secret_key,
                {
                    "discord_id": "222000222",
                    "username": "snoopygov",
                    "avatar_url": "",
                },
            )
            client.cookies.set(SESSION_COOKIE_NAME, cookie_value)

            resp = await client.get(
                f"/api/reports/private/{season_id}/{target_player_id}",
            )

            assert resp.status_code == 403
            assert "your own" in resp.json()["detail"].lower()
        finally:
            await client.aclose()
            await engine.dispose()  # type: ignore[union-attr]

    async def test_dev_mode_allows_unauthenticated_access(self) -> None:
        """In development mode, the endpoint works without auth."""
        settings = _dev_settings()
        client, engine = await _make_client_and_engine(settings)
        try:
            player_id, season_id, _ = await _create_player_and_report(engine)

            # No session cookie set — should still work in dev mode
            resp = await client.get(f"/api/reports/private/{season_id}/{player_id}")

            assert resp.status_code == 200
            data = resp.json()["data"]
            assert len(data) == 1
            assert data[0]["governor_id"] == player_id
        finally:
            await client.aclose()
            await engine.dispose()  # type: ignore[union-attr]

    async def test_unknown_discord_id_gets_403(self) -> None:
        """Auth'd user whose discord_id has no PlayerRow gets 403."""
        settings = _prod_settings()
        client, engine = await _make_client_and_engine(settings)
        try:
            player_id, season_id, _ = await _create_player_and_report(engine)

            # Cookie for a discord_id that has no player record
            cookie_value = _sign_session(
                settings.session_secret_key,
                {
                    "discord_id": "000000000",
                    "username": "ghost",
                    "avatar_url": "",
                },
            )
            client.cookies.set(SESSION_COOKIE_NAME, cookie_value)

            resp = await client.get(f"/api/reports/private/{season_id}/{player_id}")

            assert resp.status_code == 403
        finally:
            await client.aclose()
            await engine.dispose()  # type: ignore[union-attr]

    async def test_empty_reports_for_authorized_governor(self) -> None:
        """An authorized governor with no reports gets an empty list, not an error."""
        settings = _prod_settings()
        client, engine = await _make_client_and_engine(settings)
        try:
            # Create a player but no reports
            async with get_session(engine) as session:  # type: ignore[arg-type]
                repo = Repository(session)
                player = await repo.get_or_create_player(
                    discord_id="444555666", username="emptygov",
                )
                league = await repo.create_league("Empty League")
                season = await repo.create_season(league.id, "Season E")
                player_id = player.id
                season_id = season.id

            cookie_value = _sign_session(
                settings.session_secret_key,
                {
                    "discord_id": "444555666",
                    "username": "emptygov",
                    "avatar_url": "",
                },
            )
            client.cookies.set(SESSION_COOKIE_NAME, cookie_value)

            resp = await client.get(f"/api/reports/private/{season_id}/{player_id}")

            assert resp.status_code == 200
            assert resp.json()["data"] == []
        finally:
            await client.aclose()
            await engine.dispose()  # type: ignore[union-attr]
