"""Mirror models — AI-generated reflections.

See docs/product/GLOSSARY.md: Mirror.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

MirrorType = Literal[
    "simulation",
    "governance",
    "private",
    "tiebreaker",
    "series",
    "season",
    "offseason",
    "state_of_the_league",
]


class Mirror(BaseModel):
    """An AI-generated reflection on gameplay or governance patterns. Never prescriptive."""

    id: str
    mirror_type: MirrorType
    round_number: int = 0
    team_id: str = ""
    governor_id: str = ""  # Only for private mirrors
    content: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MirrorUpdate(BaseModel):
    """SSE payload when a mirror is delivered."""

    mirror_id: str
    mirror_type: MirrorType
    round_number: int
    excerpt: str = ""
