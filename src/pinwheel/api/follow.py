"""API routes for team following — cookie-based MVP.

Stores the followed team ID in a simple cookie (``pinwheel_followed_team``).
No DB table needed for MVP. Logged-in users get a persistent cookie;
the cookie is not signed because the value is just a team UUID
with no security implications.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from pinwheel.api.deps import RepoDep
from pinwheel.auth.deps import OptionalUser

router = APIRouter(tags=["follow"])

FOLLOW_COOKIE_NAME = "pinwheel_followed_team"
FOLLOW_COOKIE_MAX_AGE = 90 * 24 * 60 * 60  # 90 days


def get_followed_team_id(request: Request) -> str | None:
    """Read the followed team ID from the cookie. Returns None if unset."""
    value = request.cookies.get(FOLLOW_COOKIE_NAME)
    if value and len(value) == 36:
        return value
    return None


@router.post("/api/teams/{team_id}/follow", response_model=None)
async def follow_team(
    request: Request,
    team_id: str,
    repo: RepoDep,
    current_user: OptionalUser,
) -> HTMLResponse | JSONResponse:
    """Set the followed team cookie. Returns an HTMX partial or JSON.

    Works for both logged-in and anonymous users — the cookie is
    independent of the session. This keeps the MVP simple.
    """
    # Validate team exists
    team = await repo.get_team(team_id)
    if team is None:
        return JSONResponse({"error": "Team not found"}, status_code=404)

    is_htmx = request.headers.get("HX-Request") == "true"

    if is_htmx:
        html = _follow_button_html(team_id, team.name, following=True)
        response = HTMLResponse(html)
    else:
        response = JSONResponse({"status": "following", "team_id": team_id})  # type: ignore[assignment]

    response.set_cookie(
        FOLLOW_COOKIE_NAME,
        team_id,
        max_age=FOLLOW_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return response


@router.delete("/api/teams/{team_id}/follow", response_model=None)
async def unfollow_team(
    request: Request,
    team_id: str,
    repo: RepoDep,
    current_user: OptionalUser,
) -> HTMLResponse | JSONResponse:
    """Clear the followed team cookie. Returns an HTMX partial or JSON."""
    team = await repo.get_team(team_id)
    team_name = team.name if team else "this team"

    is_htmx = request.headers.get("HX-Request") == "true"

    if is_htmx:
        html = _follow_button_html(team_id, team_name, following=False)
        response = HTMLResponse(html)
    else:
        response = JSONResponse({"status": "unfollowed", "team_id": team_id})  # type: ignore[assignment]

    response.delete_cookie(FOLLOW_COOKIE_NAME)
    return response


def _follow_button_html(team_id: str, team_name: str, *, following: bool) -> str:
    """Render the follow/unfollow button as an HTMX-swappable fragment."""
    if following:
        return (
            f'<div id="follow-btn-container">'
            f'<button class="follow-btn follow-btn--following"'
            f' hx-delete="/api/teams/{team_id}/follow"'
            f' hx-target="#follow-btn-container"'
            f' hx-swap="outerHTML"'
            f' title="Unfollow {team_name}">'
            f"Following</button>"
            f"</div>"
        )
    return (
        f'<div id="follow-btn-container">'
        f'<button class="follow-btn"'
        f' hx-post="/api/teams/{team_id}/follow"'
        f' hx-target="#follow-btn-container"'
        f' hx-swap="outerHTML"'
        f' title="Follow {team_name} to prioritize their games">'
        f"Follow</button>"
        f"</div>"
    )
