"""Grounding check (S.2b) — entity reference validation.

Builds a GroundingContext from known entities (team names, agent names, rule params),
then checks whether mirror content references them. Returns pass/fail + counts.
Content is never stored in the result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pinwheel.evals.models import GroundingResult


@dataclass
class GroundingContext:
    """Known entities that mirrors should reference."""

    team_names: list[str] = field(default_factory=list)
    agent_names: list[str] = field(default_factory=list)
    rule_params: list[str] = field(default_factory=list)

    @property
    def all_entities(self) -> list[str]:
        return self.team_names + self.agent_names + self.rule_params


def build_grounding_context(
    teams: list[dict],
    agents: list[dict],
    ruleset: dict | None = None,
) -> GroundingContext:
    """Build grounding context from database data."""
    team_names = [t.get("name", "") for t in teams if t.get("name")]
    agent_names = [a.get("name", "") for a in agents if a.get("name")]
    rule_params = list((ruleset or {}).keys())
    return GroundingContext(
        team_names=team_names,
        agent_names=agent_names,
        rule_params=rule_params,
    )


def check_grounding(
    content: str,
    context: GroundingContext,
    mirror_id: str,
    mirror_type: str,
) -> GroundingResult:
    """Check how many known entities a mirror references. Content never stored."""
    entities = context.all_entities
    if not entities:
        return GroundingResult(
            mirror_id=mirror_id,
            mirror_type=mirror_type,
            entities_expected=0,
            entities_found=0,
            grounded=True,
        )

    found = 0
    for entity in entities:
        if not entity:
            continue
        # Case-insensitive search for entity name
        if re.search(re.escape(entity), content, re.IGNORECASE):
            found += 1

    return GroundingResult(
        mirror_id=mirror_id,
        mirror_type=mirror_type,
        entities_expected=len(entities),
        entities_found=found,
        grounded=found > 0,
    )
