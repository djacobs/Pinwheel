# Plan: Effects System Wave 2 Completion

**Date:** 2026-02-14
**Status:** Draft
**Ref:** EFFECTS_SYSTEM.md — "What's Next" section; Wave 2 items

## Context

The effects system was built in Wave 1 and is documented in `docs/EFFECTS_SYSTEM.md`. It provides the infrastructure for proposals to produce arbitrary structured effects beyond parameter changes — meta mutations, hook callbacks, and narrative instructions. Wave 1 delivered the data models, the effect registry, the hook system, the MetaStore, and the governance pipeline integration. Tests pass. The swagger example works end-to-end in test.

Wave 2 is about completing the integration into the live game loop. EFFECTS_SYSTEM.md states: "the game loop orchestration needs the full load -> fire -> tick -> flush cycle." This plan audits what is already connected and identifies what remains.

## Audit: What Is Connected

After reading `src/pinwheel/core/game_loop.py` (`_phase_simulate_and_govern`), here is the current state of integration:

### Fully connected in the game loop

| Step | Location | Status |
|------|----------|--------|
| **Load effect registry** | `game_loop.py` lines 875-891 | DONE. `load_effect_registry(repo, season_id)` is called at round start. Registry is rebuilt from `effect.registered` minus `effect.expired`/`effect.repealed`. |
| **Load MetaStore** | `game_loop.py` lines 878-883 | DONE. If effects exist, a MetaStore is created and team meta is loaded from the DB. |
| **Fire `round.pre`** | `game_loop.py` lines 937-948 | DONE. HookContext includes round_number, season_id, meta_store, teams. |
| **Fire `round.game.pre`** | `game_loop.py` lines 967-982 | DONE. Per-game context with home/away team IDs. |
| **Pass effects to simulate_game()** | `game_loop.py` lines 985-998 | DONE. `effect_registry` and `meta_store` are passed to the simulation. |
| **Sim-level hooks** | `simulation.py` | DONE. 7 hook points fire: `sim.game.pre`, `sim.quarter.pre`, `sim.possession.pre`, `sim.quarter.end`, `sim.halftime`, `sim.elam.start`, `sim.game.end`. |
| **Fire `round.game.post`** | `game_loop.py` lines 1041-1059 | DONE. Winner team ID, margin, home/away IDs in context. |
| **Fire `round.post`** | `game_loop.py` lines 1081-1093 | DONE. All game results and teams in context. |
| **Flush MetaStore to DB** | `game_loop.py` lines 1096-1110 | DONE. `meta_store.get_dirty_entities()` is flushed via `repo.flush_meta_store()`. |
| **Tick effect lifetimes** | `game_loop.py` lines 1113-1123 | DONE. `effect_registry.tick_round()` is called. Expired effect IDs are persisted via `persist_expired_effects()`. |
| **Register effects from passing proposals** | `governance.py` lines 651-667 | DONE. `tally_governance_with_effects()` calls `register_effects_for_proposal()` for non-parameter effects. |
| **Effects summary in narrative** | `game_loop.py` lines 1211-1215 | DONE. `effect_registry.build_effects_summary()` is injected into narrative context for AI reports. |

### NOT connected (gaps to fill)

| Gap | Description | Impact |
|-----|-------------|--------|
| **`report.simulation.pre` hook not fired** | Narrative effects register with hook points `["report.simulation.pre", "report.commentary.pre"]` (see `effects.py` line 151), but `game_loop.py` never fires `report.simulation.pre` before generating the simulation report. | Narrative effects that should inject context into AI reports never fire at report generation time. The effects summary IS injected via `narrative_ctx.effects_narrative`, but individual narrative effects cannot dynamically modify report context. |
| **`report.commentary.pre` hook not fired** | Same issue: narrative effects targeting commentary generation never fire. | Commentary generation does not receive dynamic narrative effect injections. |
| **`gov.pre` / `gov.post` hooks not fired** | The interpreter's system prompt references governance hooks (`gov.pre`, `gov.post` per `interpreter.py` line 308), but they are never fired in the governance tally flow. | Effects that should modify governance behavior (e.g., "all votes count double during playoffs") have no execution path. |
| **`round.pre` context is sparse** | `round.pre` fires but the HookContext does not include `game_results` (from previous rounds) or `event_bus`. | Effects that need historical context at round start cannot access it. |
| **Hooper meta not loaded** | Only team meta is loaded into MetaStore (line 882: `repo.load_team_meta(tid)`). Hooper-level meta is never loaded. | Effects targeting individual hoopers (e.g., "mark the MVP") cannot read or write hooper metadata. |
| **`sim.shot.pre` hook not fired** | The EFFECTS_SYSTEM.md swagger example references `sim.shot.pre` as a hook point for shooting probability modifiers. However, the simulation fires `sim.possession.pre` but NOT `sim.shot.pre` — there is no hook point between action selection and shot resolution. | The flagship example from the effects system docs (swagger-based shooting bonus on `sim.shot.pre`) would not actually fire in production. Effects must use `sim.possession.pre` instead. |

## What Needs to Be Built

### Priority 1: Fire report hooks

**Why:** Narrative effects are the most common non-parameter effect type. If they don't fire at report generation time, the effects system's narrative capability is inert.

**Implementation:**
- In `_phase_ai()` in `game_loop.py`, before generating commentary and reports, fire `report.commentary.pre` and `report.simulation.pre`.
- Build a `HookContext` with the report data dict, meta_store snapshot, and narrative context.
- Collect `HookResult.narrative` strings from fired effects.
- Inject collected narratives into the AI prompt context alongside the existing `effects_summary`.
- The `_phase_ai()` function does not have access to the effect_registry or meta_store (they are computed in `_phase_simulate_and_govern`). Solution: add `effect_registry` and `meta_store` fields to the `_SimPhaseResult` dataclass so they are passed to the AI phase.

### Priority 2: Fire governance hooks

**Why:** Governance hooks enable effects like "during playoffs, all proposals require supermajority" or "the losing team gets a free BOOST token." These are powerful gameplay mechanics.

**Implementation:**
- In `tally_pending_governance()` in `game_loop.py`, before the tally loop, fire `gov.pre`. After the tally loop, fire `gov.post`.
- `gov.pre` context: proposals list, current ruleset, effect registry.
- `gov.post` context: tallies, updated ruleset, which proposals passed/failed.
- `gov.post` effects could modify token regeneration amounts or trigger meta writes.

### Priority 3: Load hooper meta

**Why:** Hooper-level effects (e.g., "the top scorer each round gets +1 clutch") need meta reads/writes on individual hoopers.

**Implementation:**
- In `_phase_simulate_and_govern()`, after loading team meta, also load hooper meta for all hoopers on scheduled teams.
- Add `repo.load_hooper_meta(hooper_id)` method to repository (or batch load per team).
- MetaStore already supports arbitrary entity types — `store.set("hooper", hooper_id, "clutch", 1)` works today.
- Extend `repo.flush_meta_store()` to handle hooper entities alongside teams.

### Priority 4: Add `sim.shot.pre` hook (or document the correct hook)

**Why:** The flagship swagger example in EFFECTS_SYSTEM.md uses `sim.shot.pre`, which does not exist in the simulation. Either add the hook or update the docs and interpreter prompt.

**Options:**
- **Option A:** Add `_fire_sim_effects("sim.shot.pre", ...)` in `scoring.py` before shot resolution. This is the most precise hook point but adds complexity to the hot path.
- **Option B:** Update EFFECTS_SYSTEM.md and the interpreter prompt to use `sim.possession.pre` instead. Effects that want to modify shot probability fire at possession start and set `shot_probability_modifier` on the HookResult.

**Decision: Option B.** The `sim.possession.pre` hook already fires before shot resolution and the `HookResult.shot_probability_modifier` is already applied by `apply_hook_results()`. Adding a new hook in the hot path would complicate the simulation for minimal benefit. Update the docs and interpreter prompt.

### Priority 5: Pass `_SimPhaseResult` enrichments

**Why:** Several dataclass fields need to be added to `_SimPhaseResult` so that phase 2 (AI) and phase 3 (persist) can access effect-related data.

**Implementation:**
- Add `effect_registry: EffectRegistry | None = None` to `_SimPhaseResult`.
- Add `meta_store_snapshot: dict | None = None` — a frozen snapshot of meta state for AI context.

## Files to Create/Modify

| File | Change |
|------|--------|
| `src/pinwheel/core/game_loop.py` | (1) Add `effect_registry` and `meta_store_snapshot` to `_SimPhaseResult`. (2) In `_phase_ai()`, fire `report.simulation.pre` and `report.commentary.pre` hooks. (3) In `tally_pending_governance()`, fire `gov.pre` and `gov.post` hooks. (4) Load hooper meta into MetaStore alongside team meta. |
| `src/pinwheel/core/simulation.py` | No changes needed — sim hooks are already complete. |
| `src/pinwheel/core/hooks.py` | No structural changes. May add documentation for the full hook point reference. |
| `src/pinwheel/core/effects.py` | No changes needed — registry and lifecycle management are complete. |
| `src/pinwheel/core/meta.py` | No changes needed — already supports arbitrary entity types. |
| `src/pinwheel/db/repository.py` | Add `load_hooper_meta()` or batch hooper meta loading. Ensure `flush_meta_store()` handles hooper entity type. |
| `src/pinwheel/ai/interpreter.py` | Update system prompt to reference `sim.possession.pre` instead of `sim.shot.pre`. |
| `docs/EFFECTS_SYSTEM.md` | (1) Update swagger example to use `sim.possession.pre`. (2) Add `gov.pre`, `gov.post` to hook reference table. (3) Add `report.simulation.pre`, `report.commentary.pre` to hook reference table. (4) Mark Wave 2 items as completed. |
| `tests/test_effects.py` | Add tests for report hook firing, governance hook firing, hooper meta loading. |
| `tests/test_game_loop.py` | Add integration test verifying the full load -> fire -> tick -> flush cycle runs without errors when effects are registered. |

## Testing Strategy

### Unit tests
1. **Report hooks fire narrative effects:** Register a narrative effect with hook `report.simulation.pre`. Fire the hook with a HookContext containing report data. Verify HookResult contains the narrative text.
2. **Governance hooks fire:** Register a hook_callback effect on `gov.post`. Fire after tally. Verify the effect executes.
3. **Hooper meta round-trip:** Load hooper meta from DB. Set a value via MetaStore. Flush. Reload. Verify the value persists.
4. **Effects summary includes all active types:** Register meta_mutation, narrative, and hook_callback effects. Verify `build_effects_summary()` includes all three.

### Integration tests
5. **Full lifecycle in step_round:** Seed a league with a passing swagger-style proposal. Step a round. Verify:
   - Effect registry loads with the swagger effect.
   - MetaStore is populated with team meta.
   - `round.game.post` fires and increments swagger for the winning team.
   - MetaStore is flushed to DB.
   - Effect lifetime ticks.
   - If the effect was `N_ROUNDS` with 1 round remaining, verify it expires and `effect.expired` event is written.
6. **Narrative effects in reports:** Register a narrative effect. Generate a simulation report. Verify the narrative instruction is included in the AI prompt context (mock the AI call and inspect the prompt).
7. **Multi-round lifecycle:** Step 3 rounds with a 3-round-duration effect. Verify it fires in rounds 1-3 and is expired at round 4.

### Regression tests
8. **No effects = no change:** Run `step_round()` with no registered effects. Verify behavior is identical to before (same seed produces same results).
9. **Backward compatibility:** Run `tally_governance()` (without effects) alongside `tally_governance_with_effects()` (with empty registry). Verify identical results.
