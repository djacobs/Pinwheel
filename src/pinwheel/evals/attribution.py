"""Attribution analysis (M.3) — treatment/control report delivery.

Randomly assigns governors to treatment (immediate report) / control (delayed report).
Compares behavioral shift rates between groups. Reports aggregate delta only.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from pinwheel.evals.behavioral import detect_behavioral_shift

if TYPE_CHECKING:
    from pinwheel.db.repository import Repository


def assign_treatment_control(
    governor_ids: list[str],
    treatment_ratio: float = 0.5,
    seed: int | None = None,
) -> tuple[list[str], list[str]]:
    """Randomly split governors into treatment and control groups."""
    rng = random.Random(seed)
    shuffled = list(governor_ids)
    rng.shuffle(shuffled)
    split = int(len(shuffled) * treatment_ratio)
    return shuffled[:split], shuffled[split:]


async def compute_attribution(
    repo: Repository,
    season_id: str,
    round_number: int,
    treatment_ids: list[str],
    control_ids: list[str],
) -> dict:
    """Compare behavioral shift rates between treatment and control groups.

    Returns aggregate delta only — no individual governor data.
    """
    treatment_shifts = 0
    for gov_id in treatment_ids:
        result = await detect_behavioral_shift(repo, season_id, gov_id, round_number)
        if result.shifted:
            treatment_shifts += 1

    control_shifts = 0
    for gov_id in control_ids:
        result = await detect_behavioral_shift(repo, season_id, gov_id, round_number)
        if result.shifted:
            control_shifts += 1

    treatment_rate = treatment_shifts / len(treatment_ids) if treatment_ids else 0.0
    control_rate = control_shifts / len(control_ids) if control_ids else 0.0
    delta = treatment_rate - control_rate

    return {
        "treatment_count": len(treatment_ids),
        "control_count": len(control_ids),
        "treatment_shift_rate": treatment_rate,
        "control_shift_rate": control_rate,
        "delta": delta,
        "round_number": round_number,
    }


async def store_attribution_result(
    repo: Repository,
    season_id: str,
    round_number: int,
    result: dict,
) -> None:
    """Store attribution analysis result."""
    await repo.store_eval_result(
        season_id=season_id,
        round_number=round_number,
        eval_type="attribution",
        score=result["delta"],
        details_json=result,
    )
