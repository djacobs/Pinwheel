"""Season management -- creating new seasons, carrying over teams and rules.

Supports the "Start Season 2" flow: after a season is archived, the admin
starts a fresh season with either default rules or carried-forward rules.
Teams, hoopers, and governor enrollments are carried over; tokens are regenerated.

Also handles season archiving: when a season is completed, archive_season()
creates a frozen snapshot capturing final standings, rule change history,
champion info, and aggregate statistics.

Season lifecycle phases:
    SETUP -> ACTIVE -> TIEBREAKER_CHECK -> TIEBREAKERS -> PLAYOFFS
         -> CHAMPIONSHIP -> OFFSEASON -> COMPLETE

See docs/plans/2026-02-14-season-lifecycle.md for the full design.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from pinwheel.core.scheduler import compute_standings, generate_round_robin
from pinwheel.core.tokens import regenerate_tokens
from pinwheel.db.models import SeasonArchiveRow
from pinwheel.models.rules import DEFAULT_RULESET, RuleSet

# Default offseason governance window: 1 hour (configurable via PINWHEEL_OFFSEASON_WINDOW)
DEFAULT_OFFSEASON_WINDOW_SECONDS = 3600

if TYPE_CHECKING:
    from pinwheel.core.event_bus import EventBus
    from pinwheel.db.models import SeasonRow
    from pinwheel.db.repository import Repository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Season Phase Enum & Transitions
# ---------------------------------------------------------------------------


class SeasonPhase(StrEnum):
    """All valid season lifecycle phases.

    The str mixin allows direct comparison with raw status strings stored
    in the database (e.g., ``season.status == SeasonPhase.ACTIVE``).
    """

    SETUP = "setup"
    ACTIVE = "active"
    TIEBREAKER_CHECK = "tiebreaker_check"
    TIEBREAKERS = "tiebreakers"
    PLAYOFFS = "playoffs"
    CHAMPIONSHIP = "championship"
    OFFSEASON = "offseason"
    COMPLETE = "complete"


# Legacy status values that map to SeasonPhase values.
# Season 1 data may have "completed" instead of "complete".
_LEGACY_STATUS_MAP: dict[str, SeasonPhase] = {
    "completed": SeasonPhase.COMPLETE,
    "archived": SeasonPhase.COMPLETE,
    "regular_season_complete": SeasonPhase.PLAYOFFS,
}


def normalize_phase(status: str) -> SeasonPhase:
    """Convert a raw status string (possibly legacy) to a SeasonPhase.

    Handles backward compatibility: ``"completed"`` -> ``COMPLETE``,
    ``"regular_season_complete"`` -> ``PLAYOFFS``, etc.
    """
    if status in _LEGACY_STATUS_MAP:
        return _LEGACY_STATUS_MAP[status]
    try:
        return SeasonPhase(status)
    except ValueError:
        # Unknown status -- treat as ACTIVE to avoid breaking running seasons
        logger.warning("unknown_season_status status=%s defaulting_to=active", status)
        return SeasonPhase.ACTIVE


# Allowed phase transitions. Key = current phase, value = set of valid next phases.
ALLOWED_TRANSITIONS: dict[SeasonPhase, set[SeasonPhase]] = {
    SeasonPhase.SETUP: {SeasonPhase.ACTIVE},
    SeasonPhase.ACTIVE: {
        SeasonPhase.TIEBREAKER_CHECK,
        SeasonPhase.PLAYOFFS,
    },
    SeasonPhase.TIEBREAKER_CHECK: {
        SeasonPhase.TIEBREAKERS,
        SeasonPhase.PLAYOFFS,
    },
    SeasonPhase.TIEBREAKERS: {SeasonPhase.PLAYOFFS},
    SeasonPhase.PLAYOFFS: {SeasonPhase.CHAMPIONSHIP, SeasonPhase.COMPLETE},
    SeasonPhase.CHAMPIONSHIP: {SeasonPhase.OFFSEASON, SeasonPhase.COMPLETE},
    SeasonPhase.OFFSEASON: {SeasonPhase.COMPLETE},
    SeasonPhase.COMPLETE: set(),  # terminal state
}

# Phases considered "active" for get_active_season() queries --
# everything except SETUP and COMPLETE.
ACTIVE_PHASES: frozenset[str] = frozenset(
    p.value for p in SeasonPhase if p not in (SeasonPhase.SETUP, SeasonPhase.COMPLETE)
)


async def transition_season(
    repo: Repository,
    season_id: str,
    to_phase: SeasonPhase,
    event_bus: EventBus | None = None,
) -> SeasonPhase:
    """Validate and execute a season phase transition.

    Args:
        repo: Database repository.
        season_id: The season to transition.
        to_phase: The target phase.
        event_bus: Optional event bus for publishing phase change events.

    Returns:
        The new phase after transition.

    Raises:
        ValueError: If the season is not found or the transition is invalid.
    """
    season = await repo.get_season(season_id)
    if season is None:
        raise ValueError(f"Season {season_id} not found")

    current = normalize_phase(season.status)
    allowed = ALLOWED_TRANSITIONS.get(current, set())

    if to_phase not in allowed:
        msg = (
            f"Invalid season transition: {current.value} -> {to_phase.value}. "
            f"Allowed: {sorted(p.value for p in allowed)}"
        )
        raise ValueError(msg)

    # Update status
    await repo.update_season_status(season_id, to_phase.value)

    logger.info(
        "season_phase_changed season=%s from=%s to=%s",
        season_id,
        current.value,
        to_phase.value,
    )

    if event_bus:
        await event_bus.publish(
            "season.phase_changed",
            {
                "season_id": season_id,
                "from_phase": current.value,
                "to_phase": to_phase.value,
            },
        )

    return to_phase


# ---------------------------------------------------------------------------
# Championship Phase
# ---------------------------------------------------------------------------


async def compute_awards(repo: Repository, season_id: str) -> list[dict]:
    """Compute end-of-season awards from game stats and governance events.

    Awards:
        Gameplay:
        - MVP: highest points per game
        - Defensive Player of the Season: highest steals per game
        - Most Efficient: best field goal percentage (min 20 FGA)
        Governance:
        - Most Active Governor: most proposals + votes
        - Coalition Builder: most token trades
        - Rule Architect: highest proposal pass rate (min 1 proposal)

    Returns:
        List of award dicts with keys: category, award, recipient_id,
        recipient_name, stat_value.
    """
    from sqlalchemy import select

    from pinwheel.db.models import BoxScoreRow, GameResultRow

    awards: list[dict] = []

    # --- Gameplay awards (from box scores) ---

    # Fetch all box scores for the season via a join on game results
    stmt = (
        select(BoxScoreRow)
        .join(GameResultRow, BoxScoreRow.game_id == GameResultRow.id)
        .where(GameResultRow.season_id == season_id)
    )
    result = await repo.session.execute(stmt)
    all_box_scores = list(result.scalars().all())

    if all_box_scores:
        # Aggregate per-hooper stats
        hooper_stats: dict[str, dict] = {}
        for bs in all_box_scores:
            hid = bs.hooper_id
            if hid not in hooper_stats:
                hooper_stats[hid] = {
                    "games": 0,
                    "points": 0,
                    "steals": 0,
                    "fgm": 0,
                    "fga": 0,
                }
            hooper_stats[hid]["games"] += 1
            hooper_stats[hid]["points"] += bs.points
            hooper_stats[hid]["steals"] += bs.steals
            hooper_stats[hid]["fgm"] += bs.field_goals_made
            hooper_stats[hid]["fga"] += bs.field_goals_attempted

        # Build name lookup
        hooper_ids = list(hooper_stats.keys())
        name_lookup: dict[str, str] = {}
        for hid in hooper_ids:
            hooper = await repo.get_hooper(hid)
            if hooper:
                name_lookup[hid] = hooper.name

        # MVP: highest PPG
        ppg_candidates = [
            (hid, stats["points"] / stats["games"])
            for hid, stats in hooper_stats.items()
            if stats["games"] > 0
        ]
        if ppg_candidates:
            ppg_candidates.sort(key=lambda x: x[1], reverse=True)
            mvp_id, mvp_ppg = ppg_candidates[0]
            awards.append(
                {
                    "category": "gameplay",
                    "award": "MVP",
                    "recipient_id": mvp_id,
                    "recipient_name": name_lookup.get(mvp_id, mvp_id),
                    "stat_value": round(mvp_ppg, 1),
                    "stat_label": "PPG",
                }
            )

        # Defensive Player: highest steals/game
        spg_candidates = [
            (hid, stats["steals"] / stats["games"])
            for hid, stats in hooper_stats.items()
            if stats["games"] > 0
        ]
        if spg_candidates:
            spg_candidates.sort(key=lambda x: x[1], reverse=True)
            def_id, def_spg = spg_candidates[0]
            awards.append(
                {
                    "category": "gameplay",
                    "award": "Defensive Player of the Season",
                    "recipient_id": def_id,
                    "recipient_name": name_lookup.get(def_id, def_id),
                    "stat_value": round(def_spg, 1),
                    "stat_label": "SPG",
                }
            )

        # Most Efficient: best FG% (min 20 FGA)
        fg_candidates = [
            (hid, stats["fgm"] / stats["fga"])
            for hid, stats in hooper_stats.items()
            if stats["fga"] >= 20
        ]
        if fg_candidates:
            fg_candidates.sort(key=lambda x: x[1], reverse=True)
            eff_id, eff_pct = fg_candidates[0]
            awards.append(
                {
                    "category": "gameplay",
                    "award": "Most Efficient",
                    "recipient_id": eff_id,
                    "recipient_name": name_lookup.get(eff_id, eff_id),
                    "stat_value": round(eff_pct * 100, 1),
                    "stat_label": "FG%",
                }
            )

    # --- Governance awards (from event log) ---

    # Most Active Governor: most proposals + votes
    proposal_events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["proposal.submitted"],
    )
    vote_events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["vote.cast"],
    )

    gov_activity: dict[str, int] = {}
    for evt in proposal_events:
        gid = evt.governor_id or ""
        if gid:
            gov_activity[gid] = gov_activity.get(gid, 0) + 1
    for evt in vote_events:
        gid = evt.governor_id or ""
        if gid:
            gov_activity[gid] = gov_activity.get(gid, 0) + 1

    if gov_activity:
        most_active = max(gov_activity, key=gov_activity.get)  # type: ignore[arg-type]
        # Try to get governor display name
        player = await repo.get_player(most_active)
        gov_name = player.username if player else most_active
        awards.append(
            {
                "category": "governance",
                "award": "Most Active Governor",
                "recipient_id": most_active,
                "recipient_name": gov_name,
                "stat_value": gov_activity[most_active],
                "stat_label": "actions",
            }
        )

    # Coalition Builder: most token trades
    trade_events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["trade.accepted"],
    )
    trade_counts: dict[str, int] = {}
    for evt in trade_events:
        for key in ("from_governor_id", "to_governor_id"):
            gid = evt.payload.get(key, "")
            if gid:
                trade_counts[gid] = trade_counts.get(gid, 0) + 1

    if trade_counts:
        top_trader = max(trade_counts, key=trade_counts.get)  # type: ignore[arg-type]
        player = await repo.get_player(top_trader)
        trader_name = player.username if player else top_trader
        awards.append(
            {
                "category": "governance",
                "award": "Coalition Builder",
                "recipient_id": top_trader,
                "recipient_name": trader_name,
                "stat_value": trade_counts[top_trader],
                "stat_label": "trades",
            }
        )

    # Rule Architect: highest proposal pass rate (min 1 submitted)
    proposals_by_gov: dict[str, int] = {}
    for evt in proposal_events:
        gid = evt.governor_id or ""
        if gid:
            proposals_by_gov[gid] = proposals_by_gov.get(gid, 0) + 1

    passed_events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["proposal.passed"],
    )
    # Map passed proposals back to their submitters
    passed_proposal_ids = {evt.aggregate_id for evt in passed_events}
    passed_by_gov: dict[str, int] = {}
    for evt in proposal_events:
        pid = evt.payload.get("id", evt.aggregate_id)
        gid = evt.governor_id or ""
        if gid and pid in passed_proposal_ids:
            passed_by_gov[gid] = passed_by_gov.get(gid, 0) + 1

    if proposals_by_gov:
        # Calculate pass rates
        pass_rates = {
            gid: passed_by_gov.get(gid, 0) / count
            for gid, count in proposals_by_gov.items()
            if count > 0
        }
        if pass_rates:
            best_architect = max(pass_rates, key=pass_rates.get)  # type: ignore[arg-type]
            player = await repo.get_player(best_architect)
            arch_name = player.username if player else best_architect
            awards.append(
                {
                    "category": "governance",
                    "award": "Rule Architect",
                    "recipient_id": best_architect,
                    "recipient_name": arch_name,
                    "stat_value": round(pass_rates[best_architect] * 100, 1),
                    "stat_label": "pass rate %",
                }
            )

    return awards


async def enter_championship(
    repo: Repository,
    season_id: str,
    champion_team_id: str,
    duration_seconds: int = 1800,
    event_bus: EventBus | None = None,
) -> dict:
    """Transition a season into the CHAMPIONSHIP phase.

    Computes awards, stores championship data in ``season.config`` JSON,
    and publishes a ``season.championship_started`` event.

    Args:
        repo: Database repository.
        season_id: The season entering championship.
        champion_team_id: The team that won the finals.
        duration_seconds: How long the championship window lasts (default 30 min).
        event_bus: Optional event bus.

    Returns:
        Championship config dict stored on the season.
    """
    # Transition to CHAMPIONSHIP
    await transition_season(repo, season_id, SeasonPhase.CHAMPIONSHIP, event_bus=None)

    # Compute awards
    awards = await compute_awards(repo, season_id)

    # Get champion team name
    champion_team = await repo.get_team(champion_team_id)
    champion_name = champion_team.name if champion_team else champion_team_id

    # Build championship config
    ends_at = datetime.now(UTC) + timedelta(seconds=duration_seconds)
    championship_config = {
        "champion_team_id": champion_team_id,
        "champion_team_name": champion_name,
        "awards": awards,
        "championship_ends_at": ends_at.isoformat(),
        "championship_duration_seconds": duration_seconds,
    }

    # Store in season.config
    season = await repo.get_season(season_id)
    if season:
        # Create a new dict to ensure SQLAlchemy detects the mutation
        new_config = dict(season.config or {})
        new_config.update(championship_config)
        season.config = new_config
        await repo.session.flush()

    logger.info(
        "championship_started season=%s champion=%s (%s) awards=%d duration=%ds",
        season_id,
        champion_team_id,
        champion_name,
        len(awards),
        duration_seconds,
    )

    # Publish event (after transition, so subscribers see the full config)
    if event_bus:
        await event_bus.publish(
            "season.championship_started",
            {
                "season_id": season_id,
                "champion_team_id": champion_team_id,
                "champion_team_name": champion_name,
                "awards": awards,
                "championship_ends_at": ends_at.isoformat(),
            },
        )

    return championship_config


# ---------------------------------------------------------------------------
# Tiebreaker Logic
# ---------------------------------------------------------------------------


def _compute_head_to_head(
    results: list[dict],
    team_a: str,
    team_b: str,
) -> tuple[int, int]:
    """Compute head-to-head wins between two teams.

    Args:
        results: List of game result dicts with home_team_id, away_team_id,
                 winner_team_id.
        team_a: First team ID.
        team_b: Second team ID.

    Returns:
        Tuple of (team_a_wins, team_b_wins) in head-to-head matchups.
    """
    a_wins = 0
    b_wins = 0
    for r in results:
        teams_in_game = {r["home_team_id"], r["away_team_id"]}
        if teams_in_game == {team_a, team_b}:
            if r["winner_team_id"] == team_a:
                a_wins += 1
            elif r["winner_team_id"] == team_b:
                b_wins += 1
    return a_wins, b_wins


def check_tiebreakers(
    standings: list[dict],
    results: list[dict],
    num_playoff_teams: int = 4,
) -> tuple[list[dict], bool]:
    """Check for ties at the playoff cutoff and attempt to resolve them.

    Tiebreaker order:
        (a) Head-to-head record between tied teams
        (b) Point differential (higher is better)
        (c) Points scored (higher is better)

    If all three criteria are identical, tiebreaker games are needed.

    Args:
        standings: Sorted standings list from compute_standings().
            Each dict has: team_id, wins, losses, points_for,
            points_against, point_diff.
        results: Raw game result dicts for head-to-head computation.
        num_playoff_teams: Number of teams that qualify for playoffs.

    Returns:
        Tuple of (resolved_standings, needs_tiebreaker_games).
        - resolved_standings: standings re-sorted with tiebreakers applied.
        - needs_tiebreaker_games: True if ties could not be resolved and
          tiebreaker games must be played.
    """
    if len(standings) <= num_playoff_teams:
        # Everyone qualifies -- no tiebreaker needed
        return standings, False

    # Find the wins at the cutoff boundary
    cutoff_wins = standings[num_playoff_teams - 1]["wins"]

    # Identify teams tied at the cutoff boundary
    # (teams just inside AND just outside the cutoff with the same win count)
    tied_teams = [
        s for s in standings if s["wins"] == cutoff_wins
    ]

    if len(tied_teams) <= 1:
        # No tie at the boundary
        return standings, False

    # Check if all tied teams are clearly above or below the cutoff
    above_cutoff = [s for s in standings[:num_playoff_teams] if s["wins"] == cutoff_wins]
    below_cutoff = [s for s in standings[num_playoff_teams:] if s["wins"] == cutoff_wins]

    if not above_cutoff or not below_cutoff:
        # All tied teams are on the same side of the cutoff -- no conflict
        return standings, False

    # There IS a tie at the cutoff boundary. Attempt resolution.
    # Collect all teams that are tied at the cutoff boundary
    contested_teams = above_cutoff + below_cutoff

    # Apply tiebreaker criteria to sort the contested group
    resolved, unresolved = _resolve_tie_group(contested_teams, results)

    if unresolved:
        # Could not break the tie -- tiebreaker games needed
        return standings, True

    # Rebuild standings with the resolved order
    resolved_ids = [t["team_id"] for t in resolved]
    non_contested = [s for s in standings if s["team_id"] not in resolved_ids]

    # Split non_contested into above and below the tie group
    above = [s for s in non_contested if s["wins"] > cutoff_wins]
    below = [s for s in non_contested if s["wins"] < cutoff_wins]

    # Reassemble: above + resolved tie group + below
    final = above + resolved + below
    return final, False


def _resolve_tie_group(
    tied_teams: list[dict],
    results: list[dict],
) -> tuple[list[dict], bool]:
    """Attempt to resolve a tie group using tiebreaker criteria.

    Returns (sorted_teams, unresolved). If unresolved is True,
    the tie could not be broken.
    """
    if len(tied_teams) <= 1:
        return tied_teams, False

    # For 2-team tiebreakers, use head-to-head first
    if len(tied_teams) == 2:
        a, b = tied_teams[0], tied_teams[1]
        a_wins, b_wins = _compute_head_to_head(
            results, a["team_id"], b["team_id"]
        )
        if a_wins > b_wins:
            return [a, b], False
        if b_wins > a_wins:
            return [b, a], False

        # Head-to-head tied -> try point differential
        if a["point_diff"] != b["point_diff"]:
            if a["point_diff"] > b["point_diff"]:
                return [a, b], False
            return [b, a], False

        # Point differential tied -> try points scored
        if a["points_for"] != b["points_for"]:
            if a["points_for"] > b["points_for"]:
                return [a, b], False
            return [b, a], False

        # Everything tied -- cannot resolve
        return tied_teams, True

    # For multi-team tiebreakers (3+), sort by composite tiebreaker key.
    # Build head-to-head records among the tied group.
    team_ids = {t["team_id"] for t in tied_teams}
    h2h_wins: dict[str, int] = {tid: 0 for tid in team_ids}

    for r in results:
        if r["home_team_id"] in team_ids and r["away_team_id"] in team_ids:
            winner = r["winner_team_id"]
            if winner in h2h_wins:
                h2h_wins[winner] += 1

    def _sort_key(team: dict) -> tuple[int, int, int]:
        return (
            -h2h_wins.get(team["team_id"], 0),
            -team["point_diff"],
            -team["points_for"],
        )

    sorted_teams = sorted(tied_teams, key=_sort_key)

    # Check if the sort actually resolved the tie (i.e., no two adjacent
    # teams have the same key).
    for i in range(len(sorted_teams) - 1):
        if _sort_key(sorted_teams[i]) == _sort_key(sorted_teams[i + 1]):
            return sorted_teams, True

    return sorted_teams, False


async def generate_tiebreaker_games(
    repo: Repository,
    season_id: str,
    standings: list[dict],
    num_playoff_teams: int = 4,
    event_bus: EventBus | None = None,
) -> list[dict]:
    """Generate tiebreaker game schedule entries for unresolved ties.

    Creates schedule entries with phase='tiebreaker' for teams that
    cannot be separated by standard tiebreaker criteria.

    Args:
        repo: Database repository.
        season_id: The season with ties.
        standings: Current standings.
        num_playoff_teams: Playoff cutoff.
        event_bus: Optional event bus.

    Returns:
        List of tiebreaker matchup dicts.
    """
    # Find the cutoff boundary
    cutoff_wins = standings[num_playoff_teams - 1]["wins"]

    # Only include teams that straddle the cutoff
    above = [s for s in standings[:num_playoff_teams] if s["wins"] == cutoff_wins]
    below = [s for s in standings[num_playoff_teams:] if s["wins"] == cutoff_wins]
    contested_ids = [s["team_id"] for s in above + below]

    if len(contested_ids) < 2:
        return []

    # Find the next available round number
    full_schedule = await repo.get_full_schedule(season_id)
    games = await repo.get_all_games(season_id)
    max_sched = max((s.round_number for s in full_schedule), default=0) if full_schedule else 0
    max_played = max((g.round_number for g in games), default=0) if games else 0
    tb_round = max(max_sched, max_played) + 1

    # Create round-robin among contested teams
    matchups: list[dict] = []
    match_idx = 0
    for i in range(len(contested_ids)):
        for j in range(i + 1, len(contested_ids)):
            matchup = {
                "round_number": tb_round,
                "matchup_index": match_idx,
                "home_team_id": contested_ids[i],
                "away_team_id": contested_ids[j],
                "phase": "tiebreaker",
            }
            await repo.create_schedule_entry(
                season_id=season_id,
                round_number=tb_round,
                matchup_index=match_idx,
                home_team_id=contested_ids[i],
                away_team_id=contested_ids[j],
                phase="tiebreaker",
            )
            matchups.append(matchup)
            match_idx += 1

    logger.info(
        "tiebreaker_games_generated season=%s round=%d matchups=%d teams=%s",
        season_id,
        tb_round,
        len(matchups),
        contested_ids,
    )

    if event_bus:
        await event_bus.publish(
            "season.tiebreaker_games_generated",
            {
                "season_id": season_id,
                "tiebreaker_round": tb_round,
                "matchups": matchups,
                "contested_teams": contested_ids,
            },
        )

    return matchups


async def check_and_handle_tiebreakers(
    repo: Repository,
    season_id: str,
    num_playoff_teams: int = 4,
    event_bus: EventBus | None = None,
) -> SeasonPhase:
    """Check for ties at the playoff cutoff and handle them.

    Called after the regular season ends. Transitions the season to either
    TIEBREAKERS (if games needed) or PLAYOFFS (if ties resolved or none).

    Returns the phase the season transitioned to.
    """
    # Ensure season is in ACTIVE state before transitioning through
    # tiebreaker phases.  Seasons in "setup" or other pre-active states
    # need to be moved to ACTIVE first.
    season = await repo.get_season(season_id)
    if season is None:
        raise ValueError(f"Season {season_id} not found")
    current_phase = normalize_phase(season.status)
    if current_phase != SeasonPhase.ACTIVE:
        # Move to ACTIVE if allowed (e.g., from SETUP)
        allowed = ALLOWED_TRANSITIONS.get(current_phase, set())
        if SeasonPhase.ACTIVE in allowed:
            await transition_season(
                repo, season_id, SeasonPhase.ACTIVE, event_bus=event_bus,
            )
        elif current_phase != SeasonPhase.ACTIVE:
            # If we can't get to ACTIVE, update status directly
            await repo.update_season_status(season_id, SeasonPhase.ACTIVE.value)

    # Compute standings from all games
    games = await repo.get_all_games(season_id)
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
    standings = compute_standings(game_dicts)

    resolved_standings, needs_games = check_tiebreakers(
        standings, game_dicts, num_playoff_teams
    )

    if needs_games:
        # Transition ACTIVE -> TIEBREAKER_CHECK -> TIEBREAKERS
        await transition_season(
            repo, season_id, SeasonPhase.TIEBREAKER_CHECK, event_bus=event_bus,
        )
        await transition_season(
            repo, season_id, SeasonPhase.TIEBREAKERS, event_bus=event_bus,
        )
        await generate_tiebreaker_games(
            repo, season_id, standings, num_playoff_teams, event_bus=event_bus,
        )
        return SeasonPhase.TIEBREAKERS
    else:
        # No tiebreaker games needed -- go straight to PLAYOFFS
        # First pass through TIEBREAKER_CHECK (transient)
        await transition_season(
            repo, season_id, SeasonPhase.TIEBREAKER_CHECK, event_bus=event_bus,
        )
        await transition_season(
            repo, season_id, SeasonPhase.PLAYOFFS, event_bus=event_bus,
        )
        return SeasonPhase.PLAYOFFS


# ---------------------------------------------------------------------------
# Offseason Governance
# ---------------------------------------------------------------------------


async def enter_offseason(
    repo: Repository,
    season_id: str,
    duration_seconds: int = DEFAULT_OFFSEASON_WINDOW_SECONDS,
    event_bus: EventBus | None = None,
) -> dict:
    """Transition a season from CHAMPIONSHIP to OFFSEASON.

    Opens a governance window where governors can submit and vote on
    meta-rule proposals for the next season. Any rules enacted during
    the offseason carry forward.

    Args:
        repo: Database repository.
        season_id: The season entering offseason.
        duration_seconds: How long the offseason window lasts.
        event_bus: Optional event bus.

    Returns:
        Offseason config dict stored on the season.
    """
    await transition_season(repo, season_id, SeasonPhase.OFFSEASON, event_bus=None)

    ends_at = datetime.now(UTC) + timedelta(seconds=duration_seconds)
    offseason_config = {
        "offseason_ends_at": ends_at.isoformat(),
        "offseason_duration_seconds": duration_seconds,
    }

    season = await repo.get_season(season_id)
    if season:
        # Create a new dict to ensure SQLAlchemy detects the mutation
        new_config = dict(season.config or {})
        new_config.update(offseason_config)
        season.config = new_config
        await repo.session.flush()

    logger.info(
        "offseason_started season=%s duration=%ds ends_at=%s",
        season_id,
        duration_seconds,
        ends_at.isoformat(),
    )

    if event_bus:
        await event_bus.publish(
            "season.offseason_started",
            {
                "season_id": season_id,
                "offseason_ends_at": ends_at.isoformat(),
                "offseason_duration_seconds": duration_seconds,
            },
        )

    return offseason_config


async def close_offseason(
    repo: Repository,
    season_id: str,
    event_bus: EventBus | None = None,
) -> None:
    """Close the offseason window and transition to COMPLETE.

    Tallies any pending governance proposals, enacts rule changes,
    then archives the season.

    Args:
        repo: Database repository.
        season_id: The season to close.
        event_bus: Optional event bus.
    """
    from pinwheel.core.game_loop import tally_pending_governance
    from pinwheel.models.rules import RuleSet

    season = await repo.get_season(season_id)
    if not season:
        raise ValueError(f"Season {season_id} not found")

    ruleset = RuleSet(**(season.current_ruleset or {}))

    # Determine the last round number for governance tally
    games = await repo.get_all_games(season_id)
    last_round = max((g.round_number for g in games), default=0) if games else 0

    # Tally any pending governance proposals
    _new_ruleset, tallies, _gov_data = await tally_pending_governance(
        repo=repo,
        season_id=season_id,
        round_number=last_round,
        ruleset=ruleset,
        event_bus=event_bus,
    )

    logger.info(
        "offseason_governance_tallied season=%s tallies=%d",
        season_id,
        len(tallies),
    )

    # Transition to COMPLETE
    await transition_season(repo, season_id, SeasonPhase.COMPLETE, event_bus=event_bus)

    if event_bus:
        await event_bus.publish(
            "season.offseason_closed",
            {
                "season_id": season_id,
                "rules_enacted": len([t for t in tallies if t.passed]),
            },
        )


async def start_new_season(
    repo: Repository,
    league_id: str,
    season_name: str,
    carry_forward_rules: bool = True,
    previous_season_id: str | None = None,
) -> SeasonRow:
    """Start a new season in the league.

    Args:
        repo: Database repository.
        league_id: The league to create the season in.
        season_name: Display name for the new season.
        carry_forward_rules: If True, use previous season's final ruleset.
                           If False, reset to defaults.
        previous_season_id: Season to carry rules from (uses latest completed if None).

    Returns:
        The newly created SeasonRow.
    """
    # 1. Verify the league exists
    league = await repo.get_league(league_id)
    if league is None:
        msg = f"League {league_id} not found"
        raise ValueError(msg)

    # 2. Determine the ruleset
    ruleset_data: dict | None = None
    source_season_id: str | None = previous_season_id

    if carry_forward_rules:
        if source_season_id is not None:
            # Use the specified previous season's ruleset
            prev_season = await repo.get_season(source_season_id)
            if prev_season is None:
                msg = f"Previous season {source_season_id} not found"
                raise ValueError(msg)
            ruleset_data = prev_season.current_ruleset
        else:
            # Find the latest completed season in this league
            prev_season = await repo.get_latest_completed_season(league_id)
            if prev_season is not None:
                ruleset_data = prev_season.current_ruleset
                source_season_id = prev_season.id

    if ruleset_data is None:
        ruleset_data = DEFAULT_RULESET.model_dump()

    # 2b. Complete the previous season (tally any remaining proposals first)
    if source_season_id is not None:
        prev_season_to_close = await repo.get_season(source_season_id)
        if prev_season_to_close and prev_season_to_close.status not in (
            "completed",
            "complete",
            "archived",
        ):
            # Tally any untallied proposals so they resolve (ties fail at 0-0)
            from sqlalchemy import func as sa_func
            from sqlalchemy import select as sa_select

            from pinwheel.core.game_loop import tally_pending_governance
            from pinwheel.db.models import GameResultRow

            prev_ruleset = RuleSet(**(prev_season_to_close.current_ruleset or {}))
            max_round_result = await repo.session.execute(
                sa_select(
                    sa_func.coalesce(sa_func.max(GameResultRow.round_number), 0)
                ).where(GameResultRow.season_id == source_season_id)
            )
            last_round: int = max_round_result.scalar_one()
            updated_ruleset, tallies, _gov_data = await tally_pending_governance(
                repo, source_season_id, last_round, prev_ruleset,
            )
            if tallies:
                logger.info(
                    "start_new_season_final_tally season=%s tallied=%d",
                    source_season_id,
                    len(tallies),
                )
            # If tally changed the ruleset and we're carrying forward, use it
            if carry_forward_rules and updated_ruleset != prev_ruleset:
                ruleset_data = updated_ruleset.model_dump()

            prev_season_to_close.status = SeasonPhase.COMPLETE.value
            await repo.session.flush()
            logger.info(
                "start_new_season_closed_previous season=%s status=%s",
                source_season_id,
                prev_season_to_close.status,
            )

    # 3. Create the new season
    new_season = await repo.create_season(
        league_id=league_id,
        name=season_name,
        starting_ruleset=ruleset_data,
    )

    # 4. Carry over teams if there is a source season
    if source_season_id is None:
        # Fall back to the most recent season in this league (any status).
        # Using only "completed" seasons missed enrollments when the previous
        # season hadn't been marked completed before /new-season was called.
        from sqlalchemy import select as sa_select

        from pinwheel.db.models import SeasonRow

        stmt = (
            sa_select(SeasonRow)
            .where(
                SeasonRow.league_id == league_id,
                SeasonRow.id != new_season.id,
            )
            .order_by(SeasonRow.created_at.desc())
            .limit(1)
        )
        result = await repo.session.execute(stmt)
        prev = result.scalar_one_or_none()
        if prev is not None:
            source_season_id = prev.id

    if source_season_id is not None:
        await carry_over_teams(repo, source_season_id, new_season.id)

    # 5. Generate schedule for new season
    teams = await repo.get_teams_for_season(new_season.id)
    if len(teams) >= 2:
        team_ids = [t.id for t in teams]
        ruleset = RuleSet(**ruleset_data)
        matchups = generate_round_robin(
            team_ids,
            num_rounds=ruleset.round_robins_per_season,
        )
        for m in matchups:
            await repo.create_schedule_entry(
                season_id=new_season.id,
                round_number=m.round_number,
                matchup_index=m.matchup_index,
                home_team_id=m.home_team_id,
                away_team_id=m.away_team_id,
                phase=m.phase,
            )

    # 6. Regenerate tokens for all governors
    await regenerate_all_governor_tokens(repo, new_season.id)

    # 7. Mark season as active
    new_season.status = SeasonPhase.ACTIVE.value
    await repo.session.flush()

    logger.info(
        "new_season_created season_id=%s name=%s league_id=%s carry_rules=%s",
        new_season.id,
        season_name,
        league_id,
        carry_forward_rules,
    )

    return new_season


async def carry_over_teams(
    repo: Repository,
    from_season_id: str,
    to_season_id: str,
) -> list[str]:
    """Copy teams and their hoopers from one season to the next.

    Creates new team records linked to the new season.
    Hoopers get fresh stats but keep names, archetypes, attributes.
    Governor enrollments are carried over.

    Returns:
        List of new team IDs created in the target season.
    """
    old_teams = await repo.get_teams_for_season(from_season_id)
    new_team_ids: list[str] = []

    for old_team in old_teams:
        # Create new team for the new season
        new_team = await repo.create_team(
            season_id=to_season_id,
            name=old_team.name,
            color=old_team.color,
            color_secondary=old_team.color_secondary,
            motto=old_team.motto,
            venue=old_team.venue,
        )
        new_team_ids.append(new_team.id)

        # Copy hoopers with fresh records (same name, archetype, attributes, backstory)
        for hooper in old_team.hoopers:
            await repo.create_hooper(
                team_id=new_team.id,
                season_id=to_season_id,
                name=hooper.name,
                archetype=hooper.archetype,
                attributes=hooper.attributes,
                moves=hooper.moves,
                is_active=hooper.is_active,
                backstory=hooper.backstory or "",
            )

        # Carry over governor enrollments
        governors = await repo.get_governors_for_team(
            old_team.id,
            from_season_id,
        )
        for governor in governors:
            await repo.enroll_player(governor.id, new_team.id, to_season_id)

    logger.info(
        "teams_carried_over from=%s to=%s team_count=%d",
        from_season_id,
        to_season_id,
        len(new_team_ids),
    )

    return new_team_ids


async def regenerate_all_governor_tokens(
    repo: Repository,
    season_id: str,
) -> int:
    """Regenerate tokens for all governors enrolled in a season.

    Returns:
        Number of governors who received tokens.
    """
    teams = await repo.get_teams_for_season(season_id)
    governor_count = 0

    for team in teams:
        governors = await repo.get_governors_for_team(team.id, season_id)
        for governor in governors:
            await regenerate_tokens(
                repo=repo,
                governor_id=governor.id,
                team_id=team.id,
                season_id=season_id,
            )
            governor_count += 1

    logger.info(
        "tokens_regenerated season_id=%s governor_count=%d",
        season_id,
        governor_count,
    )

    return governor_count


async def archive_season(
    repo: Repository,
    season_id: str,
    api_key: str = "",
    event_bus: EventBus | None = None,
) -> SeasonArchiveRow:
    """Create an archive snapshot of a completed season.

    Gathers final standings, rule change history, game counts, proposal
    counts, and governor participation into an immutable archive row.
    Generates a season memorial (AI narratives + computed data) and stores
    it on the archive row. Marks the season as completed.

    Args:
        repo: Repository bound to an active session.
        season_id: The season to archive.
        api_key: Anthropic API key for AI narrative generation.
            If empty, uses mock narratives.
        event_bus: Optional event bus for publishing memorial events.

    Returns:
        The created SeasonArchiveRow.

    Raises:
        ValueError: If the season does not exist.
    """
    season = await repo.get_season(season_id)
    if not season:
        raise ValueError(f"Season {season_id} not found")

    # Gather all game results and compute standings
    games = await repo.get_all_games(season_id)
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
    standings = compute_standings(game_dicts)

    # Enrich standings with team names
    for s in standings:
        team = await repo.get_team(s["team_id"])
        if team:
            s["team_name"] = team.name

    # Determine champion (top of standings)
    champion_team_id = None
    champion_team_name = None
    if standings:
        champion_team_id = standings[0]["team_id"]
        champion_team_name = standings[0].get("team_name", str(champion_team_id))

    # Get rule change history from events
    rule_events = await repo.get_events_by_type(season_id=season_id, event_types=["rule.enacted"])
    rule_changes = [e.payload for e in rule_events]

    # Count proposals
    proposal_events = await repo.get_events_by_type(
        season_id=season_id, event_types=["proposal.submitted"]
    )

    # Count governors
    governors = await repo.get_all_governors_for_season(season_id)

    # Mark season complete (use raw "completed" for backward compat with
    # update_season_status which sets completed_at when status == "completed")
    await repo.update_season_status(season_id, "completed")

    # Gather memorial data (computed sections — AI narratives added later)
    from pinwheel.core.memorial import gather_memorial_data

    # Pull awards from championship config if available
    awards: list[dict] = []
    if season and season.config:
        awards = season.config.get("awards", [])

    memorial_data = await gather_memorial_data(repo, season_id, awards=awards)

    # Generate AI narratives (or mock if no API key)
    if api_key:
        from pinwheel.ai.report import generate_season_memorial

        memorial_data = await generate_season_memorial(
            memorial_data=memorial_data,
            season_id=season_id,
            api_key=api_key,
        )
    else:
        from pinwheel.ai.report import generate_season_memorial_mock

        memorial_data = generate_season_memorial_mock(memorial_data)

    # Create archive
    archive = SeasonArchiveRow(
        season_id=season_id,
        season_name=season.name,
        final_standings=standings,
        final_ruleset=season.current_ruleset or {},
        rule_change_history=rule_changes,
        champion_team_id=champion_team_id,
        champion_team_name=champion_team_name,
        total_games=len(games),
        total_proposals=len(proposal_events),
        total_rule_changes=len(rule_changes),
        governor_count=len(governors),
        memorial=memorial_data,
    )
    await repo.store_season_archive(archive)

    logger.info(
        "season_archived season=%s games=%d proposals=%d rule_changes=%d governors=%d",
        season_id,
        len(games),
        len(proposal_events),
        len(rule_changes),
        len(governors),
    )

    # Publish memorial generated event
    if event_bus:
        await event_bus.publish(
            "season.memorial_generated",
            {
                "season_id": season_id,
                "season_name": season.name,
                "champion_team_name": champion_team_name or "",
                "total_games": len(games),
                "total_proposals": len(proposal_events),
                "total_rule_changes": len(rule_changes),
                "narrative_excerpt": (memorial_data.get("season_narrative", ""))[:500],
            },
        )

    return archive
