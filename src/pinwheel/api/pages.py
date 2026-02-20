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
from pinwheel.core.narrative_standings import (
    compute_magic_numbers,
    compute_most_improved,
    compute_narrative_callouts,
    compute_standings_trajectory,
    compute_strength_of_schedule,
)
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
    except (ValueError, TypeError):
        return []


templates = Jinja2Templates(directory=str(PROJECT_ROOT / "templates"))


def _light_safe(hex_color: str) -> str:
    """Darken high-luminance hex colors for readability on light backgrounds.

    Colors with relative luminance > 0.5 (e.g. gold #FFD700, light blue
    #88BBDD) get darkened ~40% and saturation-boosted.  All other colors
    pass through unchanged.
    """
    c = hex_color.lstrip("#")
    if len(c) != 6:
        return hex_color
    try:
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except ValueError:
        return hex_color
    # Relative luminance (sRGB)
    luminance = 0.2126 * (r / 255) + 0.7152 * (g / 255) + 0.0722 * (b / 255)
    if luminance <= 0.5:
        return hex_color
    # Darken by 40%
    factor = 0.6
    rd, gd, bd = int(r * factor), int(g * factor), int(b * factor)
    return f"#{rd:02x}{gd:02x}{bd:02x}"


templates.env.filters["light_safe"] = _light_safe


def _prose_to_html(text: str) -> str:
    """Convert markdown/prose text to HTML.

    Uses the markdown library to render headings, bold, italic, lists, etc.
    Sanitizes the resulting HTML with nh3 to prevent XSS from AI-generated
    content that may include raw HTML or script tags.
    """
    import markdown
    import nh3

    text = text.strip()
    raw_html = markdown.markdown(text, extensions=["nl2br", "smarty"])
    return nh3.clean(raw_html)


templates.env.filters["prose"] = _prose_to_html


def _auth_context(request: Request, current_user: SessionUser | None) -> dict:
    """Build auth-related template context available on every page."""
    settings = request.app.state.settings
    oauth_enabled = bool(settings.discord_client_id and settings.discord_client_secret)
    admin_id = settings.pinwheel_admin_discord_id
    is_admin = current_user is not None and bool(admin_id) and current_user.discord_id == admin_id
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


async def _get_standings(
    repo: RepoDep,
    season_id: str,
    phase_filter: str | None = None,
) -> list[dict]:
    """Compute standings for a season.

    Args:
        phase_filter: When ``"regular"``, include only regular-season games
            (phase is ``None`` or ``"regular"``).  When ``"playoff"``, include
            only post-season games (phase in ``"playoff"``, ``"semifinal"``,
            ``"finals"``).  ``None`` includes all games (original behaviour).
    """
    games = await repo.get_all_games(season_id)

    if phase_filter == "regular":
        games = [g for g in games if getattr(g, "phase", None) in (None, "regular")]
    elif phase_filter == "playoff":
        games = [
            g for g in games if getattr(g, "phase", None) in ("playoff", "semifinal", "finals")
        ]

    all_results: list[dict] = [
        {
            "home_team_id": g.home_team_id,
            "away_team_id": g.away_team_id,
            "home_score": g.home_score,
            "away_score": g.away_score,
            "winner_team_id": g.winner_team_id,
        }
        for g in games
    ]
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
    """Return the phase for a specific round's games ('semifinal', 'finals', or None).

    Reads the precise phase directly from the schedule entry when available.
    Falls back to inference (comparing team pairs against the initial playoff
    round) for legacy entries stored as ``"playoff"``.
    """
    schedule = await repo.get_schedule_for_round(season_id, round_number)
    if not schedule:
        return None
    entry_phase = schedule[0].phase
    if entry_phase in ("semifinal", "finals"):
        return entry_phase
    if entry_phase != "playoff":
        return None
    # Legacy fallback: infer from team pairs
    full_playoff = await repo.get_full_schedule(season_id, phase="playoff")
    if not full_playoff:
        return "semifinal"
    earliest_round = min(s.round_number for s in full_playoff)
    initial_pairs = [
        frozenset({s.home_team_id, s.away_team_id})
        for s in full_playoff
        if s.round_number == earliest_round
    ]
    current_pairs = [frozenset({s.home_team_id, s.away_team_id}) for s in schedule]
    if len(initial_pairs) >= 2 and all(p in initial_pairs for p in current_pairs):
        return "semifinal"
    return "finals"


async def _generate_series_description(
    phase: str,
    home_team_name: str,
    away_team_name: str,
    home_wins: int,
    away_wins: int,
    best_of: int,
    wins_needed: int,
) -> str | None:
    """Call Haiku to generate a natural-language series description.

    Returns None on any failure (caller falls back to template).
    """
    import os

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None

    clinched = home_wins >= wins_needed or away_wins >= wins_needed
    if home_wins > away_wins:
        leader = home_team_name
        leader_wins, trailer_wins = home_wins, away_wins
    elif away_wins > home_wins:
        leader = away_team_name
        leader_wins, trailer_wins = away_wins, home_wins
    else:
        leader = ""
        leader_wins, trailer_wins = home_wins, away_wins

    phase_label = "Championship Finals" if phase == "finals" else "Semifinal Series"
    is_sweep = clinched and min(home_wins, away_wins) == 0

    situation = (
        f"Phase: {phase_label}\n"
        f"Teams: {home_team_name} vs {away_team_name}\n"
        f"Series record: {home_team_name} {home_wins}, {away_team_name} {away_wins}\n"
        f"Best of {best_of} (first to {wins_needed} wins)\n"
        f"Clinched: {clinched}\n"
        f"Sweep: {is_sweep}\n"
    )
    if leader:
        situation += f"Leader: {leader} ({leader_wins}-{trailer_wins})\n"

    prompt = (
        "Write a short (1 sentence, max 15 words) series description banner "
        "for a basketball playoff game. Be natural and situationally aware. "
        "If clinched, describe the outcome. If tied, describe the tension. "
        "If someone leads, describe the stakes. No quotes. No period at the end."
    )

    try:
        import anthropic
        import httpx

        _timeout = httpx.Timeout(10.0, connect=3.0)
        client = anthropic.AsyncAnthropic(api_key=api_key, timeout=_timeout)
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            system=[{"type": "text", "text": prompt, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": situation}],
        )
        text = response.content[0].text.strip().strip('"').strip(".")
        # Sanity check: not empty, not absurdly long
        if text and len(text) < 200:
            return text
        return None
    except Exception:  # Last-resort handler — AI (Anthropic), httpx, and response-parse errors
        return None


def _build_series_description_fallback(
    phase: str,
    phase_label: str,
    home_team_name: str,
    away_team_name: str,
    home_wins: int,
    away_wins: int,
    wins_needed: int,
) -> str:
    """Template fallback for series descriptions (used when Haiku unavailable)."""
    if home_wins >= wins_needed:
        outcome = "championship" if phase == "finals" else "series"
        return f"{phase_label} · {home_team_name} win {outcome} {home_wins}-{away_wins}"
    if away_wins >= wins_needed:
        outcome = "championship" if phase == "finals" else "series"
        return f"{phase_label} · {away_team_name} win {outcome} {away_wins}-{home_wins}"
    if home_wins == away_wins:
        record_text = f"Series tied {home_wins}-{away_wins}"
    elif home_wins > away_wins:
        record_text = f"{home_team_name} lead {home_wins}-{away_wins}"
    else:
        record_text = f"{away_team_name} lead {away_wins}-{home_wins}"
    clinch_text = (
        f"First to {wins_needed} wins is champion"
        if phase == "finals"
        else f"First to {wins_needed} wins advances"
    )
    return f"{phase_label} · {record_text} · {clinch_text}"


async def build_series_context(
    phase: str,
    home_team_name: str,
    away_team_name: str,
    home_wins: int,
    away_wins: int,
    best_of: int,
) -> dict:
    """Build a series context dict for display in the arena template.

    Calls Haiku to generate a natural-language description; falls back to
    a rigid template when the API is unavailable.

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
    phase_label = "CHAMPIONSHIP FINALS" if phase == "finals" else "SEMIFINAL SERIES"

    # Try Haiku for a natural description
    haiku_desc = await _generate_series_description(
        phase,
        home_team_name,
        away_team_name,
        home_wins,
        away_wins,
        best_of,
        wins_needed,
    )
    description = haiku_desc or _build_series_description_fallback(
        phase,
        phase_label,
        home_team_name,
        away_team_name,
        home_wins,
        away_wins,
        wins_needed,
    )

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
        ruleset.playoff_finals_best_of if game_phase == "finals" else ruleset.playoff_semis_best_of
    )

    # Get series record from playoff games
    from pinwheel.core.game_loop import _get_playoff_series_record

    home_wins, away_wins, _ = await _get_playoff_series_record(
        repo, season_id, home_team_id, away_team_id
    )

    return await build_series_context(
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


def _compute_what_changed(
    standings: list[dict],
    prev_standings: list[dict],
    streaks: dict[str, int],
    prev_streaks: dict[str, int],
    rule_changes: list[dict],
    season_phase: str,
    post_headline: str = "",
    latest_round_games: list[dict] | None = None,
    playoff_teams: int = 4,
    total_regular_rounds: int = 0,
    current_round: int = 0,
    champion_team_name: str = "",
) -> list[str]:
    """Compute 1-5 "what changed" signals for the home page.

    Returns a list of short, punchy change signals in lede priority order:
    champion crowned > team eliminated > playoff clinch > upset >
    streak change > blowout/classic > standings shift > rule change.

    Args:
        standings: Current standings dicts (must have team_id, team_name, wins).
        prev_standings: Standings from the previous round.
        streaks: Current win/loss streak per team_id.
        prev_streaks: Previous round's streaks.
        rule_changes: List of dicts with parameter/new_value keys.
        season_phase: Current season phase string.
        post_headline: Most recent Post headline (fallback).
        latest_round_games: Dicts with home_name, away_name, home_score,
            away_score, winner_team_id, home_team_id, away_team_id for
            the latest round.
        playoff_teams: Number of teams that make the playoffs.
        total_regular_rounds: Total rounds in the regular season schedule.
        current_round: Current round number.

    When no change signals are detected and *post_headline* is provided, falls
    back to the most recent Post headline (prefixed with "Latest:").
    """
    signals: list[str] = []

    # Champion signal — overrides all else
    if season_phase in ("championship", "offseason", "completed"):
        champion = champion_team_name
        if not champion and standings:
            champion = standings[0]["team_name"]
        if champion:
            signals.append(f"{champion} are your champions.")
        return signals[:1]

    # --- Playoff clinch / elimination detection ---
    if (
        standings
        and prev_standings
        and total_regular_rounds > 0
        and current_round > 0
        and season_phase not in ("playoffs", "championship")
    ):
        remaining = total_regular_rounds - current_round
        for idx, team in enumerate(standings):
            tid = team["team_id"]
            tname = team["team_name"]
            wins = team.get("wins", 0)

            # Clinch: team's wins already exceed the max possible wins
            # for the team currently on the playoff bubble
            if idx < playoff_teams and len(standings) > playoff_teams:
                bubble = standings[playoff_teams]
                bubble_max = bubble.get("wins", 0) + remaining
                if wins > bubble_max:
                    # Check this is newly clinched (wasn't clinched last round)
                    prev_team = next((s for s in prev_standings if s["team_id"] == tid), None)
                    prev_bubble = (
                        prev_standings[playoff_teams]
                        if len(prev_standings) > playoff_teams
                        else None
                    )
                    was_clinched = False
                    if prev_team and prev_bubble:
                        prev_remaining = total_regular_rounds - (current_round - 1)
                        prev_bubble_max = prev_bubble.get("wins", 0) + prev_remaining
                        was_clinched = prev_team.get("wins", 0) > prev_bubble_max
                    if not was_clinched:
                        seed = idx + 1
                        suffix = _ordinal_suffix(seed)
                        signals.append(f"{tname} clinched the {seed}{suffix} seed.")

            # Elimination: team can no longer reach playoff spot
            if idx >= playoff_teams:
                max_wins = wins + remaining
                cutoff_team = standings[playoff_teams - 1]
                cutoff_wins = cutoff_team.get("wins", 0)
                if max_wins < cutoff_wins:
                    # Check if newly eliminated
                    prev_team = next(
                        (s for s in prev_standings if s["team_id"] == tid),
                        None,
                    )
                    was_eliminated = False
                    if prev_team and prev_standings:
                        prev_remaining = total_regular_rounds - (current_round - 1)
                        prev_max = prev_team.get("wins", 0) + prev_remaining
                        prev_cutoff = (
                            prev_standings[playoff_teams - 1]
                            if len(prev_standings) >= playoff_teams
                            else None
                        )
                        if prev_cutoff:
                            was_eliminated = prev_max < prev_cutoff.get("wins", 0)
                    if not was_eliminated:
                        signals.append(f"{tname} eliminated from playoff contention.")

    # --- Upset detection ---
    if latest_round_games and standings and prev_standings:
        prev_positions = {s["team_id"]: idx for idx, s in enumerate(prev_standings)}
        for game in latest_round_games:
            winner_id = game.get("winner_team_id", "")
            home_id = game.get("home_team_id", "")
            away_id = game.get("away_team_id", "")
            loser_id = away_id if winner_id == home_id else home_id
            if not winner_id or not loser_id:
                continue
            winner_pos = prev_positions.get(winner_id)
            loser_pos = prev_positions.get(loser_id)
            if winner_pos is not None and loser_pos is not None and winner_pos - loser_pos >= 2:
                winner_name = (
                    game.get("home_name", "?")
                    if winner_id == home_id
                    else game.get("away_name", "?")
                )
                loser_name = (
                    game.get("away_name", "?")
                    if winner_id == home_id
                    else game.get("home_name", "?")
                )
                signals.append(f"Upset! {winner_name} knocked off {loser_name}.")

    # Streak changes — new 3+ streaks or broken 3+ streaks
    for team_id, streak in streaks.items():
        prev_streak = prev_streaks.get(team_id, 0)
        team_name = next(
            (s["team_name"] for s in standings if s["team_id"] == team_id),
            "Unknown",
        )

        # New streak (crossed threshold)
        if abs(streak) >= 3 and abs(prev_streak) < 3:
            if streak > 0:
                signals.append(f"{team_name} on a {streak}-game win streak.")
            else:
                signals.append(f"{team_name} on a {abs(streak)}-game losing streak.")
        # Broken streak
        elif abs(prev_streak) >= 3 and abs(streak) < 3:
            if prev_streak > 0:
                signals.append(f"{team_name} snapped their {prev_streak}-game win streak.")
            else:
                signals.append(f"{team_name} snapped their {abs(prev_streak)}-game losing streak.")

    # --- Blowout / classic game signals ---
    if latest_round_games:
        for game in latest_round_games:
            home_score = game.get("home_score", 0)
            away_score = game.get("away_score", 0)
            margin = abs(home_score - away_score)
            winner_id = game.get("winner_team_id", "")
            home_id = game.get("home_team_id", "")
            winner_name = (
                game.get("home_name", "?") if winner_id == home_id else game.get("away_name", "?")
            )
            if margin >= 20:
                signals.append(f"{winner_name} blew it open with a {margin}-point rout.")
            elif margin <= 2 and (home_score + away_score) > 0:
                signals.append(f"Instant classic: {home_score}-{away_score} nailbiter.")

    # Standings movement — compare current to previous
    if standings and prev_standings:
        # Build position maps
        prev_positions = {s["team_id"]: idx for idx, s in enumerate(prev_standings)}
        curr_positions = {s["team_id"]: idx for idx, s in enumerate(standings)}

        # Find biggest climber and biggest faller
        biggest_climb = 0
        biggest_fall = 0
        climber_name = ""
        faller_name = ""

        for team_id in curr_positions:
            if team_id not in prev_positions:
                continue
            delta = prev_positions[team_id] - curr_positions[team_id]
            if delta > biggest_climb:
                biggest_climb = delta
                climber_name = next(s["team_name"] for s in standings if s["team_id"] == team_id)
            if delta < biggest_fall:
                biggest_fall = delta
                faller_name = next(s["team_name"] for s in standings if s["team_id"] == team_id)

        if biggest_climb >= 2:
            climber_id = next(s["team_id"] for s in standings if s["team_name"] == climber_name)
            new_pos = curr_positions[climber_id]
            suffix = _ordinal_suffix(new_pos + 1)
            signals.append(f"{climber_name} climbed to {new_pos + 1}{suffix} place.")
        if biggest_fall <= -2:
            faller_id = next(s["team_id"] for s in standings if s["team_name"] == faller_name)
            new_pos = curr_positions[faller_id]
            suffix = _ordinal_suffix(new_pos + 1)
            signals.append(f"{faller_name} dropped to {new_pos + 1}{suffix} place.")

    # Rule changes
    for rc in rule_changes:
        param = rc.get("parameter", "a rule")
        new_val = rc.get("new_value")
        signals.append(f"{param.replace('_', ' ').title()} changed to {new_val}.")

    result = signals[:5]

    # Fallback: when no change signals detected, surface the Post headline
    if not result and post_headline:
        result.append(f"Latest: {post_headline}")

    return result


@router.get("/", response_class=HTMLResponse)
async def home_page(request: Request, repo: RepoDep, current_user: OptionalUser) -> HTMLResponse:
    """Home page — living dashboard for the league."""
    season_id, season_name = await _get_active_season(repo)
    latest_report = None
    standings = []
    latest_round_games: list[dict] = []
    current_round = 0
    total_games = 0
    upcoming_rounds: list[dict] = []
    team_colors: dict[str, str] = {}
    what_changed_signals: list[str] = []

    playoff_standings: dict = {}
    semis_best_of = 3
    finals_best_of = 5

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
        current_round = await repo.get_latest_round_number(season_id) or 0

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
        remaining_entries: list = [e for e in full_schedule if e.round_number > current_round]

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
                    "start_time": (start_times[idx] if idx < len(start_times) else None),
                    "games": slot_games,
                }
            )

    # Phase and streaks for template enrichment
    season_phase = ""
    streaks: dict[str, int] = {}
    if season_id:
        season_phase = await _get_season_phase(repo, season_id)

        # During/after playoffs, show series matchups and regular-season-only standings
        if season_phase in ("playoffs", "championship", "offseason"):
            from pinwheel.api.games import _build_bracket_data

            bracket = await _build_bracket_data(repo)
            playoff_standings = bracket  # series-based bracket data
            standings = await _get_standings(
                repo,
                season_id,
                phase_filter="regular",
            )
            # Get best-of values from ruleset
            _season_obj = await repo.get_season(season_id)
            if _season_obj and _season_obj.current_ruleset:
                _rs = RuleSet(**_season_obj.current_ruleset)
                semis_best_of = _rs.playoff_semis_best_of
                finals_best_of = _rs.playoff_finals_best_of

        all_games = await repo.get_all_games(season_id)
        if all_games:
            streaks = _compute_streaks_from_games(all_games)

        # Compute "What Changed" signals
        if current_round > 0:
            # Previous round's standings (exclude latest round)
            prev_standings: list[dict] = []
            if current_round > 1:
                prev_results: list[dict] = []
                for rn in range(1, current_round):
                    rg = await repo.get_games_for_round(season_id, rn)
                    for g in rg:
                        prev_results.append(
                            {
                                "home_team_id": g.home_team_id,
                                "away_team_id": g.away_team_id,
                                "home_score": g.home_score,
                                "away_score": g.away_score,
                                "winner_team_id": g.winner_team_id,
                            }
                        )
                prev_standings = compute_standings(prev_results)
                for s in prev_standings:
                    team = await repo.get_team(s["team_id"])
                    if team:
                        s["team_name"] = team.name

            # Previous streaks
            prev_streaks: dict[str, int] = {}
            if current_round > 1:
                prev_games = [g for g in all_games if g.round_number < current_round]
                if prev_games:
                    prev_streaks = _compute_streaks_from_games(prev_games)

            # Rule changes in latest round
            rule_change_events = await repo.get_events_by_type(
                season_id=season_id,
                event_types=["rule.enacted"],
            )
            latest_rule_changes = [
                e.payload for e in rule_change_events if e.round_number == current_round
            ]

            # Total regular-season rounds and playoff slots for
            # clinch / elimination detection
            regular_schedule = await repo.get_full_schedule(
                season_id,
                phase="regular",
            )
            wc_total_rounds = (
                max(s.round_number for s in regular_schedule) if regular_schedule else 0
            )
            season_obj = await repo.get_season(season_id)
            if season_obj and season_obj.current_ruleset:
                wc_ruleset = RuleSet(**season_obj.current_ruleset)
            else:
                wc_ruleset = DEFAULT_RULESET
            wc_playoff_teams = wc_ruleset.playoff_teams

            # Compute signals
            wc_champion_name = ""
            if season_obj and season_obj.config:
                wc_champion_name = season_obj.config.get("champion_team_name", "")

            what_changed_signals = _compute_what_changed(
                standings=standings,
                prev_standings=prev_standings,
                streaks=streaks,
                prev_streaks=prev_streaks,
                rule_changes=latest_rule_changes,
                season_phase=season_phase,
                latest_round_games=latest_round_games,
                playoff_teams=wc_playoff_teams,
                total_regular_rounds=wc_total_rounds,
                current_round=current_round,
                champion_team_name=wc_champion_name,
            )

    # --- Pinwheel Post data (newspaper inlined on home page) ---
    post_headline = ""
    post_subhead = ""
    post_sim_report = ""
    post_gov_report = ""
    post_highlight_reel = ""
    post_hot_players: list[dict] = []

    if season_id and current_round > 0:
        from sqlalchemy import func, select

        from pinwheel.ai.insights import generate_newspaper_headlines_mock
        from pinwheel.db.models import BoxScoreRow, GameResultRow, HooperRow, TeamRow

        round_phase = await _get_game_phase(repo, season_id, current_round)

        # Simulation report
        sim_reports = await repo.get_reports_for_round(
            season_id,
            current_round,
            "simulation",
        )
        if sim_reports:
            post_sim_report = sim_reports[0].content

        # Governance report — only show "adjourned" when the season is
        # actually finished, not merely because the current round is finals.
        if season_phase in ("offseason",):
            post_gov_report = (
                "The season has concluded. The Floor is adjourned until a new season begins."
            )
        else:
            gov_reports = await repo.get_reports_for_round(
                season_id,
                current_round,
                "governance",
            )
            if gov_reports:
                post_gov_report = gov_reports[0].content

        # Hot players (top 5 scorers)
        stmt = (
            select(
                HooperRow.name,
                TeamRow.name.label("team_name"),
                func.sum(BoxScoreRow.points).label("total_pts"),
                func.sum(BoxScoreRow.assists).label("total_ast"),
                func.count(BoxScoreRow.id).label("games"),
            )
            .join(GameResultRow, BoxScoreRow.game_id == GameResultRow.id)
            .join(HooperRow, BoxScoreRow.hooper_id == HooperRow.id)
            .join(TeamRow, BoxScoreRow.team_id == TeamRow.id)
            .where(GameResultRow.season_id == season_id)
            .group_by(BoxScoreRow.hooper_id)
            .order_by(func.sum(BoxScoreRow.points).desc())
            .limit(5)
        )
        result = await repo.session.execute(stmt)
        for row in result.all():
            ppg = round(row.total_pts / max(row.games, 1), 1)
            apg = round(row.total_ast / max(row.games, 1), 1)
            post_hot_players.append(
                {
                    "name": row.name,
                    "team": row.team_name,
                    "stat": f"{ppg} PPG, {apg} APG",
                }
            )

        # Headlines
        post_team_names: dict[str, str] = {}
        round_games_for_post = await repo.get_games_for_round(season_id, current_round)
        game_summaries: list[dict] = []
        for g in round_games_for_post:
            for tid in (g.home_team_id, g.away_team_id):
                if tid not in post_team_names:
                    t = await repo.get_team(tid)
                    post_team_names[tid] = t.name if t else tid
            game_summaries.append(
                {
                    "home_team_name": post_team_names.get(g.home_team_id, "?"),
                    "away_team_name": post_team_names.get(g.away_team_id, "?"),
                    "home_score": g.home_score,
                    "away_score": g.away_score,
                    "winner_team_id": g.winner_team_id,
                    "winner_team_name": post_team_names.get(g.winner_team_id, "?"),
                }
            )

        round_data = {
            "round_number": current_round,
            "games": game_summaries,
            "governance": {},
        }
        total_season_games = sum(s.get("wins", 0) for s in standings)
        headlines = generate_newspaper_headlines_mock(
            round_data,
            current_round,
            playoff_phase=round_phase or "",
            total_games_played=total_season_games,
        )
        post_headline = headlines.get("headline", "")
        post_subhead = headlines.get("subhead", "")

    # Fallback: when no change signals were detected, surface the Post headline
    if not what_changed_signals and post_headline:
        what_changed_signals = [f"Latest: {post_headline}"]

    ctx = {
        "active_page": "home",
        "season_name": season_name or "Season",
        "latest_report": latest_report,
        "standings": standings,
        "playoff_standings": playoff_standings,
        "semis_best_of": semis_best_of,
        "finals_best_of": finals_best_of,
        "latest_round_games": latest_round_games,
        "current_round": current_round,
        "total_games": total_games,
        "upcoming_rounds": upcoming_rounds,
        "team_colors": team_colors,
        "season_phase": season_phase,
        "streaks": streaks,
        "post_headline": post_headline,
        "post_subhead": post_subhead,
        "post_sim_report": post_sim_report,
        "post_gov_report": post_gov_report,
        "post_highlight_reel": post_highlight_reel,
        "post_hot_players": post_hot_players,
        "what_changed_signals": what_changed_signals,
    }
    return templates.TemplateResponse(
        request,
        "pages/home.html",
        {**ctx, **_auth_context(request, current_user)},
    )


@router.get("/partials/what-changed", response_class=HTMLResponse)
async def what_changed_partial(request: Request, repo: RepoDep) -> HTMLResponse:
    """HTMX partial — returns the what-changed widget HTML fragment.

    Polled by the home page via hx-trigger="every 60s" to keep the
    widget up-to-date without a full page reload.
    """
    season_id, _ = await _get_active_season(repo)
    if not season_id:
        return HTMLResponse("")

    standings = await _get_standings(repo, season_id)

    # Find current round
    current_round = await repo.get_latest_round_number(season_id) or 0

    if current_round <= 0:
        return HTMLResponse("")

    season_phase = await _get_season_phase(repo, season_id)
    all_games = await repo.get_all_games(season_id)
    streaks: dict[str, int] = {}
    if all_games:
        streaks = _compute_streaks_from_games(all_games)

    # Previous round standings
    prev_standings: list[dict] = []
    prev_streaks: dict[str, int] = {}
    if current_round > 1:
        prev_results: list[dict] = []
        for rn in range(1, current_round):
            rg = await repo.get_games_for_round(season_id, rn)
            for g in rg:
                prev_results.append(
                    {
                        "home_team_id": g.home_team_id,
                        "away_team_id": g.away_team_id,
                        "home_score": g.home_score,
                        "away_score": g.away_score,
                        "winner_team_id": g.winner_team_id,
                    }
                )
        prev_standings = compute_standings(prev_results)
        for s in prev_standings:
            team = await repo.get_team(s["team_id"])
            if team:
                s["team_name"] = team.name
        prev_games = [g for g in all_games if g.round_number < current_round]
        if prev_games:
            prev_streaks = _compute_streaks_from_games(prev_games)

    # Rule changes in latest round
    rule_change_events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["rule.enacted"],
    )
    latest_rule_changes = [e.payload for e in rule_change_events if e.round_number == current_round]

    # Compute Post headline for fallback
    post_headline = ""
    from pinwheel.ai.insights import generate_newspaper_headlines_mock

    round_games_for_post = await repo.get_games_for_round(season_id, current_round)
    if round_games_for_post:
        team_names: dict[str, str] = {}
        game_summaries: list[dict] = []
        for g in round_games_for_post:
            for tid in (g.home_team_id, g.away_team_id):
                if tid not in team_names:
                    t = await repo.get_team(tid)
                    team_names[tid] = t.name if t else tid
            game_summaries.append(
                {
                    "home_team_name": team_names.get(g.home_team_id, "?"),
                    "away_team_name": team_names.get(g.away_team_id, "?"),
                    "home_score": g.home_score,
                    "away_score": g.away_score,
                    "winner_team_id": g.winner_team_id,
                    "winner_team_name": team_names.get(g.winner_team_id, "?"),
                }
            )

        round_phase = await _get_game_phase(repo, season_id, current_round)
        total_season_games = sum(s.get("wins", 0) for s in standings)
        headlines = generate_newspaper_headlines_mock(
            {"round_number": current_round, "games": game_summaries, "governance": {}},
            current_round,
            playoff_phase=round_phase or "",
            total_games_played=total_season_games,
        )
        post_headline = headlines.get("headline", "")

    # Build latest_round_games dicts for upset / blowout detection
    partial_round_games: list[dict] = []
    if round_games_for_post:
        for g in round_games_for_post:
            home_id = g.home_team_id
            away_id = g.away_team_id
            partial_round_games.append(
                {
                    "home_name": team_names.get(home_id, "?"),
                    "away_name": team_names.get(away_id, "?"),
                    "home_score": g.home_score,
                    "away_score": g.away_score,
                    "winner_team_id": g.winner_team_id,
                    "home_team_id": home_id,
                    "away_team_id": away_id,
                }
            )

    # Total regular-season rounds and playoff slots
    regular_schedule = await repo.get_full_schedule(
        season_id,
        phase="regular",
    )
    wc_total_rounds = max(s.round_number for s in regular_schedule) if regular_schedule else 0
    season_obj = await repo.get_season(season_id)
    if season_obj and season_obj.current_ruleset:
        wc_ruleset = RuleSet(**season_obj.current_ruleset)
    else:
        wc_ruleset = DEFAULT_RULESET
    wc_playoff_teams = wc_ruleset.playoff_teams

    wc_champion_name = ""
    if season_obj and season_obj.config:
        wc_champion_name = season_obj.config.get("champion_team_name", "")

    what_changed_signals = _compute_what_changed(
        standings=standings,
        prev_standings=prev_standings,
        streaks=streaks,
        prev_streaks=prev_streaks,
        rule_changes=latest_rule_changes,
        season_phase=season_phase,
        post_headline=post_headline,
        latest_round_games=partial_round_games,
        playoff_teams=wc_playoff_teams,
        total_regular_rounds=wc_total_rounds,
        current_round=current_round,
        champion_team_name=wc_champion_name,
    )

    if not what_changed_signals:
        return HTMLResponse("")

    # Build HTML fragment
    is_fallback = len(what_changed_signals) == 1 and what_changed_signals[0].startswith("Latest:")
    items_html = ""
    for signal in what_changed_signals:
        css_class = "what-changed-item"
        if is_fallback:
            css_class += " what-changed-fallback"
        items_html += f'<div class="{css_class}">{signal}</div>'

    html = (
        '<div class="what-changed" id="what-changed"'
        ' hx-get="/partials/what-changed" hx-trigger="every 60s"'
        ' hx-swap="outerHTML">'
        f"{items_html}</div>"
    )
    return HTMLResponse(html)


@router.get("/play", response_class=HTMLResponse)
async def play_page(request: Request, repo: RepoDep, current_user: OptionalUser) -> HTMLResponse:
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
        current_round = await repo.get_latest_round_number(season_id) or 0
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
async def arena_page(request: Request, repo: RepoDep, current_user: OptionalUser) -> HTMLResponse:
    """The Arena — show recent rounds' games (newest first)."""
    season_id = await _get_active_season_id(repo)
    rounds: list[dict] = []

    if season_id:
        # Find the latest round that has games
        latest_round = await repo.get_latest_round_number(season_id) or 0

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
        latest_played = await repo.get_latest_round_number(season_id) or 0

        full_schedule = await repo.get_full_schedule(season_id)
        remaining_entries: list = [e for e in full_schedule if e.round_number > latest_played]

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
                                tid,
                                _dflt,
                            )
                        else:
                            t = await repo.get_team(tid)
                            team_names_sched[tid] = t.name if t else tid
                            c2 = getattr(t, "color_secondary", None) or "#1a1a2e"
                            team_colors_sched[tid] = (t.color or "#888", c2) if t else _dflt
                slot_games.append(
                    {
                        "home_name": team_names_sched.get(
                            entry.home_team_id,
                            "?",
                        ),
                        "away_name": team_names_sched.get(
                            entry.away_team_id,
                            "?",
                        ),
                        "home_color": team_colors_sched.get(
                            entry.home_team_id,
                            _dflt,
                        )[0],
                        "away_color": team_colors_sched.get(
                            entry.away_team_id,
                            _dflt,
                        )[0],
                    }
                )
            upcoming_rounds.append(
                {
                    "start_time": (start_times[idx] if idx < len(start_times) else None),
                    "games": slot_games,
                }
            )

    # Season phase label for the arena subtitle
    season_status = ""
    arena_round = 0
    if season_id:
        season = await repo.get_season(season_id)
        season_status = season.status if season else ""
        arena_round = await repo.get_latest_round_number(season_id) or 0

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
            "season_status": season_status,
            "arena_round": arena_round,
            **_auth_context(request, current_user),
        },
    )


def _compute_standings_callouts(
    standings: list[dict],
    streaks: dict[str, int],
    current_round: int,
    total_rounds: int,
) -> list[str]:
    """Compute 2-4 narrative callouts from standings data.

    Returns a list of short, punchy observations about the standings.
    """
    if not standings:
        return []

    callouts: list[str] = []

    # Tightest race — smallest gap in wins between adjacent teams
    if len(standings) >= 2:
        min_gap = float("inf")
        tight_pair = None
        for i in range(len(standings) - 1):
            gap = standings[i]["wins"] - standings[i + 1]["wins"]
            if gap < min_gap:
                min_gap = gap
                tight_pair = (standings[i], standings[i + 1], i + 1)

        if tight_pair and min_gap <= 1:
            team_a, team_b, seed = tight_pair
            if min_gap == 0:
                callouts.append(
                    f"{team_a['team_name']} and {team_b['team_name']} "
                    f"tied for {seed}{_ordinal_suffix(seed)} place."
                )
            else:
                callouts.append(
                    f"Only {int(min_gap)} game separates {team_a['team_name']} "
                    f"and {team_b['team_name']} for the {seed}{_ordinal_suffix(seed)} seed."
                )

    # Dominant team — top team leads by 3+ games
    if len(standings) >= 2:
        leader = standings[0]
        second = standings[1]
        lead = leader["wins"] - second["wins"]
        if lead >= 3:
            callouts.append(f"{leader['team_name']} has a commanding {int(lead)}-game lead.")

    # Longest active streak
    if streaks:
        longest_streak_team_id = max(streaks, key=lambda tid: abs(streaks[tid]))
        streak_value = streaks[longest_streak_team_id]
        if abs(streak_value) >= 3:
            team_name = next(
                (s["team_name"] for s in standings if s["team_id"] == longest_streak_team_id),
                "Unknown",
            )
            if streak_value > 0:
                callouts.append(f"{team_name} riding a {streak_value}-game win streak.")
            else:
                callouts.append(f"{team_name} on a {abs(streak_value)}-game losing streak.")

    # Late season context
    if total_rounds > 0 and current_round / total_rounds > 0.7:
        remaining = total_rounds - current_round
        if remaining == 1:
            callouts.append("1 round remaining in the regular season.")
        elif remaining > 1:
            callouts.append(f"{remaining} rounds remaining in the regular season.")

    return callouts[:4]


def _ordinal_suffix(n: int) -> str:
    """Return ordinal suffix for a number (1st, 2nd, 3rd, 4th, etc.)."""
    if 11 <= n % 100 <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


@router.get("/standings", response_class=HTMLResponse)
async def standings_page(
    request: Request, repo: RepoDep, current_user: OptionalUser
) -> HTMLResponse:
    """Standings page with narrative context."""
    season_id = await _get_active_season_id(repo)
    standings: list[dict] = []
    season_phase = ""
    streaks: dict[str, int] = {}
    callouts: list[str] = []
    sos: dict[str, dict[str, int]] = {}
    magic_numbers: dict[str, int | None] = {}
    trajectory: dict[str, int] = {}

    if season_id:
        standings = await _get_standings(repo, season_id)
        season_phase = await _get_season_phase(repo, season_id)
        all_games = await repo.get_all_games(season_id)
        if all_games:
            streaks = _compute_streaks_from_games(all_games)

        # Compute current round and total rounds
        current_round = await repo.get_latest_round_number(season_id) or 0

        # Get total scheduled rounds
        full_schedule = await repo.get_full_schedule(season_id)
        total_rounds = max((s.round_number for s in full_schedule), default=0)

        # Build result dicts with round_number for narrative computations
        if standings and current_round > 0 and all_games:
            results_with_rounds: list[dict] = [
                {
                    "home_team_id": g.home_team_id,
                    "away_team_id": g.away_team_id,
                    "home_score": g.home_score,
                    "away_score": g.away_score,
                    "winner_team_id": g.winner_team_id,
                    "round_number": g.round_number,
                }
                for g in all_games
            ]

            # Team name lookup
            team_names: dict[str, str] = {
                s["team_id"]: s.get("team_name", s["team_id"]) for s in standings
            }

            # Strength of schedule
            sos = compute_strength_of_schedule(results_with_rounds, standings)

            # Magic numbers (1 game per round in round-robin)
            magic_numbers = compute_magic_numbers(standings, total_rounds, games_per_round=1)

            # Trajectory (position movement over last 3 rounds)
            trajectory = compute_standings_trajectory(results_with_rounds, current_round)

            # Most improved
            improved_id, _old_pct, _new_pct = compute_most_improved(
                results_with_rounds, current_round
            )

            # Generate narrative callouts
            callouts = compute_narrative_callouts(
                standings=standings,
                streaks=streaks,
                current_round=current_round,
                total_rounds=total_rounds,
                sos=sos,
                magic_numbers=magic_numbers,
                trajectory=trajectory,
                most_improved_team=improved_id,
                team_names=team_names,
            )

    return templates.TemplateResponse(
        request,
        "pages/standings.html",
        {
            "active_page": "standings",
            "standings": standings,
            "season_phase": season_phase,
            "streaks": streaks,
            "callouts": callouts,
            "sos": sos,
            "magic_numbers": magic_numbers,
            "trajectory": trajectory,
            **_auth_context(request, current_user),
        },
    )


def _compute_game_standings(
    all_games: list[object],
    up_to_round: int,
) -> list[dict]:
    """Compute standings from games played before a given round.

    Used to determine game significance (first-place showdown, clinch scenarios)
    based on where teams stood going into the game.

    Args:
        all_games: All game results in the season.
        up_to_round: Include games from rounds strictly before this one.

    Returns:
        Sorted standings list (same format as compute_standings).
    """
    prior_results: list[dict] = [
        {
            "home_team_id": g.home_team_id,
            "away_team_id": g.away_team_id,
            "home_score": g.home_score,
            "away_score": g.away_score,
            "winner_team_id": g.winner_team_id,
        }
        for g in all_games
        if g.round_number < up_to_round
    ]
    if not prior_results:
        return []
    return compute_standings(prior_results)


@router.get("/games/{game_id}", response_class=HTMLResponse)
async def game_page(
    request: Request, game_id: str, repo: RepoDep, current_user: OptionalUser
) -> HTMLResponse:
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
    # Use the game's own season, not the active season (game may be from archived season)
    season_id = game.season_id
    report = None
    game_phase: str | None = None
    if season_id:
        round_reports = await repo.get_reports_for_round(season_id, game.round_number, "simulation")
        if round_reports:
            report = {"content": round_reports[0].content}
        game_phase = await _get_game_phase(repo, season_id, game.round_number)

    # Compute historical context
    game_context: list[str] = []
    game_significance: list[str] = []
    rule_changes_since_last: list[str] = []
    if season_id:
        all_games = await repo.get_all_games(season_id)
        if all_games:
            # Head-to-head record
            h2h_games = [
                g
                for g in all_games
                if {g.home_team_id, g.away_team_id} == {game.home_team_id, game.away_team_id}
            ]
            if len(h2h_games) > 1:
                home_wins = sum(1 for g in h2h_games if g.winner_team_id == game.home_team_id)
                away_wins = sum(1 for g in h2h_games if g.winner_team_id == game.away_team_id)
                if home_wins == away_wins:
                    game_context.append(f"Season series tied {home_wins}-{away_wins}")
                elif home_wins > away_wins:
                    game_context.append(f"Season series: {home_name} leads {home_wins}-{away_wins}")
                else:
                    game_context.append(f"Season series: {away_name} leads {away_wins}-{home_wins}")

            # Previous meeting context — blowout rematch + rule changes
            previous_meetings = sorted(
                [g for g in h2h_games if g.round_number < game.round_number],
                key=lambda g: g.round_number,
                reverse=True,
            )
            if previous_meetings:
                last_meeting = previous_meetings[0]
                last_margin = abs(last_meeting.home_score - last_meeting.away_score)
                if last_margin >= 15:
                    last_winner_name = (
                        home_name if last_meeting.winner_team_id == game.home_team_id else away_name
                    )
                    game_significance.append(
                        f"Last meeting: {last_winner_name} won by {last_margin}"
                    )

                # Rule changes enacted between last meeting and this game
                timeline = await repo.get_rule_change_timeline(season_id)
                for rc in timeline:
                    if last_meeting.round_number < rc["round_enacted"] <= game.round_number:
                        param_label = rc["parameter"].replace("_", " ").title()
                        rule_changes_since_last.append(
                            f"Since Round {last_meeting.round_number}: "
                            f"{param_label} changed from {rc['old_value']} "
                            f"to {rc['new_value']}"
                        )

            # Game significance — standings-based callouts
            standings_at_game = _compute_game_standings(all_games, game.round_number)
            if len(standings_at_game) >= 2:
                team_ids_in_game = {game.home_team_id, game.away_team_id}
                top_two_ids = {
                    standings_at_game[0]["team_id"],
                    standings_at_game[1]["team_id"],
                }
                if team_ids_in_game == top_two_ids:
                    game_significance.append("First place showdown")

                # Win-and-clinch: can a team lock a playoff spot with a win?
                schedule = await repo.get_full_schedule(season_id, phase="regular")
                if schedule:
                    total_regular_rounds = max(s.round_number for s in schedule)
                    remaining_after = total_regular_rounds - game.round_number
                    ruleset = RuleSet(**(game.ruleset_snapshot or {}))
                    playoff_spots = ruleset.playoff_teams
                    if len(standings_at_game) >= playoff_spots:
                        bubble_team = standings_at_game[playoff_spots - 1]
                        for tid in team_ids_in_game:
                            team_standing = next(
                                (s for s in standings_at_game if s["team_id"] == tid),
                                None,
                            )
                            if not team_standing:
                                continue
                            bubble_max = bubble_team["wins"] + remaining_after
                            if (
                                team_standing["wins"] + 1 > bubble_max
                                and tid != bubble_team["team_id"]
                            ):
                                clinch_name = home_name if tid == game.home_team_id else away_name
                                game_significance.append(
                                    f"Win-and-clinch scenario for {clinch_name}"
                                )

            # Margin context
            margin = abs(game.home_score - game.away_score)
            margins = [abs(g.home_score - g.away_score) for g in all_games]
            avg_margin = sum(margins) / len(margins) if margins else 0
            if margin == min(margins):
                game_context.append(f"Closest game of the season — {margin}-point margin")
            elif margin == max(margins):
                game_context.append(f"Biggest blowout of the season — {margin}-point margin")
            elif margin < avg_margin * 0.7:
                avg_str = round(avg_margin, 1)
                game_context.append(f"A tight {margin}-point game (season avg: {avg_str})")
            elif margin > avg_margin * 1.5:
                game_context.append(f"A decisive {margin}-point victory")

            # Scoring context
            total_pts = game.home_score + game.away_score
            all_totals = [g.home_score + g.away_score for g in all_games]
            avg_total = sum(all_totals) / len(all_totals) if all_totals else 0
            if total_pts > avg_total * 1.15:
                avg_total_str = round(avg_total, 1)
                game_context.append(
                    f"High-scoring affair — {total_pts} combined points "
                    f"(season avg: {avg_total_str})"
                )
            elif total_pts < avg_total * 0.85:
                avg_total_str = round(avg_total, 1)
                game_context.append(
                    f"Defensive battle — {total_pts} combined points (season avg: {avg_total_str})"
                )

            # Streak context — show each team's streak at game time
            games_up_to = [g for g in all_games if g.round_number <= game.round_number]
            if games_up_to:
                game_streaks = _compute_streaks_from_games(games_up_to)
                for tid, tname in [
                    (game.home_team_id, home_name),
                    (game.away_team_id, away_name),
                ]:
                    streak_val = game_streaks.get(tid, 0)
                    if streak_val >= 3:
                        game_context.append(f"{tname} on a {streak_val}-game win streak")
                    elif streak_val <= -3:
                        game_context.append(f"{tname} on a {abs(streak_val)}-game losing streak")

            # Personal bests — season-high points for hoopers in this game
            # Build a map of hooper_id -> max points scored in any game
            hooper_season_highs: dict[str, int] = {}
            for g in all_games:
                for bs in g.box_scores:
                    is_new = bs.hooper_id not in hooper_season_highs
                    is_higher = not is_new and bs.points > hooper_season_highs[bs.hooper_id]
                    if is_new or is_higher:
                        hooper_season_highs[bs.hooper_id] = bs.points

            for bs in game.box_scores:
                season_high = hooper_season_highs.get(bs.hooper_id, 0)
                if bs.points > 0 and bs.points == season_high and len(all_games) > 1:
                    name = hooper_names.get(bs.hooper_id, bs.hooper_id)
                    game_significance.append(f"Season-high {bs.points} points for {name}")

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
            "game_context": game_context,
            "game_significance": game_significance,
            "rule_changes_since_last": rule_changes_since_last,
            **_auth_context(request, current_user),
        },
    )


@router.get("/teams/{team_id}", response_class=HTMLResponse)
async def team_page(
    request: Request, team_id: str, repo: RepoDep, current_user: OptionalUser
) -> HTMLResponse:
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

    # Compute trajectory data (performance trajectory with rule impact analysis)
    trajectory = None
    if season_id:
        game_results = await repo.get_team_game_results(team_id, season_id)

        if game_results:
            # Get rule change events for regime analysis
            rule_events = await repo.get_events_by_type(
                season_id=season_id,
                event_types=["rule.enacted"],
            )
            rule_change_rounds = sorted({e.round_number for e in rule_events if e.round_number})
            rule_event_payloads: list[dict[str, object]] = [e.payload for e in rule_events]

            # Build governor proposal impact data
            team_passed_proposals: list[dict[str, object]] = []
            if governors:
                governor_ids = {g["id"] for g in governors}

                submitted_events = await repo.get_events_by_type(
                    season_id=season_id,
                    event_types=["proposal.submitted"],
                )
                team_proposals: dict[str, tuple[str, str]] = {}
                for e in submitted_events:
                    if e.governor_id and e.governor_id in governor_ids:
                        pid = e.payload.get("id", "")
                        raw_text = e.payload.get("raw_text", "")
                        if pid:
                            team_proposals[pid] = (e.governor_id, raw_text)

                if team_proposals:
                    enacted_params: dict[str, str] = {}
                    for re_evt in rule_events:
                        p_id = re_evt.payload.get("source_proposal_id", "")
                        param = re_evt.payload.get("parameter", "")
                        if p_id and param:
                            enacted_params[p_id] = param

                    passed_events = await repo.get_events_by_type(
                        season_id=season_id,
                        event_types=["proposal.passed"],
                    )
                    for oe in passed_events:
                        pid = oe.payload.get("proposal_id", oe.aggregate_id)
                        if pid in team_proposals:
                            gov_id, raw_text = team_proposals[pid]
                            enacted_round = oe.round_number or 0
                            gov_name = next(
                                (gv["username"] for gv in governors if gv["id"] == gov_id),
                                "A governor",
                            )
                            team_passed_proposals.append(
                                {
                                    "governor_name": gov_name,
                                    "raw_text": raw_text,
                                    "enacted_round": enacted_round,
                                    "parameter": enacted_params.get(pid, ""),
                                }
                            )

            from pinwheel.core.trajectory import build_performance_trajectory

            trajectory = build_performance_trajectory(
                game_results=game_results,
                rule_events=rule_event_payloads,
                team_passed_proposals=team_passed_proposals,
                rule_change_rounds=rule_change_rounds,
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
            "trajectory": trajectory,
            **_auth_context(request, current_user),
        },
    )


@router.get("/hoopers/{hooper_id}", response_class=HTMLResponse)
async def hooper_page(
    request: Request, hooper_id: str, repo: RepoDep, current_user: OptionalUser
) -> HTMLResponse:
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
) -> HTMLResponse:
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

    return templates.TemplateResponse(
        request,
        "partials/hooper_bio_edit.html",
        {"backstory": hooper.backstory or "", "hooper_id": hooper_id},
    )


@router.get("/hoopers/{hooper_id}/bio/view", response_class=HTMLResponse)
async def hooper_bio_view(
    request: Request, hooper_id: str, repo: RepoDep, current_user: OptionalUser
) -> HTMLResponse:
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

    return templates.TemplateResponse(
        request,
        "partials/hooper_bio_view.html",
        {"backstory": hooper.backstory, "can_edit": can_edit, "hooper_id": hooper_id},
    )


@router.post("/hoopers/{hooper_id}/bio", response_class=HTMLResponse)
async def update_hooper_bio(
    request: Request, hooper_id: str, repo: RepoDep, current_user: OptionalUser
) -> HTMLResponse:
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
    return templates.TemplateResponse(
        request,
        "partials/hooper_bio_view.html",
        {"backstory": backstory, "can_edit": True, "hooper_id": hooper_id},
    )


@router.get("/governors/{player_id}", response_class=HTMLResponse)
async def governor_profile_page(
    request: Request, player_id: str, repo: RepoDep, current_user: OptionalUser
) -> HTMLResponse:
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
async def governance_page(
    request: Request, repo: RepoDep, current_user: OptionalUser
) -> HTMLResponse:
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
            event_types=[
                "proposal.confirmed",
                "proposal.passed",
                "proposal.failed",
                "proposal.cancelled",
            ],
        )
        vote_events = await repo.get_events_by_type(
            season_id=season_id,
            event_types=["vote.cast"],
        )

        # Index outcomes and votes by proposal_id
        confirmed_ids: set[str] = set()
        cancelled_ids: set[str] = set()
        outcomes: dict[str, dict] = {}
        for e in outcome_events:
            pid = e.payload.get("proposal_id", e.aggregate_id)
            if e.event_type == "proposal.confirmed":
                confirmed_ids.add(pid)
            elif e.event_type == "proposal.cancelled":
                cancelled_ids.add(pid)
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
            if pid in cancelled_ids:
                continue
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
        _param_labels: dict[str, str] = {
            key: label for tier in RULE_TIERS for key, label, _ in tier.get("rules", [])
        }
        for e in rc_events:
            rc = dict(e.payload)
            param = rc.get("parameter", "")
            rc["parameter_label"] = _param_labels.get(
                param,
                param.replace("_", " ").title(),
            )
            rules_changed.append(rc)

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


async def _compute_rule_impact(repo: RepoDep, season_id: str, round_enacted: int) -> str:
    """Compute a gameplay impact string for a rule change.

    Compares average total game score (home + away) before vs after
    the round the rule was enacted.  Returns a human-readable string
    like "Scoring +12% since change" or "Too early to measure".
    """
    min_games_after = 2

    # Before: rounds 1 through round_enacted - 1
    if round_enacted > 1:
        before_avg, before_count = await repo.get_avg_total_game_score_for_rounds(
            season_id,
            1,
            round_enacted - 1,
        )
    else:
        before_avg, before_count = 0.0, 0

    # After: round_enacted onward (large upper bound)
    after_avg, after_count = await repo.get_avg_total_game_score_for_rounds(
        season_id,
        round_enacted,
        9999,
    )

    if after_count < min_games_after:
        return "Too early to measure"
    if before_count == 0 or before_avg == 0:
        return f"Avg {after_avg:.0f} pts/game over {after_count} games"

    pct_change = ((after_avg - before_avg) / before_avg) * 100
    direction = "+" if pct_change >= 0 else ""
    return f"Scoring {direction}{pct_change:.0f}% since change"


@router.get("/rules", response_class=HTMLResponse)
async def rules_page(request: Request, repo: RepoDep, current_user: OptionalUser) -> HTMLResponse:
    """Current rules page."""
    season_id = await _get_active_season_id(repo)
    ruleset = RuleSet()
    defaults = RuleSet()
    changes_from_default: dict = {}
    rule_history: list[dict[str, object]] = []
    rule_change_timeline: dict[str, list[dict[str, object]]] = {}

    if season_id:
        season = await repo.get_season(season_id)
        if season and season.current_ruleset:
            ruleset = RuleSet(**season.current_ruleset)

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

        # Get rule change timeline and map by parameter, with gameplay deltas
        timeline = await repo.get_rule_change_timeline(season_id)
        for change in timeline:
            round_enacted: int = change["round_enacted"]
            impact = await _compute_rule_impact(repo, season_id, round_enacted)
            change["impact"] = impact
            param = change["parameter"]
            if param not in rule_change_timeline:
                rule_change_timeline[param] = []
            rule_change_timeline[param].append(change)

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
            range_min: float | None = None
            range_max: float | None = None
            if fi and fi.metadata:
                bounds: list[str] = []
                for m in fi.metadata:
                    if hasattr(m, "ge"):
                        bounds.append(f"{m.ge}")
                        range_min = float(m.ge)
                    if hasattr(m, "le"):
                        bounds.append(f"{m.le}")
                        range_max = float(m.le)
                if len(bounds) == 2:
                    range_str = f"{bounds[0]}\u2013{bounds[1]}"
            changed = param in changes_from_default

            # Compute drift: how far from default as % of range (0-100)
            drift_pct: int = 0
            default_val = getattr(defaults, param)
            if changed and range_min is not None and range_max is not None:
                span = range_max - range_min
                if span > 0 and isinstance(value, (int, float)):
                    drift_pct = int(abs(float(value) - float(default_val)) / span * 100)

            # Count total changes for this parameter
            change_count = len(rule_change_timeline.get(param, []))

            tier_rules.append(
                {
                    "param": param,
                    "label": label,
                    "desc": desc,
                    "value": value,
                    "default": default_val,
                    "range": range_str,
                    "changed": changed,
                    "drift_pct": drift_pct,
                    "change_count": change_count,
                    "history": rule_change_timeline.get(param, []),
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

    # Compute most-changed tier for governance fingerprint
    tier_change_counts: dict[str, int] = {tier["key"]: 0 for tier in RULE_TIERS}
    for param in changes_from_default:
        for tier in RULE_TIERS:
            if any(p == param for p, _, _ in tier["rules"]):
                tier_change_counts[tier["key"]] += 1
                break
    most_changed_tier_key = (
        max(tier_change_counts, key=tier_change_counts.get, default="")
        if any(tier_change_counts.values())
        else ""
    )
    most_changed_tier_name = ""
    for tier in RULE_TIERS:
        if tier["key"] == most_changed_tier_key:
            most_changed_tier_name = tier["title"]
            break

    return templates.TemplateResponse(
        request,
        "pages/rules.html",
        {
            "active_page": "rules",
            "tiers": tiers,
            "community_changes": community_changes,
            "changes_from_default": changes_from_default,
            "rule_history": rule_history,
            "most_changed_tier": most_changed_tier_name,
            **_auth_context(request, current_user),
        },
    )


@router.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request, repo: RepoDep, current_user: OptionalUser) -> HTMLResponse:
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
                            repo,
                            season_id,
                            rn,
                        )
                    reports.append(
                        {
                            "report_type": m.report_type,
                            "round_number": m.round_number,
                            "content": m.content,
                            "created_at": (m.created_at.isoformat() if m.created_at else ""),
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
async def newspaper_page(
    request: Request, repo: RepoDep, current_user: OptionalUser
) -> HTMLResponse:
    """The Pinwheel Post — newspaper-style round summary page."""
    from sqlalchemy import func, select

    from pinwheel.ai.insights import generate_newspaper_headlines_mock
    from pinwheel.db.models import BoxScoreRow, GameResultRow, HooperRow, TeamRow

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
        current_round = await repo.get_latest_round_number(season_id) or 0

        if current_round > 0:
            # Detect playoff phase for this round (needed for headlines
            # and to override stale governance reports)
            round_phase = await _get_game_phase(repo, season_id, current_round)
            is_championship_round = round_phase in ("finals", "championship")

            # Fetch reports for the latest round
            sim_reports = await repo.get_reports_for_round(
                season_id,
                current_round,
                "simulation",
            )
            if sim_reports:
                sim_report = sim_reports[0].content

            # Governance report — override if this was the championship
            # (stored report says "finals underway" but season is over)
            if is_championship_round:
                gov_report = (
                    "The season has concluded. The Floor is adjourned until a new season begins."
                )
            else:
                gov_reports = await repo.get_reports_for_round(
                    season_id,
                    current_round,
                    "governance",
                )
                if gov_reports:
                    gov_report = gov_reports[0].content

            impact_reports = await repo.get_reports_for_round(
                season_id,
                current_round,
                "impact_validation",
            )
            if impact_reports:
                impact_report = impact_reports[0].content

            # Build standings
            standings = await _get_standings(repo, season_id)

            # Season leaders (top 5 scorers, top assist leader, top steals leader)
            stmt = (
                select(
                    HooperRow.name,
                    TeamRow.name.label("team_name"),
                    func.sum(BoxScoreRow.points).label("total_pts"),
                    func.sum(BoxScoreRow.assists).label("total_ast"),
                    func.sum(BoxScoreRow.steals).label("total_stl"),
                    func.count(BoxScoreRow.id).label("games"),
                )
                .join(GameResultRow, BoxScoreRow.game_id == GameResultRow.id)
                .join(HooperRow, BoxScoreRow.hooper_id == HooperRow.id)
                .join(TeamRow, BoxScoreRow.team_id == TeamRow.id)
                .where(GameResultRow.season_id == season_id)
                .group_by(BoxScoreRow.hooper_id)
                .order_by(func.sum(BoxScoreRow.points).desc())
                .limit(5)
            )
            result = await repo.session.execute(stmt)
            for row in result.all():
                ppg = round(row.total_pts / max(row.games, 1), 1)
                apg = round(row.total_ast / max(row.games, 1), 1)
                hot_players.append(
                    {
                        "name": row.name,
                        "team": row.team_name,
                        "stat": f"{ppg} PPG, {apg} APG",
                    }
                )

            # Build round data for headline generation
            round_games = await repo.get_games_for_round(season_id, current_round)
            team_names: dict[str, str] = {}
            game_summaries: list[dict] = []
            for g in round_games:
                for tid in (g.home_team_id, g.away_team_id):
                    if tid not in team_names:
                        t = await repo.get_team(tid)
                        team_names[tid] = t.name if t else tid
                game_summaries.append(
                    {
                        "home_team_name": team_names.get(g.home_team_id, "?"),
                        "away_team_name": team_names.get(g.away_team_id, "?"),
                        "home_score": g.home_score,
                        "away_score": g.away_score,
                        "winner_team_id": g.winner_team_id,
                        "winner_team_name": team_names.get(g.winner_team_id, "?"),
                    }
                )

            round_data = {
                "round_number": current_round,
                "games": game_summaries,
                "governance": {},
            }

            # Total games played this season
            total_season_games = sum(s.get("wins", 0) for s in standings)

            headlines = generate_newspaper_headlines_mock(
                round_data,
                current_round,
                playoff_phase=round_phase or "",
                total_games_played=total_season_games,
            )
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
async def playoffs_page(
    request: Request, repo: RepoDep, current_user: OptionalUser
) -> HTMLResponse:
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
async def season_archives_page(
    request: Request, repo: RepoDep, current_user: OptionalUser
) -> HTMLResponse:
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
) -> HTMLResponse:
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
        "reports": archive.reports or [],
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
async def history_page(request: Request, repo: RepoDep, current_user: OptionalUser) -> HTMLResponse:
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
) -> HTMLResponse:
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
async def admin_landing_page(request: Request, current_user: OptionalUser) -> HTMLResponse:
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
async def terms_page(request: Request, current_user: OptionalUser) -> HTMLResponse:
    """Terms of Service."""
    return templates.TemplateResponse(
        request,
        "pages/terms.html",
        {"active_page": "terms", **_auth_context(request, current_user)},
    )


@router.get("/privacy", response_class=HTMLResponse)
async def privacy_page(request: Request, current_user: OptionalUser) -> HTMLResponse:
    """Privacy Policy."""
    return templates.TemplateResponse(
        request,
        "pages/privacy.html",
        {"active_page": "privacy", **_auth_context(request, current_user)},
    )
