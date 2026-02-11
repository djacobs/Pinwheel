"""Governance models — Proposals, Votes, and the append-only event store.

See docs/GLOSSARY.md: Proposal, Amendment, Vote, Window, Governor.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

GovernanceEventType = Literal[
    "proposal.submitted",
    "proposal.confirmed",
    "proposal.cancelled",
    "proposal.amended",
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
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    governor_id: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class Proposal(BaseModel):
    """A natural-language rule change submitted by a Governor."""

    id: str
    governor_id: str
    team_id: str
    raw_text: str
    ai_interpretation: str = ""
    tier: int = Field(default=1, ge=1, le=7)
    status: Literal[
        "draft", "submitted", "confirmed", "voting", "passed", "failed", "cancelled"
    ] = "draft"
    window_id: str = ""


class Vote(BaseModel):
    """A Governor's vote on a Proposal."""

    proposal_id: str
    governor_id: str
    vote: Literal["yes", "no"]
    weight: float = 1.0
    boost_used: bool = False


class Amendment(BaseModel):
    """A modification to an active Proposal."""

    proposal_id: str
    governor_id: str
    amendment_text: str
    new_interpretation: str = ""
