"""Game definition models — data-driven action registry and game structure.

The Abstract Game Spine provides a declarative way to define game actions,
resolution parameters, and structural configuration. Basketball is the
default game definition; governance can modify it at runtime.

Phase 1: ActionDefinition and ActionRegistry for data-driven actions.
Phase 2a: GameDefinition bundles ActionRegistry + game structure config.
          simulate_game() always uses the registry — no more dual-path.
Phase 3a: Turn structure config — quarters, clock, Elam Ending, alternating
          possession. All derived from RuleSet for basketball.
Phase 4a: GameDefinitionPatch — governance can mutate the game definition
          by adding/removing/modifying actions and turn structure.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

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

    # ---- Narration (Phase 5) ----

    narration_made: list[str] = Field(default_factory=list)
    """Templates for successful action narration.

    Each template can include ``{player}`` and ``{defender}`` placeholders.
    Example: ``["{player} drains it from three over {defender}"]``.
    When empty, the narration layer falls back to generic text.
    """

    narration_missed: list[str] = Field(default_factory=list)
    """Templates for failed action narration.

    Each template can include ``{player}`` and ``{defender}`` placeholders.
    Example: ``["{player} fires from three — off the rim"]``.
    When empty, the narration layer falls back to generic text.
    """

    narration_verb: str = ""
    """Short verb for summary text, e.g. 'shoots', 'heaves', 'flips'.

    Used in condensed play-by-play and log summaries.
    """

    narration_display: str = ""
    """How to refer to this action in box scores and logs, e.g. '3PT', 'MID', 'RIM'.

    Used in stat displays and compact game summaries.
    """

    narration_winner: list[str] = Field(default_factory=list)
    """Templates for game-winning shot narration (Elam banner).

    Each template can include a ``{player}`` placeholder.
    Example: ``["{player} buries the three from deep — ballgame"]``.
    When empty, the narration layer falls back to generic text.
    """

    narration_foul_desc: str = ""
    """Short description of the action used in foul narration, e.g. 'three', 'drive'.

    Used in foul templates like '{defender} fouls {player} on the {shot_desc}'.
    When empty, defaults to 'shot'.
    """


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
            a for a in self._actions.values() if a.category != "special" and not a.is_free_throw
        ]

    def action_names(self) -> list[str]:
        """Return a sorted list of all action names."""
        return sorted(self._actions.keys())


class GameDefinition(BaseModel):
    """Complete description of how the game is played.

    Bundles an ActionRegistry with game structure configuration. Basketball
    is one instance; governance can produce modified definitions at runtime.

    The ActionRegistry is NOT serialized — it is rebuilt from the actions
    list on construction. This keeps the Pydantic model serializable while
    the registry provides fast runtime lookup.

    Turn structure fields (Phase 3a) make the quarter/period/Elam structure
    data-driven. For basketball, these are derived from the RuleSet by
    ``basketball_game_definition()``.
    """

    name: str = "Basketball"
    """Human-readable game name."""

    description: str = "3v3 basketball with Elam Ending"
    """Short description of the game variant."""

    actions: list[ActionDefinition] = Field(default_factory=list)
    """All action definitions for this game."""

    participants_per_side: int = 3
    """Number of active participants per team."""

    bench_size: int = 1
    """Number of bench participants per team."""

    # ---- Turn Structure (Phase 3a) ----

    quarters: int = 4
    """Number of periods (quarters) in the game, including the Elam period.

    For basketball with default rules: 4 (Q1, Q2, Q3, Elam).
    The first ``quarters - 1`` periods are clock-based; the last may be
    Elam-style (target score) if ``elam_ending_enabled`` is True.
    """

    quarter_clock_seconds: float = 600.0
    """Duration of each clock-based period in seconds.

    For basketball: ``quarter_minutes * 60``. This is the initial value
    of the game clock for each regular (non-Elam) quarter.
    """

    alternating_possession: bool = True
    """Whether possession alternates between teams each turn.

    True for basketball (and most team games). False for games where
    the same team can act multiple times in a row.
    """

    elam_ending_enabled: bool = True
    """Whether the final period uses Elam Ending (target score) rules.

    When True, the last quarter (``elam_trigger_quarter``) switches
    from clock-based to target-score-based play.
    """

    elam_trigger_quarter: int = 4
    """Which quarter triggers the Elam Ending.

    For basketball: typically the last quarter. Must be >= 1 and
    <= ``quarters``. Derived from ``rules.elam_trigger_quarter + 1``
    because the RuleSet field counts regular quarters before Elam.
    """

    elam_target_margin: int = 15
    """Points added to the leading score to compute the Elam target.

    Derived from ``rules.elam_margin``.
    """

    halftime_after_quarter: int = 2
    """Apply halftime recovery after this quarter number.

    For basketball: halftime after Q2. Set to 0 to disable halftime.
    """

    halftime_recovery: float = 0.40
    """Stamina recovery fraction at halftime.

    Derived from ``rules.halftime_stamina_recovery``.
    """

    quarter_break_recovery: float = 0.15
    """Stamina recovery fraction at non-halftime quarter breaks.

    Derived from ``rules.quarter_break_stamina_recovery``.
    """

    safety_cap_possessions: int = 300
    """Maximum total possessions before forcing game over.

    Derived from ``rules.safety_cap_possessions``.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def build_registry(self) -> ActionRegistry:
        """Build an ActionRegistry from this definition's actions.

        Called at simulation startup. The registry provides O(1) lookup
        by action name — much faster than scanning the list each possession.
        """
        return ActionRegistry(self.actions)


# Type alias for values that can appear in action/structure modification dicts.
# Pydantic fields on ActionDefinition and GameDefinition use these types.
PatchValue = str | int | float | bool | None | dict[str, float] | list[str]


class GameDefinitionPatch(BaseModel):
    """A mutation to apply to a GameDefinition.

    Produced by governance effects when proposals modify the game itself.
    Each patch describes a set of additive, subtractive, or modifying
    operations on the game's actions and turn structure.

    Applying a patch produces a NEW GameDefinition — the original is not
    mutated. Patches are stored in effect ``params`` dicts and serialized
    as JSON via Pydantic's ``model_dump(mode="json")``.
    """

    add_actions: list[ActionDefinition] = Field(default_factory=list)
    """New actions to add to the game. Duplicate names overwrite existing actions."""

    remove_actions: list[str] = Field(default_factory=list)
    """Action names to remove from the game. Missing names are silently ignored."""

    modify_actions: dict[str, dict[str, PatchValue]] = Field(default_factory=dict)
    """Partial updates to existing actions.

    Keys are action names; values are dicts of field names to new values.
    Example: ``{"three_point": {"points_on_success": 4}}`` changes the
    three-pointer to be worth 4 points.
    Only fields present in ActionDefinition are applied; unknown fields
    are silently ignored.
    """

    modify_structure: dict[str, PatchValue] = Field(default_factory=dict)
    """Partial updates to GameDefinition turn structure fields.

    Example: ``{"quarters": 6, "elam_ending_enabled": False}`` changes
    the game to 6 quarters with no Elam Ending.
    Only fields present on GameDefinition are applied; unknown fields
    are silently ignored. The ``actions`` field cannot be modified this
    way — use ``add_actions``, ``remove_actions``, or ``modify_actions``.
    """

    description: str = ""
    """Human-readable description of what this patch does."""

    def apply(self, game_def: GameDefinition) -> GameDefinition:
        """Apply this patch to a GameDefinition, returning a new one.

        Operations are applied in order:
        1. Remove actions (by name)
        2. Modify existing actions (partial field updates)
        3. Add new actions (overwrites if name already exists)
        4. Modify structure fields

        The original GameDefinition is not mutated.

        Args:
            game_def: The base game definition to patch.

        Returns:
            A new GameDefinition with patches applied.
        """
        # Start with a deep copy of the actions list
        actions_by_name: dict[str, ActionDefinition] = {
            a.name: a.model_copy(deep=True) for a in game_def.actions
        }

        # 1. Remove actions
        for name in self.remove_actions:
            actions_by_name.pop(name, None)

        # 2. Modify existing actions
        for name, modifications in self.modify_actions.items():
            existing = actions_by_name.get(name)
            if existing is None:
                continue
            # Build a dict of the current action, apply modifications
            action_data = existing.model_dump()
            valid_fields = set(ActionDefinition.model_fields.keys())
            for field_name, value in modifications.items():
                if field_name in valid_fields:
                    action_data[field_name] = value
            actions_by_name[name] = ActionDefinition(**action_data)

        # 3. Add new actions (overwrites existing with same name)
        for action in self.add_actions:
            actions_by_name[action.name] = action.model_copy(deep=True)

        # 4. Build new structure from base, applying modifications
        structure_data = game_def.model_dump()
        structure_data["actions"] = list(actions_by_name.values())
        valid_structure_fields = set(GameDefinition.model_fields.keys()) - {"actions"}
        for field_name, value in self.modify_structure.items():
            if field_name in valid_structure_fields:
                structure_data[field_name] = value

        return GameDefinition(**structure_data)


# ---------------------------------------------------------------------------
# Example Actions — governance can add these to the game
#
# These are NOT active by default. They serve as examples of what governance
# proposals could add, and are used in integration tests to prove the
# add-action flow works end-to-end.
# ---------------------------------------------------------------------------

EXAMPLE_ACTIONS: dict[str, ActionDefinition] = {
    "half_court_heave": ActionDefinition(
        name="half_court_heave",
        display_name="Half-Court Heave",
        description=(
            "A desperate long-range shot from half court. Almost never goes in, "
            "but when it does, the crowd goes wild."
        ),
        category="shot",
        selection_weight=5.0,
        weight_attributes={},
        resolution_type="attribute_check",
        base_midpoint=80.0,
        base_steepness=0.03,
        primary_attribute="scoring",
        stamina_factor=0.2,
        points_on_success=4,
        requires_opponent=True,
        is_free_throw=False,
        free_throw_attempts_on_foul=3,
        narration_made=[
            "{player} heaves it from half court — BANG! IT GOES!",
            "{player} launches from midcourt — ARE YOU KIDDING ME?!",
            "{player} lets it fly from half court over {defender} — NOTHING BUT NET!",
            "{player} bombs it from the logo — THE CROWD ERUPTS!",
        ],
        narration_missed=[
            "{player} heaves it from half court — not even close",
            "{player} launches from midcourt — off the backboard",
            "{player} flings it from half — airballed",
            "{player} chucks it from the logo — way off",
        ],
        narration_verb="heaves",
        narration_display="HEAVE",
        narration_winner=[
            "{player} heaves it from half court at the buzzer — IT GOES! UNBELIEVABLE!",
            "{player} from the logo — THE HALF-COURT MIRACLE!",
            "{player} launches from midcourt — THE GREATEST SHOT IN LEAGUE HISTORY!",
        ],
        narration_foul_desc="heave",
    ),
    "layup": ActionDefinition(
        name="layup",
        display_name="Layup",
        description=(
            "An easy close-range shot. High probability of going in, "
            "but only worth 2 points. Favored by fast, agile players."
        ),
        category="shot",
        selection_weight=10.0,
        weight_attributes={"speed": 0.4},
        resolution_type="attribute_check",
        base_midpoint=20.0,
        base_steepness=0.06,
        primary_attribute="scoring",
        stamina_factor=0.2,
        points_on_success=2,
        requires_opponent=True,
        is_free_throw=False,
        free_throw_attempts_on_foul=2,
        narration_made=[
            "{player} flips in the layup past {defender}",
            "{player} scoops it up and in — easy two",
            "{player} with the finger roll — good",
            "{player} kisses it off the glass over {defender}",
        ],
        narration_missed=[
            "{player} goes up for the layup — rimmed out",
            "{player} flips it up — {defender} gets a hand on it",
            "{player} can't finish the layup",
            "{player} lays it up and {defender} swats it away",
        ],
        narration_verb="flips",
        narration_display="LAYUP",
        narration_winner=[
            "{player} glides in for the game-winning layup",
            "{player} flips in the layup — that's the ball game!",
            "{player} scoops it in for the win — too easy!",
        ],
        narration_foul_desc="layup",
    ),
}


def basketball_actions(rules: RuleSet) -> list[ActionDefinition]:
    """Build the 4 standard basketball actions from a RuleSet.

    The values here are the single source of truth for basketball shot
    parameters (midpoints, steepness, weights). The scoring module's
    ``BASE_MIDPOINTS`` and ``BASE_STEEPNESS`` dicts are derived from
    these definitions for backward compatibility.

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
            narration_made=[
                "{player} drives on {defender} — finishes at the rim",
                "{player} takes it strong to the hole — converts",
                "{player} slashes past {defender} for the layup",
                "{player} attacks the basket — and it's good",
            ],
            narration_missed=[
                "{player} drives on {defender} — blocked at the rim",
                "{player} takes it to the hole — can't finish",
                "{player} gets into the lane but {defender} forces the miss",
            ],
            narration_verb="drives",
            narration_display="RIM",
            narration_winner=[
                "{player} attacks the rim — finishes through contact",
                "{player} slashes to the basket and lays it in",
                "{player} drives hard and converts at the rim",
                "{player} takes it coast to coast for the game-winner",
                "{player} muscles through the lane — and one!",
            ],
            narration_foul_desc="drive",
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
            narration_made=[
                "{player} pulls up from mid-range over {defender} — good",
                "{player} hits the elbow jumper, {defender} a step late",
                "{player} with the smooth mid-range — bucket",
                "{player} stops, pops — money from fifteen feet",
            ],
            narration_missed=[
                "{player} pulls up mid-range — rattles out, {defender} in the face",
                "{player} fires from the elbow — off the iron",
                "{player} can't get the mid-range to drop",
            ],
            narration_verb="shoots",
            narration_display="MID",
            narration_winner=[
                "{player} hits the mid-range dagger from the elbow",
                "{player} pulls up, rises, buries the jumper — game over",
                "{player} with the silky mid-range to seal it",
                "{player} fades away from fifteen feet — nothing but net",
                "{player} stops on a dime, fires — money from mid-range",
            ],
            narration_foul_desc="jumper",
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
            narration_made=[
                "{player} drains it from three over {defender}",
                "{player} connects from deep — {defender} too late on the close-out",
                "{player} buries the three, {defender} watching",
                "{player} from downtown — splash",
            ],
            narration_missed=[
                "{player} fires from three — off the rim, {defender} contesting",
                "{player} launches from deep — no good",
                "{player} can't connect from beyond the arc",
            ],
            narration_verb="shoots",
            narration_display="3PT",
            narration_winner=[
                "{player} buries the three from deep — ballgame",
                "{player} pulls up from beyond the arc — nothing but net",
                "{player} drains the dagger three",
                "{player} rises and fires from downtown — it's good",
                "{player} with ice in the veins — three-pointer to win it",
            ],
            narration_foul_desc="three",
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
            narration_verb="shoots",
            narration_display="FT",
        ),
    ]


def basketball_game_definition(rules: RuleSet) -> GameDefinition:
    """Build a complete basketball GameDefinition from a RuleSet.

    This is the primary factory for creating a game definition. The returned
    definition bundles all basketball actions with the standard 3v3 structure,
    plus turn structure fields derived from the RuleSet.

    Turn structure mapping:
    - ``quarters``: ``elam_trigger_quarter + 1`` (regular quarters + Elam)
    - ``quarter_clock_seconds``: ``quarter_minutes * 60``
    - ``elam_trigger_quarter``: ``rules.elam_trigger_quarter + 1`` (1-indexed)
    - ``elam_target_margin``: ``rules.elam_margin``
    - ``halftime_recovery``: ``rules.halftime_stamina_recovery``
    - ``quarter_break_recovery``: ``rules.quarter_break_stamina_recovery``
    - ``safety_cap_possessions``: ``rules.safety_cap_possessions``

    Args:
        rules: The current RuleSet, used to derive point values and game parameters.

    Returns:
        A GameDefinition configured for 3v3 basketball.
    """
    # In the RuleSet, elam_trigger_quarter counts regular quarters BEFORE Elam.
    # E.g. elam_trigger_quarter=3 means Q1, Q2, Q3 are regular, Q4 is Elam.
    # So total quarters = elam_trigger_quarter + 1.
    total_quarters = rules.elam_trigger_quarter + 1
    elam_quarter = total_quarters  # Elam is always the last quarter

    return GameDefinition(
        name="Basketball",
        description="3v3 basketball with Elam Ending",
        actions=basketball_actions(rules),
        participants_per_side=3,
        bench_size=1,
        # Turn structure
        quarters=total_quarters,
        quarter_clock_seconds=rules.quarter_minutes * 60.0,
        alternating_possession=True,
        elam_ending_enabled=True,
        elam_trigger_quarter=elam_quarter,
        elam_target_margin=rules.elam_margin,
        halftime_after_quarter=2,
        halftime_recovery=rules.halftime_stamina_recovery,
        quarter_break_recovery=rules.quarter_break_stamina_recovery,
        safety_cap_possessions=rules.safety_cap_possessions,
    )


# ---------------------------------------------------------------------------
# Derived constants for backward compatibility
#
# These dicts are derived from the basketball ActionDefinitions so that
# scoring.py and other modules can import them without change. The source
# of truth is basketball_actions() — these are generated, not hand-maintained.
# ---------------------------------------------------------------------------


def _build_basketball_constants() -> tuple[dict[str, float], dict[str, float]]:
    """Build BASE_MIDPOINTS and BASE_STEEPNESS from basketball_actions.

    Uses DEFAULT_RULESET since midpoints/steepness do not depend on
    point values — only on the action definitions themselves.
    """
    from pinwheel.models.rules import DEFAULT_RULESET as _default

    actions = basketball_actions(_default)
    midpoints = {a.name: a.base_midpoint for a in actions}
    steepness = {a.name: a.base_steepness for a in actions}
    return midpoints, steepness


BASKETBALL_MIDPOINTS, BASKETBALL_STEEPNESS = _build_basketball_constants()
