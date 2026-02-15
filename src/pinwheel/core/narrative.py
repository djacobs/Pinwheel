"""NarrativeContext — runtime dramatic awareness for all output systems.

A read-only data aggregation layer computed once per round in step_round().
Surfaces streaks, standings, rivalries, rule changes, season arc, and
governance state so commentary, reports, embeds, and templates can produce
contextually rich output.

This module NEVER writes to the database. It only reads.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pinwheel.db.repository import Repository

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


async def compute_narrative_context(
    repo: Repository,
    season_id: str,
    round_number: int,
    governance_interval: int = 1,
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
    if round_schedule and round_schedule[0].phase == "playoff":
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

        # --- Hot players ---
        ctx.hot_players = await _compute_hot_players(repo, season_id, games)

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
) -> dict[str, object]:
    """Compute head-to-head record between two teams.

    Args:
        games: All game results for the season.
        team_a_id: First team ID.
        team_b_id: Second team ID.

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

    # Standings
    if ctx.standings:
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

    # Head-to-head for this round's matchups
    if ctx.head_to_head:
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

    # Rule changes
    if ctx.rules_narrative:
        lines.append(f"\nRule changes in effect: {ctx.rules_narrative}")

    # Governance
    if ctx.pending_proposals > 0:
        lines.append(
            f"\nGovernance: {ctx.pending_proposals} proposal(s) pending"
        )
    if ctx.governance_window_open:
        lines.append("Governance window: OPEN this round")
    elif ctx.next_tally_round is not None:
        lines.append(f"Next governance tally: Round {ctx.next_tally_round}")

    return "\n".join(lines)
