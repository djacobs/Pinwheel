"""Move system — special abilities that activate during possessions.

8 moves from SIMULATION.md, one per archetype (plus shared ones).
See docs/product/GLOSSARY.md: Move.
"""

from __future__ import annotations

import random
import re

from pinwheel.core.state import HooperState
from pinwheel.models.team import Move

# Sane bounds for structured (governed) move modifiers. Applied at
# application time so a wild AI-emitted magnitude can never dominate a game.
MAX_SHOT_PROBABILITY_DELTA = 0.30
MAX_TURNOVER_DELTA = 0.15
MAX_STAMINA_RESTORE = 0.25
MAX_SHOT_VALUE_BONUS = 3

VALID_MODIFIER_KINDS = frozenset(
    {"shot_probability", "turnover_rate", "stamina", "shot_value"}
)

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

FATES_HAND = Move(
    name="Fate's Hand",
    trigger="any_possession",
    effect="small chance of extraordinary outcome",
    attribute_gate={"fate": 80},
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
    FATES_HAND,
]


def check_gate(move: Move, agent: HooperState) -> bool:
    """Check if agent meets the attribute gate for a move."""
    for attr_name, min_val in move.attribute_gate.items():
        if getattr(agent.hooper.attributes, attr_name, 0) < min_val:
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
        # Heat Check: +15% on the NEXT 3-point attempt (SIMULATION.md) —
        # the boost applies only when the follow-up shot is also a three.
        return last_possession_three and action == "three_point"
    if trigger == "half_court_setup":
        # "initiate" is the micro engine's half-court set-up event (Phase 4);
        # the macro path never passes it, so macro behavior is unchanged.
        return action in ("mid_range", "three_point", "pass", "initiate")
    if trigger == "opponent_iso":
        # "iso" is the micro engine's isolation event (Phase 4) — the real
        # trigger for defensive moves like Lockdown Stance.
        return action in ("iso", "drive", "at_rim", "mid_range")
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


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp value to [lo, hi]."""
    return max(lo, min(hi, value))


def apply_move_modifier(
    move: Move,
    base_probability: float,
    rng: random.Random,
    action: str = "",
) -> float:
    """Apply a move's effect to shot probability. Returns modified probability.

    The 9 archetype moves keep their exact hardcoded behavior (matched by
    name). Any other move — governed or earned — is applied generically via
    its structured modifier fields: a ``shot_probability`` move shifts the
    probability by ``magnitude`` (clamped to +/-0.30), optionally gated to a
    specific shot class via ``applicable_action``. Other modifier kinds
    (turnover_rate, stamina, shot_value) are applied elsewhere in the
    possession flow and leave the probability unchanged here.
    """
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
    if name == "Lockdown Stance":
        # Defensive move — when a lockdown defender activates this move,
        # it reduces the offensive player's shot probability by 12%
        # representing tighter, more disciplined defense on this possession.
        return max(0.01, base_probability - 0.12)
    if name == "Iron Will":
        # Stamina move — negates the shot penalty from fatigue.
        # Flat +8% bonus representing performing through exhaustion.
        return min(0.99, base_probability + 0.08)
    if name == "Fate's Hand":
        # Fate move — extraordinary outcomes. High ceiling, low floor.
        # 30% chance of a big boost (+18%), 70% chance of a small penalty (-5%).
        delta = rng.choices([0.18, -0.05], weights=[30, 70], k=1)[0]
        return max(0.01, min(0.99, base_probability + delta))

    # Generic path: structured modifiers on governed/earned moves.
    if move.modifier_kind == "shot_probability":
        applicable = (move.applicable_action or "any").strip().lower()
        if applicable in ("", "any", "all") or applicable == action:
            delta = _clamp(
                move.magnitude, -MAX_SHOT_PROBABILITY_DELTA, MAX_SHOT_PROBABILITY_DELTA
            )
            return max(0.01, min(0.99, base_probability + delta))
    return base_probability


def passive_turnover_modifier(agent: HooperState) -> float:
    """Sum turnover-rate modifiers from the agent's structured moves.

    Turnover checks happen before action selection (and thus before move
    triggering), so ``turnover_rate`` moves apply passively whenever the
    hooper handles the ball and meets the move's attribute gate. Negative
    magnitudes reduce turnovers. Result is clamped to +/-0.15.
    """
    total = 0.0
    for move in agent.hooper.moves:
        if move.modifier_kind != "turnover_rate":
            continue
        if not check_gate(move, agent):
            continue
        total += _clamp(move.magnitude, -MAX_TURNOVER_DELTA, MAX_TURNOVER_DELTA)
    return _clamp(total, -MAX_TURNOVER_DELTA, MAX_TURNOVER_DELTA)


def apply_move_secondary_effects(move: Move, agent: HooperState) -> int:
    """Apply a triggered move's non-probability structured effects.

    - ``stamina`` moves restore the agent's stamina by ``magnitude``
      (clamped to 0..0.25 per activation).
    - ``shot_value`` moves return bonus points (clamped to +/-3) that the
      possession flow adds to a made shot.

    Returns the shot-value bonus (0 for other kinds).
    """
    if move.modifier_kind == "stamina":
        restore = _clamp(move.magnitude, 0.0, MAX_STAMINA_RESTORE)
        agent.current_stamina = min(1.0, agent.current_stamina + restore)
        return 0
    if move.modifier_kind == "shot_value":
        return int(
            _clamp(round(move.magnitude), -MAX_SHOT_VALUE_BONUS, MAX_SHOT_VALUE_BONUS)
        )
    return 0


# --- Free-text move effect parsing (legacy / mock payloads) ---

_PERCENT_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*%")
_POINTS_RE = re.compile(r"([+-]?\d+)\s*(?:bonus\s+)?(?:point|pt)s?\b")
_REDUCE_WORDS = ("reduc", "lower", "cut", "fewer", "less", "decreas", "drop")

_ACTION_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("mid_range", ("mid-range", "midrange", "mid range", "jumper", "elbow", "skyhook")),
    ("at_rim", ("at-rim", "at rim", "at the rim", "rim", "paint", "layup", "dunk", "drive")),
    ("three_point", ("three", "3-point", "3pt", "3 point", "deep ball", "from deep")),
)


def parse_move_effect_text(text: str) -> dict[str, float | str] | None:
    """Map a free-text move effect into structured modifier fields.

    Handles common patterns emitted by the mock interpreter and legacy AI
    payloads, e.g. ``"+12% mid-range"``, ``"reduces turnovers by 10%"``,
    ``"+20% all shots"``, ``"restores 10% stamina"``, ``"+2 points on made
    shots"``. Returns a dict with ``modifier_kind``, ``magnitude``, and
    optionally ``applicable_action`` — or None when nothing parseable.
    """
    if not text:
        return None
    lowered = text.lower()

    pct_match = _PERCENT_RE.search(lowered)
    pct = float(pct_match.group(1)) / 100.0 if pct_match else None
    reduces = any(w in lowered for w in _REDUCE_WORDS)

    if "turnover" in lowered:
        if pct is None:
            return None
        magnitude = pct
        if reduces and magnitude > 0:
            magnitude = -magnitude
        return {"modifier_kind": "turnover_rate", "magnitude": magnitude}

    # Stamina moves need a restore-ish verb (or a drain reduction) so that
    # incidental mentions ("+20% all shots, ignore stamina modifier") still
    # parse as shot modifiers below.
    mentions_stamina = "stamina" in lowered or "fatigue" in lowered
    restore_words = ("restor", "recover", "regain", "gain", "refill", "replenish")
    if mentions_stamina and (
        any(w in lowered for w in restore_words) or ("drain" in lowered and reduces)
    ):
        if pct is None:
            return None
        return {"modifier_kind": "stamina", "magnitude": abs(pct)}

    if pct is not None:
        magnitude = pct
        if reduces and magnitude > 0:
            magnitude = -magnitude
        result: dict[str, float | str] = {
            "modifier_kind": "shot_probability",
            "magnitude": magnitude,
        }
        for action_name, keywords in _ACTION_KEYWORDS:
            if any(k in lowered for k in keywords):
                result["applicable_action"] = action_name
                break
        else:
            result["applicable_action"] = "any"
        return result

    points_match = _POINTS_RE.search(lowered)
    if points_match and ("worth" in lowered or "bonus" in lowered or "extra" in lowered):
        return {"modifier_kind": "shot_value", "magnitude": float(points_match.group(1))}

    return None
