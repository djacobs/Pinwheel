"""A/B report comparison (M.2).

Generate two prompt variants for the same input. For private reports,
content is None in review context (privacy enforcement). Track win rates
by prompt version.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from pinwheel.evals.grounding import GroundingContext, check_grounding
from pinwheel.evals.models import ABComparison, ABVariant
from pinwheel.evals.prescriptive import scan_prescriptive

if TYPE_CHECKING:
    from pinwheel.db.repository import Repository


def build_variant(
    report_id: str,
    report_type: str,
    prompt_version: str,
    content: str,
    context: GroundingContext | None = None,
) -> ABVariant:
    """Build an ABVariant, stripping content for private reports."""
    grounding_score = 0.0
    if context:
        result = check_grounding(content, context, report_id, report_type)
        grounding_score = result.entities_found / max(result.entities_expected, 1)

    presc = scan_prescriptive(content, report_id, report_type)

    return ABVariant(
        variant="A" if "A" in prompt_version else "B",
        report_id=report_id,
        report_type=report_type,
        prompt_version=prompt_version,
        content=None if report_type == "private" else content,
        grounding_score=grounding_score,
        prescriptive_count=presc.prescriptive_count,
        length=len(content),
    )


def compare_variants(
    variant_a: ABVariant,
    variant_b: ABVariant,
) -> ABComparison:
    """Compare two variants. For private reports, only structural metrics matter."""
    comparison_id = str(uuid.uuid4())

    # Score each variant
    score_a = 0.0
    score_b = 0.0

    # Lower prescriptive count is better
    if variant_a.prescriptive_count < variant_b.prescriptive_count:
        score_a += 1
    elif variant_b.prescriptive_count < variant_a.prescriptive_count:
        score_b += 1

    # Higher grounding score is better
    if variant_a.grounding_score > variant_b.grounding_score:
        score_a += 1
    elif variant_b.grounding_score > variant_a.grounding_score:
        score_b += 1

    # Prefer reasonable length (not too short, not too long)
    ideal = 500
    diff_a = abs(variant_a.length - ideal)
    diff_b = abs(variant_b.length - ideal)
    if diff_a < diff_b:
        score_a += 1
    elif diff_b < diff_a:
        score_b += 1

    if score_a > score_b:
        winner = "A"
    elif score_b > score_a:
        winner = "B"
    else:
        winner = "tie"

    return ABComparison(
        comparison_id=comparison_id,
        variant_a=variant_a,
        variant_b=variant_b,
        winner=winner,
    )


async def store_ab_comparison(
    repo: Repository,
    season_id: str,
    round_number: int,
    comparison: ABComparison,
) -> None:
    """Store an A/B comparison result."""
    await repo.store_eval_result(
        season_id=season_id,
        round_number=round_number,
        eval_type="ab_comparison",
        eval_subtype=comparison.variant_a.report_type,
        score=1.0 if comparison.winner == "A" else (0.0 if comparison.winner == "B" else 0.5),
        details_json={
            "comparison_id": comparison.comparison_id,
            "winner": comparison.winner,
            "variant_a_prompt": comparison.variant_a.prompt_version,
            "variant_b_prompt": comparison.variant_b.prompt_version,
            "variant_a_grounding": comparison.variant_a.grounding_score,
            "variant_b_grounding": comparison.variant_b.grounding_score,
            "variant_a_prescriptive": comparison.variant_a.prescriptive_count,
            "variant_b_prescriptive": comparison.variant_b.prescriptive_count,
        },
    )


async def get_ab_win_rates(
    repo: Repository,
    season_id: str,
    round_number: int | None = None,
) -> dict:
    """Get A/B win rates across all comparisons."""
    results = await repo.get_eval_results(
        season_id, eval_type="ab_comparison", round_number=round_number
    )
    if not results:
        return {"total": 0, "a_wins": 0, "b_wins": 0, "ties": 0}

    a_wins = sum(1 for r in results if (r.details_json or {}).get("winner") == "A")
    b_wins = sum(1 for r in results if (r.details_json or {}).get("winner") == "B")
    ties = sum(1 for r in results if (r.details_json or {}).get("winner") == "tie")

    return {"total": len(results), "a_wins": a_wins, "b_wins": b_wins, "ties": ties}
