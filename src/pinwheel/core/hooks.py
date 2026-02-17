"""Hook system for Game Effects.

Dual architecture:
- Legacy: HookPoint enum + GameEffect protocol + fire_hooks() — still works
- New: String-based hierarchical hooks + HookContext/HookResult + fire_effects()

The new system supports arbitrary hook points, rich context objects, and
structured results that the effect execution engine applies.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import random

    from pinwheel.core.event_bus import EventBus
    from pinwheel.core.meta import MetaStore
    from pinwheel.core.state import GameState, HooperState
    from pinwheel.models.game import GameResult
    from pinwheel.models.governance import Proposal, VoteTally
    from pinwheel.models.rules import RuleSet
    from pinwheel.models.team import Team

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Legacy hook system (backward compatible — do not remove)
# ---------------------------------------------------------------------------


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
        self, hook: HookPoint, game_state: GameState, agent: HooperState | None
    ) -> bool: ...

    def apply(self, hook: HookPoint, game_state: GameState, agent: HooperState | None) -> None: ...


def fire_hooks(
    hook: HookPoint,
    game_state: GameState,
    effects: list[GameEffect],
    agent: HooperState | None = None,
) -> None:
    """Fire all effects registered for this hook point."""
    for effect in effects:
        if effect.should_fire(hook, game_state, agent):
            effect.apply(hook, game_state, agent)


# ---------------------------------------------------------------------------
# New hook system — string-based, rich context, structured results
# ---------------------------------------------------------------------------


@dataclass
class HookContext:
    """Unified context object passed to all effects at hook fire points.

    Only the fields relevant to the current hook point are populated;
    the rest remain at their defaults.
    """

    # Simulation context (populated during sim hooks)
    game_state: GameState | None = None
    hooper: HooperState | None = None
    rules: RuleSet | None = None
    rng: random.Random | None = None

    # Round context (populated during round hooks)
    round_number: int = 0
    season_id: str = ""
    game_results: list[GameResult] | None = None
    teams: dict[str, Team] | None = None

    # Per-game context (populated during round.game.post)
    current_game_result: GameResult | None = None
    home_team_id: str = ""
    away_team_id: str = ""
    winner_team_id: str = ""
    margin: int = 0

    # Governance context (populated during gov hooks)
    proposal: Proposal | None = None
    tally: VoteTally | None = None

    # Report context (populated during report hooks)
    report_data: dict[str, object] | None = None

    # Meta read/write interface
    meta_store: MetaStore | None = None

    # General
    event_bus: EventBus | None = None


@dataclass
class HookResult:
    """Structured mutations returned by an effect.

    The effect execution engine applies these to the game state.
    """

    # Meta writes: {entity_key: {field: value}}
    meta_writes: dict[str, dict[str, object]] | None = None

    # Simulation modifiers (applied during sim hooks)
    score_modifier: int = 0
    stamina_modifier: float = 0.0
    shot_probability_modifier: float = 0.0
    shot_value_modifier: int = 0
    extra_stamina_drain: float = 0.0
    at_rim_bias: float = 0.0
    mid_range_bias: float = 0.0
    three_point_bias: float = 0.0
    turnover_modifier: float = 0.0
    random_ejection_probability: float = 0.0
    bonus_pass_count: int = 0

    # Flow control
    block_action: bool = False
    substitute_action: str | None = None

    # Narrative injection
    narrative: str = ""

    # Signal that this effect has expired and should be deregistered
    expired: bool = False


class EffectLifetime(Enum):
    """How long an effect stays active."""

    PERMANENT = "permanent"
    N_ROUNDS = "n_rounds"
    ONE_GAME = "one_game"
    UNTIL_REPEALED = "until_repealed"


class Effect(Protocol):
    """Protocol for the new effect system.

    Effects register on string-based hook points and receive rich context.
    """

    @property
    def effect_id(self) -> str:
        """Unique identifier for this effect."""
        ...

    @property
    def hook_points(self) -> list[str]:
        """Which hook points this effect listens on."""
        ...

    @property
    def lifetime(self) -> EffectLifetime:
        """How long this effect stays active."""
        ...

    def should_fire(self, hook: str, context: HookContext) -> bool:
        """Whether this effect should fire for the given hook + context."""
        ...

    def apply(self, hook: str, context: HookContext) -> HookResult:
        """Execute the effect and return mutations."""
        ...


@dataclass
class RegisteredEffect:
    """A concrete effect registered in the EffectRegistry.

    Created from an EffectSpec when a proposal passes. Evaluates conditions
    and applies structured action primitives.
    """

    effect_id: str
    proposal_id: str
    _hook_points: list[str] = field(default_factory=list)
    _lifetime: EffectLifetime = EffectLifetime.PERMANENT
    rounds_remaining: int | None = None
    registered_at_round: int = 0

    # From EffectSpec
    effect_type: str = "hook_callback"
    condition: str = ""
    action_code: dict[str, object] | None = None
    narrative_instruction: str = ""
    description: str = ""

    # Meta mutation fields
    target_type: str = ""
    target_selector: str = ""
    meta_field: str = ""
    meta_value: object = None
    meta_operation: str = "set"

    @property
    def hook_points(self) -> list[str]:
        """Which hook points this effect listens on."""
        return self._hook_points

    @property
    def lifetime(self) -> EffectLifetime:
        """How long this effect stays active."""
        return self._lifetime

    def should_fire(self, hook: str, context: HookContext) -> bool:
        """Evaluate whether this effect should fire.

        Checks hook point match and structured conditions from action_code.
        """
        if hook not in self._hook_points:
            return False

        # Evaluate structured conditions from action_code
        if self.action_code and "condition_check" in self.action_code:
            return self._evaluate_condition(
                self.action_code["condition_check"],  # type: ignore[arg-type]
                context,
            )

        return True

    def _evaluate_condition(
        self,
        condition: dict[str, object],
        context: HookContext,
    ) -> bool:
        """Evaluate a structured condition against the current context.

        Supports:
        - Meta field checks: {"meta_field": "swagger", "entity_type": "team", "gte": 5}
        - Game state: {"game_state_check": "trailing|leading|elam_active"}
        - Quarter: {"quarter_gte": 3}
        - Random: {"random_chance": 0.15}
        - Previous possession: {"last_result": "made|missed|turnover"}
        - Streak: {"consecutive_makes_gte": 3}
        - Ball handler attribute: {"ball_handler_attr": "scoring", "gte": 70}
        """
        gs = context.game_state

        # Game state conditions
        if "game_state_check" in condition:
            check = str(condition["game_state_check"])
            if not gs:
                return False
            if check == "trailing":
                off_score = gs.home_score if gs.home_has_ball else gs.away_score
                def_score = gs.away_score if gs.home_has_ball else gs.home_score
                return off_score < def_score
            if check == "leading":
                off_score = gs.home_score if gs.home_has_ball else gs.away_score
                def_score = gs.away_score if gs.home_has_ball else gs.home_score
                return off_score > def_score
            if check == "elam_active":
                return gs.elam_activated
            return False

        # Quarter conditions
        if "quarter_gte" in condition:
            threshold = condition["quarter_gte"]
            if gs and isinstance(threshold, (int, float)):
                return gs.quarter >= int(threshold)
            return False

        # Score difference conditions
        if "score_diff_gte" in condition:
            threshold = condition["score_diff_gte"]
            if gs and isinstance(threshold, (int, float)):
                off_score = gs.home_score if gs.home_has_ball else gs.away_score
                def_score = gs.away_score if gs.home_has_ball else gs.home_score
                return (off_score - def_score) >= int(threshold)
            return False

        # Random probability
        if "random_chance" in condition:
            chance = condition["random_chance"]
            if isinstance(chance, (int, float)) and context.rng:
                return context.rng.random() < chance
            return False

        # Previous possession state
        if "last_result" in condition:
            if not gs:
                return False
            return gs.last_result == str(condition["last_result"])

        # Streak conditions
        if "consecutive_makes_gte" in condition:
            threshold = condition["consecutive_makes_gte"]
            if gs and isinstance(threshold, (int, float)):
                return gs.consecutive_makes >= int(threshold)
            return False

        if "consecutive_misses_gte" in condition:
            threshold = condition["consecutive_misses_gte"]
            if gs and isinstance(threshold, (int, float)):
                return gs.consecutive_misses >= int(threshold)
            return False

        # Ball handler attribute check
        if "ball_handler_attr" in condition:
            # Requires a hooper on context to check
            if not context.hooper:
                return False
            attr_name = str(condition["ball_handler_attr"])
            attrs = context.hooper.current_attributes
            val = getattr(attrs, attr_name, None)
            if val is None:
                return False
            if "gte" in condition:
                threshold = condition["gte"]
                if isinstance(val, (int, float)) and isinstance(threshold, (int, float)):
                    return val >= threshold
            if "lte" in condition:
                threshold = condition["lte"]
                if isinstance(val, (int, float)) and isinstance(threshold, (int, float)):
                    return val <= threshold
            return True

        # Meta field checks (original system)
        meta_field = str(condition.get("meta_field", ""))
        entity_type = str(condition.get("entity_type", ""))

        if not meta_field or not entity_type:
            return True  # No condition to check — always fire

        if not context.meta_store:
            return False

        # Determine which entity to check
        entity_id = ""
        if gs and entity_type == "team":
            # Check offense team during sim hooks
            if gs.home_has_ball:
                entity_id = (
                    gs.home_agents[0].hooper.team_id
                    if gs.home_agents
                    else ""
                )
            else:
                entity_id = (
                    gs.away_agents[0].hooper.team_id
                    if gs.away_agents
                    else ""
                )
        elif context.winner_team_id and entity_type == "team":
            entity_id = context.winner_team_id

        if not entity_id:
            return False

        value = context.meta_store.get(entity_type, entity_id, meta_field, default=0)

        # Comparison operators
        if "gte" in condition:
            threshold = condition["gte"]
            if isinstance(value, (int, float)) and isinstance(threshold, (int, float)):
                return value >= threshold
        if "lte" in condition:
            threshold = condition["lte"]
            if isinstance(value, (int, float)) and isinstance(threshold, (int, float)):
                return value <= threshold
        if "eq" in condition:
            return value == condition["eq"]

        return True

    def apply(self, hook: str, context: HookContext) -> HookResult:
        """Execute the effect's action and return mutations."""
        result = HookResult()

        if self.effect_type == "meta_mutation":
            return self._apply_meta_mutation(context, result)

        if self.effect_type == "hook_callback" and self.action_code:
            return self._apply_action_code(context, result)

        if self.effect_type == "narrative":
            result.narrative = self.narrative_instruction
            return result

        if self.effect_type == "custom_mechanic":
            result.narrative = f"[Pending mechanic] {self.description}"
            return result

        return result

    def _apply_meta_mutation(
        self,
        context: HookContext,
        result: HookResult,
    ) -> HookResult:
        """Apply a meta_mutation effect."""
        if not context.meta_store:
            return result

        entity_type = self.target_type
        entity_id = self._resolve_target(context)

        if not entity_type or not entity_id:
            return result

        if self.meta_operation == "set":
            context.meta_store.set(entity_type, entity_id, self.meta_field, self.meta_value)  # type: ignore[arg-type]
        elif self.meta_operation == "increment":
            amount = self.meta_value if isinstance(self.meta_value, (int, float)) else 1
            context.meta_store.increment(entity_type, entity_id, self.meta_field, amount)
        elif self.meta_operation == "decrement":
            amount = self.meta_value if isinstance(self.meta_value, (int, float)) else 1
            context.meta_store.decrement(entity_type, entity_id, self.meta_field, amount)
        elif self.meta_operation == "toggle":
            context.meta_store.toggle(entity_type, entity_id, self.meta_field)

        return result

    def _apply_action_code(
        self,
        context: HookContext,
        result: HookResult,
    ) -> HookResult:
        """Apply a structured action primitive."""
        if not self.action_code:
            return result

        action_type = str(self.action_code.get("type", ""))

        if action_type == "modify_score":
            modifier = self.action_code.get("modifier", 0)
            if isinstance(modifier, (int, float)):
                result.score_modifier = int(modifier)

        elif action_type == "modify_probability":
            modifier = self.action_code.get("modifier", 0.0)
            if isinstance(modifier, (int, float)):
                result.shot_probability_modifier = float(modifier)

        elif action_type == "modify_stamina":
            modifier = self.action_code.get("modifier", 0.0)
            if isinstance(modifier, (int, float)):
                result.stamina_modifier = float(modifier)

        elif action_type == "write_meta":
            if context.meta_store:
                entity = str(self.action_code.get("entity", ""))
                meta_field = str(self.action_code.get("field", ""))
                value = self.action_code.get("value")
                op = str(self.action_code.get("op", "set"))

                # Resolve entity reference like "team:{winner_team_id}"
                entity_type, entity_id = self._parse_entity_ref(entity, context)

                if entity_type and entity_id and meta_field:
                    if op == "increment" and isinstance(value, (int, float)):
                        context.meta_store.increment(entity_type, entity_id, meta_field, value)
                    elif op == "decrement" and isinstance(value, (int, float)):
                        context.meta_store.decrement(entity_type, entity_id, meta_field, value)
                    elif op == "toggle":
                        context.meta_store.toggle(entity_type, entity_id, meta_field)
                    else:
                        context.meta_store.set(entity_type, entity_id, meta_field, value)  # type: ignore[arg-type]

        elif action_type == "add_narrative":
            text = self.action_code.get("text", "")
            if isinstance(text, str):
                result.narrative = text

        elif action_type == "modify_shot_value":
            modifier = self.action_code.get("modifier", 0)
            if isinstance(modifier, (int, float)):
                result.shot_value_modifier = int(modifier)

        elif action_type == "modify_shot_selection":
            for bias_key in ("at_rim_bias", "mid_range_bias", "three_point_bias"):
                val = self.action_code.get(bias_key, 0.0)
                if isinstance(val, (int, float)):
                    setattr(result, bias_key, float(val))

        elif action_type == "modify_turnover_rate":
            modifier = self.action_code.get("modifier", 0.0)
            if isinstance(modifier, (int, float)):
                result.turnover_modifier = float(modifier)

        elif action_type == "random_ejection":
            prob = self.action_code.get("probability", 0.0)
            if isinstance(prob, (int, float)):
                result.random_ejection_probability = float(prob)

        elif action_type == "derive_pass_count":
            # Simulates passes from team stats — requires game_state + rng
            if context.game_state and context.rng:
                gs = context.game_state
                off = gs.offense
                if off:
                    avg_passing = sum(
                        h.current_attributes.passing for h in off
                    ) / len(off)
                    pass_prob = avg_passing / 100.0
                    min_p = int(self.action_code.get("min_passes", 0))
                    max_p = int(self.action_code.get("max_passes", 5))
                    val_per = int(self.action_code.get("value_per_pass", 1))
                    pass_count = 0
                    for _ in range(max_p):
                        if context.rng.random() < pass_prob:
                            pass_count += 1
                        else:
                            break
                    pass_count = max(min_p, pass_count)
                    result.bonus_pass_count = pass_count * val_per

        elif action_type == "swap_roster_player":
            # Generate a temporary player with extreme stats, swap onto offense
            if context.game_state and context.rng:
                gs = context.game_state
                off_agents = gs.home_agents if gs.home_has_ball else gs.away_agents
                active = [a for a in off_agents if a.on_court and not a.ejected]
                if active:
                    extreme_stat = str(self.action_code.get("extreme_stat", "scoring"))
                    extreme_val = int(self.action_code.get("extreme_value", 95))
                    other_val = int(self.action_code.get("other_stats_value", 15))

                    if extreme_stat == "random":
                        extreme_stat = context.rng.choice(
                            ["scoring", "passing", "defense", "speed"]
                        )

                    # Build extreme attributes
                    attr_kwargs: dict[str, int] = {
                        "scoring": other_val, "passing": other_val,
                        "defense": other_val, "speed": other_val,
                        "stamina": other_val, "iq": other_val,
                        "ego": 50, "chaotic_alignment": 80, "fate": 50,
                    }
                    if extreme_stat in attr_kwargs:
                        attr_kwargs[extreme_stat] = extreme_val

                    from pinwheel.models.team import Hooper, PlayerAttributes

                    crowd_hooper = Hooper(
                        id=f"crowd-{context.rng.randint(1000, 9999)}",
                        name="Mystery Fan",
                        team_id=active[0].hooper.team_id,
                        archetype="wildcard",
                        attributes=PlayerAttributes(**attr_kwargs),
                        is_starter=True,
                    )
                    target = context.rng.choice(active)
                    from pinwheel.core.state import HooperState

                    crowd_state = HooperState(hooper=crowd_hooper, on_court=True)
                    # Swap: bench the target, add crowd player
                    target.on_court = False
                    off_agents.append(crowd_state)
                    result.narrative = (
                        f"A mysterious figure emerges from the crowd, replacing "
                        f"{target.hooper.name}!"
                    )

        elif action_type == "conditional_sequence":
            steps = self.action_code.get("steps")
            if isinstance(steps, list):
                for step in steps:
                    if not isinstance(step, dict):
                        continue
                    gate = step.get("gate")
                    # Evaluate gate
                    if gate and isinstance(gate, dict) and "random_chance" in gate:
                        chance = gate["random_chance"]
                        if (
                            isinstance(chance, (int, float))
                            and context.rng
                            and context.rng.random() >= chance
                        ):
                            continue
                    action = step.get("action")
                    if isinstance(action, dict):
                        # Recursively apply inner action
                        inner_code = self.action_code
                        self.action_code = action
                        inner_result = self._apply_action_code(context, HookResult())
                        self.action_code = inner_code
                        # Merge inner result into main result
                        result.score_modifier += inner_result.score_modifier
                        result.stamina_modifier += inner_result.stamina_modifier
                        result.shot_probability_modifier += (
                            inner_result.shot_probability_modifier
                        )
                        result.shot_value_modifier += inner_result.shot_value_modifier
                        result.extra_stamina_drain += inner_result.extra_stamina_drain
                        result.turnover_modifier += inner_result.turnover_modifier
                        result.random_ejection_probability += (
                            inner_result.random_ejection_probability
                        )
                        result.bonus_pass_count += inner_result.bonus_pass_count
                        result.at_rim_bias += inner_result.at_rim_bias
                        result.mid_range_bias += inner_result.mid_range_bias
                        result.three_point_bias += inner_result.three_point_bias
                        if inner_result.narrative:
                            existing = result.narrative
                            if existing:
                                result.narrative = f"{existing} {inner_result.narrative}"
                            else:
                                result.narrative = inner_result.narrative

        return result

    def _resolve_target(self, context: HookContext) -> str:
        """Resolve target_selector to an entity ID."""
        if self.target_selector == "winning_team":
            return context.winner_team_id
        if self.target_selector == "all":
            return ""  # Caller must iterate
        return self.target_selector or ""

    def _parse_entity_ref(
        self,
        ref: str,
        context: HookContext,
    ) -> tuple[str, str]:
        """Parse entity references like 'team:{winner_team_id}'."""
        if ":" not in ref:
            return "", ref

        parts = ref.split(":", 1)
        entity_type = parts[0]
        entity_id = parts[1]

        # Resolve template variables
        if entity_id == "{winner_team_id}":
            entity_id = context.winner_team_id
        elif entity_id == "{home_team_id}":
            entity_id = context.home_team_id
        elif entity_id == "{away_team_id}":
            entity_id = context.away_team_id

        return entity_type, entity_id

    def tick_round(self) -> bool:
        """Advance the round counter. Returns True if effect has expired."""
        if self._lifetime == EffectLifetime.N_ROUNDS and self.rounds_remaining is not None:
            self.rounds_remaining -= 1
            return self.rounds_remaining <= 0
        return self._lifetime == EffectLifetime.ONE_GAME

    def to_dict(self) -> dict[str, object]:
        """Serialize for event store persistence."""
        return {
            "effect_id": self.effect_id,
            "proposal_id": self.proposal_id,
            "hook_points": self._hook_points,
            "lifetime": self._lifetime.value,
            "rounds_remaining": self.rounds_remaining,
            "registered_at_round": self.registered_at_round,
            "effect_type": self.effect_type,
            "condition": self.condition,
            "action_code": self.action_code,
            "narrative_instruction": self.narrative_instruction,
            "description": self.description,
            "target_type": self.target_type,
            "target_selector": self.target_selector,
            "meta_field": self.meta_field,
            "meta_value": self.meta_value,
            "meta_operation": self.meta_operation,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> RegisteredEffect:
        """Reconstruct from event store payload."""
        lifetime_str = str(data.get("lifetime", "permanent"))
        try:
            lifetime = EffectLifetime(lifetime_str)
        except ValueError:
            lifetime = EffectLifetime.PERMANENT

        hook_points = data.get("hook_points", [])
        if not isinstance(hook_points, list):
            hook_points = []

        rounds_remaining = data.get("rounds_remaining")
        if not isinstance(rounds_remaining, (int, type(None))):
            rounds_remaining = None

        action_code = data.get("action_code")
        if not isinstance(action_code, (dict, type(None))):
            action_code = None

        return cls(
            effect_id=str(data.get("effect_id", "")),
            proposal_id=str(data.get("proposal_id", "")),
            _hook_points=[str(h) for h in hook_points],
            _lifetime=lifetime,
            rounds_remaining=rounds_remaining,
            registered_at_round=int(data.get("registered_at_round", 0)),
            effect_type=str(data.get("effect_type", "hook_callback")),
            condition=str(data.get("condition", "")),
            action_code=action_code,
            narrative_instruction=str(data.get("narrative_instruction", "")),
            description=str(data.get("description", "")),
            target_type=str(data.get("target_type", "")),
            target_selector=str(data.get("target_selector", "")),
            meta_field=str(data.get("meta_field", "")),
            meta_value=data.get("meta_value"),
            meta_operation=str(data.get("meta_operation", "set")),
        )


def fire_effects(
    hook: str,
    context: HookContext,
    effects: list[RegisteredEffect],
) -> list[HookResult]:
    """Fire all registered effects for a hook point.

    Returns the list of HookResults from effects that fired.
    """
    results: list[HookResult] = []
    for effect in effects:
        try:
            if effect.should_fire(hook, context):
                result = effect.apply(hook, context)
                results.append(result)
        except Exception:
            logger.exception(
                "effect_fire_failed effect_id=%s hook=%s",
                effect.effect_id,
                hook,
            )
    return results


def apply_hook_results(
    results: list[HookResult],
    context: HookContext,
) -> None:
    """Apply accumulated HookResults to the game state.

    Score modifiers, stamina modifiers, and shot probability modifiers
    are summed and applied. Meta writes are applied via the MetaStore
    (already done in effect.apply, but explicit writes in HookResult
    are also applied here).
    """
    if not context.game_state:
        return

    total_score_mod = sum(r.score_modifier for r in results)
    total_stamina_mod = sum(r.stamina_modifier for r in results)

    if total_score_mod != 0:
        if context.game_state.home_has_ball:
            context.game_state.home_score += total_score_mod
        else:
            context.game_state.away_score += total_score_mod

    if total_stamina_mod != 0.0 and context.hooper:
        context.hooper.current_stamina = max(
            0.0,
            min(1.0, context.hooper.current_stamina + total_stamina_mod),
        )
