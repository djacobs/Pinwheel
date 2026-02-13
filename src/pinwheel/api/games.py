"""Game API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from pinwheel.api.deps import RepoDep

router = APIRouter(prefix="/api/games", tags=["games"])


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
