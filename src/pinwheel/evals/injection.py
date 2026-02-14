"""Injection classification storage and retrieval.

Stores classification results from the pre-flight prompt injection classifier
as eval results (eval_type="injection_classification"). No private report
content is stored -- only the classification outcome and a truncated preview
of the proposal text.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pinwheel.ai.classifier import ClassificationResult
from pinwheel.evals.models import InjectionClassification

if TYPE_CHECKING:
    from pinwheel.db.repository import Repository

# Maximum length of proposal text stored in the preview
_PREVIEW_MAX_LENGTH = 120


async def store_injection_classification(
    repo: Repository,
    season_id: str,
    proposal_text: str,
    result: ClassificationResult,
    governor_id: str = "",
    source: str = "api",
) -> InjectionClassification:
    """Persist an injection classification as an eval result.

    Uses the existing EvalResultRow table with eval_type="injection_classification".
    The score field holds the classifier confidence. The details_json holds the
    full InjectionClassification model data.

    Args:
        repo: Database repository.
        season_id: Current season ID.
        proposal_text: The raw proposal text (truncated for storage).
        result: ClassificationResult from the classifier.
        governor_id: Governor who submitted the proposal.
        source: Where the classification happened ("api", "discord_bot", "discord_views").

    Returns:
        The InjectionClassification model that was stored.
    """
    blocked = result.classification == "injection" and result.confidence > 0.8
    preview = proposal_text[:_PREVIEW_MAX_LENGTH]

    classification = InjectionClassification(
        proposal_text_preview=preview,
        classification=result.classification,
        confidence=result.confidence,
        reason=result.reason,
        governor_id=governor_id,
        source=source,
        blocked=blocked,
    )

    await repo.store_eval_result(
        season_id=season_id,
        round_number=0,
        eval_type="injection_classification",
        eval_subtype=result.classification,
        score=result.confidence,
        details_json=classification.model_dump(mode="json"),
    )

    return classification


async def get_injection_classifications(
    repo: Repository,
    season_id: str,
    limit: int = 50,
) -> list[InjectionClassification]:
    """Retrieve recent injection classifications for a season.

    Returns InjectionClassification models ordered by most recent first,
    capped at the given limit.
    """
    results = await repo.get_eval_results(
        season_id=season_id,
        eval_type="injection_classification",
    )

    classifications: list[InjectionClassification] = []
    for row in results[:limit]:
        details = row.details_json or {}
        classifications.append(InjectionClassification(**details))

    return classifications
