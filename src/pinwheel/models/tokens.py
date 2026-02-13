"""Token economy models.

See docs/GLOSSARY.md: Token, Boost.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

TokenType = Literal["propose", "amend", "boost"]

HooperTradeStatus = Literal["proposed", "approved", "rejected", "expired"]

# Backward-compatible alias
AgentTradeStatus = HooperTradeStatus


class TokenBalance(BaseModel):
    """A Governor's current token holdings. Derived from events, not mutable state."""

    governor_id: str
    season_id: str = ""
    propose: int = Field(default=2, ge=0)
    amend: int = Field(default=2, ge=0)
    boost: int = Field(default=2, ge=0)


class Trade(BaseModel):
    """A token trade between two Governors."""

    id: str
    from_governor: str
    to_governor: str
    from_team_id: str = ""
    to_team_id: str = ""
    offered_type: TokenType
    offered_amount: int = Field(ge=1)
    requested_type: TokenType
    requested_amount: int = Field(ge=1)
    status: Literal["offered", "accepted", "rejected", "expired"] = "offered"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None


class HooperTrade(BaseModel):
    """A trade of hoopers between two teams, requiring both teams' governors to vote."""

    id: str
    from_team_id: str
    to_team_id: str
    offered_hooper_ids: list[str]  # hoopers moving from_team → to_team
    requested_hooper_ids: list[str]  # hoopers moving to_team → from_team
    offered_hooper_names: list[str] = Field(default_factory=list)
    requested_hooper_names: list[str] = Field(default_factory=list)
    status: HooperTradeStatus = "proposed"
    proposed_by: str  # governor discord_id who proposed
    votes: dict[str, str] = Field(default_factory=dict)  # governor_id → "yes"/"no"
    required_voters: list[str] = Field(default_factory=list)  # all governor IDs on both teams
    from_team_voters: list[str] = Field(default_factory=list)  # governor IDs on the offering team
    to_team_voters: list[str] = Field(default_factory=list)  # governor IDs on the receiving team
    from_team_name: str = ""
    to_team_name: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Backward-compatible aliases
    @property
    def offered_agent_ids(self) -> list[str]:
        return self.offered_hooper_ids

    @property
    def requested_agent_ids(self) -> list[str]:
        return self.requested_hooper_ids

    @property
    def offered_agent_names(self) -> list[str]:
        return self.offered_hooper_names

    @property
    def requested_agent_names(self) -> list[str]:
        return self.requested_hooper_names


# Backward-compatible alias
AgentTrade = HooperTrade
