"""Hook system for Game Effects.

Dual architecture:
- Legacy: HookPoint enum + GameEffect protocol + fire_hooks() — still works
- New: String-based hierarchical hooks + HookContext/HookResult + fire_effects()

The new system supports arbitrary hook points, rich context objects, and
structured results that the effect execution engine applies.
"""

from __future__ import annotations

import dataclasses
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

    Action selection biases can be set via the three legacy fields
    (``at_rim_bias``, ``mid_range_bias``, ``three_point_bias``) OR via
    the ``action_biases`` dict for arbitrary action names.  Both paths
    are additive — ``_fire_sim_effects`` merges them together.
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
    action_biases: dict[str, float] = field(default_factory=dict)
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

    # Codegen fields (populated when effect_type == "codegen")
    codegen_code: str | None = None
    codegen_code_hash: str | None = None
    codegen_trust_level: str | None = None  # CodegenTrustLevel value string
    codegen_enabled: bool = True
    codegen_disabled_reason: str = ""
    codegen_execution_count: int = 0
    codegen_error_count: int = 0
    codegen_consecutive_errors: int = 0
    codegen_last_error: str = ""

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

    def _build_eval_context(self, context: HookContext) -> dict[str, object]:
        """Build a flat evaluation namespace from all available context.

        All scalar GameState fields are automatically included via reflection —
        no code change needed when new fields are added to GameState. Computed
        aliases (shot_zone, trailing, leading, score_diff) provide the AI with
        natural vocabulary without adding evaluator branches.
        """
        ctx: dict[str, object] = {}
        gs = context.game_state

        if gs:
            # All scalar GameState fields — automatically, via reflection
            for f in dataclasses.fields(gs):
                val = getattr(gs, f.name)
                if isinstance(val, (str, int, float, bool, type(None))):
                    ctx[f.name] = val

            # Semantic aliases — vocabulary exposed to the AI interpreter
            ctx["shot_zone"] = gs.last_action  # "at_rim" | "mid_range" | "three_point"
            off = gs.home_score if gs.home_has_ball else gs.away_score
            def_ = gs.away_score if gs.home_has_ball else gs.home_score
            ctx["score_diff"] = off - def_  # positive = offense leading
            ctx["trailing"] = off < def_
            ctx["leading"] = off > def_

        if context.hooper:
            # All hooper attributes — model_dump() works for Pydantic models
            for attr_name, val in context.hooper.current_attributes.model_dump().items():
                if isinstance(val, (str, int, float, bool)):
                    ctx[f"hooper_{attr_name}"] = val

        return ctx

    def _evaluate_condition(
        self,
        condition: dict[str, object],
        context: HookContext,
    ) -> bool:
        """Generic condition evaluator — no per-field branches.

        Conditions are field expressions evaluated against a unified context
        built from GameState (via reflection) plus semantic aliases. Any field
        present in GameState is usable without a code change.

        Supported patterns:
        - Equality:      {"last_result": "made"}, {"shot_zone": "at_rim"}
        - Suffix ops:    {"quarter_gte": 3}, {"score_diff_lte": -5}
        - Random:        {"random_chance": 0.15}   (only true special case)
        - Meta store:    {"meta_field": "swagger", "entity_type": "team", "gte": 5}
        """
        # --- Special case: random probability (not a field, generates a value) ---
        if "random_chance" in condition:
            chance = condition["random_chance"]
            if not (isinstance(chance, (int, float)) and context.rng):
                return False
            return context.rng.random() < chance

        # --- Special case: meta store (external state, not in GameState) ---
        if "meta_field" in condition:
            return self._evaluate_meta_condition(condition, context)

        # --- Generic: field expressions against unified context ---
        ctx = self._build_eval_context(context)

        for key, expected in condition.items():
            # Suffix operators: field_gte → field >= value, field_lte → field <= value
            if key.endswith("_gte"):
                actual = ctx.get(key[:-4])
                if actual is None or not (actual >= expected):  # type: ignore[operator]
                    return False
            elif key.endswith("_lte"):
                actual = ctx.get(key[:-4])
                if actual is None or not (actual <= expected):  # type: ignore[operator]
                    return False
            else:
                # Direct equality — unknown fields pass (forward compatibility)
                actual = ctx.get(key)
                if actual is not None and actual != expected:
                    return False

        return True

    def _evaluate_meta_condition(
        self,
        condition: dict[str, object],
        context: HookContext,
    ) -> bool:
        """Evaluate a meta store condition (external state lookup).

        Special-cased because meta store is not part of GameState — it holds
        player-defined counters and flags persisted across possessions.
        Format: {"meta_field": "swagger", "entity_type": "team", "gte": 5}
        """
        if not context.meta_store:
            return False

        meta_field = str(condition.get("meta_field", ""))
        entity_type = str(condition.get("entity_type", ""))
        if not meta_field or not entity_type:
            return True

        gs = context.game_state
        entity_id = ""
        if gs and entity_type == "team":
            if gs.home_has_ball:
                entity_id = gs.home_agents[0].hooper.team_id if gs.home_agents else ""
            else:
                entity_id = gs.away_agents[0].hooper.team_id if gs.away_agents else ""
        elif context.winner_team_id and entity_type == "team":
            entity_id = context.winner_team_id

        if not entity_id:
            return False

        value = context.meta_store.get(entity_type, entity_id, meta_field, default=0)

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

        if self.effect_type == "codegen":
            return self._fire_codegen(context) or result

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

    def _fire_codegen(self, context: HookContext) -> HookResult | None:
        """Execute generated code in sandbox, return standard HookResult."""
        if not self.codegen_code or not self.codegen_code_hash:
            return None

        if not self.codegen_enabled:
            return None

        from pinwheel.core.codegen import (
            SandboxViolation,
            clamp_result,
            enforce_trust_level,
            execute_codegen_effect,
            verify_code_integrity,
        )
        from pinwheel.models.codegen import CodegenTrustLevel

        # Verify code integrity
        if not verify_code_integrity(self.codegen_code, self.codegen_code_hash):
            self._disable_codegen("Code integrity check failed")
            return None

        # Build sandboxed GameContext from HookContext
        trust = (
            CodegenTrustLevel(self.codegen_trust_level)
            if self.codegen_trust_level
            else CodegenTrustLevel.NUMERIC
        )
        game_ctx = _build_game_context(context, trust)

        rng = context.rng
        if rng is None:
            import random as _random_mod
            rng = _random_mod.Random()

        try:
            codegen_result = execute_codegen_effect(self.codegen_code, game_ctx, rng)
            # Enforce trust level
            codegen_result = enforce_trust_level(codegen_result, trust)
            codegen_result = clamp_result(codegen_result)
            self._record_codegen_success()
            return _codegen_result_to_hook_result(codegen_result)

        except SandboxViolation as e:
            self._record_codegen_error(f"Sandbox violation: {e.violation_type}: {e.detail}")
            self._disable_codegen(f"Sandbox violation: {e.violation_type}")
            return None

        except TimeoutError:
            self._record_codegen_error("Execution timeout (>1s)")
            self._disable_codegen("Execution timeout")
            return None

        except Exception:  # noqa: BLE001 — catch-all for sandbox safety
            import traceback
            err = traceback.format_exc()
            self._record_codegen_error(err[:200])
            # Auto-disable after 3 consecutive errors
            if self.codegen_consecutive_errors >= 3:
                self._disable_codegen(
                    f"Auto-disabled after {self.codegen_consecutive_errors} errors"
                )
            return None

    def _record_codegen_success(self) -> None:
        """Record successful codegen execution."""
        self.codegen_execution_count += 1
        self.codegen_consecutive_errors = 0

    def _record_codegen_error(self, error: str) -> None:
        """Record codegen execution error."""
        self.codegen_error_count += 1
        self.codegen_consecutive_errors += 1
        self.codegen_last_error = error
        logger.warning(
            "codegen_execution_error effect_id=%s errors=%d error=%s",
            self.effect_id,
            self.codegen_consecutive_errors,
            error[:100],
        )

    def _disable_codegen(self, reason: str) -> None:
        """Kill switch — immediately disable this codegen effect."""
        self.codegen_enabled = False
        self.codegen_disabled_reason = reason
        logger.warning(
            "codegen_disabled effect_id=%s reason=%s",
            self.effect_id,
            reason,
        )

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

    # Maximum nesting depth for conditional_sequence recursion.
    # Prevents infinite recursion from malicious or runaway effect chains.
    MAX_EFFECT_CHAIN_DEPTH = 3

    def _apply_action_code(
        self,
        context: HookContext,
        result: HookResult,
        _depth: int = 0,
    ) -> HookResult:
        """Apply a structured action primitive.

        The ``_depth`` parameter tracks recursion depth for ``conditional_sequence``
        actions. Exceeding ``MAX_EFFECT_CHAIN_DEPTH`` suppresses further nested
        actions to prevent runaway or malicious effect chains.
        """
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
            # Write biases to action_biases dict (primary interface).
            # Legacy field names (at_rim_bias, mid_range_bias, three_point_bias)
            # are mapped to action names (at_rim, mid_range, three_point).
            _bias_map = {
                "at_rim_bias": "at_rim",
                "mid_range_bias": "mid_range",
                "three_point_bias": "three_point",
            }
            for bias_key, action_name in _bias_map.items():
                val = self.action_code.get(bias_key, 0.0)
                if isinstance(val, (int, float)) and val != 0.0:
                    result.action_biases[action_name] = (
                        result.action_biases.get(action_name, 0.0) + float(val)
                    )

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

                    from pinwheel.models.team import (
                        Hooper,
                        PlayerAttributes,
                        suppress_budget_check,
                    )

                    with suppress_budget_check():
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
            if _depth >= self.MAX_EFFECT_CHAIN_DEPTH:
                logger.warning(
                    "effect_chain_depth_exceeded effect_id=%s depth=%d — "
                    "suppressing nested conditional_sequence",
                    self.effect_id,
                    _depth,
                )
                return result
            steps = self.action_code.get("steps")
            if isinstance(steps, list):
                for step in steps:
                    if not isinstance(step, dict):
                        continue
                    gate = step.get("gate")
                    # Route all gate types through the generic evaluator
                    if gate and isinstance(gate, dict) and not self._evaluate_condition(
                        gate, context
                    ):
                        continue
                    action = step.get("action")
                    if isinstance(action, dict):
                        # Recursively apply inner action (depth-limited)
                        inner_code = self.action_code
                        self.action_code = action
                        inner_result = self._apply_action_code(
                            context, HookResult(), _depth=_depth + 1,
                        )
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
                        # Merge legacy bias fields into action_biases
                        if inner_result.at_rim_bias != 0.0:
                            result.action_biases["at_rim"] = (
                                result.action_biases.get("at_rim", 0.0)
                                + inner_result.at_rim_bias
                            )
                        if inner_result.mid_range_bias != 0.0:
                            result.action_biases["mid_range"] = (
                                result.action_biases.get("mid_range", 0.0)
                                + inner_result.mid_range_bias
                            )
                        if inner_result.three_point_bias != 0.0:
                            result.action_biases["three_point"] = (
                                result.action_biases.get("three_point", 0.0)
                                + inner_result.three_point_bias
                            )
                        for k, v in inner_result.action_biases.items():
                            result.action_biases[k] = result.action_biases.get(k, 0.0) + v
                        if inner_result.narrative:
                            existing = result.narrative
                            if existing:
                                result.narrative = f"{existing} {inner_result.narrative}"
                            else:
                                result.narrative = inner_result.narrative

        elif action_type == "block_action":
            result.block_action = True

        elif action_type == "substitute_action":
            replacement = self.action_code.get("shot_type", "")
            if isinstance(replacement, str) and replacement in (
                "at_rim",
                "mid_range",
                "three_point",
            ):
                result.substitute_action = replacement

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
        d: dict[str, object] = {
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
        # Include codegen fields only when present (keep backward compat)
        if self.effect_type == "codegen":
            d["codegen_code"] = self.codegen_code
            d["codegen_code_hash"] = self.codegen_code_hash
            d["codegen_trust_level"] = self.codegen_trust_level
            d["codegen_enabled"] = self.codegen_enabled
            d["codegen_disabled_reason"] = self.codegen_disabled_reason
        return d

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
            # Codegen fields (present only for effect_type == "codegen")
            codegen_code=str(data["codegen_code"]) if data.get("codegen_code") else None,
            codegen_code_hash=(
                str(data["codegen_code_hash"]) if data.get("codegen_code_hash") else None
            ),
            codegen_trust_level=(
                str(data["codegen_trust_level"]) if data.get("codegen_trust_level") else None
            ),
            codegen_enabled=bool(data.get("codegen_enabled", True)),
            codegen_disabled_reason=str(data.get("codegen_disabled_reason", "")),
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
        except (ValueError, TypeError, AttributeError):
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


# ---------------------------------------------------------------------------
# Codegen helpers — build sandboxed context, convert results
# ---------------------------------------------------------------------------


def _build_game_context(
    context: HookContext,
    trust_level: object,  # CodegenTrustLevel — lazy import to avoid circular
) -> object:
    """Build a sandboxed GameContext from HookContext.

    The trust level determines what the generated code can see and do.
    """
    from pinwheel.core.codegen import ParticipantView, SandboxedGameContext
    from pinwheel.models.codegen import CodegenTrustLevel

    trust = trust_level if isinstance(trust_level, CodegenTrustLevel) else CodegenTrustLevel.NUMERIC

    # Build actor ParticipantView from offense[0]
    actor_view = ParticipantView(
        name="Unknown", team_id="", attributes={}, stamina=1.0, on_court=True,
    )
    opponent_view: ParticipantView | None = None
    actor_is_home = True

    gs = context.game_state
    if gs:
        offense = gs.offense
        defense = gs.defense
        if offense:
            h = offense[0]
            actor_view = ParticipantView(
                name=h.hooper.name,
                team_id=h.hooper.team_id,
                attributes=h.current_attributes.model_dump(),
                stamina=h.current_stamina,
                on_court=h.on_court,
            )
        if defense:
            d = defense[0]
            opponent_view = ParticipantView(
                name=d.hooper.name,
                team_id=d.hooper.team_id,
                attributes=d.current_attributes.model_dump(),
                stamina=d.current_stamina,
                on_court=d.on_court,
            )
        actor_is_home = gs.home_has_ball

    game_ctx = SandboxedGameContext(
        _actor=actor_view,
        _opponent=opponent_view,
        _home_score=gs.home_score if gs else 0,
        _away_score=gs.away_score if gs else 0,
        _phase_number=gs.quarter if gs else 0,
        _turn_count=gs.possession_number if gs else 0,
        _actor_is_home=actor_is_home,
        _game_name="Basketball",
    )

    # Trust level STATE+: add MetaStore read access
    if trust in (CodegenTrustLevel.STATE, CodegenTrustLevel.FLOW, CodegenTrustLevel.STRUCTURE):
        game_ctx._meta_store_ref = context.meta_store

    # Trust level FLOW+: add state dict
    if trust in (CodegenTrustLevel.FLOW, CodegenTrustLevel.STRUCTURE) and gs:
        import dataclasses as _dc
        state_dict: dict[str, int | float | bool | str] = {}
        for f in _dc.fields(gs):
            val = getattr(gs, f.name)
            if isinstance(val, (str, int, float, bool)):
                state_dict[f.name] = val
        game_ctx._state_dict = state_dict

    return game_ctx


def _codegen_result_to_hook_result(
    codegen_result: object,  # CodegenHookResult — lazy import
) -> HookResult:
    """Convert a CodegenHookResult to the standard HookResult."""
    from pinwheel.core.codegen import CodegenHookResult

    if not isinstance(codegen_result, CodegenHookResult):
        return HookResult()

    result = HookResult(
        score_modifier=codegen_result.score_modifier,
        stamina_modifier=codegen_result.stamina_modifier,
        shot_probability_modifier=codegen_result.shot_probability_modifier,
        shot_value_modifier=codegen_result.shot_value_modifier,
        extra_stamina_drain=codegen_result.extra_stamina_drain,
        block_action=codegen_result.block_action,
        narrative=codegen_result.narrative_note,
        meta_writes=codegen_result.meta_writes,
    )

    # opponent_score_modifier maps to score_modifier on the opposite side
    # This is a simplification — the caller handles which side gets it
    if codegen_result.opponent_score_modifier != 0:
        # Store as negative to signal "for the other team"
        result.score_modifier -= codegen_result.opponent_score_modifier

    return result
