"""Narration layer — turns structured play data into vivid text.

Transforms raw PossessionLog fields (action, result, defender_id, move_activated)
into human-readable descriptions for the arena Elam banner and game detail
play-by-play.

Phase 5: Data-driven narration. When an ``ActionRegistry`` is provided,
narration templates are looked up from the ``ActionDefinition`` instead of
branching on hardcoded action name strings. This means governance-added
actions (e.g. ``half_court_heave``) automatically get their own flavor text.
When no registry is provided, the legacy hardcoded templates are used for
backward compatibility.
"""

from __future__ import annotations

import random

from pinwheel.models.game_definition import ActionDefinition, ActionRegistry

# --- Elam banner (game-winning shot) — legacy templates ---

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

_LEGACY_WINNERS: dict[str, list[str]] = {
    "three_point": _THREE_WINNERS,
    "mid_range": _MID_WINNERS,
    "at_rim": _RIM_WINNERS,
}

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
    registry: ActionRegistry | None = None,
) -> str:
    """Generate a vivid Elam banner description for the game-winning play.

    When ``registry`` is provided, looks up ``narration_winner`` templates
    from the action's ``ActionDefinition``. Falls back to legacy hardcoded
    templates when no registry is provided or the action has no winner
    narration templates.

    Args:
        player: Name of the player who made the winning shot.
        action: Action name (e.g. 'three_point', 'half_court_heave').
        move: Name of the activated move (for flourish suffix).
        seed: RNG seed for deterministic template selection.
        registry: Optional ActionRegistry for data-driven narration.

    Returns:
        A vivid one-line description of the game-winning play.
    """
    rng = random.Random(seed)

    # Try registry-based narration first
    if registry is not None:
        action_def = registry.get(action)
        if action_def is not None and action_def.narration_winner:
            base = rng.choice(action_def.narration_winner).format(player=player)
            if move and move in _MOVE_FLOURISHES:
                base += f" — {_MOVE_FLOURISHES[move]}"
            return base

    # Legacy hardcoded path (backward compat)
    templates = _LEGACY_WINNERS.get(action)
    if templates:
        base = rng.choice(templates).format(player=player)
    else:
        base = f"{player} hits the game-winner"

    if move and move in _MOVE_FLOURISHES:
        base += f" — {_MOVE_FLOURISHES[move]}"

    return base


# --- Play-by-play narration — legacy templates ---

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

_LEGACY_MADE: dict[str, list[str]] = {
    "three_point": _THREE_MADE,
    "mid_range": _MID_MADE,
    "at_rim": _RIM_MADE,
}

_LEGACY_MISSED: dict[str, list[str]] = {
    "three_point": _THREE_MISSED,
    "mid_range": _MID_MISSED,
    "at_rim": _RIM_MISSED,
}

_LEGACY_FOUL_DESC: dict[str, str] = {
    "three_point": "three",
    "mid_range": "jumper",
    "at_rim": "drive",
}

_TURNOVER = [
    "{defender} strips {player} — stolen",
    "{player} loses the handle — {defender} with the steal",
    "{defender} picks {player}'s pocket — turnover",
    "{player} gets careless and {defender} pounces",
]

_TURNOVER_NO_DEFENDER = [
    "{player} loses the handle — turnover",
    "{player} coughs it up — stolen",
    "Loose ball! {player} can't hang on",
    "{player} gets careless — turnover",
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

_OFFENSIVE_REBOUND = [
    "{rebounder} grabs the offensive board",
    "{rebounder} fights for the offensive rebound",
    "{rebounder} with the putback opportunity — offensive rebound",
    "Second chance! {rebounder} snags the offensive rebound",
]

_DEFENSIVE_REBOUND = [
    "{rebounder} pulls down the defensive rebound",
    "{rebounder} clears the glass",
    "{rebounder} grabs the board",
    "{rebounder} corrals the defensive rebound",
]


def _resolve_foul_desc(
    action: str,
    action_def: ActionDefinition | None,
) -> str:
    """Resolve the short foul description for a given action.

    Uses the ActionDefinition's ``narration_foul_desc`` when available,
    falls back to the legacy hardcoded mapping, and ultimately defaults
    to 'shot' for unknown actions.
    """
    if action_def is not None and action_def.narration_foul_desc:
        return action_def.narration_foul_desc
    return _LEGACY_FOUL_DESC.get(action, "shot")


def _narrate_made(
    player: str,
    defender: str,
    action: str,
    rng: random.Random,
    action_def: ActionDefinition | None,
) -> str:
    """Generate narration for a made shot.

    Uses data-driven templates from the ActionDefinition when available,
    falls back to legacy hardcoded templates, and ultimately to generic
    text for unknown actions.
    """
    # Try data-driven templates first
    if action_def is not None and action_def.narration_made:
        return rng.choice(action_def.narration_made).format(
            player=player,
            defender=defender,
        )

    # Legacy hardcoded path
    templates = _LEGACY_MADE.get(action)
    if templates:
        return rng.choice(templates).format(player=player, defender=defender)

    # Generic fallback
    return f"{player} scores"


def _narrate_missed(
    player: str,
    defender: str,
    action: str,
    rng: random.Random,
    action_def: ActionDefinition | None,
) -> str:
    """Generate narration for a missed shot.

    Uses data-driven templates from the ActionDefinition when available,
    falls back to legacy hardcoded templates, and ultimately to generic
    text for unknown actions.
    """
    # Try data-driven templates first
    if action_def is not None and action_def.narration_missed:
        return rng.choice(action_def.narration_missed).format(
            player=player,
            defender=defender,
        )

    # Legacy hardcoded path
    templates = _LEGACY_MISSED.get(action)
    if templates:
        return rng.choice(templates).format(player=player, defender=defender)

    # Generic fallback
    return f"{player} misses"


def narrate_play(
    player: str,
    defender: str,
    action: str,
    result: str,
    points: int,
    move: str = "",
    rebounder: str = "",
    is_offensive_rebound: bool = False,
    score_home: int = 0,
    score_away: int = 0,
    seed: int = 0,
    assist_id: str = "",
    registry: ActionRegistry | None = None,
) -> str:
    """Generate a one-line play-by-play description from structured data.

    When ``registry`` is provided, looks up narration templates from the
    action's ``ActionDefinition`` for made/missed shots and foul descriptions.
    Falls back to legacy hardcoded templates when no registry is provided or
    the action has no narration templates defined.

    When a shot misses and a rebounder is specified, appends a rebound
    narration indicating who grabbed the board (offensive or defensive).

    Args:
        player: Name of the ball handler.
        defender: Name of the primary defender.
        action: Action name (e.g. 'three_point', 'half_court_heave').
        result: Outcome ('made', 'missed', 'foul', 'turnover').
        points: Points scored on this possession.
        move: Name of the activated move (for tag prefix).
        rebounder: Name of the rebounder (if any).
        is_offensive_rebound: Whether the rebound was offensive.
        score_home: Current home score (unused, reserved).
        score_away: Current away score (unused, reserved).
        seed: RNG seed for deterministic template selection.
        assist_id: ID of the assisting player (controls No-Look Pass display).
        registry: Optional ActionRegistry for data-driven narration.

    Returns:
        A vivid one-line play-by-play description.
    """
    rng = random.Random(seed)

    # Look up the ActionDefinition from the registry (may be None)
    action_def: ActionDefinition | None = None
    if registry is not None:
        action_def = registry.get(action)

    if action == "shot_clock_violation":
        text = rng.choice(_SHOT_CLOCK_VIOLATION).format(player=player, defender=defender)
    elif result == "turnover":
        if defender:
            text = rng.choice(_TURNOVER).format(player=player, defender=defender)
        else:
            text = rng.choice(_TURNOVER_NO_DEFENDER).format(player=player)
    elif result == "foul":
        shot_desc = _resolve_foul_desc(action, action_def)
        text = rng.choice(_FOUL).format(player=player, defender=defender, shot_desc=shot_desc)
        if points > 0:
            text += f" — hits {points} from the stripe"
        else:
            text += " — misses from the stripe"
    elif result == "made":
        text = _narrate_made(player, defender, action, rng, action_def)
    else:  # missed
        text = _narrate_missed(player, defender, action, rng, action_def)

    # Append rebound narration on missed shots
    if rebounder and result == "missed":
        if is_offensive_rebound:
            rebound_text = rng.choice(_OFFENSIVE_REBOUND).format(rebounder=rebounder)
        else:
            rebound_text = rng.choice(_DEFENSIVE_REBOUND).format(rebounder=rebounder)
        text += f". {rebound_text}"

    # Only tag No-Look Pass when there's an actual assist (a pass led to a score).
    # Other moves always show when activated.
    if move and move in _MOVE_FLOURISHES:
        if move == "No-Look Pass" and not assist_id:
            pass  # suppress — no pass play happened
        else:
            text = f"[{move}] {text}"

    return text
