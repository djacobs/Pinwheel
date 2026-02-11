"""Shot probability and scoring resolution.

Logistic curves for base probability, modified by defense, IQ, stamina, and rules.
See SIMULATION.md "Scoring Resolution".
"""

from __future__ import annotations

import math
import random
from typing import Literal

from pinwheel.core.state import AgentState
from pinwheel.models.rules import RuleSet

ShotType = Literal["at_rim", "mid_range", "three_point", "free_throw"]

# Base probability midpoints per shot type (scoring attribute value where P = 0.5)
# Tuned so average agents (scoring ~40-50) produce basketball-like FG% (~45%)
BASE_MIDPOINTS: dict[ShotType, float] = {
    "at_rim": 30.0,
    "mid_range": 40.0,
    "three_point": 50.0,
    "free_throw": 25.0,
}

# Steepness of logistic curve per shot type
BASE_STEEPNESS: dict[ShotType, float] = {
    "at_rim": 0.05,
    "mid_range": 0.045,
    "three_point": 0.04,
    "free_throw": 0.06,
}


def logistic(x: float, midpoint: float, steepness: float) -> float:
    """Logistic function mapping attribute value to probability."""
    return 1.0 / (1.0 + math.exp(-steepness * (x - midpoint)))


def compute_contest(
    defender: AgentState,
    shot_type: ShotType,
    scheme_modifier: float,
) -> float:
    """Defense contest modifier. Returns multiplier in [0.5, 1.0]."""
    if shot_type == "free_throw":
        return 1.0
    defense = defender.current_attributes.defense
    contest = 1.0 - (defense / 200.0) - scheme_modifier
    return max(0.5, min(1.0, contest))


def compute_iq_modifier(iq: int) -> float:
    """IQ modifier: shot selection quality. Returns multiplier in [0.9, 1.1]."""
    return 0.9 + (iq / 500.0)


def compute_stamina_modifier(stamina: float) -> float:
    """Stamina modifier on shot quality. Returns multiplier in [0.7, 1.0]."""
    return 0.7 + 0.3 * stamina


def compute_shot_probability(
    shooter: AgentState,
    defender: AgentState,
    shot_type: ShotType,
    scheme_modifier: float,
    rules: RuleSet,
) -> float:
    """Compute probability of making a shot. Returns value in [0.01, 0.99]."""
    scoring = shooter.current_attributes.scoring
    base = logistic(scoring, BASE_MIDPOINTS[shot_type], BASE_STEEPNESS[shot_type])
    contest = compute_contest(defender, shot_type, scheme_modifier)
    iq_mod = compute_iq_modifier(shooter.current_attributes.iq)
    stamina_mod = compute_stamina_modifier(shooter.current_stamina)
    prob = base * contest * iq_mod * stamina_mod
    return max(0.01, min(0.99, prob))


def points_for_shot(shot_type: ShotType, rules: RuleSet) -> int:
    """How many points a made shot is worth under current rules."""
    if shot_type == "three_point":
        return rules.three_point_value
    if shot_type == "at_rim" or shot_type == "mid_range":
        return rules.two_point_value
    return rules.free_throw_value


def resolve_shot(
    shooter: AgentState,
    defender: AgentState,
    shot_type: ShotType,
    scheme_modifier: float,
    rules: RuleSet,
    rng: random.Random,
) -> tuple[bool, int]:
    """Resolve a shot attempt. Returns (made, points)."""
    prob = compute_shot_probability(shooter, defender, shot_type, scheme_modifier, rules)
    made = rng.random() < prob
    pts = points_for_shot(shot_type, rules) if made else 0
    return made, pts
