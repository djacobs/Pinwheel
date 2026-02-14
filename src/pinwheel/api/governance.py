"""Governance API endpoints — proposals, votes, windows, rule history."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from pinwheel.ai.interpreter import interpret_proposal_mock
from pinwheel.api.deps import RepoDep
from pinwheel.core.governance import (
    cast_vote,
    compute_vote_weight,
    confirm_proposal,
    submit_proposal,
)
from pinwheel.models.governance import Proposal
from pinwheel.models.rules import RuleSet

router = APIRouter(prefix="/api/governance", tags=["governance"])


# --- Request Models ---


class SubmitProposalRequest(BaseModel):
    governor_id: str
    team_id: str
    season_id: str
    window_id: str
    raw_text: str


class CastVoteRequest(BaseModel):
    proposal_id: str
    governor_id: str
    team_id: str
    vote: str  # "yes" or "no"
    active_governors_on_team: int = 1
    boost_used: bool = False


class CloseWindowRequest(BaseModel):
    season_id: str
    round_number: int


# --- Endpoints ---


@router.post("/proposals")
async def api_submit_proposal(
    body: SubmitProposalRequest,
    repo: RepoDep,
    request: Request,
) -> dict:
    """Submit a new governance proposal."""
    # Get current ruleset from season
    season = await repo.get_season(body.season_id)
    if not season:
        raise HTTPException(status_code=404, detail="Season not found")
    ruleset = RuleSet(**(season.current_ruleset or {}))

    # Interpret via mock (swap to real AI when API key configured)
    settings = request.app.state.settings
    if settings.anthropic_api_key:
        from pinwheel.ai.classifier import classify_injection
        from pinwheel.ai.interpreter import interpret_proposal as interpret_ai
        from pinwheel.evals.injection import store_injection_classification

        # Pre-flight injection classification
        classification = await classify_injection(body.raw_text, settings.anthropic_api_key)

        # Store classification result for dashboard visibility
        await store_injection_classification(
            repo=repo,
            season_id=body.season_id,
            proposal_text=body.raw_text,
            result=classification,
            governor_id=body.governor_id,
            source="api",
        )

        if classification.classification == "injection" and classification.confidence > 0.8:
            from pinwheel.models.governance import RuleInterpretation as RI

            interpretation = RI(
                confidence=0.0,
                injection_flagged=True,
                rejection_reason=classification.reason,
                impact_analysis="Proposal flagged as potential prompt injection.",
            )
        else:
            interpretation = await interpret_ai(body.raw_text, ruleset, settings.anthropic_api_key)
            # Annotate suspicious proposals so the governor sees the warning
            if classification.classification == "suspicious":
                interpretation.impact_analysis = (
                    f"[Suspicious: {classification.reason}] " + interpretation.impact_analysis
                )
    else:
        interpretation = interpret_proposal_mock(body.raw_text, ruleset)

    proposal = await submit_proposal(
        repo=repo,
        governor_id=body.governor_id,
        team_id=body.team_id,
        season_id=body.season_id,
        window_id=body.window_id,
        raw_text=body.raw_text,
        interpretation=interpretation,
        ruleset=ruleset,
    )

    return {"data": proposal.model_dump(mode="json")}


@router.post("/proposals/{proposal_id}/confirm")
async def api_confirm_proposal(proposal_id: str, repo: RepoDep) -> dict:
    """Confirm AI interpretation of a proposal."""
    events = await repo.get_events_for_aggregate("proposal", proposal_id)
    if not events:
        raise HTTPException(status_code=404, detail="Proposal not found")

    payload = events[0].payload
    proposal = Proposal(**payload)
    proposal = await confirm_proposal(repo, proposal)
    return {"data": proposal.model_dump(mode="json")}


@router.post("/votes")
async def api_cast_vote(body: CastVoteRequest, repo: RepoDep) -> dict:
    """Cast a vote on a proposal."""
    events = await repo.get_events_for_aggregate("proposal", body.proposal_id)
    if not events:
        raise HTTPException(status_code=404, detail="Proposal not found")

    payload = events[0].payload
    proposal = Proposal(**payload)
    weight = compute_vote_weight(body.active_governors_on_team)

    vote = await cast_vote(
        repo=repo,
        proposal=proposal,
        governor_id=body.governor_id,
        team_id=body.team_id,
        vote_choice=body.vote,
        weight=weight,
        boost_used=body.boost_used,
    )

    return {"data": vote.model_dump(mode="json")}


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
