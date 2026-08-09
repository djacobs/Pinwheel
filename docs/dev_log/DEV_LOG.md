# Pinwheel Dev Log — 2026-08-08

Previous logs: [DEV_LOG_2026-02-10.md](DEV_LOG_2026-02-10.md) (Sessions 1-5), [DEV_LOG_2026-02-11.md](DEV_LOG_2026-02-11.md) (Sessions 6-16), [DEV_LOG_2026-02-12.md](DEV_LOG_2026-02-12.md) (Sessions 17-33), [DEV_LOG_2026-02-13.md](DEV_LOG_2026-02-13.md) (Sessions 34-47), [DEV_LOG_2026-02-14.md](DEV_LOG_2026-02-14.md) (Sessions 48-70), [DEV_LOG_2026-02-15.md](DEV_LOG_2026-02-15.md) (Sessions 71-89), [DEV_LOG_2026-02-16.md](DEV_LOG_2026-02-16.md) (Sessions 90-106), [DEV_LOG_2026-02-17.md](DEV_LOG_2026-02-17.md) (Sessions 107-111), [DEV_LOG_2026-02-18.md](DEV_LOG_2026-02-18.md) (Session 112), [DEV_LOG_2026-02-19.md](DEV_LOG_2026-02-19.md) (Sessions 113-115), [DEV_LOG_2026-02-20.md](DEV_LOG_2026-02-20.md) (Sessions 116-125), [DEV_LOG_2026-02-24.md](DEV_LOG_2026-02-24.md) (Sessions 126-128), [DEV_LOG_2026-02-25.md](DEV_LOG_2026-02-25.md) (Sessions 129-131), [DEV_LOG_2026-06-12.md](DEV_LOG_2026-06-12.md) (Sessions 132-136)

## Where We Are

- **2977 tests**, zero lint errors (Session 141)
- **Launch night (Sessions 137-141):** the full implementation & launch
  plan executed — enforced admin approval gate, amendment enactment fix,
  vote dedupe, real move mechanics, web effects truth, effect carry-over,
  docs reconciled — AND the granular sim engine built end-to-end
  (Phases 1-5): GameEvent substrate, Tier-1 subtypes, attribution &
  hustle stats, the micro event-chain possession engine (now the DEFAULT),
  Tier-2 screens/cuts/passes/defense/transition, Tier-3 violations,
  FIBA-3x3 options, injuries, modify_transitions patches, reachability
  validation (`docs/plans/2026-08-08-granular-sim-engine.md`)
- **Live at:** https://pinwheel.fly.dev — prod v158 (engine build deployed)
- **Latest commit:** `1b61400` — docs: two-engine architecture in SIMULATION.md

## Today's Agenda

- [x] Deep audit: propose→vote→enact pipeline (2 Fable agents) + published launch plan artifact
- [x] Phase 0: backup + deploy held fixes (v156)
- [x] Phase 1: approval gate, amendments-reach-tally, vote dedupe, playoff_teams, composite removal
- [x] Phase 2: real move mechanics, web effects panel, admin notifications, effect carry-over
- [x] Phase 3-4: docs reconciliation + test hardening (2814 tests) — deployed v157
- [x] Granular sim engine Phases 1-5 (Sessions 138-141) — deployed v158
- [ ] Run the four live drills (docs/LAUNCH_DRILLS.md) — needs the owner on Discord as admin
- [ ] Launch week (Phase 6): onboard cohort, seed first proposal, normal pace

## Session 137 — Launch Night: Approval Gate + Trust Fixes + Capability (2026-08-08)

**What was asked:** Execute the full implementation & launch plan (published as
an artifact after a two-agent audit): make propose→vote→enact trustworthy with
an enforced human vetting step, close capability gaps, reconcile docs, and prep
launch drills. Owner adopted all recommended decisions (gate on all proposals,
effects carry across seasons, delete composite, real Opus escalation, web
intake deferred).

**What was built (main session + 3 parallel worktree agents):**

- **Prod deploy (v156):** sessions 134-136 fixes finally shipped after backup.
- **Amendments reach the tally** — tally reconstruction now merges the latest
  `proposal.amended` interpretation and supersedes stale v2 effects. Previously
  a passed amended proposal silently enacted the *original* interpretation.
- **Enforced approval gate** (`PINWHEEL_RULES_REQUIRE_APPROVAL`, on in prod):
  every passing proposal holds in `proposal.enactment_held`; admin gets a DM
  with Approve & Enact / Reject buttons (`HeldEnactmentReviewView`); approve
  enacts the tally-time snapshot via the shared `_enact_passed_proposal`
  helper; reject refunds the PROPOSE token. Replaces the unwinnable veto race.
- **Tally trust:** vote dedupe (one vote per governor, latest wins);
  `playoff_teams` wired into bracket generation + tiebreaker cutoff (was
  hardcoded 4); inert `composite` effect type removed; broad `except
  (ValueError, Exception)` catches narrowed at enactment.
- **Effects carry across seasons** with `carry_rules=true` (to_dict round-trip
  preserves codegen approval/disabled state; repealed effects stay dead).
- **Move grants are real** (agent): structured modifier fields
  (kind/magnitude/action-class), generic `apply_move_modifier`, free-text
  parser fallback, name→ID target resolution at enactment. `_opus_escalate`
  now actually calls claude-opus-4-6.
- **Web tells the truth** (agent): /rules + /governance render the live effect
  registry incl. codegen gate status; admin DM for
  `effect.implementation_requested` with retry-until-delivered; repeal
  threshold now tier-derived.
- **Docs reconciled** (agent): RUN_OF_PLAY (BOOST regen, command table, tier
  table matches `detect_tier_v2`, absentee-admin codegen, approval gate),
  GAME_LOOP `governance_rounds_interval` claim fixed, Feb-24 audit marked
  SUPERSEDED, OPS.md admin runbook added.
- **Launch drills:** `docs/LAUNCH_DRILLS.md` — four live prod drills (param,
  effect, structural, codegen council) with verification checklists and
  launch-week monitoring signals. **These need the owner on Discord as admin.**
- **Granular sim engine design** (research agent): full proposal — tiered event
  taxonomy (NBA/WNBA PBP vocabulary), role+zone state model, BBGM-style
  event-chain possession loop behind `GameDefinition.possession_engine`,
  5-phase migration, measured 4.2ms/game baseline (~500x headroom). Slotted
  for post-launch implementation.

**Issues resolved along the way:** two merge conflicts (interpreter prompt
renumbering vs move_grant rewrite; both-appended views.py tail truncating a
function close).

**Tests:** 2814 passed (was 2747), ruff clean.

## Session 138 — Granular Sim Engine Phases 1+2: Event Substrate + Attribution (2026-08-08)

**What was asked:** Implement Phases 1 and 2 of the approved granular sim
engine plan (`docs/plans/2026-08-08-granular-sim-engine.md`): the GameEvent
substrate + Tier-1 subtype enrichment on the macro path, and attribution &
hustle stats + category hooks + effect-registry hook index. No micro engine,
no resolve_turn dispatch changes (that's Phase 3).

**What was built:**

- **Event substrate:** `GameEvent` model (namespaced `event_type`, actor/
  target/team, outcome, subtype detail, zone, tags) + `PossessionLog.events`
  stored as JSON on the possession row. Old rows deserialize to `[]`.
- **Tier-1 subtypes on the macro path:** shot subtypes derived from the
  EXISTING shot roll rescaled to a make-independent uniform (zero new draws —
  dunk share scales with scoring+speed, putback/tip from second-chance
  context, catch_and_shoot/corner/spot_up when a potential assister exists);
  `turnover.bad_pass` vs `turnover.lost_ball` split by passing-vs-speed with
  correct steal attribution (interceptor vs on-ball strip) and deflection
  credit; travel/double_dribble dead-ball violations governed by the new
  `violation_strictness` RuleSet knob (ge=0 le=5 default 1.0, Tier-1 in
  detect_tier, ~1% of possessions at 1.0, IQ reduces); `turnover.shot_clock`
  relabel; foul.shooting/foul.loose_ball events.
- **Block conversion:** missed at_rim/mid_range can convert to a block by the
  primary defender (defense+speed roll) — fixes the phantom `blocks` stat
  end-to-end: HooperState → `_build_box_scores` → new `blocks` DB column.
- **Assist-before-shot (BBGM):** passing-weighted potential assister picked
  BEFORE the shot, +0.03 make-prob when present, credit on make, putbacks
  unassisted. Replaces the old uniform-random-teammate assist.
- **And-one flow:** foul on a made shot now awards 1 FT (previously nothing).
- **Phase 2 attribution/hustle:** box-out pre-roll (defense's best rebounder,
  success shifts rebound weights +4.0 to the defensive side, hustle stat),
  contested/uncontested rebounds + rebound.tip, screen-assist stub, and new
  counting stats through HooperState → HooperBoxScore → additive BoxScoreRow
  columns: rebounds, blocks, fouls, potential_assists, passes_made, box_outs,
  screen_assists, deflections, contested_shots, drives.
- **Category hooks:** `sim.shot.pre/post`, `sim.rebound.pre/post`,
  `sim.foul.post`, `sim.turnover.post` fired from the macro path's existing
  decision points, with a per-game effect-registry hook index — games with no
  effects on these hooks pay ~zero cost. `sim.shot.pre` results can shape the
  imminent shot's probability and value.
- **RNG discipline:** all enrichment draws gated behind
  `GameDefinition.event_detail` (default True). `event_detail=False`
  reproduces the pre-change stream bit-exactly — verified 200 seeded games
  against the HEAD engine, and pinned in-tests against 3 recorded game lines.
- **Seed migration:** exactly ONE test changed
  (`test_higher_point_value_changes_scores` — single-seed paired comparison
  migrated to an 8-seed average; commit `bde17c2`).
- **Narration:** subtype-aware templates (dunks, floaters, putbacks, tips,
  stepbacks, fadeaways, catch-and-shoot, corner threes, heaves, blocks,
  violations, bad-pass/lost-ball turnovers, and-one suffix) with graceful
  fallback for unknown subtypes and pre-enrichment rows; presenter + game
  page pass event context via `extract_event_context`. Still one line per
  possession.

**Performance:** 5.03 ms/game enriched vs 4.21 ms/game legacy path
(200-game loop; plan baseline 4.19, budget 15).

**Issues resolved:** phantom blocks stat (existed on HooperState/HooperBoxScore
but never incremented, and BoxScoreRow had no blocks/rebounds/fouls columns);
made-shot fouls awarding nothing (now and-one).

**Tests: 2860 passed** (was 2814; +37 event-engine, +9 narration), ruff clean.

## Session 139 — Granular Sim Engine Phase 3: Micro Possession Engine (2026-08-08)

**What was asked:** Implement Phase 3 of the granular sim engine plan
(`docs/plans/2026-08-08-granular-sim-engine.md`): the micro event-chain
possession engine behind a flag, with the default UNCHANGED ("macro") so
behavior is identical for every existing caller. Calibration + snapshot
tests, performance under 40 ms/game, no default flip, no narration/
presenter changes (Phase 4).

**What was built:**

- **ActionDefinition chain fields** (all optional/defaulted — every stored
  GameDefinitionPatch keeps validating): `transitions` (Markov next-event
  weights, with a `"shot"` pseudo-target), `success_transitions` /
  `failure_transitions` overrides, `time_cost_seconds`, `zone_requirement`,
  `emits_tags`; `category` vocabulary widened (shot|pass|dribble|screen|
  cut|defense|rebound|turnover|violation|admin|special).
- **The load-bearing wall hardened:** `ActionRegistry.shot_actions()` now
  filters `category == "shot"` strictly (was `!= "special"`) so chain
  nodes can never leak into shot selection; the game-def patch validator
  mirrors the same filter. A macro game with chain nodes in its registry
  is bit-identical to one without — pinned in tests.
- **`GameDefinition.possession_engine: Literal["macro","micro"] = "macro"`**
  with `resolve_turn` dispatching on it. Default path untouched — the
  full pre-existing suite passed with zero modifications.
- **`core/possession_micro.py`** — the event-chain loop: setup reuses
  defense.py scheme/matchups and select_ball_handler; then per chain step
  (hard cap 16, shot clock authoritative) pick the next node from
  transition weights × weight_attributes affinity × runtime scales
  (turnover ingredients via the shared `turnover_probability`, violation
  strictness × IQ) + `PossessionContext.transition_biases`; resolve by
  resolution_type (automatic / contested_check logistic vs defender /
  shots through the SAME `select_action` + `compute_shot_probability_v2`
  path, so `action_biases`, strategies, surfaces, Elam bias, and moves
  all keep working); emit a GameEvent per step; `sim.event.pre/post` +
  all Phase-2 category hooks fire through the hook index. ORB → second
  chance continues the chain (14s-style reset, putback/tip subtypes,
  macro-equivalent scramble time charged). Chain-length fatigue keeps
  stamina curves aligned. Returns the same PossessionResult shape: one
  summary PossessionLog row + the event chain.
- **Shared helper refactor** (macro RNG stream bit-identical, verified by
  the pinned legacy seed tests): `resolve_free_throws`, `box_out_pre_roll`,
  `maybe_block`, `maybe_loose_ball_foul`, `turnover_probability` extracted
  from possession.py and reused by both engines.
- **`basketball_micro_actions()` / `basketball_micro_chain_nodes()`** —
  the Phase-3 node vocabulary as data: initiate, pass_swing, drive
  (zone-gated to perimeter), iso (ego-weighted), second_chance,
  turnover_bad_pass, turnover_lost_ball, violation_deadball. Phase 4 adds
  screens/cuts/defense purely as data. A fallback node set engages when
  governance flips the engine flag without adding chain nodes.
- **HookResult/PossessionContext `transition_biases`** — separate
  namespace from `action_biases`, merged in `_fire_sim_effects`, so
  governance biasing "three_point" keeps meaning shot selection.

**Calibration (200 fixed-seed games/engine, micro vs macro):** PPG +11.6%
(118.8 vs 106.4), TO/game +13.6% (12.95 vs 11.39), fouls/game +14.1%
(13.24 vs 11.61), ORB rate -2.1% (.489 vs .499), FG% at_rim +4.3% / mid
-0.8% / three +0.7%, attempt shares within +12.2% — all inside the ±20%
regression bands.

**Performance:** micro 10.97 ms/game sim-only (macro 6.82 on the same
box; budget 40, plan estimate 15-40). Seed-42 event stream pinned by
sha256 + first 30 events.

**Tests: 2903 passed** (was 2860; +43 micro engine), ruff clean.

## Session 140 — Granular Sim Engine Phase 4: Tier-2 Events + Default Flip (2026-08-08)

**What was asked:** Implement Phase 4 of the granular sim engine plan
(`docs/plans/2026-08-08-granular-sim-engine.md`): Tier-2 events (passes,
crossovers, screens with real screen-assist credit, cuts, defensive
switch/help/steal-gamble, loose balls, per-shot contests), moves rewired
to real chain triggers, GameState governance scalars, narration +
presenter + game-page + commentary surfaces, interpreter vocabulary +
golden evals — and FLIP the default possession engine to "micro". One
seed-migration commit; macro stays fully functional.

**What was built:**

- **Tier-2 nodes as pure data** in `basketball_micro_chain_nodes()`:
  `pass_entry/skip/kickout/extra` (entry feeds the paint, kickouts exit
  it — zone transitions respected; skip/extra grant a small next-shot
  bonus), `crossover` (speed contested_check → open look, lost-ball risk
  on failure), `screen_on_ball` with roll/pop/slip branches (screener =
  best-strength teammate; slip is a live pass to the rim) and
  `screen_off_ball` → `cut_curl`/`cut_backdoor`/`spot_up`/`relocate`
  (cutter speed/iq gated; backdoor success = open at-rim look),
  `defense_switch` + `help_rotation` (both change the effective defender
  for shot resolution; help leaves the helper's man open → kickout
  synergy), `steal_attempt` (gamble: steal / deflection + loose-ball
  scramble / blow-by). The chain vocabulary now ships inside
  `basketball_game_definition().actions`, so governance can remove/
  reweight every node ("picks are illegal" = `remove_actions` on the two
  screen nodes — pinned end-to-end in tests).
- **Contest/closeout on every shot:** each shot resolves against an
  open/contested roll (chain-created open looks are never closed out);
  contested shots take a small malus and credit the effective defender's
  `contested_shots` (replacing the scheme-threshold heuristic in micro);
  open shots keep the open bonus plus any chain-earned bonus. Every shot
  event carries an `open`/`contested` tag.
- **Real screen-assist credit** replaces the Phase-2 stub: a made basket
  after a screen this possession credits the screener (stat == events,
  pinned). New `loose_balls` hustle stat flows HooperState →
  HooperBoxScore → additive `loose_balls` DB column.
- **Moves rewired to real triggers:** `get_triggered_moves` fires
  per-event — "drive_action" on dribble.drive/crossover, "opponent_iso"
  on dribble.iso (defender's Lockdown Stance), "half_court_setup" on
  initiate/pass events (No-Look Pass, Chess Move). Chain-triggered moves
  apply at the shot through the SAME `apply_move_modifier` path,
  deduped with shot-time triggers.
- **GameState governance scalars** (auto-exposed to the condition
  evaluator by reflection): `pass_count_last_possession` (updates live
  per pass — "every fourth pass is worth a bonus" is a sim.shot.pre
  condition) and `transition_possession` (live-ball turnover or DRB opens
  a transition: faster initiation, small at-rim bias, "transition" shot
  tag). New category hooks `sim.pass.post` / `sim.screen.post` fire
  through the hook index (macro-inert).
- **Default flip:** `GameDefinition.possession_engine` defaults to
  `"micro"`. The macro engine is untouched and reachable via
  `modify_structure: {"possession_engine": "macro"}` or explicit
  construction; `event_detail=False` still pins the legacy pre-enrichment
  macro stream bit-exactly (dispatch enforces it), so Phase 1's pinned
  legacy seed tests pass UNCHANGED.
- **Surfaces:** `narrate_event()` — one short line per chain event for
  every Tier-2 family with graceful fallback for unknown/governed types;
  presenter payloads carry the narrated chain (`events`, capped at 32)
  while the default rendering stays one line per possession; the game
  page renders an expandable `<details>` chain per possession (UX note
  150); commentary consumes summary rows + at most 3 drama-selected
  highlight chains (≤14 events each) — never the raw event stream.
- **Interpreter vocabulary:** v2 prompt now lists the in-possession hooks
  (sim.shot.pre/post, sim.pass.post, sim.screen.post, rebound/foul/
  turnover), the removable chain-node names with worked examples, and the
  new condition scalars. Mock interpreter handles the three Tier-2
  patterns; 3 interpreter golden cases (`TIER2_INTERPRETER_CASES` +
  `run_interpreter_golden_case` in `evals/golden.py`) pin the grounding.

**Calibration (200 fixed-seed games/engine, micro vs macro, ±20% bands):**
PPG +16.4% (123.9 vs 106.4), TO/game +18.1% (13.46 vs 11.40), fouls/game
+14.2% (13.25 vs 11.61), ORB rate -2.5% (.486 vs .499), FG% at_rim +12.4%
/ mid +5.5% / three +5.8%, attempt shares within ±6.3% — all inside the
bands (transition/steal-gamble weights recalibrated after Tier-2 pushed
turnovers to +24.9%). The calibration harness now pins the macro baseline
explicitly (the old code inherited the default and silently became
micro-vs-micro after the flip).

**Performance:** micro 10.9 ms/game sim-only under the flipped default
(macro 6.5 on the same box; budget 40).

**Seed migration ("test: engine v2 seeds", one commit):** re-pinned the
Phase-3 micro snapshot (seed 42: 80-44, 99 possessions, 687 events, new
sha256); migrated 12 seed/behavior-dependent tests across
test_possession_micro (macro-explicit hook test, fallback construction,
contest-aware drive-open scan), test_event_engine (made-putback assist
invariant, deflection accounting includes steal gambles, turnover-subtype
mix now emerges from failed events, block targets the nearest prior shot,
assist skew threshold), test_simulation + test_expanded_ruleset (ORB
assertions moved to rebound events — micro folds ORBs into the chain),
and test_action_registry (definition carries chain nodes).

**Tests: 2932 passed** (was 2903; +27 Tier-2/surface, +2 golden), ruff clean.

## Session 141 — Granular Sim Engine Phase 5: Tier-3 Structural Governance (2026-08-08)

**What was asked:** Implement Phase 5 — the FINAL phase — of the granular
sim engine plan (`docs/plans/2026-08-08-granular-sim-engine.md`): Tier-3
violations as data nodes, FIBA-3x3 structure options, transition foul
subtypes, injury events, `GameDefinitionPatch.modify_transitions`,
chain-reachability validation, interpreter grounding + golden cases, and
a SIMULATION.md engine-section rewrite — with the hard invariant that the
default path draws ZERO new RNG (seed snapshot unchanged).

**What was built:**

- **Violations as data nodes** in `basketball_micro_chain_nodes()`:
  `violation_backcourt` / `violation_three_second` / `violation_lane`
  (dead-ball terminals emitting `violation.*` events),
  `violation_kicked_ball` (defensive — offense retains with a 14s-style
  clock reset, chain continues), `violation_goaltending` (checked at
  shot resolution; its `selection_weight` IS the per-missed-shot
  probability of awarding the basket — remove it or zero the weight for
  the FIBA "legal after rim contact" rule). All ship with zero-weight
  edges off `initiate` — pure governable surface. Active weights reuse
  the Phase-1 `violation_strictness` x IQ scaling; `three_second` also
  scales with a new paint-dwell counter
  (`PossessionState.paint_event_streak`), so it only threatens offenses
  camped in the lane (pinned: never fires from the perimeter).
- **Transition foul subtypes** (`category="foul"`, `transition_only`
  gated so they only emerge from break possessions): `foul_take` kills
  the transition — personal + team foul, side-out, half-court reset
  (`shot_ctx`/`is_transition` advantage cleared); `foul_clear_path` and
  `foul_away_from_play` award 1 FT + ball, with the FT points banked to
  the game score mid-chain (`early_points`) so box scores reconcile even
  when the possession later ends in a turnover (pinned).
- **FIBA-3x3 structure options** on GameDefinition, all default off:
  `check_ball_restarts` (dead-ball possessions open with
  `admin.check_ball`), `clear_arc_required` (transition offense must
  clear the arc; IQ-scaled failure → `violation.clear_arc` turnover),
  `target_score` (first-to-N — generalizes Elam, mutually exclusive
  with it, enforced by the validator; games stop mid-quarter on the
  target with no empty quarter rows). `resolve_turn` now hands the
  GameDefinition to the micro engine. A FIBA-style patch is pinned
  end-to-end: check-ball events, clear-arc violations, sub-60-possession
  first-to-21 games.
- **Injury events:** new tier-1 `injury_rate` RuleSet knob (ge=0 le=1,
  default 0.0 = OFF, no RNG drawn). Pace-scaled per-possession roll; the
  victim's stamina floors at 0.15 for the rest of the game
  (`HooperState.injured`; quarter-break/halftime recovery skips them),
  `injury` GameEvent + narration. No cross-game persistence — noted as
  future work in the plan doc.
- **`GameDefinitionPatch.modify_transitions`:**
  `{"node": {"target": weight}}` merge-reweights the chain's Markov
  edges — landing on `transitions` plus any non-empty
  success/failure tables so the reweight is authoritative; weight 0
  removes an edge; negative weights rejected by a field validator. Old
  stored patches keep validating.
- **Reachability validation** in `core/game_def_validation.py`: after
  cumulative patch application, every chain start node (`initiate`,
  `second_chance`) must reach a shot or turnover/violation terminal
  through positive-weight edges within the 16-step chain cap, walking
  dangling edges exactly like the engine (no candidates ⇒ forced
  "shot" ⇒ terminates). A bricking patch ("remove all pass AND shot
  exits") is rejected with an explicit "bricks possessions" violation;
  "picks are illegal" passes and still simulates. Also new invariants:
  `target_score` bounds [5, 200] and target/Elam mutual exclusivity.
- **Transition possession tag:** summary `PossessionLog` rows gain
  additive `tags` (`["transition"]` reflects how the possession OPENED,
  surviving a take-foul kill); presenter + game page thread it into
  `narrate_play(transition=...)` — "On the break — ..." lead-ins.
  Tier-3 narration templates for every new event type with graceful
  fallback preserved.
- **Interpreter + goldens:** v2 prompt gains the Tier-3 node list,
  modify_transitions with activation examples, the structure options,
  the reachability note, and three worked examples ("adopt FIBA 3x3
  rules" → structure patch + 12s clock, "traveling and backcourt
  strictly enforced" → strictness + backcourt activation, "first to 21
  wins" → target_score). Mock detects all three patterns;
  `TIER3_INTERPRETER_CASES` (3 cases) pin the grounding.
- **Docs:** SIMULATION.md's possession-model section rewritten as the
  two-engine architecture (dispatch, micro chain loop, chain-vocabulary
  table, Tier-3 surface, hooks, calibration/determinism) — claims
  verified against code. Plan doc marks Phase 5 ✅ with the injury-
  persistence future-work note.

**Zero-RNG invariant held:** the Phase-4 seed snapshot (seed 42: 80-44,
99 possessions, 687 events, same sha256) passes UNCHANGED — every new
mechanic is behind a zero weight, an off-by-default flag, or a
default-0.0 knob, and zero-weight transition edges are skipped before
any RNG draw. Calibration bands untouched. **Performance: 10.26 ms/game**
(200 fixed-seed games, default micro path; budget 40).

**Tests: 2977 passed** (was 2932; +44 Tier-3 structural, +1 golden),
ruff clean.
