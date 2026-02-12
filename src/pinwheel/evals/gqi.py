"""Governance Quality Index (M.4) — composite metric.

Four sub-metrics, each weighted 25%:
- Proposal Diversity: Shannon entropy of targeted parameters
- Participation Breadth: Inverted Gini of per-governor action counts
- Consequence Awareness: Keyword overlap between PUBLIC mirror content and next-window proposals
- Vote Deliberation: Normalized time-to-vote within window
"""

from __future__ import annotations

import math
from collections import Counter
from typing import TYPE_CHECKING

from pinwheel.evals.models import GQIResult

if TYPE_CHECKING:
    from pinwheel.db.repository import Repository


def _shannon_entropy(counts: list[int]) -> float:
    """Shannon entropy of a distribution. Returns 0-1 normalized."""
    total = sum(counts)
    if total == 0:
        return 0.0
    probs = [c / total for c in counts if c > 0]
    raw = -sum(p * math.log2(p) for p in probs)
    max_entropy = math.log2(len(probs)) if len(probs) > 1 else 1.0
    return raw / max_entropy if max_entropy > 0 else 0.0


def _gini_coefficient(values: list[float]) -> float:
    """Gini coefficient. 0 = perfect equality, 1 = perfect inequality."""
    if not values or all(v == 0 for v in values):
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    total = sum(sorted_vals)
    if total == 0:
        return 0.0
    cumsum = 0.0
    gini_sum = 0.0
    for i, v in enumerate(sorted_vals):
        cumsum += v
        gini_sum += (2 * (i + 1) - n - 1) * v
    return gini_sum / (n * total)


async def compute_proposal_diversity(
    repo: Repository,
    season_id: str,
    round_number: int,
) -> float:
    """Shannon entropy of targeted parameters in proposals this round."""
    events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["proposal.submitted"],
    )
    params: list[str] = []
    for e in events:
        if e.round_number == round_number:
            interp = (e.payload or {}).get("interpretation") or {}
            param = interp.get("parameter")
            if param:
                params.append(param)

    if not params:
        return 0.0
    counter = Counter(params)
    return _shannon_entropy(list(counter.values()))


async def compute_participation_breadth(
    repo: Repository,
    season_id: str,
    round_number: int,
) -> float:
    """Inverted Gini of per-governor action counts. 1 = equal, 0 = one person does everything."""
    events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["proposal.submitted", "vote.cast"],
    )
    gov_counts: Counter[str] = Counter()
    for e in events:
        if e.round_number == round_number and e.governor_id:
            gov_counts[e.governor_id] += 1

    if not gov_counts:
        return 0.0
    gini = _gini_coefficient(list(gov_counts.values()))
    return 1.0 - gini


async def compute_consequence_awareness(
    repo: Repository,
    season_id: str,
    round_number: int,
) -> float:
    """Keyword overlap between PUBLIC mirror content and next proposals.

    Private mirrors are excluded — only reads simulation and governance mirrors.
    """
    # Get public mirrors from this round
    sim_mirrors = await repo.get_mirrors_for_round(season_id, round_number, "simulation")
    gov_mirrors = await repo.get_mirrors_for_round(season_id, round_number, "governance")
    mirror_words: set[str] = set()
    for m in sim_mirrors + gov_mirrors:
        mirror_words.update(m.content.lower().split())

    if not mirror_words:
        return 0.0

    # Get proposals from the next round
    next_round = round_number + 1
    events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["proposal.submitted"],
    )
    proposal_words: set[str] = set()
    for e in events:
        if e.round_number == next_round:
            raw_text = (e.payload or {}).get("raw_text", "")
            proposal_words.update(raw_text.lower().split())

    if not proposal_words:
        return 0.0

    # Filter to meaningful words (> 3 chars)
    mirror_meaningful = {w for w in mirror_words if len(w) > 3}
    proposal_meaningful = {w for w in proposal_words if len(w) > 3}

    if not mirror_meaningful or not proposal_meaningful:
        return 0.0

    overlap = mirror_meaningful & proposal_meaningful
    return len(overlap) / len(proposal_meaningful)


async def compute_vote_deliberation(
    repo: Repository,
    season_id: str,
    round_number: int,
    window_duration_seconds: float = 120.0,
) -> float:
    """Normalized time-to-vote within window. Higher = more deliberation."""
    events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["vote.cast", "window.opened"],
    )

    window_opened_at = None
    for e in events:
        if e.event_type == "window.opened" and e.round_number == round_number:
            window_opened_at = e.created_at
            break

    if not window_opened_at:
        return 0.5  # Default if no window

    vote_delays: list[float] = []
    for e in events:
        if e.event_type == "vote.cast" and e.round_number == round_number:
            delay = (e.created_at - window_opened_at).total_seconds()
            if window_duration_seconds > 0:
                normalized = min(delay / window_duration_seconds, 1.0)
            else:
                normalized = 0.5
            vote_delays.append(normalized)

    if not vote_delays:
        return 0.5
    return sum(vote_delays) / len(vote_delays)


async def compute_gqi(
    repo: Repository,
    season_id: str,
    round_number: int,
) -> GQIResult:
    """Compute the full Governance Quality Index."""
    diversity = await compute_proposal_diversity(repo, season_id, round_number)
    breadth = await compute_participation_breadth(repo, season_id, round_number)
    awareness = await compute_consequence_awareness(repo, season_id, round_number)
    deliberation = await compute_vote_deliberation(repo, season_id, round_number)

    composite = 0.25 * diversity + 0.25 * breadth + 0.25 * awareness + 0.25 * deliberation

    return GQIResult(
        season_id=season_id,
        round_number=round_number,
        proposal_diversity=diversity,
        participation_breadth=breadth,
        consequence_awareness=awareness,
        vote_deliberation=deliberation,
        composite=composite,
    )


async def store_gqi(
    repo: Repository,
    season_id: str,
    round_number: int,
    result: GQIResult,
) -> None:
    """Store GQI result."""
    await repo.store_eval_result(
        season_id=season_id,
        round_number=round_number,
        eval_type="gqi",
        score=result.composite,
        details_json=result.model_dump(mode="json"),
    )
