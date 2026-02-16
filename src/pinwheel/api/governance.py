"""Governance API endpoints — proposals, votes, windows, rule history."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from pinwheel.api.deps import RepoDep
from pinwheel.models.governance import Proposal
from pinwheel.models.rules import RuleSet

router = APIRouter(prefix="/api/governance", tags=["governance"])


# --- Endpoints ---


@router.get("/proposals")
async def api_list_proposals(season_id: str, repo: RepoDep) -> dict:
    """List all proposals for a season."""
    events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["proposal.submitted"],
    )
    proposals = []
    for e in events:
        proposal_data = e.payload
        if "id" in proposal_data and "raw_text" in proposal_data:
            proposals.append(Proposal(**proposal_data))
    return {"data": [p.model_dump(mode="json") for p in proposals]}


@router.get("/rules/current")
async def api_current_rules(season_id: str, repo: RepoDep) -> dict:
    """Get the current ruleset for a season."""
    season = await repo.get_season(season_id)
    if not season:
        raise HTTPException(status_code=404, detail="Season not found")

    ruleset = RuleSet(**(season.current_ruleset or {}))
    defaults = RuleSet()

    # Highlight non-default values
    changes = {}
    for param in RuleSet.model_fields:
        current = getattr(ruleset, param)
        default = getattr(defaults, param)
        if current != default:
            changes[param] = {"current": current, "default": default}

    return {
        "data": {
            "ruleset": ruleset.model_dump(),
            "changes_from_default": changes,
        }
    }


@router.get("/rules/history")
async def api_rule_history(season_id: str, repo: RepoDep) -> dict:
    """Get all rule changes for a season."""
    events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["rule.enacted"],
    )
    return {"data": [e.payload for e in events]}
