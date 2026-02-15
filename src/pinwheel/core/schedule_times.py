"""Compute and format staggered game start times for upcoming rounds.

Pure functions — no database or framework dependencies.  Given a round's
next fire time and the number of games, produces a list of start times
spaced by the configured interval so viewers see a natural "early game /
late game" rhythm.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

# Eastern Time for display — the league's canonical time zone.
_ET = ZoneInfo("America/New_York")


def compute_game_start_times(
    next_fire_time: datetime,
    game_count: int,
    interval_seconds: int,
) -> list[datetime]:
    """Return a list of start times, one per game, staggered by *interval_seconds*.

    Game 0 starts at *next_fire_time*; game N starts at
    ``next_fire_time + N * interval_seconds``.

    Args:
        next_fire_time: When the round's first game tips off (tz-aware).
        game_count: Number of games in the round.
        interval_seconds: Seconds between successive game starts.

    Returns:
        List of tz-aware datetimes, length == *game_count*.
    """
    from datetime import timedelta

    return [
        next_fire_time + timedelta(seconds=idx * interval_seconds)
        for idx in range(game_count)
    ]


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
