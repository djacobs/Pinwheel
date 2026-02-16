"""Tests for narrative standings — strength of schedule, magic numbers,
trajectory, most improved, and enhanced callouts."""

from __future__ import annotations

from pinwheel.core.narrative_standings import (
    compute_magic_numbers,
    compute_most_improved,
    compute_narrative_callouts,
    compute_standings_trajectory,
    compute_strength_of_schedule,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(
    home: str,
    away: str,
    home_score: int,
    away_score: int,
    round_number: int = 1,
) -> dict:
    """Build a game result dict."""
    winner = home if home_score > away_score else away
    return {
        "home_team_id": home,
        "away_team_id": away,
        "home_score": home_score,
        "away_score": away_score,
        "winner_team_id": winner,
        "round_number": round_number,
    }


def _standings(teams: list[tuple[str, int, int]]) -> list[dict]:
    """Build a sorted standings list from (team_id, wins, losses) tuples."""
    rows = []
    for tid, wins, losses in teams:
        rows.append({
            "team_id": tid,
            "team_name": f"Team_{tid}",
            "wins": wins,
            "losses": losses,
            "points_for": wins * 25 + losses * 20,
            "points_against": losses * 25 + wins * 20,
            "point_diff": (wins - losses) * 5,
        })
    return sorted(rows, key=lambda t: (-t["wins"], -t["point_diff"]))


# ---------------------------------------------------------------------------
# Strength of Schedule
# ---------------------------------------------------------------------------


class TestStrengthOfSchedule:
    """Tests for compute_strength_of_schedule."""

    def test_basic_sos(self) -> None:
        """Team A beats above-.500 team B, loses to below-.500 team C."""
        standings = _standings([("A", 5, 1), ("B", 4, 2), ("C", 1, 5)])
        results = [
            _make_result("A", "B", 30, 25),  # A beats B (above .500)
            _make_result("A", "C", 30, 25),  # A beats C (below .500)
            _make_result("B", "C", 30, 25),  # B beats C
            _make_result("C", "A", 28, 25),  # C beats A (A is above .500)
            _make_result("B", "A", 30, 28),  # B beats A (A is above .500)
            _make_result("C", "B", 22, 30),  # B beats C
        ]
        sos = compute_strength_of_schedule(results, standings)

        # A played B twice (once as home winner, once as away loser)
        # A vs above-.500 (B): 1 win, 1 loss
        assert sos["A"]["wins"] == 1
        assert sos["A"]["losses"] == 1

    def test_no_games_against_good_teams(self) -> None:
        """Team with no games against above-.500 opponents has 0-0 SOS."""
        standings = _standings([("A", 5, 0), ("B", 0, 5)])
        results = [
            _make_result("A", "B", 30, 20, round_number=1),
        ]
        sos = compute_strength_of_schedule(results, standings)

        # B never plays a team above .500 (A is above .500, B played A)
        # Actually B played A (above .500), so B has losses against above .500
        assert sos["B"]["losses"] == 1
        assert sos["B"]["wins"] == 0

        # A played B (below .500), so no games vs good teams
        assert sos["A"]["wins"] == 0
        assert sos["A"]["losses"] == 0

    def test_empty_results(self) -> None:
        """Empty results should return empty SOS for all teams."""
        standings = _standings([("A", 0, 0), ("B", 0, 0)])
        sos = compute_strength_of_schedule([], standings)
        assert sos["A"] == {"wins": 0, "losses": 0}
        assert sos["B"] == {"wins": 0, "losses": 0}

    def test_all_teams_above_500(self) -> None:
        """When all teams are above .500, all games count for SOS."""
        standings = _standings([("A", 3, 1), ("B", 2, 1)])
        results = [
            _make_result("A", "B", 30, 25, round_number=1),
        ]
        sos = compute_strength_of_schedule(results, standings)
        # A beats B (above .500): A gets 1 win vs good teams
        assert sos["A"]["wins"] == 1
        # B loses to A (above .500): B gets 1 loss vs good teams
        assert sos["B"]["losses"] == 1


# ---------------------------------------------------------------------------
# Magic Numbers
# ---------------------------------------------------------------------------


class TestMagicNumbers:
    """Tests for compute_magic_numbers."""

    def test_clinched(self) -> None:
        """Team with insurmountable lead has magic number 0."""
        standings = _standings([("A", 8, 0), ("B", 5, 3), ("C", 2, 6), ("D", 1, 7)])
        magic = compute_magic_numbers(
            standings, total_rounds=9, games_per_round=1, num_playoff_spots=2,
        )
        # A: 8 wins. C (first out) has 2 wins, 3 remaining (9-6=3), max = 5.
        # Magic for A = 5 + 1 - 8 = -2 → clinched (0)
        assert magic["A"] == 0

    def test_not_yet_clinched(self) -> None:
        """Team in playoff spot but hasn't clinched yet."""
        standings = _standings([("A", 3, 1), ("B", 2, 2), ("C", 2, 2), ("D", 1, 3)])
        magic = compute_magic_numbers(
            standings, total_rounds=9, games_per_round=1, num_playoff_spots=2,
        )
        # C is the first team out (3rd). C: 2 wins, 5 remaining → max 7.
        # Magic for A = 7 + 1 - 3 = 5
        assert magic["A"] is not None
        assert magic["A"] > 0

    def test_everyone_makes_playoffs(self) -> None:
        """When all teams make playoffs, everyone has magic number 0."""
        standings = _standings([("A", 3, 0), ("B", 2, 1)])
        magic = compute_magic_numbers(
            standings, total_rounds=6, games_per_round=1, num_playoff_spots=2,
        )
        assert magic["A"] == 0
        assert magic["B"] == 0

    def test_eliminated(self) -> None:
        """Team that can't catch the last playoff spot is eliminated."""
        standings = _standings([("A", 8, 0), ("B", 7, 1), ("C", 0, 8), ("D", 0, 8)])
        magic = compute_magic_numbers(
            standings, total_rounds=9, games_per_round=1, num_playoff_spots=2,
        )
        # C has 0 wins, 1 remaining. Max possible = 1. B has 7 wins.
        # C needs 7 + 1 = 8 wins to overtake, but can only get 1. Eliminated.
        assert magic["C"] is None
        assert magic["D"] is None

    def test_magic_number_close_race(self) -> None:
        """Team close to clinching shows small magic number."""
        standings = _standings([("A", 6, 1), ("B", 5, 2), ("C", 3, 4), ("D", 1, 6)])
        magic = compute_magic_numbers(
            standings, total_rounds=9, games_per_round=1, num_playoff_spots=2,
        )
        # C is first out: 3 wins, 2 remaining → max 5.
        # A magic = 5 + 1 - 6 = 0 → clinched!
        assert magic["A"] == 0


# ---------------------------------------------------------------------------
# Trajectory
# ---------------------------------------------------------------------------


class TestStandingsTrajectory:
    """Tests for compute_standings_trajectory."""

    def test_movement_detected(self) -> None:
        """Team that improved should show positive trajectory."""
        # Rounds 1-3: B leads, A is behind
        # Rounds 4-6: A catches up and overtakes
        results = [
            _make_result("B", "A", 30, 20, round_number=1),
            _make_result("B", "C", 30, 20, round_number=2),
            _make_result("A", "C", 30, 20, round_number=3),
            # A goes on a run
            _make_result("A", "B", 30, 20, round_number=4),
            _make_result("A", "C", 30, 20, round_number=5),
            _make_result("A", "B", 30, 20, round_number=6),
        ]
        traj = compute_standings_trajectory(results, current_round=6, lookback=3)
        # A should have moved up
        assert traj["A"] > 0

    def test_no_movement_early_season(self) -> None:
        """No trajectory data when season is too young for lookback."""
        results = [
            _make_result("A", "B", 30, 20, round_number=1),
        ]
        traj = compute_standings_trajectory(results, current_round=1, lookback=3)
        assert traj == {}

    def test_no_movement_when_stable(self) -> None:
        """All zeros when standings haven't changed."""
        # Same results in both halves: A always wins
        results = [
            _make_result("A", "B", 30, 20, round_number=1),
            _make_result("A", "C", 30, 20, round_number=2),
            _make_result("B", "C", 30, 20, round_number=3),
            _make_result("A", "B", 30, 20, round_number=4),
            _make_result("A", "C", 30, 20, round_number=5),
            _make_result("B", "C", 30, 20, round_number=6),
        ]
        traj = compute_standings_trajectory(results, current_round=6, lookback=3)
        # A always first, B always second, C always third → all zeros
        assert traj.get("A", 0) == 0
        assert traj.get("B", 0) == 0
        assert traj.get("C", 0) == 0


# ---------------------------------------------------------------------------
# Most Improved
# ---------------------------------------------------------------------------


class TestMostImproved:
    """Tests for compute_most_improved."""

    def test_improvement_detected(self) -> None:
        """Team that went from losing to winning is most improved."""
        results = [
            # Early: A loses, B wins
            _make_result("B", "A", 30, 20, round_number=1),
            _make_result("B", "C", 30, 20, round_number=2),
            _make_result("C", "A", 30, 20, round_number=3),
            # Recent: A wins everything
            _make_result("A", "B", 30, 20, round_number=4),
            _make_result("A", "C", 30, 20, round_number=5),
            _make_result("A", "B", 30, 20, round_number=6),
        ]
        team_id, old_pct, new_pct = compute_most_improved(results, current_round=6, window=3)
        assert team_id == "A"
        assert new_pct > old_pct

    def test_no_improvement_early_season(self) -> None:
        """No improvement data when season is too young."""
        results = [_make_result("A", "B", 30, 20, round_number=1)]
        team_id, old_pct, new_pct = compute_most_improved(results, current_round=1, window=3)
        assert team_id is None

    def test_no_improvement_when_all_steady(self) -> None:
        """No improvement when all teams stay the same."""
        results = [
            _make_result("A", "B", 30, 20, round_number=1),
            _make_result("A", "C", 30, 20, round_number=2),
            _make_result("B", "C", 30, 20, round_number=3),
            _make_result("A", "B", 30, 20, round_number=4),
            _make_result("A", "C", 30, 20, round_number=5),
            _make_result("B", "C", 30, 20, round_number=6),
        ]
        team_id, old_pct, new_pct = compute_most_improved(results, current_round=6, window=3)
        # Everyone's rate is consistent, so improvement is 0 or minimal
        # A is 1.0 in both, B is 0.5 in both, C is 0.0 in both
        # No improvement (all 0.0 delta or less)
        assert team_id is None or old_pct == new_pct


# ---------------------------------------------------------------------------
# Narrative Callouts
# ---------------------------------------------------------------------------


class TestNarrativeCallouts:
    """Tests for compute_narrative_callouts."""

    def _names(self) -> dict[str, str]:
        return {"A": "Thorns", "B": "Breakers", "C": "Storm", "D": "Hammers"}

    def test_tightest_race_with_remaining(self) -> None:
        """Tied teams should show remaining rounds context."""
        standings = _standings([("A", 5, 2), ("B", 5, 2), ("C", 3, 4), ("D", 1, 6)])
        callouts = compute_narrative_callouts(
            standings=standings,
            streaks={},
            current_round=7,
            total_rounds=9,
            sos={},
            magic_numbers={},
            trajectory={},
            most_improved_team=None,
            team_names=self._names(),
        )
        # Should mention tied teams and remaining rounds
        assert any("Thorns" in c and "Breakers" in c and "2 rounds left" in c for c in callouts)

    def test_separated_by_one_game(self) -> None:
        """Teams separated by 1 game should show that context."""
        standings = _standings([("A", 6, 2), ("B", 5, 3), ("C", 3, 5), ("D", 1, 7)])
        callouts = compute_narrative_callouts(
            standings=standings,
            streaks={},
            current_round=8,
            total_rounds=9,
            sos={},
            magic_numbers={},
            trajectory={},
            most_improved_team=None,
            team_names=self._names(),
        )
        assert any("separated by 1 game" in c for c in callouts)

    def test_dominant_team(self) -> None:
        """Team with 3+ game lead triggers dominant callout."""
        standings = _standings([("A", 9, 0), ("B", 5, 4), ("C", 3, 6), ("D", 1, 8)])
        callouts = compute_narrative_callouts(
            standings=standings,
            streaks={},
            current_round=9,
            total_rounds=12,
            sos={},
            magic_numbers={},
            trajectory={},
            most_improved_team=None,
            team_names=self._names(),
        )
        assert any("commanding" in c and "4-game lead" in c for c in callouts)

    def test_sos_unbeaten_vs_good_teams(self) -> None:
        """Leader unbeaten against good teams triggers SOS callout."""
        standings = _standings([("A", 6, 1), ("B", 4, 3), ("C", 2, 5), ("D", 1, 6)])
        sos = {"A": {"wins": 3, "losses": 0}, "B": {"wins": 1, "losses": 2}}
        callouts = compute_narrative_callouts(
            standings=standings,
            streaks={},
            current_round=7,
            total_rounds=9,
            sos=sos,
            magic_numbers={},
            trajectory={},
            most_improved_team=None,
            team_names=self._names(),
        )
        assert any("3-0" in c and "above .500" in c for c in callouts)

    def test_sos_no_wins_vs_good_teams(self) -> None:
        """Leader with 0 wins vs good teams triggers warning callout."""
        standings = _standings([("A", 6, 1), ("B", 4, 3), ("C", 2, 5), ("D", 1, 6)])
        sos = {"A": {"wins": 0, "losses": 2}, "B": {"wins": 2, "losses": 1}}
        callouts = compute_narrative_callouts(
            standings=standings,
            streaks={},
            current_round=7,
            total_rounds=9,
            sos=sos,
            magic_numbers={},
            trajectory={},
            most_improved_team=None,
            team_names=self._names(),
        )
        assert any("haven't beaten a team above .500" in c for c in callouts)

    def test_clinch_callout(self) -> None:
        """Clinched team triggers callout."""
        standings = _standings([("A", 8, 0), ("B", 5, 3), ("C", 2, 6), ("D", 1, 7)])
        magic_numbers: dict[str, int | None] = {"A": 0, "B": 3, "C": None, "D": None}
        callouts = compute_narrative_callouts(
            standings=standings,
            streaks={},
            current_round=8,
            total_rounds=9,
            sos={},
            magic_numbers=magic_numbers,
            trajectory={},
            most_improved_team=None,
            team_names=self._names(),
        )
        assert any("clinched a playoff berth" in c for c in callouts)

    def test_close_to_clinch_callout(self) -> None:
        """Team 1-2 wins from clinching triggers callout."""
        standings = _standings([("A", 7, 1), ("B", 5, 3), ("C", 2, 6), ("D", 1, 7)])
        magic_numbers: dict[str, int | None] = {"A": 1, "B": 3, "C": None, "D": None}
        callouts = compute_narrative_callouts(
            standings=standings,
            streaks={},
            current_round=8,
            total_rounds=9,
            sos={},
            magic_numbers=magic_numbers,
            trajectory={},
            most_improved_team=None,
            team_names=self._names(),
        )
        assert any("1 win from clinching" in c for c in callouts)

    def test_streak_callout(self) -> None:
        """Active 3+ game streak triggers callout."""
        standings = _standings([("A", 7, 2), ("B", 5, 4)])
        callouts = compute_narrative_callouts(
            standings=standings,
            streaks={"A": 5, "B": -3},
            current_round=9,
            total_rounds=12,
            sos={},
            magic_numbers={},
            trajectory={},
            most_improved_team=None,
            team_names=self._names(),
        )
        assert any("5-game win streak" in c for c in callouts)

    def test_trajectory_callout(self) -> None:
        """Team that climbed 2+ spots triggers callout."""
        standings = _standings([("A", 5, 2), ("B", 4, 3), ("C", 3, 4), ("D", 2, 5)])
        callouts = compute_narrative_callouts(
            standings=standings,
            streaks={},
            current_round=7,
            total_rounds=9,
            sos={},
            magic_numbers={},
            trajectory={"A": 2, "B": 0, "C": -1, "D": -1},
            most_improved_team=None,
            team_names=self._names(),
        )
        assert any("climbed 2 spots" in c for c in callouts)

    def test_faller_callout(self) -> None:
        """Team that dropped 2+ spots triggers callout."""
        standings = _standings([("A", 5, 2), ("B", 4, 3), ("C", 3, 4), ("D", 2, 5)])
        callouts = compute_narrative_callouts(
            standings=standings,
            streaks={},
            current_round=7,
            total_rounds=9,
            sos={},
            magic_numbers={},
            trajectory={"A": 0, "B": 0, "C": 0, "D": -2},
            most_improved_team=None,
            team_names=self._names(),
        )
        assert any("dropped 2 spots" in c for c in callouts)

    def test_most_improved_callout(self) -> None:
        """Most improved team triggers callout."""
        standings = _standings([("A", 5, 2), ("B", 4, 3)])
        callouts = compute_narrative_callouts(
            standings=standings,
            streaks={},
            current_round=7,
            total_rounds=9,
            sos={},
            magic_numbers={},
            trajectory={},
            most_improved_team="B",
            team_names=self._names(),
        )
        assert any("most improved" in c for c in callouts)

    def test_late_season_callout(self) -> None:
        """Late season triggers remaining rounds callout."""
        standings = _standings([("A", 8, 1), ("B", 7, 2)])
        callouts = compute_narrative_callouts(
            standings=standings,
            streaks={},
            current_round=9,
            total_rounds=12,
            sos={},
            magic_numbers={},
            trajectory={},
            most_improved_team=None,
            team_names=self._names(),
        )
        assert any("3 rounds remaining" in c for c in callouts)

    def test_empty_standings(self) -> None:
        """Empty standings should produce no callouts."""
        callouts = compute_narrative_callouts(
            standings=[],
            streaks={},
            current_round=0,
            total_rounds=0,
            sos={},
            magic_numbers={},
            trajectory={},
            most_improved_team=None,
            team_names={},
        )
        assert callouts == []

    def test_max_callouts_limit(self) -> None:
        """Should return at most 6 callouts."""
        standings = _standings([("A", 5, 2), ("B", 5, 2), ("C", 3, 4), ("D", 1, 6)])
        callouts = compute_narrative_callouts(
            standings=standings,
            streaks={"A": 5, "B": -4, "C": 3, "D": -3},
            current_round=8,
            total_rounds=9,
            sos={"A": {"wins": 0, "losses": 3}, "B": {"wins": 2, "losses": 1}},
            magic_numbers={"A": 0, "B": 1, "C": None, "D": None},
            trajectory={"A": 2, "B": -2, "C": 1, "D": -1},
            most_improved_team="C",
            team_names=self._names(),
        )
        assert len(callouts) <= 6

    def test_one_round_remaining_singular(self) -> None:
        """1 round remaining uses singular form."""
        standings = _standings([("A", 8, 0), ("B", 7, 1)])
        callouts = compute_narrative_callouts(
            standings=standings,
            streaks={},
            current_round=8,
            total_rounds=9,
            sos={},
            magic_numbers={},
            trajectory={},
            most_improved_team=None,
            team_names=self._names(),
        )
        assert any("1 round remaining" in c for c in callouts)


# ---------------------------------------------------------------------------
# Integration: existing _compute_standings_callouts tests should still pass
# via narrative_callouts (backwards compatibility of behavior)
# ---------------------------------------------------------------------------


class TestOrdinalSuffix:
    """Test the internal ordinal suffix helper."""

    def test_ordinals(self) -> None:
        from pinwheel.core.narrative_standings import _ordinal_suffix

        assert _ordinal_suffix(1) == "st"
        assert _ordinal_suffix(2) == "nd"
        assert _ordinal_suffix(3) == "rd"
        assert _ordinal_suffix(4) == "th"
        assert _ordinal_suffix(11) == "th"
        assert _ordinal_suffix(12) == "th"
        assert _ordinal_suffix(13) == "th"
        assert _ordinal_suffix(21) == "st"
        assert _ordinal_suffix(22) == "nd"
        assert _ordinal_suffix(23) == "rd"
