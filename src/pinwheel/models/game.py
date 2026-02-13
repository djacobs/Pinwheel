"""Game result models — output types from the simulation engine.

See docs/GLOSSARY.md: Game, Box Score, Possession, Elam Ending.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PossessionLog(BaseModel):
    """Record of a single possession."""

    quarter: int
    possession_number: int
    offense_team_id: str
    ball_handler_id: str
    action: str  # drive, three_point, mid_range, post_up, pass, substitution
    result: str  # made, missed, turnover, foul, foul_out:..., fatigue:...
    points_scored: int = 0
    defender_id: str = ""
    assist_id: str = ""
    rebound_id: str = ""
    move_activated: str = ""
    defensive_scheme: str = ""
    home_score: int = 0
    away_score: int = 0
    game_clock: str = ""


class HooperBoxScore(BaseModel):
    """Per-Hooper stat line for a single Game."""

    hooper_id: str
    hooper_name: str
    team_id: str
    minutes: float = 0.0
    points: int = 0
    field_goals_made: int = 0
    field_goals_attempted: int = 0
    three_pointers_made: int = 0
    three_pointers_attempted: int = 0
    free_throws_made: int = 0
    free_throws_attempted: int = 0
    rebounds: int = 0
    assists: int = 0
    steals: int = 0
    blocks: int = 0
    turnovers: int = 0
    fouls: int = 0
    plus_minus: int = 0

    @property
    def fg_pct(self) -> float:
        if not self.field_goals_attempted:
            return 0.0
        return self.field_goals_made / self.field_goals_attempted

    @property
    def three_pct(self) -> float:
        return (
            self.three_pointers_made / self.three_pointers_attempted
            if self.three_pointers_attempted
            else 0.0
        )

    # Backward-compatible aliases
    @property
    def agent_id(self) -> str:
        return self.hooper_id

    @property
    def agent_name(self) -> str:
        return self.hooper_name


# Backward-compatible alias
AgentBoxScore = HooperBoxScore


class QuarterScore(BaseModel):
    """Score for a single quarter."""

    quarter: int
    home_score: int
    away_score: int


class GameResult(BaseModel):
    """Complete output of simulate_game(). Immutable record of a Game."""

    game_id: str
    home_team_id: str
    away_team_id: str
    home_score: int
    away_score: int
    winner_team_id: str
    seed: int
    total_possessions: int
    elam_activated: bool = False
    elam_target_score: int | None = None
    quarter_scores: list[QuarterScore] = Field(default_factory=list)
    box_scores: list[HooperBoxScore] = Field(default_factory=list)
    possession_log: list[PossessionLog] = Field(default_factory=list)
    duration_ms: float = 0.0


class CommentaryLine(BaseModel):
    """AI-generated commentary for a possession or moment."""

    game_id: str
    possession_index: int
    quarter: int
    commentary: str
    energy: Literal["low", "medium", "high", "peak"] = "low"
    tags: list[str] = Field(default_factory=list)
