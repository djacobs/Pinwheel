# Agent-Native Proposal Interpreter

## Context

Players are submitting creative, clear proposals and getting 30% confidence "narrative only" responses — which feels like rejection. Two real examples:

1. **"when a ball goes out of bounds it is worth double"** — dumped to narrative. Should be `hook_callback` (after turnover, double next score).
2. **"ball is lava... defenders GAIN stamina with great defensive plays"** — first part mapped to stamina_drain_rate (good), second part dropped (bad). The second part needs a mechanic that doesn't exist yet.

The fix: the interpreter should be faithful to player intent. If existing primitives fit, use them. If they don't, describe what needs to be built (`custom_mechanic`) and flag for admin to approve code generation. Confidence = understanding of intent, not fit to existing boxes.

---

## Changes

### 1. Model: Add `custom_mechanic` effect type

**File: `src/pinwheel/models/governance.py`**

- Add `"custom_mechanic"` to `EffectType` Literal (line 145)
- Add 4 optional fields to `EffectSpec`:
  - `mechanic_description: str | None = None` — what the mechanic does
  - `mechanic_hook_point: str | None = None` — where in the sim it fires
  - `mechanic_observable_behavior: str | None = None` — what players would see
  - `mechanic_implementation_spec: str | None = None` — what code to write

All additive, all optional. No existing data breaks.

### 2. Core governance: tier detection + admin review

**File: `src/pinwheel/core/governance.py`**

- `detect_tier_v2()`: Add `custom_mechanic` → Tier 3 (significant, not wild)
- `_needs_admin_review()`: Always return True when any effect is `custom_mechanic` — regardless of confidence. These need code, so admin must see them.
- Tally flow: `custom_mechanic` effects pass through the existing non-parameter path to `register_effects_for_proposal()` — no special handling needed.

### 3. Effects system: registry + hook handling

**File: `src/pinwheel/core/effects.py`**

- `effect_spec_to_registered()`: `custom_mechanic` → empty hook_points list (descriptive only, doesn't fire)
- `build_effects_summary()`: Show as `[PENDING MECHANIC]` instead of `[custom_mechanic]`

**File: `src/pinwheel/core/hooks.py`**

- `RegisteredEffect.apply()`: `custom_mechanic` → returns narrative with "[Pending mechanic]" prefix

### 4. V2 System Prompt: embrace creative + conditional proposals

**File: `src/pinwheel/ai/interpreter.py`**

Improvements to `INTERPRETER_V2_SYSTEM_PROMPT`:

- **Add conditional rule section** with examples:
  - "when the ball goes out of bounds, next basket worth double" → `hook_callback` at `sim.possession.pre`, condition: previous possession was turnover, action: `modify_score: 2`
  - "after a steal, the stealer gets a shooting boost" → `hook_callback`
  - "if a team is down by 10, their threes are worth 4" → `hook_callback` with condition
- **Add `custom_mechanic`** as effect type 7, with guidance: use ONLY when existing primitives genuinely can't express the intent. Most conditional proposals CAN be hook_callbacks.
- **Redefine confidence**: measures understanding of intent, not fit to primitives. `custom_mechanic` with clear intent = 0.8+. Low confidence = genuinely unclear what player wants.
- **Add compound proposal examples**: one part uses existing primitives, other part needs `custom_mechanic`
- **Add `custom_mechanic` fields** to the JSON response format
- **Increase max_tokens** from 1500 → 2000 for richer descriptions

### 5. Mock Interpreter: conditional patterns + custom_mechanic fallback

**File: `src/pinwheel/ai/interpreter.py`**

Two additions to `interpret_proposal_v2_mock()`:

**5a. Conditional rule pattern** (NEW — before the narrative fallback):
- Detect "when/if/after" + game event keyword (out of bounds, turnover, steal, block, etc.) + scoring/modifier keyword (double, triple, worth, extra)
- Produce `hook_callback` effect with appropriate hook_point and action_code
- Example: "when ball goes out of bounds it is worth double" → `hook_callback` at `sim.possession.pre`, action: `modify_score: 2`, condition: "after turnover/out of bounds"

**5b. Enhance "lava" pattern** to detect defender-gain clauses:
- If lava text also contains "defender"/"defensive" + "gain"/"earn"/"recover", add a `custom_mechanic` effect for the defender stamina part

**5c. Replace narrative-only fallback** (currently 30% confidence for everything):
- If text has 2+ "intent signals" (game verbs, conditions, entities, modifiers) → `custom_mechanic` at 0.75 confidence
- True fallback (no gameplay intent detected) → narrative at 0.3 confidence (unchanged)

### 6. Discord embeds: display custom_mechanic

**File: `src/pinwheel/discord/embeds.py`**

- `build_interpretation_embed()`: Add handler for `custom_mechanic` in the V2 effects loop — show as "New Mechanic (needs dev work)" with description and implementation spec
- `build_admin_review_embed()`: Add optional `interpretation_v2` parameter. When `custom_mechanic` effects present, change title to "Custom Mechanic -- Implement or Veto" and show mechanic description + implementation spec

### 7. Discord views: thread interpretation_v2 to admin

**File: `src/pinwheel/discord/views.py`**

- `_notify_admin_for_review()`: Add `interpretation_v2` parameter, pass to `build_admin_review_embed()`
- `ProposalConfirmView.confirm()`: Pass `self.interpretation_v2` to `_notify_admin_for_review()`

### 8. Tests

- **Model tests**: `custom_mechanic` is valid EffectType, EffectSpec accepts new fields
- **Tier detection**: `custom_mechanic` = Tier 3
- **Admin review**: `custom_mechanic` always triggers review, even with high confidence
- **Hybrid proposal**: parameter_change + custom_mechanic compound
- **Effects registry**: custom_mechanic has no hook_points, shows as PENDING MECHANIC
- **Mock interpreter**:
  - Conditional rule ("out of bounds double") → hook_callback (NOT custom_mechanic)
  - "Ball is lava" + defender clause → parameter_change + custom_mechanic
  - Clear gameplay intent without primitive match → custom_mechanic at 0.75
  - No gameplay intent → narrative at 0.3

---

## Implementation Order

1. Models (governance.py) — pure additive
2. Core governance (detect_tier, admin_review)
3. Effects system (effects.py, hooks.py)
4. Mock interpreter improvements
5. V2 system prompt rewrite
6. Discord embeds
7. Discord views (thread interpretation_v2)
8. Tests
9. `uv run pytest -x -q` + `uv run ruff check src/ tests/`

---

## Verification

1. Run existing tests to confirm no regressions: `uv run pytest -x -q`
2. Run new tests covering all custom_mechanic paths
3. Manual test with mock interpreter: proposals like "out of bounds double" and "ball is lava + defender stamina" should produce correct effect types
4. Lint: `uv run ruff check src/ tests/`
