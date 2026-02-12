"""Page routes — server-rendered HTML via Jinja2 templates."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from pinwheel.api.charts import (
    axis_lines,
    compute_grid_rings,
    compute_season_averages,
    polygon_points,
    spider_chart_data,
)
from pinwheel.api.deps import RepoDep
from pinwheel.auth.deps import OptionalUser, SessionUser
from pinwheel.config import APP_VERSION, PROJECT_ROOT
from pinwheel.core.narrate import narrate_play, narrate_winner
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
    """Home page with navigation links and league snapshot."""
    season_id = await _get_active_season_id(repo)
    latest_mirror = None
    standings_leader = None
    total_games = 0
    if season_id:
        m = await repo.get_latest_mirror(season_id, "simulation")
        if m:
            latest_mirror = {
                "content": m.content,
                "round_number": m.round_number,
            }

        standings = await _get_standings(repo, season_id)
        if standings:
            leader = standings[0]
            standings_leader = {
                "team_name": leader.get("team_name", "Unknown"),
                "wins": leader["wins"],
                "losses": leader["losses"],
            }
            total_games = sum(s["wins"] for s in standings)

    ctx = {
        "active_page": "home",
        "latest_mirror": latest_mirror,
        "standings_leader": standings_leader,
        "total_games": total_games,
    }
    return templates.TemplateResponse(
        request,
        "pages/home.html",
        {**ctx, **_auth_context(request, current_user)},
    )


@router.get("/arena", response_class=HTMLResponse)
async def arena_page(request: Request, repo: RepoDep, current_user: OptionalUser):
    """The Arena — show recent rounds' games (newest first)."""
    season_id = await _get_active_season_id(repo)
    rounds: list[dict] = []

    if season_id:
        # Find the latest round that has games
        latest_round = 0
        for rn in range(1, 100):
            round_games = await repo.get_games_for_round(season_id, rn)
            if round_games:
                latest_round = rn
            else:
                break

        # Show up to 4 recent rounds (newest first)
        team_names: dict[str, str] = {}
        agent_names: dict[str, str] = {}
        first_round = max(1, latest_round - 3)

        for round_num in range(latest_round, first_round - 1, -1):
            round_games = await repo.get_games_for_round(season_id, round_num)
            if not round_games:
                continue

            # Build team name cache
            for g in round_games:
                for tid in (g.home_team_id, g.away_team_id):
                    if tid not in team_names:
                        t = await repo.get_team(tid)
                        team_names[tid] = t.name if t else tid

            games_for_round = []
            for g in round_games:
                # Extract game-winning play and narrate it
                winning_play = None
                if g.play_by_play:
                    for play in reversed(g.play_by_play):
                        if play.get("result") == "made" and play.get("points_scored", 0) > 0:
                            handler_id = play.get("ball_handler_id", "")
                            if handler_id and handler_id not in agent_names:
                                agent = await repo.get_agent(handler_id)
                                agent_names[handler_id] = agent.name if agent else handler_id
                            action = play.get("action", "")
                            move = play.get("move_activated", "")
                            player_name = agent_names.get(handler_id, "Unknown")
                            winning_play = {
                                "player": player_name,
                                "action": narrate_winner(
                                    player_name,
                                    action,
                                    move=move,
                                    seed=hash(g.id),
                                ),
                                "points": play.get("points_scored", 0),
                                "move": move,
                            }
                            break

                games_for_round.append(
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
                        "winning_play": winning_play,
                    }
                )

            # Get simulation mirror for this round
            mirror = None
            mirrors = await repo.get_mirrors_for_round(
                season_id, round_num, "simulation"
            )
            if mirrors:
                mirror = {
                    "content": mirrors[0].content,
                    "round_number": mirrors[0].round_number,
                }

            rounds.append({
                "round_number": round_num,
                "games": games_for_round,
                "mirror": mirror,
            })

    return templates.TemplateResponse(
        request,
        "pages/arena.html",
        {
            "active_page": "arena",
            "rounds": rounds,
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

    # Build agent-name cache from box scores already loaded
    agent_names: dict[str, str] = {}
    for bs in game.box_scores:
        if bs.agent_id not in agent_names:
            agent = await repo.get_agent(bs.agent_id)
            agent_names[bs.agent_id] = agent.name if agent else bs.agent_id

    # Play-by-play from stored data (JSON dicts), enriched with narration
    raw_plays = game.play_by_play or []
    play_by_play = []
    for play in raw_plays:
        handler_id = play.get("ball_handler_id", "")
        def_id = play.get("defender_id", "")
        enriched = {**play}
        enriched["narration"] = narrate_play(
            player=agent_names.get(handler_id, handler_id),
            defender=agent_names.get(def_id, def_id),
            action=play.get("action", ""),
            result=play.get("result", ""),
            points=play.get("points_scored", 0),
            move=play.get("move_activated", ""),
            seed=play.get("possession_number", 0),
        )
        play_by_play.append(enriched)

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

    # Get this team's standings
    season_id = await _get_active_season_id(repo)
    team_standings = None
    standing_position = None
    league_name = None

    # League averages for spider chart shadow
    league_avg = {}
    if season_id:
        standings = await _get_standings(repo, season_id)
        for idx, s in enumerate(standings):
            if s["team_id"] == team_id:
                team_standings = s
                standing_position = idx + 1
                break

        season = await repo.get_season(season_id)
        if season:
            from pinwheel.db.models import LeagueRow

            league = await repo.session.get(LeagueRow, season.league_id)
            if league:
                league_name = league.name

        league_avg = await repo.get_league_attribute_averages(season_id)

    # Build agent data with spider chart geometry
    grid_rings = compute_grid_rings()
    axes = axis_lines()
    avg_points = spider_chart_data(league_avg) if league_avg else []
    avg_poly = polygon_points(avg_points) if avg_points else ""

    agents = []
    for a in team.agents:
        agent_pts = spider_chart_data(a.attributes) if a.attributes else []
        agents.append(
            {
                "id": a.id,
                "name": a.name,
                "archetype": a.archetype,
                "attributes": a.attributes,
                "is_active": a.is_active,
                "spider_points": agent_pts,
                "spider_poly": polygon_points(agent_pts) if agent_pts else "",
            }
        )

    return templates.TemplateResponse(
        request,
        "pages/team.html",
        {
            "active_page": "standings",
            "team": team,
            "agents": agents,
            "team_standings": team_standings,
            "standing_position": standing_position,
            "league_name": league_name,
            "grid_rings": grid_rings,
            "axis_lines": axes,
            "avg_points": avg_points,
            "avg_poly": avg_poly,
            **_auth_context(request, current_user),
        },
    )


@router.get("/agents/{agent_id}", response_class=HTMLResponse)
async def agent_page(
    request: Request, agent_id: str, repo: RepoDep, current_user: OptionalUser
):
    """Individual agent profile page."""
    agent = await repo.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    team = await repo.get_team(agent.team_id)
    season_id = await _get_active_season_id(repo)

    # Spider chart data
    league_avg = {}
    if season_id:
        league_avg = await repo.get_league_attribute_averages(season_id)

    agent_pts = spider_chart_data(agent.attributes) if agent.attributes else []
    avg_pts = spider_chart_data(league_avg) if league_avg else []

    # Game log + season averages
    box_score_rows = await repo.get_box_scores_for_agent(agent_id)
    game_log = []
    bs_dicts = []
    team_name_cache: dict[str, str] = {}

    for bs, game in box_score_rows:
        # Determine opponent
        opp_id = game.away_team_id if bs.team_id == game.home_team_id else game.home_team_id

        if opp_id not in team_name_cache:
            opp_team = await repo.get_team(opp_id)
            team_name_cache[opp_id] = opp_team.name if opp_team else opp_id

        game_log.append(
            {
                "game_id": game.id,
                "round_number": game.round_number,
                "opponent_name": team_name_cache[opp_id],
                "points": bs.points,
                "field_goals_made": bs.field_goals_made,
                "field_goals_attempted": bs.field_goals_attempted,
                "three_pointers_made": bs.three_pointers_made,
                "three_pointers_attempted": bs.three_pointers_attempted,
                "free_throws_made": bs.free_throws_made,
                "free_throws_attempted": bs.free_throws_attempted,
                "assists": bs.assists,
                "steals": bs.steals,
                "turnovers": bs.turnovers,
            }
        )
        bs_dicts.append(
            {
                "points": bs.points,
                "assists": bs.assists,
                "steals": bs.steals,
                "turnovers": bs.turnovers,
                "field_goals_made": bs.field_goals_made,
                "field_goals_attempted": bs.field_goals_attempted,
                "three_pointers_made": bs.three_pointers_made,
                "three_pointers_attempted": bs.three_pointers_attempted,
                "free_throws_made": bs.free_throws_made,
                "free_throws_attempted": bs.free_throws_attempted,
            }
        )

    season_averages = compute_season_averages(bs_dicts)

    # Check if current user is governor on this agent's team (can edit bio)
    can_edit_bio = False
    if current_user and season_id:
        enrollment = await repo.get_player_enrollment(
            current_user.discord_id, season_id
        )
        if enrollment and enrollment[0] == agent.team_id:
            can_edit_bio = True

    return templates.TemplateResponse(
        request,
        "pages/agent.html",
        {
            "active_page": "standings",
            "agent": agent,
            "team": team,
            "spider_points": agent_pts,
            "avg_points": avg_pts,
            "grid_rings": compute_grid_rings(),
            "axis_lines": axis_lines(),
            "spider_poly": polygon_points(agent_pts) if agent_pts else "",
            "avg_poly": polygon_points(avg_pts) if avg_pts else "",
            "game_log": game_log,
            "season_averages": season_averages,
            "can_edit_bio": can_edit_bio,
            **_auth_context(request, current_user),
        },
    )


@router.get("/agents/{agent_id}/bio/edit", response_class=HTMLResponse)
async def agent_bio_edit_form(
    request: Request, agent_id: str, repo: RepoDep, current_user: OptionalUser
):
    """Return HTMX fragment with bio edit form. Governor-only."""
    agent = await repo.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    season_id = await _get_active_season_id(repo)
    if not current_user or not season_id:
        raise HTTPException(403, "Not authorized")

    enrollment = await repo.get_player_enrollment(current_user.discord_id, season_id)
    if not enrollment or enrollment[0] != agent.team_id:
        raise HTTPException(403, "Not authorized — must be team governor")

    html = f"""
    <form hx-post="/agents/{agent_id}/bio" hx-target="#agent-bio" hx-swap="innerHTML">
      <textarea name="backstory" rows="4" style="width:100%; background:var(--bg-input);
        color:var(--text-primary); border:1px solid var(--border); border-radius:var(--radius);
        padding:0.75rem; font-family:var(--font-body); font-size:0.9rem; resize:vertical;
        line-height:1.6;">{agent.backstory or ''}</textarea>
      <div style="margin-top:0.5rem; display:flex; gap:0.5rem;">
        <button type="submit" class="bio-edit-btn">Save</button>
        <button type="button" class="bio-edit-btn"
                hx-get="/agents/{agent_id}/bio/view" hx-target="#agent-bio"
                hx-swap="innerHTML">Cancel</button>
      </div>
    </form>
    """
    return HTMLResponse(html)


@router.get("/agents/{agent_id}/bio/view", response_class=HTMLResponse)
async def agent_bio_view(
    request: Request, agent_id: str, repo: RepoDep, current_user: OptionalUser
):
    """Return HTMX fragment with bio display. Used after cancel/save."""
    agent = await repo.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    season_id = await _get_active_season_id(repo)
    can_edit = False
    if current_user and season_id:
        enrollment = await repo.get_player_enrollment(current_user.discord_id, season_id)
        if enrollment and enrollment[0] == agent.team_id:
            can_edit = True

    no_bio = '<p class="text-muted">No bio yet.</p>'
    bio_html = f"<p>{agent.backstory}</p>" if agent.backstory else no_bio
    edit_btn = ""
    if can_edit:
        edit_btn = f"""
        <button class="bio-edit-btn"
                hx-get="/agents/{agent_id}/bio/edit"
                hx-target="#agent-bio"
                hx-swap="innerHTML">Edit Bio</button>
        """
    return HTMLResponse(bio_html + edit_btn)


@router.post("/agents/{agent_id}/bio", response_class=HTMLResponse)
async def update_agent_bio(
    request: Request, agent_id: str, repo: RepoDep, current_user: OptionalUser
):
    """Update agent bio. Governor-only."""
    agent = await repo.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    season_id = await _get_active_season_id(repo)
    if not current_user or not season_id:
        raise HTTPException(403, "Not authorized")

    enrollment = await repo.get_player_enrollment(current_user.discord_id, season_id)
    if not enrollment or enrollment[0] != agent.team_id:
        raise HTTPException(403, "Not authorized — must be team governor")

    form = await request.form()
    backstory = str(form.get("backstory", "")).strip()
    await repo.update_agent_backstory(agent_id, backstory)
    await repo.session.commit()

    # Return the view fragment
    bio_html = f"<p>{backstory}</p>" if backstory else '<p class="text-muted">No bio yet.</p>'
    edit_btn = f"""
    <button class="bio-edit-btn"
            hx-get="/agents/{agent_id}/bio/edit"
            hx-target="#agent-bio"
            hx-swap="innerHTML">Edit Bio</button>
    """
    return HTMLResponse(bio_html + edit_btn)


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
