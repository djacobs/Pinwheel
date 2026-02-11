"""Token economy models.

See docs/GLOSSARY.md: Token, Boost.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

TokenType = Literal["PROPOSE", "AMEND", "BOOST"]


class TokenBalance(BaseModel):
    """A Governor's current token holdings."""

    governor_id: str
    propose: int = Field(default=1, ge=0)
    amend: int = Field(default=2, ge=0)
    boost: int = Field(default=3, ge=0)


class Trade(BaseModel):
    """A token trade between two Governors."""

    id: str
    from_governor: str
    to_governor: str
    offered_type: TokenType
    offered_amount: int = Field(ge=1)
    requested_type: TokenType
    requested_amount: int = Field(ge=1)
    status: Literal["offered", "accepted", "rejected", "expired"] = "offered"
