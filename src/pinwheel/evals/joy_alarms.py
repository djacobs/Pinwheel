"""Joy alarms — automated flags for when the game might not be fun.

Five alarm conditions from INSTRUMENTATION.md (Section A, Joy Alarms):

1. Disengagement — a governor hasn't voted or proposed in N rounds.
2. Political exclusion — a governor's proposals never pass (0% success over N proposals).
3. Economy stalling — token velocity drops (few trades, few boosts) window-over-window.
4. Reports not resonating — governor activity (proposals + votes) used as proxy for engagement.
5. Power concentration — one governor's proposals pass disproportionately (>60%).

Each alarm function queries the repository and returns a list of JoyAlarm instances.
``check_joy_alarms()`` orchestrates all checks, publishes to the event bus, and logs results.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pinwheel.core.event_bus import EventBus
    from pinwheel.db.repository import Repository

logger = logging.getLogger(__name__)

AlarmType = Literal[
    "disengagement",
    "political_exclusion",
    "economy_stalling",
    "reports_not_resonating",
    "power_concentration",
]
Severity = Literal["info", "warning", "critical"]


@dataclass
class JoyAlarm:
    """A single joy alarm instance."""

    alarm_type: AlarmType
    severity: Severity
    governor_id: str
    description: str
    recommended_action: str
    round_number: int = 0
    season_id: str = ""
    details: dict = field(default_factory=dict)


async def detect_disengagement(
    repo: Repository,
    season_id: str,
    round_number: int,
    inactive_threshold: int = 2,
) -> list[JoyAlarm]:
    """Alarm 1: A governor hasn't taken a governance action in N+ rounds.

    Compares each enrolled governor's last activity round against the current
    round. If the gap >= inactive_threshold, fire an alarm.
    """
    # Get all enrolled governors for the season
    governors = await repo.get_all_governors_for_season(season_id)
    if not governors:
        return []

    # Get all governance actions (proposals + votes) for the season
    events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["proposal.submitted", "vote.cast"],
    )

    # Map governor_id -> max round_number of activity
    last_active: dict[str, int] = {}
    for e in events:
        gid = e.governor_id
        rn = e.round_number
        if gid and rn is not None:
            last_active[gid] = max(last_active.get(gid, 0), rn)

    alarms: list[JoyAlarm] = []
    for gov in governors:
        gov_id = gov.id
        last_round = last_active.get(gov_id, 0)
        gap = round_number - last_round

        if gap >= inactive_threshold:
            alarms.append(
                JoyAlarm(
                    alarm_type="disengagement",
                    severity="warning" if gap >= inactive_threshold + 2 else "info",
                    governor_id=gov_id,
                    description=(
                        f"Governor {gov.username} has not voted or proposed "
                        f"in {gap} rounds (threshold: {inactive_threshold})."
                    ),
                    recommended_action=(
                        "Consider a direct nudge or check if the governance "
                        "surface is discoverable enough."
                    ),
                    round_number=round_number,
                    season_id=season_id,
                    details={
                        "governor_username": gov.username,
                        "last_active_round": last_round,
                        "rounds_inactive": gap,
                        "threshold": inactive_threshold,
                    },
                )
            )

    return alarms


async def detect_political_exclusion(
    repo: Repository,
    season_id: str,
    round_number: int,
    min_proposals: int = 3,
) -> list[JoyAlarm]:
    """Alarm 2: A governor's proposals never pass (0% success over N+ proposals).

    Only fires when a governor has submitted at least ``min_proposals`` proposals
    and none have passed. This avoids flagging new governors who submitted once.
    """
    # All submitted proposals
    submitted = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["proposal.submitted"],
    )

    # All outcome events
    outcomes = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["proposal.passed", "proposal.failed"],
    )

    # Build outcome lookup: proposal_id -> "passed" | "failed"
    outcome_map: dict[str, str] = {}
    for e in outcomes:
        pid = (e.payload or {}).get("proposal_id", e.aggregate_id)
        outcome_map[pid] = "passed" if e.event_type == "proposal.passed" else "failed"

    # Group proposals by governor
    proposals_by_gov: dict[str, list[str]] = {}
    gov_usernames: dict[str, str] = {}
    for e in submitted:
        gid = e.governor_id
        if not gid:
            continue
        pid = (e.payload or {}).get("id", e.aggregate_id)
        proposals_by_gov.setdefault(gid, []).append(pid)
        # Try to capture username from payload
        username = (e.payload or {}).get("governor_name", gid)
        gov_usernames[gid] = username

    alarms: list[JoyAlarm] = []
    for gov_id, proposal_ids in proposals_by_gov.items():
        # Only consider proposals that have an outcome (resolved)
        resolved = [pid for pid in proposal_ids if pid in outcome_map]
        if len(resolved) < min_proposals:
            continue

        passed = sum(1 for pid in resolved if outcome_map[pid] == "passed")
        if passed == 0:
            alarms.append(
                JoyAlarm(
                    alarm_type="political_exclusion",
                    severity="warning",
                    governor_id=gov_id,
                    description=(
                        f"Governor {gov_usernames.get(gov_id, gov_id)} has submitted "
                        f"{len(resolved)} proposals and none have passed."
                    ),
                    recommended_action=(
                        "The governance report should surface this pattern. "
                        "Consider whether proposal interpretation or coalition "
                        "dynamics are creating barriers."
                    ),
                    round_number=round_number,
                    season_id=season_id,
                    details={
                        "governor_username": gov_usernames.get(gov_id, gov_id),
                        "total_proposals": len(resolved),
                        "proposals_passed": 0,
                        "success_rate": 0.0,
                    },
                )
            )

    return alarms


async def detect_economy_stalling(
    repo: Repository,
    season_id: str,
    round_number: int,
    lookback_window: int = 3,
    drop_threshold: float = 0.5,
) -> list[JoyAlarm]:
    """Alarm 3: Token velocity drops below threshold.

    Compares trade + boost activity in the recent window vs. the previous window.
    If the recent window has <= (1 - drop_threshold) of the previous window's
    activity, fire an alarm. Uses trade.offered, trade.accepted, and token.spent
    (boost) events as signals.
    """
    # Fetch all trade and boost events for the season
    trade_events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["trade.offered", "trade.accepted"],
    )
    boost_events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["token.spent"],
    )
    # Filter boost events to only actual boosts
    boost_rounds: list[int] = []
    for e in boost_events:
        reason = (e.payload or {}).get("reason", "")
        if (
            "boost" in reason.lower()
            or (e.payload or {}).get("token_type") == "boost"
        ) and e.round_number is not None:
            boost_rounds.append(e.round_number)

    # Count activity per round
    activity_by_round: dict[int, int] = {}
    for e in trade_events:
        rn = e.round_number
        if rn is not None:
            activity_by_round[rn] = activity_by_round.get(rn, 0) + 1
    for rn in boost_rounds:
        activity_by_round[rn] = activity_by_round.get(rn, 0) + 1

    # Need at least 2 windows of data
    if round_number < 2 * lookback_window:
        return []

    # Recent window: [round_number - lookback_window + 1, round_number]
    recent_start = round_number - lookback_window + 1
    # Previous window: [recent_start - lookback_window, recent_start - 1]
    prev_start = recent_start - lookback_window

    recent_activity = sum(
        activity_by_round.get(r, 0)
        for r in range(recent_start, round_number + 1)
    )
    prev_activity = sum(
        activity_by_round.get(r, 0)
        for r in range(prev_start, recent_start)
    )

    # If previous window had activity and it dropped significantly
    if prev_activity > 0 and recent_activity <= prev_activity * (1 - drop_threshold):
        return [
            JoyAlarm(
                alarm_type="economy_stalling",
                severity="warning",
                governor_id="",  # League-wide alarm
                description=(
                    f"Token economy activity dropped >={int(drop_threshold * 100)}% "
                    f"window-over-window: {prev_activity} actions "
                    f"(rounds {prev_start}-{recent_start - 1}) -> "
                    f"{recent_activity} actions "
                    f"(rounds {recent_start}-{round_number})."
                ),
                recommended_action=(
                    "Consider whether token regeneration rates are too high "
                    "(no scarcity) or governance proposals are not contentious "
                    "enough to drive trading."
                ),
                round_number=round_number,
                season_id=season_id,
                details={
                    "prev_window_activity": prev_activity,
                    "recent_window_activity": recent_activity,
                    "drop_percent": (
                        round((1 - recent_activity / prev_activity) * 100, 1)
                        if prev_activity > 0
                        else 0.0
                    ),
                    "lookback_window": lookback_window,
                    "threshold_percent": drop_threshold * 100,
                },
            )
        ]

    # Also alarm if there's been zero economy activity for the full lookback
    total_all_time = sum(activity_by_round.values())
    if total_all_time > 0 and recent_activity == 0 and prev_activity == 0:
        return [
            JoyAlarm(
                alarm_type="economy_stalling",
                severity="info",
                governor_id="",
                description=(
                    f"No trade or boost activity in the last "
                    f"{2 * lookback_window} rounds despite prior activity."
                ),
                recommended_action=(
                    "The token economy may have stalled. Check if governors "
                    "are hoarding tokens or if the incentive to trade has "
                    "been removed by recent rule changes."
                ),
                round_number=round_number,
                season_id=season_id,
                details={
                    "prev_window_activity": 0,
                    "recent_window_activity": 0,
                    "total_season_activity": total_all_time,
                    "lookback_window": lookback_window,
                },
            )
        ]

    return []


async def detect_reports_not_resonating(
    repo: Repository,
    season_id: str,
    round_number: int,
    low_engagement_threshold: float = 0.2,
    lookback_rounds: int = 3,
) -> list[JoyAlarm]:
    """Alarm 4: Reports not resonating (proxy: governor activity drops).

    Since we don't track report views yet, we use governor activity
    (proposals + votes per round per governor) as a proxy. If the average
    activity per governor in the recent window is below the threshold
    fraction of their earlier baseline, reports may not be driving engagement.
    """
    governors = await repo.get_all_governors_for_season(season_id)
    if not governors:
        return []

    events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["proposal.submitted", "vote.cast"],
    )

    # Count actions per governor per round
    actions_per_gov_round: dict[str, dict[int, int]] = {}
    for e in events:
        gid = e.governor_id
        rn = e.round_number
        if gid and rn is not None:
            actions_per_gov_round.setdefault(gid, {})
            actions_per_gov_round[gid][rn] = actions_per_gov_round[gid].get(rn, 0) + 1

    # Need enough history
    if round_number < 2 * lookback_rounds:
        return []

    recent_start = round_number - lookback_rounds + 1
    baseline_start = max(1, recent_start - lookback_rounds)

    # Compute average actions per active governor in each window
    def window_avg(start: int, end: int) -> float:
        total_actions = 0
        active_govs: set[str] = set()
        for gid, rounds in actions_per_gov_round.items():
            for r in range(start, end + 1):
                count = rounds.get(r, 0)
                if count > 0:
                    total_actions += count
                    active_govs.add(gid)
        if not active_govs:
            return 0.0
        return total_actions / len(active_govs)

    baseline_avg = window_avg(baseline_start, recent_start - 1)
    recent_avg = window_avg(recent_start, round_number)

    if baseline_avg > 0 and recent_avg <= baseline_avg * low_engagement_threshold:
        return [
            JoyAlarm(
                alarm_type="reports_not_resonating",
                severity="info",
                governor_id="",  # League-wide alarm
                description=(
                    f"Governor activity (proxy for report engagement) dropped to "
                    f"{recent_avg:.1f} actions/governor vs baseline of "
                    f"{baseline_avg:.1f} — below {int(low_engagement_threshold * 100)}% threshold."
                ),
                recommended_action=(
                    "Review report quality. Consider whether private reports "
                    "surface actionable patterns or just restate visible data. "
                    "Check if report delivery timing aligns with governance windows."
                ),
                round_number=round_number,
                season_id=season_id,
                details={
                    "baseline_avg_actions": round(baseline_avg, 2),
                    "recent_avg_actions": round(recent_avg, 2),
                    "threshold_fraction": low_engagement_threshold,
                    "lookback_rounds": lookback_rounds,
                    "enrolled_governors": len(governors),
                },
            )
        ]

    return []


async def detect_power_concentration(
    repo: Repository,
    season_id: str,
    round_number: int,
    concentration_threshold: float = 0.6,
    min_passed: int = 3,
) -> list[JoyAlarm]:
    """Alarm 5: One governor's proposals pass disproportionately (>60%).

    If a single governor accounts for more than ``concentration_threshold``
    of all passed proposals (minimum ``min_passed`` total), fire an alarm.
    """
    # Get all passed proposals
    passed_events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["proposal.passed"],
    )

    if len(passed_events) < min_passed:
        return []

    # Map passed proposal_id -> True
    passed_ids: set[str] = set()
    for e in passed_events:
        pid = (e.payload or {}).get("proposal_id", e.aggregate_id)
        passed_ids.add(pid)

    # Get all submitted proposals to find who submitted the passed ones
    submitted_events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["proposal.submitted"],
    )

    # Count passed proposals per governor
    passed_by_gov: dict[str, int] = {}
    gov_usernames: dict[str, str] = {}
    for e in submitted_events:
        gid = e.governor_id
        if not gid:
            continue
        pid = (e.payload or {}).get("id", e.aggregate_id)
        if pid in passed_ids:
            passed_by_gov[gid] = passed_by_gov.get(gid, 0) + 1
            username = (e.payload or {}).get("governor_name", gid)
            gov_usernames[gid] = username

    total_passed = len(passed_ids)
    if total_passed < min_passed:
        return []

    alarms: list[JoyAlarm] = []
    for gov_id, count in passed_by_gov.items():
        share = count / total_passed
        if share > concentration_threshold:
            alarms.append(
                JoyAlarm(
                    alarm_type="power_concentration",
                    severity="warning" if share > 0.75 else "info",
                    governor_id=gov_id,
                    description=(
                        f"Governor {gov_usernames.get(gov_id, gov_id)} has passed "
                        f"{count} of {total_passed} proposals "
                        f"({share:.0%} of all passed proposals)."
                    ),
                    recommended_action=(
                        "The governance report should surface this pattern. "
                        "This is also gameplay — power concentration is visible "
                        "information that other governors can act on."
                    ),
                    round_number=round_number,
                    season_id=season_id,
                    details={
                        "governor_username": gov_usernames.get(gov_id, gov_id),
                        "proposals_passed": count,
                        "total_passed": total_passed,
                        "share": round(share, 3),
                        "threshold": concentration_threshold,
                    },
                )
            )

    return alarms


async def check_joy_alarms(
    repo: Repository,
    season_id: str,
    round_number: int,
    event_bus: EventBus | None = None,
) -> list[JoyAlarm]:
    """Run all joy alarm checks and return combined results.

    Publishes ``joy.alarm`` events to the event bus for each triggered alarm.
    Logs each alarm with structured logging.
    """
    alarms: list[JoyAlarm] = []

    detectors = [
        ("disengagement", detect_disengagement),
        ("political_exclusion", detect_political_exclusion),
        ("economy_stalling", detect_economy_stalling),
        ("reports_not_resonating", detect_reports_not_resonating),
        ("power_concentration", detect_power_concentration),
    ]

    for name, detector in detectors:
        try:
            results = await detector(repo, season_id, round_number)
            alarms.extend(results)
        except Exception:
            logger.exception(
                "joy_alarm_detection_failed alarm=%s season=%s round=%d",
                name,
                season_id,
                round_number,
            )

    # Log and publish each alarm
    for alarm in alarms:
        logger.info(
            "joy_alarm_triggered type=%s severity=%s governor=%s season=%s round=%d desc=%s",
            alarm.alarm_type,
            alarm.severity,
            alarm.governor_id or "league-wide",
            alarm.season_id,
            alarm.round_number,
            alarm.description,
        )

        if event_bus:
            await event_bus.publish(
                "joy.alarm",
                {
                    "alarm_type": alarm.alarm_type,
                    "severity": alarm.severity,
                    "governor_id": alarm.governor_id,
                    "description": alarm.description,
                    "recommended_action": alarm.recommended_action,
                    "round_number": alarm.round_number,
                    "season_id": alarm.season_id,
                    "details": alarm.details,
                },
            )

    if alarms:
        logger.info(
            "joy_alarms_summary season=%s round=%d total=%d types=%s",
            season_id,
            round_number,
            len(alarms),
            ", ".join(sorted({a.alarm_type for a in alarms})),
        )
    else:
        logger.info(
            "joy_alarms_clear season=%s round=%d",
            season_id,
            round_number,
        )

    return alarms
