"""Defensive model — scheme selection, matchup assignment, and modifiers.

4 schemes: man-tight, man-switch, zone, press.
See SIMULATION.md "Defensive Model".
"""

from __future__ import annotations

import random
from typing import Literal

from pinwheel.core.state import GameState, HooperState
from pinwheel.models.rules import RuleSet
from pinwheel.models.team import TeamStrategy

DefensiveScheme = Literal["man_tight", "man_switch", "zone", "press"]

# Scheme modifiers: additional contest modifier applied to shot probability
SCHEME_CONTEST_MODIFIER: dict[DefensiveScheme, float] = {
    "man_tight": 0.08,
    "man_switch": 0.05,
    "zone": 0.03,
    "press": 0.06,
}

# Scheme stamina costs per possession for defenders
SCHEME_STAMINA_COST: dict[DefensiveScheme, float] = {
    "man_tight": 0.025,
    "man_switch": 0.015,
    "zone": 0.010,
    "press": 0.030,
}

# Scheme turnover bonus (additive probability of forcing a turnover)
SCHEME_TURNOVER_BONUS: dict[DefensiveScheme, float] = {
    "man_tight": 0.02,
    "man_switch": 0.01,
    "zone": 0.005,
    "press": 0.04,
}


def select_scheme(
    offense: list[HooperState],
    defense: list[HooperState],
    game_state: GameState,
    rules: RuleSet,
    rng: random.Random,
    strategy: TeamStrategy | None = None,
) -> DefensiveScheme:
    """Select defensive scheme based on team attributes, game state, and strategy.

    When a TeamStrategy is provided, ``defensive_intensity`` nudges scheme weights:
    high intensity (> 0.2) favours man-tight and press (aggressive schemes),
    while low intensity (< -0.1) favours zone (passive/conserve energy).
    """
    if not defense:
        return "zone"

    avg_def_iq = sum(d.current_attributes.iq for d in defense) / len(defense)
    avg_def_stamina = sum(d.current_stamina for d in defense) / len(defense)
    avg_off_speed = sum(o.current_attributes.speed for o in offense) / len(offense)

    # Score-based tendencies
    score_diff = game_state.score_diff
    if not game_state.home_has_ball:
        score_diff = -score_diff  # from defense's perspective

    weights: dict[DefensiveScheme, float] = {
        "man_tight": 1.0,
        "man_switch": 1.0,
        "zone": 1.0,
        "press": 0.5,
    }

    # High IQ teams favor man-tight
    if avg_def_iq > 55:
        weights["man_tight"] += 0.5
    # Low stamina → zone (conserve energy)
    if avg_def_stamina < 0.5:
        weights["zone"] += 1.5
        weights["man_tight"] -= 0.5
        weights["press"] -= 0.3
    # Trailing → press for turnovers
    if score_diff < -5:
        weights["press"] += 1.0
    # Leading → zone to conserve
    if score_diff > 5:
        weights["zone"] += 0.5
    # Fast offense → switch to avoid getting beat
    if avg_off_speed > 55:
        weights["man_switch"] += 0.5
        weights["man_tight"] -= 0.3

    # Elam: trailing team gets more aggressive
    if game_state.elam_activated and score_diff < 0:
        weights["press"] += 1.5
        weights["man_tight"] += 0.5

    # Strategy influence: defensive_intensity biases scheme selection
    if strategy:
        if strategy.defensive_intensity > 0.2:
            weights["man_tight"] += 1.0
            weights["press"] += 0.5
        elif strategy.defensive_intensity < -0.1:
            weights["zone"] += 1.0

    # Normalize and pick
    schemes = list(weights.keys())
    w = [max(0.1, weights[s]) for s in schemes]
    return rng.choices(schemes, weights=w, k=1)[0]


def assign_matchups(
    offense: list[HooperState],
    defense: list[HooperState],
    scheme: DefensiveScheme,
    rng: random.Random,
) -> dict[str, str]:
    """Assign defensive matchups. Returns {defender_id: attacker_id}.

    Man schemes: match by role (best defender on best scorer).
    Zone: distributed assignment.
    """
    if not offense or not defense:
        return {}

    if scheme in ("man_tight", "man_switch"):
        # Sort offense by scoring (desc), defense by defense (desc)
        off_sorted = sorted(offense, key=lambda a: a.current_attributes.scoring, reverse=True)
        def_sorted = sorted(defense, key=lambda a: a.current_attributes.defense, reverse=True)
        matchups = {}
        for i, d in enumerate(def_sorted):
            opp = off_sorted[i % len(off_sorted)]
            matchups[d.hooper.id] = opp.hooper.id
        return matchups

    if scheme == "zone":
        # Zone: each defender covers a zone, roughly rotational assignment
        matchups = {}
        for i, d in enumerate(defense):
            matchups[d.hooper.id] = offense[i % len(offense)].hooper.id
        return matchups

    # Press: match by speed (fastest defender on fastest ball handler)
    off_sorted = sorted(offense, key=lambda a: a.current_attributes.speed, reverse=True)
    def_sorted = sorted(defense, key=lambda a: a.current_attributes.speed, reverse=True)
    matchups = {}
    for i, d in enumerate(def_sorted):
        matchups[d.hooper.id] = off_sorted[i % len(off_sorted)].hooper.id
    return matchups


def get_primary_defender(
    ball_handler: HooperState,
    matchups: dict[str, str],
    defense: list[HooperState],
) -> HooperState:
    """Find the defender assigned to guard the ball handler."""
    for def_id, off_id in matchups.items():
        if off_id == ball_handler.hooper.id:
            for d in defense:
                if d.hooper.id == def_id:
                    return d
    # Fallback: first available defender
    return defense[0] if defense else ball_handler
