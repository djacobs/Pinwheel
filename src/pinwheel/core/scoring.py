"""Shot probability and scoring resolution.

Logistic curves for base probability, modified by defense, IQ, stamina, and rules.
See SIMULATION.md "Scoring Resolution".

Phase 1b adds v2 functions that read from ActionDefinition instead of hardcoded
dicts. Both paths produce identical results — the v2 functions are additive and
the originals are untouched.
"""

from __future__ import annotations

import math
import random
from typing import Literal

from pinwheel.core.state import HooperState
from pinwheel.models.game_definition import ActionDefinition
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
    defender: HooperState,
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


def compute_fate_clutch_bonus(
    fate: int,
    score_differential: int,
) -> float:
    """Fate clutch bonus: in close games, high-Fate players get a shot boost.

    Args:
        fate: The shooter's Fate attribute (1-100).
        score_differential: Absolute difference between team scores.

    Returns:
        Additive bonus in [0.0, ~0.064]. A Fate-80 hooper in a game within 5
        points gets +6.4%.
    """
    if score_differential >= 5:
        return 0.0
    return (fate / 100.0) * 0.08


def compute_shot_probability(
    shooter: HooperState,
    defender: HooperState,
    shot_type: ShotType,
    scheme_modifier: float,
    rules: RuleSet,
    score_differential: int = 0,
) -> float:
    """Compute probability of making a shot. Returns value in [0.01, 0.99].

    Args:
        score_differential: Absolute difference between team scores.
            When > 0 and < 5, high-Fate shooters get a clutch bonus.
    """
    scoring = shooter.current_attributes.scoring
    midpoint = BASE_MIDPOINTS[shot_type]

    # three_point_distance shifts the difficulty curve for three-pointers:
    # farther distance = higher midpoint = harder to make.
    # Each foot from default (22.15) shifts midpoint by ~1.5 attribute points.
    if shot_type == "three_point":
        distance_shift = (rules.three_point_distance - 22.15) * 1.5
        midpoint += distance_shift

    base = logistic(scoring, midpoint, BASE_STEEPNESS[shot_type])
    contest = compute_contest(defender, shot_type, scheme_modifier)
    iq_mod = compute_iq_modifier(shooter.current_attributes.iq)
    stamina_mod = compute_stamina_modifier(shooter.current_stamina)
    prob = base * contest * iq_mod * stamina_mod

    # Fate clutch bonus: high-Fate players shine in close games
    fate = shooter.hooper.attributes.fate
    fate_bonus = compute_fate_clutch_bonus(fate, score_differential)
    prob += fate_bonus

    return max(0.01, min(0.99, prob))


def points_for_shot(shot_type: ShotType, rules: RuleSet) -> int:
    """How many points a made shot is worth under current rules."""
    if shot_type == "three_point":
        return rules.three_point_value
    if shot_type == "at_rim" or shot_type == "mid_range":
        return rules.two_point_value
    return rules.free_throw_value


def resolve_shot(
    shooter: HooperState,
    defender: HooperState,
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


# ---------------------------------------------------------------------------
# Phase 1b: Data-driven v2 functions
#
# These read from ActionDefinition instead of hardcoded dicts. They must
# produce EXACTLY the same output as the originals for identical inputs.
# ---------------------------------------------------------------------------


def compute_contest_v2(
    defender: HooperState,
    action_def: ActionDefinition,
    scheme_modifier: float,
) -> float:
    """Defense contest modifier (v2). Returns multiplier in [0.5, 1.0].

    Reads ``requires_opponent`` from the action definition to determine
    whether the shot is contested. Uncontested actions (free throws) return 1.0.
    """
    if not action_def.requires_opponent:
        return 1.0
    defense = defender.current_attributes.defense
    contest = 1.0 - (defense / 200.0) - scheme_modifier
    return max(0.5, min(1.0, contest))


def compute_shot_probability_v2(
    shooter: HooperState,
    defender: HooperState,
    action_def: ActionDefinition,
    scheme_modifier: float,
    rules: RuleSet,
    score_differential: int = 0,
) -> float:
    """Compute probability of making a shot (v2). Returns value in [0.01, 0.99].

    Same logic as ``compute_shot_probability()`` but reads curve parameters
    from the ``ActionDefinition`` instead of ``BASE_MIDPOINTS`` / ``BASE_STEEPNESS``.

    Args:
        shooter: The shooting hooper's current state.
        defender: The defending hooper's current state.
        action_def: Data-driven action definition with curve parameters.
        scheme_modifier: Defensive scheme modifier.
        rules: Current rule set.
        score_differential: Absolute difference between team scores.
    """
    # Read primary attribute value from shooter using the action's primary_attribute
    primary_attr = action_def.primary_attribute
    scoring = getattr(shooter.current_attributes, primary_attr)

    midpoint = action_def.base_midpoint

    # three_point_distance shifts the difficulty curve for three-pointers:
    # farther distance = higher midpoint = harder to make.
    # Each foot from default (22.15) shifts midpoint by ~1.5 attribute points.
    if action_def.name == "three_point":
        distance_shift = (rules.three_point_distance - 22.15) * 1.5
        midpoint += distance_shift

    base = logistic(scoring, midpoint, action_def.base_steepness)
    contest = compute_contest_v2(defender, action_def, scheme_modifier)
    iq_mod = compute_iq_modifier(shooter.current_attributes.iq)
    stamina_mod = compute_stamina_modifier(shooter.current_stamina)
    prob = base * contest * iq_mod * stamina_mod

    # Fate clutch bonus: high-Fate players shine in close games
    fate = shooter.hooper.attributes.fate
    fate_bonus = compute_fate_clutch_bonus(fate, score_differential)
    prob += fate_bonus

    return max(0.01, min(0.99, prob))


def points_for_action(action_def: ActionDefinition, rules: RuleSet) -> int:
    """How many points a successful action is worth under current rules.

    Since ``basketball_actions()`` already bakes RuleSet point values into
    ``points_on_success``, this is a simple accessor. The ``rules`` parameter
    is accepted for interface consistency and future extensibility.
    """
    return action_def.points_on_success


def resolve_shot_v2(
    shooter: HooperState,
    defender: HooperState,
    action_def: ActionDefinition,
    scheme_modifier: float,
    rules: RuleSet,
    rng: random.Random,
) -> tuple[bool, int]:
    """Resolve a shot attempt (v2). Returns (made, points).

    Same as ``resolve_shot()`` but uses ``compute_shot_probability_v2()``
    and ``points_for_action()``.
    """
    prob = compute_shot_probability_v2(
        shooter, defender, action_def, scheme_modifier, rules
    )
    made = rng.random() < prob
    pts = points_for_action(action_def, rules) if made else 0
    return made, pts
