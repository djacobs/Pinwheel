# Plan: Repeal Mechanism

**Date:** 2026-02-14
**Status:** Draft
**Ref:** EFFECTS_SYSTEM.md — "Repeal mechanism — A proposal that explicitly repeals an existing effect by ID, using `effect.repealed` events."

## Context

The effects system supports `effect.repealed` as an event type, the `UNTIL_REPEALED` lifetime is defined, and `load_effect_registry()` already filters out repealed effects during registry reconstruction. But there is no governance-triggered flow to actually repeal an effect. Governors cannot remove effects they no longer want, which breaks the "Adaptable" principle — rules should be reversible, not permanent-by-default.

This is a gameplay feature, not just cleanup. If Proposal A passes and gives winning teams +1 swagger, and another governor thinks swagger is ruining the game, they need a mechanism to propose repealing it. The repeal itself should go through governance: the community votes on whether to undo the effect. This is analogous to repealing a law — it takes the same democratic process that enacted it.

## What Exists Today

### Event store support (complete)
- `GovernanceEventType` in `src/pinwheel/models/governance.py` (line 34) includes `"effect.repealed"` as a valid event type.
- `load_effect_registry()` in `src/pinwheel/core/effects.py` (line 235-245) queries for `effect.repealed` events and adds their IDs to the `dead_ids` set, which prevents those effects from being loaded into the registry. This means if a repeal event is written, the effect will be excluded on the next round start.

### EffectLifetime support (complete)
- `EffectLifetime.UNTIL_REPEALED` is defined in `src/pinwheel/core/hooks.py` (line 153). It behaves like `PERMANENT` — never expires via `tick_round()` — but semantically signals that the effect is intended to be repealed.
- `RegisteredEffect.tick_round()` correctly does not expire `UNTIL_REPEALED` effects.

### Effect identification (partial)
- Each registered effect has an `effect_id` (UUID) and a `proposal_id` (the proposal that created it).
- `EffectRegistry.get_effects_for_proposal(proposal_id)` returns all active effects from a given proposal.
- `EffectRegistry.build_effects_summary()` produces a human-readable list of active effects, but it does not include effect IDs in the display.

### What is missing
1. **No `/repeal` command** — There is no Discord slash command for repealing effects.
2. **No repeal-via-proposal flow** — There is no mechanism to create a proposal that targets an existing effect for repeal.
3. **No repeal event writing** — While `load_effect_registry()` reads `effect.repealed` events, nothing in the codebase writes them.
4. **No effect browser for governors** — Governors have no way to see the list of active effects with their IDs, which they would need to specify which effect to repeal.

## Design Decision: Repeal via Proposal

Repeal should go through the governance pipeline, not be a unilateral command. This preserves the democratic principle: you cannot undo a community decision alone. Two approaches were considered:

**Option A: `/repeal EFFECT_ID`** — A new slash command that creates a repeal-specific proposal. The governor selects an active effect from an autocomplete list, the proposal is framed as "Repeal: [effect description]", and if it passes, the effect is deregistered.

**Option B: Natural language repeal via `/propose`** — The AI interpreter detects repeal intent in proposals like "remove the swagger system" and maps it to the appropriate effect(s). The `ProposalInterpretation` includes a new `effects_to_repeal` field.

**Decision: Option A (with Option B as a later enhancement).** Option A is simpler, more deterministic, and easier to test. Option B depends on the AI interpreter being good at matching natural language to specific effects, which is fragile. Option A can be built now; Option B can be layered on top later.

## What Needs to Be Built

### 1. Active Effects Browser

Governors need to see what effects are currently active before they can propose repealing one.

**Implementation:**
- Add a `/effects` slash command that shows all active effects in the current season.
- Each effect displays: description, effect type, lifetime, source proposal text (truncated), and a short ID suffix (last 8 characters of the UUID, enough for autocomplete).
- Use a Discord embed with fields for each active effect.
- If no effects are active, show "No active effects."

### 2. `/repeal` Slash Command

**Implementation:**
- New slash command: `/repeal effect` where `effect` is an autocomplete field.
- Autocomplete queries the effect registry and returns active effects with their descriptions and short IDs.
- When invoked, creates a governance proposal with:
  - `raw_text`: "Repeal: [effect description]"
  - `interpretation`: `RuleInterpretation(parameter=None, ...)` — this is a tier-5 proposal (game effect, not a parameter change).
  - Additional metadata: `repeal_target_effect_id` stored in the proposal event payload.
  - `status`: `"submitted"` — goes through the normal confirm flow.
- Token cost: 1 PROPOSE token (same as other proposals).
- Shows a confirmation view similar to `ProposalConfirmView` but with repeal-specific messaging.

### 3. Repeal Execution in Governance Tally

**Implementation:**
- In `tally_governance_with_effects()` in `src/pinwheel/core/governance.py`, after a repeal proposal passes:
  - Look up the `repeal_target_effect_id` from the proposal payload.
  - Write an `effect.repealed` event to the event store.
  - Deregister the effect from the current `EffectRegistry`.
- The existing `load_effect_registry()` logic already handles `effect.repealed` events — no changes needed there.

### 4. Repeal of Parameter Changes

A special case: repealing a `parameter_change` effect (which modified the RuleSet) is more complex because the parameter was already changed. Options:
- **Revert to old value:** The `RuleChange` event stores `old_value`. A repeal could set the parameter back.
- **No-op for parameter changes:** Only non-parameter effects (meta_mutation, hook_callback, narrative) can be repealed. Parameter changes are permanent once enacted.

**Decision: No-op for parameter changes.** Parameter changes are already "repealed" by passing a new proposal that changes the parameter to a different value. The repeal mechanism targets the effects system — meta mutations, hooks, and narratives — which have no other removal path. This simplifies the implementation and avoids complex rollback logic.

## Files to Create/Modify

| File | Change |
|------|--------|
| `src/pinwheel/discord/bot.py` | Add `/effects` command, add `/repeal` command with autocomplete, register handlers. |
| `src/pinwheel/discord/embeds.py` | Add `build_effects_list_embed()` for the `/effects` display. Add `build_repeal_confirm_embed()`. |
| `src/pinwheel/discord/views.py` | Add `RepealConfirmView` (Confirm/Cancel buttons for repeal proposals). |
| `src/pinwheel/core/governance.py` | Add `submit_repeal_proposal()` helper. Add repeal execution logic in `tally_governance_with_effects()`. |
| `src/pinwheel/core/effects.py` | Add `repeal_effect()` function that writes `effect.repealed` event and deregisters from registry. |
| `src/pinwheel/db/repository.py` | Possibly add helper to query active effects by season (may already be covered by `get_events_by_type`). |
| `tests/test_governance.py` | Test repeal proposal lifecycle. |
| `tests/test_effects.py` | Test repeal execution: effect.repealed event written, effect excluded from registry on reload. |
| `tests/test_discord.py` | Test `/effects` and `/repeal` commands. |

## Testing Strategy

### Unit tests
1. **Repeal event writing:** Call `repeal_effect()` with a valid effect ID. Verify `effect.repealed` event is written to the event store.
2. **Registry reload after repeal:** Register an effect, write a repeal event, reload the registry with `load_effect_registry()`. Verify the effect is not in the new registry.
3. **Repeal proposal lifecycle:** Submit a repeal proposal. Confirm it. Cast votes. Tally governance. Verify the target effect is deregistered.
4. **Repeal of non-existent effect:** Submit a repeal for an already-expired effect ID. Verify graceful handling (no crash, appropriate message).
5. **Repeal of parameter_change:** Attempt to repeal a parameter_change effect. Verify it is rejected or no-oped with a clear message.
6. **Token cost:** Verify repeal proposals cost 1 PROPOSE token.
7. **Tier assignment:** Verify repeal proposals are tier 5 (game effect).

### Integration tests
8. **Full cycle:** Register a swagger effect via a passing proposal. Verify it fires during simulation. Submit a repeal proposal for it. Vote it through. Verify the swagger effect no longer fires in the next round.
9. **Active effects display:** Create multiple effects of different types. Call `/effects`. Verify all are listed with correct descriptions and IDs.

### Edge cases
10. **Race condition:** Repeal a `ONE_GAME` effect that expires naturally in the same round. Both expiration and repeal events exist. Verify double-removal is harmless.
11. **Multiple effects from one proposal:** Proposal A created 3 effects. Repeal targets one. Verify only that one is removed; the other two remain active.
