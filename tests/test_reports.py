"""Tests for report generation (mock) and report models."""

from pinwheel.ai.report import (
    _compute_rule_correlations,
    _compute_rule_correlations_with_history,
    build_system_context,
    compute_governance_velocity,
    compute_pairwise_alignment,
    compute_proposal_parameter_clustering,
    detect_governance_blind_spots,
    generate_governance_report_mock,
    generate_private_report_mock,
    generate_simulation_report_mock,
)
from pinwheel.core.narrative import NarrativeContext
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
