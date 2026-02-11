"""Mutable game state for the simulation engine.

GameState, AgentState, PossessionState — the working memory of a game in progress.
These are internal to the simulation; GameResult is the immutable output.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pinwheel.models.team import Agent, PlayerAttributes


@dataclass
class AgentState:
    """Mutable state of an Agent during a game."""

    agent: Agent
    current_stamina: float = 1.0
    fouls: int = 0
    ejected: bool = False
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
    minutes: float = 0.0
    moves_activated: list[str] = field(default_factory=list)

    @property
    def current_attributes(self) -> PlayerAttributes:
        """Attributes scaled by current stamina."""
        base = self.agent.attributes
        s = self.current_stamina
        return PlayerAttributes(
            scoring=max(1, int(base.scoring * s)),
            passing=max(1, int(base.passing * s)),
            defense=max(1, int(base.defense * s)),
            speed=max(1, int(base.speed * s)),
            stamina=base.stamina,
            iq=base.iq,
            ego=base.ego,
            chaotic_alignment=base.chaotic_alignment,
            fate=base.fate,
        )


@dataclass
class GameState:
    """Mutable state of a game in progress."""

    home_agents: list[AgentState]
    away_agents: list[AgentState]
    home_score: int = 0
    away_score: int = 0
    quarter: int = 1
    possession_number: int = 0
    total_possessions: int = 0
    home_has_ball: bool = True
    elam_activated: bool = False
    elam_target_score: int | None = None
    game_over: bool = False

    @property
    def home_starters(self) -> list[AgentState]:
        return [a for a in self.home_agents if a.agent.is_starter and not a.ejected]

    @property
    def away_starters(self) -> list[AgentState]:
        return [a for a in self.away_agents if a.agent.is_starter and not a.ejected]

    @property
    def offense(self) -> list[AgentState]:
        return self.home_starters if self.home_has_ball else self.away_starters

    @property
    def defense(self) -> list[AgentState]:
        return self.away_starters if self.home_has_ball else self.home_starters

    @property
    def score_diff(self) -> int:
        """Positive = home leading."""
        return self.home_score - self.away_score
