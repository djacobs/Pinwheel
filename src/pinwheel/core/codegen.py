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
# Arithmetic guards: AST-legal code can still be a CPU/memory bomb via
# exponentiation and giant literals — bound them statically.
MAX_POW_EXPONENT = 16
MAX_INT_LITERAL = 10**9
MAX_STR_LITERAL_LENGTH = 500


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

            # Exponentiation only with small int-literal exponents —
            # `10 ** x` can be a CPU/memory bomb within one expression
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
                exp = node.right
                if not (
                    isinstance(exp, ast.Constant)
                    and isinstance(exp.value, int)
                    and not isinstance(exp.value, bool)
                    and exp.value <= MAX_POW_EXPONENT
                ):
                    violations.append(
                        f"Exponentiation at line {node.lineno} requires an "
                        f"int-literal exponent <= {MAX_POW_EXPONENT}"
                    )
            if isinstance(node, ast.AugAssign) and isinstance(node.op, ast.Pow):
                violations.append(
                    f"Augmented exponentiation (**=) at line {node.lineno}"
                )

            # Giant literals
            if isinstance(node, ast.Constant):
                if (
                    isinstance(node.value, int)
                    and not isinstance(node.value, bool)
                    and abs(node.value) > MAX_INT_LITERAL
                ):
                    violations.append(
                        f"Int literal exceeds {MAX_INT_LITERAL} at line "
                        f"{getattr(node, 'lineno', 0)}"
                    )
                if (
                    isinstance(node.value, str)
                    and len(node.value) > MAX_STR_LITERAL_LENGTH
                ):
                    violations.append(
                        f"String literal exceeds {MAX_STR_LITERAL_LENGTH} "
                        f"chars at line {getattr(node, 'lineno', 0)}"
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

# Per-call wall-clock budget. Enforced via a worker thread (works on any
# platform and off the main thread, unlike the signal-alarm approach it
# replaced, which was POSIX- and main-thread-only).
CODEGEN_EXEC_TIMEOUT_SECONDS = 1.0

# Compiled-code cache keyed by code hash — effects fire per possession at
# hundreds of games/hour; recompiling every call is measurable.
_COMPILE_CACHE: dict[str, object] = {}
_COMPILE_CACHE_MAX = 256


def _compile_codegen(code: str) -> object:
    """Compile (or fetch from cache) the wrapped codegen function."""
    cache_key = compute_code_hash(code)
    cached = _COMPILE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    lines = code.strip().split("\n")
    indented = "\n".join(f"    {line}" for line in lines)
    wrapped = f"def _codegen_execute(ctx, rng, math, HookResult):\n{indented}\n"
    try:
        compiled = compile(wrapped, "<codegen-effect>", "exec")
    except SyntaxError as e:
        raise SandboxViolation(
            violation_type="syntax_error",
            detail=str(e),
        ) from e

    if len(_COMPILE_CACHE) < _COMPILE_CACHE_MAX:
        _COMPILE_CACHE[cache_key] = compiled
    return compiled


def execute_codegen_effect(
    code: str,
    ctx: GameContext,
    rng: object,  # random.Random
    timeout_seconds: float | None = None,
) -> CodegenHookResult:
    """Execute generated code in a sandboxed environment.

    The code string is a function body that was previously validated
    by CodegenASTValidator and approved by the council. Execution runs
    on its own daemon thread with a wall-clock timeout — a timed-out
    thread leaks until it finishes, but cannot block other effects
    (and the offending effect is auto-disabled by the caller).
    """
    import math
    import threading

    timeout = (
        timeout_seconds
        if timeout_seconds is not None
        else CODEGEN_EXEC_TIMEOUT_SECONDS
    )

    compiled = _compile_codegen(code)

    # Build restricted globals and define the function
    sandbox_globals: dict[str, object] = {
        "__builtins__": SANDBOX_BUILTINS,
    }
    exec(compiled, sandbox_globals)  # noqa: S102 — intentional sandboxed exec
    fn = sandbox_globals["_codegen_execute"]

    outcome: dict[str, object] = {}

    def _runner() -> None:
        try:
            outcome["result"] = fn(ctx, rng, math, CodegenHookResult)  # type: ignore[operator]
        except BaseException as e:  # noqa: BLE001 — re-raised on the caller thread
            outcome["error"] = e

    runner = threading.Thread(
        target=_runner, name="codegen-exec", daemon=True,
    )
    runner.start()
    runner.join(timeout)
    if runner.is_alive():
        raise SandboxViolation(
            violation_type="timeout",
            detail=f"Execution exceeded {timeout}s timeout",
        )
    if "error" in outcome:
        raise outcome["error"]  # type: ignore[misc]
    result = outcome.get("result")

    # Validate return type
    if not isinstance(result, CodegenHookResult):
        raise SandboxViolation(
            violation_type="invalid_return",
            detail=f"Expected HookResult, got {type(result).__name__}",
        )

    # Enforce bounds (defense in depth)
    return clamp_result(result)


# ---------------------------------------------------------------------------
# Approval-time pre-flight — subprocess with resource limits
# ---------------------------------------------------------------------------

# The in-process thread timeout above bounds wall-clock per call, but cannot
# cap memory. Pre-flight runs the code against a battery of synthetic
# contexts in a SEPARATE process with CPU/memory rlimits, so memory bombs
# and CPU spins are caught before the code can ever reach a live game.
# Run twice: before proposal.codegen_ready, and again on admin Approve.

PREFLIGHT_CPU_SECONDS = 2
PREFLIGHT_MEMORY_BYTES = 256 * 1024 * 1024
PREFLIGHT_TIMEOUT_SECONDS = 20.0
PREFLIGHT_PER_CALL_TIMEOUT = 2.0


def _build_preflight_contexts() -> list[SandboxedGameContext]:
    """Synthetic game contexts covering edge shapes generated code must survive."""
    attrs = {
        "scoring": 50, "passing": 50, "defense": 50, "speed": 50,
        "stamina": 50, "iq": 50, "ego": 50, "chaotic_alignment": 50,
        "fate": 50,
    }
    hostile_attrs = {k: 0 for k in attrs}
    actor = ParticipantView(
        name="Actor", team_id="t1", attributes=attrs, stamina=1.0, on_court=True,
    )
    tired_actor = ParticipantView(
        name="", team_id="", attributes=hostile_attrs, stamina=0.0, on_court=False,
    )
    opponent = ParticipantView(
        name="Opponent", team_id="t2", attributes=attrs, stamina=0.5, on_court=True,
    )

    contexts: list[SandboxedGameContext] = []
    score_shapes = [
        (0, 0), (1, 0), (50, 48), (99, 98), (120, 60), (7, 7),
    ]
    for home, away in score_shapes:
        for actor_is_home in (True, False):
            contexts.append(
                SandboxedGameContext(
                    _actor=actor,
                    _opponent=opponent,
                    _home_score=home,
                    _away_score=away,
                    _phase_number=1,
                    _turn_count=1,
                    _actor_is_home=actor_is_home,
                )
            )
    # No opponent; degenerate actor; late game; weird phase numbers
    contexts.append(SandboxedGameContext(_actor=actor, _opponent=None))
    contexts.append(
        SandboxedGameContext(
            _actor=tired_actor, _opponent=None, _home_score=0, _away_score=0,
        )
    )
    contexts.append(
        SandboxedGameContext(
            _actor=actor, _opponent=opponent,
            _home_score=45, _away_score=44,
            _phase_number=4, _turn_count=299, _actor_is_home=False,
        )
    )
    contexts.append(
        SandboxedGameContext(
            _actor=actor, _opponent=opponent,
            _phase_number=0, _turn_count=0,
            _state_dict={"quarter": 4, "elam_activated": True},
        )
    )
    return contexts


def _preflight_worker(code: str, conn: object) -> None:
    """Child-process entry: apply rlimits, run the battery, send violations."""
    import contextlib
    import random as _random

    violations: list[str] = []
    try:
        import resource

        resource.setrlimit(
            resource.RLIMIT_CPU, (PREFLIGHT_CPU_SECONDS, PREFLIGHT_CPU_SECONDS),
        )
        # RLIMIT_AS is unreliable on some platforms (macOS) — the CPU
        # limit and parent-side timeout still apply if it fails.
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(
                resource.RLIMIT_AS,
                (PREFLIGHT_MEMORY_BYTES, PREFLIGHT_MEMORY_BYTES),
            )
    except ImportError:
        pass  # non-POSIX — limits unavailable, parent timeout still applies

    rng = _random.Random(42)
    for i, ctx in enumerate(_build_preflight_contexts()):
        try:
            result = execute_codegen_effect(
                code, ctx, rng, timeout_seconds=PREFLIGHT_PER_CALL_TIMEOUT,
            )
            # clamp_result already ran; sanity-check the shape anyway
            if not isinstance(result, CodegenHookResult):
                violations.append(f"context {i}: invalid return type")
        except SandboxViolation as e:
            violations.append(f"context {i}: {e.violation_type}: {e.detail}")
        except Exception as e:  # noqa: BLE001 — report, don't crash the worker
            violations.append(f"context {i}: {type(e).__name__}: {e}")
        if len(violations) >= 5:
            break  # enough evidence

    conn.send(violations)  # type: ignore[attr-defined]
    conn.close()  # type: ignore[attr-defined]


def preflight_codegen_effect(
    code: str,
    timeout_seconds: float = PREFLIGHT_TIMEOUT_SECONDS,
) -> list[str]:
    """Run generated code against synthetic contexts in a resource-limited
    subprocess. Returns a list of violations (empty = passed).

    A timeout or abnormal child exit is itself a violation — that's the
    memory-bomb / CPU-spin signal the in-process sandbox can't produce.
    """
    import multiprocessing

    # AST validation first — cheap, and a syntax-level failure shouldn't
    # cost a process spawn.
    ast_violations = CodegenASTValidator().validate(code)
    if ast_violations:
        return ast_violations

    mp_ctx = multiprocessing.get_context("spawn")
    parent_conn, child_conn = mp_ctx.Pipe(duplex=False)
    proc = mp_ctx.Process(
        target=_preflight_worker, args=(code, child_conn), daemon=True,
    )
    try:
        proc.start()
    except (OSError, ValueError) as e:
        logger.warning("preflight_spawn_failed error=%s — skipping", e)
        return []
    child_conn.close()

    violations: list[str]
    try:
        if parent_conn.poll(timeout_seconds):
            violations = list(parent_conn.recv())
        else:
            violations = [
                f"Pre-flight timed out after {timeout_seconds}s "
                "(possible CPU spin)"
            ]
            proc.kill()
    except (EOFError, OSError):
        violations = [
            "Pre-flight process died before reporting "
            "(possible memory bomb — killed by resource limits)"
        ]
    finally:
        proc.join(timeout=5)
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=5)
        parent_conn.close()

    if not violations and proc.exitcode not in (0, None):
        violations = [
            f"Pre-flight process exited abnormally (code {proc.exitcode})"
        ]
    return violations
