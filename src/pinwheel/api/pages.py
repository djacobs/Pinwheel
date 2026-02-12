"""Page routes — server-rendered HTML via Jinja2 templates."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from pinwheel.api.deps import RepoDep
from pinwheel.auth.deps import OptionalUser, SessionUser
from pinwheel.config import PROJECT_ROOT
from pinwheel.core.scheduler import compute_standings
from pinwheel.models.governance import Proposal
from pinwheel.models.rules import RuleSet

router = APIRouter(tags=["pages"])

templates = Jinja2Templates(directory=str(PROJECT_ROOT / "templates"))


def _auth_context(request: Request, current_user: SessionUser | None) -> dict:
    """Build auth-related template context available on every page."""
    settings = request.app.state.settings
    oauth_enabled = bool(settings.discord_client_id and settings.discord_client_secret)
    return {
        "current_user": current_user,
        "oauth_enabled": oauth_enabled,
        "pinwheel_env": settings.pinwheel_env,
    }


async def _get_active_season_id(repo: RepoDep) -> str | None:
    """Get the first available season. Hackathon shortcut."""
    from sqlalchemy import select

    from pinwheel.db.models import SeasonRow

    stmt = select(SeasonRow).limit(1)
    result = await repo.session.execute(stmt)
    row = result.scalar_one_or_none()
    return row.id if row else None


async def _get_standings(repo: RepoDep, season_id: str) -> list[dict]:
    """Compute standings for a season."""
    all_results: list[dict] = []
    for round_num in range(1, 100):
        games = await repo.get_games_for_round(season_id, round_num)
        if not games:
            break
        for g in games:
            all_results.append(
                {
                    "home_team_id": g.home_team_id,
                    "away_team_id": g.away_team_id,
                    "home_score": g.home_score,
                    "away_score": g.away_score,
                    "winner_team_id": g.winner_team_id,
                }
            )
    standings = compute_standings(all_results)
    for s in standings:
        team = await repo.get_team(s["team_id"])
        if team:
            s["team_name"] = team.name
    return standings


@router.get("/", response_class=HTMLResponse)
async def home_page(request: Request, repo: RepoDep, current_user: OptionalUser):
    """Home page with navigation links."""
    season_id = await _get_active_season_id(repo)
    latest_mirror = None
    if season_id:
        m = await repo.get_latest_mirror(season_id, "simulation")
        if m:
            latest_mirror = {
                "content": m.content,
                "round_number": m.round_number,
            }

    ctx = {"active_page": "home", "latest_mirror": latest_mirror}
    return templates.TemplateResponse(
        request,
        "pages/home.html",
        {**ctx, **_auth_context(request, current_user)},
    )


@router.get("/arena", response_class=HTMLResponse)
async def arena_page(request: Request, repo: RepoDep, current_user: OptionalUser):
    """The Arena — show the latest round's games."""
    season_id = await _get_active_season_id(repo)
    games = []
    mirror = None

    if season_id:
        # Find the latest round that has games
        latest_round = 0
        for rn in range(1, 100):
            round_games = await repo.get_games_for_round(season_id, rn)
            if round_games:
                latest_round = rn
            else:
                break

        if latest_round > 0:
            round_games = await repo.get_games_for_round(season_id, latest_round)
            # Build team name cache
            team_names: dict[str, str] = {}
            for g in round_games:
                for tid in (g.home_team_id, g.away_team_id):
                    if tid not in team_names:
                        t = await repo.get_team(tid)
                        team_names[tid] = t.name if t else tid

            for g in round_games:
                games.append(
                    {
                        "id": g.id,
                        "round_number": g.round_number,
                        "matchup_index": g.matchup_index,
                        "home_team_id": g.home_team_id,
                        "away_team_id": g.away_team_id,
                        "home_name": team_names.get(g.home_team_id, "?"),
                        "away_name": team_names.get(g.away_team_id, "?"),
                        "home_score": g.home_score,
                        "away_score": g.away_score,
                        "winner_team_id": g.winner_team_id,
                        "elam_target": g.elam_target,
                        "total_possessions": g.total_possessions,
                        "quarter_scores": g.quarter_scores or [],
                    }
                )

            # Get simulation mirror for this round
            mirrors = await repo.get_mirrors_for_round(
                season_id, latest_round, "simulation"
            )
            if mirrors:
                mirror = {
                    "content": mirrors[0].content,
                    "round_number": mirrors[0].round_number,
                }

    return templates.TemplateResponse(
        request,
        "pages/arena.html",
        {
            "active_page": "arena",
            "games": games,
            "mirror": mirror,
            **_auth_context(request, current_user),
        },
    )


@router.get("/standings", response_class=HTMLResponse)
async def standings_page(request: Request, repo: RepoDep, current_user: OptionalUser):
    """Standings page."""
    season_id = await _get_active_season_id(repo)
    standings = []
    if season_id:
        standings = await _get_standings(repo, season_id)

    return templates.TemplateResponse(
        request,
        "pages/standings.html",
        {
            "active_page": "standings",
            "standings": standings,
            **_auth_context(request, current_user),
        },
    )


@router.get("/games/{game_id}", response_class=HTMLResponse)
async def game_page(request: Request, game_id: str, repo: RepoDep, current_user: OptionalUser):
    """Single game detail page."""
    game = await repo.get_game_result(game_id)
    if not game:
        raise HTTPException(404, "Game not found")

    # Team names
    home_team = await repo.get_team(game.home_team_id)
    away_team = await repo.get_team(game.away_team_id)
    home_name = home_team.name if home_team else game.home_team_id
    away_name = away_team.name if away_team else game.away_team_id

    # Box scores grouped by team
    home_players = []
    away_players = []
    for bs in game.box_scores:
        agent = await repo.get_agent(bs.agent_id)
        player = {
            "agent_name": agent.name if agent else bs.agent_id,
            "points": bs.points,
            "field_goals_made": bs.field_goals_made,
            "field_goals_attempted": bs.field_goals_attempted,
            "three_pointers_made": bs.three_pointers_made,
            "three_pointers_attempted": bs.three_pointers_attempted,
            "assists": bs.assists,
            "steals": bs.steals,
            "turnovers": bs.turnovers,
        }
        if bs.team_id == game.home_team_id:
            home_players.append(player)
        else:
            away_players.append(player)

    box_score_groups = [
        (home_name, game.home_team_id, home_players),
        (away_name, game.away_team_id, away_players),
    ]

    # Play-by-play from stored data
    play_by_play = game.play_by_play or []

    # Mirror for this round
    season_id = await _get_active_season_id(repo)
    mirror = None
    if season_id:
        mirrors = await repo.get_mirrors_for_round(
            season_id, game.round_number, "simulation"
        )
        if mirrors:
            mirror = {"content": mirrors[0].content}

    return templates.TemplateResponse(
        request,
        "pages/game.html",
        {
            "active_page": "arena",
            "game": game,
            "home_name": home_name,
            "away_name": away_name,
            "box_score_groups": box_score_groups,
            "play_by_play": play_by_play,
            "mirror": mirror,
            **_auth_context(request, current_user),
        },
    )


@router.get("/teams/{team_id}", response_class=HTMLResponse)
async def team_page(request: Request, team_id: str, repo: RepoDep, current_user: OptionalUser):
    """Team profile page."""
    team = await repo.get_team(team_id)
    if not team:
        raise HTTPException(404, "Team not found")

    agents = []
    for a in team.agents:
        agents.append(
            {
                "name": a.name,
                "archetype": a.archetype,
                "attributes": a.attributes,
                "is_active": a.is_active,
            }
        )

    # Get this team's standings
    season_id = await _get_active_season_id(repo)
    team_standings = None
    if season_id:
        standings = await _get_standings(repo, season_id)
        for s in standings:
            if s["team_id"] == team_id:
                team_standings = s
                break

    return templates.TemplateResponse(
        request,
        "pages/team.html",
        {
            "active_page": "standings",
            "team": team,
            "agents": agents,
            "team_standings": team_standings,
            **_auth_context(request, current_user),
        },
    )


@router.get("/governance", response_class=HTMLResponse)
async def governance_page(request: Request, repo: RepoDep, current_user: OptionalUser):
    """Governance audit trail — proposals, outcomes, vote totals.

    Auth-gated: redirects to login if OAuth is enabled and user is not
    authenticated. In dev mode without OAuth credentials the page is
    accessible to support local testing.
    """
    from fastapi.responses import RedirectResponse

    settings = request.app.state.settings
    oauth_enabled = bool(
        settings.discord_client_id and settings.discord_client_secret,
    )
    if current_user is None and oauth_enabled:
        return RedirectResponse(url="/auth/login", status_code=302)

    season_id = await _get_active_season_id(repo)
    proposals = []
    rules_changed = []

    if season_id:
        # Gather all governance events we need
        submitted = await repo.get_events_by_type(
            season_id=season_id,
            event_types=["proposal.submitted"],
        )
        outcome_events = await repo.get_events_by_type(
            season_id=season_id,
            event_types=["proposal.confirmed", "proposal.passed", "proposal.failed"],
        )
        vote_events = await repo.get_events_by_type(
            season_id=season_id,
            event_types=["vote.cast"],
        )

        # Index outcomes and votes by proposal_id
        confirmed_ids: set[str] = set()
        outcomes: dict[str, dict] = {}
        for e in outcome_events:
            pid = e.payload.get("proposal_id", e.aggregate_id)
            if e.event_type == "proposal.confirmed":
                confirmed_ids.add(pid)
            elif e.event_type in ("proposal.passed", "proposal.failed"):
                outcomes[pid] = e.payload

        votes_by_proposal: dict[str, dict] = {}
        for e in vote_events:
            pid = e.payload.get("proposal_id", "")
            if not pid:
                continue
            bucket = votes_by_proposal.setdefault(
                pid, {"yes": 0.0, "no": 0.0, "count": 0},
            )
            weight = float(e.payload.get("weight", 1.0))
            if e.payload.get("vote") == "yes":
                bucket["yes"] += weight
            else:
                bucket["no"] += weight
            bucket["count"] += 1

        for e in submitted:
            p_data = e.payload
            if "id" not in p_data or "raw_text" not in p_data:
                continue
            p = Proposal(**p_data)
            interp = p.interpretation if p.interpretation else None

            # Determine latest status from events
            pid = p.id
            status = p.status
            if pid in outcomes:
                status = "passed" if outcomes[pid].get("passed") else "failed"
            elif pid in confirmed_ids:
                status = "confirmed"

            # Vote tally (totals only — no individual votes)
            tally = votes_by_proposal.get(pid)

            proposals.append({
                "id": pid,
                "governor_id": p.governor_id,
                "raw_text": p.raw_text,
                "status": status,
                "tier": p.tier,
                "interpretation": interp,
                "vote_tally": tally,
            })

        rc_events = await repo.get_events_by_type(
            season_id=season_id,
            event_types=["rule.enacted"],
        )
        for e in rc_events:
            rules_changed.append(e.payload)

    return templates.TemplateResponse(
        request,
        "pages/governance.html",
        {
            "active_page": "governance",
            "proposals": proposals,
            "rules_changed": rules_changed,
            **_auth_context(request, current_user),
        },
    )


@router.get("/rules", response_class=HTMLResponse)
async def rules_page(request: Request, repo: RepoDep, current_user: OptionalUser):
    """Current rules page."""
    season_id = await _get_active_season_id(repo)
    ruleset = RuleSet()
    changes_from_default: dict = {}
    rule_history = []

    if season_id:
        season = await repo.get_season(season_id)
        if season and season.current_ruleset:
            ruleset = RuleSet(**season.current_ruleset)

        defaults = RuleSet()
        for param in RuleSet.model_fields:
            current = getattr(ruleset, param)
            default = getattr(defaults, param)
            if current != default:
                changes_from_default[param] = {
                    "current": current,
                    "default": default,
                }

        rc_events = await repo.get_events_by_type(
            season_id=season_id,
            event_types=["rule.enacted"],
        )
        rule_history = [e.payload for e in rc_events]

    return templates.TemplateResponse(
        request,
        "pages/rules.html",
        {
            "active_page": "rules",
            "ruleset": ruleset.model_dump(),
            "changes_from_default": changes_from_default,
            "rule_history": rule_history,
            **_auth_context(request, current_user),
        },
    )


@router.get("/mirrors", response_class=HTMLResponse)
async def mirrors_page(request: Request, repo: RepoDep, current_user: OptionalUser):
    """Mirrors archive page."""
    season_id = await _get_active_season_id(repo)
    mirrors = []

    if season_id:
        for rn in range(100, 0, -1):
            round_mirrors = await repo.get_mirrors_for_round(season_id, rn)
            for m in round_mirrors:
                if m.mirror_type != "private":
                    mirrors.append(
                        {
                            "mirror_type": m.mirror_type,
                            "round_number": m.round_number,
                            "content": m.content,
                            "created_at": (
                                m.created_at.isoformat() if m.created_at else ""
                            ),
                        }
                    )
            if mirrors and rn < 95 and len(mirrors) > 20:
                break

    return templates.TemplateResponse(
        request,
        "pages/mirrors.html",
        {"active_page": "mirrors", "mirrors": mirrors, **_auth_context(request, current_user)},
    )


@router.get("/terms", response_class=HTMLResponse)
async def terms_page(request: Request, current_user: OptionalUser):
    """Terms of Service."""
    return templates.TemplateResponse(
        request,
        "pages/terms.html",
        {"active_page": "terms", **_auth_context(request, current_user)},
    )


@router.get("/privacy", response_class=HTMLResponse)
async def privacy_page(request: Request, current_user: OptionalUser):
    """Privacy Policy."""
    return templates.TemplateResponse(
        request,
        "pages/privacy.html",
        {"active_page": "privacy", **_auth_context(request, current_user)},
    )
