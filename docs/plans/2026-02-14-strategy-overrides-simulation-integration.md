# Plan: Strategy Overrides — Simulation Integration Audit and Completion

**Date:** 2026-02-14
**Status:** Draft
**Scope:** Simulation engine, AI interpreter, defense module

## Context

SIMULATION.md describes strategy overrides as "one of the richest governance surfaces in
the game." Governors submit natural language strategy instructions via the `/strategy`
Discord command. The AI interpreter parses these into structured `TeamStrategy` parameters.
The simulation engine is supposed to use these parameters to modify gameplay decisions.

The full pipeline exists: Discord command -> AI interpretation -> structured `TeamStrategy` ->
stored in governance events -> loaded in game loop -> passed to `simulate_game()`. But a
detailed trace reveals that **strategy parameters have uneven and incomplete integration into
the actual simulation calculations**. Some parameters meaningfully modify gameplay; others are
accepted and stored but silently ignored during simulation.

## What Exists Today

### TeamStrategy Model (`src/pinwheel/models/team.py`, lines 64-74)

```python
class TeamStrategy(BaseModel):
    three_point_bias: float = Field(default=0.0, ge=-20.0, le=20.0)
    mid_range_bias: float = Field(default=0.0, ge=-20.0, le=20.0)
    at_rim_bias: float = Field(default=0.0, ge=-20.0, le=20.0)
    defensive_intensity: float = Field(default=0.0, ge=-0.5, le=0.5)
    pace_modifier: float = Field(default=1.0, ge=0.7, le=1.3)
    substitution_threshold_modifier: float = Field(default=0.0, ge=-0.15, le=0.15)
    raw_text: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
```

Six tunable parameters. Each is well-defined with sensible ranges.

### AI Interpreter (`src/pinwheel/ai/interpreter.py`, lines 186-664)

The `STRATEGY_SYSTEM_PROMPT` (lines 186-227) maps basketball concepts to parameters:
- "Run and gun" -> low pace_modifier, higher at_rim_bias
- "Shoot the three" -> high three_point_bias
- "Lock down defense" -> high defensive_intensity
- "Ride the starters" -> negative substitution_threshold_modifier

Both the real AI interpreter (`interpret_strategy()`) and the mock
(`interpret_strategy_mock()`) produce valid `TeamStrategy` objects. The mock has
comprehensive keyword matching for all six parameters.

### Game State Access (`src/pinwheel/core/state.py`, lines 90-91, 131-138)

`GameState` stores strategies and provides convenience properties:

```python
home_strategy: TeamStrategy | None = None
away_strategy: TeamStrategy | None = None

@property
def offense_strategy(self) -> TeamStrategy | None:
    return self.home_strategy if self.home_has_ball else self.away_strategy

@property
def defense_strategy(self) -> TeamStrategy | None:
    return self.away_strategy if self.home_has_ball else self.home_strategy
```

This is clean -- any code in the possession model can access the current offense or
defense strategy through these properties.

### Strategy Loading in Game Loop (`src/pinwheel/core/game_loop.py`, lines 894-909)

Strategies are loaded from governance events at the start of each round:

```python
strategies: dict[str, TeamStrategy] = {}
for tid in teams_cache:
    strat_events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["strategy.interpreted"],
    )
    for evt in reversed(strat_events):
        if evt.payload.get("team_id") == tid:
            strategies[tid] = TeamStrategy(**evt.payload.get("strategy", {}))
            break
```

The most recent strategy event per team is used. Strategies are then passed to
`simulate_game()`.

### Simulation Entry Point (`src/pinwheel/core/simulation.py`, lines 310-343)

```python
def simulate_game(
    ...
    home_strategy: TeamStrategy | None = None,
    away_strategy: TeamStrategy | None = None,
    ...
) -> GameResult:
    game_state = GameState(
        home_agents=_build_hooper_states(home),
        away_agents=_build_hooper_states(away),
        home_strategy=home_strategy,
        away_strategy=away_strategy,
    )
```

Strategies are set on `GameState` at game creation and available throughout.

## Parameter-by-Parameter Integration Audit

### 1. `three_point_bias` -- INTEGRATED

**Where used:** `src/pinwheel/core/possession.py`, `select_action()`, lines 78-83

```python
strategy = game_state.offense_strategy
if strategy:
    weights["at_rim"] += strategy.at_rim_bias
    weights["mid_range"] += strategy.mid_range_bias
    weights["three_point"] += strategy.three_point_bias
```

**Verdict:** Fully functional. A positive `three_point_bias` increases the weight for
three-point shot selection. The weight system uses `max(1.0, ...)` to prevent negative
weights, so extreme negative biases bottom out at 1.0 rather than breaking.

**Gameplay impact:** A team with `three_point_bias=15` will take noticeably more threes.
Combined with a sharpshooter hooper, this creates a clear team identity.

### 2. `mid_range_bias` -- INTEGRATED

**Where used:** Same location as `three_point_bias`.

**Verdict:** Fully functional. Same mechanism.

### 3. `at_rim_bias` -- INTEGRATED

**Where used:** Same location as `three_point_bias`.

**Verdict:** Fully functional. Same mechanism.

### 4. `defensive_intensity` -- PARTIALLY INTEGRATED

**Where used:** `src/pinwheel/core/possession.py`, `resolve_possession()`, lines 251-253

```python
def_strategy = game_state.defense_strategy
if def_strategy:
    scheme_mod += def_strategy.defensive_intensity
```

**What it does:** Adds to the contest modifier (`scheme_mod`), which is then passed to
`resolve_shot()` -> `compute_contest()`. Higher `defensive_intensity` means tighter
contests on shots.

**What it does NOT do:**
- Does NOT increase foul rate. SIMULATION.md says high defensive intensity should cause
  more fouls and more fatigue: "Positive = tighter defense but more fouls/fatigue."
  Currently, `check_foul()` in `possession.py` does not read `defensive_intensity`.
- Does NOT increase stamina drain for defenders. `drain_stamina()` uses scheme-based
  drain rates but ignores the strategy's defensive intensity.
- Does NOT influence scheme selection. `select_scheme()` in `defense.py` does not
  consider the team's strategy at all -- it uses only attribute-based heuristics and
  game state.

**Verdict:** Partially functional. The shot contest effect works. The foul and stamina
side effects described in the strategy prompt are missing. Scheme selection is completely
independent of strategy.

### 5. `pace_modifier` -- INTEGRATED

**Where used:** `src/pinwheel/core/possession.py`, `resolve_possession()`, line 236-237

```python
pace = game_state.offense_strategy.pace_modifier if game_state.offense_strategy else 1.0
time_used = compute_possession_duration(rules, rng, pace_modifier=pace)
```

And in `compute_possession_duration()`, lines 214-225:

```python
def compute_possession_duration(rules, rng, pace_modifier=1.0):
    play_time = rules.shot_clock_seconds * rng.uniform(0.4, 1.0)
    dead_time = rules.dead_ball_time_seconds
    return (play_time * pace_modifier) + dead_time
```

**What it does:** Modifies how much game clock each possession consumes. Lower
`pace_modifier` = faster possessions = more possessions per quarter = more opportunities
to score.

**What it does NOT do:**
- Does NOT affect shot selection (e.g., fast pace should bias toward transition/rim
  attacks). The strategy prompt says "Run and gun -> low pace_modifier, higher at_rim_bias"
  -- but these are separate parameters. A governor could set a fast pace without the
  at_rim_bias, and the simulation would play fast but still take the same shot distribution.
  This is arguably correct (the governor controls both levers independently), but worth
  noting.
- Does NOT affect stamina drain. Faster pace logically means more physical effort, but
  `drain_stamina()` is called once per possession regardless of pace.

**Verdict:** Functionally integrated for clock management. The indirect gameplay
consequence (more possessions per quarter at faster pace) works naturally because
`_run_quarter()` loops on game clock. Missing: stamina interaction with pace.

### 6. `substitution_threshold_modifier` -- INTEGRATED

**Where used:** `src/pinwheel/core/simulation.py`, `_check_substitution()`, lines 121-125

```python
threshold = rules.substitution_stamina_threshold
strategy = game_state.home_strategy if is_home else game_state.away_strategy
if strategy:
    threshold += strategy.substitution_threshold_modifier
```

**Verdict:** Fully functional. Positive modifier = sub earlier (preserve stamina).
Negative modifier = ride starters longer. The check runs at quarter breaks.

## Summary of Gaps

| Parameter | Shot Selection | Shot Contest | Foul Rate | Stamina | Scheme Selection | Substitution |
|-----------|:---:|:---:|:---:|:---:|:---:|:---:|
| three_point_bias | YES | - | - | - | - | - |
| mid_range_bias | YES | - | - | - | - | - |
| at_rim_bias | YES | - | - | - | - | - |
| defensive_intensity | - | YES | NO | NO | NO | - |
| pace_modifier | - | - | - | NO | - | - |
| substitution_threshold_modifier | - | - | - | - | - | YES |

The three shot-selection biases and substitution modifier are fully integrated.
`pace_modifier` works for clock management but lacks stamina interaction.
`defensive_intensity` only affects shot contest -- missing foul rate, stamina drain,
and scheme selection influence.

## What Needs to Be Built

### Fix 1: Defensive Intensity Affects Foul Rate

**File:** `src/pinwheel/core/possession.py`, `check_foul()` function

Currently:
```python
def check_foul(defender, shot_type, scheme, rng, rules=None):
    base_foul_rate = 0.08
    modifier = rules.foul_rate_modifier if rules else 1.0
    scheme_add = {"man_tight": 0.03, "press": 0.04, "man_switch": 0.01, "zone": 0.0}
    iq_penalty = max(0, (50 - defender.current_attributes.iq)) / 500.0
    foul_prob = (base_foul_rate * modifier) + scheme_add[scheme] + iq_penalty
    return rng.random() < min(0.25, foul_prob)
```

**Change:** Add `game_state` or `defensive_intensity` parameter. Apply intensity as
an additive foul rate modifier:

```python
def check_foul(
    defender, shot_type, scheme, rng,
    rules=None,
    defensive_intensity: float = 0.0,
):
    ...
    intensity_add = max(0.0, defensive_intensity) * 0.08  # +0.5 intensity -> +4% foul rate
    foul_prob = (base_foul_rate * modifier) + scheme_add[scheme] + iq_penalty + intensity_add
    ...
```

**Callers to update:** `resolve_possession()` in `possession.py` -- pass the defense
strategy's `defensive_intensity` to `check_foul()`.

### Fix 2: Defensive Intensity Affects Stamina Drain

**File:** `src/pinwheel/core/possession.py`, `drain_stamina()` function

Currently:
```python
def drain_stamina(agents, scheme, is_defense, rules=None):
    base_drain = rules.stamina_drain_rate if rules else 0.007
    scheme_drain = SCHEME_STAMINA_COST[scheme] if is_defense else 0.003
    for agent in agents:
        recovery = agent.hooper.attributes.stamina / 3000.0
        drain = base_drain + scheme_drain - recovery
        agent.current_stamina = max(0.15, agent.current_stamina - max(0, drain))
```

**Change:** Add a `defensive_intensity` parameter for defensive agents. Higher intensity
increases their stamina drain:

```python
def drain_stamina(
    agents, scheme, is_defense,
    rules=None,
    defensive_intensity: float = 0.0,
):
    base_drain = rules.stamina_drain_rate if rules else 0.007
    scheme_drain = SCHEME_STAMINA_COST[scheme] if is_defense else 0.003
    intensity_drain = (max(0.0, defensive_intensity) * 0.005) if is_defense else 0.0
    for agent in agents:
        recovery = agent.hooper.attributes.stamina / 3000.0
        drain = base_drain + scheme_drain + intensity_drain - recovery
        agent.current_stamina = max(0.15, agent.current_stamina - max(0, drain))
```

**Callers to update:** Both `drain_stamina()` calls in `resolve_possession()`.

### Fix 3: Pace Modifier Affects Stamina Drain

**Rationale:** Faster pace means the offense is pushing tempo -- both teams should drain
stamina slightly faster.

**File:** `src/pinwheel/core/possession.py`, `drain_stamina()` function

Add an optional `pace_modifier` parameter:

```python
def drain_stamina(
    agents, scheme, is_defense,
    rules=None,
    defensive_intensity: float = 0.0,
    pace_modifier: float = 1.0,
):
    ...
    # Faster pace (< 1.0) increases drain; slower pace (> 1.0) decreases
    pace_drain = (1.0 - pace_modifier) * 0.003  # fast pace: +0.003 drain
    drain = base_drain + scheme_drain + intensity_drain + pace_drain - recovery
    ...
```

### Fix 4: Strategy Influences Scheme Selection

**Rationale:** SIMULATION.md describes strategy overrides as able to force scheme choices:
"Always put our best defender on their highest scorer", "Switch to zone when we're up by 8",
"Press in the Elam period." The current `select_scheme()` ignores strategy entirely.

**File:** `src/pinwheel/core/defense.py`, `select_scheme()` function

**Approach:** The current `TeamStrategy` model does not have explicit scheme-override
fields. SIMULATION.md envisions richer `StrategyInstruction` objects with conditions and
scheme overrides. For now, `defensive_intensity` can influence scheme selection as a proxy:

```python
def select_scheme(
    offense, defense, game_state, rules, rng,
    strategy: TeamStrategy | None = None,
):
    ...
    # Strategy influence on scheme selection
    if strategy:
        if strategy.defensive_intensity > 0.2:
            weights["man_tight"] += 1.0
            weights["press"] += 0.5
        elif strategy.defensive_intensity < -0.1:
            weights["zone"] += 1.0
    ...
```

**Callers to update:** `resolve_possession()` in `possession.py` -- pass the defense
strategy to `select_scheme()`.

**Future:** When the `StrategyInstruction` / `StrategyCondition` / `StrategyAction`
models from SIMULATION.md are implemented, `select_scheme()` would check active
instructions against game state and apply direct scheme overrides. This plan does not
implement the full instruction system, only the `defensive_intensity` -> scheme influence
bridge.

### Fix 5: Strategy in Game Result Metadata

**Rationale:** For the AI reporter to connect strategy to outcomes, the GameResult should
record which strategies were active.

**File:** `src/pinwheel/models/game.py`, `GameResult` model

Add optional fields:

```python
class GameResult(BaseModel):
    ...
    home_strategy_summary: str = ""   # Raw text of active strategy
    away_strategy_summary: str = ""
```

**File:** `src/pinwheel/core/simulation.py`, `simulate_game()`

Populate the new fields from the strategy objects:

```python
result = GameResult(
    ...
    home_strategy_summary=home_strategy.raw_text if home_strategy else "",
    away_strategy_summary=away_strategy.raw_text if away_strategy else "",
)
```

## Files to Modify

| File | Change |
|------|--------|
| `src/pinwheel/core/possession.py` | `check_foul()`: add defensive_intensity param. `drain_stamina()`: add defensive_intensity + pace_modifier params. `resolve_possession()`: pass strategy params to all callees. |
| `src/pinwheel/core/defense.py` | `select_scheme()`: accept and use `TeamStrategy` for scheme weight adjustments. |
| `src/pinwheel/models/game.py` | `GameResult`: add strategy summary fields. |
| `src/pinwheel/core/simulation.py` | `simulate_game()`: populate strategy summaries on GameResult. |

## Testing Strategy

### Unit Tests

- **`tests/test_simulation.py`** (extend)
  - Game with `defensive_intensity=0.5` produces more fouls than default
  - Game with `defensive_intensity=0.5` produces lower average defender stamina at game end
  - Game with `pace_modifier=0.8` produces more possessions per quarter than default
  - Game with `pace_modifier=0.8` produces lower average stamina at game end
  - Game with `defensive_intensity=0.4` selects `man_tight` more often than default
  - Game with `defensive_intensity=-0.3` selects `zone` more often than default

- **`tests/test_possession.py`** (new or extend)
  - `check_foul()` with `defensive_intensity=0.5` returns True more often (statistical test
    over 1000 trials)
  - `drain_stamina()` with `defensive_intensity=0.3` drains more than without
  - `drain_stamina()` with `pace_modifier=0.8` drains more than with `pace_modifier=1.0`
  - `select_action()` shot distribution shifts with strategy biases (existing tests cover this)

- **`tests/test_defense.py`** (extend)
  - `select_scheme()` with high-intensity strategy favors man_tight/press
  - `select_scheme()` with low-intensity strategy favors zone
  - `select_scheme()` without strategy uses existing heuristics (regression test)

### Integration Tests

- **`tests/test_game_loop.py`** (extend)
  - Strategy loaded from governance events is correctly passed to `simulate_game()`
  - Strategy summary appears in stored GameResult metadata
  - Two identical games with different strategies produce different outcomes (statistical:
    run 100 games each, compare distributions)

### Determinism Tests

- Games with the same seed and same strategy produce identical results
- Adding strategy does not break determinism of games without strategy (regression)

## Risks and Open Questions

1. **Magnitude tuning:** The coefficients (0.08 for foul rate, 0.005 for stamina drain,
   0.003 for pace drain) are initial guesses. They need tuning from batch simulation runs.
   Too strong and strategy becomes overpowered; too weak and governors feel their strategy
   has no effect. Recommend: run 1000 games with various strategy settings and compare
   statistical distributions of fouls, stamina, and possessions.

2. **Negative defensive intensity:** The strategy prompt allows negative values
   ("relax defense"). This should logically reduce contest strength, foul rate, and
   stamina drain. The current `scheme_mod += defensive_intensity` already handles the
   contest reduction. The foul fix should use `max(0.0, defensive_intensity)` for the
   foul adder (relaxed defense should not reduce fouls below base rate), but the stamina
   fix could allow slightly lower drain. Need to decide.

3. **Full `StrategyInstruction` system:** SIMULATION.md envisions conditional strategy
   overrides: "Press in the Elam period", "Zone when up by 8." This plan does NOT
   implement that system. It only deepens the integration of the existing
   6-parameter `TeamStrategy`. The conditional instruction system is a separate, larger
   feature that would require:
   - New Pydantic models (`StrategyInstruction`, `StrategyCondition`, `StrategyAction`)
   - A new interpreter prompt section for conditional strategies
   - Per-possession condition evaluation in `resolve_possession()`
   - Significant additional testing

   This is a good Phase 2 plan after the current integration gaps are closed.

4. **Strategy visibility:** Should the opposing team be able to see the active strategy?
   Currently strategies are stored as governance events with no access control. If
   strategy is meant to be private (a competitive advantage), the API and Discord bot
   should not expose another team's strategy. The DB already stores them per-team;
   access control needs to be enforced at the API layer.
