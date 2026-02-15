"""Tests for the dramatic pacing modulation module."""

from __future__ import annotations

from pinwheel.core.drama import (
    DramaAnnotation,
    annotate_drama,
    get_drama_summary,
    normalize_delays,
)
from pinwheel.models.game import GameResult, PossessionLog

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_possession(
    quarter: int = 1,
    possession_number: int = 1,
    offense_team_id: str = "home",
    ball_handler_id: str = "p1",
    action: str = "three_point",
    result: str = "made",
    points_scored: int = 3,
    home_score: int = 3,
    away_score: int = 0,
    move_activated: str = "",
    game_clock: str = "10:00",
) -> PossessionLog:
    return PossessionLog(
        quarter=quarter,
        possession_number=possession_number,
        offense_team_id=offense_team_id,
        ball_handler_id=ball_handler_id,
        action=action,
        result=result,
        points_scored=points_scored,
        home_score=home_score,
        away_score=away_score,
        move_activated=move_activated,
        game_clock=game_clock,
    )


def _make_game_result(
    possessions: list[PossessionLog],
    home_score: int = 0,
    away_score: int = 0,
    elam_target: int | None = None,
) -> GameResult:
    """Build a minimal GameResult for drama classification testing."""
    # Use final possession scores if available
    if possessions:
        home_score = home_score or possessions[-1].home_score
        away_score = away_score or possessions[-1].away_score
    winner = "home" if home_score >= away_score else "away"
    return GameResult(
        game_id="test-game",
        home_team_id="home",
        away_team_id="away",
        home_score=home_score,
        away_score=away_score,
        winner_team_id=winner,
        seed=42,
        total_possessions=len(possessions),
        elam_activated=elam_target is not None,
        elam_target_score=elam_target,
        possession_log=possessions,
    )


# ---------------------------------------------------------------------------
# Test: empty and single-possession edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_possession_log_returns_empty(self) -> None:
        """Edge case: empty possession log returns empty annotations."""
        game = _make_game_result(possessions=[])
        annotations = annotate_drama(game)
        assert annotations == []

    def test_single_possession_quarter(self) -> None:
        """Edge case: single-possession quarter returns one annotation."""
        poss = [_make_possession(quarter=1, home_score=3, away_score=0, points_scored=3)]
        game = _make_game_result(poss)
        annotations = annotate_drama(game)
        assert len(annotations) == 1
        # Single possession that scores: it's the last possession with points,
        # so it should be marked as game_winner (peak)
        assert annotations[0].level == "peak"
        assert "game_winner" in annotations[0].tags


# ---------------------------------------------------------------------------
# Test: drama level classification
# ---------------------------------------------------------------------------


class TestDramaLevelClassification:
    def test_routine_regular_season_blowout(self) -> None:
        """Regular season blowout — all possessions should be routine or elevated."""
        possessions = []
        # Home team scores 10 unanswered — creates a big run (elevated)
        for i in range(10):
            possessions.append(
                _make_possession(
                    quarter=1,
                    possession_number=i + 1,
                    offense_team_id="home",
                    points_scored=2,
                    home_score=2 * (i + 1),
                    away_score=0,
                    result="made",
                    action="mid_range",
                )
            )
        # Away misses a bunch
        for i in range(5):
            possessions.append(
                _make_possession(
                    quarter=2,
                    possession_number=11 + i,
                    offense_team_id="away",
                    points_scored=0,
                    home_score=20,
                    away_score=0,
                    result="missed",
                    action="three_point",
                )
            )
        game = _make_game_result(possessions, home_score=20, away_score=0)
        annotations = annotate_drama(game)

        # No peak or high expected in a blowout (except runs which are elevated)
        levels = {a.level for a in annotations}
        # Should NOT have peak (no lead changes, no close game, etc.)
        # The last possession is a miss so no game_winner tag
        assert "peak" not in levels

    def test_lead_change_is_high(self) -> None:
        """Lead changes should produce high drama."""
        possessions = [
            # Home leads 3-0
            _make_possession(quarter=1, possession_number=1, offense_team_id="home",
                             points_scored=3, home_score=3, away_score=0),
            # Away takes lead 3-5
            _make_possession(quarter=1, possession_number=2, offense_team_id="away",
                             points_scored=5, home_score=3, away_score=5),
            # Home takes lead back 6-5
            _make_possession(quarter=1, possession_number=3, offense_team_id="home",
                             points_scored=3, home_score=6, away_score=5),
        ]
        game = _make_game_result(possessions)
        annotations = annotate_drama(game)

        # Possession 1 (home leads): first possession, no prior leader, routine
        # Possession 2 (away leads): lead_change from home to away → high
        assert annotations[1].level == "high"
        assert "lead_change" in annotations[1].tags

        # Possession 3 (home leads again): lead_change from away to home → high
        # Also game_winner (last possession with points) → peak
        assert annotations[2].level == "peak"
        assert "lead_change" in annotations[2].tags

    def test_tie_broken_is_at_least_high(self) -> None:
        """Breaking a tie should produce at least high drama (multiplier 1.4)."""
        possessions = [
            # Tie at 3-3
            _make_possession(quarter=1, possession_number=1, offense_team_id="home",
                             points_scored=3, home_score=3, away_score=0),
            _make_possession(quarter=1, possession_number=2, offense_team_id="away",
                             points_scored=3, home_score=3, away_score=3),
            # Home breaks tie: 6-3
            _make_possession(quarter=1, possession_number=3, offense_team_id="home",
                             points_scored=3, home_score=6, away_score=3),
        ]
        game = _make_game_result(possessions)
        annotations = annotate_drama(game)

        # Possession 2: game_tied (multiplier 1.5) → high
        assert annotations[1].level == "high"
        assert "game_tied" in annotations[1].tags

        # Possession 3: tie_broken (multiplier 1.4) + game_winner → peak
        assert annotations[2].level == "peak"
        assert "tie_broken" in annotations[2].tags

    def test_close_game_late_quarter(self) -> None:
        """Close game in Q3 should get close_late tag."""
        possessions = [
            _make_possession(quarter=3, possession_number=1, offense_team_id="home",
                             points_scored=2, home_score=30, away_score=29),
        ]
        game = _make_game_result(possessions)
        annotations = annotate_drama(game)

        assert "close_late" in annotations[0].tags

    def test_move_activation_is_at_least_routine(self) -> None:
        """Move activations should bump drama with a move tag."""
        possessions = [
            _make_possession(quarter=1, possession_number=1, offense_team_id="home",
                             points_scored=3, home_score=3, away_score=0,
                             move_activated="Heat Check"),
        ]
        game = _make_game_result(possessions)
        annotations = annotate_drama(game)

        assert "move" in annotations[0].tags
        assert "move:Heat Check" in annotations[0].tags
        assert annotations[0].delay_multiplier >= 1.3


# ---------------------------------------------------------------------------
# Test: Elam Ending detection
# ---------------------------------------------------------------------------


class TestElamDetection:
    def test_elam_possessions_tagged(self) -> None:
        """Possessions in Q4+ with an elam_target should get elam tags."""
        possessions = [
            # Regular Q3 possession
            _make_possession(quarter=3, possession_number=1, offense_team_id="home",
                             points_scored=2, home_score=40, away_score=38),
            # Elam Q4 — far from target
            _make_possession(quarter=4, possession_number=2, offense_team_id="away",
                             points_scored=2, home_score=40, away_score=40),
            # Elam Q4 — approaching target (within 7)
            _make_possession(quarter=4, possession_number=3, offense_team_id="home",
                             points_scored=3, home_score=43, away_score=40),
            # Elam Q4 — climax (within 3)
            _make_possession(quarter=4, possession_number=4, offense_team_id="home",
                             points_scored=2, home_score=45, away_score=40),
        ]
        game = _make_game_result(possessions, elam_target=47)
        annotations = annotate_drama(game)

        # Q3 possession should NOT have elam tag
        assert "elam" not in annotations[0].tags

        # Q4 possession far from target: elam tag but no climax
        assert "elam" in annotations[1].tags
        assert "elam_climax" not in annotations[1].tags

        # Q4 approaching target (47 - 43 = 4, within 7): elam_tension
        assert "elam" in annotations[2].tags
        assert "elam_tension" in annotations[2].tags

        # Q4 climax (47 - 45 = 2, within 3): elam_climax → peak
        assert "elam" in annotations[3].tags
        assert "elam_climax" in annotations[3].tags
        assert annotations[3].level == "peak"

    def test_elam_activation_bumps_drama(self) -> None:
        """Entering Elam (Q3→Q4 transition) should get elam_start tag → peak."""
        possessions = [
            _make_possession(quarter=3, possession_number=1, offense_team_id="home",
                             points_scored=2, home_score=38, away_score=36),
            _make_possession(quarter=4, possession_number=2, offense_team_id="away",
                             points_scored=2, home_score=38, away_score=38),
        ]
        game = _make_game_result(possessions, elam_target=50)
        annotations = annotate_drama(game)

        # Second possession transitions from Q3 to Q4 — elam_start
        assert "elam_start" in annotations[1].tags
        assert annotations[1].delay_multiplier >= 2.0  # peak threshold

    def test_championship_game_with_elam_is_peak(self) -> None:
        """Championship game with Elam climax ending should be peak."""
        possessions = [
            # Build up in Q4
            _make_possession(quarter=4, possession_number=1, offense_team_id="home",
                             points_scored=2, home_score=45, away_score=44),
            # Game winner — hits the target
            _make_possession(quarter=4, possession_number=2, offense_team_id="home",
                             points_scored=3, home_score=48, away_score=44),
        ]
        game = _make_game_result(possessions, elam_target=48)
        annotations = annotate_drama(game)

        # Last possession: elam_climax + game_winner → definitely peak
        last = annotations[-1]
        assert last.level == "peak"
        assert "game_winner" in last.tags
        assert "elam_climax" in last.tags


# ---------------------------------------------------------------------------
# Test: scoring runs
# ---------------------------------------------------------------------------


class TestScoringRuns:
    def test_scoring_run_detected(self) -> None:
        """A team scoring 5+ unanswered should trigger a run tag."""
        possessions = [
            _make_possession(quarter=1, possession_number=1, offense_team_id="home",
                             points_scored=3, home_score=3, away_score=0),
            _make_possession(quarter=1, possession_number=2, offense_team_id="home",
                             points_scored=3, home_score=6, away_score=0),
        ]
        game = _make_game_result(possessions)
        annotations = annotate_drama(game)

        # After 6 points by home team, the second possession should detect a run (>= 5)
        assert "run" in annotations[1].tags

    def test_big_run_detected(self) -> None:
        """A team scoring 8+ unanswered should trigger a big_run tag."""
        possessions = [
            _make_possession(quarter=1, possession_number=1, offense_team_id="home",
                             points_scored=3, home_score=3, away_score=0),
            _make_possession(quarter=1, possession_number=2, offense_team_id="home",
                             points_scored=3, home_score=6, away_score=0),
            _make_possession(quarter=1, possession_number=3, offense_team_id="home",
                             points_scored=3, home_score=9, away_score=0),
        ]
        game = _make_game_result(possessions)
        annotations = annotate_drama(game)

        # After 9 points (>= 8) — big_run
        assert "big_run" in annotations[2].tags
        # big_run has multiplier < 1.0, so level should be "elevated"
        assert annotations[2].level in ("elevated", "peak")  # peak because game_winner

    def test_run_resets_on_other_team_scoring(self) -> None:
        """Scoring run resets when the other team scores."""
        possessions = [
            _make_possession(quarter=1, possession_number=1, offense_team_id="home",
                             points_scored=3, home_score=3, away_score=0),
            _make_possession(quarter=1, possession_number=2, offense_team_id="home",
                             points_scored=3, home_score=6, away_score=0),
            # Away team scores, resetting the run
            _make_possession(quarter=1, possession_number=3, offense_team_id="away",
                             points_scored=2, home_score=6, away_score=2),
            # Home scores again — run count resets to 3
            _make_possession(quarter=1, possession_number=4, offense_team_id="home",
                             points_scored=3, home_score=9, away_score=2),
        ]
        game = _make_game_result(possessions)
        annotations = annotate_drama(game)

        # Possession 3: away scores 2, their run is 2 — no run tag
        assert "run" not in annotations[2].tags
        assert "big_run" not in annotations[2].tags

        # Possession 4: home scores 3, their new run is 3 — no run tag (< 5)
        assert "run" not in annotations[3].tags


# ---------------------------------------------------------------------------
# Test: game winner detection
# ---------------------------------------------------------------------------


class TestGameWinner:
    def test_last_scoring_possession_is_game_winner(self) -> None:
        """The last possession with points_scored > 0 should be game_winner."""
        possessions = [
            _make_possession(quarter=1, possession_number=1, offense_team_id="home",
                             points_scored=3, home_score=3, away_score=0),
            _make_possession(quarter=1, possession_number=2, offense_team_id="away",
                             points_scored=0, home_score=3, away_score=0, result="missed"),
        ]
        game = _make_game_result(possessions)
        annotations = annotate_drama(game)

        # Last possession is a miss — NOT game_winner
        assert "game_winner" not in annotations[1].tags
        # First possession is not the last, so also not game_winner
        assert "game_winner" not in annotations[0].tags

    def test_game_winner_is_peak(self) -> None:
        """A scoring last possession should be peak drama."""
        possessions = [
            _make_possession(quarter=1, possession_number=1, offense_team_id="home",
                             points_scored=2, home_score=2, away_score=0),
            _make_possession(quarter=1, possession_number=2, offense_team_id="away",
                             points_scored=3, home_score=2, away_score=3),
        ]
        game = _make_game_result(possessions)
        annotations = annotate_drama(game)

        last = annotations[-1]
        assert "game_winner" in last.tags
        assert last.level == "peak"
        assert last.delay_multiplier >= 3.0


# ---------------------------------------------------------------------------
# Test: multiple drama factors stack
# ---------------------------------------------------------------------------


class TestDramaStacking:
    def test_close_playoff_higher_than_close_regular(self) -> None:
        """Multiple drama factors should stack — close + lead_change + late = higher multiplier."""
        # Simple close game with a lead change
        possessions = [
            _make_possession(quarter=1, possession_number=1, offense_team_id="home",
                             points_scored=3, home_score=3, away_score=0),
            _make_possession(quarter=1, possession_number=2, offense_team_id="away",
                             points_scored=5, home_score=3, away_score=5),
        ]
        regular_game = _make_game_result(possessions)
        regular_annotations = annotate_drama(regular_game)

        # Q3 close game with lead change (stacking close_late + lead_change)
        q3_possessions = [
            _make_possession(quarter=3, possession_number=1, offense_team_id="home",
                             points_scored=2, home_score=30, away_score=28),
            _make_possession(quarter=3, possession_number=2, offense_team_id="away",
                             points_scored=3, home_score=30, away_score=31),
        ]
        late_close_game = _make_game_result(q3_possessions)
        late_annotations = annotate_drama(late_close_game)

        # The lead change in Q3 close game should have higher multiplier
        # than the lead change in Q1 (because close_late stacks)
        regular_lc = regular_annotations[1]
        late_lc = late_annotations[1]

        assert late_lc.delay_multiplier >= regular_lc.delay_multiplier
        assert "close_late" in late_lc.tags
        assert "lead_change" in late_lc.tags


# ---------------------------------------------------------------------------
# Test: normalize_delays
# ---------------------------------------------------------------------------


class TestNormalizeDelays:
    def test_preserves_total_quarter_time(self) -> None:
        """normalize_delays should preserve total quarter time within 1%."""
        annotations = [
            DramaAnnotation(possession_index=0, level="routine", delay_multiplier=1.0),
            DramaAnnotation(possession_index=1, level="high", delay_multiplier=1.8),
            DramaAnnotation(possession_index=2, level="peak", delay_multiplier=3.0),
            DramaAnnotation(possession_index=3, level="elevated", delay_multiplier=0.75),
            DramaAnnotation(possession_index=4, level="routine", delay_multiplier=1.0),
        ]
        quarter_seconds = 300.0

        delays = normalize_delays(annotations, quarter_seconds)

        assert len(delays) == 5
        total = sum(delays)
        assert abs(total - quarter_seconds) / quarter_seconds < 0.01

    def test_all_routine_approximately_uniform(self) -> None:
        """With all routine possessions, delays should be approximately uniform."""
        annotations = [
            DramaAnnotation(possession_index=i, level="routine", delay_multiplier=1.0)
            for i in range(20)
        ]
        quarter_seconds = 300.0

        delays = normalize_delays(annotations, quarter_seconds)

        expected = quarter_seconds / 20
        for d in delays:
            assert abs(d - expected) < 0.01

    def test_peak_gets_longer_delay(self) -> None:
        """Peak possessions should get longer delays than routine ones."""
        annotations = [
            DramaAnnotation(possession_index=0, level="routine", delay_multiplier=1.0),
            DramaAnnotation(possession_index=1, level="peak", delay_multiplier=3.0),
            DramaAnnotation(possession_index=2, level="routine", delay_multiplier=1.0),
        ]
        quarter_seconds = 300.0

        delays = normalize_delays(annotations, quarter_seconds)

        # Peak delay should be 3x the routine delay
        assert delays[1] > delays[0]
        assert abs(delays[1] / delays[0] - 3.0) < 0.01

    def test_elevated_gets_shorter_delay(self) -> None:
        """Elevated (fast-paced run) possessions should get shorter delays."""
        annotations = [
            DramaAnnotation(possession_index=0, level="routine", delay_multiplier=1.0),
            DramaAnnotation(possession_index=1, level="elevated", delay_multiplier=0.75),
            DramaAnnotation(possession_index=2, level="routine", delay_multiplier=1.0),
        ]
        quarter_seconds = 300.0

        delays = normalize_delays(annotations, quarter_seconds)

        assert delays[1] < delays[0]
        assert abs(delays[1] / delays[0] - 0.75) < 0.01

    def test_empty_annotations_returns_empty(self) -> None:
        """Empty annotations should return empty delays."""
        delays = normalize_delays([], 300.0)
        assert delays == []

    def test_single_annotation(self) -> None:
        """Single annotation should get the full quarter time."""
        annotations = [
            DramaAnnotation(possession_index=0, level="peak", delay_multiplier=3.0),
        ]
        delays = normalize_delays(annotations, 300.0)

        assert len(delays) == 1
        assert abs(delays[0] - 300.0) < 0.01


# ---------------------------------------------------------------------------
# Test: suggested delay seconds match levels
# ---------------------------------------------------------------------------


class TestDelayMultipliers:
    def test_routine_multiplier_is_one(self) -> None:
        """Routine possessions should have multiplier of 1.0."""
        possessions = [
            # A simple basket with no drama triggers
            _make_possession(quarter=1, possession_number=1, offense_team_id="home",
                             points_scored=2, home_score=2, away_score=0),
            _make_possession(quarter=1, possession_number=2, offense_team_id="away",
                             points_scored=2, home_score=2, away_score=2),
            # Another basket, no special context, but score is tied then home leads
            # (game_tied is triggered by poss 2, tie_broken by poss 3)
            _make_possession(quarter=1, possession_number=3, offense_team_id="home",
                             points_scored=2, home_score=4, away_score=2),
        ]
        game = _make_game_result(possessions)
        annotations = annotate_drama(game)

        # First possession: no prior leader, just scores — routine or elevated
        # The multiplier should be 1.0 (no drama triggers)
        assert annotations[0].delay_multiplier == 1.0

    def test_peak_multiplier_gte_2(self) -> None:
        """Peak drama should have multiplier >= 2.0."""
        possessions = [
            _make_possession(quarter=4, possession_number=1, offense_team_id="home",
                             points_scored=3, home_score=47, away_score=44),
        ]
        game = _make_game_result(possessions, elam_target=48)
        annotations = annotate_drama(game)

        # Q4 with elam_target, within 1 of target: elam_climax + game_winner
        assert annotations[0].delay_multiplier >= 2.0
        assert annotations[0].level == "peak"


# ---------------------------------------------------------------------------
# Test: get_drama_summary
# ---------------------------------------------------------------------------


class TestDramaSummary:
    def test_summary_counts(self) -> None:
        """get_drama_summary should count levels correctly."""
        annotations = [
            DramaAnnotation(possession_index=0, level="routine"),
            DramaAnnotation(possession_index=1, level="routine"),
            DramaAnnotation(possession_index=2, level="elevated"),
            DramaAnnotation(possession_index=3, level="high"),
            DramaAnnotation(possession_index=4, level="peak"),
            DramaAnnotation(possession_index=5, level="peak"),
        ]
        summary = get_drama_summary(annotations)

        assert summary == {"routine": 2, "elevated": 1, "high": 1, "peak": 2}

    def test_empty_summary(self) -> None:
        """Empty annotations should return all-zero summary."""
        summary = get_drama_summary([])
        assert summary == {"routine": 0, "elevated": 0, "high": 0, "peak": 0}


# ---------------------------------------------------------------------------
# Test: win/loss streaks (indirectly through scoring runs)
# ---------------------------------------------------------------------------


class TestStreaks:
    def test_extended_scoring_run(self) -> None:
        """Extended scoring run (8+ points) should be detected as big_run."""
        possessions = []
        running_score = 0
        for i in range(5):
            running_score += 2
            possessions.append(
                _make_possession(
                    quarter=2,
                    possession_number=i + 1,
                    offense_team_id="home",
                    points_scored=2,
                    home_score=running_score,
                    away_score=10,
                    action="mid_range",
                    result="made",
                )
            )
        game = _make_game_result(possessions)
        annotations = annotate_drama(game)

        # After 10 points (5 baskets * 2), should have big_run
        big_run_found = any("big_run" in a.tags for a in annotations)
        assert big_run_found


# ---------------------------------------------------------------------------
# Test: integration with real simulation data shapes
# ---------------------------------------------------------------------------


class TestIntegrationShapes:
    def test_full_game_structure(self) -> None:
        """A game with multiple quarters should classify all possessions."""
        possessions = []
        home_score = 0
        away_score = 0
        poss_num = 0

        # Q1: Home dominance
        for _i in range(10):
            poss_num += 1
            home_score += 2
            possessions.append(
                _make_possession(
                    quarter=1, possession_number=poss_num,
                    offense_team_id="home", points_scored=2,
                    home_score=home_score, away_score=away_score,
                )
            )

        # Q2: Away fights back
        for _i in range(10):
            poss_num += 1
            away_score += 3
            possessions.append(
                _make_possession(
                    quarter=2, possession_number=poss_num,
                    offense_team_id="away", points_scored=3,
                    home_score=home_score, away_score=away_score,
                )
            )

        # Q3: Trading baskets
        for i in range(6):
            poss_num += 1
            team = "home" if i % 2 == 0 else "away"
            pts = 2
            if team == "home":
                home_score += pts
            else:
                away_score += pts
            possessions.append(
                _make_possession(
                    quarter=3, possession_number=poss_num,
                    offense_team_id=team, points_scored=pts,
                    home_score=home_score, away_score=away_score,
                )
            )

        game = _make_game_result(possessions)
        annotations = annotate_drama(game)

        # Should have one annotation per possession
        assert len(annotations) == len(possessions)

        # Should have a variety of drama levels
        levels = {a.level for a in annotations}
        assert len(levels) >= 2  # At least routine + something else

        # Summary should account for all possessions
        summary = get_drama_summary(annotations)
        total = sum(summary.values())
        assert total == len(possessions)

    def test_annotations_have_correct_indices(self) -> None:
        """Each annotation should have the correct possession_index."""
        possessions = [
            _make_possession(quarter=1, possession_number=i + 1,
                             offense_team_id="home" if i % 2 == 0 else "away",
                             points_scored=2,
                             home_score=2 * ((i // 2) + 1) if i % 2 == 0 else 2 * ((i + 1) // 2),
                             away_score=2 * (i // 2) if i % 2 == 0 else 2 * ((i + 1) // 2))
            for i in range(5)
        ]
        game = _make_game_result(possessions)
        annotations = annotate_drama(game)

        for i, ann in enumerate(annotations):
            assert ann.possession_index == i
