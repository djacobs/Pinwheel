"""Game result models — output types from the simulation engine.

See docs/product/GLOSSARY.md: Game, Box Score, Possession, Elam Ending.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class GameEvent(BaseModel):
    """A granular event within a possession.

    Every event type is governable surface. Events are stored as JSON on
    the possession row (``PossessionLog.events``) — NOT one DB row per event.

    ``event_type`` is namespaced: ``"shot.at_rim"``, ``"turnover.bad_pass"``,
    ``"rebound.defensive"``, ``"foul.shooting"``, ``"block"``, ``"assist"``, ...
    ``detail`` carries the subtype: ``"dunk"``, ``"stepback"``, ``"tip"``, ...
    """

    seq: int
    event_type: str
    actor_id: str = ""
    target_id: str = ""
    team_id: str = ""
    outcome: str = ""  # "made" | "missed" | "stolen" | "success" | ...
    detail: str = ""  # subtype: "dunk", "stepback", ...
    points: int = 0
    clock: str = ""
    zone: str = ""  # "paint" | "mid" | "perimeter" | "backcourt"
    tags: list[str] = Field(default_factory=list)


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
    is_offensive_rebound: bool = False
    move_activated: str = ""
    defensive_scheme: str = ""
    home_score: int = 0
    away_score: int = 0
    game_clock: str = ""
    events: list[GameEvent] = Field(default_factory=list)
    """Granular event chain for this possession (Phase 1 event substrate).

    Additive: old serialized rows without this field deserialize to [].
    """


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
    # --- Attribution & hustle stats (Phase 2, all additive) ---
    potential_assists: int = 0
    passes_made: int = 0
    box_outs: int = 0
    screen_assists: int = 0
    deflections: int = 0
    contested_shots: int = 0
    drives: int = 0
    # --- Tier-2 hustle stats (Phase 4, additive) ---
    loose_balls: int = 0

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
    home_strategy_summary: str = ""
    away_strategy_summary: str = ""


class CommentaryLine(BaseModel):
    """AI-generated commentary for a possession or moment."""

    game_id: str
    possession_index: int
    quarter: int
    commentary: str
    energy: Literal["low", "medium", "high", "peak"] = "low"
    tags: list[str] = Field(default_factory=list)
