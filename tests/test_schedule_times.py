"""Tests for schedule_times — staggered game start time computation."""

from datetime import UTC, datetime, timedelta

from pinwheel.core.schedule_times import compute_game_start_times, format_game_time


class TestComputeGameStartTimes:
    """Tests for compute_game_start_times."""

    def test_two_games_staggered(self):
        """Two games should have the second offset by interval_seconds."""
        base = datetime(2026, 2, 15, 18, 0, 0, tzinfo=UTC)
        times = compute_game_start_times(base, game_count=2, interval_seconds=1800)

        assert len(times) == 2
        assert times[0] == base
        assert times[1] == base + timedelta(seconds=1800)

    def test_single_game_no_stagger(self):
        """A single game should return exactly one time — the fire time."""
        base = datetime(2026, 2, 15, 20, 0, 0, tzinfo=UTC)
        times = compute_game_start_times(base, game_count=1, interval_seconds=1800)

        assert len(times) == 1
        assert times[0] == base

    def test_zero_interval_all_identical(self):
        """With interval=0, all games start at the same time."""
        base = datetime(2026, 2, 15, 18, 0, 0, tzinfo=UTC)
        times = compute_game_start_times(base, game_count=3, interval_seconds=0)

        assert len(times) == 3
        assert all(t == base for t in times)

    def test_three_games_offsets(self):
        """Three games should have proper 0, 1x, 2x offsets."""
        base = datetime(2026, 2, 15, 17, 0, 0, tzinfo=UTC)
        times = compute_game_start_times(base, game_count=3, interval_seconds=600)

        assert times[0] == base
        assert times[1] == base + timedelta(seconds=600)
        assert times[2] == base + timedelta(seconds=1200)

    def test_empty_game_count(self):
        """Zero games should return an empty list."""
        base = datetime(2026, 2, 15, 18, 0, 0, tzinfo=UTC)
        times = compute_game_start_times(base, game_count=0, interval_seconds=1800)

        assert times == []


class TestFormatGameTime:
    """Tests for format_game_time."""

    def test_afternoon_et(self):
        """A UTC time should be converted to ET and formatted correctly."""
        # 6:00 PM UTC = 1:00 PM ET (standard time, Feb)
        dt = datetime(2026, 2, 15, 18, 0, 0, tzinfo=UTC)
        result = format_game_time(dt)

        assert result == "1:00 PM ET"

    def test_custom_tz_label(self):
        """Custom tz_label should appear in the output."""
        dt = datetime(2026, 2, 15, 18, 0, 0, tzinfo=UTC)
        result = format_game_time(dt, tz_label="Eastern")

        assert "Eastern" in result

    def test_morning_time(self):
        """Morning times should format with AM."""
        # 2:30 PM UTC = 9:30 AM ET (standard time, Feb)
        dt = datetime(2026, 2, 15, 14, 30, 0, tzinfo=UTC)
        result = format_game_time(dt)

        assert result == "9:30 AM ET"

    def test_midnight_et(self):
        """Midnight ET should format as 12:00 AM."""
        # 5:00 AM UTC = 12:00 AM ET (standard time)
        dt = datetime(2026, 2, 15, 5, 0, 0, tzinfo=UTC)
        result = format_game_time(dt)

        assert result == "12:00 AM ET"

    def test_noon_et(self):
        """Noon ET should format as 12:00 PM."""
        # 5:00 PM UTC = 12:00 PM ET (standard time)
        dt = datetime(2026, 2, 15, 17, 0, 0, tzinfo=UTC)
        result = format_game_time(dt)

        assert result == "12:00 PM ET"
