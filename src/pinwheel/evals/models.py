"""Pydantic models for the evals framework.

All eval results are stored via EvalResultRow in the database.
Privacy: no model here stores or returns private mirror content.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


class GroundingResult(BaseModel):
    """Result of grounding check (S.2b): does a mirror reference real entities?"""

    mirror_id: str
    mirror_type: str
    entities_expected: int = 0
    entities_found: int = 0
    grounded: bool = False


class PrescriptiveResult(BaseModel):
    """Result of prescriptive language scan (S.2c): count of directive phrases."""

    mirror_id: str
    mirror_type: str
    prescriptive_count: int = 0
    flagged: bool = False


class BehavioralShiftResult(BaseModel):
    """Result of behavioral shift detection (S.2a) for one governor."""

    governor_id: str
    round_number: int
    shifted: bool = False
    actions_this_round: int = 0
    baseline_avg: float = 0.0


class RubricScore(BaseModel):
    """Manual rubric score for a PUBLIC mirror only.

    mirror_type is restricted to simulation|governance — Pydantic rejects 'private'.
    """

    mirror_id: str
    mirror_type: Literal["simulation", "governance"]
    scorer_id: str = ""
    accuracy: int = Field(default=3, ge=1, le=5)
    insight: int = Field(default=3, ge=1, le=5)
    tone: int = Field(default=3, ge=1, le=5)
    conciseness: int = Field(default=3, ge=1, le=5)
    non_prescriptive: int = Field(default=3, ge=1, le=5)


class GoldenCase(BaseModel):
    """A single golden dataset test case."""

    id: str
    mirror_type: Literal["simulation", "governance", "private"]
    input_data: dict = Field(default_factory=dict)
    expected_patterns: list[str] = Field(default_factory=list)
    structural_only: bool = False
    min_length: int = 50
    max_length: int = 2000


class ABVariant(BaseModel):
    """One variant in an A/B mirror comparison.

    content is None for private mirrors in review context (privacy enforcement).
    """

    variant: Literal["A", "B"]
    mirror_id: str
    mirror_type: str
    prompt_version: str = ""
    content: str | None = None
    grounding_score: float = 0.0
    prescriptive_count: int = 0
    length: int = 0


class ABComparison(BaseModel):
    """A/B comparison result: which variant won?"""

    comparison_id: str
    variant_a: ABVariant
    variant_b: ABVariant
    winner: Literal["A", "B", "tie"] = "tie"
    judge_notes: str = ""


class ScenarioFlag(BaseModel):
    """A flagged scenario for admin review."""

    flag_type: Literal[
        "blowout_game",
        "suspicious_unanimity",
        "governance_stagnation",
        "participation_collapse",
        "rule_backfire",
        "prescriptive_mirror",
    ]
    severity: Literal["info", "warning", "critical"] = "info"
    round_number: int = 0
    season_id: str = ""
    details: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RuleEvaluation(BaseModel):
    """AI-generated rule analysis (admin-only, expansive)."""

    season_id: str
    round_number: int
    suggested_experiments: list[str] = Field(default_factory=list)
    stale_parameters: list[str] = Field(default_factory=list)
    equilibrium_notes: str = ""
    flagged_concerns: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class GQIResult(BaseModel):
    """Governance Quality Index composite metric."""

    season_id: str
    round_number: int
    proposal_diversity: float = 0.0
    participation_breadth: float = 0.0
    consequence_awareness: float = 0.0
    vote_deliberation: float = 0.0
    composite: float = 0.0
