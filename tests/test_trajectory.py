"""Tests for the performance trajectory analytics module.

Covers:
- compute_streaks: win/loss streak detection
- compute_trend_description: first-half vs second-half analysis
- compute_win_rate_timeline: rolling win rate with rule change markers
- compute_rule_regimes: season segmentation by rule changes
- compute_governor_impacts: before/after deltas for governor proposals
- compute_team_trends: home/away, streaks, rule-change trends
- build_performance_trajectory: full integration
- GovernorProposalImpact: impact_label property
- RuleRegime: win_pct property
"""

import pytest

from pinwheel.core.trajectory import (
    GovernorProposalImpact,
    PerformanceTrajectory,
    RuleRegime,
    build_performance_trajectory,
    compute_governor_impacts,
    compute_rule_regimes,
    compute_streaks,
    compute_team_trends,
    compute_trend_description,
    compute_win_rate_timeline,
)


def _make_game(
    round_number: int,
    won: bool,
    is_home: bool = True,
    margin: int = 5,
) -> dict[str, object]:
    """Build a minimal game result dict."""
    return {
        "round_number": round_number,
        "won": won,
        "is_home": is_home,
        "margin": margin if won else -margin,
        "opponent_team_id": "opp-1",
        "opponent_team_name": "Opponent",
        "team_score": 55 + margin if won else 55 - margin,
        "opponent_score": 55 - margin if won else 55 + margin,
    }


# ============================================================================
# compute_streaks Tests
# ============================================================================


class TestComputeStreaks:
    def test_all_wins(self) -> None:
        games = [_make_game(i, True) for i in range(1, 6)]
        longest_win, longest_loss, streak_type, streak_count = compute_streaks(games)
        assert longest_win == 5
        assert longest_loss == 0
        assert streak_type == "W"
        assert streak_count == 5

    def test_all_losses(self) -> None:
        games = [_make_game(i, False) for i in range(1, 4)]
        longest_win, longest_loss, streak_type, streak_count = compute_streaks(games)
        assert longest_win == 0
        assert longest_loss == 3
        assert streak_type == "L"
        assert streak_count == 3

    def test_mixed_results(self) -> None:
        """WLLWWWL -> longest_win=3, longest_loss=2, current=L/1."""
        results = [True, False, False, True, True, True, False]
        games = [_make_game(i + 1, w) for i, w in enumerate(results)]
        longest_win, longest_loss, streak_type, streak_count = compute_streaks(games)
        assert longest_win == 3
        assert longest_loss == 2
        assert streak_type == "L"
        assert streak_count == 1

    def test_empty_games(self) -> None:
        longest_win, longest_loss, streak_type, streak_count = compute_streaks([])
        assert longest_win == 0
        assert longest_loss == 0
        assert streak_type == ""
        assert streak_count == 0

    def test_single_game_win(self) -> None:
        games = [_make_game(1, True)]
        longest_win, longest_loss, streak_type, streak_count = compute_streaks(games)
        assert longest_win == 1
        assert longest_loss == 0
        assert streak_type == "W"
        assert streak_count == 1


# ============================================================================
# compute_trend_description Tests
# ============================================================================


class TestComputeTrendDescription:
    def test_strong_finish(self) -> None:
        """First half mostly losses, second half mostly wins."""
        games = (
            [_make_game(i, False) for i in range(1, 5)]
            + [_make_game(i, True) for i in range(5, 9)]
        )
        desc = compute_trend_description(games)
        assert "Strong finish" in desc

    def test_cooled_off(self) -> None:
        """First half mostly wins, second half mostly losses."""
        games = (
            [_make_game(i, True) for i in range(1, 5)]
            + [_make_game(i, False) for i in range(5, 9)]
        )
        desc = compute_trend_description(games)
        assert "cooled" in desc

    def test_consistent(self) -> None:
        """Mixed results throughout."""
        games = [_make_game(i, i % 2 == 0) for i in range(1, 9)]
        desc = compute_trend_description(games)
        assert "Consistent" in desc

    def test_too_few_games(self) -> None:
        """Less than 4 games should return empty."""
        games = [_make_game(1, True), _make_game(2, False)]
        assert compute_trend_description(games) == ""

    def test_empty_games(self) -> None:
        assert compute_trend_description([]) == ""


# ============================================================================
# compute_win_rate_timeline Tests
# ============================================================================


class TestComputeWinRateTimeline:
    def test_basic_timeline(self) -> None:
        games = [
            _make_game(1, True),
            _make_game(2, False),
            _make_game(3, True),
        ]
        timeline = compute_win_rate_timeline(games, [])
        assert len(timeline) == 3
        # After game 1: 1-0 = 1.0
        assert timeline[0]["win_rate"] == 1.0
        assert timeline[0]["cumulative_wins"] == 1
        # After game 2: 1-1 = 0.5
        assert timeline[1]["win_rate"] == 0.5
        # After game 3: 2-1 = 0.667
        assert timeline[2]["win_rate"] == pytest.approx(0.667, abs=0.001)

    def test_rule_change_markers(self) -> None:
        games = [_make_game(i, True) for i in range(1, 5)]
        timeline = compute_win_rate_timeline(games, [2])
        markers = [pt["marker"] for pt in timeline]
        assert markers[0] == ""
        assert markers[1] == "rule_change"
        assert markers[2] == ""

    def test_empty_games(self) -> None:
        assert compute_win_rate_timeline([], []) == []


# ============================================================================
# compute_rule_regimes Tests
# ============================================================================


class TestComputeRuleRegimes:
    def test_no_rule_changes(self) -> None:
        games = [_make_game(i, True) for i in range(1, 4)]
        regimes = compute_rule_regimes(games, [])
        assert len(regimes) == 1
        assert regimes[0].label == "Default rules"
        assert regimes[0].wins == 3
        assert regimes[0].losses == 0

    def test_single_rule_change(self) -> None:
        games = [
            _make_game(1, True),
            _make_game(2, True),
            _make_game(3, False),
            _make_game(4, False),
        ]
        rule_events = [
            {"round_enacted": 3, "parameter": "three_point_value",
             "old_value": "3", "new_value": "5"},
        ]
        regimes = compute_rule_regimes(games, rule_events)
        assert len(regimes) == 2
        # Before rule change: rounds 1-2 = 2-0
        assert regimes[0].wins == 2
        assert regimes[0].losses == 0
        # After rule change: rounds 3-4 = 0-2
        assert regimes[1].wins == 0
        assert regimes[1].losses == 2
        assert "three_point_value" in regimes[1].label

    def test_multiple_rule_changes(self) -> None:
        games = [_make_game(i, i <= 3) for i in range(1, 7)]
        rule_events = [
            {"round_enacted": 3, "parameter": "shot_clock",
             "old_value": "24", "new_value": "14"},
            {"round_enacted": 5, "parameter": "three_point_value",
             "old_value": "3", "new_value": "4"},
        ]
        regimes = compute_rule_regimes(games, rule_events)
        assert len(regimes) == 3

    def test_empty_games(self) -> None:
        assert compute_rule_regimes([], []) == []


# ============================================================================
# compute_governor_impacts Tests
# ============================================================================


class TestComputeGovernorImpacts:
    def test_single_proposal(self) -> None:
        games = [
            _make_game(1, True),
            _make_game(2, True),
            _make_game(3, False),
            _make_game(4, True),
        ]
        proposals = [{
            "governor_name": "TestGov",
            "raw_text": "Make threes worth 5",
            "enacted_round": 3,
            "parameter": "three_point_value",
        }]
        impacts = compute_governor_impacts(games, proposals)
        assert len(impacts) == 1
        assert impacts[0].governor_name == "TestGov"
        assert impacts[0].before_wins == 2
        assert impacts[0].before_losses == 0
        assert impacts[0].after_wins == 1
        assert impacts[0].after_losses == 1

    def test_no_proposals(self) -> None:
        games = [_make_game(1, True)]
        assert compute_governor_impacts(games, []) == []

    def test_zero_enacted_round_skipped(self) -> None:
        games = [_make_game(1, True)]
        proposals = [{
            "governor_name": "Gov",
            "raw_text": "text",
            "enacted_round": 0,
            "parameter": "",
        }]
        assert compute_governor_impacts(games, proposals) == []


# ============================================================================
# compute_team_trends Tests
# ============================================================================


class TestComputeTeamTrends:
    def test_win_streak_trend(self) -> None:
        trends = compute_team_trends([], "W", 5, [])
        labels = [t.label for t in trends]
        assert any("5-game win streak" in label for label in labels)

    def test_loss_streak_trend(self) -> None:
        trends = compute_team_trends([], "L", 4, [])
        labels = [t.label for t in trends]
        assert any("4-game loss streak" in label for label in labels)

    def test_no_streak_below_threshold(self) -> None:
        trends = compute_team_trends([], "W", 2, [])
        assert not any(t.trend_type == "streak" for t in trends)

    def test_home_away_disparity(self) -> None:
        """Strong home record, weak away record should show a trend."""
        games = (
            [_make_game(i, True, is_home=True) for i in range(1, 5)]
            + [_make_game(i, False, is_home=False) for i in range(5, 9)]
        )
        trends = compute_team_trends(games, "", 0, [])
        home_away = [t for t in trends if t.trend_type == "home_away"]
        assert len(home_away) == 1
        assert "at home" in home_away[0].label

    def test_better_on_road(self) -> None:
        """Better away than home."""
        games = (
            [_make_game(i, False, is_home=True) for i in range(1, 5)]
            + [_make_game(i, True, is_home=False) for i in range(5, 9)]
        )
        trends = compute_team_trends(games, "", 0, [])
        home_away = [t for t in trends if t.trend_type == "home_away"]
        assert len(home_away) == 1
        assert "road" in home_away[0].label

    def test_no_home_away_when_balanced(self) -> None:
        """50/50 at home and away should not produce a trend."""
        games = (
            [_make_game(1, True, is_home=True), _make_game(2, False, is_home=True)]
            + [_make_game(3, True, is_home=False), _make_game(4, False, is_home=False)]
        )
        trends = compute_team_trends(games, "", 0, [])
        assert not any(t.trend_type == "home_away" for t in trends)

    def test_since_last_rule_change(self) -> None:
        games = [_make_game(i, True) for i in range(1, 6)]
        trends = compute_team_trends(games, "", 0, [3])
        recent = [t for t in trends if t.trend_type == "recent"]
        assert len(recent) == 1
        assert "3-0" in recent[0].label


# ============================================================================
# GovernorProposalImpact properties
# ============================================================================


class TestGovernorProposalImpactProperties:
    def test_impact_label_helped(self) -> None:
        impact = GovernorProposalImpact(
            governor_name="Gov",
            proposal_text="test",
            enacted_round=3,
            before_wins=1,
            before_losses=3,  # 25%
            after_wins=3,
            after_losses=1,  # 75%
        )
        assert impact.impact_label == "Helped"
        assert impact.delta > 0

    def test_impact_label_hurt(self) -> None:
        impact = GovernorProposalImpact(
            governor_name="Gov",
            proposal_text="test",
            enacted_round=3,
            before_wins=3,
            before_losses=1,  # 75%
            after_wins=1,
            after_losses=3,  # 25%
        )
        assert impact.impact_label == "Hurt"
        assert impact.delta < 0

    def test_impact_label_neutral(self) -> None:
        impact = GovernorProposalImpact(
            governor_name="Gov",
            proposal_text="test",
            enacted_round=3,
            before_wins=2,
            before_losses=2,
            after_wins=2,
            after_losses=2,
        )
        assert impact.impact_label == "Neutral"

    def test_impact_label_too_early(self) -> None:
        impact = GovernorProposalImpact(
            governor_name="Gov",
            proposal_text="test",
            enacted_round=3,
            before_wins=2,
            before_losses=2,
            after_wins=1,
            after_losses=0,
        )
        assert impact.impact_label == "Too early to tell"

    def test_before_pct_empty(self) -> None:
        impact = GovernorProposalImpact(
            governor_name="Gov", proposal_text="t", enacted_round=1,
        )
        assert impact.before_pct == 0.0
        assert impact.after_pct == 0.0


# ============================================================================
# RuleRegime properties
# ============================================================================


class TestRuleRegimeProperties:
    def test_win_pct(self) -> None:
        regime = RuleRegime(
            label="test", start_round=1, end_round=5,
            wins=3, losses=2,
        )
        assert regime.win_pct == pytest.approx(0.6)
        assert regime.games_played == 5

    def test_win_pct_zero_games(self) -> None:
        regime = RuleRegime(label="test", start_round=1, end_round=1)
        assert regime.win_pct == 0.0


# ============================================================================
# build_performance_trajectory Integration
# ============================================================================


class TestBuildPerformanceTrajectory:
    def test_empty_games(self) -> None:
        result = build_performance_trajectory([], [], [], [])
        assert isinstance(result, PerformanceTrajectory)
        assert result.recent_form == ""
        assert result.rule_regimes == []
        assert result.governor_impacts == []

    def test_full_build(self) -> None:
        """Build trajectory from 6 games with a rule change at round 4."""
        games = [
            _make_game(1, True, is_home=True),
            _make_game(2, False, is_home=False),
            _make_game(3, True, is_home=True),
            _make_game(4, False, is_home=False),
            _make_game(5, True, is_home=True),
            _make_game(6, True, is_home=False),
        ]
        rule_events = [
            {"round_enacted": 4, "parameter": "three_point_value",
             "old_value": "3", "new_value": "5"},
        ]
        proposals = [{
            "governor_name": "TestGov",
            "raw_text": "Make threes worth 5 points",
            "enacted_round": 4,
            "parameter": "three_point_value",
        }]

        result = build_performance_trajectory(
            games, rule_events, proposals, [4]
        )

        # Recent form (last 5)
        assert len(result.recent_form) == 5
        assert result.recent_form[-2:] == "WW"

        # Win rate timeline has 6 points
        assert len(result.win_rate_timeline) == 6
        # Round 4 should be marked
        r4_markers = [
            pt for pt in result.win_rate_timeline if pt["marker"] == "rule_change"
        ]
        assert len(r4_markers) == 1

        # Rule regimes: 2 (before and after change)
        assert len(result.rule_regimes) == 2

        # Governor impact
        assert len(result.governor_impacts) == 1
        assert result.governor_impacts[0].governor_name == "TestGov"
        assert result.governor_impacts[0].enacted_round == 4

        # Trends should exist
        assert isinstance(result.trends, list)

    def test_build_with_no_rule_changes(self) -> None:
        """Build trajectory for a season with no rule changes."""
        games = [_make_game(i, i % 2 == 0) for i in range(1, 8)]
        result = build_performance_trajectory(games, [], [], [])

        assert len(result.recent_form) == 5
        assert len(result.win_rate_timeline) == 7
        assert len(result.rule_regimes) == 1
        assert result.rule_regimes[0].label == "Default rules"
        assert result.governor_impacts == []
