# World 2: Event Pipeline Architecture

*Design document for the complete Pinwheel simulation rewrite.*
*No backwards compatibility. Players, teams, and hooper stats are preserved. Everything else is rebuilt.*

---

## 1. The Problem

The current architecture has basketball baked into the engine. Every time a player proposes something that pushes past known concepts, we add a branch. Two weeks of branches.

**Where special-casing lives today:**

**`hooks.py` — `_evaluate_condition()` (was 150 lines):**
Eight `if "condition_type" in condition:` branches. Every new gate type (`shot_zone`, `last_result`, `game_state_check`, `ball_handler_attr`, etc.) needs a new branch. This was replaced in Session 115 with a generic reflective evaluator — but that's a band-aid. The evaluator still lives on `RegisteredEffect`, which is itself a basketball-aware object.

**`hooks.py` — `_apply_action_code()` (~200 lines):**
`elif action_type == "modify_score":` … `elif action_type == "swap_roster_player":` … `elif action_type == "conditional_sequence":`. Every new action primitive a player invents needs a new branch.

**`simulation.py` — the game loop:**
`_run_quarter()` and `_run_elam()` are procedures that know about quarters, Elam endings, shot clocks. The "hook" system is a bolt-on: effects can observe, but they can't change what events exist. `sim.shot.post` doesn't exist in the engine — the AI generates it because it's semantically correct, but nothing fires it.

**`possession.py` (400+ lines):**
Every step is hardcoded: select handler, select shot type from {at_rim, mid_range, three_point}, resolve shot, handle foul, handle rebound, drain stamina. This is basketball. A player can propose that possession works differently — they can't reach in to change these steps.

**`scoring.py` — hardcoded shot types:**
```python
ShotType = Literal["at_rim", "mid_range", "three_point", "free_throw"]
BASE_MIDPOINTS = {"at_rim": 30.0, "mid_range": 40.0, "three_point": 50.0}
```
If a player proposes a new shot zone, these constants need updating.

**The `EffectSpec` / `ProposalInterpretation` schema:**
The AI generates `effect_type`, `hook_callback`, `conditional_sequence`, `meta_mutation` — implementation-specific concepts from our Python class hierarchy. The AI shouldn't know about these. It should say what should happen, not which Python primitive to call.

**The free throw problem:**
Free throws are hardcoded in `possession.py` lines ~460–500. A player proposing "free throws worth 2 points" or "4 free throws after any foul in Elam" can't be handled without code changes. This is the litmus test. **If free throws can't be expressed as data, the design isn't expansive enough.**

---

## 2. The Vision: Simulation as Event Pipeline

The new engine has no basketball knowledge. Basketball is the *default ruleset* — a set of rules expressed as data. The engine is a pure event loop:

```
fire(event_name, data, context)
  → find matching rules
  → evaluate conditions (generic field expressions)
  → collect mutations
  → apply mutations to event data and game state
  → fire emitted sub-events
  → return result
```

**A possession in the new model:**

```
possession.start      → rules select ball handler
shot.selected         → rules set shot type weights, select shot  ← "shot zone" is here
shot.attempted        → rules influence probability
shot.resolved         → rules score points, apply hot-hand effects  ← "shot.post" exists now
foul.check            → rules trigger free throw sequence
free_throw.sequence   → rules set attempt count
free_throw.attempt    → rules resolve each attempt
free_throw.resolved   → rules score the point
rebound.contested     → rules resolve rebound
stamina.drain         → rules control drain rates
possession.end        → rules track cross-possession state
```

Every one of these is a real event the engine fires. Rules listen to events. A player proposes something that affects a `shot.resolved` event — that works, because `shot.resolved` exists.

**Free throws as data (the litmus test):**

```json
[
  {
    "on": "foul.committed",
    "when": {"field": "event.shot_made", "eq": false},
    "then": [
      {
        "emit": "free_throw.sequence.start",
        "data": {
          "shooter_id": "{event.foulee_id}",
          "attempts": "{3 if event.shot_type == 'three_point' else 2}"
        }
      }
    ]
  },
  {
    "on": "free_throw.sequence.start",
    "then": [
      {
        "emit_n": {
          "event": "free_throw.attempt",
          "count": "{event.attempts}",
          "data": {"shooter_id": "{event.shooter_id}"}
        }
      }
    ]
  },
  {
    "on": "free_throw.resolved",
    "when": {"field": "event.made", "eq": true},
    "then": [{"score": {"team": "{offense_team_id}", "points": 1}}]
  }
]
```

A player proposes "free throws worth 2 points": new rule on `free_throw.resolved`, higher priority, `points: 2`. No Python.

A player proposes "4 free throws in Elam": new rule overriding `free_throw.sequence.start` with `{"field": "game.elam_active", "eq": true}` condition, `attempts: 4`. No Python.

**This is the design target.**

---

## 3. The Rule Schema

Every rule is `{on, when, then, duration}`:

```python
@dataclass
class Rule:
    id: str                    # Unique — proposal ID + index, or "default.{name}"
    on: str                    # Event pattern: "shot.resolved", "shot.*", "*"
    when: dict | None          # Condition tree, or null (always fires)
    then: list[dict]           # Mutations applied when rule fires
    duration: str              # "permanent" | "n_rounds" | "one_game" | "until_repealed"
    duration_rounds: int | None
    source: str                # "default" | proposal_id
    priority: int              # Higher fires first. Default=10 for player rules, 0 for defaults.
    description: str
```

**Condition vocabulary (`when`):**

```json
// Equality
{"field": "event.shot_type", "eq": "at_rim"}
{"field": "game.elam_active", "eq": true}
{"field": "game.last_result", "eq": "made"}

// Comparisons
{"field": "game.quarter", "gte": 3}
{"field": "game.consecutive_makes", "gte": 3}
{"field": "player.current_stamina", "lt": 0.4}

// Computed aliases (always in context)
{"field": "game.trailing", "eq": true}
{"field": "game.leading", "eq": true}
{"field": "game.score_diff", "gte": -5}
{"field": "game.shot_zone", "eq": "at_rim"}   // alias for last_action

// Probabilistic
{"random": 0.15}

// Meta store
{"field": "meta.team.swagger", "gte": 5}

// Logical
{"all": [...conditions...]}
{"any": [...conditions...]}
{"not": {...condition...}}
```

**Mutation vocabulary (`then`):**

```json
// Mutate event data (before it propagates to other rules)
{"mutate_event": {"points": 0}}
{"mutate_event": {"probability": "{event.probability + 0.05}"}}

// Mutate game/player/team state
{"mutate_state": {"target": "game", "field": "shot_clock_seconds", "op": "set", "value": 4}}
{"mutate_state": {"target": "player:{event.ball_handler_id}", "field": "shot_bonus", "op": "add", "value": 0.05}}

// Score points
{"score": {"team": "{offense_team_id}", "points": 2}}

// Emit sub-events
{"emit": "free_throw.sequence.start", "data": {"attempts": 2}}
{"emit_n": {"event": "free_throw.attempt", "count": "{event.attempts}", "data": {...}}}

// Flow control
{"block_default": true}   // prevent lower-priority default rules from firing
{"block_event": true}     // cancel the event entirely

// Narrative injection
{"narrative": "The ball burns! {player.name} staggers!"}
```

**Field path syntax:** `namespace.field_path`
- `event.*` — current event data
- `game.*` — current `GameContext` fields
- `player.*` or `player:{id}.*` — player attributes and state
- `team:{id}.*` — team fields
- `meta.{entity_type}.{field}` — persistent meta store values

---

## 4. The Unified Evaluation Context

For each event, the evaluation context merges event data with game state into one flat namespace.

```python
@dataclass
class EventContext:
    # Current event
    event: dict[str, object]

    # Game state (all fields automatically available)
    game_id: str
    quarter: int
    home_score: int
    away_score: int
    home_has_ball: bool
    elam_active: bool
    elam_target: int | None
    game_over: bool
    last_action: str
    last_result: str
    consecutive_makes: int
    consecutive_misses: int
    possession_number: int
    total_possessions: int

    # Computed aliases
    trailing: bool          # offense < defense
    leading: bool           # offense > defense
    score_diff: int         # offense - defense
    shot_zone: str          # alias for last_action

    # Player lookups
    players: dict[str, PlayerState]
    ball_handler_id: str

    # Meta store access
    meta: MetaStore

    # RNG (for probabilistic conditions)
    rng: random.Random
```

**The resolver:**

```python
def resolve_field(ctx: EventContext, path: str) -> object:
    parts = path.split(".", 1)
    namespace, rest = parts[0], parts[1] if len(parts) > 1 else ""

    if namespace == "event":
        return ctx.event.get(rest)
    if namespace == "game":
        return getattr(ctx, rest, None)
    if namespace == "player":
        return getattr(ctx.players.get(ctx.ball_handler_id), rest, None)
    if namespace.startswith("player:"):
        player_id = namespace[7:]
        return getattr(ctx.players.get(player_id), rest, None)
    if namespace == "meta":
        # "meta.team.swagger" → meta_store.get("team", team_id, "swagger")
        return resolve_meta(ctx.meta, rest, ctx)
    return None
```

**Adding a new `GameContext` field automatically makes it available to all conditions.** No evaluator change needed.

---

## 5. The Event Loop (new simulation.py)

```python
class EventPipeline:
    """Pure event loop. Zero basketball knowledge."""

    def __init__(self, rules: list[Rule], rng: random.Random):
        self._handlers: dict[str, list[Rule]] = _build_rule_index(rules)
        self._rng = rng

    def fire(self, event_name: str, data: dict, ctx: GameContext) -> EventResult:
        eval_ctx = _build_eval_context(event_name, data, ctx, self._rng)
        matching = self._match_rules(event_name, eval_ctx)

        mutations: list[dict] = []
        for rule in sorted(matching, key=lambda r: -r.priority):
            if rule.when is None or evaluate_condition(rule.when, eval_ctx):
                for mutation in rule.then:
                    if mutation.get("block_event"):
                        return EventResult(blocked=True)
                    mutations.append(mutation)
                if any(m.get("block_default") for m in rule.then):
                    break

        mutated_data = apply_event_mutations(data, mutations)
        apply_state_mutations(ctx, mutations)

        sub_results = []
        for emission in collect_emissions(mutations):
            sub_results.append(self.fire(emission["event"], emission["data"], ctx))

        return EventResult(
            event_name=event_name,
            mutated_data=mutated_data,
            narrative=collect_narratives(mutations),
            sub_events=sub_results,
        )

    def _match_rules(self, event_name: str, ctx: EventContext) -> list[Rule]:
        candidates = list(self._handlers.get(event_name, []))
        parts = event_name.split(".")
        for depth in range(1, len(parts)):
            candidates.extend(self._handlers.get(".".join(parts[:depth]) + ".*", []))
        candidates.extend(self._handlers.get("*", []))
        return [r for r in candidates if r.when is None or evaluate_condition(r.when, ctx)]


def simulate_game(home: Team, away: Team, rules: list[Rule], seed: int) -> GameResult:
    """Pure function. No basketball knowledge in the engine."""
    rng = random.Random(seed)
    pipeline = EventPipeline(rules, rng)
    ctx = GameContext(home=home, away=away)
    log: list[PossessionLog] = []

    pipeline.fire("game.start", {"home_team_id": home.id, "away_team_id": away.id}, ctx)

    while not ctx.game_over:
        poss_result = run_possession(pipeline, ctx, rng)
        log.append(poss_result.log)
        ctx.total_possessions += 1
        ctx.alternate_possession()
        if ctx.total_possessions >= MAX_POSSESSIONS:
            ctx.game_over = True

    return build_game_result(ctx, log)


def run_possession(pipeline: EventPipeline, ctx: GameContext, rng: random.Random) -> PossessionResult:
    """One possession entirely through events. No basketball hardcoding."""

    pipeline.fire("possession.start", {"offense_team_id": ctx.offense_team_id}, ctx)

    # Turnover check
    pipeline.fire("possession.turnover_check", {
        "ball_handler_id": ctx.ball_handler_id,
        "defense_scheme": ctx.defense_scheme,
    }, ctx)
    if ctx.turnover_occurred:
        pipeline.fire("possession.turnover", {}, ctx)
        return PossessionResult(turnover=True)

    # Shot selection
    pipeline.fire("shot.selected", {
        "ball_handler_id": ctx.ball_handler_id,
        "weights": ctx.shot_weights,
    }, ctx)
    shot_type = ctx.selected_shot_type

    # Shot attempt
    pipeline.fire("shot.attempted", {
        "shot_type": shot_type,
        "ball_handler_id": ctx.ball_handler_id,
        "base_probability": ctx.compute_base_probability(shot_type),
    }, ctx)

    made = rng.random() < ctx.current_shot_probability

    # Shot resolved — rules score points, apply effects, etc.
    pipeline.fire("shot.resolved", {
        "shot_type": shot_type,
        "made": made,
        "points": ctx.current_shot_points if made else 0,
        "ball_handler_id": ctx.ball_handler_id,
    }, ctx)

    # Foul check — rules handle free throw sequences
    pipeline.fire("foul.check", {
        "defender_id": ctx.primary_defender_id,
        "shot_type": shot_type,
        "shot_made": made,
    }, ctx)

    if not made and not ctx.foul_occurred:
        pipeline.fire("rebound.contested", {
            "offense_players": ctx.offense_ids,
            "defense_players": ctx.defense_ids,
        }, ctx)

    pipeline.fire("stamina.drain", {"players": ctx.all_active_player_ids}, ctx)
    pipeline.fire("possession.end", {"made": made, "shot_type": shot_type}, ctx)

    return PossessionResult(made=made, points=ctx.points_scored_this_possession)
```

---

## 6. The Default Ruleset (basketball as data)

`config/default_rules.json` — the full set of rules that makes the game play like basketball. Every rule uses the schema from Section 3.

### Shot Scoring

```json
[
  {"id": "default.score_at_rim", "on": "shot.resolved",
   "when": {"all": [{"field": "event.shot_type", "eq": "at_rim"}, {"field": "event.made", "eq": true}]},
   "then": [{"score": {"team": "{offense_team_id}", "points": 2}}]},

  {"id": "default.score_mid_range", "on": "shot.resolved",
   "when": {"all": [{"field": "event.shot_type", "eq": "mid_range"}, {"field": "event.made", "eq": true}]},
   "then": [{"score": {"team": "{offense_team_id}", "points": 2}}]},

  {"id": "default.score_three", "on": "shot.resolved",
   "when": {"all": [{"field": "event.shot_type", "eq": "three_point"}, {"field": "event.made", "eq": true}]},
   "then": [{"score": {"team": "{offense_team_id}", "points": 3}}]}
]
```

### Free Throw Sequence (complete)

```json
[
  {"id": "default.foul_triggers_fts", "on": "foul.committed",
   "when": {"field": "event.shot_made", "eq": false},
   "then": [{"emit": "free_throw.sequence.start",
             "data": {"shooter_id": "{event.foulee_id}",
                      "attempts": "{3 if event.shot_type == 'three_point' else 2}"}}]},

  {"id": "default.ft_sequence", "on": "free_throw.sequence.start",
   "then": [{"emit_n": {"event": "free_throw.attempt",
                        "count": "{event.attempts}",
                        "data": {"shooter_id": "{event.shooter_id}"}}}]},

  {"id": "default.ft_probability", "on": "free_throw.attempt",
   "then": [{"mutate_event": {"probability": "{logistic(player.attributes.scoring, 25, 0.06)}"}}]},

  {"id": "default.ft_score", "on": "free_throw.resolved",
   "when": {"field": "event.made", "eq": true},
   "then": [{"score": {"team": "{offense_team_id}", "points": 1}}]}
]
```

### Elam Ending

```json
[
  {"id": "default.elam_trigger", "on": "game.quarter.end",
   "when": {"field": "event.quarter", "eq": 3},
   "then": [
     {"mutate_state": {"target": "game", "field": "elam_target",
                       "op": "set", "value": "{max(game.home_score, game.away_score) + 15}"}},
     {"mutate_state": {"target": "game", "field": "elam_active", "op": "set", "value": true}},
     {"emit": "game.elam.trigger", "data": {"target": "{game.elam_target}"}}]},

  {"id": "default.elam_win_check", "on": "score.points",
   "when": {"field": "game.elam_active", "eq": true},
   "then": [{"conditional_emit": {
     "condition": {"any": [
       {"field": "game.home_score", "gte": "game.elam_target"},
       {"field": "game.away_score", "gte": "game.elam_target"}]},
     "emit": "game.over", "data": {"reason": "elam_target_reached"}}}]}
]
```

### Stamina Drain

```json
{"id": "default.stamina_drain", "on": "stamina.drain",
 "then": [{"mutate_state": {
   "target": "each_player:{event.players}",
   "field": "current_stamina", "op": "subtract",
   "value": "{0.007 + scheme_drain(game.defense_scheme) - (player.attributes.stamina / 3000.0)}"}}]}
```

### Substitution

```json
[
  {"id": "default.foul_out_sub", "on": "foul.ejection",
   "then": [{"emit": "sub.triggered",
             "data": {"outgoing_id": "{event.player_id}",
                      "incoming_id": "{best_bench_player(event.team_id)}", "cause": "foul_out"}}]},

  {"id": "default.fatigue_sub", "on": "game.quarter.end",
   "then": [{"for_each_team": {
     "condition": {"field": "weakest_active.current_stamina", "lt": 0.35},
     "emit": "sub.triggered",
     "data": {"outgoing_id": "{weakest_active_player(team_id)}",
              "incoming_id": "{best_bench_player(team_id)}", "cause": "fatigue"}}}]}
]
```

---

## 7. The Interpreter Schema

The AI no longer generates `effect_type`, `hook_callback`, `conditional_sequence` — implementation internals. It generates rules in game terms.

### New Interpreter JSON Output

```json
{
  "rules": [
    {
      "on": "<event name or wildcard>",
      "when": "<condition tree or null>",
      "then": "<list of mutations>",
      "duration": "permanent|n_rounds|one_game|until_repealed",
      "duration_rounds": null,
      "description": "<human-readable>",
      "priority": 10
    }
  ],
  "impact_analysis": "<1-3 sentences on gameplay impact>",
  "confidence": 0.9,
  "clarification_needed": false,
  "injection_flagged": false
}
```

### Example Interpretations of the 5 Real Proposals

**#8 "la pelota es lava" (Adriana) — stamina:**
```json
{"on": "stamina.drain", "when": null,
 "then": [{"mutate_event": {"amount": "{event.amount * 214}"}},
          {"narrative": "The ball burns!"}],
 "description": "La pelota es lava: stamina drain 214x normal"}
```

**#9 "baskets from inside the key score 0" (Rob Drimmie):**
```json
{"on": "shot.resolved",
 "when": {"all": [{"field": "event.shot_type", "eq": "at_rim"}, {"field": "event.made", "eq": true}]},
 "then": [{"mutate_event": {"points": 0}},
          {"narrative": "Inside the key means nothing."}],
 "description": "At-rim baskets score 0 points"}
```
No gate gap. `event.shot_type` is a field on the `shot.resolved` event. Condition is exact equality. Works.

**#10 "hot hand" (.djacobs):**
```json
[
  {"on": "shot.resolved", "when": {"field": "event.made", "eq": true},
   "then": [{"mutate_state": {"target": "player:{event.ball_handler_id}",
                              "field": "shot_bonus", "op": "add", "value": 0.05}}],
   "description": "Hot hand accumulator: +5% per make"},
  {"on": "shot.attempted", "when": {"field": "meta.player.shot_bonus", "gt": 0},
   "then": [{"mutate_event": {"base_probability": "{event.base_probability + meta.player.shot_bonus}"}}],
   "description": "Apply hot hand bonus to shot probability"}
]
```

**#14/#15 "no hold > 4/3 sec" (JudgeJedd):**
```json
{"on": "game.start", "when": null,
 "then": [{"mutate_state": {"target": "game", "field": "shot_clock_seconds", "op": "set", "value": 4}}],
 "description": "Shot clock reduced to 4 seconds"}
```

---

## 8. Stat Preservation Strategy

### Preserved As-Is (no migration)

- All `HooperBoxScore` records: points, FGM, FGA, 3PM, 3PA, FTM, FTA, rebounds, assists, steals, turnovers, fouls, minutes, plus-minus
- All `GameResult` records: home/away score, winner, seed, total_possessions, elam_activated
- All governance data: proposals, votes, amendments, token balances, governance events
- All identity data: team/hooper IDs, names, archetypes, attributes
- All AI report content
- All Discord guild/governor enrollment

**These are output data, not engine internals. They survive unchanged.**

### Needs Migration

**Active `RegisteredEffect` records:** Expire at World 1 season boundary. Non-default `RuleSet` parameter values (e.g., `shot_clock_seconds=4`) are converted to World 2 rules with `source: "world1_migration"` and added to the default ruleset for Season 1 of World 2.

**`PossessionLog.action` field:** Old values (`"at_rim"`, `"mid_range"`, `"turnover"`) map directly to World 2 event names. Old logs remain readable.

### The History Essay

At season boundary, Pinwheel generates a World 1 Season Archive document:

> *World 1 ran for N seasons. These rules, enacted by player governance, carried into World 2:*
> - Proposal #8 (Adriana): "La pelota es lava" — stamina drain 214x normal [carried as `rule-p8`]
> - Proposal #9 (Rob Drimmie): "Inside the key = 0 points" — at-rim baskets score 0 [carried as `rule-p9`]
> - ...

The stat pages show career stats across World 1 and World 2 without distinction. The box score format is identical.

---

## 9. Transition Path

### Phase 1: Parallel Infrastructure (no breaking changes)

1. Define `Rule` dataclass and `EventContext` alongside existing code
2. Implement `evaluate_condition(condition, ctx)` — generic field-path resolver, pure Python
3. Implement `EventPipeline.fire()` with mutation application and sub-event emission
4. Write `config/default_rules.json` — the complete basketball default ruleset
5. Run `test_simulation.py` against new engine with default ruleset — all tests must pass

### Phase 2: New Interpreter (no breaking changes)

6. Write V3 interpreter system prompt using event vocabulary
7. Run the 5 Session 114 proposals through V3 interpreter — verify correct World 2 rules
8. Write `eval_rule(rule, test_cases)` validator — add 20 golden cases in World 2 format
9. Deploy V3 interpreter in shadow mode alongside V2 — log both outputs, compare

### Phase 3: Flag-Day Cutover (season boundary)

10. Convert non-default `RuleSet` values to World 2 rules, write to database
11. Replace `simulate_game()` with `EventPipeline`-based version
12. Delete `possession.py`, `scoring.py`, `defense.py`, `moves.py`, legacy `HookPoint`/`GameEffect` system
13. Replace `interpret_proposal_v2` with `interpret_proposal_v3`
14. Update `EffectRegistry` to store `Rule` objects instead of `RegisteredEffect`
15. Full test suite, demo pipeline, Rodney screenshots

### What Gets Deleted

- `core/possession.py` — basketball logic moves to `config/default_rules.json`
- `core/scoring.py` — scoring is default rules
- `core/defense.py` — defensive scheme selection becomes rules
- `core/moves.py` — moves become rules on `shot.attempted`
- `hooks.py` legacy system: `HookPoint` enum, `GameEffect` protocol, `fire_hooks()`
- `HookResult` dataclass (replaced by mutation dicts)
- `PossessionContext` dataclass (replaced by event data)
- `INTERPRETER_SYSTEM_PROMPT` v1 and v2
- The entire `_apply_action_code()` method
- The entire `RegisteredEffect` class (replaced by `Rule`)

---

## 10. What Remains Special-Cased

Being honest about irreducible code:

**The event loop itself.** `EventPipeline.fire()` is Python. The engine's execution model is code.

**RNG.** `random.Random(seed)` is code. Rules reference `{"random": 0.15}` symbolically.

**Mathematical functions.** `logistic()`, `weighted_choice()` — referenced symbolically in rules, implemented in Python. Adding a new function (e.g., a custom shot curve) requires a Python change. Adding a new *rule that uses* an existing function requires zero Python.

**New mutation types.** `for_each_team`, `emit_n`, `conditional_emit` are generic mutation types — not basketball-specific — but they're code. Adding a genuinely new kind of mutation (e.g., "teleport player to other team") requires Python. Using that mutation in a new rule requires zero Python. The set of mutation types grows much more slowly than the set of player-authored rules.

**Expression evaluation.** `"{event.amount * 214}"` is evaluated by a safe expression evaluator (restricted grammar: arithmetic, field references, built-in functions). The evaluator is Python.

**Database persistence, network I/O, security sandbox.** These are always code.

**What this means in practice:** In World 1, every new effect type required a developer. In World 2, only truly new *effect primitive types* require a developer. Most basketball concepts — including ones we haven't imagined — can be expressed with the mutation vocabulary above. A player can propose anything, the AI generates a rule in the schema, and it works.

---

*Written 2026-02-19. Implements the architecture discussed in Session 115.*
*Current code: `hooks.py` generic evaluator (Session 115 immediate fix).*
*Next: Phase 1 — `EventPipeline` and `config/default_rules.json`.*
