"""Game API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from pinwheel.api.deps import RepoDep
from pinwheel.core.scheduler import compute_standings

router = APIRouter(prefix="/api/games", tags=["games"])


async def _build_bracket_data(repo: RepoDep) -> dict:
    """Build structured playoff bracket data from schedule and game results.

    Returns a dict with season info, semifinals, finals, and champion.
    Used by both the API endpoint and the page route.
    """
    season_row = await repo.get_active_season()
    if not season_row:
        return {
            "season_id": None,
            "season_name": None,
            "phase": "none",
            "semifinals": [],
            "finals": None,
            "champion": None,
        }

    season_id = season_row.id
    season_name = season_row.name
    season_status = season_row.status or "active"

    # Map raw status to a bracket-relevant phase label
    phase_map: dict[str, str] = {
        "setup": "regular_season",
        "active": "regular_season",
        "tiebreaker_check": "regular_season",
        "tiebreakers": "tiebreakers",
        "regular_season_complete": "playoffs",
        "playoffs": "playoffs",
        "championship": "complete",
        "offseason": "complete",
        "completed": "complete",
        "complete": "complete",
    }
    phase = phase_map.get(season_status, "regular_season")

    playoff_schedule = await repo.get_full_schedule(season_id, phase="playoff")
    if not playoff_schedule:
        return {
            "season_id": season_id,
            "season_name": season_name,
            "phase": phase,
            "semifinals": [],
            "finals": None,
            "champion": None,
        }

    # Get all playoff game results
    all_games = await repo.get_all_games(season_id)
    playoff_rounds = {s.round_number for s in playoff_schedule}
    playoff_games = [g for g in all_games if g.round_number in playoff_rounds]

    # Build team name + color cache
    team_cache: dict[str, dict] = {}

    async def _team_info(team_id: str) -> dict:
        if team_id not in team_cache:
            team = await repo.get_team(team_id)
            if team:
                team_cache[team_id] = {
                    "team_id": team_id,
                    "team_name": team.name,
                    "color": team.color or "#888",
                }
            else:
                team_cache[team_id] = {
                    "team_id": team_id,
                    "team_name": team_id,
                    "color": "#888",
                }
        return team_cache[team_id]

    # Identify initial (semifinal) matchups from earliest playoff round
    earliest_round = min(s.round_number for s in playoff_schedule)
    initial_entries = sorted(
        [s for s in playoff_schedule if s.round_number == earliest_round],
        key=lambda s: s.matchup_index,
    )
    initial_pairs = [
        frozenset({s.home_team_id, s.away_team_id}) for s in initial_entries
    ]

    # Separate finals entries (team pairs NOT in initial round)
    finals_entries = [
        s
        for s in playoff_schedule
        if frozenset({s.home_team_id, s.away_team_id}) not in initial_pairs
    ]

    is_direct_finals = len(initial_entries) == 1

    # Compute seedings from regular-season standings
    regular_games = [g for g in all_games if g.round_number not in playoff_rounds]
    regular_dicts = [
        {
            "home_team_id": g.home_team_id,
            "away_team_id": g.away_team_id,
            "home_score": g.home_score,
            "away_score": g.away_score,
            "winner_team_id": g.winner_team_id,
        }
        for g in regular_games
    ]
    standings = compute_standings(regular_dicts)
    seed_map: dict[str, int] = {}
    for idx, s in enumerate(standings):
        seed_map[s["team_id"]] = idx + 1

    # Helper: compute series record between two teams in playoff games
    def _series_record(
        team_a_id: str, team_b_id: str
    ) -> tuple[int, int, list[dict]]:
        pair = frozenset({team_a_id, team_b_id})
        a_wins = 0
        b_wins = 0
        game_list: list[dict] = []
        for g in sorted(playoff_games, key=lambda x: x.round_number):
            if frozenset({g.home_team_id, g.away_team_id}) == pair:
                if g.winner_team_id == team_a_id:
                    a_wins += 1
                elif g.winner_team_id == team_b_id:
                    b_wins += 1
                game_list.append(
                    {
                        "game_id": g.id,
                        "round_number": g.round_number,
                        "home_team_id": g.home_team_id,
                        "away_team_id": g.away_team_id,
                        "home_score": g.home_score,
                        "away_score": g.away_score,
                        "winner_team_id": g.winner_team_id,
                    }
                )
        return a_wins, b_wins, game_list

    # Build semifinals data
    semifinals: list[dict] = []
    if not is_direct_finals:
        for entry in initial_entries:
            home_id = entry.home_team_id
            away_id = entry.away_team_id
            home_info = await _team_info(home_id)
            away_info = await _team_info(away_id)
            h_wins, a_wins, games_list = _series_record(home_id, away_id)

            home_seed = seed_map.get(home_id, 0)
            away_seed = seed_map.get(away_id, 0)

            # Higher seed is the one with lower seed number
            if home_seed <= away_seed:
                high_info = {**home_info, "seed": home_seed, "wins": h_wins}
                low_info = {**away_info, "seed": away_seed, "wins": a_wins}
            else:
                high_info = {**away_info, "seed": away_seed, "wins": a_wins}
                low_info = {**home_info, "seed": home_seed, "wins": h_wins}

            semifinals.append(
                {
                    "seed_high": high_info,
                    "seed_low": low_info,
                    "games": games_list,
                }
            )

    # Build finals data
    finals: dict | None = None
    champion: dict | None = None

    # Determine finals team IDs
    finals_team_ids: tuple[str, str] | None = None
    if is_direct_finals:
        fe = initial_entries[0]
        finals_team_ids = (fe.home_team_id, fe.away_team_id)
    elif finals_entries:
        fe = finals_entries[0]
        finals_team_ids = (fe.home_team_id, fe.away_team_id)

    if finals_team_ids:
        team_a_id, team_b_id = finals_team_ids
        a_info = await _team_info(team_a_id)
        b_info = await _team_info(team_b_id)
        a_wins, b_wins, games_list = _series_record(team_a_id, team_b_id)

        a_seed = seed_map.get(team_a_id, 0)
        b_seed = seed_map.get(team_b_id, 0)

        finals = {
            "team_a": {**a_info, "seed": a_seed, "wins": a_wins},
            "team_b": {**b_info, "seed": b_seed, "wins": b_wins},
            "games": games_list,
        }

        # Check for champion
        season_config = season_row.config or {}
        champion_id = season_config.get("champion_team_id")
        if champion_id:
            champ_info = await _team_info(champion_id)
            champion = champ_info

    return {
        "season_id": season_id,
        "season_name": season_name,
        "phase": phase,
        "semifinals": semifinals,
        "finals": finals,
        "champion": champion,
    }


@router.get("/playoffs/bracket")
async def get_playoff_bracket(repo: RepoDep) -> dict:
    """Get structured playoff bracket data.

    Returns bracket with semifinals, finals, series records, and champion.
    """
    bracket = await _build_bracket_data(repo)
    return {"data": bracket}


@router.get("/{game_id}")
async def get_game(game_id: str, repo: RepoDep) -> dict:
    """Get a game result by ID."""
    game = await repo.get_game_result(game_id)
    if not game:
        raise HTTPException(404, "Game not found")
    return {
        "data": {
            "id": game.id,
            "home_team_id": game.home_team_id,
            "away_team_id": game.away_team_id,
            "home_score": game.home_score,
            "away_score": game.away_score,
            "winner_team_id": game.winner_team_id,
            "total_possessions": game.total_possessions,
            "elam_target": game.elam_target,
            "quarter_scores": game.quarter_scores,
            "seed": game.seed,
        },
    }


@router.get("/{game_id}/boxscore")
async def get_boxscore(game_id: str, repo: RepoDep) -> dict:
    """Get box scores for a game."""
    game = await repo.get_game_result(game_id)
    if not game:
        raise HTTPException(404, "Game not found")
    return {
        "data": [
            {
                "hooper_id": bs.hooper_id,
                "team_id": bs.team_id,
                "points": bs.points,
                "field_goals_made": bs.field_goals_made,
                "field_goals_attempted": bs.field_goals_attempted,
                "three_pointers_made": bs.three_pointers_made,
                "three_pointers_attempted": bs.three_pointers_attempted,
                "assists": bs.assists,
                "steals": bs.steals,
                "turnovers": bs.turnovers,
            }
            for bs in game.box_scores
        ],
    }
