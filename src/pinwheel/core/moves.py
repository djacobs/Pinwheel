"""Move system — special abilities that activate during possessions.

8 moves from SIMULATION.md, one per archetype (plus shared ones).
See docs/GLOSSARY.md: Move.
"""

from __future__ import annotations

import random

from pinwheel.core.state import HooperState
from pinwheel.models.team import Move

# --- Move definitions ---

HEAT_CHECK = Move(
    name="Heat Check",
    trigger="made_three_last_possession",
    effect="+15% three-point, ignore IQ modifier",
    attribute_gate={"ego": 30},
    source="archetype",
)

NO_LOOK_PASS = Move(
    name="No-Look Pass",
    trigger="half_court_setup",
    effect="assist window doubled, +10% teammate shot",
    attribute_gate={"passing": 60, "iq": 50},
    source="archetype",
)

LOCKDOWN_STANCE = Move(
    name="Lockdown Stance",
    trigger="opponent_iso",
    effect="+20% contest, -5% own stamina",
    attribute_gate={"defense": 70},
    source="archetype",
)

ANKLE_BREAKER = Move(
    name="Ankle Breaker",
    trigger="drive_action",
    effect="+15% at-rim, chance to force defender stumble",
    attribute_gate={"speed": 70},
    source="archetype",
)

IRON_WILL = Move(
    name="Iron Will",
    trigger="stamina_below_40",
    effect="stamina floor at 0.35, reduce degradation by 50%",
    attribute_gate={"stamina": 70},
    source="archetype",
)

CHESS_MOVE = Move(
    name="Chess Move",
    trigger="half_court_setup",
    effect="force optimal play selection, +10% all teammate shots this possession",
    attribute_gate={"iq": 75},
    source="archetype",
)

CLUTCH_GENE = Move(
    name="Clutch Gene",
    trigger="elam_period",
    effect="+20% all shots, ignore stamina modifier",
    attribute_gate={"ego": 50},
    source="archetype",
)

WILD_CARD = Move(
    name="Wild Card",
    trigger="any_possession",
    effect="random: +25% or -15% shot probability",
    attribute_gate={"chaotic_alignment": 70},
    source="archetype",
)

ALL_MOVES = [
    HEAT_CHECK,
    NO_LOOK_PASS,
    LOCKDOWN_STANCE,
    ANKLE_BREAKER,
    IRON_WILL,
    CHESS_MOVE,
    CLUTCH_GENE,
    WILD_CARD,
]


def check_gate(move: Move, agent: HooperState) -> bool:
    """Check if agent meets the attribute gate for a move."""
    attrs = agent.hooper.attributes.model_dump()
    for attr_name, min_val in move.attribute_gate.items():
        if attrs.get(attr_name, 0) < min_val:
            return False
    return True


def check_trigger(
    move: Move,
    agent: HooperState,
    action: str,
    last_possession_three: bool,
    is_elam: bool,
) -> bool:
    """Check if a move's trigger condition is met."""
    trigger = move.trigger
    if trigger == "made_three_last_possession":
        return last_possession_three
    if trigger == "half_court_setup":
        return action in ("mid_range", "three_point", "pass")
    if trigger == "opponent_iso":
        return action in ("drive", "at_rim", "mid_range")
    if trigger == "drive_action":
        return action in ("drive", "at_rim")
    if trigger == "stamina_below_40":
        return agent.current_stamina < 0.4
    if trigger == "elam_period":
        return is_elam
    return trigger == "any_possession"


def get_triggered_moves(
    agent: HooperState,
    action: str,
    last_possession_three: bool,
    is_elam: bool,
    rng: random.Random,
) -> list[Move]:
    """Return all moves that trigger for this agent in this situation."""
    triggered = []
    for move in agent.hooper.moves:
        if not check_gate(move, agent):
            continue
        if not check_trigger(move, agent, action, last_possession_three, is_elam):
            continue
        # Moves activate with 70% probability when conditions are met
        if rng.random() < 0.7:
            triggered.append(move)
    return triggered


def apply_move_modifier(
    move: Move,
    base_probability: float,
    rng: random.Random,
) -> float:
    """Apply a move's effect to shot probability. Returns modified probability."""
    name = move.name
    if name == "Heat Check":
        return min(0.99, base_probability + 0.15)
    if name == "Ankle Breaker":
        return min(0.99, base_probability + 0.15)
    if name == "Clutch Gene":
        return min(0.99, base_probability + 0.20)
    if name == "Chess Move":
        return min(0.99, base_probability + 0.10)
    if name == "No-Look Pass":
        return min(0.99, base_probability + 0.10)
    if name == "Wild Card":
        delta = rng.choice([0.25, -0.15])
        return max(0.01, min(0.99, base_probability + delta))
    return base_probability
