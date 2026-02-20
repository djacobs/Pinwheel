"""Admin roster route -- GET /admin/roster.

Shows all enrolled governors with team, tokens, proposals, and votes.
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


@router.get("/roster", response_class=HTMLResponse)
async def admin_roster(request: Request, repo: RepoDep, current_user: OptionalUser) -> HTMLResponse:
    """Admin roster -- table of all enrolled governors.

    Auth-gated: requires admin Discord ID match when OAuth is enabled.
    In dev mode without OAuth credentials the page is accessible to support
    local testing.
    """
    if denied := check_admin_access(current_user, request):
        return denied

    # Show ALL players, regardless of season enrollment
    all_players = await repo.get_all_players()
    governors: list[dict] = []

    # Token balances are scoped to the active season (current state).
    # Proposals and votes aggregate across ALL seasons (lifetime record).
    active_season = await repo.get_active_season()
    active_season_id = active_season.id if active_season else None
    all_seasons = await repo.get_all_seasons()

    for player in all_players:
        team = await repo.get_team(player.team_id) if player.team_id else None
        team_name = team.name if team else "Unassigned"
        team_color = team.color if team else "#888"

        propose = 0
        amend = 0
        boost = 0

        if active_season_id:
            from pinwheel.core.tokens import get_token_balance

            balance = await get_token_balance(repo, player.id, active_season_id)
            propose = balance.propose
            amend = balance.amend
            boost = balance.boost

        # Aggregate activity across all seasons
        proposals_submitted = 0
        proposals_passed = 0
        proposals_failed = 0
        proposals_pending = 0
        votes_cast = 0
        proposal_list: list[dict] = []
        pending_interpretations: list[dict] = []

        for season in all_seasons:
            activity = await repo.get_governor_activity(player.id, season.id)
            proposals_submitted += activity.get("proposals_submitted", 0)
            proposals_passed += activity.get("proposals_passed", 0)
            proposals_failed += activity.get("proposals_failed", 0)
            votes_cast += activity.get("votes_cast", 0)
            for p in activity.get("proposal_list", []):
                p["season_name"] = season.name
                proposal_list.append(p)

            # Find pending/expired interpretations for this governor in this season
            pending_events = await repo.get_events_by_type(
                season_id=season.id,
                event_types=["proposal.pending_interpretation"],
            )
            ready_events = await repo.get_events_by_type(
                season_id=season.id,
                event_types=["proposal.interpretation_ready"],
            )
            expired_events = await repo.get_events_by_type(
                season_id=season.id,
                event_types=["proposal.interpretation_expired"],
            )
            resolved_ids = {e.aggregate_id for e in ready_events} | {
                e.aggregate_id for e in expired_events
            }
            for ev in pending_events:
                if ev.governor_id != player.id:
                    continue
                status = "EXPIRED" if ev.aggregate_id in resolved_ids else "PENDING"
                # Skip resolved ones unless expired
                if ev.aggregate_id in {e.aggregate_id for e in ready_events}:
                    continue
                pending_interpretations.append({
                    "aggregate_id": ev.aggregate_id,
                    "raw_text": ev.payload.get("raw_text", ""),
                    "season_name": season.name,
                    "status": status,
                    "timestamp": getattr(ev, "created_at", None)
                    or getattr(ev, "timestamp", None),
                })

        proposals_pending = proposals_submitted - proposals_passed - proposals_failed

        governors.append(
            {
                "id": player.id,
                "username": player.username,
                "team_name": team_name,
                "team_color": team_color,
                "joined": player.created_at,
                "propose": propose,
                "amend": amend,
                "boost": boost,
                "proposals_submitted": proposals_submitted,
                "proposals_passed": proposals_passed,
                "proposals_failed": proposals_failed,
                "proposals_pending": proposals_pending,
                "votes_cast": votes_cast,
                "proposals": proposal_list,
                "pending_interpretations": pending_interpretations,
            }
        )

    return templates.TemplateResponse(
        request,
        "pages/admin_roster.html",
        {
            "active_page": "roster",
            "governors": governors,
            **admin_auth_context(request, current_user),
        },
    )
