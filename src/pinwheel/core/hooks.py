"""Hook system for Game Effects.

Day 1: Hooks are in the code but the effects list is empty.
Day 2: Game Effects plug into these hooks.
See docs/plans/2026-02-11-simulation-extensibility-plan.md.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pinwheel.core.state import AgentState, GameState


class HookPoint(Enum):
    """Points in the simulation where effects can fire."""

    PRE_POSSESSION = "pre_possession"
    POST_ACTION_SELECTION = "post_action_selection"
    PRE_SHOT_RESOLUTION = "pre_shot_resolution"
    POST_SHOT_RESOLUTION = "post_shot_resolution"
    PRE_REBOUND = "pre_rebound"
    POST_REBOUND = "post_rebound"
    PRE_FOUL_CHECK = "pre_foul_check"
    POST_FOUL = "post_foul"
    QUARTER_END = "quarter_end"
    ELAM_START = "elam_start"
    GAME_END = "game_end"


class GameEffect(Protocol):
    """Protocol for game effects that modify simulation behavior."""

    def should_fire(
        self, hook: HookPoint, game_state: GameState, agent: AgentState | None
    ) -> bool: ...

    def apply(self, hook: HookPoint, game_state: GameState, agent: AgentState | None) -> None: ...


def fire_hooks(
    hook: HookPoint,
    game_state: GameState,
    effects: list[GameEffect],
    agent: AgentState | None = None,
) -> None:
    """Fire all effects registered for this hook point."""
    for effect in effects:
        if effect.should_fire(hook, game_state, agent):
            effect.apply(hook, game_state, agent)
