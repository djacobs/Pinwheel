"""Narration layer — turns structured play data into vivid text.

Transforms raw PossessionLog fields (action, result, defender_id, move_activated)
into human-readable descriptions for the arena Elam banner and game detail
play-by-play.
"""

from __future__ import annotations

import random

# --- Elam banner (game-winning shot) ---

_THREE_WINNERS = [
    "{player} buries the three from deep — ballgame",
    "{player} pulls up from beyond the arc — nothing but net",
    "{player} drains the dagger three",
    "{player} rises and fires from downtown — it's good",
    "{player} with ice in the veins — three-pointer to win it",
]

_MID_WINNERS = [
    "{player} hits the mid-range dagger from the elbow",
    "{player} pulls up, rises, buries the jumper — game over",
    "{player} with the silky mid-range to seal it",
    "{player} fades away from fifteen feet — nothing but net",
    "{player} stops on a dime, fires — money from mid-range",
]

_RIM_WINNERS = [
    "{player} attacks the rim — finishes through contact",
    "{player} slashes to the basket and lays it in",
    "{player} drives hard and converts at the rim",
    "{player} takes it coast to coast for the game-winner",
    "{player} muscles through the lane — and one!",
]

_MOVE_FLOURISHES = {
    "Heat Check": "riding the hot hand",
    "Ankle Breaker": "leaving the defender on the floor",
    "Clutch Gene": "with the clutch gene activated",
    "Chess Move": "the chess move setting it all up",
    "No-Look Pass": "off the no-look dime",
    "Wild Card": "chaos incarnate",
    "Iron Will": "running on pure will",
    "Lockdown Stance": "after locking down on the other end",
}


def narrate_winner(
    player: str,
    action: str,
    move: str = "",
    seed: int = 0,
) -> str:
    """Generate a vivid Elam banner description for the game-winning play."""
    rng = random.Random(seed)
    if action == "three_point":
        base = rng.choice(_THREE_WINNERS).format(player=player)
    elif action == "mid_range":
        base = rng.choice(_MID_WINNERS).format(player=player)
    elif action == "at_rim":
        base = rng.choice(_RIM_WINNERS).format(player=player)
    else:
        base = f"{player} hits the game-winner"

    if move and move in _MOVE_FLOURISHES:
        base += f" — {_MOVE_FLOURISHES[move]}"

    return base


# --- Play-by-play narration ---

_THREE_MADE = [
    "{player} drains it from three over {defender}",
    "{player} connects from deep — {defender} too late on the close-out",
    "{player} buries the three, {defender} watching",
    "{player} from downtown — splash",
]

_THREE_MISSED = [
    "{player} fires from three — off the rim, {defender} contesting",
    "{player} launches from deep — no good",
    "{player} can't connect from beyond the arc",
]

_MID_MADE = [
    "{player} pulls up from mid-range over {defender} — good",
    "{player} hits the elbow jumper, {defender} a step late",
    "{player} with the smooth mid-range — bucket",
    "{player} stops, pops — money from fifteen feet",
]

_MID_MISSED = [
    "{player} pulls up mid-range — rattles out, {defender} in the face",
    "{player} fires from the elbow — off the iron",
    "{player} can't get the mid-range to drop",
]

_RIM_MADE = [
    "{player} drives on {defender} — finishes at the rim",
    "{player} takes it strong to the hole — converts",
    "{player} slashes past {defender} for the layup",
    "{player} attacks the basket — and it's good",
]

_RIM_MISSED = [
    "{player} drives on {defender} — blocked at the rim",
    "{player} takes it to the hole — can't finish",
    "{player} gets into the lane but {defender} forces the miss",
]

_TURNOVER = [
    "{defender} strips {player} — turnover",
    "{player} coughs it up — {defender} with the steal",
    "Loose ball! {defender} picks {player}'s pocket",
    "{player} gets careless — {defender} pounces",
]

_SHOT_CLOCK_VIOLATION = [
    "{player} can't find a shot — shot clock violation",
    "Shot clock expires on {player} — the defense locked them down",
    "The defense smothers {player} — shot clock turnover",
    "{player} runs out of time — 15 seconds wasn't enough",
]

_FOUL = [
    "{defender} fouls {player} on the {shot_desc} — to the line",
    "Whistle! {defender} catches {player} on the arm",
    "Foul called — {defender} hacks {player} going up",
    "{player} draws the foul on {defender} — heads to the stripe",
]


def narrate_play(
    player: str,
    defender: str,
    action: str,
    result: str,
    points: int,
    move: str = "",
    score_home: int = 0,
    score_away: int = 0,
    seed: int = 0,
) -> str:
    """Generate a one-line play-by-play description from structured data."""
    rng = random.Random(seed)

    if action == "shot_clock_violation":
        text = rng.choice(_SHOT_CLOCK_VIOLATION).format(player=player, defender=defender)
    elif result == "turnover":
        text = rng.choice(_TURNOVER).format(player=player, defender=defender)
    elif result == "foul":
        shot_desc = {"three_point": "three", "mid_range": "jumper", "at_rim": "drive"}.get(
            action, "shot"
        )
        text = rng.choice(_FOUL).format(
            player=player, defender=defender, shot_desc=shot_desc
        )
        if points > 0:
            text += f" — hits {points} from the stripe"
        else:
            text += " — misses from the stripe"
    elif result == "made":
        if action == "three_point":
            text = rng.choice(_THREE_MADE).format(player=player, defender=defender)
        elif action == "mid_range":
            text = rng.choice(_MID_MADE).format(player=player, defender=defender)
        elif action == "at_rim":
            text = rng.choice(_RIM_MADE).format(player=player, defender=defender)
        else:
            text = f"{player} scores"
    else:  # missed
        if action == "three_point":
            text = rng.choice(_THREE_MISSED).format(player=player, defender=defender)
        elif action == "mid_range":
            text = rng.choice(_MID_MISSED).format(player=player, defender=defender)
        elif action == "at_rim":
            text = rng.choice(_RIM_MISSED).format(player=player, defender=defender)
        else:
            text = f"{player} misses"

    if move and move in _MOVE_FLOURISHES:
        text = f"[{move}] {text}"

    return text
