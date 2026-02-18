"""NarrativeContext — runtime dramatic awareness for all output systems.

A read-only data aggregation layer computed once per round in step_round().
Surfaces streaks, standings, rivalries, rule changes, season arc, and
governance state so commentary, reports, embeds, and templates can produce
contextually rich output.

This module NEVER writes to the database. It only reads.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pinwheel.db.repository import Repository
    from pinwheel.models.rules import RuleSet

logger = logging.getLogger(__name__)


@dataclass
class NarrativeContext:
    """Pre-computed dramatic context for the current round.

    Every player-facing output system receives this object and decides
    which fields to surface. Not every context applies everywhere.
    """

    # Phase / season arc
    phase: str = "regular"
    """Current season phase: 'regular', 'tiebreakers', 'semifinal', 'finals',
    'championship', 'offseason'."""

    season_arc: str = "early"
    """Position within the season: 'early', 'mid', 'late', 'playoff', 'championship'."""

    round_number: int = 0
    """Current round number."""

    total_rounds: int = 0
    """Total regular-season rounds (for 'Round 5 of 9' display)."""

    # Standings snapshot (computed before this round's games)
    standings: list[dict[str, object]] = field(default_factory=list)
    """Sorted standings: [{team_id, team_name, wins, losses, rank, point_diff}]."""

    # Streaks (per team)
    streaks: dict[str, int] = field(default_factory=dict)
    """team_id -> streak length. Positive = wins, negative = losses."""

    # Rule evolution
    active_rule_changes: list[dict[str, object]] = field(default_factory=list)
    """[{parameter, old_value, new_value, round_enacted, narrative}]."""

    rules_narrative: str = ""
    """Human-readable summary of active rule changes."""

    # Head-to-head for this round's matchups
    head_to_head: dict[str, dict[str, object]] = field(default_factory=dict)
    """'teamA_vs_teamB' -> {wins_a, wins_b, total_games, last_winner}."""

    # Individual milestones
    hot_players: list[dict[str, object]] = field(default_factory=list)
    """[{hooper_id, name, team_name, stat, value, games}]."""

    # Governance state
    governance_window_open: bool = False
    """Whether a governance tally is happening this round."""

    pending_proposals: int = 0
    """Number of confirmed proposals awaiting tally."""

    next_tally_round: int | None = None
    """Next round when governance will be tallied (None if manual)."""

    effects_narrative: str = ""
    """Human-readable summary of active proposal effects (meta_mutation,
    hook_callback, narrative). Built from EffectRegistry.build_effects_summary()."""

    # System-level game milestones
    season_game_number: int = 0
    """Total games played in this season so far (before this round's games)."""

    pre_rule_avg_score: float = 0.0
    """Average total game score (home+away) from games before the most recent
    scoring rule change.  Used for stat-comparison callouts in commentary."""

    # Bedrock league facts — structural truths the AI must not contradict
    bedrock_facts: str = ""
    """Verified structural facts about the league (team count, format, scoring, etc.).
    Built from the current RuleSet. Appears at the top of every AI prompt."""

    # Playoff series record (separate from season h2h)
    playoff_series: dict[str, object] = field(default_factory=dict)
    """Playoff series record for the current round's matchup, e.g.
    {home_wins, away_wins, best_of, wins_needed, phase_label, description}."""

    # Prior season memory
    prior_seasons: list[dict[str, object]] = field(default_factory=list)
    """Lightweight summaries of completed seasons from the archive."""


def _build_bedrock_facts(ruleset: RuleSet) -> str:
    """Build verified structural facts about the league from the current ruleset.

    These facts appear at the top of every AI prompt and must not be contradicted.
    """
    semis_wins = (ruleset.playoff_semis_best_of // 2) + 1
    finals_wins = (ruleset.playoff_finals_best_of // 2) + 1
    return (
        f"League: {ruleset.teams_count} teams, 3v3 basketball.\n"
        f"No byes — every team plays every round during the regular season.\n"
        f"Playoffs: top {ruleset.playoff_teams} teams qualify. "
        f"Semifinals are best-of-{ruleset.playoff_semis_best_of} "
        f"(first to {semis_wins} wins). "
        f"Finals are best-of-{ruleset.playoff_finals_best_of} "
        f"(first to {finals_wins} wins).\n"
        f"Elam Ending: activates after Q{ruleset.elam_trigger_quarter} "
        f"if margin is within {ruleset.elam_margin} points.\n"
        f"Scoring: 3pt={ruleset.three_point_value}, 2pt={ruleset.two_point_value}, "
        f"FT={ruleset.free_throw_value}.\n"
        f"Quarter length: {ruleset.quarter_minutes} minutes. "
        f"Shot clock: {ruleset.shot_clock_seconds} seconds."
    )


async def compute_narrative_context(
    repo: Repository,
    season_id: str,
    round_number: int,
    governance_interval: int = 1,
    *,
    ruleset: RuleSet | None = None,
) -> NarrativeContext:
    """Build a NarrativeContext from current database state.

    This is a read-only function. It queries game results, schedule, rule
    change events, and governance events to produce a snapshot of the
    current dramatic situation.

    Args:
        repo: Repository instance for database queries.
        season_id: Current season ID.
        round_number: The round about to be played/just played.
        governance_interval: How often governance tallies occur (in rounds).
        ruleset: Current RuleSet — if provided, bedrock facts are computed.

    Returns:
        A fully populated NarrativeContext.
    """
    ctx = NarrativeContext(round_number=round_number)

    # --- Season / phase ---
    season = await repo.get_season(season_id)
    if season:
        ctx.phase = _compute_phase(season.status)
        schedule = await repo.get_full_schedule(season_id, phase="regular")
        if schedule:
            ctx.total_rounds = max(s.round_number for s in schedule)
        ctx.season_arc = _compute_season_arc(
            round_number, ctx.total_rounds, ctx.phase
        )

    # Check if this round has playoff games
    round_schedule = await repo.get_schedule_for_round(season_id, round_number)
    _playoff_phases = ("playoff", "semifinal", "finals")
    if round_schedule and round_schedule[0].phase in _playoff_phases:
        # Determine if semifinal or finals
        full_playoff = await repo.get_full_schedule(season_id, phase="playoff")
        if full_playoff:
            earliest_round = min(s.round_number for s in full_playoff)
            initial_pairs = [
                frozenset({s.home_team_id, s.away_team_id})
                for s in full_playoff
                if s.round_number == earliest_round
            ]
            current_pairs = [
                frozenset({s.home_team_id, s.away_team_id})
                for s in round_schedule
            ]
            if len(initial_pairs) >= 2 and all(
                p in initial_pairs for p in current_pairs
            ):
                ctx.phase = "semifinal"
            else:
                ctx.phase = "finals"
        ctx.season_arc = "playoff"

    # --- Standings ---
    games = await repo.get_all_games(season_id)
    if games:
        from pinwheel.core.scheduler import compute_standings

        game_dicts = [
            {
                "home_team_id": g.home_team_id,
                "away_team_id": g.away_team_id,
                "home_score": g.home_score,
                "away_score": g.away_score,
                "winner_team_id": g.winner_team_id,
            }
            for g in games
        ]
        raw_standings = compute_standings(game_dicts)

        # Enrich with team names and rank
        for i, s in enumerate(raw_standings):
            team = await repo.get_team(s["team_id"])
            s["team_name"] = team.name if team else s["team_id"]
            s["rank"] = i + 1
        ctx.standings = raw_standings

        # --- Streaks ---
        ctx.streaks = _compute_streaks(games)

        # --- Head-to-head for upcoming matchups ---
        if round_schedule:
            for entry in round_schedule:
                h2h_key = f"{entry.home_team_id}_vs_{entry.away_team_id}"
                h2h = _compute_head_to_head(
                    games, entry.home_team_id, entry.away_team_id
                )
                ctx.head_to_head[h2h_key] = h2h

            # --- Playoff series (separate from season h2h) ---
            if ctx.phase in ("semifinal", "finals"):
                for entry in round_schedule:
                    playoff_h2h = _compute_head_to_head(
                        games,
                        entry.home_team_id,
                        entry.away_team_id,
                        phase_filter="playoff",
                    )
                    best_of = (
                        ruleset.playoff_finals_best_of
                        if ctx.phase == "finals" and ruleset
                        else ruleset.playoff_semis_best_of
                        if ruleset
                        else 3
                    )
                    wins_needed = (best_of // 2) + 1
                    home_team = await repo.get_team(entry.home_team_id)
                    away_team = await repo.get_team(entry.away_team_id)
                    home_name = home_team.name if home_team else entry.home_team_id
                    away_name = away_team.name if away_team else entry.away_team_id
                    hw = playoff_h2h["wins_a"]
                    aw = playoff_h2h["wins_b"]
                    desc = (
                        f"{home_name} leads {hw}-{aw}" if hw > aw
                        else f"{away_name} leads {aw}-{hw}" if aw > hw
                        else f"Series tied {hw}-{aw}"
                    )
                    series_key = f"{entry.home_team_id}_vs_{entry.away_team_id}"
                    ctx.playoff_series[series_key] = {
                        "home_wins": hw,
                        "away_wins": aw,
                        "best_of": best_of,
                        "wins_needed": wins_needed,
                        "phase_label": ctx.phase,
                        "description": desc,
                    }

        # --- Hot players ---
        ctx.hot_players = await _compute_hot_players(repo, season_id, games)

    # --- Season game count ---
    if games:
        ctx.season_game_number = len(games)

    # --- Rule changes ---
    rule_events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["rule.enacted"],
    )
    if rule_events:
        for evt in rule_events:
            ctx.active_rule_changes.append(
                {
                    "parameter": evt.payload.get("parameter", ""),
                    "old_value": evt.payload.get("old_value"),
                    "new_value": evt.payload.get("new_value"),
                    "round_enacted": evt.payload.get("round_enacted"),
                    "narrative": evt.payload.get("narrative", ""),
                }
            )
        ctx.rules_narrative = _build_rules_narrative(ctx.active_rule_changes)

        # Compute pre-rule average score for scoring parameter changes
        scoring_params = {"three_point_value", "two_point_value", "free_throw_value"}
        scoring_changes = [
            rc for rc in ctx.active_rule_changes
            if rc.get("parameter") in scoring_params
        ]
        if scoring_changes and games:
            # Use the most recent scoring rule change
            most_recent = max(
                scoring_changes,
                key=lambda rc: int(rc.get("round_enacted", 0) or 0),
            )
            enacted_round = int(most_recent.get("round_enacted", 0) or 0)
            if enacted_round > 1:
                avg_score, count = await repo.get_avg_total_game_score_for_rounds(
                    season_id, 1, enacted_round - 1,
                )
                if count > 0:
                    ctx.pre_rule_avg_score = avg_score

    # --- Governance state ---
    if governance_interval > 0:
        ctx.governance_window_open = round_number % governance_interval == 0
        # Compute next tally round
        if ctx.governance_window_open:
            ctx.next_tally_round = round_number + governance_interval
        else:
            remainder = round_number % governance_interval
            ctx.next_tally_round = round_number + (governance_interval - remainder)

    # Count pending confirmed proposals
    confirmed_events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["proposal.confirmed"],
    )
    resolved_events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["proposal.passed", "proposal.failed"],
    )
    vetoed_events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["proposal.vetoed"],
    )
    resolved_ids = {e.aggregate_id for e in resolved_events}
    vetoed_ids = {e.aggregate_id for e in vetoed_events}
    pending_count = 0
    for ce in confirmed_events:
        pid = ce.payload.get("proposal_id", ce.aggregate_id)
        if pid not in resolved_ids and pid not in vetoed_ids:
            pending_count += 1
    ctx.pending_proposals = pending_count

    # --- Bedrock facts ---
    if ruleset is not None:
        ctx.bedrock_facts = _build_bedrock_facts(ruleset)

    # --- Prior season memory ---
    try:
        archives = await repo.get_all_archives()
        for archive in archives[:3]:  # at most 3, newest first
            summary: dict[str, object] = {
                "season_name": archive.season_name,
                "champion_team_name": archive.champion_team_name or "Unknown",
                "total_games": archive.total_games,
                "total_rule_changes": archive.total_rule_changes,
            }
            # Extract governance_legacy excerpt from memorial
            memorial = archive.memorial
            if isinstance(memorial, str):
                try:
                    memorial = json.loads(memorial)
                except (json.JSONDecodeError, TypeError):
                    memorial = None
            if isinstance(memorial, dict):
                legacy = memorial.get("governance_legacy", "")
                if legacy:
                    summary["governance_legacy"] = str(legacy)[:100]
            # Extract notable rules from rule_change_history
            rule_history = archive.rule_change_history
            if isinstance(rule_history, str):
                try:
                    rule_history = json.loads(rule_history)
                except (json.JSONDecodeError, TypeError):
                    rule_history = []
            if isinstance(rule_history, list):
                notable: list[str] = []
                for rc in rule_history[:3]:
                    if isinstance(rc, dict):
                        param = rc.get("parameter", "")
                        val = rc.get("new_value", "")
                        if param:
                            notable.append(f"{param}={val}")
                if notable:
                    summary["notable_rules"] = notable
            ctx.prior_seasons.append(summary)
    except Exception:
        logger.debug("prior_seasons_skipped — archives not available", exc_info=True)

    return ctx


def _compute_phase(status: str) -> str:
    """Map season status to a narrative phase label."""
    phase_map: dict[str, str] = {
        "setup": "regular",
        "active": "regular",
        "tiebreaker_check": "regular",
        "tiebreakers": "tiebreakers",
        "regular_season_complete": "regular",
        "playoffs": "semifinal",
        "championship": "championship",
        "offseason": "offseason",
        "completed": "regular",
        "complete": "regular",
        "archived": "regular",
    }
    return phase_map.get(status, "regular")


def _compute_season_arc(
    round_number: int,
    total_rounds: int,
    phase: str,
) -> str:
    """Determine narrative arc position within the season.

    Returns one of: 'early', 'mid', 'late', 'playoff', 'championship'.
    """
    if phase in ("semifinal", "finals"):
        return "playoff"
    if phase == "championship":
        return "championship"

    if total_rounds == 0:
        return "early"

    pct = round_number / total_rounds
    if pct <= 0.33:
        return "early"
    elif pct <= 0.66:
        return "mid"
    else:
        return "late"


def _compute_streaks(games: list[object]) -> dict[str, int]:
    """Compute current win/loss streaks per team from game results.

    Positive = win streak, negative = loss streak. Only counts the
    current streak (resets on reversal).

    Args:
        games: List of game result rows sorted by round_number.

    Returns:
        Dict mapping team_id to streak length.
    """
    # Sort games by round_number to process chronologically
    sorted_games = sorted(games, key=lambda g: (g.round_number, g.matchup_index))

    # Track per-team results in order
    team_results: dict[str, list[bool]] = {}
    for g in sorted_games:
        for tid in (g.home_team_id, g.away_team_id):
            if tid not in team_results:
                team_results[tid] = []
            team_results[tid].append(g.winner_team_id == tid)

    # Compute current streak from the end
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


def _compute_head_to_head(
    games: list[object],
    team_a_id: str,
    team_b_id: str,
    phase_filter: str | None = None,
) -> dict[str, object]:
    """Compute head-to-head record between two teams.

    Args:
        games: All game results for the season.
        team_a_id: First team ID.
        team_b_id: Second team ID.
        phase_filter: If set, only count games matching this phase
            (e.g. "playoff"). Uses getattr to check game.phase.

    Returns:
        Dict with wins_a, wins_b, total_games, last_winner.
    """
    pair = frozenset({team_a_id, team_b_id})
    wins_a = 0
    wins_b = 0
    total = 0
    last_winner: str | None = None

    sorted_games = sorted(games, key=lambda g: g.round_number)
    for g in sorted_games:
        if frozenset({g.home_team_id, g.away_team_id}) == pair:
            # Apply phase filter if requested
            if phase_filter is not None:
                game_phase = getattr(g, "phase", None)
                if game_phase != phase_filter:
                    continue
            total += 1
            if g.winner_team_id == team_a_id:
                wins_a += 1
                last_winner = team_a_id
            elif g.winner_team_id == team_b_id:
                wins_b += 1
                last_winner = team_b_id

    return {
        "wins_a": wins_a,
        "wins_b": wins_b,
        "total_games": total,
        "last_winner": last_winner,
    }


async def _compute_hot_players(
    repo: Repository,
    season_id: str,
    games: list[object],
) -> list[dict[str, object]]:
    """Find players with notable recent performances.

    Looks for hoopers who scored 20+ points in their most recent game.

    Args:
        repo: Repository for box score queries.
        season_id: Current season.
        games: All game results.

    Returns:
        List of dicts with hooper_id, name, team_name, stat, value, games.
    """
    hot: list[dict[str, object]] = []
    if not games:
        return hot

    # Get the most recent round's games
    max_round = max(g.round_number for g in games)
    recent_games = [g for g in games if g.round_number == max_round]

    for game in recent_games:
        game_row = await repo.get_game_result(game.id)
        if not game_row or not game_row.box_scores:
            continue
        for bs in game_row.box_scores:
            if bs.points >= 20:
                hooper = await repo.get_hooper(bs.hooper_id)
                team = await repo.get_team(bs.team_id) if bs.team_id else None
                hot.append(
                    {
                        "hooper_id": bs.hooper_id,
                        "name": hooper.name if hooper else bs.hooper_id,
                        "team_name": team.name if team else "",
                        "stat": "points",
                        "value": bs.points,
                        "games": 1,
                    }
                )

    return hot


def _build_rules_narrative(
    rule_changes: list[dict[str, object]],
) -> str:
    """Build a human-readable summary of active rule changes.

    Args:
        rule_changes: List of rule change dicts.

    Returns:
        A summary string like 'Three-pointers worth 5 (changed Round 4)'.
    """
    if not rule_changes:
        return ""

    parts: list[str] = []
    for rc in rule_changes:
        param = str(rc.get("parameter", ""))
        new_val = rc.get("new_value")
        round_enacted = rc.get("round_enacted")

        # Humanize parameter name
        label = param.replace("_", " ").title()

        narrative = rc.get("narrative", "")
        if narrative:
            parts.append(str(narrative))
        elif round_enacted is not None:
            parts.append(f"{label} set to {new_val} (changed Round {round_enacted})")
        else:
            parts.append(f"{label} set to {new_val}")

    return "; ".join(parts)


def format_narrative_for_prompt(ctx: NarrativeContext) -> str:
    """Format NarrativeContext as a text block suitable for AI prompt injection.

    This produces a structured text summary that can be appended to
    commentary, report, or any AI prompt to give the model dramatic context.

    Args:
        ctx: The narrative context to format.

    Returns:
        Multi-line string with all relevant narrative context.
    """
    lines: list[str] = []

    # Bedrock facts at the TOP — ground truth the AI must not contradict
    if ctx.bedrock_facts:
        lines.append("=== LEAGUE FACTS (do not contradict) ===")
        lines.append(ctx.bedrock_facts)
        lines.append("")

    # Phase and arc
    if ctx.phase not in ("regular",):
        phase_labels = {
            "semifinal": "SEMIFINAL PLAYOFFS",
            "finals": "CHAMPIONSHIP FINALS",
            "championship": "CHAMPIONSHIP CELEBRATION",
            "tiebreakers": "TIEBREAKER GAMES",
            "offseason": "OFFSEASON",
        }
        label = phase_labels.get(ctx.phase, ctx.phase.upper())
        lines.append(f"*** {label} ***")

    if ctx.total_rounds > 0:
        lines.append(
            f"Season arc: {ctx.season_arc} (Round {ctx.round_number} of {ctx.total_rounds})"
        )

    # Standings — label differently during playoffs
    if ctx.standings:
        if ctx.phase in ("semifinal", "finals"):
            lines.append("\nRegular-season standings (for seeding reference):")
        else:
            lines.append("\nStandings:")
        for s in ctx.standings:
            rank = s.get("rank", "?")
            name = s.get("team_name", s.get("team_id", "???"))
            wins = s.get("wins", 0)
            losses = s.get("losses", 0)
            streak = ctx.streaks.get(str(s.get("team_id", "")), 0)
            streak_str = ""
            if streak >= 3:
                streak_str = f" (W{streak} streak)"
            elif streak <= -3:
                streak_str = f" (L{abs(streak)} streak)"
            lines.append(f"  {rank}. {name} ({wins}W-{losses}L){streak_str}")

    # Playoff series — separate from season h2h
    if ctx.playoff_series:
        lines.append(
            "\nPLAYOFF SERIES (current series only — NOT season h2h):"
        )
        for key, series in ctx.playoff_series.items():
            if isinstance(series, dict):
                desc = series.get("description", "")
                best_of = series.get("best_of", "?")
                phase_label = series.get("phase_label", "playoff")
                lines.append(
                    f"  {key}: {desc} (best-of-{best_of}, {phase_label})"
                )

    # Head-to-head for this round's matchups
    if ctx.head_to_head:
        if ctx.phase in ("semifinal", "finals"):
            lines.append("\nSeason head-to-head (all games this season):")
        else:
            lines.append("\nMatchup history:")
        for key, h2h in ctx.head_to_head.items():
            total = h2h.get("total_games", 0)
            if total and isinstance(total, int) and total > 0:
                lines.append(
                    f"  {key}: {h2h.get('wins_a', 0)}-{h2h.get('wins_b', 0)} "
                    f"({total} game{'s' if total != 1 else ''})"
                )

    # Hot players
    if ctx.hot_players:
        lines.append("\nHot players:")
        for hp in ctx.hot_players:
            lines.append(
                f"  {hp.get('name', '?')} ({hp.get('team_name', '?')}): "
                f"{hp.get('value', 0)} {hp.get('stat', 'pts')}"
            )

    # Season game milestones
    if ctx.season_game_number > 0:
        lines.append(f"\nSeason game count: {ctx.season_game_number} games played so far")

    # Rule changes
    if ctx.rules_narrative:
        lines.append(f"\nRule changes in effect: {ctx.rules_narrative}")

    # Pre-rule average score for stat comparison context
    if ctx.pre_rule_avg_score > 0 and ctx.active_rule_changes:
        lines.append(
            f"Pre-rule-change average total score: {ctx.pre_rule_avg_score:.1f} points per game"
        )

    # Active proposal effects (v2: meta mutations, hook callbacks, narratives)
    if ctx.effects_narrative:
        lines.append(f"\nActive proposal effects:\n{ctx.effects_narrative}")

    # Governance
    if ctx.pending_proposals > 0:
        lines.append(
            f"\nGovernance: {ctx.pending_proposals} proposal(s) pending"
        )
    if ctx.governance_window_open:
        lines.append("Governance window: OPEN this round")
    elif ctx.next_tally_round is not None:
        lines.append(f"Next governance tally: Round {ctx.next_tally_round}")

    # Prior season memory
    if ctx.prior_seasons:
        lines.append("\nLeague history:")
        for ps in ctx.prior_seasons:
            name = ps.get("season_name", "Unknown Season")
            champ = ps.get("champion_team_name", "Unknown")
            games_count = ps.get("total_games", 0)
            rule_changes = ps.get("total_rule_changes", 0)
            lines.append(
                f"  {name}: champion {champ}, "
                f"{games_count} games, {rule_changes} rule changes"
            )
            legacy = ps.get("governance_legacy")
            if legacy:
                lines.append(f"    Governance: {legacy}")
            notable = ps.get("notable_rules")
            if notable and isinstance(notable, list):
                lines.append(f"    Key rules: {', '.join(str(r) for r in notable)}")

    return "\n".join(lines)
