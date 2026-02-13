"""RuleSet — the complete set of governable parameters.

The central model. Consumed by simulation, governance, AI, and the API.
See docs/GLOSSARY.md: Rule / RuleSet.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RuleSet(BaseModel):
    """The complete set of governable parameters.

    Organized by tier (SIMULATION.md):
    - Tier 1: Game Mechanics — core gameplay numbers
    - Tier 2: Hooper Behavior — how hoopers interact with the environment
    - Tier 3: League Structure — season format, scheduling
    - Tier 4: Meta-Governance — rules about rules
    """

    # Tier 1: Game Mechanics
    quarter_minutes: int = Field(default=10, ge=3, le=20)
    shot_clock_seconds: int = Field(default=15, ge=10, le=60)
    three_point_value: int = Field(default=3, ge=1, le=10)
    two_point_value: int = Field(default=2, ge=1, le=10)
    free_throw_value: int = Field(default=1, ge=1, le=5)
    personal_foul_limit: int = Field(default=5, ge=3, le=10)
    team_foul_bonus_threshold: int = Field(default=4, ge=3, le=10)
    three_point_distance: float = Field(default=22.15, ge=15.0, le=30.0)
    elam_trigger_quarter: int = Field(default=3, ge=1, le=4)
    elam_margin: int = Field(default=15, ge=5, le=40)
    halftime_stamina_recovery: float = Field(default=0.40, ge=0.0, le=0.6)
    quarter_break_stamina_recovery: float = Field(default=0.15, ge=0.0, le=0.3)
    safety_cap_possessions: int = Field(default=300, ge=50, le=500)
    substitution_stamina_threshold: float = Field(default=0.35, ge=0.1, le=0.8)

    # Tier 2: Hooper Behavior
    max_shot_share: float = Field(default=1.0, ge=0.2, le=1.0)
    min_pass_per_possession: int = Field(default=0, ge=0, le=5)
    home_court_enabled: bool = True
    home_crowd_boost: float = Field(default=0.05, ge=0.0, le=0.15)
    away_fatigue_factor: float = Field(default=0.02, ge=0.0, le=0.10)
    crowd_pressure: float = Field(default=0.03, ge=0.0, le=0.10)
    altitude_stamina_penalty: float = Field(default=0.01, ge=0.0, le=0.05)
    travel_fatigue_enabled: bool = True
    travel_fatigue_per_mile: float = Field(default=0.001, ge=0.0, le=0.005)

    # Tier 3: League Structure
    teams_count: int = Field(default=8, ge=4, le=16)
    round_robins_per_season: int = Field(default=3, ge=1, le=5)
    playoff_teams: int = Field(default=4, ge=2, le=8)
    playoff_semis_best_of: int = Field(default=5, ge=1, le=7)
    playoff_finals_best_of: int = Field(default=7, ge=1, le=7)

    # Tier 4: Meta-Governance
    proposals_per_window: int = Field(default=3, ge=1, le=10)
    vote_threshold: float = Field(default=0.5, ge=0.3, le=0.8)


class RuleChange(BaseModel):
    """A single parameter change from a governance action."""

    parameter: str
    old_value: int | float | bool
    new_value: int | float | bool
    source_proposal_id: str
    round_enacted: int


DEFAULT_RULESET = RuleSet()
