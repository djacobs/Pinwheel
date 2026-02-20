# Living Rules: Every Wild Proposal Fires Real Game Mechanics

## Context

Pinwheel's promise is that players can propose *anything* — "the court is now round," "the ball is red hot," "a fan from the crowd takes the court" — and see real gameplay impact. Today, the system can handle parameter tweaks ("threes worth 5") and simple conditional hooks ("trailing team gets a boost"), but creative proposals either produce inert `custom_mechanic` placeholders or get approximated so loosely that the player's intent is lost.

The deeper problem: **there's a severed wire between the effect system and the simulation engine.** Effects compute `shot_probability_modifier` but it's never applied to actual shots. The possession resolution function has no channel for effect-derived modifiers. And the action primitive vocabulary is too narrow — 5 types when creative proposals need 12.

### Test Proposals

Every design decision is validated against these real and hypothetical proposals:

- **a. Real production proposals:** "when a ball goes out of bounds it is worth double," "ball is lava... defenders GAIN stamina with great defensive plays"
- **b.** "The court is now round, not rectangular"
- **c.** "The ball is red hot — players randomly are unable to continue"
- **d.** "A player from the crowd switches places with a player, has one extreme stat and others poor"
- **e.** "Each pass adds 1 point to the eventual value of a basket made"

---

## The Critical Gap: `shot_probability_modifier` Is Never Applied

**File:** `src/pinwheel/core/hooks.py` (line 523-551)

`apply_hook_results()` reads `score_modifier` and `stamina_modifier` from effect results and applies them to `game_state`. But it **ignores** `shot_probability_modifier` — a field that exists on `HookResult` (line 134) and is correctly set by `_apply_action_code` when `modify_probability` fires. The value is computed, returned, and discarded.

**File:** `src/pinwheel/core/simulation.py` (line 174-176)

```python
_fire_sim_effects("sim.possession.pre", ...)   # computes probability modifier
result = resolve_possession(game_state, rules, rng, last_three)  # never receives it
```

`resolve_possession` takes `(game_state, rules, rng, last_possession_three)`. There is no parameter for effect-provided modifiers. Every `modify_probability` effect in production is silently doing nothing.

---

## Architecture: PossessionContext

A new dataclass that carries effect-derived modifiers INTO `resolve_possession`. This is the single bridge between the hook system and the possession engine.

**File:** `src/pinwheel/core/state.py`

```python
@dataclass
class PossessionContext:
    """Effect-derived modifiers applied to this possession.

    Built from HookResult accumulation at sim.possession.pre,
    consumed by resolve_possession().
    """
    shot_probability_modifier: float = 0.0    # additive to compute_shot_probability
    shot_value_modifier: int = 0              # additive to points_for_shot
    extra_stamina_drain: float = 0.0          # extra drain on ball handler
    at_rim_bias: float = 0.0                  # shot selection weight bias
    mid_range_bias: float = 0.0
    three_point_bias: float = 0.0
    turnover_modifier: float = 0.0            # additive to turnover probability
    random_ejection_probability: float = 0.0  # chance a player burns out
    bonus_pass_count: int = 0                 # derived passes for scoring bonus
    narrative_tags: list[str] = field(default_factory=list)
```

**Why not expand GameState?** `GameState` is mutable state persisting across the entire game. `PossessionContext` is ephemeral (one possession), effect-derived, and clearly scoped. This keeps the simulation's pure-function character intact.

### Wiring It Through

1. `_fire_sim_effects()` returns `PossessionContext` instead of `list[HookResult]`
2. `_run_quarter()` captures it and passes to `resolve_possession()`
3. `resolve_possession()` gets new optional param `context: PossessionContext | None = None`
4. Modifiers are applied at the correct points within the possession

**In `simulation.py` — `_run_quarter` inner loop (line 174-176):**
```python
# Before:
_fire_sim_effects("sim.possession.pre", game_state, rules, rng, new_effects, meta_store)
result = resolve_possession(game_state, rules, rng, last_three)

# After:
poss_ctx = _fire_sim_effects("sim.possession.pre", game_state, rules, rng, new_effects, meta_store)
result = resolve_possession(game_state, rules, rng, last_three, poss_ctx)
```

Same change in `_run_elam()` (line 233-234).

### Where Modifiers Apply Inside `resolve_possession`

| Modifier | Where Applied | How |
|----------|--------------|-----|
| `random_ejection_probability` | Before ball handler selection (~line 262) | `rng.random() < prob` → eject random player, trigger sub |
| `shot selection biases` | In `select_action()` (~line 354) | Add to weights dict |
| `turnover_modifier` | In `check_turnover()` (~line 281) | Add to `to_prob` |
| `shot_probability_modifier` | After `compute_shot_probability()` (~lines 377, 385) | Add to `base_prob` before RNG roll |
| `shot_value_modifier` + `bonus_pass_count` | After `points_for_shot()` (~lines 383, 385) | Add to `pts` if shot made |
| `extra_stamina_drain` | In `drain_stamina()` calls (~lines 448-455) | Add to handler's drain |

---

## New Action Primitives

Currently: `modify_score`, `modify_probability`, `modify_stamina`, `write_meta`, `add_narrative` (5 types).

### Added in this plan (7 new types):

**1. `modify_shot_value`** — Adds/subtracts points from the value of a made shot.
```json
{"type": "modify_shot_value", "modifier": 1}
```
*Needed for:* proposal (e) "each pass adds 1 point"

**2. `modify_shot_selection`** — Biases which shot type the offense selects.
```json
{"type": "modify_shot_selection", "at_rim_bias": 10.0, "three_point_bias": -12.0}
```
*Needed for:* proposal (b) "round court" (no corner threes)

**3. `modify_turnover_rate`** — Adds to base turnover probability.
```json
{"type": "modify_turnover_rate", "modifier": -0.01}
```
*Needed for:* proposal (b) "round court" (no sideline turnovers)

**4. `random_ejection`** — Probability that a random active player is forced out.
```json
{"type": "random_ejection", "probability": 0.03, "reason": "burned_by_hot_ball"}
```
*Needed for:* proposal (c) "ball is red hot"

**5. `derive_pass_count`** — Simulates passes from team passing stats, adds to shot value.
```json
{"type": "derive_pass_count", "min_passes": 0, "max_passes": 5, "value_per_pass": 1}
```
*Needed for:* proposal (e) "each pass adds 1 point"

**6. `swap_roster_player`** — Generates a temporary player with extreme stats, swaps in.
```json
{"type": "swap_roster_player", "extreme_stat": "scoring", "extreme_value": 95, "other_stats_value": 15, "target": "random_active", "duration": "one_quarter"}
```
*Needed for:* proposal (d) "player from the crowd"

**7. `conditional_sequence`** — Executes a list of actions with optional gates between them.
```json
{
  "type": "conditional_sequence",
  "steps": [
    {"action": {"type": "modify_stamina", "modifier": -0.015}, "gate": null},
    {"action": {"type": "random_ejection", "probability": 0.03}, "gate": null},
    {"action": {"type": "add_narrative", "text": "Sizzle!"}, "gate": {"random_chance": 0.1}}
  ]
}
```
*Needed for:* proposal (c) "hot ball" (compound behavior from one effect)

---

## Expanded Conditions

Currently `_evaluate_condition` only checks meta field comparisons (`swagger >= 5`). Expand to:

### Game State Conditions
```json
{"game_state_check": "trailing"}       // offense is behind
{"game_state_check": "leading"}        // offense is ahead
{"game_state_check": "elam_active"}    // Elam Ending in progress
{"quarter_gte": 3}                     // 2nd half
{"score_diff_gte": -5}                 // close game from offense perspective
```

### Random Probability
```json
{"random_chance": 0.15}                // fires 15% of the time (uses game rng)
```

### Previous Possession State
Requires new tracking fields on `GameState`: `last_action`, `last_result`, `consecutive_makes`.

```json
{"last_result": "made"}                // last possession was a make
{"consecutive_makes_gte": 3}           // hot streak
```

### Player Attribute Conditions
```json
{"ball_handler_attr": "scoring", "gte": 70}
```

All condition types are evaluated in `RegisteredEffect._evaluate_condition()` (hooks.py line 242). The expansion adds ~40 lines of condition handlers.

---

## Proposal Walk-Throughs

### b. "The court is now round, not rectangular"

**What it means mechanically:** No corners (fewer open threes), curved boundaries (fewer sideline turnovers), equal distance from all perimeter spots (drives easier).

**Effects produced by interpreter:**
1. `hook_callback` at `sim.possession.pre`: `modify_shot_selection` with `at_rim_bias: +8`, `three_point_bias: -12`, `mid_range_bias: +4` — drives up, corner threes down
2. `hook_callback` at `sim.possession.pre`: `modify_turnover_rate` with `modifier: -0.01` — no sideline turnovers
3. `hook_callback` at `sim.possession.pre`: `modify_probability` with `modifier: -0.03` — unfamiliar geometry
4. `narrative`: "The court is perfectly circular. No corners, no baseline traps..."

**Gameplay impact:** Slashers gain, sharpshooters lose. Teams with at_rim_bias strategies gain double benefit. Turnovers drop slightly. ~5% FG reduction across the board from geometric confusion.

**All expressible with new primitives. No custom_mechanic needed.**

### c. "The ball is red hot — players randomly are unable to continue"

**Effects produced by interpreter:**
1. `hook_callback` at `sim.possession.pre`: `conditional_sequence` with three steps:
   - Always: `modify_stamina` -0.015 (extra drain on ball handler)
   - Always: `random_ejection` probability 0.03 (3% chance per possession of burnout)
   - 10% chance: `add_narrative` "The ball sizzles in their hands!"
2. `parameter_change`: `stamina_drain_rate` from 0.007 to 0.012
3. `narrative`: "The ball is scorching hot. Players wince when they catch it..."

**Gameplay impact:** ~1.5 ejections per game from heat. Stamina becomes the dominant stat. Bench depth is critical. Games become shorter and more frantic. High-stamina hoopers become the most valuable.

**Expressible with `conditional_sequence` + `random_ejection`. No custom_mechanic needed.**

### d. "A player from the crowd switches places with a player"

**Effects produced by interpreter:**
1. `hook_callback` at `sim.quarter.pre`: `conditional_sequence`:
   - Gate `random_chance: 0.25`: `swap_roster_player` with `extreme_stat: "random"`, `extreme_value: 95`, `other_stats_value: 15`, `duration: "one_quarter"`
   - Gate `previous_step_result: "fired"`: `add_narrative` "A figure emerges from the crowd!"
2. `custom_mechanic`: Full spec for generating a named temporary hooper with backstory, tracking across quarter boundaries, and restoring the original player
3. `narrative`: "Occasionally, a mysterious figure from the crowd steps onto the court..."

**Gameplay impact:** ~1 crowd swap per game. Massive variance — a crowd sharpshooter (scoring 95, everything else 15) could hit every shot but can't defend or pass. Creates legendary moments and stories.

**Approximation fires via `swap_roster_player`. Full vision (named crowd player, backstory, cross-quarter tracking) documented in custom_mechanic for admin build-out.**

### e. "Each pass adds 1 point to the eventual value of a basket"

**Effects produced by interpreter:**
1. `hook_callback` at `sim.possession.pre`: `derive_pass_count` with `min_passes: 0`, `max_passes: 5`, `value_per_pass: 1`
2. `narrative`: "Ball movement is currency. Every pass adds 1 point to the next basket..."
3. `custom_mechanic`: Full pass-chain sub-loop where each pass is individually resolved against defender steal chance (the `derive_pass_count` primitive uses a simpler team-average approach)

**How `derive_pass_count` works:**
- Reads offense team's average passing attribute
- `pass_probability = avg_passing / 100.0`
- Loops up to `max_passes` times, each iteration: `rng.random() < pass_probability` to continue
- Result: `shot_value_modifier = pass_count * value_per_pass`

A team of Floor Generals (passing ~80) averages ~3.5 passes → baskets worth 5.5-6.5 points. A team of Closers (passing ~25) averages ~1 pass → baskets worth 3-4 points.

**Gameplay impact:** Radical shift rewarding collective play. Three-pointers after 4 passes = 7 points. Iso-heavy teams are punished. Floor General archetype becomes most valuable.

**Core mechanic fires via `derive_pass_count`. Full pass-chain sub-loop (individually resolved passes) in custom_mechanic for admin to build later.**

---

## Interpreter Prompt Update

**File:** `src/pinwheel/ai/interpreter.py` — `INTERPRETER_V2_SYSTEM_PROMPT`

Replace the current "Action Primitives" section with the expanded vocabulary:

```
## Action Primitives (for action_code)
modify_score (modifier: int), modify_probability (modifier: float -0.5..+0.5),
modify_stamina (modifier: float), write_meta (entity, field, value, op),
add_narrative (text),
modify_shot_value (modifier: int — adds to point value of made shots),
modify_shot_selection (at_rim_bias, mid_range_bias, three_point_bias — weight adjustments),
modify_turnover_rate (modifier: float — additive to turnover probability),
random_ejection (probability: float 0..1, reason: str — chance player forced out),
derive_pass_count (min_passes, max_passes, value_per_pass — simulates passes from team stats),
swap_roster_player (extreme_stat, extreme_value, other_stats_value, target, duration),
conditional_sequence (steps: [{action, gate}] — compound actions with conditional gates)
```

Add to conditions documentation:
```
## Condition Types (for condition_check or gate)
Meta field: {"meta_field": "swagger", "entity_type": "team", "gte": 5}
Game state: {"game_state_check": "trailing|leading|elam_active"}
Quarter: {"quarter_gte": 3}
Random: {"random_chance": 0.15}
Previous possession: {"last_result": "made|missed|turnover"}
Streak: {"consecutive_makes_gte": 3}
```

---

## Cross-Possession Tracking

**File:** `src/pinwheel/core/state.py` — `GameState`

Add fields for condition evaluation that references previous possessions:

```python
# Cross-possession tracking (for condition evaluation)
last_action: str = ""           # "three_point", "mid_range", "at_rim", "turnover"
last_result: str = ""           # "made", "missed", "turnover", "foul"
consecutive_makes: int = 0      # scoring streak counter (reset on miss/turnover)
consecutive_misses: int = 0     # cold streak counter (reset on make)
```

Updated at the end of `resolve_possession` in `possession.py` — write the result back to `game_state` for next possession's conditions.

---

## Implementation Phases

### Phase 1: Fix the Critical Gap (immediate value)
- Add `PossessionContext` to `state.py`
- Modify `_fire_sim_effects` to return `PossessionContext` from accumulated `HookResult` fields
- Wire through `_run_quarter` and `_run_elam` to `resolve_possession`
- `resolve_possession` accepts optional `PossessionContext`, applies `shot_probability_modifier` after `compute_shot_probability`
- Existing `modify_probability` effects start actually working
- **Tests:** effect with modify_probability changes shot outcomes; backward compat (no context = same behavior)

### Phase 2: Core New Primitives (proposal b, e)
- Add `modify_shot_value`, `modify_shot_selection`, `modify_turnover_rate` to `_apply_action_code`
- Add corresponding fields to `HookResult`
- Wire through `PossessionContext` → `resolve_possession` → `select_action`, `check_turnover`, `points_for_shot`
- **Tests:** round court effects change shot distribution; shot value bonus adds points

### Phase 3: Conditions + Compound Actions (proposal c)
- Expand `_evaluate_condition` with game state, random probability, quarter checks
- Add `conditional_sequence` action type
- Add `random_ejection` primitive
- Add cross-possession tracking fields to `GameState`
- **Tests:** conditional_sequence fires steps in order; random_ejection ejects players; game_state conditions evaluate correctly

### Phase 4: Derived Stats + Entity Lifecycle (proposals d, e)
- Add `derive_pass_count` primitive (team passing → pass count → shot value)
- Add `swap_roster_player` primitive (generate temp hooper, swap in, restore at lifetime)
- **Tests:** pass count correlates with team passing attribute; crowd player appears and is restored

### Phase 5: Interpreter Prompt Update
- Update `INTERPRETER_V2_SYSTEM_PROMPT` with new primitives and conditions
- Update mock interpreter patterns for new action types
- **Tests:** integration — proposal text → interpretation → effect registration → simulation execution

---

## Files Modified

| File | What Changes |
|------|-------------|
| `src/pinwheel/core/state.py` | Add `PossessionContext`, tracking fields on `GameState` |
| `src/pinwheel/core/hooks.py` | Expand `HookResult` fields, `_apply_action_code` (7 new action types), `_evaluate_condition` (5 new condition types), fix `apply_hook_results` |
| `src/pinwheel/core/simulation.py` | `_fire_sim_effects` returns `PossessionContext`; `_run_quarter` and `_run_elam` pass it to `resolve_possession` |
| `src/pinwheel/core/possession.py` | `resolve_possession` accepts `PossessionContext`, applies modifiers at correct points; `select_action` and `check_turnover` accept modifier params |
| `src/pinwheel/core/scoring.py` | No changes needed — modifiers applied in `resolve_possession` after calling these functions |
| `src/pinwheel/ai/interpreter.py` | Update `INTERPRETER_V2_SYSTEM_PROMPT` with new primitives and conditions |
| `tests/test_simulation.py` | PossessionContext wiring tests |
| `tests/test_effects.py` | New primitive tests, condition expansion tests |
| `tests/test_possession.py` | PossessionContext modifier application tests |

## Verification

1. `uv run pytest -x -q` — all tests pass after each phase
2. `uv run ruff check src/ tests/` — lint clean
3. For each test proposal (b-e): manually construct the EffectSpec, register it, simulate 100 games with and without, verify statistical shift matches expected gameplay impact
4. Deploy and submit test proposals — verify interpreter produces correct EffectSpec JSON, effects register and fire, gameplay impact is visible in commentary and box scores
