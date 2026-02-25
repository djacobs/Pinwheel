"""Codegen models — AI-generated code effects, council reviews, trust levels.

Phase 6 of the abstract game spine. These models describe the lifecycle of
proposals that require generated Python code: trust levels, council review
verdicts, and the immutable code artifact stored alongside the proposal.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class CodegenTrustLevel(StrEnum):
    """Progressive trust levels for generated code.

    Higher levels get access to more powerful APIs in the sandbox.
    """

    NUMERIC = "numeric"       # Level 1: Can only return modified numbers
    STATE = "state"           # Level 2: Can read/write MetaStore
    FLOW = "flow"             # Level 3: Can block actions, inject narrative
    STRUCTURE = "structure"   # Level 4: Can modify GameDefinition


class ReviewVerdict(BaseModel):
    """Result from a single council reviewer."""

    reviewer: str                   # "security", "gameplay", "adversarial"
    verdict: str                    # "APPROVE" or "REJECT"
    rationale: str = ""             # Why — shown to admin on rejection
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    raw_response: dict = Field(default_factory=dict)


class CouncilReview(BaseModel):
    """Aggregate result from the council of LLMs."""

    proposal_id: str
    code_hash: str                  # SHA-256 of the generated code
    reviews: list[ReviewVerdict] = Field(default_factory=list)
    consensus: bool = False         # All three approved?
    flagged_for_admin: bool = False  # At least one rejected?
    flag_reasons: list[str] = Field(default_factory=list)
    reviewed_at: str = ""           # ISO timestamp
    cost_tokens: int = 0            # Total tokens across all council calls


class CodegenEffectSpec(BaseModel):
    """Extension of EffectSpec for codegen effects.

    Stored alongside the proposal in the governance event store.
    The code string is immutable once approved — no runtime modification.
    """

    code: str                       # The generated Python function body
    code_hash: str                  # SHA-256 for integrity verification
    trust_level: CodegenTrustLevel
    council_review: CouncilReview
    generator_model: str = ""
    generator_prompt_hash: str = ""
    hook_points: list[str] = Field(default_factory=list)
    description: str = ""
    example_output: str = ""

    # Operational
    enabled: bool = True
    disabled_reason: str = ""
    execution_count: int = 0
    error_count: int = 0
    last_error: str = ""
    avg_execution_ms: float = 0.0
