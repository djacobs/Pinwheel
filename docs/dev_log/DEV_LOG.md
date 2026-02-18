# Pinwheel Dev Log — 2026-02-17

Previous logs: [DEV_LOG_2026-02-10.md](DEV_LOG_2026-02-10.md) (Sessions 1-5), [DEV_LOG_2026-02-11.md](DEV_LOG_2026-02-11.md) (Sessions 6-16), [DEV_LOG_2026-02-12.md](DEV_LOG_2026-02-12.md) (Sessions 17-33), [DEV_LOG_2026-02-13.md](DEV_LOG_2026-02-13.md) (Sessions 34-47), [DEV_LOG_2026-02-14.md](DEV_LOG_2026-02-14.md) (Sessions 48-70), [DEV_LOG_2026-02-15.md](DEV_LOG_2026-02-15.md) (Sessions 71-89), [DEV_LOG_2026-02-16.md](DEV_LOG_2026-02-16.md) (Sessions 90-106)

## Where We Are

- **2032 tests**, zero lint errors (Session 109)
- **Days 1-7 complete:** simulation engine, governance + AI interpretation, reports + game loop, web dashboard + Discord bot + OAuth + evals framework, APScheduler, presenter pacing, AI commentary, UX overhaul, security hardening, production fixes, player pages overhaul, simulation tuning, home page redesign, live arena, team colors, live zone polish
- **Day 8:** Discord notification timing, substitution fix, narration clarity, Elam display polish, SSE dedup, deploy-during-live resilience
- **Day 9:** The Floor rename, voting UX, admin veto, profiles, trades, seasons, doc updates, mirror->report rename
- **Day 10:** Production bugfixes — presentation mode, player enrollment, Discord invite URL
- **Day 11:** Discord defer/timeout fixes, get_active_season migration, playoff progression pipeline
- **Day 12:** P0 fixes — /join, score spoilers, strategy system, trade verification, substitution verification
- **Day 13:** Self-heal missing player enrollments, decouple governance from game simulation
- **Day 14:** Admin visibility, season lifecycle phases, effects system, NarrativeContext, game richness audit, SQLite write lock fix, playoff series, V2 interpreter, e2e verification, workbench
- **Day 15:** Overnight wave execution — amendments, repeal, milestones, drama pacing, effects wave 2, documentation, Discord guard, V2 tier detection, tick-based scheduling, SSE dedup, team links, playoff series banners
- **Day 16:** AI intelligence layer, Amplify Human Judgment (9 features), P0/P1 security hardening, doc reconciliation, Messages API phases 1-2, performance optimization, video demo pipeline
- **Day 17:** Repo cleanup, excluded demo PNGs from git, showboat image fix, deployed
- **Day 18:** Report prompt simplification, regen-report command, production report fix, report ordering fix
- **Day 19:** Resilient proposal pipeline — deferred interpreter, mock fallback detection, custom_mechanic activation
- **Live at:** https://pinwheel.fly.dev
- **Latest commit:** `7bdb7c5` — feat: resilient proposal pipeline — deferred interpreter, mock fallback, custom_mechanic activation
- **Day 20:** Smarter reporter — bedrock facts, playoff series context, prior-season memory, model switch to claude-sonnet-4-6

## Today's Agenda

- [x] Implement resilient proposal pipeline (deferred interpreter, mock fallback, custom_mechanic activation)
- [ ] Reset season history for hackathon demo
- [ ] Record demo video (3-minute hackathon submission)
- [ ] Final deploy and verification

---

## Session 107 — Resilient Proposal Pipeline

**What was asked:** Implement a 10-part plan to make every proposal get a real AI interpretation. Production had 100% interpreter timeout rate because `output_config` constrained decoding on a 28-field Pydantic schema caused API timeouts. The plan: drop `output_config`, queue failed interpretations for background retry, DM players when ready, make custom_mechanic always fire alongside concrete effects, add `/activate-mechanic` admin command.

**What was built:**
- **Dropped `output_config`** from all 5 interpreter call sites, added `_parse_json_response()` helper for strip-fences + JSON parsing, added Response Format section to system prompt
- **`is_mock_fallback` flag** on `ProposalInterpretation` — set True when falling back to keyword matcher
- **5 new event types** — `proposal.pending_interpretation`, `proposal.interpretation_ready`, `proposal.interpretation_expired`, `effect.activated`, `effect.implementation_requested`
- **Deferred interpreter** (`core/deferred_interpreter.py`) — background retry job on 60s tick: finds pending interpretations, retries each, DMs player on success, expires stale ones after 4 hours with token refund
- **Mock fallback detection in `/propose`** — queues `pending_interpretation` event, tells player "queued for retry", blocks governor from submitting another while pending
- **`/activate-mechanic` admin command** — autocomplete from pending custom_mechanic effects, upgrades to real hook_callback or confirms approximation
- **`ProposalConfirmView.on_timeout()`** — refunds PROPOSE token when DM'd interpretation times out
- **custom_mechanic fires at report hooks** — `mechanic_observable_behavior` becomes narrative that appears in commentary even before full implementation
- **Admin DM on custom_mechanic enactment** — sends implementation spec to admin when proposal passes
- **APScheduler job** — `tick_deferred_interpretations` registered unconditionally at 60s interval
- **14 new tests** covering deferred interpreter discovery, retry, expiry, mock fallback flag, JSON parsing

**Files modified (12):** `src/pinwheel/ai/interpreter.py`, `src/pinwheel/models/governance.py`, `src/pinwheel/core/deferred_interpreter.py` (new), `src/pinwheel/discord/bot.py`, `src/pinwheel/discord/views.py`, `src/pinwheel/core/effects.py`, `src/pinwheel/core/governance.py`, `src/pinwheel/main.py`, `tests/test_messages_api.py`, `tests/test_deferred_interpreter.py` (new), `tests/test_effects.py`, `docs/dev_log/DEV_LOG.md`

**1990 tests, zero lint errors.**

**What could have gone better:** The `output_config` constrained decoding was identified as the likely timeout cause back in Session 106 ("Response Format section was obviously redundant since Session 104 when `output_config` was added"), but it took 3 more sessions of production failures before the full fix was built. The deferred interpreter and custom_mechanic activation are architecturally clean, but the `/activate-mechanic` command won't be needed until players start submitting truly novel proposals — it's slightly ahead of current gameplay needs.

## Session 108 — Living Rules: Every Wild Proposal Fires Real Game Mechanics

**What was asked:** Implement the "Living Rules" plan — fix the severed wire between the effect system and the simulation engine where `shot_probability_modifier` was computed by effects but silently discarded, expand the action primitive vocabulary from 5 to 12 types, expand conditions from meta-field-only to 7 condition types, and wire effect-derived modifiers through `PossessionContext` into the possession engine.

**What was built:**
- **`PossessionContext` dataclass** (`state.py`) — ephemeral per-possession modifier carrier with 10 fields (shot_probability_modifier, shot_value_modifier, extra_stamina_drain, shot selection biases, turnover_modifier, random_ejection_probability, bonus_pass_count, narrative_tags)
- **Cross-possession tracking** on `GameState` — `last_action`, `last_result`, `consecutive_makes`, `consecutive_misses` updated at end of every possession for condition evaluation
- **Critical gap fix** — `_fire_sim_effects()` now returns `PossessionContext` (accumulated from `HookResult`s) instead of `list[HookResult]`; `_run_quarter` and `_run_elam` wire it through to `resolve_possession`
- **`resolve_possession` applies all modifiers** — shot_probability_modifier after compute_shot_probability, shot_value_modifier + bonus_pass_count to scoring, turnover_modifier to check_turnover, shot selection biases to select_action, extra_stamina_drain to ball handler, random_ejection_probability before possession start
- **7 new action primitives** — `modify_shot_value`, `modify_shot_selection`, `modify_turnover_rate`, `random_ejection`, `derive_pass_count` (team passing → pass count → shot value), `swap_roster_player` (extreme-stat temp player), `conditional_sequence` (compound actions with gates)
- **7 new condition types** — `game_state_check` (trailing/leading/elam_active), `quarter_gte`, `score_diff_gte`, `random_chance`, `last_result`, `consecutive_makes_gte`/`consecutive_misses_gte`, `ball_handler_attr`
- **Interpreter prompt update** — `INTERPRETER_V2_SYSTEM_PROMPT` now documents all 12 action primitives and 7 condition types
- **27 new tests** across 5 test classes: PossessionContext wiring, all 7 new primitives, all new condition types, cross-possession tracking, integration scenarios (round court reduces threes, hot ball causes ejections, shot value modifier adds points, turnover modifier changes frequency)

**Files modified (6):** `src/pinwheel/core/state.py`, `src/pinwheel/core/hooks.py`, `src/pinwheel/core/simulation.py`, `src/pinwheel/core/possession.py`, `src/pinwheel/ai/interpreter.py`, `tests/test_effects.py`

**2017 tests, zero lint errors.**

**What could have gone better:** The plan called for 5 phases but phases 2-4 collapsed naturally into phase 2 since all the new action primitives live in the same `_apply_action_code` method. The `swap_roster_player` implementation is a rough approximation (adds a player to the list rather than properly tracking lifetime/restoration) — the full vision with named crowd players and cross-quarter tracking would need a custom_mechanic. The `conditional_sequence` gate system only supports `random_chance` gates currently; other gate types (e.g., `previous_step_result`) are placeholders for future expansion.

## Session 109 — Smarter Reporter: Bedrock Facts + Season Memory + Model Switch

**What was asked:** Fix AI reporter hallucinations (inventing byes, confusing pre/post-game series records, general basketball knowledge filling Pinwheel-specific gaps) and switch all AI models to `claude-sonnet-4-6`.

**What was built:**
- **Bedrock facts** — `_build_bedrock_facts(ruleset)` generates ~8 lines of verified structural facts (team count, 3v3 format, "no byes," playoff series format, Elam trigger, scoring values, quarter/shot clock) from the current RuleSet. Emitted at the top of every AI prompt as `=== LEAGUE FACTS (do not contradict) ===`
- **Head-to-head phase filtering** — `_compute_head_to_head()` gains a `phase_filter` param. During playoffs, playoff-only series records are computed separately into `ctx.playoff_series` with home/away wins, best-of, wins-needed, and a human-readable description
- **Prior season memory** — Queries `SeasonArchiveRow` (at most 3, newest first) and extracts season name, champion, game/rule counts, governance legacy excerpt, and notable rules. Emitted as "League history:" section in prompts
- **Smarter prompt formatting** — Standings labeled as "Regular-season standings (for seeding reference)" during playoffs; h2h labeled "Season head-to-head (all games this season)"; playoff series separate section
- **AI prompt constraints** — Added ground rules to simulation report, commentary, and highlight reel prompts: never contradict LEAGUE FACTS, use PLAYOFF SERIES during playoffs, don't invent concepts not in the data
- **Model switch** — All 13 AI call sites switched from `claude-opus-4-6`/`claude-sonnet-4-5-20250929` to `claude-sonnet-4-6`. Added `claude-sonnet-4-6` pricing entry in `usage.py`
- **`game_loop.py`** — passes `ruleset=ruleset` to `compute_narrative_context()`
- **15 new tests** — bedrock facts (default + custom ruleset), h2h phase filter (3 cases), format output verification (bedrock at top, playoff series, labeled standings/h2h, prior seasons, all fields together), integration tests (with/without ruleset), pricing dict

**Files modified (10):** `src/pinwheel/ai/usage.py`, `src/pinwheel/core/narrative.py`, `src/pinwheel/core/game_loop.py`, `src/pinwheel/ai/report.py`, `src/pinwheel/ai/commentary.py`, `src/pinwheel/ai/interpreter.py`, `src/pinwheel/ai/search.py`, `src/pinwheel/ai/mirror.py`, `tests/test_narrative.py`, `tests/test_ai_costs.py`

**2032 tests, zero lint errors.**

**What could have gone better:** The prior-season memory feature depends on `SeasonArchiveRow` data existing in the database. In production, the first season hasn't been archived yet, so this feature won't produce output until a season is completed and archived. The bedrock facts and playoff series context are immediately valuable — they'll prevent the hallucinations we saw in production reports during the current season's playoffs.
