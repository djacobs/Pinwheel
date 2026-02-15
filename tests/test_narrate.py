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

    def test_defensive_rebound_on_missed_three(self) -> None:
        """Missed three with a defensive rebounder should mention the rebounder."""
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="three_point",
            result="missed",
            points=0,
            rebounder="Brick",
            is_offensive_rebound=False,
            seed=7,
        )
        assert "Flash" in text
        assert "Brick" in text

    def test_offensive_rebound_on_missed_rim(self) -> None:
        """Missed at_rim with an offensive rebounder should mention the rebounder."""
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="at_rim",
            result="missed",
            points=0,
            rebounder="Hustle",
            is_offensive_rebound=True,
            seed=3,
        )
        assert "Flash" in text
        assert "Hustle" in text
        assert "offensive" in text.lower()

    def test_defensive_rebound_mentions_defensive(self) -> None:
        """Defensive rebound narration should include 'defensive' or 'board' or 'glass'."""
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="mid_range",
            result="missed",
            points=0,
            rebounder="Glass",
            is_offensive_rebound=False,
            seed=5,
        )
        assert "Glass" in text
        # Should contain some rebound-related language
        lower = text.lower()
        assert any(word in lower for word in ["rebound", "board", "glass"])

    def test_no_rebound_on_made_shot(self) -> None:
        """Made shots should not include rebound narration even if rebounder passed."""
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="mid_range",
            result="made",
            points=2,
            rebounder="Brick",
            is_offensive_rebound=False,
            seed=1,
        )
        assert "Brick" not in text

    def test_no_rebound_when_rebounder_empty(self) -> None:
        """Missed shots with no rebounder should not include rebound narration."""
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="three_point",
            result="missed",
            points=0,
            rebounder="",
            is_offensive_rebound=False,
            seed=1,
        )
        assert "rebound" not in text.lower()
        assert "board" not in text.lower()

    def test_rebound_deterministic_with_same_seed(self) -> None:
        """Rebound narration should be deterministic for the same seed."""
        t1 = narrate_play(
            "A", "B", "mid_range", "missed", 0,
            rebounder="R", is_offensive_rebound=True, seed=42,
        )
        t2 = narrate_play(
            "A", "B", "mid_range", "missed", 0,
            rebounder="R", is_offensive_rebound=True, seed=42,
        )
        assert t1 == t2

    def test_no_rebound_on_foul(self) -> None:
        """Foul results should not include rebound narration."""
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="at_rim",
            result="foul",
            points=2,
            rebounder="Brick",
            is_offensive_rebound=False,
            seed=1,
        )
        assert "Brick" not in text


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
