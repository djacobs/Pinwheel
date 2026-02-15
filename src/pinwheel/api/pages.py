"""Page routes — server-rendered HTML via Jinja2 templates."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
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
from pinwheel.config import APP_VERSION, PROJECT_ROOT, Settings
from pinwheel.core.narrate import narrate_play, narrate_winner
from pinwheel.core.schedule_times import (
    compute_round_start_times,
    format_game_time,
    group_into_slots,
)
from pinwheel.core.scheduler import compute_standings
from pinwheel.models.governance import Proposal
from pinwheel.models.rules import DEFAULT_RULESET, RuleSet

router = APIRouter(tags=["pages"])


def _get_slot_start_times(
    request: Request,
    slot_count: int,
) -> list[str]:
    """Compute formatted start times for the next *slot_count* time slots.

    Each slot fires on the cron cadence (e.g. every 30 min).  A slot is
    a set of games where no team plays twice — they tip off simultaneously.
    Returns an empty list if the cron is unavailable.
    """
    if slot_count <= 0:
        return []
    settings: Settings = request.app.state.settings
    cron_expr = settings.effective_game_cron()
    if not cron_expr:
        return []
    try:
        times = compute_round_start_times(cron_expr, slot_count)
        return [format_game_time(t) for t in times]
    except Exception:
        return []

templates = Jinja2Templates(directory=str(PROJECT_ROOT / "templates"))


def _auth_context(request: Request, current_user: SessionUser | None) -> dict:
    """Build auth-related template context available on every page."""
    settings = request.app.state.settings
    oauth_enabled = bool(settings.discord_client_id and settings.discord_client_secret)
    admin_id = settings.pinwheel_admin_discord_id
    is_admin = (
        current_user is not None
        and bool(admin_id)
        and current_user.discord_id == admin_id
    )
    return {
        "current_user": current_user,
        "oauth_enabled": oauth_enabled,
        "pinwheel_env": settings.pinwheel_env,
        "app_version": APP_VERSION,
        "discord_invite_url": settings.discord_invite_url,
        "is_admin": is_admin,
    }


async def _get_active_season_id(repo: RepoDep) -> str | None:
    """Get the active season ID (most recent non-terminal)."""
    row = await repo.get_active_season()
    return row.id if row else None


async def _get_active_season(repo: RepoDep) -> tuple[str | None, str | None]:
    """Get (season_id, season_name) for the active season."""
    row = await repo.get_active_season()
    if row:
        return row.id, row.name
    return None, None


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
            s["color"] = team.color or "#888"
            s["color_secondary"] = getattr(team, "color_secondary", None) or "#1a1a2e"
    return standings


async def _get_season_phase(repo: RepoDep, season_id: str) -> str:
    """Get the current season phase as a display label.

    Returns one of: 'regular', 'tiebreakers', 'playoffs', 'championship',
    'offseason'. Used to drive phase-aware badges in templates.
    """
    season = await repo.get_season(season_id)
    if not season:
        return "regular"
    phase_map: dict[str, str] = {
        "setup": "regular",
        "active": "regular",
        "tiebreaker_check": "regular",
        "tiebreakers": "tiebreakers",
        "regular_season_complete": "regular",
        "playoffs": "playoffs",
        "championship": "championship",
        "offseason": "offseason",
        "completed": "offseason",
        "complete": "offseason",
    }
    return phase_map.get(season.status or "", "regular")


async def _get_game_phase(repo: RepoDep, season_id: str, round_number: int) -> str | None:
    """Return the phase for a specific round's games ('semifinal', 'finals', or None)."""
    schedule = await repo.get_schedule_for_round(season_id, round_number)
    if not schedule or schedule[0].phase != "playoff":
        return None
    full_playoff = await repo.get_full_schedule(season_id, phase="playoff")
    if not full_playoff:
        return "semifinal"
    earliest_round = min(s.round_number for s in full_playoff)
    initial_pairs = [
        frozenset({s.home_team_id, s.away_team_id})
        for s in full_playoff
        if s.round_number == earliest_round
    ]
    current_pairs = [
        frozenset({s.home_team_id, s.away_team_id})
        for s in schedule
    ]
    if len(initial_pairs) >= 2 and all(p in initial_pairs for p in current_pairs):
        return "semifinal"
    return "finals"


def build_series_context(
    phase: str,
    home_team_name: str,
    away_team_name: str,
    home_wins: int,
    away_wins: int,
    best_of: int,
) -> dict:
    """Build a series context dict for display in the arena template.

    Args:
        phase: 'semifinal' or 'finals'.
        home_team_name: Display name for home team.
        away_team_name: Display name for away team.
        home_wins: Number of series wins for the home team.
        away_wins: Number of series wins for the away team.
        best_of: Best-of-N for this series round.

    Returns:
        Dict with keys: phase, phase_label, home_wins, away_wins, best_of,
        wins_needed, description.
    """
    wins_needed = (best_of + 1) // 2

    if phase == "finals":
        phase_label = "CHAMPIONSHIP FINALS"
        clinch_text = f"First to {wins_needed} wins is champion"
    else:
        phase_label = "SEMIFINAL SERIES"
        clinch_text = f"First to {wins_needed} wins advances"

    if home_wins == away_wins:
        record_text = f"Series tied {home_wins}-{away_wins}"
    elif home_wins > away_wins:
        record_text = f"{home_team_name} lead {home_wins}-{away_wins}"
    else:
        record_text = f"{away_team_name} lead {away_wins}-{home_wins}"

    description = f"{phase_label} \u00b7 {record_text} \u00b7 {clinch_text}"

    return {
        "phase": phase,
        "phase_label": phase_label,
        "home_wins": home_wins,
        "away_wins": away_wins,
        "best_of": best_of,
        "wins_needed": wins_needed,
        "description": description,
    }


async def _compute_series_context_for_game(
    repo: RepoDep,
    season_id: str,
    home_team_id: str,
    away_team_id: str,
    home_team_name: str,
    away_team_name: str,
    game_phase: str | None,
    ruleset: RuleSet | None = None,
) -> dict | None:
    """Compute series context for a specific playoff matchup.

    Returns None if the game is not a playoff game.
    """
    if not game_phase:
        return None

    # Get ruleset for best-of values
    if ruleset is None:
        season = await repo.get_season(season_id)
        if season and season.current_ruleset:
            ruleset = RuleSet(**season.current_ruleset)
        else:
            ruleset = DEFAULT_RULESET

    best_of = (
        ruleset.playoff_finals_best_of
        if game_phase == "finals"
        else ruleset.playoff_semis_best_of
    )

    # Get series record from playoff games
    from pinwheel.core.game_loop import _get_playoff_series_record

    home_wins, away_wins, _ = await _get_playoff_series_record(
        repo, season_id, home_team_id, away_team_id
    )

    return build_series_context(
        phase=game_phase,
        home_team_name=home_team_name,
        away_team_name=away_team_name,
        home_wins=home_wins,
        away_wins=away_wins,
        best_of=best_of,
    )


def _compute_streaks_from_games(games: list[object]) -> dict[str, int]:
    """Compute current win/loss streaks per team from game result rows.

    Positive = win streak, negative = loss streak. Resets on reversal.
    """
    sorted_games = sorted(games, key=lambda g: (g.round_number, g.matchup_index))
    team_results: dict[str, list[bool]] = {}
    for g in sorted_games:
        for tid in (g.home_team_id, g.away_team_id):
            if tid not in team_results:
                team_results[tid] = []
            team_results[tid].append(g.winner_team_id == tid)

    streaks: dict[str, int] = {}
    for tid, results in team_results.items():
        if not results:
            streaks[tid] = 0
            continue
        streak = 0
        last_result = results[-1]
        for r in reversed(results):
            if r == last_result:
                streak += 1
            else:
                break
        streaks[tid] = streak if last_result else -streak
    return streaks


@router.get("/", response_class=HTMLResponse)
async def home_page(request: Request, repo: RepoDep, current_user: OptionalUser):
    """Home page — living dashboard for the league."""
    season_id, season_name = await _get_active_season(repo)
    latest_report = None
    standings = []
    latest_round_games: list[dict] = []
    current_round = 0
    total_games = 0
    upcoming_rounds: list[dict] = []
    team_colors: dict[str, str] = {}

    if season_id:
        # Build standings
        standings = await _get_standings(repo, season_id)
        total_games = sum(s["wins"] for s in standings)

        # Team color + name cache
        team_names: dict[str, str] = {}
        hooper_names: dict[str, str] = {}
        for s in standings:
            t = await repo.get_team(s["team_id"])
            if t:
                team_colors[s["team_id"]] = t.color or "#888"
                team_names[s["team_id"]] = t.name
                s["color"] = t.color or "#888"

        # Find current round
        for rn in range(1, 100):
            round_games = await repo.get_games_for_round(season_id, rn)
            if round_games:
                current_round = rn
            else:
                break

        # Latest round's games — the headline scores (presented only)
        if current_round > 0:
            round_games = await repo.get_games_for_round(
                season_id,
                current_round,
                presented_only=True,
            )
            for g in round_games:
                # Cache team names
                for tid in (g.home_team_id, g.away_team_id):
                    if tid not in team_names:
                        t = await repo.get_team(tid)
                        team_names[tid] = t.name if t else tid
                        team_colors[tid] = t.color if t else "#888"

                # Extract game-winning play
                winning_play = None
                if g.play_by_play:
                    for play in reversed(g.play_by_play):
                        if play.get("result") == "made" and play.get("points_scored", 0) > 0:
                            handler_id = play.get("ball_handler_id", "")
                            if handler_id and handler_id not in hooper_names:
                                h = await repo.get_hooper(handler_id)
                                hooper_names[handler_id] = h.name if h else handler_id
                            winning_play = narrate_winner(
                                hooper_names.get(handler_id, "Unknown"),
                                play.get("action", ""),
                                move=play.get("move_activated", ""),
                                seed=hash(g.id),
                            )
                            break

                latest_round_games.append(
                    {
                        "id": g.id,
                        "home_name": team_names.get(g.home_team_id, "?"),
                        "away_name": team_names.get(g.away_team_id, "?"),
                        "home_score": g.home_score,
                        "away_score": g.away_score,
                        "winner_team_id": g.winner_team_id,
                        "home_team_id": g.home_team_id,
                        "away_team_id": g.away_team_id,
                        "home_color": team_colors.get(g.home_team_id, "#888"),
                        "away_color": team_colors.get(g.away_team_id, "#888"),
                        "elam_target": g.elam_target,
                        "total_possessions": g.total_possessions,
                        "winning_play": winning_play,
                    }
                )

        # Latest report
        m = await repo.get_latest_report(season_id, "simulation")
        if m:
            latest_report = {
                "content": m.content,
                "round_number": m.round_number,
            }

        # Upcoming time slots — group all unplayed games into slots
        # where no team plays twice (simultaneous tip-off).
        full_schedule = await repo.get_full_schedule(season_id)
        remaining_entries: list = [
            e for e in full_schedule if e.round_number > current_round
        ]

        # Group into time slots across all remaining rounds
        all_slots: list[list] = []
        by_round: dict[int, list] = {}
        for entry in remaining_entries:
            by_round.setdefault(entry.round_number, []).append(entry)
        for rn in sorted(by_round.keys()):
            all_slots.extend(group_into_slots(by_round[rn]))

        start_times = _get_slot_start_times(request, len(all_slots))

        for idx, slot_entries in enumerate(all_slots):
            slot_games: list[dict] = []
            for entry in slot_entries:
                for tid in (entry.home_team_id, entry.away_team_id):
                    if tid not in team_names:
                        t = await repo.get_team(tid)
                        team_names[tid] = t.name if t else tid
                        team_colors[tid] = t.color if t else "#888"
                slot_games.append(
                    {
                        "home_name": team_names.get(entry.home_team_id, "?"),
                        "away_name": team_names.get(entry.away_team_id, "?"),
                        "home_color": team_colors.get(entry.home_team_id, "#888"),
                        "away_color": team_colors.get(entry.away_team_id, "#888"),
                    }
                )
            upcoming_rounds.append(
                {
                    "start_time": (
                        start_times[idx] if idx < len(start_times) else None
                    ),
                    "games": slot_games,
                }
            )

    # Phase and streaks for template enrichment
    season_phase = ""
    streaks: dict[str, int] = {}
    if season_id:
        season_phase = await _get_season_phase(repo, season_id)
        all_games = await repo.get_all_games(season_id)
        if all_games:
            streaks = _compute_streaks_from_games(all_games)

    ctx = {
        "active_page": "home",
        "season_name": season_name or "Season",
        "latest_report": latest_report,
        "standings": standings,
        "latest_round_games": latest_round_games,
        "current_round": current_round,
        "total_games": total_games,
        "upcoming_rounds": upcoming_rounds,
        "team_colors": team_colors,
        "season_phase": season_phase,
        "streaks": streaks,
    }
    return templates.TemplateResponse(
        request,
        "pages/home.html",
        {**ctx, **_auth_context(request, current_user)},
    )


@router.get("/play", response_class=HTMLResponse)
async def play_page(request: Request, repo: RepoDep, current_user: OptionalUser):
    """How to Play — onboarding page for new players."""
    settings = request.app.state.settings
    season_id, season_name = await _get_active_season(repo)

    # Current league state for context
    current_round = 0
    total_teams = 0
    total_hoopers = 0
    total_games = 0
    season_status = ""
    season_phase_desc = ""
    team_names: list[str] = []

    if season_id:
        standings = await _get_standings(repo, season_id)
        total_teams = len(standings)
        total_games = sum(s["wins"] for s in standings)
        for rn in range(1, 100):
            games = await repo.get_games_for_round(season_id, rn)
            if games:
                current_round = rn
            else:
                break
        # Count agents + collect team names
        for s in standings:
            team = await repo.get_team(s["team_id"])
            if team:
                total_hoopers += len(team.hoopers)
                team_names.append(team.name)

        # Season phase context (season loaded below for ruleset too)
        season_row = await repo.get_season(season_id)
        if season_row:
            season_status = season_row.status or "active"

        # Human-readable phase description
        phase_map = {
            "active": f"Regular season in progress — Round {current_round} complete.",
            "setup": "Season is being set up. Games haven't started yet.",
            "regular_season_complete": "Regular season is over. Playoffs are next.",
            "tiebreaker_check": "Checking for tiebreakers before playoffs.",
            "tiebreakers": "Tiebreaker games are being played.",
            "playoffs": "Playoffs are underway.",
            "championship": "Championship series is being played.",
            "offseason": "The season has ended. A new season will begin soon.",
            "completed": "The season is complete.",
        }
        season_phase_desc = phase_map.get(season_status, f"Season is {season_status}.")

    # Pace description
    pace = settings.pinwheel_presentation_pace
    pace_desc = {
        "fast": "every minute",
        "normal": "every 5 minutes",
        "slow": "every 15 minutes",
        "manual": "when the commissioner advances them",
    }.get(pace, f"on a {pace} schedule")

    gov_interval = settings.pinwheel_governance_interval

    # Load current ruleset for key game parameters
    ruleset = DEFAULT_RULESET
    community_changes = 0
    if season_id:
        if not season_row:
            season_row = await repo.get_season(season_id)
        if season_row and season_row.current_ruleset:
            ruleset = RuleSet(**season_row.current_ruleset)
        for field_name in RuleSet.model_fields:
            if getattr(ruleset, field_name) != getattr(DEFAULT_RULESET, field_name):
                community_changes += 1

    d = DEFAULT_RULESET
    key_params = [
        {
            "label": "Shot Clock",
            "value": f"{ruleset.shot_clock_seconds}s",
            "desc": "Seconds per possession",
            "changed": ruleset.shot_clock_seconds != d.shot_clock_seconds,
        },
        {
            "label": "Three-Point Value",
            "value": ruleset.three_point_value,
            "desc": "Points for a three-pointer",
            "changed": ruleset.three_point_value != d.three_point_value,
        },
        {
            "label": "Quarter Length",
            "value": f"{ruleset.quarter_minutes} min",
            "desc": "Minutes per quarter (Q1-Q3)",
            "changed": ruleset.quarter_minutes != d.quarter_minutes,
        },
        {
            "label": "Elam Margin",
            "value": f"+{ruleset.elam_margin}",
            "desc": "Points added to leader's score for Elam target",
            "changed": ruleset.elam_margin != d.elam_margin,
        },
        {
            "label": "Free Throw Value",
            "value": ruleset.free_throw_value,
            "desc": "Points per free throw",
            "changed": ruleset.free_throw_value != d.free_throw_value,
        },
        {
            "label": "Foul Limit",
            "value": ruleset.personal_foul_limit,
            "desc": "Fouls before fouling out",
            "changed": ruleset.personal_foul_limit != d.personal_foul_limit,
        },
    ]

    return templates.TemplateResponse(
        request,
        "pages/play.html",
        {
            "active_page": "play",
            "season_name": season_name or "Season",
            "season_status": season_status,
            "season_phase_desc": season_phase_desc,
            "team_names": team_names,
            "current_round": current_round,
            "total_teams": total_teams,
            "total_hoopers": total_hoopers,
            "total_games": total_games,
            "pace_desc": pace_desc,
            "gov_interval": gov_interval,
            "key_params": key_params,
            "community_changes": community_changes,
            **_auth_context(request, current_user),
        },
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
        team_colors: dict[str, tuple[str, str]] = {}
        hooper_names: dict[str, str] = {}
        first_round = max(1, latest_round - 3)

        for round_num in range(latest_round, first_round - 1, -1):
            round_games = await repo.get_games_for_round(
                season_id,
                round_num,
                presented_only=True,
            )
            if not round_games:
                continue

            # Build team name + color cache
            for g in round_games:
                for tid in (g.home_team_id, g.away_team_id):
                    if tid not in team_names:
                        t = await repo.get_team(tid)
                        team_names[tid] = t.name if t else tid
                        team_colors[tid] = (
                            (t.color or "#888", getattr(t, "color_secondary", None) or "#1a1a2e")
                            if t
                            else ("#888", "#1a1a2e")
                        )

            games_for_round = []
            for g in round_games:
                # Extract game-winning play and narrate it
                winning_play = None
                if g.play_by_play:
                    for play in reversed(g.play_by_play):
                        if play.get("result") == "made" and play.get("points_scored", 0) > 0:
                            handler_id = play.get("ball_handler_id", "")
                            if handler_id and handler_id not in hooper_names:
                                h = await repo.get_hooper(handler_id)
                                hooper_names[handler_id] = h.name if h else handler_id
                            action = play.get("action", "")
                            move = play.get("move_activated", "")
                            player_name = hooper_names.get(handler_id, "Unknown")
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
                        "home_color": team_colors.get(g.home_team_id, ("#888", "#1a1a2e"))[0],
                        "home_color2": team_colors.get(g.home_team_id, ("#888", "#1a1a2e"))[1],
                        "away_color": team_colors.get(g.away_team_id, ("#888", "#1a1a2e"))[0],
                        "away_color2": team_colors.get(g.away_team_id, ("#888", "#1a1a2e"))[1],
                    }
                )

            # Get simulation report for this round
            report = None
            round_reports = await repo.get_reports_for_round(season_id, round_num, "simulation")
            if round_reports:
                report = {
                    "content": round_reports[0].content,
                    "round_number": round_reports[0].round_number,
                }

            # Determine the phase for this round
            round_phase = await _get_game_phase(repo, season_id, round_num)

            # Compute series context for playoff games
            round_series_contexts: list[dict | None] = []
            if round_phase:
                # Load ruleset once for best-of values
                season_row = await repo.get_season(season_id)
                round_ruleset = DEFAULT_RULESET
                if season_row and season_row.current_ruleset:
                    round_ruleset = RuleSet(**season_row.current_ruleset)

                for g in games_for_round:
                    ctx = await _compute_series_context_for_game(
                        repo,
                        season_id,
                        g["home_team_id"],
                        g["away_team_id"],
                        g["home_name"],
                        g["away_name"],
                        round_phase,
                        ruleset=round_ruleset,
                    )
                    round_series_contexts.append(ctx)
            else:
                round_series_contexts = [None] * len(games_for_round)

            # Attach series_context to each game dict
            for g, sc in zip(games_for_round, round_series_contexts, strict=False):
                g["series_context"] = sc

            rounds.append(
                {
                    "round_number": round_num,
                    "games": games_for_round,
                    "report": report,
                    "phase": round_phase,
                }
            )

    # Build live_round from PresentationState if presentation is active
    from pinwheel.core.presenter import PresentationState

    live_round = None
    pstate: PresentationState = request.app.state.presentation_state
    if pstate.is_active and pstate.live_games:
        live_round = {
            "round_number": pstate.current_round,
            "games": [
                {
                    "game_index": gs.game_index,
                    "home_team_name": gs.home_team_name,
                    "away_team_name": gs.away_team_name,
                    "home_score": gs.home_score,
                    "away_score": gs.away_score,
                    "quarter": gs.quarter,
                    "game_clock": gs.game_clock,
                    "status": gs.status,
                    "recent_plays": gs.recent_plays[-20:],
                    "home_leader": gs.home_leader,
                    "away_leader": gs.away_leader,
                    "home_color": gs.home_team_color,
                    "home_color2": gs.home_team_color2,
                    "away_color": gs.away_team_color,
                    "away_color2": gs.away_team_color2,
                    "series_context": gs.series_context,
                }
                for gs in pstate.live_games.values()
            ],
        }

    # Upcoming time slots — group all unplayed games into slots
    # where no team plays twice (simultaneous tip-off).
    upcoming_rounds: list[dict] = []
    if season_id:
        latest_played = 0
        for rn in range(1, 100):
            rg = await repo.get_games_for_round(season_id, rn)
            if rg:
                latest_played = rn
            else:
                break

        full_schedule = await repo.get_full_schedule(season_id)
        remaining_entries: list = [
            e for e in full_schedule if e.round_number > latest_played
        ]

        by_round: dict[int, list] = {}
        for entry in remaining_entries:
            by_round.setdefault(entry.round_number, []).append(entry)
        all_slots: list[list] = []
        for rn in sorted(by_round.keys()):
            all_slots.extend(group_into_slots(by_round[rn]))

        start_times = _get_slot_start_times(request, len(all_slots))

        team_names_sched: dict[str, str] = {}
        team_colors_sched: dict[str, tuple[str, str]] = {}
        _dflt = ("#888", "#1a1a2e")
        for idx, slot_entries in enumerate(all_slots):
            slot_games: list[dict] = []
            for entry in slot_entries:
                for tid in (entry.home_team_id, entry.away_team_id):
                    if tid not in team_names_sched:
                        if tid in team_names:
                            team_names_sched[tid] = team_names[tid]
                            team_colors_sched[tid] = team_colors.get(
                                tid, _dflt,
                            )
                        else:
                            t = await repo.get_team(tid)
                            team_names_sched[tid] = t.name if t else tid
                            c2 = (
                                getattr(t, "color_secondary", None)
                                or "#1a1a2e"
                            )
                            team_colors_sched[tid] = (
                                (t.color or "#888", c2)
                                if t
                                else _dflt
                            )
                slot_games.append(
                    {
                        "home_name": team_names_sched.get(
                            entry.home_team_id, "?",
                        ),
                        "away_name": team_names_sched.get(
                            entry.away_team_id, "?",
                        ),
                        "home_color": team_colors_sched.get(
                            entry.home_team_id, _dflt,
                        )[0],
                        "away_color": team_colors_sched.get(
                            entry.away_team_id, _dflt,
                        )[0],
                    }
                )
            upcoming_rounds.append(
                {
                    "start_time": (
                        start_times[idx]
                        if idx < len(start_times)
                        else None
                    ),
                    "games": slot_games,
                }
            )

    settings: Settings = request.app.state.settings
    return templates.TemplateResponse(
        request,
        "pages/arena.html",
        {
            "active_page": "arena",
            "rounds": rounds,
            "live_round": live_round,
            "upcoming_rounds": upcoming_rounds,
            "auto_advance": settings.pinwheel_auto_advance,
            **_auth_context(request, current_user),
        },
    )


@router.get("/standings", response_class=HTMLResponse)
async def standings_page(request: Request, repo: RepoDep, current_user: OptionalUser):
    """Standings page."""
    season_id = await _get_active_season_id(repo)
    standings = []
    season_phase = ""
    streaks: dict[str, int] = {}

    if season_id:
        standings = await _get_standings(repo, season_id)
        season_phase = await _get_season_phase(repo, season_id)
        all_games = await repo.get_all_games(season_id)
        if all_games:
            streaks = _compute_streaks_from_games(all_games)

    return templates.TemplateResponse(
        request,
        "pages/standings.html",
        {
            "active_page": "standings",
            "standings": standings,
            "season_phase": season_phase,
            "streaks": streaks,
            **_auth_context(request, current_user),
        },
    )


@router.get("/games/{game_id}", response_class=HTMLResponse)
async def game_page(request: Request, game_id: str, repo: RepoDep, current_user: OptionalUser):
    """Single game detail page."""
    game = await repo.get_game_result(game_id)
    if not game or not game.presented:
        raise HTTPException(404, "Game not found")

    # Team names and colors
    home_team = await repo.get_team(game.home_team_id)
    away_team = await repo.get_team(game.away_team_id)
    home_name = home_team.name if home_team else game.home_team_id
    away_name = away_team.name if away_team else game.away_team_id
    home_color = home_team.color if home_team else "#888"
    home_color2 = (
        (getattr(home_team, "color_secondary", None) or "#1a1a2e") if home_team else "#1a1a2e"
    )
    away_color = away_team.color if away_team else "#888"
    away_color2 = (
        (getattr(away_team, "color_secondary", None) or "#1a1a2e") if away_team else "#1a1a2e"
    )

    # Box scores grouped by team
    home_players = []
    away_players = []
    for bs in game.box_scores:
        h = await repo.get_hooper(bs.hooper_id)
        player = {
            "hooper_id": bs.hooper_id,
            "hooper_name": h.name if h else bs.hooper_id,
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
        (home_name, game.home_team_id, home_players, home_color),
        (away_name, game.away_team_id, away_players, away_color),
    ]

    # Build hooper-name cache from box scores already loaded
    hooper_names: dict[str, str] = {}
    for bs in game.box_scores:
        if bs.hooper_id not in hooper_names:
            h = await repo.get_hooper(bs.hooper_id)
            hooper_names[bs.hooper_id] = h.name if h else bs.hooper_id

    # Play-by-play from stored data (JSON dicts), enriched with narration
    raw_plays = game.play_by_play or []
    play_by_play = []
    for play in raw_plays:
        handler_id = play.get("ball_handler_id", "")
        def_id = play.get("defender_id", "")
        reb_id = play.get("rebound_id", "")
        enriched = {**play}
        enriched["handler_id"] = handler_id
        enriched["handler_name"] = hooper_names.get(handler_id, handler_id)
        enriched["narration"] = narrate_play(
            player=hooper_names.get(handler_id, handler_id),
            defender=hooper_names.get(def_id, def_id),
            action=play.get("action", ""),
            result=play.get("result", ""),
            points=play.get("points_scored", 0),
            move=play.get("move_activated", ""),
            rebounder=hooper_names.get(reb_id, reb_id) if reb_id else "",
            is_offensive_rebound=play.get("is_offensive_rebound", False),
            seed=play.get("possession_number", 0),
            assist_id=play.get("assist_id", ""),
        )
        play_by_play.append(enriched)

    # Report for this round + game phase
    season_id = await _get_active_season_id(repo)
    report = None
    game_phase: str | None = None
    if season_id:
        round_reports = await repo.get_reports_for_round(season_id, game.round_number, "simulation")
        if round_reports:
            report = {"content": round_reports[0].content}
        game_phase = await _get_game_phase(repo, season_id, game.round_number)

    return templates.TemplateResponse(
        request,
        "pages/game.html",
        {
            "active_page": "arena",
            "game": game,
            "home_name": home_name,
            "away_name": away_name,
            "home_color": home_color,
            "home_color2": home_color2,
            "away_color": away_color,
            "away_color2": away_color2,
            "box_score_groups": box_score_groups,
            "play_by_play": play_by_play,
            "report": report,
            "game_phase": game_phase,
            **_auth_context(request, current_user),
        },
    )


@router.get("/teams/{team_id}", response_class=HTMLResponse)
async def team_page(request: Request, team_id: str, repo: RepoDep, current_user: OptionalUser):
    """Team profile page."""
    team = await repo.get_team(team_id)
    if not team:
        raise HTTPException(404, "Team not found")

    # Use the team's own season for contextual data (standings, governors,
    # strategy, league averages).  This ensures team pages remain fully
    # populated even when a newer season is active — e.g. when a user
    # follows a link from an old game detail page.
    season_id = team.season_id
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

    # Query latest team strategy from events
    team_strategy = None
    if season_id:
        strategy_events = await repo.get_events_by_type(
            season_id=season_id,
            event_types=["strategy.set"],
        )
        for e in reversed(strategy_events):
            if e.team_id == team_id:
                team_strategy = e.payload.get("raw_text", "")
                break

    # Build hooper data with spider chart geometry
    grid_rings = compute_grid_rings()
    axes = axis_lines()
    avg_points = spider_chart_data(league_avg) if league_avg else []
    avg_poly = polygon_points(avg_points) if avg_points else ""

    hoopers = []
    for a in team.hoopers:
        hooper_pts = spider_chart_data(a.attributes) if a.attributes else []
        hoopers.append(
            {
                "id": a.id,
                "name": a.name,
                "archetype": a.archetype,
                "attributes": a.attributes,
                "is_active": a.is_active,
                "spider_points": hooper_pts,
                "spider_poly": polygon_points(hooper_pts) if hooper_pts else "",
            }
        )

    # Get governors enrolled on this team
    governors = []
    if season_id:
        governor_rows = await repo.get_governors_for_team(team_id, season_id)
        for g in governor_rows:
            governors.append(
                {
                    "id": g.id,
                    "username": g.username,
                }
            )

    return templates.TemplateResponse(
        request,
        "pages/team.html",
        {
            "active_page": "standings",
            "team": team,
            "hoopers": hoopers,
            "governors": governors,
            "team_standings": team_standings,
            "standing_position": standing_position,
            "league_name": league_name,
            "team_strategy": team_strategy,
            "grid_rings": grid_rings,
            "axis_lines": axes,
            "avg_points": avg_points,
            "avg_poly": avg_poly,
            **_auth_context(request, current_user),
        },
    )


@router.get("/hoopers/{hooper_id}", response_class=HTMLResponse)
async def hooper_page(request: Request, hooper_id: str, repo: RepoDep, current_user: OptionalUser):
    """Individual hooper profile page."""
    hooper = await repo.get_hooper(hooper_id)
    if not hooper:
        raise HTTPException(404, "Hooper not found")

    team = await repo.get_team(hooper.team_id)
    season_id = await _get_active_season_id(repo)

    # Spider chart data
    league_avg = {}
    if season_id:
        league_avg = await repo.get_league_attribute_averages(season_id)

    hooper_pts = spider_chart_data(hooper.attributes) if hooper.attributes else []
    avg_pts = spider_chart_data(league_avg) if league_avg else []

    # Game log + season averages
    box_score_rows = await repo.get_box_scores_for_hooper(hooper_id)
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

    # Check if current user is governor on this hooper's team (can edit bio)
    can_edit_bio = False
    if current_user and season_id:
        enrollment = await repo.get_player_enrollment(current_user.discord_id, season_id)
        if enrollment and enrollment[0] == hooper.team_id:
            can_edit_bio = True

    return templates.TemplateResponse(
        request,
        "pages/hooper.html",
        {
            "active_page": "standings",
            "hooper": hooper,
            "team": team,
            "spider_points": hooper_pts,
            "avg_points": avg_pts,
            "grid_rings": compute_grid_rings(),
            "axis_lines": axis_lines(),
            "spider_poly": polygon_points(hooper_pts) if hooper_pts else "",
            "avg_poly": polygon_points(avg_pts) if avg_pts else "",
            "game_log": game_log,
            "season_averages": season_averages,
            "can_edit_bio": can_edit_bio,
            **_auth_context(request, current_user),
        },
    )


@router.get("/hoopers/{hooper_id}/bio/edit", response_class=HTMLResponse)
async def hooper_bio_edit_form(
    request: Request, hooper_id: str, repo: RepoDep, current_user: OptionalUser
):
    """Return HTMX fragment with bio edit form. Governor-only."""
    hooper = await repo.get_hooper(hooper_id)
    if not hooper:
        raise HTTPException(404, "Hooper not found")

    season_id = await _get_active_season_id(repo)
    if not current_user or not season_id:
        raise HTTPException(403, "Not authorized")

    enrollment = await repo.get_player_enrollment(current_user.discord_id, season_id)
    if not enrollment or enrollment[0] != hooper.team_id:
        raise HTTPException(403, "Not authorized — must be team governor")

    html = f"""
    <form hx-post="/hoopers/{hooper_id}/bio" hx-target="#hooper-bio" hx-swap="innerHTML">
      <textarea name="backstory" rows="4" style="width:100%; background:var(--bg-input);
        color:var(--text-primary); border:1px solid var(--border); border-radius:var(--radius);
        padding:0.75rem; font-family:var(--font-body); font-size:0.9rem; resize:vertical;
        line-height:1.6;">{hooper.backstory or ""}</textarea>
      <div style="margin-top:0.5rem; display:flex; gap:0.5rem;">
        <button type="submit" class="bio-edit-btn">Save</button>
        <button type="button" class="bio-edit-btn"
                hx-get="/hoopers/{hooper_id}/bio/view" hx-target="#hooper-bio"
                hx-swap="innerHTML">Cancel</button>
      </div>
    </form>
    """
    return HTMLResponse(html)


@router.get("/hoopers/{hooper_id}/bio/view", response_class=HTMLResponse)
async def hooper_bio_view(
    request: Request, hooper_id: str, repo: RepoDep, current_user: OptionalUser
):
    """Return HTMX fragment with bio display. Used after cancel/save."""
    hooper = await repo.get_hooper(hooper_id)
    if not hooper:
        raise HTTPException(404, "Hooper not found")

    season_id = await _get_active_season_id(repo)
    can_edit = False
    if current_user and season_id:
        enrollment = await repo.get_player_enrollment(current_user.discord_id, season_id)
        if enrollment and enrollment[0] == hooper.team_id:
            can_edit = True

    no_bio = '<p class="text-muted">No bio yet.</p>'
    bio_html = f"<p>{hooper.backstory}</p>" if hooper.backstory else no_bio
    edit_btn = ""
    if can_edit:
        edit_btn = f"""
        <button class="bio-edit-btn"
                hx-get="/hoopers/{hooper_id}/bio/edit"
                hx-target="#hooper-bio"
                hx-swap="innerHTML">Edit Bio</button>
        """
    return HTMLResponse(bio_html + edit_btn)


@router.post("/hoopers/{hooper_id}/bio", response_class=HTMLResponse)
async def update_hooper_bio(
    request: Request, hooper_id: str, repo: RepoDep, current_user: OptionalUser
):
    """Update hooper bio. Governor-only."""
    hooper = await repo.get_hooper(hooper_id)
    if not hooper:
        raise HTTPException(404, "Hooper not found")

    season_id = await _get_active_season_id(repo)
    if not current_user or not season_id:
        raise HTTPException(403, "Not authorized")

    enrollment = await repo.get_player_enrollment(current_user.discord_id, season_id)
    if not enrollment or enrollment[0] != hooper.team_id:
        raise HTTPException(403, "Not authorized — must be team governor")

    form = await request.form()
    backstory = str(form.get("backstory", "")).strip()
    await repo.update_hooper_backstory(hooper_id, backstory)
    await repo.session.commit()

    # Return the view fragment
    bio_html = f"<p>{backstory}</p>" if backstory else '<p class="text-muted">No bio yet.</p>'
    edit_btn = f"""
    <button class="bio-edit-btn"
            hx-get="/hoopers/{hooper_id}/bio/edit"
            hx-target="#hooper-bio"
            hx-swap="innerHTML">Edit Bio</button>
    """
    return HTMLResponse(bio_html + edit_btn)


@router.get("/governors/{player_id}", response_class=HTMLResponse)
async def governor_profile_page(
    request: Request, player_id: str, repo: RepoDep, current_user: OptionalUser
):
    """Governor profile page -- governance record and activity history."""
    player = await repo.get_player(player_id)
    if not player:
        raise HTTPException(404, "Governor not found")

    season_id = await _get_active_season_id(repo)
    team = None
    activity: dict = {
        "proposals_submitted": 0,
        "proposals_passed": 0,
        "proposals_failed": 0,
        "votes_cast": 0,
        "proposal_list": [],
        "token_balance": None,
    }

    if player.team_id:
        team = await repo.get_team(player.team_id)

    if season_id:
        activity = await repo.get_governor_activity(player_id, season_id)

    return templates.TemplateResponse(
        request,
        "pages/governor.html",
        {
            "active_page": "governance",
            "player": player,
            "team": team,
            "activity": activity,
            **_auth_context(request, current_user),
        },
    )


@router.get("/governance", response_class=HTMLResponse)
async def governance_page(request: Request, repo: RepoDep, current_user: OptionalUser):
    """Governance audit trail — proposals, outcomes, vote totals.

    Publicly viewable. Proposing and voting require Discord auth
    (via bot slash commands).
    """
    season_id = await _get_active_season_id(repo)
    proposals = []
    rules_changed = []
    season_phase = ""

    if season_id:
        season_phase = await _get_season_phase(repo, season_id)

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
                pid,
                {"yes": 0.0, "no": 0.0, "count": 0},
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

            proposals.append(
                {
                    "id": pid,
                    "governor_id": p.governor_id,
                    "raw_text": p.raw_text,
                    "status": status,
                    "tier": p.tier,
                    "interpretation": interp,
                    "vote_tally": tally,
                }
            )

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
            "season_phase": season_phase,
            **_auth_context(request, current_user),
        },
    )


# Human-readable rule display metadata, grouped by tier.
# Each rule: (param_key, display_label, description)
_GAME_MECHANICS_RULES = [
    ("quarter_minutes", "Quarter Length", "Minutes per quarter."),
    ("shot_clock_seconds", "Shot Clock", "Seconds to get a shot off."),
    ("three_point_value", "Three-Pointer Value", "Points for a made three."),
    ("two_point_value", "Two-Pointer Value", "Points for a mid-range or at-rim shot."),
    ("free_throw_value", "Free Throw Value", "Points per made free throw."),
    ("personal_foul_limit", "Personal Foul Limit", "Fouls before a player fouls out."),
    ("team_foul_bonus_threshold", "Team Foul Bonus", "Team fouls before bonus free throws."),
    ("three_point_distance", "Three-Point Distance", "Distance of the arc in feet."),
    ("elam_trigger_quarter", "Elam Trigger Quarter", "After this quarter, first to target wins."),
    ("elam_margin", "Elam Target Margin", "Added to leading score for the target."),
    ("halftime_stamina_recovery", "Halftime Recovery", "Stamina recovered at halftime (0\u20131)."),
    (
        "quarter_break_stamina_recovery",
        "Quarter Break Recovery",
        "Stamina recovered between quarters (0\u20131).",
    ),
    ("safety_cap_possessions", "Safety Cap", "Max possessions before force-ending."),
]

_HOOPER_BEHAVIOR_RULES = [
    ("max_shot_share", "Max Shot Share", "Max fraction of team shots for one player."),
    ("min_pass_per_possession", "Min Passes", "Required passes before a shot attempt."),
    ("home_court_enabled", "Home Court Advantage", "Whether home court provides a boost."),
    ("home_crowd_boost", "Home Crowd Boost", "Scoring bonus from a friendly crowd."),
    ("away_fatigue_factor", "Away Fatigue", "Extra stamina drain for visitors."),
    ("crowd_pressure", "Crowd Pressure", "Defensive boost from the home crowd."),
    ("altitude_stamina_penalty", "Altitude Penalty", "Extra drain at high-altitude venues."),
    ("travel_fatigue_enabled", "Travel Fatigue", "Whether travel distance affects stamina."),
    ("travel_fatigue_per_mile", "Travel Fatigue Rate", "Stamina drain per mile of travel."),
]

_LEAGUE_STRUCTURE_RULES = [
    ("teams_count", "Teams in League", "Number of teams in the league."),
    ("round_robins_per_season", "Round Robins", "Times each team plays every other."),
    ("playoff_teams", "Playoff Teams", "Teams that qualify for playoffs."),
    ("playoff_semis_best_of", "Semifinal Series", "Best-of format for semifinals."),
    ("playoff_finals_best_of", "Finals Series", "Best-of format for the championship."),
]

_META_GOVERNANCE_RULES = [
    ("proposals_per_window", "Proposals per Window", "Max proposals per governance window."),
    ("vote_threshold", "Vote Threshold", "Votes needed to pass (0.5 = majority)."),
]

RULE_TIERS = [
    {
        "key": "game_mechanics",
        "title": "Game Mechanics",
        "subtitle": "The core numbers that define how basketball works.",
        "color": "var(--accent-highlight)",
        "rules": _GAME_MECHANICS_RULES,
    },
    {
        "key": "agent_behavior",
        "title": "Hooper Behavior",
        "subtitle": "How players interact with the court and crowd.",
        "color": "var(--accent-governance)",
        "rules": _HOOPER_BEHAVIOR_RULES,
    },
    {
        "key": "league_structure",
        "title": "League Structure",
        "subtitle": "Season format, scheduling, and playoffs.",
        "color": "var(--accent-score)",
        "rules": _LEAGUE_STRUCTURE_RULES,
    },
    {
        "key": "meta_governance",
        "title": "Meta-Governance",
        "subtitle": "The rules about rules.",
        "color": "var(--accent-report)",
        "rules": _META_GOVERNANCE_RULES,
    },
]


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

    # Build tiered display data
    ruleset_dict = ruleset.model_dump()
    field_info = RuleSet.model_fields
    tiers = []
    for tier in RULE_TIERS:
        tier_rules = []
        for param, label, desc in tier["rules"]:
            value = ruleset_dict.get(param)
            fi = field_info.get(param)
            range_str = ""
            if fi and fi.metadata:
                bounds = []
                for m in fi.metadata:
                    if hasattr(m, "ge"):
                        bounds.append(f"{m.ge}")
                    if hasattr(m, "le"):
                        bounds.append(f"{m.le}")
                if len(bounds) == 2:
                    range_str = f"{bounds[0]}–{bounds[1]}"
            changed = param in changes_from_default
            tier_rules.append(
                {
                    "param": param,
                    "label": label,
                    "desc": desc,
                    "value": value,
                    "range": range_str,
                    "changed": changed,
                }
            )
        tiers.append(
            {
                "key": tier["key"],
                "title": tier["title"],
                "subtitle": tier["subtitle"],
                "color": tier["color"],
                "rules": tier_rules,
            }
        )

    community_changes = len(changes_from_default)

    return templates.TemplateResponse(
        request,
        "pages/rules.html",
        {
            "active_page": "rules",
            "tiers": tiers,
            "community_changes": community_changes,
            "changes_from_default": changes_from_default,
            "rule_history": rule_history,
            **_auth_context(request, current_user),
        },
    )


@router.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request, repo: RepoDep, current_user: OptionalUser):
    """Reports archive page."""
    season_id = await _get_active_season_id(repo)
    reports = []
    season_phase = ""

    if season_id:
        season_phase = await _get_season_phase(repo, season_id)

        # Build a map of round_number -> phase for playoff-aware labelling
        round_phases: dict[int, str | None] = {}

        for rn in range(100, 0, -1):
            round_reports = await repo.get_reports_for_round(season_id, rn)
            for m in round_reports:
                if m.report_type != "private":
                    # Lazily compute phase for this round
                    if rn not in round_phases:
                        round_phases[rn] = await _get_game_phase(
                            repo, season_id, rn,
                        )
                    reports.append(
                        {
                            "report_type": m.report_type,
                            "round_number": m.round_number,
                            "content": m.content,
                            "created_at": (
                                m.created_at.isoformat() if m.created_at else ""
                            ),
                            "phase": round_phases.get(rn),
                        }
                    )
            if reports and rn < 95 and len(reports) > 20:
                break

    return templates.TemplateResponse(
        request,
        "pages/reports.html",
        {
            "active_page": "reports",
            "reports": reports,
            "season_phase": season_phase,
            **_auth_context(request, current_user),
        },
    )


@router.get("/post", response_class=HTMLResponse)
async def newspaper_page(request: Request, repo: RepoDep, current_user: OptionalUser):
    """The Pinwheel Post — newspaper-style round summary page."""
    from pinwheel.ai.insights import generate_newspaper_headlines_mock

    season_id, season_name = await _get_active_season(repo)
    headline = ""
    subhead = ""
    sim_report = ""
    gov_report = ""
    impact_report = ""
    highlight_reel = ""
    standings: list[dict] = []
    hot_players: list[dict] = []
    current_round = 0

    if season_id:
        # Find latest round
        for rn in range(1, 100):
            games = await repo.get_games_for_round(season_id, rn)
            if games:
                current_round = rn
            else:
                break

        if current_round > 0:
            # Fetch reports for the latest round
            sim_reports = await repo.get_reports_for_round(
                season_id, current_round, "simulation",
            )
            if sim_reports:
                sim_report = sim_reports[0].content

            gov_reports = await repo.get_reports_for_round(
                season_id, current_round, "governance",
            )
            if gov_reports:
                gov_report = gov_reports[0].content

            impact_reports = await repo.get_reports_for_round(
                season_id, current_round, "impact_validation",
            )
            if impact_reports:
                impact_report = impact_reports[0].content

            # Build standings
            standings = await _get_standings(repo, season_id)

            # Build round data for headline generation
            round_games = await repo.get_games_for_round(season_id, current_round)
            team_names: dict[str, str] = {}
            game_summaries: list[dict] = []
            for g in round_games:
                for tid in (g.home_team_id, g.away_team_id):
                    if tid not in team_names:
                        t = await repo.get_team(tid)
                        team_names[tid] = t.name if t else tid
                game_summaries.append({
                    "home_team_name": team_names.get(g.home_team_id, "?"),
                    "away_team_name": team_names.get(g.away_team_id, "?"),
                    "home_score": g.home_score,
                    "away_score": g.away_score,
                    "winner_team_id": g.winner_team_id,
                    "winner_team_name": team_names.get(g.winner_team_id, "?"),
                })

            round_data = {
                "round_number": current_round,
                "games": game_summaries,
                "governance": {},
            }

            headlines = generate_newspaper_headlines_mock(round_data, current_round)
            headline = headlines.get("headline", "")
            subhead = headlines.get("subhead", "")

    ctx = {
        "active_page": "post",
        "season_name": season_name or "",
        "round_number": current_round,
        "headline": headline,
        "subhead": subhead,
        "sim_report": sim_report,
        "gov_report": gov_report,
        "impact_report": impact_report,
        "highlight_reel": highlight_reel,
        "standings": standings,
        "hot_players": hot_players,
    }
    return templates.TemplateResponse(
        request,
        "pages/newspaper.html",
        {**ctx, **_auth_context(request, current_user)},
    )


@router.get("/playoffs", response_class=HTMLResponse)
async def playoffs_page(request: Request, repo: RepoDep, current_user: OptionalUser):
    """Playoff bracket visualization page."""
    from pinwheel.api.games import _build_bracket_data

    bracket = await _build_bracket_data(repo)

    return templates.TemplateResponse(
        request,
        "pages/playoffs.html",
        {
            "active_page": "playoffs",
            "bracket": bracket,
            **_auth_context(request, current_user),
        },
    )


@router.get("/seasons/archive", response_class=HTMLResponse)
async def season_archives_page(request: Request, repo: RepoDep, current_user: OptionalUser):
    """List all archived seasons."""
    archives = await repo.get_all_archives()
    archive_list = []
    for a in archives:
        archive_list.append(
            {
                "season_id": a.season_id,
                "season_name": a.season_name,
                "champion_team_name": a.champion_team_name,
                "total_games": a.total_games,
                "total_proposals": a.total_proposals,
                "total_rule_changes": a.total_rule_changes,
                "governor_count": a.governor_count,
                "created_at": a.created_at.isoformat() if a.created_at else "",
            }
        )

    return templates.TemplateResponse(
        request,
        "pages/season_archive.html",
        {
            "active_page": "archives",
            "archives": archive_list,
            "archive": None,
            **_auth_context(request, current_user),
        },
    )


@router.get("/seasons/archive/{season_id}", response_class=HTMLResponse)
async def season_archive_detail(
    request: Request, season_id: str, repo: RepoDep, current_user: OptionalUser
):
    """View a specific season's archive."""
    archive = await repo.get_season_archive(season_id)
    if not archive:
        raise HTTPException(404, "Archive not found")

    archive_data = {
        "season_id": archive.season_id,
        "season_name": archive.season_name,
        "champion_team_id": archive.champion_team_id,
        "champion_team_name": archive.champion_team_name,
        "final_standings": archive.final_standings or [],
        "final_ruleset": archive.final_ruleset or {},
        "rule_change_history": archive.rule_change_history or [],
        "total_games": archive.total_games,
        "total_proposals": archive.total_proposals,
        "total_rule_changes": archive.total_rule_changes,
        "governor_count": archive.governor_count,
        "created_at": archive.created_at.isoformat() if archive.created_at else "",
    }

    return templates.TemplateResponse(
        request,
        "pages/season_archive.html",
        {
            "active_page": "archives",
            "archives": None,
            "archive": archive_data,
            **_auth_context(request, current_user),
        },
    )


@router.get("/history", response_class=HTMLResponse)
async def history_page(request: Request, repo: RepoDep, current_user: OptionalUser):
    """Hall of History -- index of all past seasons with championship banners."""
    archives = await repo.get_all_archives()
    archive_list = []
    for a in archives:
        # Extract memorial data if available
        memorial = a.memorial or {}
        narrative_excerpt = str(memorial.get("season_narrative", ""))[:200]

        archive_list.append(
            {
                "season_id": a.season_id,
                "season_name": a.season_name,
                "champion_team_name": a.champion_team_name,
                "total_games": a.total_games,
                "total_proposals": a.total_proposals,
                "total_rule_changes": a.total_rule_changes,
                "governor_count": a.governor_count,
                "narrative_excerpt": narrative_excerpt,
                "has_memorial": bool(memorial.get("season_narrative")),
                "created_at": a.created_at.isoformat() if a.created_at else "",
            }
        )

    return templates.TemplateResponse(
        request,
        "pages/history.html",
        {
            "active_page": "history",
            "archives": archive_list,
            **_auth_context(request, current_user),
        },
    )


@router.get("/seasons/{season_id}/memorial", response_class=HTMLResponse)
async def memorial_page(
    request: Request, season_id: str, repo: RepoDep, current_user: OptionalUser
):
    """Full memorial page for a completed season."""
    archive = await repo.get_season_archive(season_id)
    if not archive:
        raise HTTPException(404, "Season archive not found")

    memorial = archive.memorial or {}

    # Build structured memorial data for the template
    memorial_data = {
        "season_id": archive.season_id,
        "season_name": archive.season_name,
        "champion_team_id": archive.champion_team_id,
        "champion_team_name": archive.champion_team_name,
        "total_games": archive.total_games,
        "total_proposals": archive.total_proposals,
        "total_rule_changes": archive.total_rule_changes,
        "governor_count": archive.governor_count,
        "final_standings": archive.final_standings or [],
        "final_ruleset": archive.final_ruleset or {},
        "rule_change_history": archive.rule_change_history or [],
        # AI narrative sections
        "season_narrative": memorial.get("season_narrative", ""),
        "championship_recap": memorial.get("championship_recap", ""),
        "champion_profile": memorial.get("champion_profile", ""),
        "governance_legacy": memorial.get("governance_legacy", ""),
        # Computed data sections
        "awards": memorial.get("awards", []),
        "statistical_leaders": memorial.get("statistical_leaders", {}),
        "key_moments": memorial.get("key_moments", []),
        "head_to_head": memorial.get("head_to_head", []),
        "rule_timeline": memorial.get("rule_timeline", []),
        # Metadata
        "generated_at": memorial.get("generated_at", ""),
        "model_used": memorial.get("model_used", ""),
        "created_at": archive.created_at.isoformat() if archive.created_at else "",
    }

    return templates.TemplateResponse(
        request,
        "pages/memorial.html",
        {
            "active_page": "history",
            "memorial": memorial_data,
            **_auth_context(request, current_user),
        },
    )


@router.get("/admin", response_class=HTMLResponse)
async def admin_landing_page(request: Request, current_user: OptionalUser):
    """Admin landing page — hub for admin tools.

    Redirects unauthenticated users to login. Returns 403 for non-admins.
    """
    settings = request.app.state.settings
    if current_user is None:
        oauth_enabled = bool(settings.discord_client_id and settings.discord_client_secret)
        if oauth_enabled:
            return RedirectResponse("/auth/login", status_code=302)
        raise HTTPException(403, "Not authorized")

    admin_id = settings.pinwheel_admin_discord_id
    if not admin_id or current_user.discord_id != admin_id:
        raise HTTPException(403, "Not authorized")

    return templates.TemplateResponse(
        request,
        "pages/admin.html",
        {"active_page": "admin", **_auth_context(request, current_user)},
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
