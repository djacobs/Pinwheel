"""Admin season route -- GET /admin/season.

Shows current season attributes, runtime configuration, past seasons,
and a form to start a new season. Admin-gated in production.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func as sa_func
from sqlalchemy import select

from pinwheel.api.deps import RepoDep
from pinwheel.auth.deps import OptionalUser, SessionUser
from pinwheel.config import APP_VERSION, PROJECT_ROOT
from pinwheel.db.models import GameResultRow, TeamRow

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


def _is_admin(current_user: SessionUser | None, settings: object) -> bool:
    """Check if the current user is the configured admin."""
    if current_user is None:
        return False
    admin_id = getattr(settings, "pinwheel_admin_discord_id", "")
    if not admin_id:
        return False
    return current_user.discord_id == admin_id


@router.get("/season", response_class=HTMLResponse)
async def admin_season(request: Request, repo: RepoDep, current_user: OptionalUser):
    """Admin season dashboard -- current config, past seasons, new season form."""
    settings = request.app.state.settings
    oauth_enabled = bool(settings.discord_client_id and settings.discord_client_secret)

    if oauth_enabled:
        if current_user is None:
            return RedirectResponse(url="/auth/login", status_code=302)
        if not _is_admin(current_user, settings):
            return HTMLResponse("Unauthorized -- admin access required.", status_code=403)

    # Current season
    active_season = await repo.get_active_season()
    current_season_data: dict | None = None
    current_round = 0
    team_count = 0
    governor_count = 0
    games_played = 0

    if active_season:
        teams = await repo.get_teams_for_season(active_season.id)
        team_count = len(teams)

        players = await repo.get_players_for_season(active_season.id)
        governor_count = len(players)

        # Count completed games
        game_count_result = await repo.session.execute(
            select(sa_func.count(GameResultRow.id)).where(
                GameResultRow.season_id == active_season.id,
            )
        )
        games_played = game_count_result.scalar() or 0

        # Current round (highest round with a game result)
        round_result = await repo.session.execute(
            select(sa_func.max(GameResultRow.round_number)).where(
                GameResultRow.season_id == active_season.id,
            )
        )
        current_round = round_result.scalar() or 0

        current_season_data = {
            "id": active_season.id,
            "name": active_season.name,
            "status": active_season.status,
            "created_at": active_season.created_at,
            "current_round": current_round,
            "team_count": team_count,
            "governor_count": governor_count,
            "games_played": games_played,
        }

    # Runtime config (from Settings, not DB)
    runtime_config = {
        "pace": settings.pinwheel_presentation_pace,
        "pace_cron": settings.effective_game_cron() or "disabled",
        "auto_advance": settings.pinwheel_auto_advance,
        "presentation_mode": settings.pinwheel_presentation_mode,
        "governance_interval": settings.pinwheel_governance_interval,
        "evals_enabled": settings.pinwheel_evals_enabled,
        "env": settings.pinwheel_env,
        "quarter_replay_seconds": settings.pinwheel_quarter_replay_seconds,
        "game_interval_seconds": settings.pinwheel_game_interval_seconds,
    }

    # All seasons for the history table
    all_seasons = await repo.get_all_seasons()
    past_seasons: list[dict] = []

    for season in all_seasons:
        # Count teams for this season
        s_team_count_result = await repo.session.execute(
            select(sa_func.count(TeamRow.id)).where(TeamRow.season_id == season.id)
        )
        s_team_count = s_team_count_result.scalar() or 0

        # Count games for this season
        s_game_count_result = await repo.session.execute(
            select(sa_func.count(GameResultRow.id)).where(
                GameResultRow.season_id == season.id,
            )
        )
        s_game_count = s_game_count_result.scalar() or 0

        past_seasons.append(
            {
                "id": season.id,
                "name": season.name,
                "status": season.status,
                "created_at": season.created_at,
                "completed_at": season.completed_at,
                "team_count": s_team_count,
                "games_played": s_game_count,
                "is_active": active_season and season.id == active_season.id,
            }
        )

    return templates.TemplateResponse(
        request,
        "pages/admin_season.html",
        {
            "active_page": "season",
            "current_season": current_season_data,
            "runtime_config": runtime_config,
            "past_seasons": past_seasons,
            **_auth_context(request, current_user),
        },
    )
