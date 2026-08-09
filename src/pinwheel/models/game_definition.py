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

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    """Grouping category.

    Vocabulary (Phase 3 + Tier-3): ``shot`` | ``pass`` | ``dribble`` |
    ``screen`` | ``cut`` | ``defense`` | ``rebound`` | ``turnover`` |
    ``violation`` | ``foul`` | ``admin`` | ``special``.

    Only ``category == "shot"`` actions participate in shot selection
    (see :meth:`ActionRegistry.shot_actions`) — chain nodes from other
    categories can NEVER leak into shot selection.
    """

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

    # ---- Micro event-chain fields (Phase 3) ----
    #
    # All optional/defaulted: every stored GameDefinitionPatch keeps
    # validating, and macro-engine games ignore these entirely.

    transitions: dict[str, float] = Field(default_factory=dict)
    """Markov next-event weights for the micro possession engine.

    Keys are action names (chain nodes) plus the pseudo-target ``"shot"``,
    which delegates to shot-class selection among ``category == "shot"``
    actions via the standard selection path (same ``action_biases`` keys).
    Empty for actions that are terminal or macro-only.
    """

    success_transitions: dict[str, float] = Field(default_factory=dict)
    """Optional override of ``transitions`` used when this action succeeds."""

    failure_transitions: dict[str, float] = Field(default_factory=dict)
    """Optional override of ``transitions`` used when this action fails."""

    time_cost_seconds: float = 0.0
    """Shot-clock time consumed by this event in the micro engine.

    A small jitter is applied at runtime. 0.0 means the engine's default
    per-event cost is used.
    """

    zone_requirement: str = ""
    """Court zone required for this action to be a candidate.

    One of ``paint`` | ``mid`` | ``perimeter`` | ``backcourt``; empty means
    the action is available from any zone.
    """

    emits_tags: list[str] = Field(default_factory=list)
    """Tags appended to the GameEvent emitted when this action resolves."""


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

        Only ``category == "shot"`` actions (excluding free throws) are
        eligible for shot selection. This filter is the load-bearing wall
        of the micro event engine: chain nodes (pass/dribble/turnover/
        violation/admin/...) must NEVER leak into shot selection.
        """
        return [
            a for a in self._actions.values() if a.category == "shot" and not a.is_free_throw
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

    event_detail: bool = True
    """Whether the possession engine runs Tier-1 event enrichment.

    When True (default), possessions emit granular ``GameEvent`` chains and
    the enrichment mechanics (shot subtypes, turnover attribution, violations,
    block conversion, assist-before-shot, and-ones, box-outs) are active —
    these consume additional RNG draws. When False, the engine reproduces the
    exact pre-enrichment RNG stream: tests asserting exact seeded lines can
    set this to pin legacy outcomes. Because that promise is only satisfiable
    by the macro path, ``event_detail=False`` forces the legacy macro engine
    even when ``possession_engine`` is ``"micro"``.
    """

    # ---- Tier-3 structure options (Phase 5) — all defaulted OFF ----
    #
    # None of these draw RNG or change behavior at their defaults; they are
    # governable surface activated by a GameDefinitionPatch.

    check_ball_restarts: bool = False
    """FIBA-3x3-style check-ball restarts (Tier-3, default off).

    When True, every dead-ball possession opens with an ``admin.check_ball``
    event (the ball is checked at the top before play begins), costing a
    little clock. Transition possessions skip the check — the break is live.
    """

    clear_arc_required: bool = False
    """FIBA-3x3-style clear-arc rule (Tier-3, default off).

    When True, a possession opened by a live-ball turnover or defensive
    rebound must first clear the ball to the perimeter (an
    ``admin.clear_arc`` zone-transition event). Failing the clearance is a
    ``violation.clear_arc`` — a dead-ball turnover.
    """

    target_score: int = 0
    """First-to-N target score (Tier-3, default 0 = disabled).

    A generalization of the Elam Ending: when > 0, the game ends the moment
    either team reaches this score, in any period ("first to 21 wins").
    Mutually exclusive with ``elam_ending_enabled`` — the patch validator
    rejects definitions with both active. If neither team reaches the
    target, the game ends normally after the final quarter.
    """

    possession_engine: Literal["macro", "micro"] = "micro"
    """Which possession engine resolves turns.

    ``"micro"`` (default since Phase 4) runs the event-chain engine in
    ``core/possession_micro.py``: possessions become chains of pass/
    dribble/screen/cut/defense/shot/turnover events driven by
    ActionDefinition transitions — the whole event graph is governable.
    ``"macro"`` is the original single-shot possession model, still fully
    functional and reachable via a game-definition patch
    (``modify_structure: {"possession_engine": "macro"}``) or explicit
    construction. Note: ``event_detail=False`` always pins the legacy
    macro stream regardless of this flag (see ``event_detail``).
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

    modify_transitions: dict[str, dict[str, float]] = Field(default_factory=dict)
    """Reweight the Markov edges of the possession chain (Tier-3).

    ``{"node_name": {"target": weight}}`` merges into the node's outgoing
    edge tables: ``transitions`` always, plus ``success_transitions`` /
    ``failure_transitions`` when the node defines them — so the reweight
    takes effect regardless of which table the engine consults. A weight
    of ``0.0`` removes the edge (zero-weight edges are never selected).
    Weights must be non-negative; unknown node names are silently ignored.

    Example — activate backcourt violations off the initiate node:
    ``{"initiate": {"violation_backcourt": 0.8}}``.
    """

    @field_validator("modify_transitions")
    @classmethod
    def _transitions_non_negative(
        cls, v: dict[str, dict[str, float]]
    ) -> dict[str, dict[str, float]]:
        """Transition weights must be non-negative finite floats."""
        for node_name, edges in v.items():
            for target, weight in edges.items():
                if not isinstance(weight, (int, float)) or weight != weight:
                    raise ValueError(
                        f"modify_transitions[{node_name!r}][{target!r}] "
                        f"is not a number: {weight!r}"
                    )
                if weight < 0:
                    raise ValueError(
                        f"modify_transitions[{node_name!r}][{target!r}] "
                        f"is negative: {weight}"
                    )
        return v

    description: str = ""
    """Human-readable description of what this patch does."""

    def apply(self, game_def: GameDefinition) -> GameDefinition:
        """Apply this patch to a GameDefinition, returning a new one.

        Operations are applied in order:
        1. Remove actions (by name)
        2. Modify existing actions (partial field updates)
        3. Add new actions (overwrites if name already exists)
        4. Reweight chain transitions (modify_transitions merge)
        5. Modify structure fields

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

        # 4. Reweight chain transitions. The merge lands on every edge
        # table the node defines so the reweight is authoritative no
        # matter which table the engine consults at runtime.
        for name, edges in self.modify_transitions.items():
            existing = actions_by_name.get(name)
            if existing is None:
                continue
            updates: dict[str, dict[str, float]] = {
                "transitions": {**existing.transitions, **edges},
            }
            if existing.success_transitions:
                updates["success_transitions"] = {
                    **existing.success_transitions, **edges,
                }
            if existing.failure_transitions:
                updates["failure_transitions"] = {
                    **existing.failure_transitions, **edges,
                }
            actions_by_name[name] = existing.model_copy(update=updates)

        # 5. Build new structure from base, applying modifications
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


def basketball_micro_chain_nodes() -> list[ActionDefinition]:
    """Build the micro-engine chain nodes (Phase 3 + Tier-2 in Phase 4).

    These are the non-shot nodes of the possession event chain: an
    initiate node, pass/dribble/screen/cut/defense nodes, terminal
    turnover nodes, and a dead-ball violation node. Shot classes are NOT
    duplicated here — the ``"shot"`` pseudo-target in ``transitions``
    delegates to the standard shot-selection path over
    ``category == "shot"`` actions, so governance ``action_biases`` and
    custom shot actions keep working unchanged.

    Everything here is governable data: "picks are illegal" is
    ``remove_actions: ["screen_on_ball", "screen_off_ball"]``; reshaping
    ball movement is a ``modify_actions`` patch on ``transitions``.

    Weights are calibrated so aggregate micro distributions (PPG, FG% by
    shot class, turnover rate, ORB rate, foul rate) stay within the
    regression bands against the macro engine
    (see tests/test_possession_micro.py).
    """
    return [
        ActionDefinition(
            name="initiate",
            display_name="Initiate Offense",
            description="Bring the ball up and start the half-court set.",
            category="admin",
            selection_weight=0.0,
            resolution_type="automatic",
            time_cost_seconds=3.4,
            transitions={
                "pass_swing": 28.0,
                "pass_entry": 5.0,
                "drive": 14.0,
                "iso": 5.0,
                "crossover": 5.0,
                "screen_on_ball": 8.0,
                "screen_off_ball": 5.0,
                "steal_attempt": 1.2,
                "shot": 21.0,
                "violation_deadball": 1.2,
                # Tier-3 governable surface — zero weight by default, so
                # these edges are never selected (and draw no RNG) until a
                # GameDefinitionPatch.modify_transitions or a
                # transition_biases effect activates them.
                "violation_backcourt": 0.0,
                "violation_three_second": 0.0,
                "violation_lane": 0.0,
                "violation_kicked_ball": 0.0,
                "foul_take": 0.0,
                "foul_clear_path": 0.0,
                "foul_away_from_play": 0.0,
            },
        ),
        ActionDefinition(
            name="pass_swing",
            display_name="Swing Pass",
            description="Move the ball to a teammate on the perimeter.",
            category="pass",
            selection_weight=0.0,
            resolution_type="contested_check",
            primary_attribute="passing",
            base_midpoint=0.0,
            base_steepness=0.05,
            time_cost_seconds=3.0,
            success_transitions={
                "pass_swing": 17.0,
                "pass_skip": 5.0,
                "pass_extra": 5.0,
                "drive": 10.0,
                "iso": 3.0,
                "crossover": 4.0,
                "screen_on_ball": 6.0,
                "screen_off_ball": 4.0,
                "steal_attempt": 0.9,
                "shot": 39.0,
            },
            failure_transitions={
                "turnover_bad_pass": 30.0,
                "pass_swing": 25.0,
                "drive": 15.0,
                "shot": 30.0,
            },
        ),
        ActionDefinition(
            name="pass_entry",
            display_name="Entry Pass",
            description="Feed the ball into the post.",
            category="pass",
            selection_weight=0.0,
            resolution_type="contested_check",
            primary_attribute="passing",
            base_midpoint=5.0,
            base_steepness=0.05,
            time_cost_seconds=2.8,
            zone_requirement="perimeter",
            success_transitions={
                "shot": 58.0,
                "pass_kickout": 28.0,
                "pass_swing": 14.0,
            },
            failure_transitions={
                "turnover_bad_pass": 24.0,
                "pass_swing": 46.0,
                "shot": 30.0,
            },
        ),
        ActionDefinition(
            name="pass_skip",
            display_name="Skip Pass",
            description="Cross-court pass over the defense — shifts the whole scheme.",
            category="pass",
            selection_weight=0.0,
            resolution_type="contested_check",
            primary_attribute="passing",
            base_midpoint=10.0,
            base_steepness=0.05,
            time_cost_seconds=2.6,
            success_transitions={
                "shot": 52.0,
                "pass_swing": 20.0,
                "drive": 16.0,
                "pass_extra": 12.0,
            },
            failure_transitions={
                "turnover_bad_pass": 28.0,
                "pass_swing": 42.0,
                "shot": 30.0,
            },
        ),
        ActionDefinition(
            name="pass_kickout",
            display_name="Kickout Pass",
            description="Kick the ball out of the paint to a shooter.",
            category="pass",
            selection_weight=0.0,
            resolution_type="contested_check",
            primary_attribute="passing",
            base_midpoint=-5.0,
            base_steepness=0.05,
            time_cost_seconds=2.4,
            zone_requirement="paint",
            success_transitions={
                "shot": 54.0,
                "pass_extra": 14.0,
                "pass_swing": 22.0,
                "screen_on_ball": 10.0,
            },
            failure_transitions={
                "turnover_bad_pass": 24.0,
                "pass_swing": 46.0,
                "shot": 30.0,
            },
        ),
        ActionDefinition(
            name="pass_extra",
            display_name="Extra Pass",
            description="One more pass for a better look.",
            category="pass",
            selection_weight=0.0,
            resolution_type="contested_check",
            primary_attribute="passing",
            base_midpoint=-5.0,
            base_steepness=0.05,
            time_cost_seconds=2.2,
            success_transitions={
                "shot": 68.0,
                "pass_swing": 22.0,
                "drive": 10.0,
            },
            failure_transitions={
                "turnover_bad_pass": 24.0,
                "pass_swing": 46.0,
                "shot": 30.0,
            },
        ),
        ActionDefinition(
            name="drive",
            display_name="Drive",
            description="Attack the paint off the dribble.",
            category="dribble",
            selection_weight=0.0,
            weight_attributes={"speed": 0.4},
            resolution_type="contested_check",
            primary_attribute="speed",
            base_midpoint=45.0,
            base_steepness=0.04,
            time_cost_seconds=3.4,
            zone_requirement="perimeter",
            emits_tags=["drive"],
            success_transitions={
                "shot": 56.0,
                "pass_kickout": 20.0,
                "help_rotation": 14.0,
                "pass_swing": 10.0,
            },
            failure_transitions={
                "shot": 42.0,
                "pass_swing": 36.0,
                "turnover_lost_ball": 22.0,
            },
        ),
        ActionDefinition(
            name="crossover",
            display_name="Crossover",
            description="Shake the defender off the dribble.",
            category="dribble",
            selection_weight=0.0,
            weight_attributes={"speed": 0.4},
            resolution_type="contested_check",
            primary_attribute="speed",
            base_midpoint=50.0,
            base_steepness=0.04,
            time_cost_seconds=2.6,
            zone_requirement="perimeter",
            emits_tags=["crossover"],
            success_transitions={
                "shot": 54.0,
                "drive": 30.0,
                "pass_swing": 16.0,
            },
            failure_transitions={
                "pass_swing": 40.0,
                "shot": 30.0,
                "turnover_lost_ball": 18.0,
                "iso": 12.0,
            },
        ),
        ActionDefinition(
            name="iso",
            display_name="Isolation",
            description="Clear out and go one-on-one.",
            category="dribble",
            selection_weight=0.0,
            weight_attributes={"ego": 0.6},
            resolution_type="contested_check",
            primary_attribute="scoring",
            base_midpoint=55.0,
            base_steepness=0.04,
            time_cost_seconds=3.8,
            emits_tags=["iso"],
            success_transitions={"shot": 85.0, "pass_swing": 15.0},
            failure_transitions={
                "shot": 55.0,
                "pass_swing": 25.0,
                "turnover_lost_ball": 20.0,
            },
        ),
        ActionDefinition(
            name="screen_on_ball",
            display_name="On-Ball Screen",
            description="A teammate sets a pick for the ball handler — roll, pop, or slip.",
            category="screen",
            selection_weight=0.0,
            weight_attributes={"iq": 0.2},
            resolution_type="automatic",
            time_cost_seconds=2.8,
            zone_requirement="perimeter",
            transitions={
                "drive": 26.0,
                "shot": 30.0,
                "pass_swing": 12.0,
                "defense_switch": 18.0,
                "pass_skip": 6.0,
                "iso": 6.0,
                "steal_attempt": 2.0,
            },
        ),
        ActionDefinition(
            name="screen_off_ball",
            display_name="Off-Ball Screen",
            description="A screen away from the ball frees a cutter.",
            category="screen",
            selection_weight=0.0,
            resolution_type="automatic",
            time_cost_seconds=2.6,
            zone_requirement="perimeter",
            transitions={
                "cut_curl": 28.0,
                "cut_backdoor": 18.0,
                "spot_up": 32.0,
                "relocate": 22.0,
            },
        ),
        ActionDefinition(
            name="cut_curl",
            display_name="Curl Cut",
            description="The cutter curls off the screen for a catch in rhythm.",
            category="cut",
            selection_weight=0.0,
            resolution_type="contested_check",
            primary_attribute="speed",
            base_midpoint=45.0,
            base_steepness=0.04,
            time_cost_seconds=2.4,
            success_transitions={
                "shot": 74.0,
                "pass_swing": 16.0,
                "drive": 10.0,
            },
            failure_transitions={
                "pass_swing": 58.0,
                "shot": 30.0,
                "turnover_bad_pass": 12.0,
            },
        ),
        ActionDefinition(
            name="cut_backdoor",
            display_name="Backdoor Cut",
            description="The cutter slips behind the defense toward the rim.",
            category="cut",
            selection_weight=0.0,
            resolution_type="contested_check",
            primary_attribute="speed",
            base_midpoint=52.0,
            base_steepness=0.04,
            time_cost_seconds=2.4,
            success_transitions={
                "shot": 90.0,
                "pass_kickout": 10.0,
            },
            failure_transitions={
                "pass_swing": 55.0,
                "shot": 25.0,
                "turnover_bad_pass": 20.0,
            },
        ),
        ActionDefinition(
            name="spot_up",
            display_name="Spot Up",
            description="The shooter sets their feet and waits for the kick.",
            category="cut",
            selection_weight=0.0,
            resolution_type="automatic",
            time_cost_seconds=2.0,
            transitions={
                "shot": 64.0,
                "pass_swing": 26.0,
                "drive": 10.0,
            },
        ),
        ActionDefinition(
            name="relocate",
            display_name="Relocate",
            description="Off-ball movement to a new spot on the floor.",
            category="cut",
            selection_weight=0.0,
            resolution_type="automatic",
            time_cost_seconds=2.0,
            transitions={
                "pass_swing": 44.0,
                "shot": 36.0,
                "drive": 20.0,
            },
        ),
        ActionDefinition(
            name="defense_switch",
            display_name="Defensive Switch",
            description="The defense switches the screen — new matchup on the ball.",
            category="defense",
            selection_weight=0.0,
            resolution_type="automatic",
            time_cost_seconds=1.6,
            transitions={
                "shot": 44.0,
                "drive": 30.0,
                "iso": 16.0,
                "pass_swing": 10.0,
            },
        ),
        ActionDefinition(
            name="help_rotation",
            display_name="Help Rotation",
            description="A helper rotates over to stop the drive — someone is open.",
            category="defense",
            selection_weight=0.0,
            resolution_type="automatic",
            time_cost_seconds=1.4,
            zone_requirement="paint",
            transitions={
                "pass_kickout": 55.0,
                "shot": 45.0,
            },
        ),
        ActionDefinition(
            name="steal_attempt",
            display_name="Steal Gamble",
            description="A defender jumps the passing lane — steal, scramble, or blow-by.",
            category="defense",
            selection_weight=0.0,
            resolution_type="automatic",
            time_cost_seconds=1.8,
            transitions={
                "shot": 55.0,
                "pass_swing": 25.0,
                "drive": 20.0,
            },
        ),
        ActionDefinition(
            name="second_chance",
            display_name="Second Chance",
            description="Reset after an offensive rebound.",
            category="rebound",
            selection_weight=0.0,
            resolution_type="automatic",
            time_cost_seconds=1.2,
            transitions={"shot": 70.0, "pass_swing": 18.0, "drive": 12.0},
        ),
        ActionDefinition(
            name="turnover_bad_pass",
            display_name="Bad Pass",
            description="Pass picked off or thrown away — live-ball turnover.",
            category="turnover",
            selection_weight=0.0,
            resolution_type="automatic",
            time_cost_seconds=0.5,
        ),
        ActionDefinition(
            name="turnover_lost_ball",
            display_name="Lost Ball",
            description="Stripped on the dribble — live-ball turnover.",
            category="turnover",
            selection_weight=0.0,
            resolution_type="automatic",
            time_cost_seconds=0.5,
        ),
        ActionDefinition(
            name="violation_deadball",
            display_name="Violation",
            description="Travel or double dribble — dead-ball turnover.",
            category="violation",
            selection_weight=0.0,
            resolution_type="automatic",
            time_cost_seconds=0.4,
        ),
        # ---- Tier-3 violation + foul nodes (Phase 5) ----
        #
        # All governable surface, INERT by default: no default transition
        # edge carries positive weight into them, so they draw no RNG and
        # never fire until governance activates them (modify_transitions,
        # transition_biases, or — for goaltending — selection_weight).
        # Once active, violation-category weights scale with the
        # ``violation_strictness`` knob x handler IQ, same as travel.
        ActionDefinition(
            name="violation_backcourt",
            display_name="Backcourt Violation",
            description="Ball taken back over half court — dead-ball turnover.",
            category="violation",
            selection_weight=0.0,
            resolution_type="automatic",
            time_cost_seconds=0.4,
        ),
        ActionDefinition(
            name="violation_three_second",
            display_name="Three-Second Violation",
            description=(
                "Offense camps in the paint — dead-ball turnover. Weight "
                "scales with the possession's consecutive paint-zone events "
                "(the paint-dwell counter), so it only threatens teams that "
                "actually park in the lane."
            ),
            category="violation",
            selection_weight=0.0,
            resolution_type="automatic",
            time_cost_seconds=0.4,
            emits_tags=["paint_dwell"],
        ),
        ActionDefinition(
            name="violation_lane",
            display_name="Lane Violation",
            description="Offensive lane violation — dead-ball turnover.",
            category="violation",
            selection_weight=0.0,
            resolution_type="automatic",
            time_cost_seconds=0.4,
        ),
        ActionDefinition(
            name="violation_kicked_ball",
            display_name="Kicked Ball",
            description=(
                "Defensive kicked-ball violation — the offense keeps the "
                "ball with a shot-clock reset."
            ),
            category="violation",
            selection_weight=0.0,
            resolution_type="automatic",
            time_cost_seconds=0.8,
            transitions={
                "shot": 40.0,
                "pass_swing": 35.0,
                "drive": 25.0,
            },
        ),
        ActionDefinition(
            name="violation_goaltending",
            display_name="Goaltending",
            description=(
                "Defensive goaltend on a missed rim/mid shot awards the "
                "basket. ``selection_weight`` is the per-missed-shot "
                "probability (default 0.0 = never checked, no RNG drawn). "
                "Governance activates it via modify_actions "
                '({"violation_goaltending": {"selection_weight": 0.03}}); '
                'the FIBA-style "legal after rim contact" rule is '
                "remove_actions: [\"violation_goaltending\"] or weight 0."
            ),
            category="violation",
            selection_weight=0.0,
            resolution_type="automatic",
            time_cost_seconds=0.0,
        ),
        ActionDefinition(
            name="foul_take",
            display_name="Take Foul",
            description=(
                "A defender takes the foul to kill the break — personal + "
                "team foul, side-out, offense resets in the half court. "
                "Only reachable on transition possessions."
            ),
            category="foul",
            selection_weight=0.0,
            resolution_type="automatic",
            time_cost_seconds=1.2,
            emits_tags=["transition_only"],
            transitions={"initiate": 100.0},
        ),
        ActionDefinition(
            name="foul_clear_path",
            display_name="Clear-Path Foul",
            description=(
                "Clear-path foul on the break — one free throw plus the "
                "ball. Only reachable on transition possessions."
            ),
            category="foul",
            selection_weight=0.0,
            resolution_type="automatic",
            time_cost_seconds=1.2,
            emits_tags=["transition_only"],
            transitions={"initiate": 100.0},
        ),
        ActionDefinition(
            name="foul_away_from_play",
            display_name="Away-From-Play Foul",
            description=(
                "Off-ball foul during a transition advance — one free "
                "throw plus the ball. Only reachable on transition "
                "possessions."
            ),
            category="foul",
            selection_weight=0.0,
            resolution_type="automatic",
            time_cost_seconds=1.2,
            emits_tags=["transition_only"],
            transitions={"initiate": 100.0},
        ),
    ]


def basketball_micro_actions(rules: RuleSet) -> list[ActionDefinition]:
    """Build the full micro-engine action list: shots + chain nodes.

    The shot actions are exactly ``basketball_actions(rules)`` — the micro
    engine resolves shots with the same probability curves and point
    values as the macro engine. Chain nodes are appended alongside.

    Args:
        rules: The current RuleSet, used to derive shot point values.

    Returns:
        Shot actions + free throw + micro chain nodes.
    """
    return basketball_actions(rules) + basketball_micro_chain_nodes()


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
        # Shot actions + the micro chain-node vocabulary. The chain nodes
        # live in the definition (not just the engine fallback) so
        # governance can patch them — remove screens, reweight transitions,
        # add new nodes. The macro engine provably ignores them
        # (test_macro_ignores_chain_nodes_bit_exactly).
        actions=basketball_micro_actions(rules),
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
