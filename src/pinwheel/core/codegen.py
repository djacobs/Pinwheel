"""AI Codegen Frontier — sandboxed execution of AI-generated game mechanics.

Phase 6 of the abstract game spine. This module provides:
- SandboxViolation exception
- ParticipantView (immutable participant snapshot)
- GameContext protocol + SandboxedGameContext implementation
- CodegenHookResult (output from generated code)
- RESULT_BOUNDS + clamp_result() (defense-in-depth bounds enforcement)
- enforce_trust_level() (trust-level field gating)
- CodegenASTValidator (static analysis before execution)
- execute_codegen_effect() (sandbox runtime — Phase 6b)
- compute_code_hash() / verify_code_integrity() (Phase 6b)
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import logging
from typing import TYPE_CHECKING, Protocol

from pinwheel.models.codegen import CodegenTrustLevel

if TYPE_CHECKING:
    from pinwheel.core.meta import MetaStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sandbox exception
# ---------------------------------------------------------------------------


class SandboxViolation(Exception):
    """Raised when generated code attempts a forbidden operation."""

    def __init__(self, violation_type: str, detail: str) -> None:
        self.violation_type = violation_type
        self.detail = detail
        super().__init__(f"{violation_type}: {detail}")


# ---------------------------------------------------------------------------
# Read-only participant view for sandbox
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ParticipantView:
    """Immutable view of a participant. Generated code cannot modify this."""

    name: str
    team_id: str
    attributes: dict[str, int]
    stamina: float
    on_court: bool


# ---------------------------------------------------------------------------
# GameContext protocol — the ONLY interface generated code has to the game
# ---------------------------------------------------------------------------


class GameContext(Protocol):
    """Read-only view of game state provided to generated code."""

    @property
    def actor(self) -> ParticipantView: ...

    @property
    def opponent(self) -> ParticipantView | None: ...

    @property
    def home_score(self) -> int: ...

    @property
    def away_score(self) -> int: ...

    @property
    def phase_number(self) -> int: ...

    @property
    def turn_count(self) -> int: ...

    @property
    def state(self) -> dict[str, int | float | bool | str]: ...

    @property
    def actor_is_home(self) -> bool: ...

    @property
    def game_name(self) -> str: ...

    def meta_get(
        self,
        entity_type: str,
        entity_id: str,
        field_name: str,
        default: object = None,
    ) -> object: ...


@dataclasses.dataclass
class SandboxedGameContext:
    """Concrete implementation of GameContext for sandbox execution.

    Built from HookContext + trust level. Higher trust levels populate
    more fields (meta_store_ref, state_dict).
    """

    _actor: ParticipantView
    _opponent: ParticipantView | None = None
    _home_score: int = 0
    _away_score: int = 0
    _phase_number: int = 0
    _turn_count: int = 0
    _actor_is_home: bool = True
    _game_name: str = "Basketball"
    _state_dict: dict[str, int | float | bool | str] = dataclasses.field(
        default_factory=dict
    )
    _meta_store_ref: MetaStore | None = None

    @property
    def actor(self) -> ParticipantView:
        return self._actor

    @property
    def opponent(self) -> ParticipantView | None:
        return self._opponent

    @property
    def home_score(self) -> int:
        return self._home_score

    @property
    def away_score(self) -> int:
        return self._away_score

    @property
    def phase_number(self) -> int:
        return self._phase_number

    @property
    def turn_count(self) -> int:
        return self._turn_count

    @property
    def state(self) -> dict[str, int | float | bool | str]:
        return dict(self._state_dict)

    @property
    def actor_is_home(self) -> bool:
        return self._actor_is_home

    @property
    def game_name(self) -> str:
        return self._game_name

    def meta_get(
        self,
        entity_type: str,
        entity_id: str,
        field_name: str,
        default: object = None,
    ) -> object:
        """Read from MetaStore if available (trust level STATE+)."""
        if self._meta_store_ref is None:
            return default
        return self._meta_store_ref.get(entity_type, entity_id, field_name, default=default)


# ---------------------------------------------------------------------------
# CodegenHookResult — output from generated code
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class CodegenHookResult:
    """Output from generated code. Validated and clamped before merging."""

    score_modifier: int = 0
    opponent_score_modifier: int = 0
    stamina_modifier: float = 0.0
    shot_probability_modifier: float = 0.0
    shot_value_modifier: int = 0
    extra_stamina_drain: float = 0.0
    meta_writes: dict[str, dict[str, object]] | None = None
    block_action: bool = False
    narrative_note: str = ""


# ---------------------------------------------------------------------------
# Bounds enforcement — defense in depth
# ---------------------------------------------------------------------------

RESULT_BOUNDS: dict[str, tuple[int | float, int | float]] = {
    "score_modifier": (-10, 10),
    "opponent_score_modifier": (-10, 10),
    "stamina_modifier": (-1.0, 1.0),
    "shot_probability_modifier": (-0.5, 0.5),
    "shot_value_modifier": (-5, 5),
    "extra_stamina_drain": (0.0, 0.5),
}

MAX_META_WRITES = 10
MAX_NARRATIVE_LENGTH = 500
MAX_META_VALUE_SIZE = 256


def clamp_result(result: CodegenHookResult) -> CodegenHookResult:
    """Enforce bounds on generated code output.

    Even if the code tries to return score_modifier=9999, it gets clamped.
    """
    for field_name, (lo, hi) in RESULT_BOUNDS.items():
        val = getattr(result, field_name)
        if isinstance(val, (int, float)):
            clamped = max(lo, min(hi, val))
            # Preserve type: int fields stay int, float fields stay float
            if isinstance(lo, int) and isinstance(hi, int):
                setattr(result, field_name, int(clamped))
            else:
                setattr(result, field_name, float(clamped))

    # Clamp narrative
    if len(result.narrative_note) > MAX_NARRATIVE_LENGTH:
        result.narrative_note = result.narrative_note[:MAX_NARRATIVE_LENGTH]

    # Clamp meta_writes
    if result.meta_writes:
        if len(result.meta_writes) > MAX_META_WRITES:
            trimmed = dict(list(result.meta_writes.items())[:MAX_META_WRITES])
            result.meta_writes = trimmed
        # Clamp string meta values
        for _entity_key, fields in result.meta_writes.items():
            for k, v in fields.items():
                if isinstance(v, str) and len(v) > MAX_META_VALUE_SIZE:
                    fields[k] = v[:MAX_META_VALUE_SIZE]

    return result


# ---------------------------------------------------------------------------
# Trust level enforcement — zero out fields beyond the code's trust level
# ---------------------------------------------------------------------------

TRUST_LEVEL_ALLOWED_RESULT_FIELDS: dict[CodegenTrustLevel, frozenset[str]] = {
    CodegenTrustLevel.NUMERIC: frozenset({
        "score_modifier",
        "opponent_score_modifier",
        "stamina_modifier",
        "shot_probability_modifier",
        "shot_value_modifier",
        "extra_stamina_drain",
    }),
    CodegenTrustLevel.STATE: frozenset({
        "score_modifier",
        "opponent_score_modifier",
        "stamina_modifier",
        "shot_probability_modifier",
        "shot_value_modifier",
        "extra_stamina_drain",
        "meta_writes",
    }),
    CodegenTrustLevel.FLOW: frozenset({
        "score_modifier",
        "opponent_score_modifier",
        "stamina_modifier",
        "shot_probability_modifier",
        "shot_value_modifier",
        "extra_stamina_drain",
        "meta_writes",
        "block_action",
        "narrative_note",
    }),
    CodegenTrustLevel.STRUCTURE: frozenset({
        "score_modifier",
        "opponent_score_modifier",
        "stamina_modifier",
        "shot_probability_modifier",
        "shot_value_modifier",
        "extra_stamina_drain",
        "meta_writes",
        "block_action",
        "narrative_note",
    }),
}


def enforce_trust_level(
    result: CodegenHookResult,
    trust_level: CodegenTrustLevel,
) -> CodegenHookResult:
    """Zero out any fields the code shouldn't be writing to."""
    allowed = TRUST_LEVEL_ALLOWED_RESULT_FIELDS[trust_level]
    defaults = CodegenHookResult()
    for f in dataclasses.fields(result):
        if f.name not in allowed:
            setattr(result, f.name, getattr(defaults, f.name))
    return result


# ---------------------------------------------------------------------------
# Code integrity
# ---------------------------------------------------------------------------


def compute_code_hash(code: str) -> str:
    """Compute SHA-256 hash of generated code for integrity verification."""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def verify_code_integrity(code: str, expected_hash: str) -> bool:
    """Verify that code has not been tampered with since approval."""
    return compute_code_hash(code) == expected_hash


# ---------------------------------------------------------------------------
# AST Validator — static analysis before execution
# ---------------------------------------------------------------------------

FORBIDDEN_NAMES = frozenset({
    "exec", "eval", "compile", "__import__", "getattr", "setattr",
    "delattr", "globals", "locals", "vars", "dir", "breakpoint",
    "exit", "quit", "input", "print", "open", "type", "super",
    "classmethod", "staticmethod", "property",
})

FORBIDDEN_ATTR_PREFIXES = ("__",)

MAX_LOOP_BOUND = 1000
MAX_AST_DEPTH = 20
MAX_CODE_LENGTH = 5000


class CodegenASTValidator:
    """Static analysis pass on generated code before execution.

    Rejects: imports, exec/eval/compile, dunder access, while loops,
    unbounded for-loops, nested functions, code >5000 chars, AST depth >20.
    """

    def validate(self, code: str) -> list[str]:
        """Return list of violations. Empty list = code is valid."""
        violations: list[str] = []

        if len(code) > MAX_CODE_LENGTH:
            violations.append(
                f"Code exceeds max length ({len(code)} > {MAX_CODE_LENGTH})"
            )
            return violations

        try:
            tree = ast.parse(code, mode="exec")
        except SyntaxError as e:
            violations.append(f"Syntax error: {e}")
            return violations

        # Check AST depth
        depth = self._max_depth(tree)
        if depth > MAX_AST_DEPTH:
            violations.append(f"AST depth {depth} exceeds max {MAX_AST_DEPTH}")

        for node in ast.walk(tree):
            # No imports
            if isinstance(node, ast.Import | ast.ImportFrom):
                violations.append(
                    f"Import statement at line {node.lineno}"
                )

            # No forbidden function calls
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in FORBIDDEN_NAMES
            ):
                violations.append(
                    f"Forbidden call: {node.func.id} at line {node.lineno}"
                )

            # No dunder attribute access
            if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
                violations.append(
                    f"Dunder access: .{node.attr} at line {node.lineno}"
                )

            # No while loops
            if isinstance(node, ast.While):
                violations.append(
                    f"While loop at line {node.lineno} (use bounded for-range)"
                )

            # For loops must use range(N) or .items()/.values()/.keys()
            if isinstance(node, ast.For) and not self._is_bounded_for(node):
                violations.append(
                    f"Unbounded for-loop at line {node.lineno} "
                    f"(must use range() with max {MAX_LOOP_BOUND}, "
                    f"or .items()/.values()/.keys())"
                )

            # No nested function definitions
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                violations.append(
                    f"Function definition at line {node.lineno} (nested functions not allowed)"
                )

            # No class definitions
            if isinstance(node, ast.ClassDef):
                violations.append(
                    f"Class definition at line {node.lineno}"
                )

            # No lambda
            if isinstance(node, ast.Lambda):
                violations.append(
                    f"Lambda at line {getattr(node, 'lineno', 0)}"
                )

            # No yield/yield from
            if isinstance(node, ast.Yield | ast.YieldFrom):
                violations.append(
                    f"Yield at line {getattr(node, 'lineno', 0)}"
                )

            # No global/nonlocal
            if isinstance(node, ast.Global):
                violations.append(
                    f"Global statement at line {node.lineno}"
                )
            if isinstance(node, ast.Nonlocal):
                violations.append(
                    f"Nonlocal statement at line {node.lineno}"
                )

        return violations

    def _is_bounded_for(self, node: ast.For) -> bool:
        """Check that a for-loop uses range() with bounded arg, or dict iteration."""
        if not isinstance(node.iter, ast.Call):
            return False

        func = node.iter.func

        # range(N) where N is a literal <= MAX_LOOP_BOUND
        if isinstance(func, ast.Name) and func.id == "range":
            args = node.iter.args
            if not args:
                return False
            stop_arg = args[0] if len(args) == 1 else args[1]
            if isinstance(stop_arg, ast.Constant) and isinstance(stop_arg.value, int):
                return stop_arg.value <= MAX_LOOP_BOUND
            return False

        # .items(), .values(), .keys() calls on any object
        if isinstance(func, ast.Attribute) and func.attr in ("items", "values", "keys"):
            return True

        # enumerate() wrapping a bounded iterable — allow if wrapping dict method
        if isinstance(func, ast.Name) and func.id == "enumerate":
            if node.iter.args:
                inner = node.iter.args[0]
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr in ("items", "values", "keys")
                ):
                    return True
            return False

        return False

    def _max_depth(self, node: ast.AST, current: int = 0) -> int:
        """Compute maximum AST depth."""
        max_d = current
        for child in ast.iter_child_nodes(node):
            child_depth = self._max_depth(child, current + 1)
            if child_depth > max_d:
                max_d = child_depth
        return max_d


# ---------------------------------------------------------------------------
# Sandbox builtins — the minimal set available to generated code
# ---------------------------------------------------------------------------

SANDBOX_BUILTINS: dict[str, object] = {
    "range": range,
    "len": len,
    "min": min,
    "max": max,
    "abs": abs,
    "int": int,
    "float": float,
    "str": str,
    "bool": bool,
    "round": round,
    "sum": sum,
    "sorted": sorted,
    "enumerate": enumerate,
    "zip": zip,
    "map": map,
    "filter": filter,
    "list": list,
    "dict": dict,
    "tuple": tuple,
    "True": True,
    "False": False,
    "None": None,
}


# ---------------------------------------------------------------------------
# Sandbox runtime — Phase 6b (execute_codegen_effect)
# ---------------------------------------------------------------------------

def execute_codegen_effect(
    code: str,
    ctx: GameContext,
    rng: object,  # random.Random
) -> CodegenHookResult:
    """Execute generated code in a sandboxed environment.

    The code string is a function body that was previously validated
    by CodegenASTValidator and approved by the council.
    """
    import math
    import signal

    # Wrap the code in a function definition
    lines = code.strip().split("\n")
    indented = "\n".join(f"    {line}" for line in lines)
    wrapped = f"def _codegen_execute(ctx, rng, math, HookResult):\n{indented}\n"

    # Build restricted globals
    sandbox_globals: dict[str, object] = {
        "__builtins__": SANDBOX_BUILTINS,
    }

    # Compile
    try:
        compiled = compile(wrapped, "<codegen-effect>", "exec")
    except SyntaxError as e:
        raise SandboxViolation(
            violation_type="syntax_error",
            detail=str(e),
        ) from e

    # Execute the function definition (defines _codegen_execute in sandbox_globals)
    exec(compiled, sandbox_globals)  # noqa: S102 — intentional sandboxed exec
    fn = sandbox_globals["_codegen_execute"]

    # Execute with timeout via SIGALRM (POSIX only, 1 second)
    timed_out = False

    def _timeout_handler(signum: int, frame: object) -> None:
        nonlocal timed_out
        timed_out = True
        raise TimeoutError("Codegen execution exceeded 1 second timeout")

    old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(1)  # 1 second timeout
    try:
        result = fn(ctx, rng, math, CodegenHookResult)
    except TimeoutError as exc:
        raise SandboxViolation(
            violation_type="timeout",
            detail="Execution exceeded 1 second timeout",
        ) from exc
    finally:
        signal.alarm(0)  # Cancel alarm
        signal.signal(signal.SIGALRM, old_handler)

    # Validate return type
    if not isinstance(result, CodegenHookResult):
        raise SandboxViolation(
            violation_type="invalid_return",
            detail=f"Expected HookResult, got {type(result).__name__}",
        )

    # Enforce bounds (defense in depth)
    return clamp_result(result)
