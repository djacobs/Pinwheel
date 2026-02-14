"""Round-robin schedule generation.

Generates a valid schedule where every team plays every other team once per round.
Uses the circle method (polygon scheduling) for balanced round-robin.

Terminology:
  - **round**: one complete round-robin cycle where every team plays every other
    team exactly once.  With 4 teams this means C(4,2) = 6 games per round.
  - **num_rounds**: how many complete round-robins make up the regular season
    (default from ``RuleSet.round_robins_per_season``).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Matchup:
    """A single scheduled game between two teams."""

    round_number: int
    matchup_index: int
    home_team_id: str
    away_team_id: str
    phase: str = "regular"


def generate_round_robin(
    team_ids: list[str],
    num_rounds: int = 1,
) -> list[Matchup]:
    """Generate round-robin schedule using the circle method.

    Each *round* is one complete round-robin: every team plays every other
    team exactly once.  With N teams (even) this produces C(N,2) = N*(N-1)/2
    games per round.  ``num_rounds`` controls how many such complete
    round-robins are generated.

    Home/away alternates between rounds so no team is always home.

    Args:
        team_ids: List of team IDs to schedule.
        num_rounds: Number of complete round-robins (default 1).

    Returns:
        List of Matchup objects sorted by round_number, then matchup_index.
    """
    n = len(team_ids)
    if n < 2:
        return []

    # For odd number of teams, add a bye placeholder
    ids = list(team_ids)
    has_bye = n % 2 != 0
    if has_bye:
        ids.append("BYE")
        n += 1

    matchups: list[Matchup] = []

    for cycle in range(num_rounds):
        round_num = cycle + 1  # 1-indexed round number
        match_idx = 0

        # Circle method: fix team 0, rotate the rest
        rotating = list(ids[1:])

        for _slot in range(n - 1):
            for i in range(n // 2):
                if i == 0:
                    home_id = ids[0]
                    away_id = rotating[0]
                else:
                    home_id = rotating[i]
                    away_id = rotating[n - 1 - i]

                # Alternate home/away on even cycles
                if cycle % 2 == 1:
                    home_id, away_id = away_id, home_id

                # Skip bye games
                if home_id == "BYE" or away_id == "BYE":
                    continue

                matchups.append(
                    Matchup(
                        round_number=round_num,
                        matchup_index=match_idx,
                        home_team_id=home_id,
                        away_team_id=away_id,
                    )
                )
                match_idx += 1

            # Rotate: move last element to front
            rotating = [rotating[-1], *rotating[:-1]]

    return matchups


def compute_standings(
    results: list[dict],
) -> list[dict]:
    """Compute league standings from game results.

    Args:
        results: List of dicts with keys: home_team_id, away_team_id,
                 home_score, away_score, winner_team_id.

    Returns:
        Sorted list of standing dicts (wins desc, point_diff desc).
    """
    teams: dict[str, dict] = {}

    for r in results:
        for tid in (r["home_team_id"], r["away_team_id"]):
            if tid not in teams:
                teams[tid] = {
                    "team_id": tid,
                    "wins": 0,
                    "losses": 0,
                    "points_for": 0,
                    "points_against": 0,
                }

        home_id = r["home_team_id"]
        away_id = r["away_team_id"]
        teams[home_id]["points_for"] += r["home_score"]
        teams[home_id]["points_against"] += r["away_score"]
        teams[away_id]["points_for"] += r["away_score"]
        teams[away_id]["points_against"] += r["home_score"]

        winner = r["winner_team_id"]
        loser = away_id if winner == home_id else home_id
        teams[winner]["wins"] += 1
        teams[loser]["losses"] += 1

    for t in teams.values():
        t["point_diff"] = t["points_for"] - t["points_against"]

    return sorted(teams.values(), key=lambda t: (-t["wins"], -t["point_diff"]))
