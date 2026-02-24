

# The Abstract Game Spine

## Architecture Plan for Game-Agnostic Simulation in Pinwheel Fates

### Executive Summary

Pinwheel's current simulation engine is a beautiful, tightly tuned basketball machine. The shot probability logistic curves, the defensive scheme selection, the possession-by-possession drama -- it works. But it works for *basketball*. The governance system already lets players modify basketball's parameters. The next step is letting players modify what the game *is*.

The core insight: **the game definition itself is governable data**. Basketball is the default dataset. Coin flipping, arm wrestling, jump rope, or something nobody has imagined yet -- each is just a different configuration loaded into the same simulation spine.

The spine has five components:

1. **Game Definition** -- a Pydantic schema describing actions, resolutions, outcomes, state, turn structure, and win conditions. Basketball is one instance.
2. **Resolution Engine** -- a generic resolver that dispatches to pluggable resolution strategies (logistic, random, comparison, degrading, custom).
3. **Turn Executor** -- a generic loop that reads the turn structure from the game definition and executes it, replacing the hardcoded quarter/possession/Elam flow.
4. **Attribute Registry** -- attributes are part of the game definition, not hardcoded on hoopers. Attribute remapping handles game transitions.
5. **The Interpreter DSL** -- the AI produces `GameDefinitionPatch` operations that modify the game definition through governance, using the same effect system that already exists.

What stays unchanged: teams, hoopers, governors, Discord commands, the event store, the effect registry, the AI report pipeline, the web frontend, the scheduling system. The spine replaces only the simulation core (`core/simulation.py`, `core/possession.py`, `core/scoring.py`, `core/defense.py`, `core/moves.py`) and makes `models/rules.py` a special case of a more general game definition.

---

## 1. The Game Definition Schema

A `GameDefinition` is a complete, self-contained description of how the game is played. It is the successor to `RuleSet` -- but where `RuleSet` contains 40 parameters for one specific game, `GameDefinition` contains everything needed to simulate *any* game.

### 1.1 Top-Level Schema

```python
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class TurnOrder(str, Enum):
    """How participants take turns within a contest."""
    ALTERNATING = "alternating"    # Team A acts, Team B acts, repeat
    SIMULTANEOUS = "simultaneous"  # Both teams declare, then resolve
    PROGRESSIVE = "progressive"    # Each team acts until failure (elimination)
    SEQUENTIAL = "sequential"      # All of Team A, then all of Team B
    AUCTION = "auction"            # Teams bid resources to act


class WinConditionType(str, Enum):
    """How the contest ends."""
    HIGHEST_SCORE = "highest_score"          # Most points when time/turns expire
    FIRST_TO_THRESHOLD = "first_to_threshold" # First to reach target score
    LAST_STANDING = "last_standing"           # Elimination until one remains
    TARGET_SCORE_MARGIN = "target_score_margin" # Elam-style: leading + margin
    BEST_OF_N = "best_of_n"                  # Win N sub-contests
    CUSTOM = "custom"                        # Evaluated by expression


class ResolutionType(str, Enum):
    """How an action's outcome is determined."""
    RANDOM = "random"                # Pure probability (coin flip)
    ATTRIBUTE_CHECK = "attribute_check"  # Logistic curve on attribute
    COMPARISON = "comparison"        # Attacker vs defender attribute
    DEGRADING = "degrading"          # Gets harder over time
    EXPRESSION = "expression"        # Custom formula


class GameDefinition(BaseModel):
    """Complete description of how the game is played.

    This is the abstract successor to RuleSet. Basketball, coin flipping,
    arm wrestling, jump rope -- each is an instance of this schema.
    Governance changes produce new versions of this definition.
    """

    # Identity
    game_id: str = ""
    name: str = "Basketball"
    version: int = 1
    description: str = ""
    flavor_text: str = ""  # How the AI should narrate this game

    # Attribute Schema
    attributes: list[AttributeDefinition] = Field(default_factory=list)
    attribute_budget: int = 360  # Total points per participant

    # Actions
    actions: list[ActionDefinition] = Field(default_factory=list)

    # Turn Structure
    turn_order: TurnOrder = TurnOrder.ALTERNATING
    phases: list[PhaseDefinition] = Field(default_factory=list)
    turns_per_phase: int | None = None  # None = clock-based
    clock_seconds_per_phase: float | None = None
    clock_per_turn_seconds: float | None = None  # Shot clock equivalent

    # Win Condition
    win_condition: WinCondition = Field(default_factory=lambda: WinCondition())

    # Participants
    participants_per_side: int = 3
    bench_size: int = 1
    substitution_allowed: bool = True

    # Derived Mechanics (optional, game-specific)
    mechanics: list[MechanicDefinition] = Field(default_factory=list)

    # Goverable Parameters (the classic RuleSet knobs, now generic)
    parameters: dict[str, ParameterDefinition] = Field(default_factory=dict)

    # State Template
    state_template: StateTemplate = Field(
        default_factory=lambda: StateTemplate()
    )
```

### 1.2 Attribute Definitions

```python
class AttributeDefinition(BaseModel):
    """A single attribute that participants can have.

    In basketball: scoring, passing, defense, speed, stamina, iq, ego,
    chaotic_alignment, fate.
    In arm wrestling: strength, technique, endurance, intimidation, focus.
    """

    name: str                          # "scoring", "strength", etc.
    description: str = ""              # Human-readable explanation
    min_value: int = 1
    max_value: int = 100
    default_value: int = 40
    category: str = "physical"         # physical, mental, meta
    scales_with_stamina: bool = True   # Does fatigue degrade this?
    display_order: int = 0             # For UI ordering


class AttributeMapping(BaseModel):
    """How to remap attributes when the game definition changes.

    When basketball becomes arm wrestling, 'scoring' maps to 'strength',
    'defense' maps to 'endurance', etc. Unmapped attributes use default.
    """

    source: str           # "scoring"
    target: str           # "strength"
    scale: float = 1.0    # Multiplier during conversion
    offset: int = 0       # Additive adjustment
```

### 1.3 Action Definitions

```python
class ActionDefinition(BaseModel):
    """An action a participant can take during their turn.

    In basketball: at_rim_shot, mid_range_shot, three_pointer, pass, drive.
    In coin flip: call_heads, call_tails.
    In arm wrestling: power_push, technique_hold, psych_out.
    """

    name: str                          # "at_rim_shot", "call_heads"
    display_name: str = ""             # "At-Rim Shot", "Call Heads"
    description: str = ""
    category: str = "default"          # For grouping in UI

    # Selection
    selection_weight: float = 1.0      # Base probability of AI choosing this
    weight_attributes: dict[str, float] = Field(default_factory=dict)
    # e.g. {"speed": 0.3, "iq": 0.2} -- attribute contribution to selection

    # Resolution
    resolution: ResolutionDefinition = Field(
        default_factory=lambda: ResolutionDefinition()
    )

    # Outcomes
    outcomes: list[OutcomeDefinition] = Field(default_factory=list)

    # Constraints
    requires_opponent: bool = False    # Needs a defender/opponent
    stamina_cost: float = 0.0         # Direct stamina drain
    cooldown_turns: int = 0           # Cannot repeat for N turns
    max_per_phase: int | None = None  # Usage cap per phase

    # Conditional availability
    available_when: ConditionExpression | None = None
```

### 1.4 Resolution Definitions

```python
class ResolutionDefinition(BaseModel):
    """How an action's outcome is determined.

    The resolution engine reads this and dispatches to the
    appropriate resolver function.
    """

    type: ResolutionType = ResolutionType.RANDOM

    # RANDOM: pure probability
    probability: float = 0.5

    # ATTRIBUTE_CHECK: logistic curve
    attribute: str = ""               # Which participant attribute
    curve: str = "logistic"           # logistic, linear, step
    midpoint: float = 50.0            # Where P = 0.5
    steepness: float = 0.05           # How sharp the curve is
    stamina_factor: float = 0.3       # How much stamina affects result
    modifier_attributes: dict[str, float] = Field(default_factory=dict)
    # e.g. {"iq": 0.1, "stamina": 0.3} -- additional attribute modifiers

    # COMPARISON: attacker vs defender
    attacker_attribute: str = ""
    defender_attribute: str = ""
    modifier_attribute: str = ""      # Third attribute as tiebreaker
    advantage_threshold: float = 0.0  # How much advantage needed

    # DEGRADING: gets harder over time
    base_difficulty: float = 0.1
    increase_per_turn: float = 0.02
    degrading_attribute: str = ""     # Attribute that resists degradation
    max_difficulty: float = 0.95

    # EXPRESSION: custom formula (safe subset)
    expression: str = ""
    # e.g. "0.5 + (actor.strength - opponent.defense) / 200"

    # Defense integration (optional)
    defender_contest: DefenderContest | None = None


class DefenderContest(BaseModel):
    """How a defender contests an action.

    In basketball: defensive attribute reduces shot probability.
    In arm wrestling: resistance attribute counters power.
    Not all games have defenders -- coin flip has no contest.
    """

    attribute: str = "defense"        # Defender's relevant attribute
    impact_factor: float = 0.005      # How much 1 point reduces probability
    stamina_factor: float = 0.3       # How much defender fatigue helps attacker
    scheme_modifiers: dict[str, float] = Field(default_factory=dict)
    # Defensive schemes and their modifiers (basketball: man_tight, zone, etc.)
```

### 1.5 Outcome Definitions

```python
class OutcomeDefinition(BaseModel):
    """What happens when an action succeeds or fails.

    Each action has a list of outcomes. The resolution determines
    which outcome fires. Outcomes modify game state.
    """

    name: str                          # "made", "missed", "advantage_gained"
    trigger: str = "success"           # "success", "failure", "always", "critical"
    probability: float | None = None   # Override: specific probability for this outcome

    # State modifications
    score_change: int = 0              # Points added to acting team
    opponent_score_change: int = 0     # Points added to opponent
    state_changes: dict[str, str] = Field(default_factory=dict)
    # e.g. {"advantage_counter": "+1"}, {"lives": "-1"}

    # Participant effects
    stamina_drain: float = 0.0
    attribute_modifier: dict[str, float] = Field(default_factory=dict)
    # Temporary attribute changes for the participant

    # Flow control
    grants_extra_turn: bool = False    # Act again immediately
    ends_phase: bool = False           # Immediately end current phase
    eliminates_participant: bool = False # Remove from contest

    # Follow-up actions
    triggers_action: str | None = None # Another action fires automatically
    # e.g. missed shot -> rebound check

    # Narrative
    narrative_template: str = ""
    energy_level: str = "medium"       # low, medium, high, peak
```

### 1.6 Phase Definitions

```python
class PhaseDefinition(BaseModel):
    """A structural phase of the contest.

    In basketball: Quarter 1, Quarter 2, Halftime, Quarter 3, Elam Ending.
    In coin flip: just "Round" repeated until win condition.
    In jump rope: "Round N" with increasing difficulty.
    """

    name: str                          # "Quarter", "Round", "Period"
    display_name: str = ""
    count: int | None = None           # How many of this phase (None = until win)

    # Duration
    clock_seconds: float | None = None
    max_turns: int | None = None

    # Between phases
    recovery: dict[str, float] = Field(default_factory=dict)
    # e.g. {"stamina": 0.15} -- recovery between phases

    # Phase-specific rules
    parameter_overrides: dict[str, float | int | bool] = Field(
        default_factory=dict
    )

    # Transition
    transition_condition: ConditionExpression | None = None
    # When does this phase trigger? (for conditional phases like Elam)

    # Flavor
    narrative_instruction: str = ""
```

### 1.7 Win Conditions

```python
class WinCondition(BaseModel):
    """When and how the contest ends."""

    type: WinConditionType = WinConditionType.HIGHEST_SCORE
    threshold: int | None = None       # For FIRST_TO_THRESHOLD
    margin: int | None = None          # For TARGET_SCORE_MARGIN (Elam)
    safety_cap: int = 300              # Max turns before forced end
    tiebreaker: str = "overtime"       # "overtime", "sudden_death", "coin_flip"

    # State-based win (for games that track custom state)
    state_field: str | None = None     # Which state field to check
    # e.g. "advantage" for arm wrestling -- when advantage >= threshold, you win

    # Expression-based (for CUSTOM)
    expression: str = ""
    # e.g. "home_advantage_counter >= 5"


class ConditionExpression(BaseModel):
    """A safe, evaluable condition expression.

    Uses the same evaluation engine as the existing effect system's
    condition_check -- field equality, _gte/_lte suffixes, meta lookups.
    This is NOT arbitrary code execution; it's structured conditions.
    """

    checks: dict[str, object] = Field(default_factory=dict)
    # Same format as action_code condition_check:
    # {"quarter_gte": 3, "score_diff_lte": 10}
    # {"consecutive_makes_gte": 3}
    # {"random_chance": 0.15}
```

### 1.8 State Template

```python
class StateTemplate(BaseModel):
    """What mutable state the game tracks beyond scores.

    Basketball tracks: fouls, rebounds, assists, turnovers.
    Arm wrestling tracks: advantage_counter, grip_strength_remaining.
    Jump rope tracks: consecutive_successes, difficulty_level.
    """

    team_state: dict[str, StateFieldDefinition] = Field(default_factory=dict)
    participant_state: dict[str, StateFieldDefinition] = Field(
        default_factory=dict
    )
    contest_state: dict[str, StateFieldDefinition] = Field(default_factory=dict)


class StateFieldDefinition(BaseModel):
    """A single state field tracked during a contest."""

    name: str
    type: str = "int"                  # "int", "float", "bool", "str"
    default: int | float | bool | str = 0
    min_value: float | None = None
    max_value: float | None = None
    display: bool = True               # Show in box score / summary
    display_name: str = ""
    display_format: str = ""           # e.g. "{value:.1f}%"


class ParameterDefinition(BaseModel):
    """A governable parameter within the game definition.

    This is the generic version of RuleSet fields. Each game defines
    its own parameters with ranges and descriptions.
    """

    name: str
    value: int | float | bool
    min_value: float | None = None
    max_value: float | None = None
    description: str = ""
    tier: int = 1                      # Governance tier (1-4)
    category: str = "mechanics"


class MechanicDefinition(BaseModel):
    """A named mechanic within the game.

    Mechanics are reusable rule components -- basketball's Elam Ending,
    arm wrestling's reversal system, jump rope's difficulty escalation.
    They attach to phases or turns and modify behavior.
    """

    name: str
    description: str = ""
    hook_point: str = ""               # When this mechanic fires
    condition: ConditionExpression | None = None
    effects: list[dict[str, object]] = Field(default_factory=list)
    # Same format as action_code in the effect system
```

---

## 2. The Resolution Engine

The current simulation uses logistic curves in `core/scoring.py`. The abstract engine needs to dispatch to different resolution strategies based on the action's `ResolutionDefinition`.

### 2.1 Resolver Protocol

```python
from typing import Protocol
import random


class Resolver(Protocol):
    """Protocol for action resolution strategies."""

    def resolve(
        self,
        resolution: ResolutionDefinition,
        actor: ParticipantState,
        opponent: ParticipantState | None,
        contest_state: ContestState,
        rng: random.Random,
    ) -> ResolutionResult: ...


class ResolutionResult:
    """Output of resolution -- which outcome triggered and with what probability."""

    success: bool
    probability: float          # The computed probability
    outcome_name: str           # Which OutcomeDefinition triggered
    details: dict[str, object]  # Resolver-specific details for logging/narrative
```

### 2.2 Resolver Implementations

```python
class RandomResolver:
    """Pure probability. No attributes involved.

    Used by: Coin Flip Championship.
    """

    def resolve(self, resolution, actor, opponent, contest_state, rng):
        prob = resolution.probability
        success = rng.random() < prob
        return ResolutionResult(
            success=success,
            probability=prob,
            outcome_name="success" if success else "failure",
            details={"type": "random"},
        )


class AttributeCheckResolver:
    """Logistic curve based on participant attribute.

    Used by: Basketball (shots), Jump Rope (trick difficulty).
    This is the direct successor to core/scoring.py's compute_shot_probability.
    """

    def resolve(self, resolution, actor, opponent, contest_state, rng):
        attr_value = actor.get_attribute(resolution.attribute)
        base_prob = logistic(
            attr_value,
            resolution.midpoint,
            resolution.steepness,
        )

        # Apply stamina degradation
        stamina_mod = 1.0 - resolution.stamina_factor * (1.0 - actor.stamina)

        # Apply modifier attributes (IQ, etc.)
        for attr_name, weight in resolution.modifier_attributes.items():
            mod_val = actor.get_attribute(attr_name)
            base_prob *= (0.9 + mod_val / (500.0 / weight))

        # Apply defender contest if present
        if opponent and resolution.defender_contest:
            dc = resolution.defender_contest
            def_attr = opponent.get_attribute(dc.attribute)
            contest = 1.0 - (def_attr * dc.impact_factor)
            contest = max(0.5, min(1.0, contest))
            base_prob *= contest

        prob = max(0.01, min(0.99, base_prob * stamina_mod))
        success = rng.random() < prob
        return ResolutionResult(
            success=success,
            probability=prob,
            outcome_name="success" if success else "failure",
            details={"type": "attribute_check", "attribute": resolution.attribute},
        )


class ComparisonResolver:
    """Direct attribute comparison with modifier.

    Used by: Arm Wrestling.
    """

    def resolve(self, resolution, actor, opponent, contest_state, rng):
        if not opponent:
            return ResolutionResult(True, 1.0, "success", {})

        atk = actor.get_attribute(resolution.attacker_attribute)
        dfn = opponent.get_attribute(resolution.defender_attribute)
        mod = 0
        if resolution.modifier_attribute:
            mod = actor.get_attribute(resolution.modifier_attribute) - 50

        # Comparison with noise
        atk_roll = atk + mod + rng.gauss(0, 10)
        dfn_roll = dfn + rng.gauss(0, 10)

        advantage = atk_roll - dfn_roll - resolution.advantage_threshold
        prob = logistic(advantage, 0, 0.1)  # Sigmoid on advantage
        success = rng.random() < prob

        return ResolutionResult(
            success=success,
            probability=prob,
            outcome_name="success" if success else "failure",
            details={
                "type": "comparison",
                "attacker_roll": atk_roll,
                "defender_roll": dfn_roll,
                "advantage": advantage,
            },
        )


class DegradingResolver:
    """Gets harder over time. Tests endurance.

    Used by: Jump Rope Marathon.
    """

    def resolve(self, resolution, actor, opponent, contest_state, rng):
        turn = contest_state.get("turn_count", 0)
        difficulty = min(
            resolution.max_difficulty,
            resolution.base_difficulty + resolution.increase_per_turn * turn,
        )

        # Attribute resists degradation
        resist = 0.0
        if resolution.degrading_attribute:
            resist = actor.get_attribute(resolution.degrading_attribute) / 200.0

        fail_prob = max(0.01, difficulty - resist)
        success = rng.random() > fail_prob  # Higher = success

        return ResolutionResult(
            success=success,
            probability=1.0 - fail_prob,
            outcome_name="success" if success else "failure",
            details={
                "type": "degrading",
                "turn": turn,
                "difficulty": difficulty,
                "resist": resist,
            },
        )


class ExpressionResolver:
    """Safe expression evaluation for custom resolutions.

    Uses the same sandboxed evaluation as the existing effect system's
    condition_check, extended to produce a probability value.
    """

    def resolve(self, resolution, actor, opponent, contest_state, rng):
        # Build evaluation context (same pattern as hooks.py _build_eval_context)
        ctx = {
            "actor": actor.to_eval_dict(),
            "opponent": opponent.to_eval_dict() if opponent else {},
            "state": contest_state.to_eval_dict(),
            "random": rng.random(),
        }
        prob = safe_eval_probability(resolution.expression, ctx)
        success = rng.random() < prob

        return ResolutionResult(
            success=success,
            probability=prob,
            outcome_name="success" if success else "failure",
            details={"type": "expression", "expression": resolution.expression},
        )


RESOLVERS: dict[ResolutionType, Resolver] = {
    ResolutionType.RANDOM: RandomResolver(),
    ResolutionType.ATTRIBUTE_CHECK: AttributeCheckResolver(),
    ResolutionType.COMPARISON: ComparisonResolver(),
    ResolutionType.DEGRADING: DegradingResolver(),
    ResolutionType.EXPRESSION: ExpressionResolver(),
}
```

---

## 3. The Turn/Round Structure

### 3.1 The Generic Turn Executor

The current simulation has a rigid structure: `simulate_game` -> `_run_quarter` (x3) -> `_run_elam`. The abstract version reads the phase and turn structure from the game definition.

```python
class TurnExecutor:
    """Executes a contest according to its GameDefinition.

    Replaces simulate_game(), _run_quarter(), and _run_elam()
    with a data-driven execution loop.
    """

    def execute_contest(
        self,
        home: Team,
        away: Team,
        game_def: GameDefinition,
        seed: int,
        effects: list[RegisteredEffect] | None = None,
        meta_store: MetaStore | None = None,
    ) -> ContestResult:
        rng = random.Random(seed)

        # Initialize state from game definition
        contest_state = ContestState.from_definition(game_def, home, away)

        # Fire pre-contest hooks (same as sim.game.pre)
        self._fire_hooks("sim.game.pre", contest_state, effects, meta_store, rng)

        # Execute phases
        for phase_def in game_def.phases:
            if not self._should_enter_phase(phase_def, contest_state):
                continue

            self._execute_phase(
                phase_def, game_def, contest_state, rng, effects, meta_store,
            )

            if contest_state.is_over:
                break

            # Between-phase recovery
            self._apply_recovery(phase_def, contest_state)

        # Check win condition
        winner = self._determine_winner(game_def.win_condition, contest_state)

        # Fire post-contest hooks
        self._fire_hooks("sim.game.end", contest_state, effects, meta_store, rng)

        return self._build_result(contest_state, winner, game_def)

    def _execute_phase(
        self,
        phase: PhaseDefinition,
        game_def: GameDefinition,
        state: ContestState,
        rng: random.Random,
        effects: list[RegisteredEffect] | None,
        meta_store: MetaStore | None,
    ) -> None:
        """Execute one phase (quarter, round, period)."""
        self._fire_hooks("sim.phase.pre", state, effects, meta_store, rng)

        turn = 0
        while not self._phase_complete(phase, state, turn):
            if state.is_over:
                break

            turn += 1
            state.increment("turn_count", 1)

            self._execute_turn(game_def, state, rng, effects, meta_store)

            # Check win condition after each turn
            if self._check_win(game_def.win_condition, state):
                state.is_over = True
                break

        self._fire_hooks("sim.phase.end", state, effects, meta_store, rng)

    def _execute_turn(
        self,
        game_def: GameDefinition,
        state: ContestState,
        rng: random.Random,
        effects: list[RegisteredEffect] | None,
        meta_store: MetaStore | None,
    ) -> None:
        """Execute one turn according to the turn order."""
        self._fire_hooks("sim.turn.pre", state, effects, meta_store, rng)

        if game_def.turn_order == TurnOrder.ALTERNATING:
            self._alternating_turn(game_def, state, rng, effects, meta_store)
        elif game_def.turn_order == TurnOrder.SIMULTANEOUS:
            self._simultaneous_turn(game_def, state, rng, effects, meta_store)
        elif game_def.turn_order == TurnOrder.PROGRESSIVE:
            self._progressive_turn(game_def, state, rng, effects, meta_store)

        # Swap active team (for alternating)
        if game_def.turn_order == TurnOrder.ALTERNATING:
            state.swap_active_team()

        self._fire_hooks("sim.turn.post", state, effects, meta_store, rng)

    def _alternating_turn(self, game_def, state, rng, effects, meta_store):
        """One team acts, outcome resolved."""
        actor = self._select_actor(state.active_participants, game_def, rng)
        action = self._select_action(actor, game_def, state, rng)
        opponent = self._select_opponent(
            state.opposing_participants, action, game_def, rng,
        ) if action.requires_opponent else None

        resolver = RESOLVERS[action.resolution.type]
        result = resolver.resolve(
            action.resolution, actor, opponent, state, rng,
        )

        # Apply outcome
        outcome = self._select_outcome(action, result)
        self._apply_outcome(outcome, actor, opponent, state)

        # Log the turn
        state.log_turn(actor, action, result, outcome)

    def _simultaneous_turn(self, game_def, state, rng, effects, meta_store):
        """Both teams declare actions, then resolve together.
        Used for rock-paper-scissors style games.
        """
        home_actor = self._select_actor(state.home_participants, game_def, rng)
        away_actor = self._select_actor(state.away_participants, game_def, rng)

        home_action = self._select_action(home_actor, game_def, state, rng)
        away_action = self._select_action(away_actor, game_def, state, rng)

        # Resolve both against each other
        home_result = RESOLVERS[home_action.resolution.type].resolve(
            home_action.resolution, home_actor, away_actor, state, rng,
        )
        away_result = RESOLVERS[away_action.resolution.type].resolve(
            away_action.resolution, away_actor, home_actor, state, rng,
        )

        # Apply outcomes
        home_outcome = self._select_outcome(home_action, home_result)
        away_outcome = self._select_outcome(away_action, away_result)
        self._apply_outcome(home_outcome, home_actor, away_actor, state)
        self._apply_outcome(away_outcome, away_actor, home_actor, state)

    def _progressive_turn(self, game_def, state, rng, effects, meta_store):
        """Each participant acts until failure. Used for elimination games."""
        for participant in state.active_participants:
            if participant.eliminated:
                continue

            action = self._select_action(participant, game_def, state, rng)
            result = RESOLVERS[action.resolution.type].resolve(
                action.resolution, participant, None, state, rng,
            )
            outcome = self._select_outcome(action, result)
            self._apply_outcome(outcome, participant, None, state)

            if outcome.eliminates_participant:
                participant.eliminated = True

            state.log_turn(participant, action, result, outcome)
```

### 3.2 Contest State

```python
class ContestState:
    """Mutable state during a contest.

    The generic replacement for GameState. Tracks scores, participants,
    and arbitrary state fields defined by the game definition.
    """

    home_score: int = 0
    away_score: int = 0
    home_participants: list[ParticipantState]
    away_participants: list[ParticipantState]
    phase_number: int = 0
    turn_count: int = 0
    is_over: bool = False
    home_is_active: bool = True  # Who's turn is it?

    # Dynamic state from StateTemplate
    state: dict[str, int | float | bool | str]
    team_state: dict[str, dict[str, int | float | bool | str]]
    participant_state_extra: dict[str, dict[str, int | float | bool | str]]

    turn_log: list[TurnLog]

    @classmethod
    def from_definition(
        cls,
        game_def: GameDefinition,
        home: Team,
        away: Team,
    ) -> ContestState:
        """Initialize contest state from a game definition."""
        # Build participants with mapped attributes
        home_parts = [
            ParticipantState.from_hooper(h, game_def.attributes)
            for h in home.hoopers
        ]
        away_parts = [
            ParticipantState.from_hooper(h, game_def.attributes)
            for h in away.hoopers
        ]

        # Initialize state template fields
        contest_fields = {
            name: defn.default
            for name, defn in game_def.state_template.contest_state.items()
        }
        team_fields = {
            name: defn.default
            for name, defn in game_def.state_template.team_state.items()
        }

        return cls(
            home_participants=home_parts,
            away_participants=away_parts,
            state=contest_fields,
            team_state={
                home.id: dict(team_fields),
                away.id: dict(team_fields),
            },
            participant_state_extra={},
            turn_log=[],
        )

    @property
    def active_participants(self) -> list[ParticipantState]:
        if self.home_is_active:
            return [p for p in self.home_participants if not p.eliminated]
        return [p for p in self.away_participants if not p.eliminated]

    @property
    def opposing_participants(self) -> list[ParticipantState]:
        if self.home_is_active:
            return [p for p in self.away_participants if not p.eliminated]
        return [p for p in self.home_participants if not p.eliminated]

    def swap_active_team(self) -> None:
        self.home_is_active = not self.home_is_active

    def increment(self, field: str, amount: int | float) -> None:
        if field in self.state:
            self.state[field] += amount  # type: ignore[operator]


class ParticipantState:
    """Mutable state of a participant during a contest.

    The generic replacement for HooperState. Carries attributes
    as defined by the current game definition.
    """

    hooper: Hooper
    attributes: dict[str, int]         # Current game's attributes
    base_attributes: dict[str, int]    # Original values (before stamina)
    stamina: float = 1.0
    on_court: bool = True
    eliminated: bool = False

    # Stats accumulated during the contest
    stats: dict[str, int | float]

    @classmethod
    def from_hooper(
        cls,
        hooper: Hooper,
        attr_defs: list[AttributeDefinition],
    ) -> ParticipantState:
        """Create participant from hooper, mapping attributes.

        If the hooper has basketball attributes but the game needs
        arm wrestling attributes, the attribute mapping in the game
        definition handles the conversion.
        """
        attrs = {}
        base = hooper.attributes.model_dump()
        for attr_def in attr_defs:
            # Direct match: hooper has this attribute
            if attr_def.name in base:
                attrs[attr_def.name] = base[attr_def.name]
            else:
                # Use default -- attribute doesn't exist on this hooper
                attrs[attr_def.name] = attr_def.default_value

        return cls(
            hooper=hooper,
            attributes=dict(attrs),
            base_attributes=dict(attrs),
            stamina=1.0,
            stats={},
        )

    def get_attribute(self, name: str) -> int:
        """Get current attribute value, scaled by stamina if applicable."""
        base = self.attributes.get(name, 40)
        # Check if this attribute scales with stamina
        # (determined by the game definition, cached on the participant)
        return max(1, int(base * self.stamina))
```

---

## 4. The Attribute System

### 4.1 The Attribute Tension

Hoopers have 9 hardcoded basketball attributes (`PlayerAttributes`). When the game changes, these need to translate to the new game's attribute schema. Three strategies, used in order of preference:

**Strategy 1: Direct Mapping.** If the new game has attributes that semantically match basketball attributes, map directly. Arm wrestling's `strength` maps from basketball's `scoring`. The AI includes `AttributeMapping` entries in the game definition patch.

**Strategy 2: Archetype-Based Generation.** If the new game's attributes are too different for direct mapping, generate new attribute values based on the hooper's archetype and personality. A `sharpshooter` in basketball might have high `precision` in darts. The AI reasons about the archetype's personality to assign appropriate values.

**Strategy 3: Fresh Roll.** If neither mapping nor archetype reasoning works, generate attributes from scratch within the budget, seeded by the hooper's ID (so it's deterministic). This is the least desirable because it disconnects hooper identity from their new capabilities.

### 4.2 The Attribute Registry in Practice

```python
class AttributeRegistry:
    """Manages attribute definitions and hooper-to-participant mapping."""

    def __init__(self, game_def: GameDefinition) -> None:
        self.definitions = {a.name: a for a in game_def.attributes}
        self.mappings: dict[str, AttributeMapping] = {}

    def map_hooper(
        self,
        hooper: Hooper,
        rng: random.Random,
    ) -> dict[str, int]:
        """Map a hooper's existing attributes to the current game's schema."""
        base = hooper.attributes.model_dump()
        result: dict[str, int] = {}

        for attr_def in self.definitions.values():
            # Check explicit mapping first
            if attr_def.name in self.mappings:
                mapping = self.mappings[attr_def.name]
                source_val = base.get(mapping.source, attr_def.default_value)
                result[attr_def.name] = max(
                    attr_def.min_value,
                    min(
                        attr_def.max_value,
                        int(source_val * mapping.scale + mapping.offset),
                    ),
                )
            # Direct name match
            elif attr_def.name in base:
                result[attr_def.name] = base[attr_def.name]
            # Default
            else:
                result[attr_def.name] = attr_def.default_value

        return result

    def set_mapping(self, mapping: AttributeMapping) -> None:
        """Register an attribute mapping (from governance)."""
        self.mappings[mapping.target] = mapping
```

### 4.3 Backward Compatibility with PlayerAttributes

The existing `PlayerAttributes` model stays for the database layer and API contracts. When the game is basketball (the default), `ParticipantState` is built directly from `PlayerAttributes`. When the game is something else, the `AttributeRegistry` handles the mapping. The hoopers' stored attributes never change -- only the in-memory participant state during simulation reads from the mapped values.

---

## 5. The Migration Path

### 5.1 How Games Transition

When a governance proposal changes the game definition, the transition is governed by a `GameTransition`:

```python
class GameTransition(BaseModel):
    """How to transition from one game definition to another."""

    from_version: int
    to_version: int
    attribute_mappings: list[AttributeMapping] = Field(default_factory=list)
    transition_type: str = "clean_break"
    # "clean_break" -- new definition takes effect next round
    # "gradual" -- blend over N rounds (not implemented in v1)
    # "hybrid" -- some elements change, others persist (custom)
    blend_rounds: int = 0  # For gradual transitions
    preserve_standings: bool = True
    preserve_stats: bool = False  # Stats from old game are usually meaningless
    narrative_instruction: str = ""
    # e.g. "The league has voted to transform basketball into arm wrestling.
    # The hoopers flex their muscles uncertainly..."
```

**Clean Break (recommended for v1):**
- New game definition takes effect at the start of the next round.
- Standings carry forward (wins and losses are game-agnostic).
- Individual stats reset (basketball FG% is meaningless in arm wrestling).
- Attribute mapping runs once, producing new participant attributes.
- The AI generates a transition narrative.

**Gradual (future):**
- Game definition A blends with game definition B over N rounds.
- Each round, the blend ratio shifts: round 1 is 80% A / 20% B, round N is 100% B.
- Actions from both games are available, weighted by the blend ratio.

### 5.2 Versioning

Every game definition has a `version` number. Governance changes increment the version. The event store records `game_definition.updated` events with the full new definition, so the history is auditable:

```
Round 1-18:  GameDefinition v1 (basketball)
Round 19:    Proposal passes: "Replace basketball with arm wrestling"
             GameDefinition v2 (arm wrestling) takes effect
Round 25:    Proposal passes: "Add a coin flip bonus round after each match"
             GameDefinition v3 (arm wrestling + coin flip mechanic)
```

---

## 6. How This Integrates with Current Code

### 6.1 Files That Get Replaced

| Current File | What It Does | Abstract Replacement |
|---|---|---|
| `core/simulation.py` | Top-level `simulate_game()` | `core/engine.py` -- `TurnExecutor.execute_contest()` |
| `core/possession.py` | `resolve_possession()` -- the atomic game unit | `core/turn.py` -- `_execute_turn()` dispatches by turn order |
| `core/scoring.py` | Logistic curves, shot probability, point values | `core/resolvers.py` -- `AttributeCheckResolver` (and others) |
| `core/defense.py` | Defensive scheme selection, matchups, contest modifiers | Folded into `DefenderContest` within `ResolutionDefinition` |
| `core/moves.py` | Special ability triggers and effects | Folded into `MechanicDefinition` within `GameDefinition` |
| `core/archetypes.py` | 9 archetype attribute templates | `core/archetypes.py` -- still exists but generates attributes using `AttributeDefinition` list |
| `core/state.py` | `GameState`, `HooperState`, `PossessionContext` | `core/contest_state.py` -- `ContestState`, `ParticipantState` |
| `models/rules.py` | `RuleSet` with 40 hardcoded parameters | `models/game_definition.py` -- `GameDefinition` with `ParameterDefinition` dict |
| `models/game.py` | `GameResult`, `PossessionLog`, `HooperBoxScore` | `models/contest.py` -- `ContestResult`, `TurnLog`, `ParticipantScore` |

### 6.2 Files That Get Refactored (But Not Replaced)

| Current File | What Changes |
|---|---|
| `core/game_loop.py` | `step_round()` calls `TurnExecutor.execute_contest()` instead of `simulate_game()`. The rest of the loop (governance, reports, events) stays the same. |
| `core/hooks.py` | Hook points expand from basketball-specific to generic (`sim.turn.pre` instead of `sim.possession.pre`). `RegisteredEffect` and `HookContext` gain a reference to `ContestState` instead of `GameState`. Backward-compatible aliases preserved. |
| `ai/interpreter.py` | `INTERPRETER_V2_SYSTEM_PROMPT` gets the game definition schema instead of just parameters. The AI produces `GameDefinitionPatch` operations for game-changing proposals. |
| `ai/commentary.py` | Commentary reads the game definition to know what actions mean. "Made a three-pointer" becomes "Won the coin flip" based on action definitions. |
| `ai/report.py` | Reports read the game definition to contextualize stats. |
| `db/models.py` | `GameRow.result` stores `ContestResult` (JSON). Schema is additive -- old basketball results remain readable. |
| `db/repository.py` | Query methods generalize: `get_hooper_season_stats` becomes `get_participant_season_stats` reading from the contest result's stat template. |
| `models/team.py` | `PlayerAttributes` stays for storage. `Hooper` gains optional `custom_attributes: dict[str, int]` for non-basketball games. |

### 6.3 Files That Stay Unchanged

| File | Why |
|---|---|
| `core/governance.py` | Governance is game-agnostic. Proposals, votes, tallying -- all unchanged. |
| `core/tokens.py` | Token economy is game-agnostic. |
| `core/scheduler.py` | Schedule generation is game-agnostic (teams play teams). |
| `core/scheduler_runner.py` | Cron-based advancement is game-agnostic. |
| `core/event_bus.py` | Event pub/sub is game-agnostic. |
| `core/effects.py` | Effect registry is game-agnostic. Effects fire at hook points regardless of game. |
| `core/meta.py` | MetaStore is already fully generic. |
| `discord/bot.py` | Discord commands are game-agnostic. `/propose`, `/vote`, `/strategy` all work the same. |
| `auth/` | Authentication is game-agnostic. |
| `api/` routes | All API routes are thin handlers. They call the same service layer. |
| `evals/` | Eval framework is game-agnostic (measures AI quality, not game mechanics). |
| All templates | Templates read from API data. If the data shape changes, templates adapt. |

---

## 7. The Interpreter DSL

### 7.1 GameDefinitionPatch

When the AI interprets a proposal that changes what the game IS (not just a parameter tweak), it produces a `GameDefinitionPatch`:

```python
class GameDefinitionPatch(BaseModel):
    """Operations to modify the game definition.

    Produced by the AI interpreter when a proposal goes beyond
    parameter changes. Applied atomically through governance.
    """

    operations: list[GameDefOperation] = Field(default_factory=list)
    attribute_mappings: list[AttributeMapping] = Field(default_factory=list)
    transition_type: str = "clean_break"
    narrative_instruction: str = ""


class GameDefOperation(BaseModel):
    """A single operation on the game definition."""

    op: str  # "add_action", "remove_action", "modify_action",
             # "add_attribute", "remove_attribute",
             # "set_turn_order", "add_phase", "remove_phase",
             # "set_win_condition", "add_mechanic", "remove_mechanic",
             # "set_parameter", "replace_all" (nuclear option)
    path: str = ""     # JSON path to target, e.g. "actions[0].resolution"
    value: dict | list | str | int | float | bool | None = None
```

### 7.2 How the AI Interpreter Handles Game-Changing Proposals

The v2 interpreter system prompt already encourages creative interpretation. The extension for game-changing proposals adds a new effect type:

```python
# New effect type in EffectSpec
class EffectSpec(BaseModel):
    effect_type: EffectType  # Now includes "game_definition_patch"

    # ... existing fields ...

    # game_definition_patch
    game_patch: GameDefinitionPatch | None = None
```

Example: A proposal says "Instead of basketball, let's play arm wrestling."

The AI produces:

```json
{
  "effects": [
    {
      "effect_type": "game_definition_patch",
      "game_patch": {
        "operations": [
          {
            "op": "replace_all",
            "value": {
              "name": "Arm Wrestling",
              "description": "Teams compete in arm wrestling matches.",
              "turn_order": "alternating",
              "attributes": [
                {"name": "strength", "category": "physical", "default_value": 50},
                {"name": "technique", "category": "physical", "default_value": 40},
                {"name": "endurance", "category": "physical", "default_value": 40},
                {"name": "intimidation", "category": "mental", "default_value": 30},
                {"name": "focus", "category": "mental", "default_value": 40}
              ],
              "actions": [
                {
                  "name": "power_push",
                  "resolution": {
                    "type": "comparison",
                    "attacker_attribute": "strength",
                    "defender_attribute": "endurance",
                    "modifier_attribute": "technique"
                  },
                  "outcomes": [
                    {"name": "advantage_gained", "trigger": "success", "state_changes": {"advantage": "+2"}},
                    {"name": "hold", "trigger": "failure", "state_changes": {"advantage": "+0"}},
                    {"name": "reversal", "trigger": "critical_failure", "state_changes": {"advantage": "-1"}}
                  ]
                }
              ],
              "win_condition": {
                "type": "first_to_threshold",
                "threshold": 10
              }
            }
          }
        ],
        "attribute_mappings": [
          {"source": "scoring", "target": "strength", "scale": 1.0},
          {"source": "defense", "target": "endurance", "scale": 1.0},
          {"source": "iq", "target": "technique", "scale": 0.8},
          {"source": "ego", "target": "intimidation", "scale": 1.0},
          {"source": "stamina", "target": "focus", "scale": 0.7}
        ],
        "transition_type": "clean_break",
        "narrative_instruction": "The league has voted to settle disputes the old-fashioned way. The basketball court transforms into an arm wrestling arena..."
      },
      "description": "Replace basketball with arm wrestling. Teams compete in direct strength contests."
    }
  ],
  "impact_analysis": "This completely replaces the basketball simulation with arm wrestling matches...",
  "confidence": 0.85
}
```

### 7.3 Incremental Changes vs. Full Replacement

Most proposals will NOT replace the entire game. They'll tweak it:

**"Make three-pointers worth 5 points"** -- a `parameter_change` effect. Same as today.

**"Add a lightning round where both teams play simultaneously"** -- a `game_definition_patch` that adds a new phase with `turn_order: simultaneous`.

**"If a team scores 10 in a row, they win instantly"** -- a `game_definition_patch` that adds a mechanic with a condition and a win-trigger outcome.

**"Replace jump shots with coin flips"** -- a `game_definition_patch` that modifies existing actions' resolution types from `attribute_check` to `random`.

The AI differentiates between "modify the game" and "replace the game" based on scope.

---

## 8. Scenario Walkthroughs

### 8.1 Basketball (Current Game, Data-Driven)

The default `GameDefinition` instance:

```python
BASKETBALL_DEFINITION = GameDefinition(
    name="3v3 Basketball",
    version=1,
    description="3-on-3 half-court basketball with Elam Ending",
    flavor_text="The ball bounces on hardwood. Sneakers squeak. The crowd holds its breath.",

    attributes=[
        AttributeDefinition(name="scoring", category="physical", default_value=40),
        AttributeDefinition(name="passing", category="physical", default_value=40),
        AttributeDefinition(name="defense", category="physical", default_value=40),
        AttributeDefinition(name="speed", category="physical", default_value=40),
        AttributeDefinition(name="stamina", category="physical", default_value=40, scales_with_stamina=False),
        AttributeDefinition(name="iq", category="mental", default_value=40, scales_with_stamina=False),
        AttributeDefinition(name="ego", category="meta", default_value=40, scales_with_stamina=False),
        AttributeDefinition(name="chaotic_alignment", category="meta", default_value=40, scales_with_stamina=False),
        AttributeDefinition(name="fate", category="meta", default_value=40, scales_with_stamina=False),
    ],
    attribute_budget=360,

    actions=[
        ActionDefinition(
            name="at_rim",
            display_name="At-Rim Shot",
            selection_weight=30.0,
            weight_attributes={"speed": 0.3},
            resolution=ResolutionDefinition(
                type=ResolutionType.ATTRIBUTE_CHECK,
                attribute="scoring",
                midpoint=30.0,
                steepness=0.05,
                stamina_factor=0.3,
                modifier_attributes={"iq": 0.1},
                defender_contest=DefenderContest(
                    attribute="defense",
                    impact_factor=0.005,
                ),
            ),
            outcomes=[
                OutcomeDefinition(name="made", trigger="success", score_change=2),
                OutcomeDefinition(name="missed", trigger="failure", triggers_action="rebound"),
            ],
            requires_opponent=True,
        ),
        ActionDefinition(
            name="mid_range",
            display_name="Mid-Range Shot",
            selection_weight=25.0,
            weight_attributes={"iq": 0.2},
            resolution=ResolutionDefinition(
                type=ResolutionType.ATTRIBUTE_CHECK,
                attribute="scoring",
                midpoint=40.0,
                steepness=0.045,
                stamina_factor=0.3,
                modifier_attributes={"iq": 0.1},
                defender_contest=DefenderContest(
                    attribute="defense",
                    impact_factor=0.005,
                ),
            ),
            outcomes=[
                OutcomeDefinition(name="made", trigger="success", score_change=2),
                OutcomeDefinition(name="missed", trigger="failure", triggers_action="rebound"),
            ],
            requires_opponent=True,
        ),
        ActionDefinition(
            name="three_point",
            display_name="Three-Pointer",
            selection_weight=20.0,
            weight_attributes={"scoring": 0.3},
            resolution=ResolutionDefinition(
                type=ResolutionType.ATTRIBUTE_CHECK,
                attribute="scoring",
                midpoint=50.0,
                steepness=0.04,
                stamina_factor=0.3,
                modifier_attributes={"iq": 0.1},
                defender_contest=DefenderContest(
                    attribute="defense",
                    impact_factor=0.005,
                ),
            ),
            outcomes=[
                OutcomeDefinition(name="made", trigger="success", score_change=3),
                OutcomeDefinition(name="missed", trigger="failure", triggers_action="rebound"),
            ],
            requires_opponent=True,
        ),
    ],

    turn_order=TurnOrder.ALTERNATING,
    phases=[
        PhaseDefinition(name="Quarter", count=3, clock_seconds=600.0, recovery={"stamina": 0.15}),
        PhaseDefinition(
            name="Elam Ending",
            count=1,
            transition_condition=ConditionExpression(checks={"phase_number_gte": 4}),
            narrative_instruction="The Elam Ending begins. Next score wins.",
        ),
    ],

    win_condition=WinCondition(
        type=WinConditionType.TARGET_SCORE_MARGIN,
        margin=15,
        safety_cap=300,
    ),

    participants_per_side=3,
    bench_size=1,
)
```

This produces *identical gameplay* to the current simulation -- the same logistic curves, the same attribute weights, the same Elam mechanics. The only difference is the data lives in a `GameDefinition` instead of being hardcoded.

### 8.2 Coin Flip Championship

```python
COIN_FLIP_DEFINITION = GameDefinition(
    name="Coin Flip Championship",
    version=1,
    description="Call it in the air. Pure luck. Pure drama.",
    flavor_text="The referee produces a gleaming coin. The crowd goes silent.",

    attributes=[],  # No attributes matter
    attribute_budget=0,

    actions=[
        ActionDefinition(
            name="call_heads",
            display_name="Call Heads",
            selection_weight=1.0,
            resolution=ResolutionDefinition(
                type=ResolutionType.RANDOM,
                probability=0.5,
            ),
            outcomes=[
                OutcomeDefinition(name="correct", trigger="success", score_change=1,
                                  narrative_template="{actor} calls heads... HEADS! +1!",
                                  energy_level="high"),
                OutcomeDefinition(name="wrong", trigger="failure", opponent_score_change=1,
                                  narrative_template="{actor} calls heads... tails. {opponent} gains a point.",
                                  energy_level="medium"),
            ],
        ),
        ActionDefinition(
            name="call_tails",
            display_name="Call Tails",
            selection_weight=1.0,
            resolution=ResolutionDefinition(
                type=ResolutionType.RANDOM,
                probability=0.5,
            ),
            outcomes=[
                OutcomeDefinition(name="correct", trigger="success", score_change=1),
                OutcomeDefinition(name="wrong", trigger="failure", opponent_score_change=1),
            ],
        ),
    ],

    turn_order=TurnOrder.ALTERNATING,
    phases=[
        PhaseDefinition(name="Round", count=None),  # Until win condition
    ],

    win_condition=WinCondition(
        type=WinConditionType.FIRST_TO_THRESHOLD,
        threshold=10,
        safety_cap=100,
    ),

    participants_per_side=1,  # 1v1 coin flips
    bench_size=0,
    substitution_allowed=False,
)
```

Key difference: **no attributes**. The resolution is pure random. This tests that the spine works when the attribute system is completely irrelevant.

### 8.3 Arm Wrestling League

```python
ARM_WRESTLING_DEFINITION = GameDefinition(
    name="Arm Wrestling League",
    version=1,
    description="Raw power meets technique in direct confrontation.",
    flavor_text="Chalk dust fills the air. Two competitors lock hands across the table.",

    attributes=[
        AttributeDefinition(name="strength", category="physical", default_value=50),
        AttributeDefinition(name="technique", category="physical", default_value=40),
        AttributeDefinition(name="endurance", category="physical", default_value=40),
        AttributeDefinition(name="intimidation", category="mental", default_value=30),
        AttributeDefinition(name="focus", category="mental", default_value=40),
    ],
    attribute_budget=200,

    actions=[
        ActionDefinition(
            name="power_push",
            display_name="Power Push",
            description="Brute force attempt to pin the opponent's arm.",
            selection_weight=1.5,
            weight_attributes={"strength": 0.5},
            resolution=ResolutionDefinition(
                type=ResolutionType.COMPARISON,
                attacker_attribute="strength",
                defender_attribute="endurance",
                modifier_attribute="technique",
            ),
            outcomes=[
                OutcomeDefinition(name="advantage_gained", trigger="success",
                                  state_changes={"advantage": "+2"},
                                  stamina_drain=0.08),
                OutcomeDefinition(name="hold", trigger="failure",
                                  state_changes={"advantage": "+0"},
                                  stamina_drain=0.05),
                OutcomeDefinition(name="reversal", trigger="critical_failure",
                                  state_changes={"advantage": "-1"},
                                  stamina_drain=0.03),
            ],
            requires_opponent=True,
            stamina_cost=0.05,
        ),
        ActionDefinition(
            name="technique_hold",
            display_name="Technique Hold",
            description="Use leverage and position to slowly gain advantage.",
            selection_weight=1.0,
            weight_attributes={"technique": 0.5, "focus": 0.3},
            resolution=ResolutionDefinition(
                type=ResolutionType.COMPARISON,
                attacker_attribute="technique",
                defender_attribute="technique",
                modifier_attribute="focus",
            ),
            outcomes=[
                OutcomeDefinition(name="advantage_gained", trigger="success",
                                  state_changes={"advantage": "+1"}),
                OutcomeDefinition(name="hold", trigger="failure"),
            ],
            requires_opponent=True,
            stamina_cost=0.02,
        ),
        ActionDefinition(
            name="psych_out",
            display_name="Psych Out",
            description="Intimidate the opponent to break their focus.",
            selection_weight=0.5,
            weight_attributes={"intimidation": 0.5},
            resolution=ResolutionDefinition(
                type=ResolutionType.COMPARISON,
                attacker_attribute="intimidation",
                defender_attribute="focus",
            ),
            outcomes=[
                OutcomeDefinition(
                    name="shaken", trigger="success",
                    state_changes={"advantage": "+1"},
                    attribute_modifier={"focus": -5},  # Temporary debuff
                    narrative_template="{actor} stares down {opponent}. {opponent} flinches.",
                    energy_level="high",
                ),
                OutcomeDefinition(name="unfazed", trigger="failure",
                                  narrative_template="{opponent} doesn't blink.",
                                  energy_level="low"),
            ],
            requires_opponent=True,
            stamina_cost=0.01,
            max_per_phase=2,  # Can't spam psych outs
        ),
    ],

    turn_order=TurnOrder.ALTERNATING,
    phases=[
        PhaseDefinition(name="Match", count=None, max_turns=50),
    ],

    win_condition=WinCondition(
        type=WinConditionType.FIRST_TO_THRESHOLD,
        threshold=10,  # Cumulative advantage reaches 10
        state_field="advantage",
        safety_cap=50,
    ),

    state_template=StateTemplate(
        contest_state={
            "advantage": StateFieldDefinition(
                name="advantage", type="int", default=0, display=True,
                display_name="Advantage",
            ),
        },
    ),

    participants_per_side=1,  # 1v1 matches
    bench_size=0,
)
```

Key differences: the `comparison` resolver, custom state (`advantage`), and the win condition checks a state field instead of score.

### 8.4 Jump Rope Marathon

```python
JUMP_ROPE_DEFINITION = GameDefinition(
    name="Jump Rope Marathon",
    version=1,
    description="Teams jump until they drop. Last team standing wins.",
    flavor_text="The rope whips through the air. Feet leave the ground in rhythm.",

    attributes=[
        AttributeDefinition(name="agility", category="physical", default_value=50),
        AttributeDefinition(name="stamina", category="physical", default_value=50,
                            scales_with_stamina=False),
        AttributeDefinition(name="rhythm", category="mental", default_value=40),
        AttributeDefinition(name="showmanship", category="meta", default_value=30),
    ],
    attribute_budget=170,

    actions=[
        ActionDefinition(
            name="basic_jump",
            display_name="Basic Jump",
            description="Keep the rhythm. Stay alive.",
            selection_weight=3.0,
            resolution=ResolutionDefinition(
                type=ResolutionType.DEGRADING,
                base_difficulty=0.05,
                increase_per_turn=0.015,
                degrading_attribute="stamina",
                max_difficulty=0.9,
            ),
            outcomes=[
                OutcomeDefinition(name="success", trigger="success",
                                  narrative_template="{actor} keeps jumping.",
                                  energy_level="low"),
                OutcomeDefinition(name="stumble", trigger="failure",
                                  state_changes={"strikes": "+1"},
                                  stamina_drain=0.1,
                                  narrative_template="{actor} stumbles! Strike {state.strikes}.",
                                  energy_level="high"),
            ],
        ),
        ActionDefinition(
            name="double_dutch",
            display_name="Double Dutch",
            description="Two ropes. Twice the risk. Twice the reward.",
            selection_weight=1.0,
            weight_attributes={"rhythm": 0.5},
            resolution=ResolutionDefinition(
                type=ResolutionType.DEGRADING,
                base_difficulty=0.15,
                increase_per_turn=0.02,
                degrading_attribute="agility",
                max_difficulty=0.95,
            ),
            outcomes=[
                OutcomeDefinition(name="success", trigger="success",
                                  score_change=2,
                                  narrative_template="{actor} nails double dutch! Crowd goes wild!",
                                  energy_level="high"),
                OutcomeDefinition(name="stumble", trigger="failure",
                                  state_changes={"strikes": "+2"},
                                  stamina_drain=0.15),
            ],
        ),
        ActionDefinition(
            name="trick_attempt",
            display_name="Trick Attempt",
            description="Flip, spin, dazzle the judges. Huge payoff if you land it.",
            selection_weight=0.5,
            weight_attributes={"showmanship": 0.5, "agility": 0.3},
            resolution=ResolutionDefinition(
                type=ResolutionType.ATTRIBUTE_CHECK,
                attribute="agility",
                midpoint=60.0,
                steepness=0.04,
                stamina_factor=0.4,
                modifier_attributes={"showmanship": 0.2},
            ),
            outcomes=[
                OutcomeDefinition(name="spectacular", trigger="success",
                                  score_change=5,
                                  narrative_template="{actor} LANDS THE TRICK! The crowd explodes!",
                                  energy_level="peak"),
                OutcomeDefinition(name="stumble", trigger="failure",
                                  state_changes={"strikes": "+1"},
                                  stamina_drain=0.2),
            ],
        ),
    ],

    turn_order=TurnOrder.PROGRESSIVE,  # Each team member acts until they fail
    phases=[
        PhaseDefinition(name="Round", count=None),
    ],

    win_condition=WinCondition(
        type=WinConditionType.LAST_STANDING,
        safety_cap=200,
    ),

    state_template=StateTemplate(
        participant_state={
            "strikes": StateFieldDefinition(
                name="strikes", type="int", default=0, max_value=3,
                display=True, display_name="Strikes",
            ),
        },
    ),

    participants_per_side=3,
    bench_size=0,
    substitution_allowed=False,
)
```

Key differences: `progressive` turn order (each participant acts until failure), `degrading` resolution (gets harder every turn), `last_standing` win condition (teams are eliminated when all participants hit 3 strikes).

### 8.5 Something Players Invent Mid-Season

**Proposal:** "I propose that instead of basketball, we play a trading card game where each hooper IS a card with attack/defense values."

The AI interpreter produces:

```json
{
  "effects": [
    {
      "effect_type": "game_definition_patch",
      "game_patch": {
        "operations": [
          {
            "op": "replace_all",
            "value": {
              "name": "Hooper Card Battles",
              "description": "Each hooper is a card. Teams play cards against each other.",
              "turn_order": "simultaneous",
              "attributes": [
                {"name": "attack", "category": "physical", "default_value": 50},
                {"name": "defense", "category": "physical", "default_value": 50},
                {"name": "speed", "category": "physical", "default_value": 40},
                {"name": "special", "category": "meta", "default_value": 30}
              ],
              "actions": [
                {
                  "name": "play_card",
                  "display_name": "Play Card",
                  "resolution": {
                    "type": "comparison",
                    "attacker_attribute": "attack",
                    "defender_attribute": "defense"
                  },
                  "outcomes": [
                    {"name": "wins_clash", "trigger": "success", "score_change": 1,
                     "eliminates_participant": true},
                    {"name": "loses_clash", "trigger": "failure",
                     "eliminates_participant": true}
                  ],
                  "requires_opponent": true
                },
                {
                  "name": "special_ability",
                  "display_name": "Special Ability",
                  "resolution": {
                    "type": "attribute_check",
                    "attribute": "special",
                    "midpoint": 60,
                    "steepness": 0.05
                  },
                  "outcomes": [
                    {"name": "activated", "trigger": "success", "score_change": 2,
                     "narrative_template": "{actor} activates their special power!"},
                    {"name": "fizzled", "trigger": "failure"}
                  ],
                  "available_when": {"checks": {"random_chance": 0.3}}
                }
              ],
              "phases": [
                {"name": "Battle Round", "count": null, "max_turns": 30}
              ],
              "win_condition": {
                "type": "last_standing",
                "safety_cap": 100
              },
              "participants_per_side": 3,
              "bench_size": 1
            }
          }
        ],
        "attribute_mappings": [
          {"source": "scoring", "target": "attack"},
          {"source": "defense", "target": "defense"},
          {"source": "speed", "target": "speed"},
          {"source": "fate", "target": "special"}
        ],
        "transition_type": "clean_break"
      },
      "description": "Transform the league into a card battle game where hoopers are cards."
    }
  ],
  "impact_analysis": "This completely transforms the game. Each hooper becomes a card with attack and defense values derived from their basketball attributes. Teams play cards simultaneously, with the stronger card winning each clash. This preserves the competitive structure while creating an entirely new game mechanic.",
  "confidence": 0.80
}
```

This is the nuclear option -- a `replace_all` operation. The governance system treats it like any other proposal: it goes to vote, needs to pass by the appropriate threshold (Tier 5+, 67% required), and if it passes, the game definition changes at the next round.

---

## 9. Implementation Phases

### Phase 1: Foundation (No Behavior Change)

**Goal:** Introduce `GameDefinition` as a data model alongside the existing simulation. Basketball is expressed as a `GameDefinition` instance but the existing code still runs.

**Files created:**
- `src/pinwheel/models/game_definition.py` -- All the Pydantic models above
- `src/pinwheel/core/resolvers.py` -- Resolution strategy implementations
- `src/pinwheel/core/contest_state.py` -- `ContestState` and `ParticipantState`

**Files modified:**
- `src/pinwheel/models/rules.py` -- Add `to_game_definition()` method that converts a `RuleSet` to a basketball `GameDefinition`
- `src/pinwheel/db/models.py` -- Add `game_definition_json` column to `SeasonRow`

**Tests:**
- Verify basketball `GameDefinition` produces identical RuleSet parameters
- Verify all 5 scenario definitions are valid Pydantic models
- Verify resolver implementations produce correct probability distributions

**What doesn't change:** The simulation still runs via the existing code paths. `GameDefinition` exists but isn't wired up.

### Phase 2: Turn Executor (Parallel Path)

**Goal:** Build `TurnExecutor` that can simulate any `GameDefinition`. Run it alongside the existing simulation for basketball and verify identical outcomes.

**Files created:**
- `src/pinwheel/core/engine.py` -- `TurnExecutor` class
- `src/pinwheel/core/turn.py` -- Turn execution by turn order type
- `src/pinwheel/models/contest.py` -- `ContestResult`, `TurnLog`

**Files modified:**
- `src/pinwheel/core/hooks.py` -- Add `ContestState` to `HookContext`, preserve backward compat

**Tests:**
- Run basketball through both the old simulation and new `TurnExecutor` with same seeds; verify statistically similar outcomes (exact match is not required due to RNG sequence differences, but distributions should match)
- Run all 5 scenarios through `TurnExecutor`; verify they produce reasonable results
- Fuzz: generate random `GameDefinition` instances, verify executor doesn't crash

### Phase 3: Cut Over (Basketball via Spine)

**Goal:** `step_round()` calls `TurnExecutor` instead of `simulate_game()`. Basketball results now flow through the generic spine.

**Files modified:**
- `src/pinwheel/core/game_loop.py` -- `simulate_game()` call replaced with `TurnExecutor.execute_contest()`
- `src/pinwheel/api/games.py` -- Read `ContestResult` instead of `GameResult`
- `src/pinwheel/ai/commentary.py` -- Read game definition for action names

**Files deprecated (not deleted):**
- `core/simulation.py`, `core/possession.py`, `core/scoring.py`, `core/defense.py`, `core/moves.py` -- Kept for reference but no longer called

**Tests:**
- Full integration test: seed, step 3 rounds, verify standings, box scores, and reports
- Verify commentary correctly references actions from the game definition
- Performance: verify the new path maintains <50ms per game

### Phase 4: Governance Integration

**Goal:** The AI interpreter can produce `GameDefinitionPatch` effects. Players can change what the game is.

**Files modified:**
- `src/pinwheel/ai/interpreter.py` -- Add game definition schema to v2 system prompt; add `game_definition_patch` effect type handling
- `src/pinwheel/models/governance.py` -- Add `game_definition_patch` to `EffectType`; add `GameDefinitionPatch` to `EffectSpec`
- `src/pinwheel/core/effects.py` -- Handle `game_definition_patch` effect registration and application
- `src/pinwheel/core/game_loop.py` -- Load active `GameDefinition` from event store at round start

**Tests:**
- Submit "make three-pointers worth 5" -- verify it uses the existing parameter_change path
- Submit "replace basketball with coin flipping" -- verify it produces a valid GameDefinitionPatch
- Submit "add a bonus round of arm wrestling after each game" -- verify it adds a phase without replacing everything
- Verify attribute mapping works when transitioning between games

### Phase 5: Polish and Drama

**Goal:** The game transition is visible, dramatic, and fun.

**Files modified:**
- `src/pinwheel/ai/report.py` -- Reports reflect the current game definition
- `src/pinwheel/ai/commentary.py` -- Commentary adapts to any game type
- Templates -- UI adapts to show game-appropriate stats and actions
- `src/pinwheel/discord/embeds.py` -- Discord embeds adapt to current game

**Features:**
- Transition narrative: when the game changes, the AI writes a dramatic transition report
- Game history: the season page shows which game was played in each round
- Stats contextualization: "points per game" becomes game-appropriate stats

---

## 10. What Stays Unchanged (The Invariants)

These are the architectural constants -- the things that make Pinwheel *Pinwheel* regardless of what game is being played:

1. **The governance loop:** Propose -> Vote -> Tally -> Enact. Always.
2. **The AI reporter:** Observe -> Describe -> Reflect. Never prescribe.
3. **Teams and hoopers:** Named entities with identities and stories.
4. **The Discord community:** Slash commands, embeds, DMs.
5. **The event store:** Append-only governance events.
6. **The effect registry:** Effects fire at hook points.
7. **The MetaStore:** Arbitrary state attached to entities.
8. **The scheduling system:** Rounds, seasons, playoffs.
9. **The token economy:** PROPOSE, AMEND, BOOST.
10. **The eval framework:** Measuring AI quality.

The game spine changes *what happens inside a contest*. Everything else -- the social structure, the governance system, the AI layer, the community -- wraps around whatever game the spine is running. That's the whole point: the game is just the substrate for governance. When players can change the substrate itself, governance becomes truly unlimited.

---

## Appendix A: The Basketball Compatibility Layer

For Phase 1-2, a compatibility layer translates between the old and new models:

```python
def ruleset_to_game_definition(rules: RuleSet) -> GameDefinition:
    """Convert current RuleSet to a basketball GameDefinition."""
    # ... maps each RuleSet field to the appropriate place in GameDefinition

def game_result_to_contest_result(result: GameResult) -> ContestResult:
    """Convert old GameResult to new ContestResult for unified storage."""

def contest_result_to_game_result(result: ContestResult) -> GameResult:
    """Convert back for backward-compatible API responses."""
```

## Appendix B: Safety Constraints

The `GameDefinition` schema has built-in safety:

- **Attribute values are bounded** (1-100).
- **Probabilities are bounded** (0.0-1.0).
- **Safety cap** prevents infinite games.
- **Expression evaluation is sandboxed** -- same safe evaluator as the existing effect system.
- **The AI cannot execute arbitrary code** -- it produces structured data that the spine interprets.
- **Admin veto** still works for wild game-changing proposals.
- **Versioning** means you can always roll back.

## Appendix C: Box Score Generalization

The current `HooperBoxScore` has basketball-specific fields (FG%, 3PT%, rebounds, assists). The abstract replacement:

```python
class ParticipantScore(BaseModel):
    """Per-participant stats for a contest. Fields are game-definition-driven."""

    participant_id: str
    participant_name: str
    team_id: str

    # Universal fields (always present)
    turns_played: int = 0
    stamina_remaining: float = 1.0

    # Game-specific stats (from StateTemplate.participant_state)
    stats: dict[str, int | float] = Field(default_factory=dict)
    # e.g. {"points": 15, "rebounds": 3} for basketball
    # or {"correct_calls": 7} for coin flip
    # or {"advantage_gained": 12, "reversals": 2} for arm wrestling

    # For backward compatibility, basketball stats are accessible as properties
    @property
    def points(self) -> int:
        return int(self.stats.get("points", 0))

    @property
    def rebounds(self) -> int:
        return int(self.stats.get("rebounds", 0))
```

## Appendix D: Key Design Decision -- Why Not a Full DSL?

An alternative approach would be a full domain-specific language where the AI writes actual simulation code. We explicitly reject this because:

1. **Security:** Executing AI-generated code is a hard no. The AI produces *data* that the spine interprets.
2. **Determinism:** The spine's resolvers are tested and deterministic. AI-generated code is unpredictable.
3. **Debuggability:** When a game produces weird results, you can inspect the `GameDefinition` JSON. You can't easily debug AI-generated code.
4. **Performance:** The resolvers are optimized Python. AI-generated code would need sandboxing overhead.
5. **The existing effect system already works this way.** The `action_code` primitives in `RegisteredEffect._apply_action_code` are the proof of concept. We're extending that pattern, not inventing a new one.

The `ExpressionResolver` is the one concession -- safe, bounded mathematical expressions for edge cases that the structured resolvers can't handle. But even these are evaluated in a sandboxed context, not `eval()`'d.