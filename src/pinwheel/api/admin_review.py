"""Admin proposal review queue -- GET /admin/review.

Shows proposals flagged for admin review (Tier 5+ or confidence < 0.5),
with approve/veto actions. Also shows injection-flagged proposals.
Admin-gated via PINWHEEL_ADMIN_DISCORD_ID or accessible in dev without OAuth.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from pinwheel.api.deps import RepoDep
from pinwheel.auth.deps import OptionalUser, admin_auth_context, check_admin_access
from pinwheel.config import PROJECT_ROOT

router = APIRouter(prefix="/admin", tags=["admin"])

templates = Jinja2Templates(directory=str(PROJECT_ROOT / "templates"))


async def _get_active_season_id(repo: RepoDep) -> str | None:
    """Get the active season ID (most recent non-terminal)."""
    row = await repo.get_active_season()
    return row.id if row else None


async def _build_review_queue(
    repo: RepoDep,
    season_id: str,
) -> list[dict]:
    """Build the proposal review queue from governance events.

    Finds proposals flagged for review that haven't been cleared or vetoed.
    Returns a list of proposal dicts with all relevant metadata.
    """
    # Get all flagged proposals
    flagged_events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["proposal.flagged_for_review"],
    )

    # Get cleared/vetoed proposals to filter them out
    resolved_events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["proposal.review_cleared", "proposal.vetoed"],
    )
    resolved_ids = {e.aggregate_id for e in resolved_events}

    # Get proposal outcomes (passed/failed) -- these are also resolved
    outcome_events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["proposal.passed", "proposal.failed"],
    )
    outcome_map: dict[str, str] = {}
    for e in outcome_events:
        pid = e.payload.get("proposal_id", e.aggregate_id)
        outcome_map[pid] = "passed" if e.event_type == "proposal.passed" else "failed"

    queue: list[dict] = []
    for e in flagged_events:
        proposal_id = e.aggregate_id
        p_data = e.payload

        # Determine status: pending, cleared, vetoed, passed, failed
        if proposal_id in resolved_ids:
            status = "resolved"
        elif proposal_id in outcome_map:
            status = outcome_map[proposal_id]
        else:
            status = "pending"

        # Extract interpretation details
        interp = p_data.get("interpretation") or {}
        confidence = interp.get("confidence", 0.0)
        parameter = interp.get("parameter", "")
        new_value = interp.get("new_value")
        impact = interp.get("impact_analysis", "")
        injection_flagged = interp.get("injection_flagged", False)

        queue.append({
            "id": proposal_id,
            "governor_id": p_data.get("governor_id", ""),
            "raw_text": p_data.get("raw_text", ""),
            "sanitized_text": p_data.get("sanitized_text", ""),
            "tier": p_data.get("tier", 5),
            "status": status,
            "confidence": confidence,
            "parameter": parameter,
            "new_value": new_value,
            "impact_analysis": impact,
            "injection_flagged": injection_flagged,
            "created_at": e.created_at,
        })

    # Sort pending first, then by creation time (newest first)
    def _sort_key(p: dict) -> tuple[int, float]:
        ts = (p.get("created_at") or e.created_at).timestamp()
        return (0 if p["status"] == "pending" else 1, -ts)

    queue.sort(key=_sort_key)
    return queue


@router.get("/review", response_class=HTMLResponse)
async def admin_review(request: Request, repo: RepoDep, current_user: OptionalUser) -> HTMLResponse:
    """Admin proposal review queue.

    Shows proposals flagged for admin review. In dev mode without OAuth,
    the page is accessible to support local testing.
    """
    if denied := check_admin_access(current_user, request):
        return denied

    season_id = await _get_active_season_id(repo)
    queue: list[dict] = []
    pending_count = 0
    total_flagged = 0

    if season_id:
        queue = await _build_review_queue(repo, season_id)
        total_flagged = len(queue)
        pending_count = sum(1 for p in queue if p["status"] == "pending")

    # Also get injection classifications for context
    from pinwheel.evals.injection import get_injection_classifications

    injection_classifications: list[dict] = []
    if season_id:
        raw_classifications = await get_injection_classifications(repo, season_id, limit=10)
        for c in raw_classifications:
            if c.classification in ("injection", "suspicious"):
                injection_classifications.append({
                    "preview": c.proposal_text_preview,
                    "classification": c.classification,
                    "confidence": c.confidence,
                    "reason": c.reason,
                    "source": c.source,
                    "blocked": c.blocked,
                    "created_at": c.created_at.strftime("%Y-%m-%d %H:%M"),
                })

    return templates.TemplateResponse(
        request,
        "pages/admin_review.html",
        {
            "active_page": "review",
            "queue": queue,
            "pending_count": pending_count,
            "total_flagged": total_flagged,
            "injection_classifications": injection_classifications,
            **admin_auth_context(request, current_user),
        },
    )
