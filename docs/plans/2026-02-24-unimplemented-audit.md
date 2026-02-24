# Pinwheel Fates: Unimplemented Audit

**Date:** 2026-02-24
**Auditor:** Claude Opus 4.6 (automated codebase analysis)

## Summary

| Category | P0 | P1 | P2 | P3 | Total |
|----------|-----|-----|-----|-----|-------|
| 1. Code Stubs & Dead Code | 2 | 6 | 8 | 5 | 21 |
| 2. Plans Not Fully Executed | 0 | 3 | 5 | 2 | 10 |
| 3. Docs Describe But Code Missing | 3 | 8 | 7 | 4 | 22 |
| 4. RuleSet Parameters Not Used | 1 | 4 | 3 | 0 | 8 |
| **Total** | **6** | **21** | **23** | **11** | **61** |

---

## 1. Code Stubs and Dead Code

### P0 — Blocks Core Gameplay

**1.1 Moves "Lockdown Stance" and "Iron Will" have no effect in simulation**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/src/pinwheel/core/moves.py` lines 144-164
- **What's missing:** `apply_move_modifier()` only handles Heat Check, Ankle Breaker, Clutch Gene, Chess Move, No-Look Pass, and Wild Card. Lockdown Stance (defensive move: "+20% contest, -5% own stamina") and Iron Will (stamina move: "stamina floor at 0.35, reduce degradation by 50%") are defined but their effects are never applied. The function returns `base_probability` unchanged for any unknown move name, so these two moves trigger (consuming activation probability) but produce zero effect.
- **Severity:** P0 — These moves are assigned to Lockdown and Iron Horse archetypes. Players who strategize around these abilities get nothing.
- **Effort:** Small (1-2 hours) — Add branches in `apply_move_modifier()` and, for Lockdown Stance specifically, hook into the defensive contest modifier path in `possession.py`.

**1.2 Venue/home court mechanics are completely unimplemented in simulation**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/src/pinwheel/core/simulation.py` — entire file
- **What's missing:** The simulation engine (`simulate_game()`) never references `home_venue`, `away_venue`, crowd boost, altitude penalty, travel fatigue, or any venue modifier. The `Team` model has a `Venue` field, but `simulate_game` receives `Team` objects and ignores their `.venue` attribute entirely. There is no `compute_venue_modifiers()` function anywhere in the codebase despite being described in SIMULATION.md. Seven RuleSet parameters (`home_court_enabled`, `home_crowd_boost`, `away_fatigue_factor`, `crowd_pressure`, `altitude_stamina_penalty`, `travel_fatigue_enabled`, `travel_fatigue_per_mile`) exist solely for governance display but have zero simulation effect.
- **Severity:** P0 — Home/away distinction is cosmetic. Acceptance criteria 1.5.2 ("home team receives measurable advantage") is not met.
- **Effort:** Medium (half day) — Implement `compute_venue_modifiers()`, wire into `simulate_game()` pre-possession loop. Apply crowd boost to shooting probability, altitude penalty to away stamina, travel fatigue at game start.

### P1 — Important Feature Gap

**1.3 Deprecated config: `pinwheel_gov_window` kept but never consumed**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/src/pinwheel/config.py` line 101
- **What's missing:** `pinwheel_gov_window: int = 900` is explicitly marked "DEPRECATED -- unused, kept for backward compat." No code reads this value. It's dead weight in the config.
- **Severity:** P1 — Confusing for anyone reading the config. Could mislead governors into thinking governance windows are time-limited.
- **Effort:** Trivial (< 30 min) — Remove the field.

**1.4 Discord `/rules` command listed in RUN_OF_PLAY.md but not implemented**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/docs/product/RUN_OF_PLAY.md` line 154, `/Users/djacobs/Documents/GitHub/Pinwheel/src/pinwheel/discord/bot.py`
- **What's missing:** RUN_OF_PLAY lists `/rules` as "View current ruleset" in the Discord Commands table. No such slash command is registered in `bot.py`. Governors must visit the web UI to see current rules.
- **Severity:** P1 — Governors need to see the current ruleset from Discord without context-switching to the web.
- **Effort:** Small (1-2 hours) — Register the command, fetch current ruleset from repo, format as embed.

**1.5 `three_point_distance` RuleSet parameter has no simulation effect**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/src/pinwheel/models/rules.py` line 30
- **What's missing:** `three_point_distance: float = Field(default=22.15, ge=15.0, le=30.0)` is defined and governable, but nowhere in `possession.py`, `scoring.py`, `simulation.py`, `moves.py`, or `defense.py` is this value read. Shot selection uses fixed probability curves that don't reference the arc distance.
- **Severity:** P1 — Governance theater. Players can vote to change the three-point distance and it does nothing.
- **Effort:** Small (1-2 hours) — Map distance to a probability modifier on three-point attempts in `scoring.py`.

**1.6 `Fate` attribute is completely dormant**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/src/pinwheel/core/simulation.py`, `/Users/djacobs/Documents/GitHub/Pinwheel/docs/SIMULATION.md` lines 37, 51-53
- **What's missing:** SIMULATION.md describes Fate as "In rare occasions, players will get to author their own attributes, for players, games, teams or seasons." The `fate` attribute is part of `PlayerAttributes` and Oracle archetype has it at 80. No code anywhere reads `hooper.attributes.fate` during simulation. No Fate events, no `fate_trigger`, no mid-simulation modification of parameters. The implementation note says "Fates are post-Day-1" but the attribute occupies budget points (deducting from useful stats) with zero gameplay effect.
- **Severity:** P1 — Oracle archetype (360 budget, 80 points in Fate) is objectively the weakest hooper since those 80 points are wasted.
- **Effort:** Large (full day+) — Design and implement Fate event system: trigger probability scaled by attribute, AI-generated event text, parameter modifications within the simulation.

**1.7 `surface` modifier described in SIMULATION.md not implemented**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/docs/SIMULATION.md` lines 135, 156
- **What's missing:** SIMULATION.md describes: "If a governance vote changes a venue's surface (grass, sand, ice?), Speed and Drive actions are modified. The simulation checks venue.surface against a surface effects table." The `Venue` model has a `surface: str = "hardwood"` field, but no code reads it. No surface effects table exists.
- **Severity:** P1 — One of the most creative governance surfaces described in the design is missing.
- **Effort:** Medium (half day) — Create a surface effects mapping, apply modifiers to speed/drive actions in possession resolution.

**1.8 No request timing middleware**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/src/pinwheel/main.py`
- **What's missing:** INSTRUMENTATION.md requires "Middleware timer: FastAPI middleware that logs request path, method, duration, and status code for every request." No such middleware exists in `main.py`. The word "middleware" does not appear anywhere in the main application file. Individual endpoints log timing in some places, but there is no systematic middleware.
- **Severity:** P1 — Cannot measure API performance systematically. Acceptance criteria 5.3.1 not met.
- **Effort:** Small (1-2 hours) — Add a simple timing middleware that logs method, path, duration, status.

### P2 — Nice to Have

**1.9 `proposals_per_window` is only enforced in Discord, not in the API/governance layer**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/src/pinwheel/discord/bot.py` lines 2187-2211, `/Users/djacobs/Documents/GitHub/Pinwheel/src/pinwheel/core/governance.py`
- **What's missing:** The `proposals_per_window` limit is checked in `bot.py`'s `/propose` command handler, but `governance.py`'s `submit_proposal()` function does not enforce it. Anyone calling the API directly could bypass this limit.
- **Severity:** P2 — Discord is the only proposal entry point currently, so this isn't exploitable in practice.
- **Effort:** Trivial (< 30 min) — Add the check in `submit_proposal()`.

**1.10 Trade rejection never records an event**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/src/pinwheel/core/tokens.py`
- **What's missing:** `offer_trade()` records `trade.offered`, `accept_trade()` records `trade.accepted`, but there is no `reject_trade()` function that records `trade.rejected`. The Discord bot handles rejection UI but does not persist a rejection event to the governance event store.
- **Severity:** P2 — Trade rejection data is invisible to reports and evals. The AI can't analyze rejection patterns.
- **Effort:** Trivial (< 30 min) — Add `reject_trade()` that appends a `trade.rejected` event.

**1.11 `pinwheel_evals_enabled` config is read but evals have no disable path**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/src/pinwheel/config.py` line 116
- **What's missing:** `pinwheel_evals_enabled: bool = True` is defined but grep shows the flag is only partially checked. Some eval paths run regardless of this setting.
- **Severity:** P2 — Minor. Evals running when disabled wastes compute but doesn't break anything.
- **Effort:** Trivial (< 30 min) — Audit all eval call sites and gate on the flag.

**1.12 No simulation profiling mode**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/src/pinwheel/core/simulation.py`
- **What's missing:** INSTRUMENTATION.md specifies "The simulation engine has an optional `profile=True` flag that records time-per-possession, time-per-decision-node, and total game time." No such flag exists. Only `duration_ms` on the GameResult is tracked.
- **Severity:** P2 — Only matters for performance optimization.
- **Effort:** Small (1-2 hours) — Add optional per-possession timing collection.

**1.13 No SSE catch-up/reconnect payload**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/src/pinwheel/api/events.py`
- **What's missing:** GAME_LOOP.md specifies: "Clients that disconnect and reconnect get a catch-up payload of events since their last received event ID." The SSE endpoint has no `Last-Event-Id` handling, no event ID assignment, and no replay buffer.
- **Severity:** P2 — Clients that briefly disconnect during a live game miss events with no recovery.
- **Effort:** Medium (half day) — Add event IDs, maintain a rolling buffer, handle `Last-Event-Id` header.

**1.14 `State of the League` report is not generated periodically**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/src/pinwheel/core/game_loop.py`
- **What's missing:** GAME_LOOP.md specifies "A State of the League report is generated every 7 rounds (1 round-robin)." The `state_of_the_league` report type exists in `models/report.py` and the embed builder exists in `discord/embeds.py`, but no code in `game_loop.py` or `scheduler_runner.py` triggers periodic generation of this report type. It's only used for onboarding (`/join` and `/status`).
- **Severity:** P2 — Acceptance criteria 3.4.1 not met. Players don't get periodic league-wide narrative reports.
- **Effort:** Medium (half day) — Add a check in `step_round()` for `round_number % 7 == 0` to generate and store this report.

**1.15 Plus-minus calculation is simplified/incorrect**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/src/pinwheel/core/simulation.py` lines 317-321
- **What's missing:** `_build_box_scores()` computes plus-minus as the final score differential regardless of when the player was on/off the court. The comment says "simplified: team score diff while on court" but the implementation is just final game score diff, not tracking when each player was on the court.
- **Severity:** P2 — Misleading stat. Box score shows the same +/- for all starters on a team.
- **Effort:** Medium (half day) — Track per-player score accumulator while on_court in `HooperState`.

**1.16 `pinwheel_quarter_replay_seconds` and `pinwheel_game_interval_seconds` defaults don't match docs**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/src/pinwheel/config.py` lines 104-105
- **What's missing:** `pinwheel_quarter_replay_seconds: int = 300` (5 min) and `pinwheel_game_interval_seconds: int = 1800` (30 min) are used in the presenter, but the GAME_LOOP.md describes "20-30 minutes" for full game presentation. With 4 quarters at 5 min each, that's 20 min per game, plus 30 min gaps between games in the same round, meaning a 2-game round would take 80 minutes. This seems too slow for production.
- **Severity:** P2 — Configuration mismatch, not a bug per se.
- **Effort:** Trivial (< 30 min) — Review and adjust defaults or update docs.

### P3 — Cosmetic/Cleanup

**1.17 Many mock fallbacks could be eliminated**
- **File:** Multiple files in `ai/` directory
- **What's missing:** Every AI function (reports, commentary, interpreter, search) has a parallel `*_mock()` function. These are useful for development without API keys but add significant code surface area. No cleanup plan to consolidate or remove once production is stable.
- **Severity:** P3 — Code maintenance burden, not a bug.
- **Effort:** Small (1-2 hours) — Not urgent. Consider consolidating mock logic into a single module.

**1.18 Legacy `GameEffect` protocol in hooks.py is dead code**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/src/pinwheel/core/hooks.py` lines 34-73
- **What's missing:** The legacy hook system (`HookPoint` enum, `GameEffect` protocol, `fire_hooks()`) is preserved for backward compatibility but no code creates `GameEffect` instances. All effects use the new `RegisteredEffect` system. The legacy hooks fire but with empty effect lists.
- **Severity:** P3 — Dead code. Comment says "do not remove" but nothing depends on it.
- **Effort:** Trivial (< 30 min) — Remove after confirming no external callers.

**1.19 `custom_mechanic` effect type returns a placeholder**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/src/pinwheel/core/hooks.py` lines 395-397
- **What's missing:** `RegisteredEffect.apply()` handles `effect_type == "custom_mechanic"` by returning `result.narrative = f"[Pending mechanic] {self.description}"`. This is a placeholder that leaks "[Pending mechanic]" into game narratives.
- **Severity:** P3 — Cosmetic. Visible to players only if a wild proposal creates a custom_mechanic effect.
- **Effort:** Trivial (< 30 min) — Either implement or remove the prefix tag.

**1.20 `pinwheel_gov_window` should be removed from config**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/src/pinwheel/config.py` line 101
- **What's missing:** Already noted in 1.3 above.
- **Severity:** P3 — Duplicate of 1.3.
- **Effort:** Trivial.

**1.21 `governance_rounds_interval` mentioned in GAME_LOOP.md as a governable Tier 4 parameter but it's not in RuleSet**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/docs/GAME_LOOP.md` line 53, `/Users/djacobs/Documents/GitHub/Pinwheel/src/pinwheel/models/rules.py`
- **What's missing:** GAME_LOOP.md says "The governance interval is itself a governable parameter (`governance_rounds_interval` in Tier 4) — players can vote to make tallying more or less frequent." This parameter does not exist in `RuleSet`. Governance interval is only configurable via the `PINWHEEL_GOVERNANCE_INTERVAL` env var.
- **Severity:** P3 — The doc claims governance interval is governable but it isn't.
- **Effort:** Small (1-2 hours) — Add to RuleSet and wire into `step_round()`.

---

## 2. Plans That Haven't Been Fully Executed

### P1

**2.1 Spectator Journey plan has no implementation**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/docs/plans/2026-02-14-spectator-journey-and-team-following.md`
- **What's missing:** Plan describes team following (persistent preference), personalized feeds, and spectator notifications. None of this exists. There's no "follow a team" concept in the codebase.
- **Severity:** P1 — Non-governor users have no personalized experience.
- **Effort:** Large (full day+).

**2.2 Dramatic Pacing Modulation plan not implemented**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/docs/plans/2026-02-14-dramatic-pacing-modulation.md`
- **What's missing:** Plan describes the presenter modulating pacing based on game drama — slowing down during close finishes, speeding up during blowouts. The presenter (`core/presenter.py`) uses fixed timing regardless of game state.
- **Severity:** P1 — All games feel the same dramatic pace regardless of their actual drama level.
- **Effort:** Medium (half day).

**2.3 Token Cost Dashboard not implemented**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/docs/INSTRUMENTATION.md` lines 210-216, `/Users/djacobs/Documents/GitHub/Pinwheel/docs/plans/2026-02-14-token-cost-tracking-dashboard.md`
- **What's missing:** INSTRUMENTATION.md explicitly says "Status: Not yet implemented." The `ai/usage.py` module tracks costs per API call, but there's no dashboard to view the data. No `/admin/costs` web page exists that aggregates AI spending by call type, day, or report type. (There is an `api/admin_costs.py` file, but it may be minimal.)
- **Severity:** P1 — Cannot monitor AI spending without querying the database directly.
- **Effort:** Medium (half day).

### P2

**2.4 Proposal Amendment flow: amendments exist but have no Discord UI**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/src/pinwheel/core/governance.py` lines 520-578
- **What's missing:** `amend_proposal()` is fully implemented in the governance module, but no Discord slash command exists for `/amend`. AMEND tokens are granted and tracked but cannot be spent via Discord. The amendment flow described in Acceptance Criteria 2.3.8-2.3.11 has backend support but no user-facing interface.
- **Severity:** P2 — AMEND tokens are useless to players. Not blocking because proposals work without amendments.
- **Effort:** Medium (half day) — Register `/amend` command, add amendment UI flow.

**2.5 Tiebreaker extra governance round not implemented**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/docs/GAME_LOOP.md` lines 265-266
- **What's missing:** GAME_LOOP.md describes: "Extra governance round before the tiebreaker game. Players get one more window to adjust rules." `check_and_handle_tiebreakers()` in `season.py` transitions to TIEBREAKERS phase and generates tiebreaker games, but does not trigger an extra governance tally before those games.
- **Severity:** P2 — Tiebreaker games play under existing rules without the dramatic extra governance window described in the design.
- **Effort:** Small (1-2 hours).

**2.6 Offseason governance limited to timer expiration**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/src/pinwheel/core/scheduler_runner.py` lines 452-480
- **What's missing:** GAME_LOOP.md describes offseason as a "constitutional convention between seasons" with ruleset carry-forward votes, roster changes, new agents, and season parameter votes. The implementation is a simple timer (`pinwheel_offseason_window = 3600`) that expires and transitions to COMPLETE. Governors can submit proposals during this window, but there's no special offseason UI, no carry-forward vote mechanism, and no roster expansion/retirement governance.
- **Severity:** P2 — Offseason is functional but thin. The "constitutional convention" vision is unrealized.
- **Effort:** Large (full day+).

**2.7 Cross-reference and efficiencies plan partially implemented**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/docs/plans/2026-02-14-cross-reference-and-efficiencies.md`
- **What's missing:** Plan describes linking game outcomes to specific rule changes in reports (e.g., "since proposal X passed, Team Y's scoring dropped 15%"). While reports reference rule changes broadly, there's no systematic before/after statistical comparison per rule change.
- **Severity:** P2 — Reports would be much more compelling with causal attribution.
- **Effort:** Medium (half day).

**2.8 Accessibility and navigation contrast plan not completed**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/docs/plans/2026-02-17-accessibility-navigation-contrast.md`
- **What's missing:** Plan describes WCAG AA contrast fixes, keyboard navigation, ARIA labels, and screen reader support. Some contrast improvements were made but systematic accessibility audit items remain unchecked.
- **Severity:** P2 — Accessibility issues affect some users.
- **Effort:** Medium (half day).

### P3

**2.9 Light/dark mode toggle planned but not fully implemented**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/docs/plans/2026-02-16-light-dark-mode-toggle.md`
- **What's missing:** Plan exists for a persistent theme toggle. Current UI has a single theme.
- **Severity:** P3 — Cosmetic preference.
- **Effort:** Medium (half day).

**2.10 Documentation consolidation plan items remain unchecked**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/docs/plans/2026-02-16-documentation-consolidation.md`
- **What's missing:** Plan to consolidate overlapping docs, remove stale TODOs, and update design docs to match implementation. Many docs still describe the system as designed rather than as built.
- **Severity:** P3 — Docs drift, not a code issue.
- **Effort:** Medium (half day).

---

## 3. Features Described in Docs But Missing from Code

### P0

**3.1 SIMULATION.md: No `compute_venue_modifiers()` function exists**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/docs/SIMULATION.md` lines 128-143
- **What's missing:** SIMULATION.md describes a `compute_venue_modifiers()` function that computes crowd boost, crowd pressure, altitude penalty, travel fatigue, and surface modifiers before each possession. This function does not exist anywhere in the codebase. (Duplicate of finding 1.2 above, listed here for cross-reference.)
- **Severity:** P0 — Same as 1.2.
- **Effort:** Medium (half day).

**3.2 SIMULATION.md: No Ego × crowd pressure interaction**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/docs/SIMULATION.md` lines 132-133
- **What's missing:** "Crowd pressure — Ego checks are modified: home players get a boost (crowd fuels confidence), away players get a penalty (crowd rattles them). High-Ego agents resist crowd pressure; low-Ego agents are more affected." No Ego × crowd interaction exists in the simulation. Ego only affects shot selection confidence and the Heat Check/Clutch Gene moves.
- **Severity:** P0 — Documented mechanic completely absent.
- **Effort:** Small (1-2 hours) — Part of venue modifier implementation.

**3.3 No `attribute_budget` validation on `PlayerAttributes`**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/docs/product/ACCEPTANCE_CRITERIA.md` criterion 1.2.2, `/Users/djacobs/Documents/GitHub/Pinwheel/src/pinwheel/models/team.py`
- **What's missing:** Acceptance criteria 1.2.2 requires: "PlayerAttributes rejects any attribute set that does not sum to the season's `attribute_budget` (default 360, +/-10 variance per attribute)." The `PlayerAttributes` model has no validator that checks `total() == 360`. The `total()` method exists but is never called for validation.
- **Severity:** P0 — Agents could be seeded with any total points. Governance could create unbalanced hoopers.
- **Effort:** Small (1-2 hours) — Add a model_validator.

### P1

**3.4 INSTRUMENTATION.md: Player behavior events not captured**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/docs/INSTRUMENTATION.md` lines 13-26
- **What's missing:** INSTRUMENTATION.md specifies 12 player behavior event types: `governance.proposal.abandon`, `governance.vote.skip`, `report.private.view`, `report.private.dismiss`, `game.result.view`, `feed.scroll_depth`, `session.start`, `session.end`. None of these are emitted anywhere in the codebase. The governance event store captures proposal/vote/trade actions, but not the engagement signals (views, reads, abandons, scrolls, sessions).
- **Severity:** P1 — Cannot compute joy metrics (report read rate, return rate, time-to-first-action, scroll depth).
- **Effort:** Medium (half day) — Add event emission in page handlers, SSE endpoint, and Discord bot.

**3.5 INSTRUMENTATION.md: Joy alarms not implemented**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/docs/INSTRUMENTATION.md` lines 42-49
- **What's missing:** Five joy alarm conditions are described (disengagement, political exclusion, economy stalling, reports not resonating, power concentration). None are implemented. The `evals/flags.py` module handles some game-state flags (dominant strategies, degenerate equilibria) but not the player engagement alarms.
- **Severity:** P1 — No early warning system for engagement problems.
- **Effort:** Medium (half day).

**3.6 INSTRUMENTATION.md: Token cost dashboard not built**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/docs/INSTRUMENTATION.md` lines 208-216
- **What's missing:** Explicitly marked "Status: Not yet implemented." Daily/weekly spend charts, cost per player, tokens per report distribution, cache hit rates. (Duplicate of 2.3.)
- **Severity:** P1 — Same as 2.3.
- **Effort:** Medium (half day).

**3.7 INSTRUMENTATION.md: Performance dashboard `/admin/perf` not built**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/docs/INSTRUMENTATION.md` line 86, `/Users/djacobs/Documents/GitHub/Pinwheel/docs/product/ACCEPTANCE_CRITERIA.md` criteria 5.3.4-5.3.5
- **What's missing:** INSTRUMENTATION.md notes: "The original perf metrics (latencies, throughput, connection pools) are not yet surfaced in a dashboard." There is no `/admin/perf` route. Acceptance criteria 5.3.4 ("P50/P95/P99 latencies") and 5.3.5 ("Token cost tracking shows daily spend") are not met.
- **Severity:** P1 — Cannot monitor system health via UI.
- **Effort:** Medium (half day).

**3.8 GAME_LOOP.md: Tiebreaker report not generated**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/docs/GAME_LOOP.md` line 64
- **What's missing:** GAME_LOOP.md describes a "Tiebreaker report (shared): The extra governance round before a tiebreaker — what did players change and why?" The `tiebreaker` report type is defined in `models/report.py` but no code generates it.
- **Severity:** P1 — Tiebreaker games happen without narrative AI coverage.
- **Effort:** Small (1-2 hours).

**3.9 GAME_LOOP.md: Offseason report not generated**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/docs/GAME_LOOP.md` line 67
- **What's missing:** GAME_LOOP.md describes an "Offseason report (shared): What carried forward, what was reset, and what that says about the community." No code generates an offseason-specific report.
- **Severity:** P1 — Season transitions happen without narrative coverage of the governance decisions made.
- **Effort:** Small (1-2 hours).

**3.10 SECURITY.md: Several implementation checklist items unchecked**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/docs/SECURITY.md` lines 288-299
- **What's missing:** The following items remain unchecked:
  - [ ] Input sanitization function (partially done — `sanitize_text()` exists but `remove_invisible_chars` and `strip_prompt_markers` are not separate functions as described)
  - [ ] Red team exercise against the interpreter before launch
  - [ ] Anomaly alerting on high rejection rates or unusual patterns
  - [ ] Strategy instruction validation through the same interpreter pipeline (strategy uses a separate `interpret_strategy()` call, not the same pipeline)
- **Severity:** P1 — Security hygiene gaps.
- **Effort:** Small to medium depending on item.

**3.11 ACCEPTANCE_CRITERIA: Private reports not access-controlled**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/docs/product/ACCEPTANCE_CRITERIA.md` criterion 3.3.3
- **What's missing:** Criterion 3.3.3: "Private reports are only visible to the intended governor -- no other governor can access them via API." The web reports page serves all reports including private ones. While private reports are delivered via Discord DM, the web API does not enforce per-governor access control on private report content (it requires auth but any authenticated user can see report listings).
- **Severity:** P1 — Privacy concern. Private reports are meant to be private.
- **Effort:** Small (1-2 hours) — Filter private reports by authenticated governor ID.

### P2

**3.12 SIMULATION.md: No Scoring × IQ shot selection quality interaction**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/docs/SIMULATION.md` lines 45-46
- **What's missing:** "Scoring × IQ = shot selection quality. A high scorer with low IQ takes (and sometimes makes) bad shots." IQ affects shot selection through the move system (Chess Move) but there's no direct Scoring × IQ interaction in the shot selection probability curves.
- **Severity:** P2 — Simulation is less nuanced than the design intends.
- **Effort:** Small (1-2 hours).

**3.13 SIMULATION.md: No Passing × Speed = fast break generation**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/docs/SIMULATION.md` line 47
- **What's missing:** "Passing × Speed = fast break generation. Teams with both create easy transition baskets." No fast-break mechanic exists. Possessions are resolved independently without transition context.
- **Severity:** P2 — Missing a described simulation dynamic.
- **Effort:** Small (1-2 hours).

**3.14 SIMULATION.md: No Chaotic Alignment × everything = variance amplifier**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/docs/SIMULATION.md` lines 50-51
- **What's missing:** "High-chaos players widen the probability distribution on every action they're involved in. Stacks multiplicatively — two high-chaos players on the floor is exponentially more chaotic." Chaotic alignment is used by the Wild Card move (random +25%/-15%) but there's no general variance amplification. Other shot types are not affected by chaotic alignment.
- **Severity:** P2 — Wildcard archetype is less impactful than designed.
- **Effort:** Small (1-2 hours) — Add variance scaling based on chaotic_alignment to possession resolution.

**3.15 GAME_LOOP.md: Seed formula not using `ruleset_hash`**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/docs/GAME_LOOP.md` lines 215-219
- **What's missing:** GAME_LOOP.md specifies `seed = hash(season_id, round_number, matchup_index, ruleset_hash)`. The actual seed generation in `game_loop.py` uses a different formula. There's no `ruleset_hash` component, which means the "same matchup under different rules produces a different game" property is not guaranteed.
- **Severity:** P2 — Reduces auditability. "Re-run game under different ruleset" won't produce different results if the seed is the same.
- **Effort:** Trivial (< 30 min) — Add ruleset hash to seed computation.

**3.16 No database query slow-query logging**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/docs/INSTRUMENTATION.md` line 82
- **What's missing:** "Log slow queries (>100ms) with the full query and execution plan." No SQLAlchemy event listener for slow queries exists.
- **Severity:** P2 — Performance debugging tool missing.
- **Effort:** Small (1-2 hours).

**3.17 ACCEPTANCE_CRITERIA: Automatic rule rollback not implemented**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/docs/product/ACCEPTANCE_CRITERIA.md` criteria 2.5.1-2.5.3
- **What's missing:** "If an enacted rule causes a simulation error, the rule is automatically rolled back." No rollback mechanism exists. If a rule change causes a simulation error, the error is logged and the game is skipped, but the invalid rule persists.
- **Severity:** P2 — Could leave the league stuck with broken rules until a manual fix.
- **Effort:** Medium (half day).

**3.18 ACCEPTANCE_CRITERIA: Effect chain depth limit not enforced**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/docs/product/ACCEPTANCE_CRITERIA.md` criterion 2.5.5
- **What's missing:** "Effect chain depth is limited to 3 levels; excess effects are suppressed." SECURITY.md also mentions this. No code checks chain depth when effects trigger other effects. The `conditional_sequence` action type in hooks.py can recurse but has no depth counter.
- **Severity:** P2 — Potential for infinite loops in effect chains.
- **Effort:** Small (1-2 hours).

### P3

**3.19 CLAUDE.md: Discord command table includes `/effects` and `/repeal` but GAME_LOOP.md doesn't mention them**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/CLAUDE.md`
- **What's missing:** Minor documentation inconsistency. `/effects` and `/repeal` are implemented in the Discord bot but not documented in GAME_LOOP.md or RUN_OF_PLAY.md.
- **Severity:** P3 — Docs drift.
- **Effort:** Trivial (< 30 min).

**3.20 GAME_LOOP.md: "State of the League" report described as every 7 rounds**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/docs/GAME_LOOP.md` line 68
- **What's missing:** Duplicate of finding 1.14 above. Listed for cross-reference.
- **Severity:** P3 — Report type exists but isn't auto-generated on schedule.
- **Effort:** Same as 1.14.

**3.21 ACCEPTANCE_CRITERIA: OpenAPI docs not verified**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/docs/product/ACCEPTANCE_CRITERIA.md` criterion 1.8.6
- **What's missing:** "OpenAPI docs are accessible at `/docs`." FastAPI provides this by default, so it likely works, but there's no test verifying it.
- **Severity:** P3 — Low risk since FastAPI auto-generates.
- **Effort:** Trivial (< 30 min).

**3.22 ACCEPTANCE_CRITERIA: WebSocket health metrics not tracked**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/docs/INSTRUMENTATION.md` line 81
- **What's missing:** "Track connection count, message throughput, failed deliveries, and reconnection rate." SSE connections are limited by a semaphore (`_MAX_SSE_CONNECTIONS = 100`) but no metrics are collected about connection count, throughput, or failures.
- **Severity:** P3 — Operational visibility gap.
- **Effort:** Small (1-2 hours).

---

## 4. RuleSet Parameters Defined But Not Used in Simulation

Each parameter in `models/rules.py` is categorized by whether simulation code actually reads and uses it.

### Parameters USED by simulation code:

| Parameter | Used In | How |
|-----------|---------|-----|
| `quarter_minutes` | `simulation.py:190` | Sets game clock per quarter |
| `shot_clock_seconds` | `possession.py:256` | Determines play time per possession |
| `three_point_value` | `scoring.py:84` | Points awarded for made three |
| `two_point_value` | `scoring.py:86` | Points awarded for made two |
| `free_throw_value` | `scoring.py:87` | Points awarded for free throw |
| `personal_foul_limit` | `possession.py:530` | Foul-out threshold |
| `elam_trigger_quarter` | `simulation.py:390` | When Elam Ending starts |
| `elam_margin` | `simulation.py:254` | Elam target = leader + margin |
| `halftime_stamina_recovery` | `simulation.py:300` | Stamina recovery at half |
| `quarter_break_stamina_recovery` | `simulation.py:308` | Recovery between quarters |
| `safety_cap_possessions` | `simulation.py:238,289` | Maximum possessions before force-end |
| `substitution_stamina_threshold` | `simulation.py:154` | When fatigue subs trigger |
| `turnover_rate_modifier` | `possession.py:117` | Scales base turnover probability |
| `foul_rate_modifier` | `possession.py:147` | Scales base foul probability |
| `offensive_rebound_weight` | `possession.py:173` | Offensive rebound probability |
| `stamina_drain_rate` | `possession.py:235` | Base stamina drain per possession |
| `dead_ball_time_seconds` | `possession.py:257` | Time between possessions |
| `playoff_teams` | `game_loop.py`, `pages.py` | Number qualifying for playoffs |
| `playoff_semis_best_of` | `game_loop.py`, `narrative.py` | Semifinal series length |
| `playoff_finals_best_of` | `game_loop.py`, `narrative.py` | Finals series length |
| `proposals_per_window` | `bot.py:2209` | Proposal limit per governor (Discord only) |
| `vote_threshold` | `governance.py:720,815` | Votes needed to pass |
| `round_robins_per_season` | `season.py:1075`, `scheduler.py` | Schedule generation |
| `teams_count` | `narrative.py:117` | Display only |

### Parameters NOT USED by simulation code (governance theater):

**4.1 `three_point_distance` — P1**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/src/pinwheel/models/rules.py` line 30
- **What's missing:** Defined as `three_point_distance: float = Field(default=22.15, ge=15.0, le=30.0)`. Never read by simulation. Governors can vote to change the arc distance and nothing changes.
- **Severity:** P1 — Active governance deception.
- **Effort:** Small (1-2 hours) — Map to a three-point attempt probability modifier.

**4.2 `team_foul_bonus_threshold` — P1**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/src/pinwheel/models/rules.py` line 29
- **What's missing:** Defined as `team_foul_bonus_threshold: int = Field(default=4, ge=3, le=10)`. Never referenced in possession.py or simulation.py. Team foul bonus (free throws when team is in the penalty) is not implemented.
- **Severity:** P1 — Basketball mechanic missing. Fouls are tracked per player but team fouls per quarter are not accumulated.
- **Effort:** Small (1-2 hours) — Track team fouls per quarter, grant free throws when threshold exceeded.

**4.3 `max_shot_share` — P1**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/src/pinwheel/models/rules.py` line 44
- **What's missing:** Defined as `max_shot_share: float = Field(default=1.0, ge=0.2, le=1.0)`. Never read during simulation. A dominant scorer takes as many shots as the action selection gives them, regardless of this setting.
- **Severity:** P1 — Interesting governance lever (force ball movement) with zero effect.
- **Effort:** Small (1-2 hours) — Track shots per player during game, cap when ratio exceeds `max_shot_share`.

**4.4 `min_pass_per_possession` — P1**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/src/pinwheel/models/rules.py` line 45
- **What's missing:** Defined as `min_pass_per_possession: int = Field(default=0, ge=0, le=5)`. Never referenced in possession.py. Setting this to 3 should force 3 passes before any shot attempt. Currently, action selection picks shoot/pass/drive without any pass-count requirement.
- **Severity:** P1 — Same as above. Creative governance option with no effect.
- **Effort:** Small (1-2 hours) — Track pass count per possession, require passes before allowing shot action.

**4.5 `home_court_enabled` — P1 (duplicate of 1.2)**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/src/pinwheel/models/rules.py` line 46
- **What's missing:** Entire venue/home court system unimplemented. Already detailed in 1.2.
- **Severity:** P1.
- **Effort:** Part of 1.2.

**4.6 `home_crowd_boost` — P2 (part of venue system)**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/src/pinwheel/models/rules.py` line 47
- **What's missing:** Part of the unimplemented venue modifier system.
- **Severity:** P2.
- **Effort:** Part of 1.2.

**4.7 `away_fatigue_factor`, `crowd_pressure`, `altitude_stamina_penalty`, `travel_fatigue_enabled`, `travel_fatigue_per_mile` — P2 (all part of venue system)**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/src/pinwheel/models/rules.py` lines 48-52
- **What's missing:** All five are part of the unimplemented venue modifier system.
- **Severity:** P2.
- **Effort:** Part of 1.2.

**4.8 `teams_count` — P3**
- **File:** `/Users/djacobs/Documents/GitHub/Pinwheel/src/pinwheel/models/rules.py` line 55
- **What's missing:** `teams_count: int = Field(default=8, ge=4, le=16)` is used only in narrative display text ("League: 8 teams"). It doesn't control actual team count — teams are seeded and stored in the database independently. Changing this via governance would change display text but not add/remove teams.
- **Severity:** P3 — Misleading but low impact since adding/removing teams mid-season is complex.
- **Effort:** Trivial to note; large to make functional.

---

## Summary of Top Priorities

### P0 (Must Fix — Blocks Core Gameplay)
1. **Venue/home court mechanics completely absent** (1.2/3.1/3.2) — 7 RuleSet params, described as core to the game, do nothing
2. **Lockdown Stance + Iron Will moves have no effect** (1.1) — Two archetypes' signature moves are broken
3. **No attribute budget validation** (3.3) — Agents can have any total stat points

### Critical P1 Items
4. **4 RuleSet parameters are pure governance theater** (4.1-4.4) — `three_point_distance`, `team_foul_bonus_threshold`, `max_shot_share`, `min_pass_per_possession`
5. **Fate attribute completely dormant** (1.6) — Oracle archetype wastes 80 of 360 budget points
6. **No request timing middleware** (1.8) — Cannot measure system performance
7. **Player behavior events not captured** (3.4) — Cannot compute any joy/engagement metrics
8. **Private reports not access-controlled** (3.11) — Privacy violation
9. **Token cost / perf dashboards missing** (2.3/3.6/3.7) — No visibility into AI spending or system health
