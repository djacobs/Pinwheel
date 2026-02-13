"""Behavioral shift detection (S.2a).

For each governor who got a private report, compare this round's governance
actions to a rolling baseline. Never reads ReportRow.content — only queries
GovernanceEventRow and ReportRow.governor_id.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pinwheel.evals.models import BehavioralShiftResult

if TYPE_CHECKING:
    from pinwheel.db.repository import Repository


async def get_governor_action_count(
    repo: Repository,
    season_id: str,
    governor_id: str,
    round_number: int,
) -> int:
    """Count governance actions (proposals + votes) for a governor in a specific round."""
    events = await repo.get_events_by_type_and_governor(
        season_id=season_id,
        governor_id=governor_id,
        event_types=["proposal.submitted", "vote.cast"],
    )
    return sum(1 for e in events if e.round_number == round_number)


async def compute_baseline(
    repo: Repository,
    season_id: str,
    governor_id: str,
    current_round: int,
    window: int = 3,
) -> float:
    """Compute rolling average of governance actions over previous rounds."""
    events = await repo.get_events_by_type_and_governor(
        season_id=season_id,
        governor_id=governor_id,
        event_types=["proposal.submitted", "vote.cast"],
    )

    start_round = max(1, current_round - window)
    counts = []
    for rn in range(start_round, current_round):
        count = sum(1 for e in events if e.round_number == rn)
        counts.append(count)

    if not counts:
        return 0.0
    return sum(counts) / len(counts)


async def detect_behavioral_shift(
    repo: Repository,
    season_id: str,
    governor_id: str,
    round_number: int,
    threshold: float = 1.5,
) -> BehavioralShiftResult:
    """Detect if a governor's actions shifted after receiving a private report.

    A 'shift' means this round's action count differs from baseline by more
    than the threshold ratio. Never reads report content.
    """
    actions = await get_governor_action_count(repo, season_id, governor_id, round_number)
    baseline = await compute_baseline(repo, season_id, governor_id, round_number)

    shifted = False
    if baseline > 0:
        shifted = abs(actions - baseline) / baseline >= threshold
    elif actions > 0:
        shifted = True  # Going from zero actions to any action is a shift

    return BehavioralShiftResult(
        governor_id=governor_id,
        round_number=round_number,
        shifted=shifted,
        actions_this_round=actions,
        baseline_avg=baseline,
    )


async def compute_report_impact_rate(
    repo: Repository,
    season_id: str,
    round_number: int,
) -> float:
    """Compute Report Impact Rate = shifted / total governors with private reports.

    Never reads report content — only checks governor_id on ReportRow.
    """
    reports = await repo.get_reports_for_round(season_id, round_number, report_type="private")
    governor_ids = {m.governor_id for m in reports if m.governor_id}

    if not governor_ids:
        return 0.0

    shifted_count = 0
    for gov_id in governor_ids:
        result = await detect_behavioral_shift(repo, season_id, gov_id, round_number)
        if result.shifted:
            shifted_count += 1

    return shifted_count / len(governor_ids)
