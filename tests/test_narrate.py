"""Tests for the narration layer."""

from pinwheel.core.narrate import narrate_play, narrate_winner


class TestNarratePlay:
    def test_foul_with_points_shows_hits(self) -> None:
        """Foul with points > 0 should include 'hits' from the stripe."""
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="mid_range",
            result="foul",
            points=2,
            seed=42,
        )
        assert "hits 2 from the stripe" in text

    def test_foul_with_zero_points_shows_misses(self) -> None:
        """Foul with 0 points should include 'misses from the stripe'."""
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="mid_range",
            result="foul",
            points=0,
            seed=42,
        )
        assert "misses from the stripe" in text

    def test_made_three_has_narration(self) -> None:
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="three_point",
            result="made",
            points=3,
            seed=1,
        )
        assert "Flash" in text
        assert len(text) > 10

    def test_missed_shot_has_narration(self) -> None:
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="at_rim",
            result="missed",
            points=0,
            seed=1,
        )
        assert "Flash" in text

    def test_turnover_has_narration(self) -> None:
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="mid_range",
            result="turnover",
            points=0,
            seed=1,
        )
        assert "Flash" in text or "Thunder" in text

    def test_shot_clock_violation(self) -> None:
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="shot_clock_violation",
            result="turnover",
            points=0,
            seed=1,
        )
        assert "Flash" in text
        assert "shot clock" in text.lower()

    def test_move_flourish_prepended(self) -> None:
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="three_point",
            result="made",
            points=3,
            move="Heat Check",
            seed=1,
        )
        assert "[Heat Check]" in text

    def test_deterministic_with_same_seed(self) -> None:
        t1 = narrate_play("A", "B", "mid_range", "made", 2, seed=99)
        t2 = narrate_play("A", "B", "mid_range", "made", 2, seed=99)
        assert t1 == t2


class TestNarrateWinner:
    def test_three_point_winner(self) -> None:
        text = narrate_winner("Flash", "three_point", seed=42)
        assert "Flash" in text

    def test_mid_range_winner(self) -> None:
        text = narrate_winner("Flash", "mid_range", seed=42)
        assert "Flash" in text

    def test_at_rim_winner(self) -> None:
        text = narrate_winner("Flash", "at_rim", seed=42)
        assert "Flash" in text

    def test_unknown_action_fallback(self) -> None:
        text = narrate_winner("Flash", "unknown_action", seed=42)
        assert text == "Flash hits the game-winner"

    def test_move_flourish_appended(self) -> None:
        text = narrate_winner("Flash", "three_point", move="Clutch Gene", seed=42)
        assert "clutch gene activated" in text
