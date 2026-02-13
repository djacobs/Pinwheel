"""Scenario flagging (M.6) — detect follow-up-worthy game patterns.

Pure functions that scan game results and governance events. Each flag returns
a ScenarioFlag. Flags are surfaced to admin, never to players.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pinwheel.evals.models import ScenarioFlag

if TYPE_CHECKING:
    from pinwheel.db.repository import Repository


def detect_blowout(
    game_summaries: list[dict],
    round_number: int,
    season_id: str = "",
    elam_margin: int = 13,
) -> list[ScenarioFlag]:
    """Flag games where score differential > 2x Elam margin."""
    flags = []
    threshold = elam_margin * 2
    for game in game_summaries:
        diff = abs(game.get("home_score", 0) - game.get("away_score", 0))
        if diff > threshold:
            flags.append(
                ScenarioFlag(
                    flag_type="blowout_game",
                    severity="warning",
                    round_number=round_number,
                    season_id=season_id,
                    details={
                        "home_team": game.get("home_team", ""),
                        "away_team": game.get("away_team", ""),
                        "home_score": game.get("home_score", 0),
                        "away_score": game.get("away_score", 0),
                        "differential": diff,
                        "threshold": threshold,
                    },
                )
            )
    return flags


async def detect_suspicious_unanimity(
    repo: Repository,
    season_id: str,
    round_number: int,
    consecutive_threshold: int = 3,
) -> list[ScenarioFlag]:
    """Flag when all governors vote identically on consecutive proposals."""
    events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["vote.cast"],
    )

    # Group votes by proposal (in round order)
    votes_by_proposal: dict[str, list[str]] = {}
    for e in events:
        pid = (e.payload or {}).get("proposal_id", "")
        vote = (e.payload or {}).get("vote", "")
        if pid and vote:
            votes_by_proposal.setdefault(pid, []).append(vote)

    # Check for consecutive unanimity
    unanimous_streak = 0
    for _pid, votes in votes_by_proposal.items():
        if len(votes) >= 2 and len(set(votes)) == 1:
            unanimous_streak += 1
        else:
            unanimous_streak = 0

        if unanimous_streak >= consecutive_threshold:
            return [
                ScenarioFlag(
                    flag_type="suspicious_unanimity",
                    severity="warning",
                    round_number=round_number,
                    season_id=season_id,
                    details={
                        "consecutive_unanimous_votes": unanimous_streak,
                        "threshold": consecutive_threshold,
                    },
                )
            ]
    return []


async def detect_governance_stagnation(
    repo: Repository,
    season_id: str,
    round_number: int,
    stagnation_threshold: int = 3,
) -> list[ScenarioFlag]:
    """Flag when the same parameter is targeted 3+ rounds in a row."""
    events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["proposal.submitted"],
    )

    # Track parameter targets per round
    params_by_round: dict[int, list[str]] = {}
    for e in events:
        rn = e.round_number
        if rn is not None:
            interp = (e.payload or {}).get("interpretation") or {}
            param = interp.get("parameter")
            if param:
                params_by_round.setdefault(rn, []).append(param)

    # Check recent rounds for repeated parameter
    recent_params: list[set[str]] = []
    for rn in range(max(1, round_number - stagnation_threshold + 1), round_number + 1):
        recent_params.append(set(params_by_round.get(rn, [])))

    if len(recent_params) >= stagnation_threshold:
        # Find params that appear in all recent rounds
        common = set.intersection(*recent_params) if recent_params else set()
        if common:
            return [
                ScenarioFlag(
                    flag_type="governance_stagnation",
                    severity="info",
                    round_number=round_number,
                    season_id=season_id,
                    details={
                        "stagnant_parameters": list(common),
                        "rounds_checked": stagnation_threshold,
                    },
                )
            ]
    return []


async def detect_participation_collapse(
    repo: Repository,
    season_id: str,
    round_number: int,
    min_participation_rate: float = 0.5,
) -> list[ScenarioFlag]:
    """Flag when < 50% of known governors are active."""
    # Get all governors who have ever acted
    all_events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["proposal.submitted", "vote.cast"],
    )
    all_governors = {e.governor_id for e in all_events if e.governor_id}
    active_this_round = {
        e.governor_id for e in all_events if e.governor_id and e.round_number == round_number
    }

    if not all_governors:
        return []

    rate = len(active_this_round) / len(all_governors)
    if rate < min_participation_rate:
        return [
            ScenarioFlag(
                flag_type="participation_collapse",
                severity="warning",
                round_number=round_number,
                season_id=season_id,
                details={
                    "total_governors": len(all_governors),
                    "active_this_round": len(active_this_round),
                    "participation_rate": rate,
                    "threshold": min_participation_rate,
                },
            )
        ]
    return []


async def detect_rule_backfire(
    repo: Repository,
    season_id: str,
    round_number: int,
) -> list[ScenarioFlag]:
    """Flag when a team's win rate drops after their proposal passes."""
    # Get enacted rules
    enacted = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["rule.enacted"],
    )

    flags = []
    for e in enacted:
        # Find the original proposal to get team_id
        proposal_id = (e.payload or {}).get("source_proposal_id", "")
        if not proposal_id:
            continue

        proposal_events = await repo.get_events_for_aggregate("proposal", proposal_id)
        team_id = None
        for pe in proposal_events:
            if pe.team_id:
                team_id = pe.team_id
                break

        if not team_id:
            continue

        enact_round = (e.payload or {}).get("round_enacted", 0)
        if enact_round == 0 or round_number <= enact_round + 1:
            continue

        # Compare win rates before and after enactment
        all_games = await repo.get_all_game_results_for_season(season_id)
        wins_before = 0
        games_before = 0
        wins_after = 0
        games_after = 0

        for g in all_games:
            if g.home_team_id == team_id or g.away_team_id == team_id:
                won = g.winner_team_id == team_id
                if g.round_number < enact_round:
                    games_before += 1
                    wins_before += 1 if won else 0
                elif g.round_number > enact_round:
                    games_after += 1
                    wins_after += 1 if won else 0

        if games_before >= 2 and games_after >= 2:
            rate_before = wins_before / games_before
            rate_after = wins_after / games_after
            if rate_after < rate_before - 0.2:  # 20% drop
                flags.append(
                    ScenarioFlag(
                        flag_type="rule_backfire",
                        severity="info",
                        round_number=round_number,
                        season_id=season_id,
                        details={
                            "team_id": team_id,
                            "proposal_id": proposal_id,
                            "win_rate_before": rate_before,
                            "win_rate_after": rate_after,
                            "delta": rate_after - rate_before,
                        },
                    )
                )
    return flags


async def detect_all_flags(
    repo: Repository,
    season_id: str,
    round_number: int,
    game_summaries: list[dict],
) -> list[ScenarioFlag]:
    """Run all flag detectors and return combined results."""
    flags: list[ScenarioFlag] = []

    # Blowout detection (pure function)
    flags.extend(detect_blowout(game_summaries, round_number, season_id))

    # Async detectors
    flags.extend(await detect_suspicious_unanimity(repo, season_id, round_number))
    flags.extend(await detect_governance_stagnation(repo, season_id, round_number))
    flags.extend(await detect_participation_collapse(repo, season_id, round_number))
    flags.extend(await detect_rule_backfire(repo, season_id, round_number))

    return flags
