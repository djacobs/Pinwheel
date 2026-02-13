"""Report models — AI-generated reports.

See docs/GLOSSARY.md: Report.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

ReportType = Literal[
    "simulation",
    "governance",
    "private",
    "tiebreaker",
    "series",
    "season",
    "offseason",
    "state_of_the_league",
]


class Report(BaseModel):
    """An AI-generated report on gameplay or governance patterns. Never prescriptive."""

    id: str
    report_type: ReportType
    round_number: int = 0
    team_id: str = ""
    governor_id: str = ""  # Only for private reports
    content: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ReportUpdate(BaseModel):
    """SSE payload when a report is delivered."""

    report_id: str
    report_type: ReportType
    round_number: int
    excerpt: str = ""
