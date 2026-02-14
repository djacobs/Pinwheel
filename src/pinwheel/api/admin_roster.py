"""Admin roster route -- GET /admin/roster.

Shows all enrolled governors with team, tokens, proposals, and votes.
Admin-gated via PINWHEEL_ADMIN_DISCORD_ID or accessible in dev without OAuth.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from pinwheel.api.deps import RepoDep
from pinwheel.auth.deps import OptionalUser, SessionUser
from pinwheel.config import APP_VERSION, PROJECT_ROOT

router = APIRouter(prefix="/admin", tags=["admin"])

templates = Jinja2Templates(directory=str(PROJECT_ROOT / "templates"))


def _auth_context(request: Request, current_user: SessionUser | None) -> dict:
    """Build auth-related template context."""
    settings = request.app.state.settings
    oauth_enabled = bool(settings.discord_client_id and settings.discord_client_secret)
    return {
        "current_user": current_user,
        "oauth_enabled": oauth_enabled,
        "pinwheel_env": settings.pinwheel_env,
        "app_version": APP_VERSION,
    }


async def _get_active_season_id(repo: RepoDep) -> str | None:
    """Get the first available season. Hackathon shortcut."""
    from sqlalchemy import select

    from pinwheel.db.models import SeasonRow

    stmt = select(SeasonRow).limit(1)
    result = await repo.session.execute(stmt)
    row = result.scalar_one_or_none()
    return row.id if row else None


def _is_admin(current_user: SessionUser | None, settings: object) -> bool:
    """Check if the current user is the configured admin."""
    if current_user is None:
        return False
    admin_id = getattr(settings, "pinwheel_admin_discord_id", "")
    if not admin_id:
        return False
    return current_user.discord_id == admin_id


@router.get("/roster", response_class=HTMLResponse)
async def admin_roster(request: Request, repo: RepoDep, current_user: OptionalUser):
    """Admin roster -- table of all enrolled governors.

    Auth-gated: requires admin Discord ID match when OAuth is enabled.
    In dev mode without OAuth credentials the page is accessible to support
    local testing.
    """
    settings = request.app.state.settings
    oauth_enabled = bool(settings.discord_client_id and settings.discord_client_secret)

    # Auth gate: in production, require admin login
    if oauth_enabled:
        if current_user is None:
            return RedirectResponse(url="/auth/login", status_code=302)
        if not _is_admin(current_user, settings):
            return HTMLResponse("Unauthorized -- admin access required.", status_code=403)

    # Show ALL players, regardless of season enrollment
    all_players = await repo.get_all_players()
    governors: list[dict] = []

    # Get active season for token balances and activity
    active_season = await repo.get_active_season()
    season_id = active_season.id if active_season else None

    for player in all_players:
        team = await repo.get_team(player.team_id) if player.team_id else None
        team_name = team.name if team else "Unassigned"
        team_color = team.color if team else "#888"

        propose = 0
        amend = 0
        boost = 0
        proposals_submitted = 0
        proposals_passed = 0
        proposals_failed = 0
        votes_cast = 0

        if season_id:
            from pinwheel.core.tokens import get_token_balance

            balance = await get_token_balance(repo, player.id, season_id)
            propose = balance.propose
            amend = balance.amend
            boost = balance.boost
            activity = await repo.get_governor_activity(player.id, season_id)
            proposals_submitted = activity.get("proposals_submitted", 0)
            proposals_passed = activity.get("proposals_passed", 0)
            proposals_failed = activity.get("proposals_failed", 0)
            votes_cast = activity.get("votes_cast", 0)

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
                "votes_cast": votes_cast,
            }
        )

    return templates.TemplateResponse(
        request,
        "pages/admin_roster.html",
        {
            "active_page": "roster",
            "governors": governors,
            **_auth_context(request, current_user),
        },
    )
