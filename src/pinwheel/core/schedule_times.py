"""Compute and format game start times for upcoming rounds.

Uses the cron schedule to determine when each round tips off.  All games
within a round start at the same time (no team plays twice in a round),
and successive rounds are spaced by the cron cadence.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

# Eastern Time for display — the league's canonical time zone.
_ET = ZoneInfo("America/New_York")


def compute_round_start_times(
    cron_expression: str,
    round_count: int,
    now: datetime | None = None,
) -> list[datetime]:
    """Return the start time for each of the next *round_count* rounds.

    Uses APScheduler's ``CronTrigger`` to iterate successive fire times
    from the game cron expression, so it works correctly with any cron
    cadence (``*/30 * * * *``, ``0 * * * *``, etc.).

    Args:
        cron_expression: The cron expression driving round advancement.
        round_count: How many upcoming rounds to compute times for.
        now: Reference time (defaults to ``datetime.now(UTC)``).

    Returns:
        List of tz-aware datetimes, length == *round_count*.
    """
    from apscheduler.triggers.cron import CronTrigger

    trigger = CronTrigger.from_crontab(cron_expression)
    ref = now or datetime.now(UTC)
    times: list[datetime] = []
    for _ in range(round_count):
        t = trigger.get_next_fire_time(ref, ref)
        if t is None:
            break
        times.append(t)
        ref = t
    return times


def format_game_time(dt: datetime, tz_label: str = "ET") -> str:
    """Format a datetime as ``"1:00 PM ET"`` in US Eastern time.

    Args:
        dt: A tz-aware datetime to format.
        tz_label: Label appended after the time string (default ``"ET"``).

    Returns:
        Human-readable time string, e.g. ``"1:00 PM ET"``.
    """
    et_time = dt.astimezone(_ET)
    # %-I removes leading zero on hour (platform-dependent; fall back to lstrip)
    try:
        formatted = et_time.strftime("%-I:%M %p")
    except ValueError:
        # Windows doesn't support %-I
        formatted = et_time.strftime("%I:%M %p").lstrip("0")
    return f"{formatted} {tz_label}"
