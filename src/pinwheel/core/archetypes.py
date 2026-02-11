"""9 archetype templates from SIMULATION.md.

Each archetype defines a base attribute distribution (360-point budget)
and signature moves. Variance is applied at seeding time.
"""

from __future__ import annotations

from pinwheel.models.team import Move, PlayerAttributes

# --- Attribute templates (360 total each) ---

ARCHETYPES: dict[str, PlayerAttributes] = {
    "sharpshooter": PlayerAttributes(
        scoring=80,
        passing=40,
        defense=25,
        speed=35,
        stamina=30,
        iq=55,
        ego=35,
        chaotic_alignment=25,
        fate=35,
    ),
    "floor_general": PlayerAttributes(
        scoring=40,
        passing=80,
        defense=30,
        speed=40,
        stamina=35,
        iq=70,
        ego=25,
        chaotic_alignment=15,
        fate=25,
    ),
    "lockdown": PlayerAttributes(
        scoring=25,
        passing=30,
        defense=85,
        speed=50,
        stamina=50,
        iq=45,
        ego=20,
        chaotic_alignment=20,
        fate=35,
    ),
    "slasher": PlayerAttributes(
        scoring=55,
        passing=35,
        defense=30,
        speed=80,
        stamina=40,
        iq=35,
        ego=40,
        chaotic_alignment=25,
        fate=20,
    ),
    "iron_horse": PlayerAttributes(
        scoring=35,
        passing=35,
        defense=45,
        speed=40,
        stamina=85,
        iq=40,
        ego=25,
        chaotic_alignment=20,
        fate=35,
    ),
    "savant": PlayerAttributes(
        scoring=45,
        passing=55,
        defense=30,
        speed=30,
        stamina=30,
        iq=85,
        ego=20,
        chaotic_alignment=25,
        fate=40,
    ),
    "the_closer": PlayerAttributes(
        scoring=60,
        passing=30,
        defense=35,
        speed=45,
        stamina=35,
        iq=45,
        ego=65,
        chaotic_alignment=15,
        fate=30,
    ),
    "wildcard": PlayerAttributes(
        scoring=40,
        passing=35,
        defense=30,
        speed=45,
        stamina=30,
        iq=25,
        ego=45,
        chaotic_alignment=85,
        fate=25,
    ),
    "oracle": PlayerAttributes(
        scoring=30,
        passing=40,
        defense=35,
        speed=30,
        stamina=35,
        iq=50,
        ego=20,
        chaotic_alignment=30,
        fate=90,
    ),
}

# --- Signature moves per archetype ---

ARCHETYPE_MOVES: dict[str, list[Move]] = {
    "sharpshooter": [
        Move(
            name="Heat Check",
            trigger="made_three_last_possession",
            effect="+15% three-point, ignore IQ modifier",
            attribute_gate={"ego": 30},
        ),
    ],
    "floor_general": [
        Move(
            name="No-Look Pass",
            trigger="half_court_setup",
            effect="assist window doubled, +10% teammate shot",
            attribute_gate={"passing": 60, "iq": 50},
        ),
    ],
    "lockdown": [
        Move(
            name="Lockdown Stance",
            trigger="opponent_iso",
            effect="+20% contest, -5% own stamina",
            attribute_gate={"defense": 70},
        ),
    ],
    "slasher": [
        Move(
            name="Ankle Breaker",
            trigger="drive_action",
            effect="+15% at-rim, chance to force defender stumble",
            attribute_gate={"speed": 70},
        ),
    ],
    "iron_horse": [
        Move(
            name="Iron Will",
            trigger="stamina_below_40",
            effect="stamina floor at 0.35, reduce degradation by 50%",
            attribute_gate={"stamina": 70},
        ),
    ],
    "savant": [
        Move(
            name="Chess Move",
            trigger="half_court_setup",
            effect="force optimal play selection, +10% all teammate shots",
            attribute_gate={"iq": 75},
        ),
    ],
    "the_closer": [
        Move(
            name="Clutch Gene",
            trigger="elam_period",
            effect="+20% all shots, ignore stamina modifier",
            attribute_gate={"ego": 50},
        ),
    ],
    "wildcard": [
        Move(
            name="Wild Card",
            trigger="any_possession",
            effect="random: +25% or -15% shot probability",
            attribute_gate={"chaotic_alignment": 70},
        ),
    ],
    "oracle": [
        Move(
            name="Fate's Hand",
            trigger="any_possession",
            effect="small chance of extraordinary outcome",
            attribute_gate={"fate": 80},
        ),
    ],
}


def apply_variance(
    base: PlayerAttributes,
    rng_seed: int,
    variance: int = 10,
) -> PlayerAttributes:
    """Apply random variance to base archetype attributes."""
    import random

    rng = random.Random(rng_seed)
    data = base.model_dump()
    for key in data:
        delta = rng.randint(-variance, variance)
        data[key] = max(1, min(100, data[key] + delta))
    return PlayerAttributes(**data)
