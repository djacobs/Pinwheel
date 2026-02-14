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
    "window.opened",
    "window.closed",
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


class GovernanceWindow(BaseModel):
    """A time-bounded governance period between simulation rounds."""

    id: str
    season_id: str
    round_number: int
    status: Literal["open", "closed"] = "open"
    opened_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    closed_at: datetime | None = None
    proposals_count: int = 0
    votes_count: int = 0


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
