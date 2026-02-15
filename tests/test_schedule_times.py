"""Tests for schedule_times — round-based start time computation."""

from datetime import UTC, datetime

from pinwheel.core.schedule_times import compute_round_start_times, format_game_time


class TestComputeRoundStartTimes:
    """Tests for compute_round_start_times (cron-based, per-round)."""

    def test_half_hour_cron_three_rounds(self):
        """*/30 cron should produce three times 30 min apart."""
        now = datetime(2026, 2, 15, 17, 55, 0, tzinfo=UTC)
        times = compute_round_start_times("*/30 * * * *", 3, now=now)

        assert len(times) == 3
        # First fire: top of the next half-hour (18:00)
        assert times[0] == datetime(2026, 2, 15, 18, 0, 0, tzinfo=UTC)
        assert times[1] == datetime(2026, 2, 15, 18, 30, 0, tzinfo=UTC)
        assert times[2] == datetime(2026, 2, 15, 19, 0, 0, tzinfo=UTC)

    def test_hourly_cron_two_rounds(self):
        """Hourly cron should produce times 1 hour apart."""
        now = datetime(2026, 2, 15, 12, 30, 0, tzinfo=UTC)
        times = compute_round_start_times("0 * * * *", 2, now=now)

        assert len(times) == 2
        assert times[0] == datetime(2026, 2, 15, 13, 0, 0, tzinfo=UTC)
        assert times[1] == datetime(2026, 2, 15, 14, 0, 0, tzinfo=UTC)

    def test_single_round(self):
        """A single round returns exactly one fire time."""
        now = datetime(2026, 2, 15, 12, 0, 0, tzinfo=UTC)
        times = compute_round_start_times("*/30 * * * *", 1, now=now)

        assert len(times) == 1
        assert times[0] == datetime(2026, 2, 15, 12, 30, 0, tzinfo=UTC)

    def test_zero_rounds(self):
        """Zero rounds returns an empty list."""
        now = datetime(2026, 2, 15, 12, 0, 0, tzinfo=UTC)
        times = compute_round_start_times("*/30 * * * *", 0, now=now)

        assert times == []

    def test_defaults_to_now(self):
        """Without an explicit now, should still return valid fire times."""
        times = compute_round_start_times("*/30 * * * *", 2)

        assert len(times) == 2
        # Second time should be 30 min after the first
        diff = (times[1] - times[0]).total_seconds()
        assert diff == 1800

    def test_every_fifteen_minutes(self):
        """*/15 cron should produce times 15 min apart."""
        now = datetime(2026, 2, 15, 10, 0, 0, tzinfo=UTC)
        times = compute_round_start_times("*/15 * * * *", 4, now=now)

        assert len(times) == 4
        assert times[0] == datetime(2026, 2, 15, 10, 15, 0, tzinfo=UTC)
        assert times[1] == datetime(2026, 2, 15, 10, 30, 0, tzinfo=UTC)
        assert times[2] == datetime(2026, 2, 15, 10, 45, 0, tzinfo=UTC)
        assert times[3] == datetime(2026, 2, 15, 11, 0, 0, tzinfo=UTC)


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
