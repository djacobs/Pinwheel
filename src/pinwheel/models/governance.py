"""Governance models — Proposals, Votes, AI interpretations, and the event store.

See docs/product/GLOSSARY.md: Proposal, Amendment, Vote, Window, Governor.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

GovernanceEventType = Literal[
    "proposal.submitted",
    "proposal.confirmed",
    "proposal.cancelled",
    "proposal.amended",
    "proposal.pending_review",
    "proposal.rejected",
    "proposal.vetoed",
    "proposal.flagged_for_review",
    "proposal.first_tally_seen",
    "vote.cast",
    "vote.revealed",
    "proposal.passed",
    "proposal.failed",
    "rule.enacted",
    "rule.rolled_back",
    "token.regenerated",
    "token.spent",
    "trade.offered",
    "trade.accepted",
    "trade.rejected",
    "trade.expired",
    "effect.registered",
    "effect.expired",
    "effect.repealed",
]


class GovernanceEvent(BaseModel):
    """Append-only governance event. Source of truth for all governance state."""

    id: str
    event_type: GovernanceEventType
    aggregate_id: str
    aggregate_type: str
    season_id: str = ""
    round_number: int | None = None
    governor_id: str = ""
    team_id: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict = Field(default_factory=dict)


class RuleInterpretation(BaseModel):
    """AI's structured reading of a natural language proposal."""

    parameter: str | None = None
    new_value: int | float | bool | None = None
    old_value: int | float | bool | None = None
    impact_analysis: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    clarification_needed: bool = False
    injection_flagged: bool = False
    rejection_reason: str | None = None


class Proposal(BaseModel):
    """A natural-language rule change submitted by a Governor."""

    id: str
    season_id: str = ""
    governor_id: str
    team_id: str
    window_id: str = ""
    raw_text: str
    sanitized_text: str = ""
    interpretation: RuleInterpretation | None = None
    tier: int = Field(default=1, ge=1, le=7)
    token_cost: int = 1
    status: Literal[
        "draft",
        "submitted",
        "confirmed",
        "amended",
        "voting",
        "passed",
        "failed",
        "cancelled",
        "pending_review",
        "rejected",
    ] = "draft"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Amendment(BaseModel):
    """A modification to an active Proposal. Replaces the interpretation on the ballot."""

    id: str = ""
    proposal_id: str
    governor_id: str
    amendment_text: str
    new_interpretation: RuleInterpretation | None = None
    token_cost: int = 1
    status: Literal["submitted", "confirmed", "cancelled"] = "submitted"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Vote(BaseModel):
    """A Governor's vote on a Proposal. Hidden until window closes."""

    id: str = ""
    proposal_id: str
    governor_id: str
    team_id: str = ""
    vote: Literal["yes", "no"]
    weight: float = 1.0
    boost_used: bool = False
    cast_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class VoteTally(BaseModel):
    """Result of tallying votes for a single proposal."""

    proposal_id: str
    weighted_yes: float = 0.0
    weighted_no: float = 0.0
    total_weight: float = 0.0
    passed: bool = False
    threshold: float = 0.5
    yes_count: int = 0
    no_count: int = 0
    total_eligible: int = 0


# ---------------------------------------------------------------------------
# Proposal Effects System — structured effects beyond parameter changes
# ---------------------------------------------------------------------------

# Allowed primitive types for meta values (no arbitrary objects)
MetaValue = int | float | str | bool | None

EffectType = Literal[
    "parameter_change",
    "meta_mutation",
    "hook_callback",
    "narrative",
    "composite",
    "move_grant",
    "custom_mechanic",
]

EffectDuration = Literal[
    "permanent",
    "n_rounds",
    "one_game",
    "until_repealed",
]


class EffectSpec(BaseModel):
    """A single structured effect produced by AI interpretation of a proposal.

    Each proposal can produce multiple EffectSpecs. Together they describe
    the mechanical consequences of the proposal passing.
    """

    effect_type: EffectType

    # parameter_change (backward compatible with RuleInterpretation)
    parameter: str | None = None
    new_value: int | float | bool | None = None
    old_value: int | float | bool | None = None

    # meta_mutation
    target_type: str | None = None  # "team", "hooper", "game", "season"
    target_selector: str | None = None  # "all", "winning_team", specific ID
    meta_field: str | None = None
    meta_value: MetaValue = None
    meta_operation: Literal["set", "increment", "decrement", "toggle"] = "set"

    # hook_callback
    hook_point: str | None = None
    condition: str | None = None  # Natural language condition
    action: str | None = None  # Natural language action
    action_code: dict[str, MetaValue | dict[str, MetaValue]] | None = None

    # narrative
    narrative_instruction: str | None = None

    # move_grant — grants a move to hoopers
    move_name: str | None = None
    move_trigger: str | None = None
    move_effect: str | None = None
    move_attribute_gate: dict[str, int] | None = None
    target_hooper_id: str | None = None  # specific hooper
    target_team_id: str | None = None  # all hoopers on a team

    # custom_mechanic — describes a mechanic that needs code implementation
    mechanic_description: str | None = None
    mechanic_hook_point: str | None = None
    mechanic_observable_behavior: str | None = None
    mechanic_implementation_spec: str | None = None

    # lifetime
    duration: EffectDuration = "permanent"
    duration_rounds: int | None = None

    description: str = ""


class ProposalInterpretation(BaseModel):
    """AI interpretation of a proposal as structured effects.

    Replaces RuleInterpretation for proposals that go beyond parameter tweaks.
    Backward compatible: a single parameter_change EffectSpec is equivalent
    to the old RuleInterpretation.
    """

    effects: list[EffectSpec] = Field(default_factory=list)
    impact_analysis: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    clarification_needed: bool = False
    injection_flagged: bool = False
    rejection_reason: str | None = None
    original_text_echo: str = ""

    def to_rule_interpretation(self) -> RuleInterpretation:
        """Convert to legacy RuleInterpretation for backward compatibility.

        Uses the first parameter_change effect if available.
        """
        for effect in self.effects:
            if effect.effect_type == "parameter_change" and effect.parameter:
                return RuleInterpretation(
                    parameter=effect.parameter,
                    new_value=effect.new_value,
                    old_value=effect.old_value,
                    impact_analysis=self.impact_analysis,
                    confidence=self.confidence,
                    clarification_needed=self.clarification_needed,
                    injection_flagged=self.injection_flagged,
                    rejection_reason=self.rejection_reason,
                )
        return RuleInterpretation(
            parameter=None,
            impact_analysis=self.impact_analysis,
            confidence=self.confidence,
            clarification_needed=self.clarification_needed,
            injection_flagged=self.injection_flagged,
            rejection_reason=self.rejection_reason,
        )

    @classmethod
    def from_rule_interpretation(
        cls,
        interp: RuleInterpretation,
        raw_text: str = "",
    ) -> ProposalInterpretation:
        """Convert a legacy RuleInterpretation to ProposalInterpretation."""
        effects: list[EffectSpec] = []
        if interp.parameter:
            effects.append(
                EffectSpec(
                    effect_type="parameter_change",
                    parameter=interp.parameter,
                    new_value=interp.new_value,
                    old_value=interp.old_value,
                    description=interp.impact_analysis,
                )
            )
        return cls(
            effects=effects,
            impact_analysis=interp.impact_analysis,
            confidence=interp.confidence,
            clarification_needed=interp.clarification_needed,
            injection_flagged=interp.injection_flagged,
            rejection_reason=interp.rejection_reason,
            original_text_echo=raw_text,
        )
