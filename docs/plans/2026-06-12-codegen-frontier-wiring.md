# Codegen Frontier Wiring — Implementation Plan (2026-06-12)

Goal: deliver the "players can change ANYTHING" promise. The Phase 6 codegen
infrastructure (AST validator, sandbox runtime, council pipeline) exists but is
disconnected from player proposals. This plan wires it up, adds a pre-execution
human gate, opens the structural-change path, and hardens the sandbox.

## Audit verification (confirmed line refs as of 2026-06-12)

| Claim | Verified location |
|---|---|
| `interpret_codegen_proposal` has zero callers | `src/pinwheel/ai/interpreter.py:1274` |
| `/propose` always uses `interpret_proposal_v2` | `src/pinwheel/discord/bot.py:2296-2298`; revise path `views.py:373-375`; deferred path `core/deferred_interpreter.py:108` |
| STRUCTURE trust level "not supported yet" | `src/pinwheel/ai/codegen_council.py:99` |
| `GameDefinitionPatch` consumed but never produced | `models/game_definition.py:295`; `core/effects.py:185-203`; `core/simulation.py:536-548` |
| `council_rerun_requested` has no consumer | emitted at `discord/bot.py:4393` |
| `opponent_score_modifier` applied to wrong team | `core/hooks.py:1057-1062` folds into actor's `score_modifier`; `apply_hook_results` (`hooks.py:930-957`) adds to ball-holder |
| exec + SIGALRM sandbox | `core/codegen.py:519-584` |

Latent bugs found during exploration (folded into phases):

- **`/disable-effect` does not persist.** `_handle_disable_effect` (`bot.py:4246`)
  mutates a throwaway registry; `load_effect_registry` (`core/effects.py:309`)
  only replays `effect.registered`/`effect.expired`/`effect.repealed`. The
  effect comes back enabled next round.
- **Codegen `meta_writes` silently dropped.** Copied onto `HookResult`
  (`hooks.py:1054`) but `apply_hook_results` never applies them — STATE-trust
  codegen can't persist state.
- **Codegen error counters/auto-disable don't persist** (in-memory only,
  rebuilt from event store each round).
- **`effect.codegen_disabled` and `proposal.review_cleared` missing from
  `GovernanceEventType`** (`models/governance.py:15-46`).

## Architecture

Five phases, ordered so each is independently shippable and the **human gate
exists before generated code can be wired to player proposals**:

1. **Phase 1 — Engine correctness** (opponent score, meta_writes). Pure fix.
2. **Phase 2 — Codegen effect lifecycle + admin pre-execution gate.** Ships dark.
3. **Phase 3 — Router: wire `/propose` to the codegen council.** Feature-flagged.
4. **Phase 4 — Sandbox hardening** (subprocess pre-flight, thread timeout, compile cache, AST tightening, per-game budget).
5. **Phase 5 — Structural change path** (interpreter emits `GameDefinitionPatch`).

Persistence stays event-store-only (no new tables). All new event types added
to `GovernanceEventType`.

---

## Phase 1 — Fix opponent_score_modifier and meta_writes application

`CodegenHookResult.opponent_score_modifier` must survive into `HookResult` as
its own field and be applied to the *defending* team. The current fold
(`result.score_modifier -= codegen_result.opponent_score_modifier`) means
"give the opponent +2" becomes "take 2 from the team with the ball".

Changes — `src/pinwheel/core/hooks.py`:
- Add `opponent_score_modifier: int = 0` to `HookResult` (~line 140).
- `_codegen_result_to_hook_result` (~1037): map the field directly; delete the
  folding block at 1057-1062.
- `apply_hook_results` (~930): sum `opponent_score_modifier` across results and
  apply to the team WITHOUT the ball (mirror the `home_has_ball` branch).
- `apply_hook_results`: apply `r.meta_writes` via `context.meta_store` when
  present. Entity keys are `"entity_type:entity_id"`; ignore malformed keys.

Tests: opponent modifier hits the correct side for both possession directions;
both fields set simultaneously stay independent; meta_writes land in MetaStore;
seeded end-to-end sim with a codegen effect crediting the defending team.

Acceptance: "every made three gives the other team 1 point" demonstrably
credits the defender in a seeded sim.

---

## Phase 2 — Pre-execution human gate (effect lifecycle + admin approval)

State machine on codegen effects, persisted as events, replayed by
`load_effect_registry`:

```
council approves → registered with codegen_approval_status="pending"
pending  --admin Approve--> approved   (effect.codegen_approved)
pending  --admin Reject-->  rejected   (effect.codegen_rejected; stays inert)
approved --/disable-effect or auto-disable--> disabled (effect.codegen_disabled)
```

`_fire_codegen` executes only when `codegen_enabled and status == "approved"`.
New setting `pinwheel_codegen_auto_approve: bool = False` for dev/demo.

While pending, the effect is visible but inert: `/effects`,
`build_effects_summary`, and the proposal-passed announcement show "awaiting
admin sign-off; approximation active" — the `custom_mechanic` approximation
registered at tally stays live until the code is approved.

Changes:
1. `core/hooks.py` (`RegisteredEffect`): add `codegen_approval_status: str = "approved"`
   (default keeps backward compat); include in `to_dict`/`from_dict`;
   `_fire_codegen` (~421) early-returns unless approved.
2. `core/effects.py`: register codegen specs as pending (auto-approve passed as
   a parameter, not a settings import); `load_effect_registry` replays
   `effect.codegen_approved/rejected/disabled` — **this also fixes the
   `/disable-effect` persistence bug**; new `approve_codegen_effect` /
   `reject_codegen_effect` helpers; `build_effects_summary` renders states.
3. `models/governance.py`: extend `GovernanceEventType` with
   `effect.codegen_approved`, `effect.codegen_rejected`,
   `effect.codegen_disabled`, `effect.council_rerun_requested`,
   `effect.council_rerun_completed`, `proposal.review_cleared`.
4. `config.py`: `pinwheel_codegen_auto_approve: bool = False`.
5. `discord/views.py`: `CodegenApprovalView` (pattern: `AdminReviewView`) —
   Approve/Reject with reason modal; approval also repeals the same proposal's
   `custom_mechanic` placeholder (`superseded_by_codegen`); 24h timeout means
   effect stays pending (gate, not veto window).
6. `discord/bot.py`: admin DM on pending registration
   (`notify_admin_codegen_pending`); `/review-codegen` attaches the approval
   view; status in `build_codegen_review_embed` (`embeds.py:1937`).

Tests: pending effect doesn't execute in `simulate_game`, approved does;
approve/reject events round-trip through registry reload; replay of
`effect.codegen_disabled` (regression for persistence bug); view handlers
(non-admin blocked, approve persists + repeals placeholder).

Acceptance: no code path executes council-approved code without an
`effect.codegen_approved` event or auto-approve; admin disable survives reload.

Risk: absentee admin → pending effects never go live. Acceptable by design;
document the asymmetry in `docs/product/RUN_OF_PLAY.md`.

---

## Phase 3 — Wire codegen to player proposals (the router)

**Escalation trigger** — after `interpret_proposal_v2`, escalate iff ALL of:
(a) any effect has `effect_type == "custom_mechanic"` (the interpreter's own
signal that primitives don't suffice); (b) not injection-flagged;
(c) `confidence >= 0.5`; (d) `pinwheel_codegen_enabled` flag on. The council
output is an *additional* codegen EffectSpec attached to the proposal — the v2
result (with its approximation effects) remains what voters vote on.

**Latency**: the council is ~4 Opus calls (60–120s, most expensive AI op in the
system) — runs as a background task after the player clicks Confirm. `/propose`
p50 latency unchanged. Cost bounded by `proposals_per_window` + cooldown, plus
a per-window council-run cap (e.g. 10) in the tick as backstop.

**Race with tally** (fast pace = 1 min): handle both orders —
- Council finishes before pass: append `proposal.codegen_ready` with the
  serialized spec; `tally_governance_with_effects` merges it at pass time.
- Council finishes after pass: pipeline tick sees proposal already passed,
  calls `register_effects_for_proposal` directly, then DMs admin. Idempotency
  by `code_hash`.

**Crash resilience**: mirror `core/deferred_interpreter.py`. Confirm handler
appends `proposal.codegen_requested` before spawning the task; a 60s scheduler
tick re-drives any request without a terminal event (retry cap).

**Rerun-council consumer** lives in the same tick: scan
`effect.council_rerun_requested` minus completed; re-review the EXISTING stored
code (factor `review_existing_code()` out of `run_council_review` — no
regeneration); rejection → `effect.codegen_disabled` + admin DM.

Changes:
1. New `core/codegen_pipeline.py`: `should_escalate_to_codegen`,
   `run_codegen_for_proposal`, `tick_codegen_pipeline`.
2. `ai/codegen_council.py`: factor `review_existing_code`; thread
   `db_session`/usage tracking through council calls (`codegen.generate`,
   `codegen.review.security`, ...). Move `interpret_codegen_proposal`'s body
   into the pipeline and delete it from `interpreter.py` (dead code, wrong
   contract).
3. `discord/views.py` (`ProposalConfirmView.confirm` ~121): append
   `proposal.codegen_requested` + `asyncio.create_task(...)` when escalating;
   keep task reference on the bot. Embeds gain a "Code Council" field.
4. `core/governance.py` (`tally_governance_with_effects` ~813): merge
   `proposal.codegen_ready` specs into the proposal's effects at pass time.
5. `main.py`: register `tick_codegen_pipeline` on the scheduler (60s), only
   when `pinwheel_codegen_enabled`.
6. `config.py`: `pinwheel_codegen_enabled: bool = False` (rollout flag).
7. `core/governance.py` `detect_tier_v2` (~176): bump codegen-bearing
   interpretations to tier 5 (2 tokens, 67%) per RUN_OF_PLAY's "Tier 5+ = wild,
   admin reviewed". Update the tier table in the doc.
8. `models/governance.py`: add `proposal.codegen_requested/ready/rejected/failed`.
9. Validate council `hook_points ⊆` known sim hooks before storing; reject
   otherwise.

Tests: escalation trigger matrix; pipeline event sequences incl. retry and
both race orders with idempotency; rerun consumer; full integration (mock AI):
propose → confirm → tally → pending → approve → seeded sim fires effect,
placeholder repealed.

Acceptance: with flag on + real key, a beyond-primitive proposal produces
approximation effects on pass + a council-generated effect awaiting admin
approval, admin DM shows the code. Flag off → byte-identical to today.

---

## Phase 4 — Sandbox hardening

Keep in-game execution in-process; move heavy isolation to approval time.
Per-call subprocesses (~10–50ms) are incompatible with hundreds of games/hour
with per-possession effects. The AST validator already statically bounds loops;
a small-community game with a mandatory human gate needs: (a) an unblockable
event loop, (b) memory-bomb protection somewhere, (c) cheap per-call guards.

Three layers:
1. **Approval-time pre-flight in a subprocess with rlimits** — new
   `preflight_codegen_effect(code, trust_level) -> list[str]` in
   `core/codegen.py`: `multiprocessing` spawn, `RLIMIT_CPU` 1s, `RLIMIT_AS`
   256MB, execute against ~20 synthetic `SandboxedGameContext`s (edge scores,
   `opponent=None`, hostile meta values), assert valid clamped results <2s.
   Run before `proposal.codegen_ready` AND on admin Approve. POSIX-only is fine
   (macOS dev, Fly.io Linux prod); skip with a warning elsewhere.
2. **In-game: replace SIGALRM with a shared worker thread.** Module-level
   `ThreadPoolExecutor(max_workers=1)`, `future.result(timeout=0.1)`. On
   timeout raise `SandboxViolation` → existing auto-disable. Unblocks running
   `simulate_game` via `asyncio.to_thread` (optional stretch).
3. **Cheap tightening**: compile cache keyed by `code_hash` (currently
   re-compiles every possession); AST: forbid `ast.Pow` unless int-literal
   exponent ≤ 16, reject int literals > 1e9 and string literals > 500 chars;
   per-game execution budget in `_fire_codegen` (~250ms total → skip for
   remainder of game + log).

Update `docs/SECURITY.md`: layered model + explicit non-goals (not a
hostile-multi-tenant sandbox; the human gate is the trust boundary).

Tests: timeout without SIGALRM works off-main-thread; compile cache; AST
rejections; pre-flight catches memory bomb / CPU spin in subprocess; benchmark
100 executions < 50ms.

Acceptance: no `signal.alarm` remains; hostile-but-AST-legal payload caught at
pre-flight; sim throughput within 5% of baseline with two active effects.

---

## Phase 5 — Structural change path (GameDefinitionPatch production)

Structural changes go through the **declarative patch**, not codegen. Producer:
extend the v2 interpreter with effect type `modify_game_definition` — prompt
documents `game_def_patch` JSON (add/remove/modify actions incl. narration
templates, `modify_structure`) with worked examples. Enactment plumbing already
works end-to-end; missing pieces are production and validation.

**Validation — `validate_game_def_patch(patch_dict, current_rules)`**:
1. Pydantic-construct the patch.
2. Apply to `basketball_game_definition(current_rules)` and check invariants:
   ≥1 non-FT shot action survives; `0 ≤ points_on_success ≤ 25`;
   `1 ≤ quarters ≤ 12`; `elam_trigger_quarter ≤ quarters`; at least one shot
   action with weight > 0; `1 ≤ participants_per_side ≤ 5`;
   `50 ≤ safety_cap_possessions ≤ 1000`; `quarter_clock_seconds` 30–3600.
3. **Smoke sim**: one seeded `simulate_game` with the patched def; assert it
   terminates and total score < 500.
4. Validate against the *cumulatively patched* def (apply existing active
   patches first) — patches compound in registration order.

Call sites: interpretation time (violations → `clarification_needed` with the
list) and defensively at tally (`effect.patch_rejected`, skip registration).
`_needs_admin_review` includes `modify_game_definition`.

What this delivers: new shot types/values/narration, period count/length, Elam
on/off/retune, on-court roster size, pace — genuinely "not basketball anymore".

What the patch model still CANNOT express (next expressiveness increment,
separate follow-up — three additive `GameDefinition` fields):
1. `win_condition: WinCondition` (`clock_elam | first_to_n | best_of_turns |
   score_cap`) — consumed by game-over checks.
2. `resources: dict[str, ResourceDefinition]` — stored in `GameState`, exposed
   via a `modify_resource` primitive and `ctx.state` for codegen, rendered in
   box scores.
3. `ActionDefinition.on_success / on_failure`: lists of EXISTING hook
   `action_code` primitives executed by the possession resolver — reuses the
   `hooks.py` evaluator rather than inventing a second vocabulary.

Tests: per-invariant validation messages; mock interpreter patterns ("add a
shot called X worth N", "make games N quarters"); end-to-end seeded sim shows
the new action in the possession log; tally rejects an invalid stored patch
without crashing the round.

Acceptance: "Add a shot from half court worth 4 points called The Prayer"
works end-to-end through `/propose` and shows in play-by-play; "6 quarters, no
Elam" changes next round's games; invalid patches can never register.

---

## Rollout order

1. **Phase 1** — pure fix, deploy immediately.
2. **Phase 2** — ships dark; also delivers the `/disable-effect` persistence fix.
3. **Phase 3** — behind `PINWHEEL_CODEGEN_ENABLED`; staging burn-in (5–10 live
   council proposals) before prod.
4. **Phase 4** — land before flipping the Phase 3 flag in prod (3+4 can be
   developed in parallel; the flag flip is the gate).
5. **Phase 5** — independent of 2–4; can ship any time after Phase 1.

No schema changes anywhere — all state is event-store payloads. Each phase ends
with `uv run pytest -x -q` + `ruff check` green and a dev-log entry.
