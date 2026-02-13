"""Manual rubric scoring (S.1) — for PUBLIC reports only.

RubricScore.report_type is Literal["simulation", "governance"] — Pydantic
rejects "private" at the type level, enforcing the privacy boundary.
"""

from __future__ import annotations

import csv
import io
from typing import TYPE_CHECKING

from pinwheel.evals.models import RubricScore

if TYPE_CHECKING:
    from pinwheel.db.repository import Repository

PASS_THRESHOLD = 3.0
DIMENSIONS = ["accuracy", "insight", "tone", "conciseness", "non_prescriptive"]


def score_average(rubric: RubricScore) -> float:
    """Compute average score across all rubric dimensions."""
    values = [getattr(rubric, d) for d in DIMENSIONS]
    return sum(values) / len(values)


async def score_report(
    repo: Repository,
    season_id: str,
    round_number: int,
    rubric: RubricScore,
) -> float:
    """Score a public report and store the result. Returns average score."""
    avg = score_average(rubric)
    await repo.store_eval_result(
        season_id=season_id,
        round_number=round_number,
        eval_type="rubric",
        eval_subtype=rubric.report_type,
        score=avg,
        details_json={
            "report_id": rubric.report_id,
            "report_type": rubric.report_type,
            "scorer_id": rubric.scorer_id,
            "accuracy": rubric.accuracy,
            "insight": rubric.insight,
            "tone": rubric.tone,
            "conciseness": rubric.conciseness,
            "non_prescriptive": rubric.non_prescriptive,
            "average": avg,
        },
    )
    return avg


async def get_rubric_summary(
    repo: Repository,
    season_id: str,
) -> dict:
    """Get per-dimension averages and pass rate for all rubric scores."""
    results = await repo.get_eval_results(season_id, eval_type="rubric")
    if not results:
        return {"count": 0, "averages": {}, "pass_rate": 0.0}

    totals = {d: 0.0 for d in DIMENSIONS}
    count = 0
    passing = 0

    for r in results:
        details = r.details_json or {}
        avg = details.get("average", 0.0)
        if avg >= PASS_THRESHOLD:
            passing += 1
        for d in DIMENSIONS:
            totals[d] += details.get(d, 0.0)
        count += 1

    averages = {d: totals[d] / count for d in DIMENSIONS} if count else {}
    return {
        "count": count,
        "averages": averages,
        "pass_rate": passing / count if count else 0.0,
    }


def export_rubric_csv(results: list[dict]) -> str:
    """Export rubric results to CSV string for offline analysis."""
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=["report_id", "report_type", "scorer_id"] + DIMENSIONS + ["average"],
    )
    writer.writeheader()
    for r in results:
        writer.writerow(r)
    return output.getvalue()
