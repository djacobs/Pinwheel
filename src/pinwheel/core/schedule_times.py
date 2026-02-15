"""Compute and format game start times for upcoming time slots.

Uses the cron schedule to determine when each time slot tips off.
A "round" in the database may contain more games than can play
simultaneously (e.g. 6 matchups for 4 teams).  This module groups
games into *slots* — sets of games where no team appears twice —
and assigns each slot a successive cron fire time.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

# Eastern Time for display — the league's canonical time zone.
_ET = ZoneInfo("America/New_York")


def group_into_slots(
    entries: Sequence[Any],
    home_key: str = "home_team_id",
    away_key: str = "away_team_id",
) -> list[list[Any]]:
    """Group schedule entries into simultaneous time slots.

    A time slot is a set of games where no team appears twice —
    i.e. all games in a slot can tip off at the same time.

    Uses greedy first-fit: iterate entries in order and place each
    in the first slot that has no team overlap.  The round-robin
    scheduler already orders matchups so that consecutive pairs are
    non-overlapping, so this produces the minimal number of slots.

    Works with both objects (``getattr``) and dicts (``[]``).

    Args:
        entries: Schedule entries ordered by matchup_index.
        home_key: Attribute/key for the home team identifier.
        away_key: Attribute/key for the away team identifier.

    Returns:
        List of slots, each a list of entries.
    """

    def _get(entry: Any, key: str) -> str:
        if isinstance(entry, dict):
            return entry[key]  # type: ignore[return-value]
        return getattr(entry, key)  # type: ignore[return-value]

    slots: list[list[Any]] = []
    slot_teams: list[set[str]] = []
    for entry in entries:
        teams = {_get(entry, home_key), _get(entry, away_key)}
        placed = False
        for i, st in enumerate(slot_teams):
            if not teams & st:
                slots[i].append(entry)
                st.update(teams)
                placed = True
                break
        if not placed:
            slots.append([entry])
            slot_teams.append(set(teams))
    return slots


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
