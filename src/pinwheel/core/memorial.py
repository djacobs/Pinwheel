"""Season memorial data collection — computed sections for end-of-season reports.

Gathers statistical leaders, key moments, head-to-head records, and rule
timeline from the database. These computed sections form the data backbone
of the season memorial; AI narrative generation is a separate phase.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from pinwheel.db.models import BoxScoreRow, GameResultRow

if TYPE_CHECKING:
    from pinwheel.db.repository import Repository

logger = logging.getLogger(__name__)


async def compute_statistical_leaders(repo: Repository, season_id: str) -> dict:
    """Compute top 3 per statistical category from box scores.

    Categories: PPG (points per game), APG (assists per game),
    SPG (steals per game), FG% (field goal percentage, min 10 FGA).

    Returns:
        Dict with keys "ppg", "apg", "spg", "fg_pct", each containing
        a list of up to 3 dicts with hooper_id, hooper_name, team_name,
        value, and games.
    """
    # Fetch all box scores for the season via join on game results
    stmt = (
        select(BoxScoreRow)
        .join(GameResultRow, BoxScoreRow.game_id == GameResultRow.id)
        .where(GameResultRow.season_id == season_id)
    )
    result = await repo.session.execute(stmt)
    all_box_scores = list(result.scalars().all())

    if not all_box_scores:
        return {"ppg": [], "apg": [], "spg": [], "fg_pct": []}

    # Aggregate per-hooper stats
    hooper_stats: dict[str, dict] = {}
    for bs in all_box_scores:
        hid = bs.hooper_id
        if hid not in hooper_stats:
            hooper_stats[hid] = {
                "games": 0,
                "points": 0,
                "assists": 0,
                "steals": 0,
                "fgm": 0,
                "fga": 0,
                "team_id": bs.team_id,
            }
        hooper_stats[hid]["games"] += 1
        hooper_stats[hid]["points"] += bs.points
        hooper_stats[hid]["assists"] += bs.assists
        hooper_stats[hid]["steals"] += bs.steals
        hooper_stats[hid]["fgm"] += bs.field_goals_made
        hooper_stats[hid]["fga"] += bs.field_goals_attempted

    # Build name lookups
    hooper_names: dict[str, str] = {}
    team_names: dict[str, str] = {}
    for hid, stats in hooper_stats.items():
        if hid not in hooper_names:
            hooper = await repo.get_hooper(hid)
            hooper_names[hid] = hooper.name if hooper else hid
        tid = stats["team_id"]
        if tid not in team_names:
            team = await repo.get_team(tid)
            team_names[tid] = team.name if team else tid

    def _build_leader(hid: str, value: float, games: int) -> dict:
        return {
            "hooper_id": hid,
            "hooper_name": hooper_names.get(hid, hid),
            "team_name": team_names.get(hooper_stats[hid]["team_id"], ""),
            "value": round(value, 1),
            "games": games,
        }

    # PPG: top 3
    ppg_list = [
        (hid, stats["points"] / stats["games"], stats["games"])
        for hid, stats in hooper_stats.items()
        if stats["games"] > 0
    ]
    ppg_list.sort(key=lambda x: x[1], reverse=True)
    ppg = [_build_leader(hid, val, g) for hid, val, g in ppg_list[:3]]

    # APG: top 3
    apg_list = [
        (hid, stats["assists"] / stats["games"], stats["games"])
        for hid, stats in hooper_stats.items()
        if stats["games"] > 0
    ]
    apg_list.sort(key=lambda x: x[1], reverse=True)
    apg = [_build_leader(hid, val, g) for hid, val, g in apg_list[:3]]

    # SPG: top 3
    spg_list = [
        (hid, stats["steals"] / stats["games"], stats["games"])
        for hid, stats in hooper_stats.items()
        if stats["games"] > 0
    ]
    spg_list.sort(key=lambda x: x[1], reverse=True)
    spg = [_build_leader(hid, val, g) for hid, val, g in spg_list[:3]]

    # FG%: top 3 (min 10 FGA to filter noise)
    fg_list = [
        (hid, stats["fgm"] / stats["fga"] * 100, stats["games"])
        for hid, stats in hooper_stats.items()
        if stats["fga"] >= 10
    ]
    fg_list.sort(key=lambda x: x[1], reverse=True)
    fg_pct = [_build_leader(hid, val, g) for hid, val, g in fg_list[:3]]

    return {"ppg": ppg, "apg": apg, "spg": spg, "fg_pct": fg_pct}


async def compute_key_moments(repo: Repository, season_id: str) -> list[dict]:
    """Identify 5-8 most notable games from the season.

    Criteria (in priority order):
    1. Playoff games (always notable)
    2. Closest margin (regular season nail-biters)
    3. Largest blowout
    4. Elam Ending activations (games with elam_target set)

    Each moment dict includes: game_id, round_number, home_team_name,
    away_team_name, home_score, away_score, margin, winner_name,
    moment_type, elam_target.

    Returns:
        List of 5-8 moment dicts, deduplicated.
    """
    # Fetch all games with box scores
    stmt = (
        select(GameResultRow)
        .where(GameResultRow.season_id == season_id)
        .options(selectinload(GameResultRow.box_scores))
        .order_by(GameResultRow.round_number, GameResultRow.matchup_index)
    )
    result = await repo.session.execute(stmt)
    all_games = list(result.scalars().all())

    if not all_games:
        return []

    # Fetch playoff schedule entries to identify playoff games
    playoff_schedule = await repo.get_full_schedule(season_id, phase="playoff")
    playoff_keys: set[tuple[int, int]] = set()
    for s in playoff_schedule:
        playoff_keys.add((s.round_number, s.matchup_index))

    # Build team name cache
    team_names: dict[str, str] = {}

    async def _team_name(tid: str) -> str:
        if tid not in team_names:
            team = await repo.get_team(tid)
            team_names[tid] = team.name if team else tid
        return team_names[tid]

    def _game_dict(game: GameResultRow, moment_type: str) -> dict:
        margin = abs(game.home_score - game.away_score)
        return {
            "game_id": game.id,
            "round_number": game.round_number,
            "home_team_id": game.home_team_id,
            "away_team_id": game.away_team_id,
            "home_score": game.home_score,
            "away_score": game.away_score,
            "margin": margin,
            "winner_team_id": game.winner_team_id,
            "moment_type": moment_type,
            "elam_target": game.elam_target,
        }

    moments: list[dict] = []
    seen_game_ids: set[str] = set()

    # 1. Playoff games (always notable)
    for game in all_games:
        key = (game.round_number, game.matchup_index)
        if key in playoff_keys:
            moments.append(_game_dict(game, "playoff"))
            seen_game_ids.add(game.id)

    # 2. Closest margin (regular season)
    regular_games = [g for g in all_games if g.id not in seen_game_ids]
    regular_games_sorted_close = sorted(
        regular_games,
        key=lambda g: abs(g.home_score - g.away_score),
    )
    for game in regular_games_sorted_close[:2]:
        if game.id not in seen_game_ids:
            moments.append(_game_dict(game, "closest_game"))
            seen_game_ids.add(game.id)

    # 3. Largest blowout
    regular_games_sorted_blowout = sorted(
        regular_games,
        key=lambda g: abs(g.home_score - g.away_score),
        reverse=True,
    )
    for game in regular_games_sorted_blowout[:1]:
        if game.id not in seen_game_ids:
            moments.append(_game_dict(game, "blowout"))
            seen_game_ids.add(game.id)

    # 4. Elam Ending activations
    elam_games = [g for g in all_games if g.elam_target is not None and g.id not in seen_game_ids]
    for game in elam_games[:2]:
        moments.append(_game_dict(game, "elam_ending"))
        seen_game_ids.add(game.id)

    # Enrich with team names
    for m in moments:
        m["home_team_name"] = await _team_name(m["home_team_id"])
        m["away_team_name"] = await _team_name(m["away_team_id"])
        m["winner_name"] = await _team_name(m["winner_team_id"])

    # Cap at 8 moments
    return moments[:8]


async def compute_head_to_head(repo: Repository, season_id: str) -> list[dict]:
    """Compute team-vs-team win/loss records and point differentials.

    Returns:
        List of dicts, each with: team_a_id, team_a_name, team_b_id,
        team_b_name, team_a_wins, team_b_wins, point_differential
        (positive means team_a scored more total).
    """
    all_games = await repo.get_all_game_results_for_season(season_id)

    if not all_games:
        return []

    # Build team name cache
    team_names: dict[str, str] = {}

    async def _team_name(tid: str) -> str:
        if tid not in team_names:
            team = await repo.get_team(tid)
            team_names[tid] = team.name if team else tid
        return team_names[tid]

    # Aggregate matchup data: key = (min(team_a, team_b), max(team_a, team_b))
    matchups: dict[tuple[str, str], dict] = {}

    for game in all_games:
        a, b = game.home_team_id, game.away_team_id
        # Canonical key: alphabetically sorted so (A, B) == (B, A)
        key = (min(a, b), max(a, b))

        if key not in matchups:
            matchups[key] = {
                "team_a_id": key[0],
                "team_b_id": key[1],
                "team_a_wins": 0,
                "team_b_wins": 0,
                "team_a_points": 0,
                "team_b_points": 0,
            }

        m = matchups[key]
        # Assign scores to canonical teams
        if game.home_team_id == key[0]:
            m["team_a_points"] += game.home_score
            m["team_b_points"] += game.away_score
        else:
            m["team_a_points"] += game.away_score
            m["team_b_points"] += game.home_score

        if game.winner_team_id == key[0]:
            m["team_a_wins"] += 1
        else:
            m["team_b_wins"] += 1

    # Build output
    result = []
    for _key, m in sorted(matchups.items()):
        result.append(
            {
                "team_a_id": m["team_a_id"],
                "team_a_name": await _team_name(m["team_a_id"]),
                "team_b_id": m["team_b_id"],
                "team_b_name": await _team_name(m["team_b_id"]),
                "team_a_wins": m["team_a_wins"],
                "team_b_wins": m["team_b_wins"],
                "point_differential": m["team_a_points"] - m["team_b_points"],
            }
        )

    return result


async def compute_rule_timeline(repo: Repository, season_id: str) -> list[dict]:
    """Build chronological timeline of rule changes during the season.

    Returns:
        List of dicts ordered by sequence number, each with: round_number,
        parameter, old_value, new_value, proposer_id, proposer_name.
    """
    # Get rule.enacted events
    rule_events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["rule.enacted"],
    )

    if not rule_events:
        return []

    # Also fetch proposal.submitted events to find proposers
    proposal_events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["proposal.submitted"],
    )
    # Map proposal_id -> governor_id
    proposal_to_governor: dict[str, str] = {}
    for evt in proposal_events:
        pid = evt.payload.get("id", evt.aggregate_id)
        if evt.governor_id:
            proposal_to_governor[pid] = evt.governor_id

    # Build name lookup cache
    governor_names: dict[str, str] = {}

    timeline = []
    for evt in rule_events:
        payload = evt.payload
        proposal_id = payload.get("proposal_id", "")
        governor_id = proposal_to_governor.get(proposal_id, "")

        # Resolve governor name
        governor_name = ""
        if governor_id:
            if governor_id not in governor_names:
                player = await repo.get_player(governor_id)
                governor_names[governor_id] = player.username if player else governor_id
            governor_name = governor_names[governor_id]

        timeline.append(
            {
                "round_number": evt.round_number or payload.get("round_enacted", 0),
                "parameter": payload.get("parameter", ""),
                "old_value": payload.get("old_value"),
                "new_value": payload.get("new_value"),
                "proposal_id": proposal_id,
                "proposer_id": governor_id,
                "proposer_name": governor_name,
            }
        )

    return timeline


async def gather_memorial_data(
    repo: Repository,
    season_id: str,
    awards: list[dict] | None = None,
) -> dict:
    """Orchestrate all memorial data collection.

    Calls each compute function and assembles the full memorial data dict.

    Args:
        repo: Database repository.
        season_id: The season to gather data for.
        awards: Pre-computed awards list. If None, an empty list is used.
            Awards are typically computed separately by compute_awards().

    Returns:
        Dict matching SeasonMemorial fields, suitable for JSON storage.
    """
    statistical_leaders = await compute_statistical_leaders(repo, season_id)
    key_moments = await compute_key_moments(repo, season_id)
    head_to_head = await compute_head_to_head(repo, season_id)
    rule_timeline = await compute_rule_timeline(repo, season_id)

    return {
        # AI narrative placeholders
        "season_narrative": "",
        "championship_recap": "",
        "champion_profile": "",
        "governance_legacy": "",
        # Computed data
        "awards": awards or [],
        "statistical_leaders": statistical_leaders,
        "key_moments": key_moments,
        "head_to_head": head_to_head,
        "rule_timeline": rule_timeline,
        # Metadata
        "generated_at": datetime.now(UTC).isoformat(),
        "model_used": "",
    }
