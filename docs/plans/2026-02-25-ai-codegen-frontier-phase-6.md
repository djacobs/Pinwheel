# Phase 6: AI Codegen Frontier

## Context

Phases 1-5 made the simulation data-driven (ActionDefinition, ActionRegistry, GameDefinition, GameDefinitionPatch, narration). But the declarative spine has a ceiling — it can't express mechanics like rock-paper-scissors free throws, momentum systems, or weather effects. Phase 6 breaks through: Claude generates sandboxed Python code that runs inside the simulation, secured by an LLM council (3 independent reviewers), AST validation, restricted builtins, and resource limits.

Full architecture spec: `docs/plans/abstract_game_spine.md` lines 1929-3053.

## Sub-Phase Breakdown

### Phase 6a: Models + AST Validator (foundation, no execution)

**New files:**
- `src/pinwheel/models/codegen.py` — `CodegenTrustLevel`, `ReviewVerdict`, `CouncilReview`, `CodegenEffectSpec`
- `src/pinwheel/core/codegen.py` — `SandboxViolation`, `ParticipantView`, `GameContext` protocol, `SandboxedGameContext`, `CodegenHookResult`, `RESULT_BOUNDS`, `clamp_result()`, `enforce_trust_level()`, `CodegenASTValidator`
- `tests/test_codegen.py` — 40+ tests (AST validator adversarial cases, bounds clamping, trust enforcement, model validation)

**Modified files:**
- `src/pinwheel/models/governance.py` — add `"codegen"` to `EffectType` literal, add `codegen: CodegenEffectSpec | None = None` to `EffectSpec`

**Key decisions:**
- AST validator rejects: imports, exec/eval/compile, dunder access, while loops, unbounded for-loops, nested functions, code >5000 chars, AST depth >20
- For-loops allowed only with `range(N)` where N is a literal <= 1000, or `.items()`/`.values()`/`.keys()` calls
- Trust levels gate which `CodegenHookResult` fields are allowed (NUMERIC=numbers only, STATE=+meta_writes, FLOW=+block_action+narrative, STRUCTURE=+game patches)

### Phase 6b: Sandbox Runtime (execution, no AI)

**Modified files:**
- `src/pinwheel/core/codegen.py` — add `execute_codegen_effect()`, `SANDBOX_BUILTINS`, `sandbox_resource_limits()`, `verify_code_integrity()`, `compute_code_hash()`

**Tests added to** `tests/test_codegen.py` — 20+ tests (correct execution, sandbox escapes, timeout, return type validation, RPS example)

**Key decisions:**
- Pure Python sandbox: restricted `__builtins__` dict + SIGALRM timeout (1 sec). No RestrictedPython dependency.
- Code wrapped as `def _codegen_execute(ctx, rng, math, HookResult):` body
- Allowed builtins: range, len, min, max, abs, int, float, str, bool, round, sum, sorted, enumerate, zip, map, filter, list, dict, tuple + True/False/None
- `clamp_result()` enforced after every execution (defense in depth)

### Phase 6c: Council Pipeline (AI calls, mocked in tests)

**New files:**
- `src/pinwheel/ai/codegen_council.py` — `generate_codegen_effect()`, `review_security()`, `review_gameplay()`, `review_adversarial()`, `run_council_review()`, `generate_codegen_effect_mock()`
- `tests/test_codegen_council.py` — 15+ tests with mocked Anthropic client

**Key decisions:**
- Generator: Opus, produces Python function body + trust level + hook points
- Security + Gameplay reviewers run in parallel; Adversarial gets security results as context, runs after
- All 3 must APPROVE for consensus; any rejection flags for admin
- AST validation happens between generation and review (fast-fail)
- Cost tracked via existing `record_ai_usage()` in `ai/usage.py`
- Mock generator for tests and API-key-absent fallback

### Phase 6d: Governance Integration (wiring)

**Modified files:**
- `src/pinwheel/core/hooks.py` — add codegen fields to `RegisteredEffect` (`codegen_code`, `codegen_code_hash`, `codegen_trust_level`, `codegen_enabled`, error tracking), add `_fire_codegen()`, `_record_codegen_error()`, `_disable_codegen()`, update `apply()` dispatch, update `to_dict()`/`from_dict()`
- `src/pinwheel/core/effects.py` — update `effect_spec_to_registered()` for codegen
- `src/pinwheel/core/governance.py` — codegen = tier 4, always needs admin review
- `src/pinwheel/ai/interpreter.py` — add `interpret_codegen_proposal()` routing function

**New test file:** `tests/test_codegen_integration.py` — 15+ tests (fire_codegen, hash verification, auto-disable, trust enforcement, effect_spec conversion, HookContext→GameContext mapping)

**Key integration:** `_build_game_context(HookContext, trust_level)` maps:
- `actor` from `game_state.offense[0]` → `ParticipantView`
- `opponent` from `game_state.defense[0]` → `ParticipantView`
- scores, quarter, possession_count from `GameState`
- meta_store gated by trust level (STATE+)
- state dict gated by trust level (FLOW+)

### Phase 6e: Admin Tooling + End-to-End

**Modified files:**
- `src/pinwheel/discord/bot.py` — `/review-codegen`, `/disable-effect`, `/rerun-council` commands
- `src/pinwheel/discord/embeds.py` — codegen review embed
- `src/pinwheel/core/effects.py` — extend effects summary for codegen metadata

**New test file:** `tests/test_codegen_admin.py` — 10+ tests (commands, notifications, end-to-end lifecycle)

**End-to-end test:** proposal → council (mocked) → vote → register → fire → HookResult

## Execution Plan

Run as 5 sequential background agents (same pattern as Phases 1-5):
1. **6a** → commit → verify all tests pass
2. **6b** → commit → verify
3. **6c** → commit → verify
4. **6d** → commit → verify
5. **6e** → commit → verify → post-commit → deploy

## Verification

After each sub-phase: `uv run pytest -x -q` + `uv run ruff check src/ tests/`

After Phase 6e, verify end-to-end:
- Codegen effect creates and fires correctly
- AST validator catches all forbidden patterns
- Sandbox timeout works
- Trust level enforcement zeros disallowed fields
- Council consensus/rejection logic correct
- Auto-disable after 3 errors works
- Admin commands respond correctly
