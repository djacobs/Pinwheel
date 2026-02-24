# Pinwheel Dev Log — 2026-02-24

Previous logs: [DEV_LOG_2026-02-10.md](DEV_LOG_2026-02-10.md) (Sessions 1-5), [DEV_LOG_2026-02-11.md](DEV_LOG_2026-02-11.md) (Sessions 6-16), [DEV_LOG_2026-02-12.md](DEV_LOG_2026-02-12.md) (Sessions 17-33), [DEV_LOG_2026-02-13.md](DEV_LOG_2026-02-13.md) (Sessions 34-47), [DEV_LOG_2026-02-14.md](DEV_LOG_2026-02-14.md) (Sessions 48-70), [DEV_LOG_2026-02-15.md](DEV_LOG_2026-02-15.md) (Sessions 71-89), [DEV_LOG_2026-02-16.md](DEV_LOG_2026-02-16.md) (Sessions 90-106), [DEV_LOG_2026-02-17.md](DEV_LOG_2026-02-17.md) (Sessions 107-111), [DEV_LOG_2026-02-18.md](DEV_LOG_2026-02-18.md) (Session 112), [DEV_LOG_2026-02-19.md](DEV_LOG_2026-02-19.md) (Sessions 113-115), [DEV_LOG_2026-02-20.md](DEV_LOG_2026-02-20.md) (Sessions 116-125)

## Where We Are

- **2111 tests**, zero lint errors (Session 127)
- **Days 1-24 complete:** Full simulation engine, governance + AI interpretation, reports + game loop, web dashboard + Discord bot + OAuth + evals framework, APScheduler, presenter pacing, AI commentary, UX overhaul, security hardening, production fixes, player pages overhaul, simulation tuning, home page redesign, live arena, team colors, live zone polish, career stats, league leaders
- **Day 25:** Abstract game spine architecture — rearchitecting the simulation to be truly malleable by governance
- **Live at:** https://pinwheel.fly.dev
- **Latest commit:** `7ac4e59` — docs: Phase 6 codegen frontier

## Today's Agenda

- [x] Deep architecture research: why have rules never changed a real game?
- [x] Fix governance effect pipeline (3 bugs preventing effects from reaching gameplay)
- [x] Abstract game spine plan (77KB architecture doc in `docs/plans/abstract_game_spine.md`)
- [x] Wire up 11 dormant RuleSet params (home court, governance theater)
- [x] Fix broken moves (Lockdown Stance, Iron Will) + activate Fate attribute
- [x] Add instrumentation (request timing, AI logging, behavior events, phase timing)
- [x] Phase 6 codegen frontier added to architecture plan
- [x] Comprehensive unimplemented audit (61 findings)
- [ ] Record demo video (3-minute hackathon submission)

---

## Session 126 — Governance Effects Pipeline Fix + Abstract Game Spine Architecture

**What was asked:** Rearchitect the app so the game is truly malleable — not just basketball with tunable parameters, but an abstract game engine where players could vote to change the sport entirely (coin flipping, arm wrestling, jump rope, etc.). Keep teams, players, and Discord community; make everything else data.

**What was built:**

Governance pipeline fix (3 bugs preventing rules from ever affecting gameplay):
- `_extract_effects_from_proposal()` was a stub returning `[]` — now calls `get_proposal_effects_v2()` on the proposal payload
- `tally_governance_with_effects()` now backfills v2 effects from event store payloads for proposals not in the explicit map
- `block_action` and `substitute_action` on `HookResult` existed as fields but were never consumed — now wired through `PossessionContext` into `resolve_possession()` (block causes turnover, substitute overrides shot type)
- Added `block_action` and `substitute_action` action types to `RegisteredEffect._apply_action_code()`

Architecture research (4 parallel agents):
- Deep analysis of simulation engine rigidity (42 governable params vs hardcoded flow)
- Full governance lifecycle trace (identified 5 bottlenecks)
- Event system and game loop architecture analysis
- External research: ECS, rule engines, Nomic/Calvinball, Factorio mod system, LLM-as-interpreter papers

Abstract game spine plan (in progress):
- Background agent writing comprehensive architecture doc to `docs/plans/abstract_game_spine.md`
- Core insight: replace hardcoded enums with data-driven registries, refactor possession flow into event pipeline with interceptors, expand AI interpreter DSL

**Files modified (5):** `src/pinwheel/core/governance.py`, `src/pinwheel/core/hooks.py`, `src/pinwheel/core/possession.py`, `src/pinwheel/core/simulation.py`, `src/pinwheel/core/state.py`

**2079 tests, zero lint errors.**

**What could have gone better:** The `_extract_effects_from_proposal()` stub has been in the codebase since the effects system was built — a placeholder that was never implemented. This meant the entire v2 effects pipeline was dead code in production. Should have been caught by the production audit in Session 116 (which found 0 `effect.registered` events but didn't trace back to the stub). A test asserting "proposal with effects_v2 payload produces non-empty effect list" would have caught this immediately.

---

## Session 127 — Wire Up Dormant Params, Fix Moves, Activate Fate, Add Instrumentation

**What was asked:** Fix all P0/P1 findings from the unimplemented audit: 11 dormant RuleSet params, 2 broken moves, dormant Fate attribute, missing instrumentation. Also add Phase 6 (AI-generated code execution with LLM council) to the abstract game spine plan.

**What was built:**

Home court mechanics (7 params now active in simulation):
- `home_crowd_boost`: shot probability bonus for home offense
- `away_fatigue_factor`: extra stamina drain per possession for away team
- `crowd_pressure`: turnover rate increase for away offense
- `altitude_stamina_penalty`: drain scaled by venue altitude (Haversine distance computation)
- `travel_fatigue_enabled`/`travel_fatigue_per_mile`: pre-game stamina penalty from travel

Governance theater params (4 params now active):
- `three_point_distance`: affects shot selection weight + logistic curve midpoint
- `team_foul_bonus_threshold`: +1 bonus FT when team fouls exceed threshold per quarter
- `max_shot_share`: reduces ball handler selection for players over the cap
- `min_pass_per_possession`: each required pass has ~3% turnover chance

Moves fixed + Fate activated:
- Lockdown Stance: -12% shot probability (defensive move now triggers in possession flow)
- Iron Will: +8% flat bonus (performing through exhaustion)
- Fate's Hand: 30% chance +18%, 70% chance -5% (Oracle's chaos move)
- Fate clutch shooting: +6.4% in close games (diff < 5) for Fate-80 hoopers
- Fate lucky bounces: +2.7 offensive rebound weight for Fate-90 players
- Defensive moves now checked alongside offensive moves in resolve_possession()

Instrumentation:
- HTTP request timing middleware (method, path, status, duration_ms; skips /static/)
- Structured AI call logging in record_ai_usage() (tokens, latency, model, cost)
- Governor behavior events on EventBus: proposal_submitted, vote_cast, token_spent, strategy_set
- Per-phase timing in game loop: simulate_and_govern, ai_generation, persist_and_finalize + round summary

Architecture plan update:
- Phase 6: Codegen frontier — AI-generated game mechanics with LLM council security harness (generator + 3 independent reviewers), sandbox architecture, progressive trust levels, full RPS free-throw walkthrough

Audit:
- Comprehensive unimplemented audit: 61 findings (6 P0, 21 P1, 23 P2, 11 P3) written to `docs/plans/2026-02-24-unimplemented-audit.md`

**Files modified (13):** `src/pinwheel/core/simulation.py`, `src/pinwheel/core/possession.py`, `src/pinwheel/core/scoring.py`, `src/pinwheel/core/state.py`, `src/pinwheel/core/moves.py`, `src/pinwheel/core/game_loop.py`, `src/pinwheel/core/hooks.py`, `src/pinwheel/ai/usage.py`, `src/pinwheel/main.py`, `src/pinwheel/discord/bot.py`, `src/pinwheel/discord/views.py`, `tests/test_simulation.py`, `tests/test_instrumentation.py`

**2111 tests, zero lint errors.**

**What could have gone better:** Running 3 parallel agents that all touch the simulation engine created merge risk — agents 1 (home court) and 2 (moves/fate) both modified `possession.py`, `scoring.py`, and `state.py`. The agents happened to touch non-overlapping sections, but this was luck. A safer pattern would be to assign each agent a strict file boundary, or run them sequentially with the shared files.
