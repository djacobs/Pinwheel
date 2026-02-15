# 2026-02-14 Plan Cross-Reference and Implementation Efficiencies

**Date:** 2026-02-14
**Scope:** All 29 plan files created on 2026-02-14
**Purpose:** Dependency mapping, overlap analysis, shared infrastructure, bug consolidation, and recommended implementation order.

---

## 1. Dependencies — What Must Be Done Before What

### Hard dependencies (plan A must complete before plan B can start)

| Prerequisite Plan | Dependent Plan(s) | Why |
|---|---|---|
| `proposal-effects-system.md` (Phase 1: Meta Columns) | `effects-system-wave-2-completion.md` | Wave 2 completes the integration of the effects system. Cannot proceed without the base effects infrastructure. |
| `proposal-effects-system.md` (Phase 2: Hook Architecture) | `effects-system-wave-2-completion.md` | Report hooks, governance hooks, and hooper meta loading all depend on the hook architecture being in place. |
| `proposal-effects-system.md` (Phase 3: New Interpreter) | `upgrade-propose-to-v2-effects-interpreter.md` | The V2 interpreter upgrade switches `/propose` to use `interpret_proposal_v2()`, which is defined in the effects system plan. |
| `proposal-effects-system.md` | `repeal-mechanism.md` | Repeal targets effects registered via the effects system. Without effects being registerable, there is nothing to repeal. |
| `effects-system-wave-2-completion.md` | `repeal-mechanism.md` | The repeal mechanism depends on the full effect lifecycle (load, fire, tick, flush) being connected. |
| `moves-earned-and-governed-acquisition.md` (Phase 1: Fix Move Loading) | `moves-earned-and-governed-acquisition.md` (Phases 2-4) | The `_row_to_team()` hardcoding `moves=[]` bug must be fixed before any moves (earned or governed) can work. |
| `proposal-effects-system.md` (Phase 3: New Interpreter) | `moves-earned-and-governed-acquisition.md` (Phase 3: Governed Moves) | Governed moves extend the V2 interpreter with `move_grant` effect type. |
| `decouple-governance-from-game-simulation.md` | `season-lifecycle.md` (Phase 3: Offseason) | The offseason phase is governance-only (no games). The decoupled `tally_pending_governance()` function is what enables governance to run independently of simulation. |
| `season-lifecycle.md` (Phase 1: Phase Enum) | `season-lifecycle.md` (Phases 2-4) | All subsequent lifecycle phases depend on the `SeasonPhase` enum and `transition_season()` being in place. |
| `season-lifecycle.md` | `season-memorial-system.md` | Memorials generate during `archive_season()`, which depends on the CHAMPIONSHIP -> OFFSEASON -> COMPLETE lifecycle. |
| `season-lifecycle.md` | `season-transitions-and-carryover.md` | Carryover improvements (backstory preservation, offseason governance path) depend on the full lifecycle being implemented. |
| `auto-migrate-missing-columns.md` | Any plan adding new DB columns | The auto-migrator prevents the class of bug where new columns are added to ORM models but production SQLite is never migrated. Must be in place before adding more columns. |
| `fix-governance-user-journey-p0.md` | `rate-limiting-proposals.md` | Rate limiting only matters once governors actually have tokens and can propose. The P0 fix ensures `/join` grants tokens. |
| `fix-sqlite-write-lock-contention.md` | `bot-search-natural-language-stats.md` | Bot search adds more concurrent Discord commands hitting the DB. Without the write lock fix, these will fail during `tick_round`. |
| `parallel-voting-admin-veto.md` | `proposal-amendment-flow.md` | The amendment flow changes how proposals are displayed and tallied. The veto mechanism should be settled first to avoid conflicting tally logic. |

### Soft dependencies (plan A should ideally precede plan B)

| Earlier Plan | Later Plan | Why |
|---|---|---|
| `remove-postgresql-go-sqlite-only.md` | `fix-sqlite-write-lock-contention.md` | Removing PostgreSQL simplifies the engine module, making the write-lock fix cleaner. |
| `admin-nav-landing-page.md` | `token-cost-tracking-dashboard.md` | The cost dashboard is an admin page. Having the admin landing page and `is_admin` auth context in place provides the registration point. |
| `governance-event-store-enumeration.md` | Multiple governance plans | This reference document identifies bugs (`trade.completed` vs `trade.accepted`) that other plans may encounter. Should be consulted before modifying governance code. |
| `new-player-onboarding.md` | `spectator-journey-and-team-following.md` | The `build_league_context()` function from onboarding could be reused by the spectator journey for personalized dashboards. |
| `fix-duplicate-discord-channels.md` | Any plan adding Discord commands | The distributed lock prevents duplicate operations. Should be in place before adding more bot functionality. |
| `strategy-overrides-simulation-integration.md` | `proposal-effects-system.md` (Phase 4: Simulation Integration) | Completing strategy integration ensures the simulation parameters are fully connected before the effects system adds hook points alongside them. |

---

## 2. Overlapping Work — Files Touched by Multiple Plans

### `src/pinwheel/discord/bot.py` (touched by 11 plans)

| Plan | Changes |
|---|---|
| `fix-governance-user-journey-p0.md` | Add `regenerate_tokens()` after enrollment in `_handle_join()` and `_sync_role_enrollments()` |
| `decouple-governance-from-game-simulation.md` | No direct changes, but related test cleanup for `make_interaction()` |
| `parallel-voting-admin-veto.md` | Modify `ProposalConfirmView.confirm()` flow |
| `new-player-onboarding.md` | Add `build_league_context()` call in `_handle_join()`, add `/status` command |
| `upgrade-propose-to-v2-effects-interpreter.md` | Switch `/propose` to V2 interpreter |
| `rate-limiting-proposals.md` | Add cooldown dict, `proposals_per_window` enforcement to `_handle_propose()` |
| `bot-search-natural-language-stats.md` | Add `/ask` command and `_handle_ask()` handler |
| `repeal-mechanism.md` | Add `/effects` and `/repeal` commands |
| `spectator-journey-and-team-following.md` | Add `/follow` and `/unfollow` commands |
| `season-memorial-system.md` | Add `/history` command, `season.memorial_generated` event handler |
| `proposal-amendment-flow.md` | Add `/amend` command with autocomplete |

**Efficiency:** A developer touching `bot.py` should batch related command additions. Group: `/status` + `/ask` + `/effects` + `/repeal` + `/follow` + `/history` + `/amend` into 2-3 sessions rather than 7 separate ones.

### `src/pinwheel/core/game_loop.py` (touched by 9 plans)

| Plan | Changes |
|---|---|
| `decouple-governance-from-game-simulation.md` | Extract `tally_pending_governance()` |
| `fix-sqlite-write-lock-contention.md` | Extract phase functions, add `step_round_multisession()` |
| `parallel-voting-admin-veto.md` | Add veto exclusion in `tally_pending_governance()` |
| `moves-earned-and-governed-acquisition.md` | Fix `_row_to_team()` to load moves, add `_check_earned_moves()` |
| `effects-system-wave-2-completion.md` | Fire report hooks, governance hooks, load hooper meta, enrich `_SimPhaseResult` |
| `proposal-effects-system.md` | Load registry, create MetaStore, flush after round |
| `proposal-amendment-flow.md` | Update `tally_pending_governance()` for vote timestamp filtering |
| `season-lifecycle.md` | Replace `update_season_status(completed)` with `enter_championship()` |
| `strategy-overrides-simulation-integration.md` | Pass strategy params to simulation callees |

**Efficiency:** The `decouple-governance` and `fix-sqlite-write-lock-contention` plans both refactor `step_round()`. Do them together. The governance extraction creates the function boundaries that the multi-session split then separates with session boundaries. Doing them sequentially in one session avoids a double-refactor.

### `src/pinwheel/db/repository.py` (touched by 10 plans)

| Plan | Changes |
|---|---|
| `parallel-voting-admin-veto.md` | Update `get_governor_activity()`, `get_all_proposals()` for vetoed detection |
| `remove-postgresql-go-sqlite-only.md` | Remove `.with_for_update()` |
| `rate-limiting-proposals.md` | Add query for counting proposals per governance window |
| `moves-earned-and-governed-acquisition.md` | Add `get_hooper_season_stats()`, `add_hooper_move()` |
| `bot-search-natural-language-stats.md` | Add `get_stat_leaders()`, `get_head_to_head()`, `get_games_for_team()` |
| `effects-system-wave-2-completion.md` | Add `load_hooper_meta()`, extend `flush_meta_store()` |
| `season-memorial-system.md` | Add `get_all_reports_for_season()`, `get_playoff_games()` |
| `spectator-journey-and-team-following.md` | Add follow/unfollow/query methods |
| `proposal-amendment-flow.md` | Add query for `proposal.amended` event count |
| `token-cost-tracking-dashboard.md` | Add `record_ai_usage()`, `query_ai_usage()` |

**Efficiency:** Many plans need new repository query methods. A single "repository expansion" session could add all the new query methods at once, with tests, then the consuming plans can build on top.

### `src/pinwheel/discord/views.py` (touched by 5 plans)

| Plan | Changes |
|---|---|
| `parallel-voting-admin-veto.md` | Rewrite `ProposalConfirmView.confirm()`, rewrite `AdminReviewView` to Veto/Clear |
| `upgrade-propose-to-v2-effects-interpreter.md` | Switch Revise modal to V2 interpreter, add V2 interpretation to `ProposalConfirmView` |
| `rate-limiting-proposals.md` | Move token deduction to propose-time |
| `repeal-mechanism.md` | Add `RepealConfirmView` |
| `proposal-amendment-flow.md` | Add `AmendConfirmView` |

### `src/pinwheel/discord/embeds.py` (touched by 7 plans)

| Plan | Changes |
|---|---|
| `parallel-voting-admin-veto.md` | Update announcement embed, `_STATUS_LABELS` |
| `upgrade-propose-to-v2-effects-interpreter.md` | Update `build_interpretation_embed` for V2 |
| `new-player-onboarding.md` | Add `build_onboarding_embed()` |
| `bot-search-natural-language-stats.md` | Add `build_search_result_embed()` |
| `repeal-mechanism.md` | Add `build_effects_list_embed()`, `build_repeal_confirm_embed()` |
| `season-memorial-system.md` | Add `build_memorial_embed()` |
| `proposal-amendment-flow.md` | Add `build_amendment_confirm_embed()`, update `build_proposals_embed()` |

### `src/pinwheel/core/governance.py` (touched by 6 plans)

| Plan | Changes |
|---|---|
| `decouple-governance-from-game-simulation.md` | Standalone `tally_pending_governance()` |
| `parallel-voting-admin-veto.md` | Add `admin_veto_proposal()`, rename `admin_approve` -> `admin_clear` |
| `rate-limiting-proposals.md` | Add `count_proposals_in_window()` |
| `moves-earned-and-governed-acquisition.md` | Enact `move_grant` effects |
| `repeal-mechanism.md` | Add `submit_repeal_proposal()`, repeal execution in `tally_governance_with_effects()` |
| `proposal-amendment-flow.md` | Add `count_amendments()`, vote timestamp filtering |

### `src/pinwheel/ai/interpreter.py` (touched by 4 plans)

| Plan | Changes |
|---|---|
| `proposal-effects-system.md` | Add `interpret_proposal_v2()` with new prompt |
| `upgrade-propose-to-v2-effects-interpreter.md` | Improve V2 system prompt for creative proposals |
| `moves-earned-and-governed-acquisition.md` | Add move-grant vocabulary to V2 prompt |
| `effects-system-wave-2-completion.md` | Update prompt to reference `sim.possession.pre` instead of `sim.shot.pre` |

**Efficiency:** All four interpreter changes should be batched. The V2 prompt is being created, improved, and extended across these plans.

### `src/pinwheel/core/scheduler_runner.py` (touched by 4 plans)

| Plan | Changes |
|---|---|
| `decouple-governance-from-game-simulation.md` | Add governance-only path for completed seasons |
| `fix-sqlite-write-lock-contention.md` | Update `tick_round` to use multi-session variant |
| `fix-duplicate-discord-channels.md` | Add distributed tick_round lock |
| `season-lifecycle.md` | Handle new season phases in `tick_round()` |

### `src/pinwheel/db/models.py` (touched by 5 plans)

| Plan | Changes |
|---|---|
| `proposal-effects-system.md` | Add `meta` columns to 7 tables |
| `season-memorial-system.md` | Add `memorial` column to `SeasonArchiveRow` |
| `spectator-journey-and-team-following.md` | Add `TeamFollowRow` table, `notification_preferences` on `PlayerRow` |
| `token-cost-tracking-dashboard.md` | Add `AIUsageLogRow` table |
| `moves-earned-and-governed-acquisition.md` | (Verify `moves` JSON column already exists) |

### `src/pinwheel/core/possession.py` (touched by 2 plans)

| Plan | Changes |
|---|---|
| `strategy-overrides-simulation-integration.md` | Add `defensive_intensity` to `check_foul()` and `drain_stamina()`, add `pace_modifier` to `drain_stamina()` |
| `proposal-effects-system.md` | Add hook fire points between possession steps |

---

## 3. Shared Infrastructure — What Could Serve Multiple Plans

### 3a. Governor/Season Context Query Function

**Plans served:** `new-player-onboarding.md`, `spectator-journey-and-team-following.md`, `bot-search-natural-language-stats.md`, `season-memorial-system.md`

All four plans need to gather league-wide context: standings, active proposals, rule changes, governor counts. The `build_league_context()` function from `new-player-onboarding.md` could be generalized into a shared `core/context.py` module that serves:
- Onboarding DMs (`/join`)
- Status commands (`/status`)
- Spectator personalized home page
- Bot search data layer
- Memorial data gathering

### 3b. Repository Stat Aggregation Methods

**Plans served:** `bot-search-natural-language-stats.md`, `moves-earned-and-governed-acquisition.md`, `season-memorial-system.md`, `playoff-bracket-and-seeding.md`

All need aggregate box score queries:
- `get_hooper_season_stats()` (milestones)
- `get_stat_leaders()` (bot search, memorial)
- `compute_head_to_head()` (bot search, memorial, playoff seeding)
- `get_games_for_team()` (bot search, memorial)

Build these as a single "repository stats expansion" before any of the consuming plans.

### 3c. Discord `make_interaction()` Test Helper

**Plans served:** Every plan that adds Discord commands (11 plans)

The `decouple-governance-from-game-simulation.md` plan proposes a `make_interaction()` test helper to replace scattered mock setup. This helper would serve all 11 plans that add or modify Discord commands.

### 3d. Admin Auth Gate (`is_admin` in auth context)

**Plans served:** `admin-nav-landing-page.md`, `token-cost-tracking-dashboard.md`, any future admin page

The `is_admin` flag from the admin nav plan is the auth foundation for every admin-only route.

### 3e. Effect Registry Query Interface

**Plans served:** `repeal-mechanism.md`, `effects-system-wave-2-completion.md`, `proposal-effects-system.md`

All three need to query active effects by season, display them, and manage their lifecycle. A well-defined `get_active_effects_summary()` function would serve the `/effects` command, the effects-in-narrative injection, and the admin review flow.

### 3f. Name Resolver (Team/Hooper Name to ID)

**Plans served:** `bot-search-natural-language-stats.md`, `moves-earned-and-governed-acquisition.md` (governed moves targeting by name), `proposal-amendment-flow.md` (proposal autocomplete)

A shared `NameResolver` class that loads team and hooper names for the active season and does fuzzy matching would serve all three plans.

---

## 4. Bugs Found Across Plans

### Bug 1: `trade.completed` vs `trade.accepted` naming mismatch
- **Severity:** Medium (data correctness)
- **Found in:** `governance-event-store-enumeration.md` (Gap #1)
- **Affected plans:** `season-lifecycle.md` (awards), `season-memorial-system.md` (Coalition Builder award)
- **Description:** `compute_awards()` in `season.py` queries for `["trade.completed"]` events, but `accept_trade()` in `tokens.py` emits `"trade.accepted"`. The Coalition Builder award always shows 0 trades.
- **Fix:** Change the query in `compute_awards()` to `["trade.accepted"]`.

### Bug 2: `_row_to_team()` hardcodes `moves=[]`
- **Severity:** High (feature-blocking)
- **Found in:** `moves-earned-and-governed-acquisition.md` (Phase 1)
- **Affected plans:** `moves-earned-and-governed-acquisition.md`, `proposal-effects-system.md` (any effect targeting moves)
- **Description:** `game_loop.py` line 67 creates hoopers with `moves=[]`, discarding all DB-stored moves. No moves are active during simulation despite being seeded correctly at creation. This means archetype moves assigned during seeding are silently ignored.
- **Fix:** Deserialize `hooper.moves` JSON into `Move` objects in `_row_to_team()`.

### Bug 3: `proposals_per_window` never enforced
- **Severity:** Medium (governance integrity)
- **Found in:** `rate-limiting-proposals.md`
- **Affected plans:** `rate-limiting-proposals.md`, `governance-event-store-enumeration.md`
- **Description:** The `proposals_per_window` field exists on `RuleSet` (default 3, range 1-10, Tier 4 governable parameter) but is never read or enforced anywhere in the submission flow. Governors can submit unlimited proposals as long as they have tokens.
- **Fix:** Enforce in `_handle_propose()` by counting `proposal.submitted` events per governor in the current governance window.

### Bug 4: Token balance race condition on `/propose`
- **Severity:** Medium (exploitable)
- **Found in:** `rate-limiting-proposals.md`
- **Affected plans:** `rate-limiting-proposals.md`
- **Description:** `has_token()` check happens at `/propose` time, but `token.spent` event is appended during confirm (in `ProposalConfirmView`). Two rapid `/propose` calls can both pass the balance check before either deducts. With 1 remaining PROPOSE token, both proposals could proceed.
- **Fix:** Move `token.spent` event from confirm step to propose step. Refund on cancel.

### Bug 5: `defensive_intensity` not fully integrated
- **Severity:** Medium (gameplay fidelity)
- **Found in:** `strategy-overrides-simulation-integration.md`
- **Affected plans:** `strategy-overrides-simulation-integration.md`
- **Description:** `defensive_intensity` only affects shot contest. It does NOT affect foul rate (documented as intended), stamina drain, or scheme selection. The AI strategy prompt tells governors these effects exist, but they do not.
- **Fix:** Add `defensive_intensity` parameter to `check_foul()` and `drain_stamina()`. Add strategy influence to `select_scheme()`.

### Bug 6: `pace_modifier` does not affect stamina drain
- **Severity:** Low (gameplay fidelity)
- **Found in:** `strategy-overrides-simulation-integration.md`
- **Affected plans:** `strategy-overrides-simulation-integration.md`
- **Description:** Faster pace (lower `pace_modifier`) creates more possessions per quarter but does not increase stamina drain per possession. Logically, pushing tempo should tire players faster.
- **Fix:** Add `pace_modifier` parameter to `drain_stamina()`.

### Bug 7: `sim.shot.pre` hook referenced but does not exist
- **Severity:** Medium (documentation/code mismatch)
- **Found in:** `effects-system-wave-2-completion.md`
- **Affected plans:** `effects-system-wave-2-completion.md`, `proposal-effects-system.md`
- **Description:** EFFECTS_SYSTEM.md and the interpreter V2 prompt reference `sim.shot.pre` as a hook point for shooting probability modifiers. The simulation fires `sim.possession.pre` but never `sim.shot.pre`. The flagship swagger example would not fire.
- **Fix:** Update docs and interpreter prompt to reference `sim.possession.pre` instead. The `shot_probability_modifier` on `HookResult` is already applied at that point.

### Bug 8: Report hooks not fired (`report.simulation.pre`, `report.commentary.pre`)
- **Severity:** Medium (feature gap)
- **Found in:** `effects-system-wave-2-completion.md`
- **Affected plans:** `effects-system-wave-2-completion.md`, `proposal-effects-system.md`
- **Description:** Narrative effects register with `report.simulation.pre` and `report.commentary.pre` hook points, but these hooks are never fired before report generation. Narrative effects that should inject context into AI reports are inert.
- **Fix:** Fire these hooks in `_phase_ai()` before generating commentary and reports.

### Bug 9: Hooper backstories not carried over on `/new-season`
- **Severity:** Low (user experience)
- **Found in:** `season-transitions-and-carryover.md` (Gap #1)
- **Affected plans:** `season-transitions-and-carryover.md`
- **Description:** `carry_over_teams()` creates new hooper rows but does not copy the `backstory` column. Governors who wrote backstories via `/bio` lose them on season transition.
- **Fix:** Add `backstory=hooper.backstory` to `create_hooper()` in `carry_over_teams()`.

### Bug 10: Governance hooks not fired (`gov.pre`, `gov.post`)
- **Severity:** Medium (feature gap)
- **Found in:** `effects-system-wave-2-completion.md`
- **Affected plans:** `effects-system-wave-2-completion.md`
- **Description:** The interpreter prompt references governance hooks, but they are never fired in the governance tally flow. Effects like "all votes count double during playoffs" have no execution path.
- **Fix:** Fire `gov.pre` and `gov.post` hooks in `tally_pending_governance()`.

### Bug 11: Hooper meta never loaded into MetaStore
- **Severity:** Medium (feature gap)
- **Found in:** `effects-system-wave-2-completion.md`
- **Affected plans:** `effects-system-wave-2-completion.md`, `moves-earned-and-governed-acquisition.md`
- **Description:** Only team meta is loaded into MetaStore. Hooper-level meta is never loaded. Effects targeting individual hoopers cannot read or write hooper metadata.
- **Fix:** Load hooper meta in `_phase_simulate_and_govern()` alongside team meta.

### Bug 12: Mirrors module is dead code
- **Severity:** Low (code hygiene)
- **Found in:** `api-architecture-doc.md`
- **Affected plans:** None directly
- **Description:** `mirrors.py` router exists in the codebase but is NOT included in `main.py`. Dead module.
- **Fix:** Remove or register the module.

---

## 5. Recommended Implementation Order

### Wave 1: Bug Fixes and Prerequisites (unblock other work)

These should be done first because they fix correctness issues and unblock downstream plans.

| # | Plan | Effort | Rationale |
|---|---|:---:|---|
| 1.1 | `auto-migrate-missing-columns.md` | **S** | Prevents the entire class of "forgot to migrate prod" bugs. Must be in place before adding any more DB columns. |
| 1.2 | `fix-governance-user-journey-p0.md` | **S** | P0 fix. Governors cannot play the game without this. 2 code changes + tests. |
| 1.3 | `remove-postgresql-go-sqlite-only.md` | **S** | Pure cleanup. Removes dead code and simplifies engine module before other plans touch it. |
| 1.4 | `decouple-governance-from-game-simulation.md` | **M** | Structural fix. Enables governance during completed/offseason seasons. Creates `tally_pending_governance()` that multiple plans depend on. |
| 1.5 | Bug: `trade.completed` -> `trade.accepted` | **S** | One-line query fix in `compute_awards()`. |
| 1.6 | Bug: `_row_to_team()` hardcodes `moves=[]` | **S** | One function change. Unblocks the entire moves system. |
| 1.7 | Bug: `proposals_per_window` enforcement | **S** | Part of `rate-limiting-proposals.md` but can be done standalone as a quick fix. |

### Wave 2: Quick Wins (high value, low effort)

| # | Plan | Effort | Rationale |
|---|---|:---:|---|
| 2.1 | `admin-nav-landing-page.md` | **S** | Small template + auth context change. Unblocks all admin pages. |
| 2.2 | `fix-duplicate-discord-channels.md` | **S** | Distributed lock prevents production duplicates. Low risk, high reliability value. |
| 2.3 | `parallel-voting-admin-veto.md` | **M** | Removes the admin approval bottleneck. Direct UX improvement for governors. |
| 2.4 | `rate-limiting-proposals.md` (remaining items) | **M** | Cooldown, race condition fix, and semaphore. Completes governance safety rails. |
| 2.5 | `fix-sqlite-write-lock-contention.md` | **M** | Prevents `/join` failures during `tick_round`. Direct reliability improvement. |
| 2.6 | `strategy-overrides-simulation-integration.md` | **M** | Completes `defensive_intensity` and `pace_modifier` integration. Makes strategy meaningful. |
| 2.7 | `upgrade-propose-to-v2-effects-interpreter.md` | **M** | Switches `/propose` to V2 interpreter. Immediate improvement to proposal interpretation quality. |

### Wave 3: Core Feature Work

| # | Plan | Effort | Rationale |
|---|---|:---:|---|
| 3.1 | `proposal-effects-system.md` (Phases 1-4) | **XL** | The big one. Meta columns, hook architecture, new interpreter, effect execution engine. Foundation for the game's creative governance. |
| 3.2 | `effects-system-wave-2-completion.md` | **M** | Completes effects integration: report hooks, governance hooks, hooper meta, `sim.shot.pre` doc fix. |
| 3.3 | `season-lifecycle.md` | **L** | 8-phase lifecycle: championship, offseason, tiebreakers. Major narrative improvement. |
| 3.4 | `moves-earned-and-governed-acquisition.md` (Phases 2-4) | **L** | Earned and governed moves. Deepens hooper progression. |
| 3.5 | `new-player-onboarding.md` | **M** | State of the League briefing for new governors. `/status` command for all. |
| 3.6 | `repeal-mechanism.md` | **M** | Governance completeness: `/effects` browser + `/repeal` command. |
| 3.7 | `proposal-amendment-flow.md` | **L** | `/amend` command, `AmendConfirmView`, vote reset, amendment cap. Full governance feature. |
| 3.8 | `token-cost-tracking-dashboard.md` | **M** | AI cost visibility. New DB table, wrapper function, admin dashboard page. |

### Wave 4: Polish and Nice-to-Haves

| # | Plan | Effort | Rationale |
|---|---|:---:|---|
| 4.1 | `bot-search-natural-language-stats.md` | **L** | `/ask` command with two-call AI pipeline. Rich but complex feature. |
| 4.2 | `season-memorial-system.md` | **L** | AI-generated season memorials, `/history` command, memorial web pages. Narrative polish. |
| 4.3 | `spectator-journey-and-team-following.md` | **XL** | Team following, notifications, conversion funnel, metrics. Large multi-phase feature. |
| 4.4 | `dramatic-pacing-modulation.md` | **L** | Variable-speed replay with drama classification. Presentation polish. |
| 4.5 | `playoff-bracket-and-seeding.md` | **M** | Bracket visualization page, series record in API. Mostly documentation + one new page. |
| 4.6 | `season-transitions-and-carryover.md` | **S** | Backstory carryover, offseason path documentation. Mostly small fixes. |

### Wave 5: Documentation (can be done anytime)

| # | Plan | Effort | Rationale |
|---|---|:---:|---|
| 5.1 | `api-architecture-doc.md` | **S** | Reference documentation of all API endpoints. Already written as a plan; just needs to become a doc. |
| 5.2 | `tech-architecture-doc.md` | **S** | Reference documentation of all technical systems. Already written as a plan. |
| 5.3 | `governance-event-store-enumeration.md` | **S** | Reference documentation of all event types. Already written as a plan. |
| 5.4 | `elam-ending-mechanics.md` | **S** | Reference documentation of Elam implementation. Already written as a plan. |

---

## 6. Effort Estimates

| Plan File | Size | Rationale |
|---|:---:|---|
| `auto-migrate-missing-columns.md` | **S** | One new function in `engine.py`, replace 6 lines in `main.py`, one test. |
| `fix-governance-user-journey-p0.md` | **S** | Two `regenerate_tokens()` calls + tests. |
| `remove-postgresql-go-sqlite-only.md` | **S** | Delete dependency, simplify engine, update docs. No logic changes. |
| `decouple-governance-from-game-simulation.md` | **M** | Extract function from `step_round()`, add governance-only path in `tick_round()`, test interaction mock helper, 4 new tests. |
| `admin-nav-landing-page.md` | **S** | Auth context change, one template, one nav update. |
| `fix-duplicate-discord-channels.md` | **S** | DB-level lock with BotStateRow. Two lock functions + integration in `tick_round` and `_setup_server`. |
| `parallel-voting-admin-veto.md` | **M** | Rewrite confirm flow, rewrite admin review view, add veto exclusion in tally, update embeds, 6 new tests. |
| `rate-limiting-proposals.md` | **M** | Enforce `proposals_per_window`, add cooldown, fix race condition, optional semaphore. 7 tests. |
| `fix-sqlite-write-lock-contention.md` | **M** | Extract 3 phase functions, add `step_round_multisession()`, update `tick_round()`. Significant refactor but mechanical. |
| `strategy-overrides-simulation-integration.md` | **M** | 4 fixes (foul rate, stamina, scheme selection, metadata). Each is a small code change but needs statistical verification testing. |
| `upgrade-propose-to-v2-effects-interpreter.md` | **M** | Switch `/propose` to V2, improve prompt, update embed, update mock. Backward compatibility via `.to_rule_interpretation()`. |
| `proposal-effects-system.md` | **XL** | 5 implementation phases. New modules (`meta.py`, `effects.py`), rewrite `hooks.py`, extend 10+ files, migration script, 5 test categories. The single largest plan. |
| `effects-system-wave-2-completion.md` | **M** | 5 priorities: fire report hooks, fire governance hooks, load hooper meta, fix `sim.shot.pre` docs, enrich `_SimPhaseResult`. Moderate because the infrastructure exists; this is wiring. |
| `season-lifecycle.md` | **L** | 4 phases: enum foundation, championship, offseason, tiebreakers. Touches `season.py`, `game_loop.py`, `scheduler_runner.py`, `config.py`, `bot.py`, `report.py`. |
| `moves-earned-and-governed-acquisition.md` | **L** | 4 phases: fix loading, milestones, governed moves, narrative integration. New module (`milestones.py`), extend interpreter, governance, repository, reports, commentary. |
| `new-player-onboarding.md` | **M** | New `onboarding.py` module, new embed builder, `/status` command, 13 tests. |
| `repeal-mechanism.md` | **M** | `/effects` browser, `/repeal` command with autocomplete, `RepealConfirmView`, `repeal_effect()` function, tally integration. 11 tests. |
| `proposal-amendment-flow.md` | **L** | `/amend` command, `AmendConfirmView`, AI re-interpretation, vote reset, amendment cap, self-prevention, display updates. 13 tests. |
| `token-cost-tracking-dashboard.md` | **M** | New DB table, usage wrapper, modify 5 AI call sites, dashboard route + template. |
| `bot-search-natural-language-stats.md` | **L** | New `search.py` module with two-call AI pipeline, name resolver, 3 repo methods, `/ask` command, rate limiting, guard tests. |
| `season-memorial-system.md` | **L** | New `memorial.py` module, 4 AI prompts, lifecycle integration, 2 new templates, `/history` command, memorial embed. |
| `spectator-journey-and-team-following.md` | **XL** | 4 phases: team following (DB + API + UI), notifications (web + Discord DM), conversion, metrics. New table, new router, template changes across multiple pages. |
| `dramatic-pacing-modulation.md` | **L** | New `drama.py` module with classification engine, presenter integration, SSE event enrichment, CSS treatment, 11+ tests. |
| `playoff-bracket-and-seeding.md` | **M** | Mostly documentation of existing behavior. New bracket page + API endpoint. 6 identified gaps with varying effort. |
| `season-transitions-and-carryover.md` | **S** | Mostly documentation of existing behavior. Backstory fix is one line. Other gaps are documented but deferred. |
| `api-architecture-doc.md` | **S** | Documentation only. Content is already written in the plan. |
| `tech-architecture-doc.md` | **S** | Documentation only. Content is already written in the plan. |
| `governance-event-store-enumeration.md` | **S** | Documentation + 5 small bug/gap recommendations. |
| `elam-ending-mechanics.md` | **S** | Documentation only. No code changes. |

### Size Definitions

| Size | Meaning | Approximate Scope |
|---|---|---|
| **S** | Small | 1-3 files changed, < 100 lines of new code, < 5 tests |
| **M** | Medium | 3-6 files changed, 100-400 lines of new code, 5-10 tests |
| **L** | Large | 6-10 files changed, 400-1000 lines of new code, 10-20 tests |
| **XL** | Extra Large | 10+ files changed, 1000+ lines of new code, 20+ tests, multiple phases |

---

## 7. Suggested Developer Sessions (Batch by File Overlap)

### Session A: Governance Pipeline Fixes
**Plans:** `fix-governance-user-journey-p0`, `decouple-governance-from-game-simulation`, `parallel-voting-admin-veto`, `rate-limiting-proposals`, trade.completed bug fix
**Files touched:** `core/governance.py`, `core/game_loop.py`, `discord/bot.py`, `discord/views.py`, `core/tokens.py`
**Why batch:** All four plans modify the governance submission and tally pipeline. Doing them together avoids repeated refactoring of the same functions.

### Session B: Database and Infrastructure Cleanup
**Plans:** `auto-migrate-missing-columns`, `remove-postgresql-go-sqlite-only`, `fix-sqlite-write-lock-contention`, `fix-duplicate-discord-channels`
**Files touched:** `db/engine.py`, `main.py`, `core/game_loop.py`, `core/scheduler_runner.py`, `discord/bot.py`
**Why batch:** All four are infrastructure/reliability improvements with no feature dependencies on each other.

### Session C: Simulation Fidelity
**Plans:** `strategy-overrides-simulation-integration`, `moves-earned-and-governed-acquisition` (Phase 1 only: fix move loading)
**Files touched:** `core/possession.py`, `core/defense.py`, `core/game_loop.py`, `core/simulation.py`, `models/game.py`
**Why batch:** Both plans make the simulation more faithful to its documented behavior. The strategy fixes and move loading fix are independent changes to the same hot path.

### Session D: Effects System (multi-session epic)
**Plans:** `proposal-effects-system`, `effects-system-wave-2-completion`, `upgrade-propose-to-v2-effects-interpreter`
**Files touched:** `core/hooks.py`, `core/effects.py`, `core/meta.py`, `core/game_loop.py`, `ai/interpreter.py`, `models/governance.py`, `db/models.py`, `db/repository.py`
**Why batch:** These three plans are tightly coupled phases of the same system.

### Session E: Discord Command Expansion
**Plans:** `new-player-onboarding`, `repeal-mechanism`, `proposal-amendment-flow`
**Files touched:** `discord/bot.py`, `discord/views.py`, `discord/embeds.py`, `core/onboarding.py`
**Why batch:** All add new slash commands and Discord interactive views. Batching minimizes repeated test setup and `bot.py` editing.

### Session F: Admin and Ops
**Plans:** `admin-nav-landing-page`, `token-cost-tracking-dashboard`
**Files touched:** `api/pages.py`, `templates/base.html`, `templates/pages/admin.html`, `ai/usage.py`, `db/models.py`
**Why batch:** Both are admin-facing features sharing the `is_admin` auth gate.

### Session G: Season Arc
**Plans:** `season-lifecycle`, `season-memorial-system`, `season-transitions-and-carryover`
**Files touched:** `core/season.py`, `core/scheduler_runner.py`, `ai/report.py`, `discord/bot.py`, `core/memorial.py`
**Why batch:** All three plans modify the season state machine and its surrounding narrative systems.

### Session H: Documentation
**Plans:** `api-architecture-doc`, `tech-architecture-doc`, `governance-event-store-enumeration`, `elam-ending-mechanics`
**Files touched:** `docs/` only
**Why batch:** Pure documentation. Can be done in one focused session with no code risk.
