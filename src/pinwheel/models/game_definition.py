"""Game definition models — data-driven action registry.

Phase 1a of the Abstract Game Spine: ActionDefinition and ActionRegistry
provide a declarative way to define the actions participants can take during
gameplay. The basketball_actions() factory produces ActionDefinitions that
exactly reproduce the hardcoded constants in scoring.py and possession.py.

This module is PURELY ADDITIVE — no existing code references it yet.
Future phases will wire the simulation engine to read from the registry
instead of hardcoded constants.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from pinwheel.models.rules import RuleSet


class ActionDefinition(BaseModel):
    """An action a participant can take during their turn.

    Encapsulates everything the simulation engine needs to select and resolve
    an action: selection weights, probability curve parameters, point values,
    and attribute mappings.
    """

    name: str
    """Unique identifier, e.g. 'at_rim', 'mid_range', 'three_point'."""

    display_name: str = ""
    """Human-readable name, e.g. 'At-Rim Shot'."""

    description: str = ""
    """Flavor text or mechanical description."""

    category: str = "shot"
    """Grouping category, e.g. 'shot', 'special'."""

    selection_weight: float = 1.0
    """Base weight for action selection (before attribute contributions)."""

    weight_attributes: dict[str, float] = Field(default_factory=dict)
    """Attribute contribution to selection weight, e.g. {'speed': 0.3}.

    The final selection weight is: selection_weight + sum(attr_value * factor
    for attr, factor in weight_attributes.items()).
    """

    resolution_type: str = "attribute_check"
    """How the action is resolved. Currently only 'attribute_check' (logistic curve)."""

    base_midpoint: float = 50.0
    """Logistic curve midpoint — the attribute value where P(success) = 0.5."""

    base_steepness: float = 0.05
    """Logistic curve steepness — how sharply probability changes near the midpoint."""

    primary_attribute: str = "scoring"
    """Main attribute used for resolution probability."""

    stamina_factor: float = 0.3
    """How much stamina affects resolution probability."""

    modifier_attributes: dict[str, float] = Field(default_factory=dict)
    """Secondary attribute contributions to resolution, e.g. {'iq': 0.1}."""

    points_on_success: int = 2
    """Points awarded when the action succeeds."""

    requires_opponent: bool = True
    """Whether an opponent is needed for resolution (False for free throws)."""

    stamina_cost: float = 0.0
    """Extra stamina cost for performing this action."""

    is_free_throw: bool = False
    """Whether this action is a free throw (special handling in foul resolution)."""

    free_throw_attempts_on_foul: int = 2
    """Number of free throw attempts awarded when fouled during this action."""


class ActionRegistry:
    """Container for ActionDefinitions with lookup by name.

    Not a Pydantic model — this is a runtime container that provides
    dictionary-style access to a set of ActionDefinitions.
    """

    def __init__(self, actions: list[ActionDefinition]) -> None:
        """Initialize the registry from a list of action definitions.

        If duplicate names are provided, the last definition wins.
        """
        self._actions: dict[str, ActionDefinition] = {a.name: a for a in actions}

    def get(self, name: str) -> ActionDefinition | None:
        """Look up an action by name, returning None if not found."""
        return self._actions.get(name)

    def __getitem__(self, name: str) -> ActionDefinition:
        """Look up an action by name, raising KeyError if not found."""
        return self._actions[name]

    def __contains__(self, name: str) -> bool:
        """Check whether an action name exists in the registry."""
        return name in self._actions

    def __len__(self) -> int:
        """Return the number of actions in the registry."""
        return len(self._actions)

    def all_actions(self) -> list[ActionDefinition]:
        """Return all action definitions."""
        return list(self._actions.values())

    def shot_actions(self) -> list[ActionDefinition]:
        """Return non-free-throw shot actions.

        Excludes actions where category == 'special' or is_free_throw is True.
        """
        return [
            a
            for a in self._actions.values()
            if a.category != "special" and not a.is_free_throw
        ]

    def action_names(self) -> list[str]:
        """Return a sorted list of all action names."""
        return sorted(self._actions.keys())


def basketball_actions(rules: RuleSet) -> list[ActionDefinition]:
    """Build the 4 standard basketball actions from a RuleSet.

    The values exactly reproduce the hardcoded constants in scoring.py
    (BASE_MIDPOINTS, BASE_STEEPNESS) and possession.py (select_action weights).

    Args:
        rules: The current RuleSet, used to derive point values.

    Returns:
        A list of 4 ActionDefinitions: at_rim, mid_range, three_point, free_throw.
    """
    return [
        ActionDefinition(
            name="at_rim",
            display_name="At-Rim Shot",
            description="Close-range shot near the basket. Favored by fast players.",
            category="shot",
            selection_weight=30.0,
            weight_attributes={"speed": 0.3},
            resolution_type="attribute_check",
            base_midpoint=30.0,
            base_steepness=0.05,
            primary_attribute="scoring",
            stamina_factor=0.3,
            points_on_success=rules.two_point_value,
            requires_opponent=True,
            is_free_throw=False,
            free_throw_attempts_on_foul=2,
        ),
        ActionDefinition(
            name="mid_range",
            display_name="Mid-Range Shot",
            description="Shot from mid-range distance. Favored by high-IQ players.",
            category="shot",
            selection_weight=25.0,
            weight_attributes={"iq": 0.2},
            resolution_type="attribute_check",
            base_midpoint=40.0,
            base_steepness=0.045,
            primary_attribute="scoring",
            stamina_factor=0.3,
            points_on_success=rules.two_point_value,
            requires_opponent=True,
            is_free_throw=False,
            free_throw_attempts_on_foul=2,
        ),
        ActionDefinition(
            name="three_point",
            display_name="Three-Point Shot",
            description="Long-range shot from beyond the arc. Favored by elite scorers.",
            category="shot",
            selection_weight=20.0,
            weight_attributes={"scoring": 0.3},
            resolution_type="attribute_check",
            base_midpoint=50.0,
            base_steepness=0.04,
            primary_attribute="scoring",
            stamina_factor=0.3,
            points_on_success=rules.three_point_value,
            requires_opponent=True,
            is_free_throw=False,
            free_throw_attempts_on_foul=3,
        ),
        ActionDefinition(
            name="free_throw",
            display_name="Free Throw",
            description="Uncontested shot from the free throw line.",
            category="special",
            selection_weight=1.0,
            weight_attributes={},
            resolution_type="attribute_check",
            base_midpoint=25.0,
            base_steepness=0.06,
            primary_attribute="scoring",
            stamina_factor=0.3,
            points_on_success=rules.free_throw_value,
            requires_opponent=False,
            is_free_throw=True,
            free_throw_attempts_on_foul=2,
        ),
    ]
