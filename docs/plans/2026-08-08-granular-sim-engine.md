# Granular Simulation & Event Engine — Design + Migration Plan

Approved 2026-08-08. Goal: a much more granular sim — dribbling, passing,
turnover subtypes, off-ball motion, picks, box-outs — because **every event
type is governable surface**. Build a Basketball-GM-class event-chain engine
one level deeper than BBGM, on role+zone state (no coordinates), expressed
entirely as ActionDefinitions so the whole event graph is governable through
the existing GameDefinitionPatch path.

Baseline measured: 4.19 ms/game, 127 possessions/game (~860k games/hour) —
~500x headroom over the "hundreds of games/hour" requirement.

## Invariants (hold through every phase)

- Seeded determinism within an engine version; any planned RNG-stream change
  lands as a single seed-migration commit per phase.
- `PossessionResult` / `PossessionLog` summary shape unchanged (consumers
  untouched until Phase 4); `PossessionLog.events` is additive.
- `PossessionContext` / `HookResult` fields only added, never removed.
- `GameDefinitionPatch.apply` ignores unknown fields (verified) — old stored
  patches must keep validating.
- DB changes are additive columns only (auto_migrate_schema handles them).
- `ActionRegistry.shot_actions()` category filter is the load-bearing wall:
  new categories must NEVER default into shot selection.
- Full suite green + ruff clean before each phase's commit.

## Event schema

New model in `models/game.py`:

```python
class GameEvent(BaseModel):
    seq: int
    event_type: str          # namespaced: "shot.at_rim", "pass.swing", ...
    actor_id: str = ""
    target_id: str = ""
    team_id: str = ""
    outcome: str = ""        # "made"|"missed"|"stolen"|"success"|...
    detail: str = ""         # subtype: "dunk", "stepback", ...
    points: int = 0
    clock: str = ""
    zone: str = ""           # "paint"|"mid"|"perimeter"|"backcourt"
    tags: list[str] = []
```

`PossessionLog` gains `events: list[GameEvent] = []` (stored as JSON on the
possession row — NOT one DB row per event).

## Event taxonomy

**Tier 1 (Phase 1-2, subtype enrichment on the macro path):**
shot.at_rim subtypes (layup/dunk/floater/putback/tip), shot.mid_range
(pullup/stepback/fadeaway/catch_and_shoot), shot.three (spot_up/pullup/
heave/corner), turnover.bad_pass vs turnover.lost_ball (passing vs dribbling
attrs decide; steal attribution follows: bad_pass→interceptor,
lost_ball→strip), turnover.travel + turnover.double_dribble (base rate scaled
by new `violation_strictness` RuleSet knob, ge=0.0 le=5.0 default 1.0; iq
reduces), turnover.shot_clock (existing check relabeled), foul.shooting vs
foul.personal vs foul.loose_ball (phase of possession decides), block (on
missed at_rim/mid: defender defense+speed roll converts miss→blocked — fixes
the phantom `blocks` box-score stat that exists but is never incremented),
assist/potential_assist (BBGM pattern: pick passer BEFORE the shot,
passing-weighted, excluding shooter; credit on make; small make-prob bonus
when passer exists; putbacks unassisted), rebound contested/uncontested +
rebound.tip, and_one (foul roll on made shot + 1 FT).

**Tier 2 (Phase 4, micro-event chain):**
pass.swing/entry/skip/kickout/extra (moves ball-handler token; "every fourth
pass is worth a bonus" becomes governable), dribble.drive (speed vs defender,
perimeter→paint, sets up at_rim/kickout branch), dribble.iso (ego-gated),
dribble.crossover (success → open-shot tag), screen.on_ball with
roll/pop/slip branches + screen_assist credit, screen.off_ball →
cut.curl/cut.backdoor/spot_up/relocate, defense.contest/closeout on every
shot (contested/open tag), defense.switch + defense.help_rotation (changes
effective defender), defense.steal_attempt/deflection (gamble: steal /
loose-ball / blow-by), rebound.box_out (pre-rebound pairing roll shifts
rebound weights; hustle stat), hustle.loose_ball scramble, clock.14s_reset
on ORB.

**Tier 3 (Phase 5):** violation.backcourt/three_second/lane/kicked_ball,
violation.goaltending, admin.jump_ball/check_ball/clear_arc (FIBA-3x3 mode),
admin.timeout, foul.clear_path/away_from_play/take, play_type.transition
(possession opens in transition after live-ball TO/DRB), injury (pace-scaled),
admin.challenge.

## State model — role+zone hybrid, NO coordinates

```python
@dataclass
class PossessionState:            # ephemeral, per-possession (state.py)
    ball_handler: HooperState
    zone: str = "perimeter"       # paint|mid|perimeter|backcourt
    pass_count: int = 0
    dribble_count: int = 0
    shot_clock_remaining: float = 15.0
    potential_assister: HooperState | None = None
    screens_set: int = 0
    last_event: str = ""
    open_shot: bool = False
    second_chance: bool = False
    roles: dict[str, str]         # hooper_id -> handler|screener|spacer
    def_roles: dict[str, str]     # hooper_id -> on_ball|helper|weak_side
    events: list[GameEvent]
```

`HooperState` gains counting stats only: blocks_recorded, screen_assists,
deflections, box_outs, loose_balls, charges_drawn, potential_assists,
passes_made, drives, contested_shots — all flow to `HooperBoxScore` and DB
additively. `GameState` gains scalars (auto-exposed to the effect condition
evaluator by reflection): pass_count_last_possession, transition_possession,
bonus_active_home/away.

`ActionDefinition` gains (all optional/defaulted): `category` widens to
shot|pass|dribble|screen|cut|defense|rebound|violation|admin|special;
`transitions: dict[str, float]` (Markov next-event weights);
`success_transitions`/`failure_transitions` overrides;
`time_cost_seconds: float`; `zone_requirement: str`; `emits_tags: list[str]`.

## Simulation loop (Phase 3)

Outer structure untouched: `_run_quarter`/`_run_elam`/`_run_sudden_death`
still call `resolve_turn` once per possession. `resolve_turn` dispatches on
`GameDefinition.possession_engine`: `"macro"` → existing
`resolve_possession`; `"micro"` → new `core/possession_micro.py`.

Micro loop: setup roles+matchups (reuse defense.py) → loop (hard cap 16
events, shot clock authoritative): pick next event from registry actions
whose category/zone fits, weighted by transitions × weight_attributes(actor)
× strategy biases × PossessionContext.action_biases → fire sim.event.pre
(only if a registered effect subscribes — hook index makes no-effect games
~zero-cost) → resolve by resolution_type (attribute_check logistic via
compute_shot_probability_v2 / contested_check / automatic) → emit GameEvent,
decrement clock by time_cost ± jitter → terminal events exit; live-ball
events update PossessionState → sim.event.post. Rebound branch on miss:
box_out → attempt_rebound (reuse) → ORB sets second_chance + 14s reset,
chain continues (putback/tip nodes). Returns the same PossessionResult; one
summary PossessionLog row + events list.

New hooks (string names, mirroring the never-fired legacy enum):
sim.event.pre/post, sim.shot.pre/post, sim.pass.post, sim.screen.post,
sim.rebound.pre/post, sim.foul.post, sim.turnover.post. Keep shot-node
selection reading the same action_biases keys; introduce `transition_biases`
separately so governance biasing "three_point" keeps meaning shot selection.

Moves plug in per-event: triggers "drive_action", "opponent_iso",
"half_court_setup" match real event names via get_triggered_moves.

## Phases

- **Phase 1 — Event substrate + Tier-1 subtypes.** ✅ Shipped Session 138. GameEvent +
  PossessionLog.events; emit events for what already happens; subtype
  splits; block conversion; assist-before-shot; behind
  `GameDefinition.event_detail: bool = True` with legacy override for
  exact-score seed tests. Files: models/game.py, core/possession.py,
  core/state.py, core/narrate.py (subtype templates), models/
  game_definition.py, db/models.py, tests.
- **Phase 2 — Attribution & hustle.** ✅ Shipped Session 138. Steal attribution by subtype, and-one
  flow, contested rebounds, box-out pre-roll, screen-assist bookkeeping
  stub, phantom blocks fixed end-to-end; new category hooks fired from the
  macro path at existing decision points; effect-registry hook index.
  Files: possession.py, simulation.py, hooks.py, effects.py, models/game.py.
- **Phase 3 — Micro-engine behind flag (default "macro" — zero behavior
  change).** ✅ Shipped Session 139 — calibration within ±20% of macro on
  all bands; 10.97 ms/game sim-only. possession_micro.py, resolve_turn dispatch, ActionDefinition
  extensions, basketball_micro_actions(), distribution-calibration test
  (fixed seeds, 200 games: PPG/FG% by type/TO rate/ORB rate/foul rate within
  tolerance of macro), seed-snapshot test pinning one full game's event
  stream per engine version.
- **Phase 4 — Flip default to "micro" + Tier-2 events.** Passes/drives/
  screens/cuts/defense/box-outs live; moves rewired to real triggers;
  narration per category with graceful fallback; presenter renders summary
  line + expandable chain; commentary consumes summary rows + drama-selected
  chains ONLY (never raw event stream); interpreter vocabulary updated; one
  "engine v2 seeds" commit updates seed-dependent tests.
- **Phase 5 — Tier-3 structural governance.** Violations, admin events,
  transition play-type, FIBA-3x3 structure options (check_ball, clear_arc,
  target-score), GameDefinitionPatch.modify_transitions, and
  game_def_validation reachability check (every start node must reach a
  terminal node — governance can otherwise brick possessions). Update
  docs/SIMULATION.md + interpreter grounding + golden evals.

## Risks & mitigations

1. Effect fan-out, not CPU, is the perf risk: hook-index skip; codegen
   effects register to sim.possession.* and category hooks by default, not
   raw sim.event.pre; existing 250ms/game codegen budget bounds hostile code.
2. PBP bloat: events as JSON on the possession row; SSE/presenter show
   summary + expandable; commentary reads summaries + tagged highlights.
3. Determinism: engine-version flag + planned seed-migration commits;
   distribution-regression suite catches unplanned RNG draws.
4. Balance: chain probabilities compound — anchor on BBGM bases/slopes,
   calibrate against macro distributions; keep usage-efficiency tradeoff.
5. Interpreter mis-grounding: add golden-eval cases per tier as vocabulary
   lands.

## Reference (external research, for implementers)

NBA Stats API EVENTMSGTYPE taxonomy + pbpstats enhanced model (turnover/foul
subtype vocabularies, derived flags is_assisted/is_blocked/is_and1/
is_second_chance, shot zones); NBA hustle stats (screen assists, deflections,
box outs, contested shots); Synergy play types (iso, PnR handler/roll,
spot-up, cut, off-screen, transition, putback); Basketball GM GameSim
(possession-based chain, truncGauss possession length, assist-before-shot,
pickPlayer power-weighted selection, composite ratings, numPlayersOnCourt
variable, Elam implemented, league god-factors as rule knobs); FIBA 3x3 rules
(12s clock, clear-arc, check-ball, first-to-21); Elam Ending (TBT +8).
