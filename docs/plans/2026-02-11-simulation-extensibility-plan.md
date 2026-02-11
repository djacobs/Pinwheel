---
title: "feat: Simulation Engine Extensibility Architecture"
type: feat
date: 2026-02-11
---

# Simulation Engine Extensibility Architecture

## Problem

The Day 1 simulation engine must be a pure function with no side effects. But Day 2-3 adds Game Effects (conditional modifications within a game), and post-hackathon adds League Effects (cross-game modifications) and Fate events. If the Day 1 code doesn't have structural hooks for these, adding them means rewriting the possession model.

## Design: Hook Points in the Possession Model

The simulation engine fires **hooks** at defined points during the game. On Day 1, hooks do nothing (empty list of effects). On Day 2-3, Game Effects register against these hooks and modify the game state.

### Hook Architecture

```python
from enum import Enum
from typing import Protocol

class HookPoint(str, Enum):
    """Points in the simulation where effects can fire."""
    # Game-level
    GAME_START = "game_start"
    QUARTER_START = "quarter_start"
    QUARTER_END = "quarter_end"
    HALFTIME = "halftime"
    ELAM_START = "elam_start"
    GAME_END = "game_end"

    # Possession-level
    POSSESSION_START = "possession_start"
    POSSESSION_END = "possession_end"

    # Action-level
    ON_SCORE = "on_score"
    ON_MISS = "on_miss"
    ON_STEAL = "on_steal"
    ON_FOUL = "on_foul"
    ON_REBOUND = "on_rebound"
    ON_MOVE_TRIGGER = "on_move_trigger"

    # Momentum-level
    ON_LEAD_CHANGE = "on_lead_change"
    ON_RUN = "on_run"  # N consecutive scores by one team


class HookContext(BaseModel):
    """Snapshot of game state at the moment a hook fires."""
    game_state: GameState
    possession: PossessionState | None  # None for game-level hooks
    action_result: ActionResult | None  # None for pre-action hooks
    triggering_agent: AgentState | None
    defending_agent: AgentState | None


class EffectResult(BaseModel):
    """What an effect wants to modify."""
    score_modifier: int = 0
    attribute_modifiers: dict[str, dict[str, int]] = {}  # agent_id -> {attr: delta}
    stamina_modifier: dict[str, float] = {}  # agent_id -> delta
    grant_possession: bool = False
    force_substitution: str | None = None  # team_id
    probability_modifier: float = 1.0  # multiplier on next action probability
    foul_modifier: dict[str, int] = {}  # agent_id -> delta


class GameEffect(Protocol):
    """Interface for effects that can register on hooks."""
    def should_trigger(self, hook: HookPoint, context: HookContext) -> bool: ...
    def apply(self, context: HookContext) -> EffectResult: ...
```

### How It Integrates with the Possession Model

```python
def simulate_game(
    home: Team,
    away: Team,
    rules: RuleSet,
    seed: int,
    effects: list[GameEffect] | None = None,  # Day 1: None or []
) -> GameResult:
    """Pure function. Effects are an input, not a side effect."""
    rng = random.Random(seed)
    effects = effects or []
    game_state = GameState(home=home, away=away, rules=rules)

    for quarter in range(1, 5):  # Q1-Q4 (Q4 = Elam)
        _fire_hooks(HookPoint.QUARTER_START, game_state, effects)

        for possession_num in range(rules.quarter_possessions):
            possession = PossessionState(...)
            _fire_hooks(HookPoint.POSSESSION_START, game_state, effects)

            # --- Defensive setup ---
            scheme = select_scheme(offense, defense, game_state, rules, rng)
            matchups = assign_matchups(offense, defense, scheme, game_state, rng)

            # --- Action loop ---
            while not possession.resolved:
                action = select_action(ball_handler, scheme, matchups, game_state, rules, rng)
                result = resolve_action(action, scheme, matchups, game_state, rules, rng)

                # Check moves
                triggered_moves = check_moves(ball_handler, action, result, game_state)
                for move in triggered_moves:
                    result = apply_move(move, result)
                    _fire_hooks(HookPoint.ON_MOVE_TRIGGER, game_state, effects)

                # Fire result hooks
                if result.scored:
                    _fire_hooks(HookPoint.ON_SCORE, game_state, effects)
                    if game_state.lead_changed:
                        _fire_hooks(HookPoint.ON_LEAD_CHANGE, game_state, effects)
                elif result.missed:
                    _fire_hooks(HookPoint.ON_MISS, game_state, effects)
                elif result.turnover:
                    _fire_hooks(HookPoint.ON_STEAL, game_state, effects)
                if result.foul:
                    _fire_hooks(HookPoint.ON_FOUL, game_state, effects)

                # Update game state
                update_game_state(game_state, result)

            _fire_hooks(HookPoint.POSSESSION_END, game_state, effects)

            # Elam check
            if quarter >= rules.elam_trigger_quarter and game_state.target_reached:
                break

        _fire_hooks(HookPoint.QUARTER_END, game_state, effects)
        if quarter == 2:
            _fire_hooks(HookPoint.HALFTIME, game_state, effects)
            handle_halftime(game_state, rules)
        if quarter == rules.elam_trigger_quarter:
            game_state.set_elam_target(rules.elam_margin)
            _fire_hooks(HookPoint.ELAM_START, game_state, effects)

    _fire_hooks(HookPoint.GAME_END, game_state, effects)
    return game_state.to_result()


def _fire_hooks(
    hook: HookPoint,
    game_state: GameState,
    effects: list[GameEffect],
    max_depth: int = 3,  # safety boundary: prevent infinite chains
) -> None:
    """Check all registered effects and apply those that trigger."""
    context = HookContext.from_game_state(game_state)
    for effect in effects:
        if effect.should_trigger(hook, context):
            result = effect.apply(context)
            apply_effect_result(game_state, result)
            # Note: we do NOT re-fire hooks from effect results on Day 1.
            # When effect chaining is needed, decrement max_depth and recurse.
```

### Day 1: No Effects, Full Hooks

On Day 1, `effects=[]` (or `None`). The `_fire_hooks` calls iterate over an empty list — zero overhead. But the hook points are in the code, the `HookContext` is built, and the `GameEffect` protocol is defined. When Day 2 adds governance-created effects, they plug in without touching the possession model.

### Day 2-3: Game Effects from Governance

The governance pipeline produces `GameEffect` objects from proposals:

```
Governor proposes "Dunking gives an extra possession"
    → AI interpreter produces: GameEffect(trigger=ON_SCORE, condition=shot_type=="drive", action=grant_possession)
    → Pydantic validates against the EffectTrigger/Condition/Action/Scope/Duration enums
    → Vote passes → effect stored in DB
    → Game loop loads active effects → passes to simulate_game(effects=[...])
```

The effect classes implement the `GameEffect` protocol:

```python
class GovernedGameEffect:
    """A game effect created through governance."""
    def __init__(self, definition: GameEffectDefinition):
        self.definition = definition

    def should_trigger(self, hook: HookPoint, context: HookContext) -> bool:
        if hook != self.definition.trigger.to_hook_point():
            return False
        if self.definition.condition:
            return evaluate_condition(self.definition.condition, context)
        return True

    def apply(self, context: HookContext) -> EffectResult:
        return evaluate_action(self.definition.action, self.definition.scope, context)
```

### Post-Hackathon: League Effects

League Effects run OUTSIDE the simulation, in a post-processing step:

```python
def run_round(season, round_number, schedule, rules, effects, league_effects):
    # Step 1: Simulate all games (pure, independent)
    game_results = []
    for matchup in schedule[round_number]:
        result = simulate_game(
            home=matchup.home_team,
            away=matchup.away_team,
            rules=rules,
            seed=compute_seed(season, round_number, matchup),
            effects=effects,
        )
        game_results.append(result)

    # Step 2: Apply League Effects (post-processing)
    modified_results = apply_league_effects(game_results, league_effects, season)

    # Step 3: Store
    for result in modified_results:
        store_game_result(result)
```

The simulation contract is preserved — `simulate_game()` remains pure. League Effects are a separate, auditable layer.

### Fate Events

Fate events are modeled as a special `GameEffect` with high-Fate agents:

```python
class FateEffect:
    """A Fate event authored by Opus 4.6 during simulation."""
    def should_trigger(self, hook: HookPoint, context: HookContext) -> bool:
        if hook != HookPoint.POSSESSION_START:
            return False
        # Check if any agent on floor has Fate > threshold
        # Roll against fate_trigger_rate * agent.fate / 100
        return rng_check(context)

    def apply(self, context: HookContext) -> EffectResult:
        # Fate events are pre-generated by Opus 4.6 at game start
        # (or between possessions if we allow async Opus calls)
        return self.pregenerated_fate_event
```

Because Fate events follow the same `GameEffect` protocol, they plug into the same hook system. The only difference is their source: governance-created effects come from votes, Fate effects come from high-Fate agents and AI authorship.

## Mutable Game State Model

The simulation needs a mutable `GameState` that effects can modify:

```python
class AgentState(BaseModel):
    """Mutable agent state during a game."""
    agent_id: str
    team_id: str
    base_attributes: PlayerAttributes  # immutable originals
    current_attributes: PlayerAttributes  # modified by effects, stamina, venue
    current_stamina: float  # 0.0 to 1.0
    fouls: int
    is_ejected: bool
    is_on_floor: bool
    stats: AgentGameStats  # accumulates during game

class GameState:
    """Mutable state of a game in progress."""
    home_agents: list[AgentState]
    away_agents: list[AgentState]
    rules: RuleSet
    home_score: int
    away_score: int
    quarter: int
    possession_number: int
    possessions_log: list[PossessionLog]
    elam_target: int | None
    team_fouls: dict[str, int]  # team_id -> fouls this half
    active_effects: list[EffectResult]  # temporary effects with duration tracking
    rng: random.Random
```

`current_attributes` starts equal to `base_attributes` and is modified by:
- Venue modifiers (applied at game start)
- Stamina degradation (continuous)
- Game Effects (temporary, with duration tracking)
- Moves (per-action modifiers)

## Key Invariant

The simulation is still deterministic: `simulate_game(home, away, rules, seed, effects)` always produces the same `GameResult` for the same inputs. Effects are an input, not randomness. The `seed` drives all RNG.

## File Structure

```
core/
├── simulation.py       # simulate_game() top-level function, game loop
├── possession.py       # Possession model: action selection, resolution
├── defense.py          # Defensive model: scheme selection, matchup assignment
├── scoring.py          # Shot probability calculation, scoring resolution
├── hooks.py            # HookPoint enum, _fire_hooks, GameEffect protocol
├── effects.py          # GovernedGameEffect, FateEffect implementations (Day 2+)
├── moves.py            # Move definitions, trigger checking, application
├── state.py            # GameState, AgentState, PossessionState
├── scheduler.py        # Round-robin generation, matchup scheduling
├── rules.py            # Rule space definitions, validation, application logic
└── events.py           # GovernanceEvent types (for the event store)
```

## Acceptance Criteria

- [ ] Hook points exist in the possession model at all specified locations
- [ ] `effects=[]` produces zero overhead (no behavior change from un-hooked simulation)
- [ ] `GameEffect` protocol is defined and testable with mock effects
- [ ] A test can register a custom effect and verify it modifies game state
- [ ] GameState tracks mutable agent state separate from immutable base attributes
- [ ] Venue modifiers applied to current_attributes at game start
- [ ] Stamina degradation updates current_attributes continuously
- [ ] Simulation remains deterministic with effects (same inputs → same outputs)
