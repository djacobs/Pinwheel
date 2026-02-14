# Proposal Effects System — Architecture Plan

## Status: Draft
## Date: 2026-02-14
## Priority: P0

---

## 0. Design Philosophy

The current system maps proposals to one RuleSet parameter. That is the ceiling. A proposal that says "every team gets a morale score" or "the losing team plays the next game with 2 players" or "three-pointers are now called 'moonshots' and score 5 when made from the left side" has nowhere to go — it hits `parameter=None`, gets classified as Tier 5, gets flagged for admin review, and either gets vetoed or passes with zero mechanical effect.

This plan makes every proposal mechanically real. The goal is not to contain chaos but to give chaos a surface to land on. The game starts as basketball and finishes as ???. Governance proposals are how players INVENT A NEW GAME. Three pillars:

1. **Callbacks everywhere** — every meaningful moment in the system gets a hook point where registered effects can observe and mutate state
2. **Meta columns on everything** — every persistent object gets a `meta: JSON` column where effects can store arbitrary state without schema migrations
3. **An effect execution engine** — proposals produce structured effects that register against hook points, read/write meta, and have defined lifetimes

---

## 1. Callback Architecture: The Complete Hook Map

### 1.1 Current State

The existing `HookPoint` enum in `src/pinwheel/core/hooks.py` defines 11 hook points, all within the simulation. Only 4 are actually called (`PRE_POSSESSION`, `QUARTER_END`, `ELAM_START`, `GAME_END`). The `GameEffect` protocol is minimal.

### 1.2 New Hook Architecture

Replace the enum-based hook system with a string-based, hierarchical hook system. This allows proposals to create new hook points without code changes.

```
# Examples:
#   "sim.possession.pre"
#   "sim.quarter.end"
#   "round.pre"
#   "governance.tally.post"
#   "report.simulation.pre"
#   "season.phase.transition"
#   "custom.morale.check"  -- effects can register on custom hooks
```

**New Effect protocol:**

```python
class Effect(Protocol):
    @property
    def effect_id(self) -> str: ...
    @property
    def hook_points(self) -> list[str]: ...
    @property
    def lifetime(self) -> EffectLifetime: ...
    def should_fire(self, hook: str, context: HookContext) -> bool: ...
    def apply(self, hook: str, context: HookContext) -> HookResult: ...
```

**HookContext** — the unified context object passed to all effects:

```python
@dataclass
class HookContext:
    # Simulation context (populated during sim hooks)
    game_state: GameState | None = None
    hooper: HooperState | None = None
    possession_result: PossessionResult | None = None
    rules: RuleSet | None = None
    rng: random.Random | None = None

    # Round context (populated during round hooks)
    round_number: int = 0
    season_id: str = ""
    game_results: list[GameResult] | None = None
    teams: dict[str, Team] | None = None

    # Governance context (populated during gov hooks)
    proposal: Proposal | None = None
    tally: VoteTally | None = None

    # Report context (populated during report hooks)
    report_data: dict | None = None

    # Meta read/write interface
    meta_store: MetaStore | None = None

    # General
    event_bus: EventBus | None = None
```

**HookResult** — effects return mutations:

```python
@dataclass
class HookResult:
    meta_writes: dict[str, dict[str, object]] | None = None
    score_modifier: int = 0
    stamina_modifier: float = 0.0
    shot_probability_modifier: float = 0.0
    block_action: bool = False
    substitute_action: str | None = None
    narrative: str = ""
    events_to_emit: list[dict] | None = None
    expired: bool = False
```

### 1.3 Complete Hook Point Catalog

#### Simulation Hooks (`simulation.py`, `possession.py`)

| Hook Point | Where Fired | What Can Be Modified |
|---|---|---|
| `sim.game.pre` | Top of `simulate_game()` | Meta on teams/hoopers, initial game state |
| `sim.quarter.pre` | Start of `_run_quarter()` | Game state, stamina, meta |
| `sim.possession.pre` | Before `resolve_possession()` | Shot selection weights, turnover rates |
| `sim.action_selected` | After `select_action()` | Override shot type, modify probabilities |
| `sim.shot.pre` | Before `resolve_shot()` | Modify shot probability, block shot |
| `sim.shot.post` | After `resolve_shot()` | Modify points scored, bonus effects |
| `sim.foul.pre` | Before `check_foul()` | Modify foul probability |
| `sim.foul.post` | After foul assessed | Override ejection, modify limits |
| `sim.rebound.pre` | Before `attempt_rebound()` | Modify rebound weights |
| `sim.rebound.post` | After rebound resolved | Trigger bonus on offensive rebound |
| `sim.turnover` | On turnover | Add consequences |
| `sim.substitution` | On sub | Block or modify subs |
| `sim.quarter.end` | End of `_run_quarter()` | Recovery rates, meta updates |
| `sim.halftime` | Between Q2 and Q3 | Recovery rates, mid-game effects |
| `sim.elam.start` | Top of `_run_elam()` | Modify elam target |
| `sim.game.end` | After all play | Post-game meta (streaks, morale) |

#### Round Hooks (`game_loop.py`)

| Hook Point | Where Fired | What Can Be Modified |
|---|---|---|
| `round.pre` | Top of `step_round()` | Pre-game meta (between-game effects) |
| `round.game.pre` | Before each `simulate_game()` | Modify ruleset per-game, inject effects |
| `round.game.post` | After each `simulate_game()` | Post-game meta (streaks, rivalries) |
| `round.post` | After all games, before governance | Between-round meta state |
| `round.complete` | After governance + reports + evals | Final cleanup, narrative injection |

#### Governance Hooks (`governance.py`)

| Hook Point | Where Fired | What Can Be Modified |
|---|---|---|
| `gov.proposal.submitted` | After submission | Meta on proposal |
| `gov.vote.cast` | After vote | Meta on vote (secret ballot effects) |
| `gov.tally.pre` | Before tallying | Vote weights, thresholds |
| `gov.tally.post` | After tally, before enacting | Override tally results |
| `gov.rule.enacted` | After rule change applied | Trigger cascading effects |

#### Report Hooks (`report.py`, `commentary.py`)

| Hook Point | Where Fired | What Can Be Modified |
|---|---|---|
| `report.simulation.pre` | Before building sim report prompt | Inject effects context |
| `report.governance.pre` | Before gov report prompt | Inject effects context |
| `report.private.pre` | Before private report | Personalized effects context |
| `report.commentary.pre` | Before game commentary | Custom game context |

#### Season Lifecycle Hooks (`season.py`)

| Hook Point | Where Fired |
|---|---|
| `season.phase.transition` | When season status changes |
| `season.regular_complete` | When regular season ends |
| `season.playoffs.start` | When playoff bracket generated |
| `season.champion` | When champion crowned |

---

## 2. Meta Columns

### 2.1 Database Changes

Add `meta: JSON` (nullable, default `{}`) to:

| Table | ORM Class | Rationale |
|---|---|---|
| `teams` | `TeamRow` | Morale, swagger, win streaks, custom team properties |
| `hoopers` | `HooperRow` | Stamina penalties, buffs, custom hooper state |
| `game_results` | `GameResultRow` | Custom scoring records, effect activations |
| `seasons` | `SeasonRow` | Effect-written state (separate from `config`) |
| `schedule` | `ScheduleRow` | Pre-game modifiers, venue overrides |
| `box_scores` | `BoxScoreRow` | Per-hooper-per-game effect data |
| `players` | `PlayerRow` | Governor-level effect state |

### 2.2 Migration Script

`scripts/migrate_add_meta.py` — safe `ALTER TABLE ... ADD COLUMN` for each table. Additive, nullable, existing data unaffected. Works on both SQLite and PostgreSQL.

### 2.3 MetaStore: In-Memory Read/Write Interface

`src/pinwheel/core/meta.py` — loaded at round start from DB, effects read/write during round, flushed to DB at round end.

```python
class MetaStore:
    def get(self, entity_type: str, entity_id: str, field: str, default=None) -> object: ...
    def set(self, entity_type: str, entity_id: str, field: str, value: object) -> None: ...
    def get_all(self, entity_type: str, entity_id: str) -> dict[str, object]: ...
```

---

## 3. Proposal Interpretation: Beyond Parameters

### 3.1 New Models (`models/governance.py`)

```python
class EffectSpec(BaseModel):
    effect_type: Literal["parameter_change", "meta_mutation", "hook_callback", "narrative", "composite"]

    # parameter_change (backward compatible)
    parameter: str | None = None
    new_value: int | float | bool | None = None
    old_value: int | float | bool | None = None

    # meta_mutation
    target_type: str | None = None      # "team", "hooper", "game", "season"
    target_selector: str | None = None   # "all", "winning_team", specific ID
    meta_field: str | None = None
    meta_value: object = None
    meta_operation: str = "set"          # "set", "increment", "decrement", "toggle"

    # hook_callback
    hook_point: str | None = None
    condition: str | None = None         # Natural language condition
    action: str | None = None            # Natural language action
    action_code: str | None = None       # Structured action spec (JSON)

    # narrative
    narrative_instruction: str | None = None

    # lifetime
    duration: str = "permanent"          # "permanent", "N_rounds", "one_game", "until_repealed"
    duration_rounds: int | None = None

    description: str = ""


class ProposalInterpretation(BaseModel):
    effects: list[EffectSpec] = Field(default_factory=list)
    impact_analysis: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    clarification_needed: bool = False
    injection_flagged: bool = False
    rejection_reason: str | None = None
    original_text_echo: str = ""
```

### 3.2 New Interpreter Prompt

`ai/interpreter.py` — `interpret_proposal_v2()` tells the AI about all available hook points, meta targets, and action primitives. Every proposal produces at least one effect. The AI is instructed to prefer mechanical effects over narrative-only.

### 3.3 Structured Action Primitives

```python
ACTION_PRIMITIVES = {
    "modify_score": {"modifier": int},
    "modify_probability": {"modifier": float},
    "modify_stamina": {"target": str, "modifier": float},
    "force_turnover": {"probability": float},
    "write_meta": {"entity": str, "field": str, "value": object, "op": str},
    "emit_event": {"event_type": str, "data": dict},
    "modify_foul_limit": {"modifier": int},
    "block_substitution": {},
    "add_narrative": {"text": str},
}
```

---

## 4. Effect Execution Engine

### 4.1 EffectRegistry (`core/effects.py`)

```python
class EffectRegistry:
    def register(self, effect: RegisteredEffect) -> None: ...
    def deregister(self, effect_id: str) -> None: ...
    def get_effects_for_hook(self, hook: str) -> list[RegisteredEffect]: ...
    def get_all_active(self) -> list[RegisteredEffect]: ...
    def tick_round(self, current_round: int) -> list[str]:  # returns expired IDs
    def get_narrative_effects(self) -> list[RegisteredEffect]: ...
```

### 4.2 Persistence

Effects stored as append-only governance events: `effect.registered`, `effect.expired`, `effect.repealed`. Registry rebuilt from event store at round start via `load_effect_registry()`.

### 4.3 Enacting Effects

In `tally_governance()`, after a proposal passes:
- `parameter_change` effects → existing `apply_rule_change()` path
- All other effects → `effect.registered` event in store

### 4.4 Simulation Integration

`simulate_game()` accepts `effect_registry` and `meta_store`. At each hook point:

```python
results = fire_effects("sim.shot.post", context, effect_registry)
apply_results(results, context)
```

### 4.5 Condition Evaluation

- **Simulation hooks (hot path):** Pre-compiled `action_code` with structured condition checks
- **Round/governance hooks:** Can use lightweight AI evaluation for complex conditions

---

## 5. How the Reporter Absorbs Chaos

### 5.1 Effects Context Injection

Before generating any report, build a human-readable summary of active effects and meta state, inject into the system prompt as `## Active Effects`.

### 5.2 Narrative Effects

Effects with `narrative_instruction` instruct the reporter directly. "The league is now called the Chaos Basketball Association" → reporter adopts it. These are mechanically real — the AI treats them as ground truth.

### 5.3 Commentary Integration

Track which effects fired during a game, include in commentary context.

---

## 6. Implementation Phases

### Phase 1: Meta Columns (safe, immediate)
1. Migration script `scripts/migrate_add_meta.py`
2. Add `meta` to ORM models
3. Run migration locally + production
4. Add `MetaStore` in `core/meta.py`
5. Add `update_*_meta()` repository methods

### Phase 2: Hook Architecture (backward compatible)
1. New `HookContext`, `HookResult`, `Effect` protocol in `core/hooks.py`
2. Keep `fire_hooks()` working, add `fire_effects()` alongside
3. Add `effect_registry` and `meta_store` params to `simulate_game()` (default None)
4. Wire hook fire points into simulation, possession, scoring

### Phase 3: New Interpreter (parallel path)
1. Add `EffectSpec`, `ProposalInterpretation` to `models/governance.py`
2. Add `interpret_proposal_v2()` in `ai/interpreter.py`
3. Feature flag `PINWHEEL_EFFECTS_V2=true` to switch interpreters
4. Update `ProposalConfirm` Discord view for multi-effect display

### Phase 4: Effect Execution (completes the loop)
1. `EffectRegistry` + `load_effect_registry()` in `core/effects.py`
2. Effect registration path in `tally_governance()`
3. Registry loading + MetaStore creation in `step_round()`
4. Wire into `simulate_game()` call
5. Effects context into report prompts

### Phase 5: Tests
1. Unit tests for effect interpretation (mock + AI)
2. Tests for `RegisteredEffect` applying via hooks
3. Tests for MetaStore load/flush cycle
4. Tests for narrative injection into reports
5. Integration tests for full proposal → effect → simulation → report lifecycle

---

## 7. Safety Rails

- Effects are append-only in the event store. Repeal by adding `effect.repealed` event.
- `action_code` is a closed vocabulary of primitives — no arbitrary code.
- Effects have lifetimes. Permanent effects can be repealed via governance.
- Admin veto still works pre-tally. Post-enactment, admin can repeal.
- Meta columns are JSON. Malformed writes are just values in a nullable column.
- Backward compatible: all existing parameterized proposals continue to work.

---

## 8. Example: End-to-End

**Proposal:** "Every team that wins by 20+ gets a swagger rating that goes up by 1. Teams with swagger 5+ get a 5% shooting boost."

**AI Interpretation:**

```json
{
  "effects": [
    {
      "effect_type": "hook_callback",
      "hook_point": "round.game.post",
      "condition": "winner margin >= 20",
      "action_code": {"type": "write_meta", "entity": "team:{winner_team_id}", "field": "swagger", "value": 1, "op": "increment"},
      "duration": "permanent",
      "description": "Winning by 20+ increases team swagger by 1"
    },
    {
      "effect_type": "hook_callback",
      "hook_point": "sim.shot.pre",
      "condition": "offense team swagger >= 5",
      "action_code": {"type": "modify_probability", "modifier": 0.05, "condition_check": {"meta_field": "swagger", "entity_type": "team", "gte": 5}},
      "duration": "permanent",
      "description": "Teams with swagger 5+ get 5% shooting boost"
    },
    {
      "effect_type": "narrative",
      "narrative_instruction": "Track and report on team swagger ratings. Mention swagger in game commentary when relevant.",
      "duration": "permanent"
    }
  ],
  "impact_analysis": "Creates a snowball mechanic: dominant teams get progressively stronger. This could motivate underdogs to govern swagger limits.",
  "confidence": 0.9
}
```

---

## New/Modified Files

| File | Status | Purpose |
|---|---|---|
| `core/meta.py` | NEW | MetaStore in-memory cache |
| `core/effects.py` | NEW | EffectRegistry, RegisteredEffect, load_effect_registry() |
| `core/hooks.py` | REWRITE | HookContext, HookResult, Effect protocol, fire_effects() + legacy compat |
| `models/governance.py` | EXTEND | Add EffectSpec, ProposalInterpretation |
| `db/models.py` | EXTEND | Add meta columns to 7 tables |
| `db/repository.py` | EXTEND | Add update_*_meta() methods |
| `ai/interpreter.py` | EXTEND | Add interpret_proposal_v2() with new prompt |
| `core/simulation.py` | EXTEND | Accept registry + meta_store, add hook fire points |
| `core/possession.py` | EXTEND | Add hook fire points between possession steps |
| `core/game_loop.py` | EXTEND | Load registry, create MetaStore, flush after round |
| `core/governance.py` | EXTEND | Register effects for passing proposals |
| `ai/report.py` | EXTEND | Build effects context, inject into prompts |
| `ai/commentary.py` | EXTEND | Include effects in game commentary context |
| `scripts/migrate_add_meta.py` | NEW | Migration script for meta columns |
