# Proposal Effects System

The simulation engine is a pure function. The governance system changes the rules fed into that function. For the first 60 sessions of Pinwheel, "changing the rules" meant tweaking one of ~6 parameters on a `RuleSet` model: three-point value, shot clock, quarter length, Elam target. This was safe, testable, and boring. A player who proposed "every winning team should gain swagger, and swagger should affect shooting" had no mechanical path to see that happen.

The Proposal Effects System removes that ceiling. Proposals can now produce arbitrary structured effects that fire at hook points throughout the simulation, read and write metadata on any entity, modify shot probabilities, inject narrative instructions into AI reports, and expire on configurable timelines. The game starts as basketball. What it becomes is up to the governors.

## Design Principles

**No arbitrary code execution.** Effects are structured data, not code. The AI interpreter produces `EffectSpec` objects with typed fields. The execution engine reads those fields and dispatches to a fixed set of action primitives (`modify_probability`, `modify_score`, `write_meta`, `modify_stamina`, `add_narrative`). A proposal can never inject Python into the runtime. This is the same security boundary as the original rule interpretation — the AI is a constitutional interpreter, not a code author.

**Append-only event store.** Effects are persisted as governance events (`effect.registered`, `effect.expired`, `effect.repealed`). The registry is rebuilt from the event store at round start. No mutable state survives between rounds. This means the system is replayable, auditable, and recoverable — the same properties that make the simulation engine trustworthy.

**Backward compatible.** The old `RuleInterpretation` → `RuleSet` parameter path still works unchanged. `ProposalInterpretation` wraps it: a proposal with a single `parameter_change` effect is mechanically identical to the old system. `tally_governance_with_effects()` delegates to the existing `tally_governance()` for parameter changes. Nothing breaks.

**Effects compose.** A single proposal can produce multiple `EffectSpec` objects — a meta_mutation, a hook_callback, and a narrative instruction. These are independent effects that register separately, fire at different hook points, and expire on different timelines. Composition is the mechanism by which simple proposals produce complex emergent behavior.

## Architecture

Five layers, bottom to top:

```
┌─────────────────────────────────────────────────────────┐
│  AI Interpreter                                         │
│  raw_text → ProposalInterpretation (list[EffectSpec])   │
├─────────────────────────────────────────────────────────┤
│  Governance Pipeline                                    │
│  tally_governance_with_effects() → register effects     │
├─────────────────────────────────────────────────────────┤
│  Effect Registry                                        │
│  load from event store, tick lifetimes, query by hook   │
├─────────────────────────────────────────────────────────┤
│  Hook System                                            │
│  HookContext + HookResult + fire_effects()               │
├─────────────────────────────────────────────────────────┤
│  MetaStore                                              │
│  in-memory read/write cache for entity metadata          │
└─────────────────────────────────────────────────────────┘
```

### Layer 1: MetaStore (`core/meta.py`)

An in-memory key-value store for arbitrary entity metadata. Effects read and write during a round; dirty entries flush to the database at round end.

Keys are `(entity_type, entity_id, field)`. Values are JSON-safe primitives (`int | float | str | bool | None`).

```python
store = MetaStore()
store.set("team", "team-123", "swagger", 3)
store.increment("team", "team-123", "swagger", 1)  # → 4
store.get("team", "team-123", "swagger")  # → 4
store.toggle("team", "team-123", "hot_streak")  # False → True
```

**Dirty tracking.** `set()`, `increment()`, `decrement()`, and `toggle()` mark entities as dirty. `load_entity()` (used at round start to hydrate from DB) does not. `get_dirty_entities()` returns all modified entries and clears the dirty set — the game loop calls this once at round end to flush to the database.

**Snapshot.** `snapshot()` returns a deep copy of all state. Used to pass meta context to AI reports without risking mutation.

**Database backing.** A `meta` JSON column exists on 7 entity tables (teams, hoopers, game_results, seasons, schedule, box_scores, players). These columns are auto-migrated at startup by `auto_migrate_schema()` in `db/engine.py`, which compares ORM models against the live SQLite schema and adds any missing nullable columns automatically. The manual migration script `scripts/migrate_add_meta.py` is now redundant — kept for reference only. Repository methods `update_team_meta()`, `flush_meta_store()`, and `load_all_team_meta()` handle persistence.

### Layer 2: Hook System (`core/hooks.py`)

The original hook system used a `HookPoint` enum and a `GameEffect` protocol. That still exists and still works for legacy effects. The new system runs alongside it.

**`HookContext`** — A dataclass with optional fields populated based on which hook point is firing:

| Field Group | Populated During | Key Fields |
|---|---|---|
| Simulation | `sim.*` hooks | `game_state`, `hooper`, `rules`, `rng` |
| Round | `round.*` hooks | `round_number`, `season_id`, `game_results`, `teams` |
| Per-game | `round.game.post` | `winner_team_id`, `home_team_id`, `away_team_id`, `margin` |
| Governance | `gov.*` hooks | `proposal`, `tally` |
| Report | `report.*` hooks | `report_data` |
| Meta | All hooks | `meta_store` |

**`HookResult`** — Structured mutations returned by an effect:

| Field | What It Does |
|---|---|
| `score_modifier: int` | Added to current possession team's score |
| `shot_probability_modifier: float` | Added to shot probability before resolution |
| `stamina_modifier: float` | Applied to current hooper's stamina (clamped 0.0–1.0) |
| `narrative: str` | Injected into AI report/commentary context |
| `block_action: bool` | Prevents the default action from executing |
| `meta_writes` | Explicit meta writes (most meta writes happen in `apply()` directly) |

**`RegisteredEffect`** — The concrete runtime object created from an `EffectSpec`. Key behavior:

- **`should_fire(hook, context)`** — Checks hook point match, then evaluates structured conditions from `action_code`. Conditions are declarative: `{"meta_field": "swagger", "entity_type": "team", "gte": 5}` checks whether the offense team's swagger is at least 5. Supports `gte`, `lte`, `eq` operators.

- **`apply(hook, context)`** — Dispatches by `effect_type`:
  - `meta_mutation` → resolves target (e.g. `winning_team` → actual team ID), applies operation (`set`, `increment`, `decrement`, `toggle`) to MetaStore
  - `hook_callback` → reads `action_code` and applies primitives: `modify_score`, `modify_probability`, `modify_stamina`, `write_meta` (with template refs like `team:{winner_team_id}`), `add_narrative`
  - `narrative` → returns the `narrative_instruction` as a `HookResult.narrative`

- **`tick_round()`** — Decrements `rounds_remaining` for `N_ROUNDS` lifetime. Returns `True` if expired. `PERMANENT` never expires. `ONE_GAME` always expires.

- **Serialization** — `to_dict()` / `from_dict()` for event store persistence. `from_dict()` handles bad data gracefully (empty dict → valid effect with defaults).

**`fire_effects(hook, context, effects)`** — Fires all effects whose `should_fire()` returns True. Returns `list[HookResult]`. Exceptions in individual effects are caught and logged — one bad effect never crashes the round.

**`apply_hook_results(results, context)`** — Sums all modifiers and applies to game state. Score modifiers go to the team with the ball. Stamina modifiers are clamped to [0.0, 1.0].

### Layer 3: Effect Registry (`core/effects.py`)

Manages the lifecycle of active effects for a season.

**`EffectRegistry`** — In-memory registry rebuilt from the event store at round start.

```python
registry = EffectRegistry()
registry.register(effect)              # Add an effect
registry.get_effects_for_hook("sim.shot.pre")  # Query by hook
registry.get_narrative_effects()       # All narrative effects
registry.get_effects_for_proposal("p-123")     # All effects from one proposal
registry.tick_round(current_round)     # Expire N_ROUNDS effects, returns expired IDs
registry.build_effects_summary()       # Human-readable for AI context
```

**`effect_spec_to_registered(spec, proposal_id, current_round)`** — Converts an `EffectSpec` into a `RegisteredEffect`. Assigns default hook points based on effect type:

| Effect Type | Default Hook Points |
|---|---|
| `meta_mutation` | `round.game.post` |
| `narrative` | `report.simulation.pre`, `report.commentary.pre` |
| `hook_callback` | From `spec.hook_point` (explicit) |
| `parameter_change` | N/A (handled by RuleSet path, skipped during registration) |

**`register_effects_for_proposal(repo, registry, ...)`** — Registers effects when a proposal passes. Persists `effect.registered` events to the store. Skips `parameter_change` effects — those go through the existing RuleSet modification path.

**`load_effect_registry(repo, season_id)`** — Rebuilds from event store. Replays `effect.registered`, subtracts `effect.expired` and `effect.repealed`. Used at round start.

**`persist_expired_effects(repo, season_id, expired_ids)`** — Writes `effect.expired` events for effects that ran out of lifetime.

### Layer 4: Governance Pipeline (`core/governance.py`)

**`tally_governance_with_effects()`** extends the existing `tally_governance()` flow. For each passing proposal, it:

1. Applies the parameter change (if any) to the RuleSet — same as before
2. Checks if the proposal has a `ProposalInterpretation` with non-parameter effects
3. Calls `register_effects_for_proposal()` to register meta_mutation, hook_callback, and narrative effects in the registry and event store

Backward compatible: if no `effect_registry` is passed, it behaves identically to the old `tally_governance()`.

### Layer 5: AI Interpreter (`ai/interpreter.py`)

**`interpret_proposal_v2_mock()`** — The mock interpreter for testing. Uses pattern matching on the raw text:

| Pattern | Effect(s) Produced |
|---|---|
| Matches a RuleSet parameter | `parameter_change` (via legacy mock) |
| Contains "swagger" or "morale" | `meta_mutation` (winning team +1) + `narrative` (reporter tracks it) |
| Contains "bonus" or "boost" | `hook_callback` (5% shooting boost on `sim.shot.pre`) |
| Contains "call" or "rename" | `narrative` (instruction text) |
| No pattern match | Low-confidence `narrative` with `clarification_needed=True` |

The real Opus-powered interpreter will produce `ProposalInterpretation` objects using the same structured output format. The mock validates the pipeline end-to-end.

## Simulation Integration

`simulate_game()` now accepts optional `effect_registry` and `meta_store` parameters. Effects fire at 8 hook points during a game:

```
sim.game.pre       → before first possession
sim.quarter.pre    → before each quarter starts
sim.possession.pre → before each possession resolves
sim.quarter.end    → after each quarter
sim.halftime       → after the halftime quarter
sim.elam.start     → when the Elam Ending activates
sim.game.end       → after final score is determined
```

The internal `_fire_sim_effects()` helper builds a `HookContext` with the current game state, rules, RNG, and meta store, fires all matching effects, and applies the results. This runs alongside the legacy `fire_hooks()` system — both systems fire at their respective hook points in the same game.

When no effects are registered, simulation behavior is identical to before. The `TestSimulationEffectsIntegration` suite verifies this with same-seed comparison.

## The Swagger Example

This is the end-to-end proof of concept. Two proposals, submitted in sequence:

**Proposal 1:** *"Every team that wins gets +1 swagger."*

AI produces two effects:
- `meta_mutation`: target `winning_team`, field `swagger`, operation `increment`, value `1`, hook `round.game.post`
- `narrative`: instruction "Track and report on team swagger ratings"

After passing, the meta_mutation fires after every game. Winning teams accumulate swagger in the MetaStore. The narrative effect feeds into AI reports. The reporter starts mentioning swagger.

**Proposal 2:** *"Teams with 5+ swagger get a shooting bonus."*

AI produces one effect:
- `hook_callback`: hook `sim.shot.pre`, condition `{"meta_field": "swagger", "entity_type": "team", "gte": 5}`, action `{"type": "modify_probability", "modifier": 0.05}`

After passing, this effect fires before every shot. It checks the shooting team's swagger. If swagger >= 5, the shot probability gets +5%. The dominant team gets more dominant. Other governors now have a governance incentive to propose swagger-reducing rules. The game has evolved.

## Hook Point Reference

### Simulation Hooks (fire during `simulate_game()`)

| Hook | When | Typical Context |
|---|---|---|
| `sim.game.pre` | Before first possession | Full game state, both rosters |
| `sim.quarter.pre` | Before each quarter | Game state, quarter number |
| `sim.possession.pre` | Before each possession | Game state, ball handler |
| `sim.quarter.end` | After each quarter | Updated scores |
| `sim.halftime` | After halftime quarter | Pre-recovery state |
| `sim.elam.start` | Elam Ending activates | Target score set |
| `sim.game.end` | After final score | Final game state |

### Round Hooks (fire during `step_round()`)

| Hook | When | Typical Context |
|---|---|---|
| `round.game.post` | After each game result stored | Winner, margin, both team IDs |

### Report Hooks (fire during report generation)

| Hook | When | Typical Context |
|---|---|---|
| `report.simulation.pre` | Before simulation report prompt | Report data dict |
| `report.commentary.pre` | Before commentary prompt | Game results |

## Effect Lifetime

| Lifetime | Behavior |
|---|---|
| `PERMANENT` | Never expires. Active until repealed by governance. |
| `N_ROUNDS` | Counts down via `tick_round()`. Expires when `rounds_remaining` hits 0. |
| `ONE_GAME` | Expires after a single `tick_round()` call. |
| `UNTIL_REPEALED` | Like permanent, but semantically intended to be repealed. |

Expiration is persisted via `effect.expired` events. Repeal (by a future proposal) uses `effect.repealed` events. Both are subtracted during `load_effect_registry()`.

## File Reference

| File | Purpose |
|---|---|
| `src/pinwheel/models/governance.py` | `EffectSpec`, `ProposalInterpretation`, `EffectType`, `EffectDuration`, `MetaValue` |
| `src/pinwheel/core/hooks.py` | `HookContext`, `HookResult`, `RegisteredEffect`, `EffectLifetime`, `fire_effects()`, `apply_hook_results()` |
| `src/pinwheel/core/meta.py` | `MetaStore` — in-memory metadata cache |
| `src/pinwheel/core/effects.py` | `EffectRegistry`, `effect_spec_to_registered()`, `register_effects_for_proposal()`, `load_effect_registry()`, `persist_expired_effects()` |
| `src/pinwheel/core/governance.py` | `tally_governance_with_effects()` |
| `src/pinwheel/ai/interpreter.py` | `interpret_proposal_v2_mock()` |
| `src/pinwheel/core/simulation.py` | `_fire_sim_effects()`, updated `simulate_game()` signature |
| `src/pinwheel/db/repository.py` | `update_team_meta()`, `flush_meta_store()`, `load_all_team_meta()` |
| `scripts/migrate_add_meta.py` | ~~Additive migration~~ Superseded by `auto_migrate_schema()` in `db/engine.py` — kept for reference |
| `tests/test_effects.py` | 83 tests across 12 test classes |

## What's Next

The effects system is the foundation. The following Wave 2 items build on it:

- **GameEffect hooks** — Wire the effect registry into `step_round()` so effects load, fire, tick, and persist each round automatically. Currently the integration exists in tests; the game loop orchestration needs the full load→fire→tick→flush cycle.
- **Multi-parameter interpretation** — Expand the AI interpreter to handle compound proposals ("make threes worth 4 AND add a swagger system") and produce multi-effect `ProposalInterpretation` objects via real Opus calls.
- **Repeal mechanism** — A proposal that explicitly repeals an existing effect by ID, using `effect.repealed` events.
