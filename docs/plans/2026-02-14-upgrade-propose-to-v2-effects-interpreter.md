# Plan: Upgrade /propose to V2 Effects Interpreter with Creative Prompting

## Context

Adriana proposed "the ball is lava and holding it costs extra stamina" — a creative, clearly interpretable rule that maps to `stamina_drain_rate`. The interpreter returned "Could not map to a game parameter" with 30% confidence. The current `/propose` flow uses the V1 interpreter which maps to EXACTLY ONE RuleSet parameter. The V2 effects interpreter already exists and supports parameter changes, meta mutations, hook callbacks, and narrative effects — but `/propose` doesn't use it yet. The V2 system prompt also needs to be more creative about embracing metaphorical proposals.

## Changes

### 1. Improve V2 system prompt — be more imaginative
**File:** `src/pinwheel/ai/interpreter.py` (INTERPRETER_V2_SYSTEM_PROMPT, line 259)

Add guidance to the system prompt:
- Embrace metaphorical/playful proposals — "the ball is lava" = increase stamina drain
- Always find a mechanical interpretation, even for creative language
- Map flavorful proposals to the closest parameter OR invent a hook_callback/meta effect
- Include examples of creative proposals → structured effects
- Raise confidence for proposals that have a clear gameplay intent even if expressed creatively

### 2. Switch `/propose` to V2 interpreter
**File:** `src/pinwheel/discord/bot.py` (lines ~1609-1693)

- Import `interpret_proposal_v2` instead of `interpret_proposal`
- Call `interpret_proposal_v2(text, ruleset, api_key)` → returns `ProposalInterpretation`
- Convert to `RuleInterpretation` via `.to_rule_interpretation()` for tier detection + backward compat
- Store V2 `ProposalInterpretation` alongside legacy `RuleInterpretation` on the view
- Keep injection detection exactly as-is (create `ProposalInterpretation` directly for injection rejections)

### 3. Switch Revise modal to V2 interpreter
**File:** `src/pinwheel/discord/views.py` (lines ~250-335)

Same change as #2: `interpret_proposal` → `interpret_proposal_v2`, convert for compat.

### 4. Update `ProposalConfirmView` to carry V2 interpretation
**File:** `src/pinwheel/discord/views.py` (line 40)

- Add `interpretation_v2: ProposalInterpretation | None = None` field
- Keep `interpretation: RuleInterpretation` for backward compat (derived from V2)
- On Confirm, pass the V2 effects to submit_proposal if available

### 5. Update embed to show V2 effects
**File:** `src/pinwheel/discord/embeds.py` (lines 323-386)

Update `build_interpretation_embed` to accept either `RuleInterpretation` or `ProposalInterpretation`. When given V2:
- Show each effect with a human-readable description
- For parameter_change: `stamina_drain_rate: 1.0 → 1.5`
- For hook_callback: the description + hook point
- For meta_mutation: target + field + operation
- For narrative: the instruction text
- Never show "Could not map to a game parameter" when effects exist
- Show the overall impact_analysis

### 6. Update V2 mock for testing
**File:** `src/pinwheel/ai/interpreter.py` (interpret_proposal_v2_mock, line 422)

Add keyword patterns for "lava", "stamina", "hot potato" etc. that map to stamina_drain_rate.

## Backward Compatibility

- `ProposalInterpretation.to_rule_interpretation()` already exists — extracts first parameter_change effect
- `detect_tier()` and `token_cost_for_tier()` continue to use `RuleInterpretation`
- `submit_proposal()` in `core/governance.py` takes `RuleInterpretation` — convert before calling
- Governance tally still uses V1 — no changes needed there yet

## Verification

1. Run `uv run pytest -x -q` — all tests pass
2. Test locally with mock: propose "the ball is lava" → should get stamina_drain_rate effect
3. Deploy and test in Discord: propose creative rules → see imaginative interpretations
4. Verify tier/cost calculation still works correctly
