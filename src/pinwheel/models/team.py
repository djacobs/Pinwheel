"""Team, Hooper, and related models.

See docs/product/GLOSSARY.md: Team, Hooper, Archetype, Venue, Move.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PlayerAttributes(BaseModel):
    """Nine attributes that define a Hooper's capabilities. Budget: 360 points."""

    scoring: int = Field(ge=1, le=100)
    passing: int = Field(ge=1, le=100)
    defense: int = Field(ge=1, le=100)
    speed: int = Field(ge=1, le=100)
    stamina: int = Field(ge=1, le=100)
    iq: int = Field(ge=1, le=100)
    ego: int = Field(ge=1, le=100)
    chaotic_alignment: int = Field(ge=1, le=100)
    fate: int = Field(ge=1, le=100)

    def total(self) -> int:
        """Sum of all attribute points."""
        return sum(self.model_dump().values())


class Move(BaseModel):
    """A special ability a Hooper can activate during a Possession."""

    name: str
    trigger: str
    effect: str
    attribute_gate: dict[str, int] = Field(default_factory=dict)
    source: Literal["archetype", "earned", "governed"] = "archetype"


class Venue(BaseModel):
    """A Team's home court. Affects gameplay via modifiers."""

    name: str
    capacity: int = Field(ge=500, le=50000)
    altitude_ft: int = Field(ge=0, le=10000, default=0)
    surface: str = "hardwood"
    location: list[float] = Field(default_factory=lambda: [45.5152, -122.6784])  # [lat, lon]


class Hooper(BaseModel):
    """A simulated basketball player."""

    id: str
    name: str
    team_id: str
    archetype: str
    backstory: str = ""
    attributes: PlayerAttributes
    moves: list[Move] = Field(default_factory=list)
    is_starter: bool = True


class TeamStrategy(BaseModel):
    """Interpreted strategy parameters derived from natural language."""

    three_point_bias: float = Field(default=0.0, ge=-20.0, le=20.0)
    mid_range_bias: float = Field(default=0.0, ge=-20.0, le=20.0)
    at_rim_bias: float = Field(default=0.0, ge=-20.0, le=20.0)
    defensive_intensity: float = Field(default=0.0, ge=-0.5, le=0.5)
    pace_modifier: float = Field(default=1.0, ge=0.7, le=1.3)
    substitution_threshold_modifier: float = Field(default=0.0, ge=-0.15, le=0.15)
    raw_text: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class Team(BaseModel):
    """A group of 4 Hoopers (3 starters + 1 bench)."""

    id: str
    name: str
    color: str = "#000000"
    color_secondary: str = "#ffffff"
    motto: str = ""
    venue: Venue
    hoopers: list[Hooper] = Field(default_factory=list)
