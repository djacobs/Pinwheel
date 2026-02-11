"""Token economy models.

See docs/GLOSSARY.md: Token, Boost.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

TokenType = Literal["propose", "amend", "boost"]


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
