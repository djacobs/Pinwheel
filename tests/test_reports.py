"""Tests for report generation (mock) and report models."""

from pinwheel.ai.report import (
    generate_governance_report_mock,
    generate_private_report_mock,
    generate_simulation_report_mock,
)
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
