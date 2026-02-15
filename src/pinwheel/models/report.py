"""Report models — AI-generated reports.

See docs/product/GLOSSARY.md: Report.
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
    "impact_validation",
    "leverage",
    "behavioral",
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


class SeasonMemorial(BaseModel):
    """Permanent memorial record for a completed season.

    Combines computed data sections (statistical leaders, key moments,
    head-to-head records, rule timeline, awards) with placeholder fields
    for AI-generated narrative sections (filled in a later phase).
    """

    # AI-written narrative sections (placeholder — populated by AI generation phase)
    season_narrative: str = ""
    championship_recap: str = ""
    champion_profile: str = ""
    governance_legacy: str = ""

    # Computed data sections
    awards: list[dict] = Field(default_factory=list)
    statistical_leaders: dict = Field(default_factory=dict)
    key_moments: list[dict] = Field(default_factory=list)
    head_to_head: list[dict] = Field(default_factory=list)
    rule_timeline: list[dict] = Field(default_factory=list)

    # Metadata
    generated_at: str = ""
    model_used: str = ""
