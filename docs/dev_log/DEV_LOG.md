# Pinwheel Dev Log — 2026-02-15

Previous logs: [DEV_LOG_2026-02-10.md](DEV_LOG_2026-02-10.md) (Sessions 1-5), [DEV_LOG_2026-02-11.md](DEV_LOG_2026-02-11.md) (Sessions 6-16), [DEV_LOG_2026-02-12.md](DEV_LOG_2026-02-12.md) (Sessions 17-33), [DEV_LOG_2026-02-13.md](DEV_LOG_2026-02-13.md) (Sessions 34-47), [DEV_LOG_2026-02-14.md](DEV_LOG_2026-02-14.md) (Sessions 48-70)

## Where We Are

- **1446 tests**, zero lint errors (Session 73)
- **Days 1-7 complete:** simulation engine, governance + AI interpretation, reports + game loop, web dashboard + Discord bot + OAuth + evals framework, APScheduler, presenter pacing, AI commentary, UX overhaul, security hardening, production fixes, player pages overhaul, simulation tuning, home page redesign, live arena, team colors, live zone polish
- **Day 8:** Discord notification timing, substitution fix, narration clarity, Elam display polish, SSE dedup, deploy-during-live resilience
- **Day 9:** The Floor rename, voting UX, admin veto, profiles, trades, seasons, doc updates, mirror→report rename
- **Day 10:** Production bugfixes — presentation mode, player enrollment, Discord invite URL
- **Day 11:** Discord defer/timeout fixes, get_active_season migration, playoff progression pipeline
- **Day 12:** P0 fixes — /join, score spoilers, strategy system, trade verification, substitution verification
- **Day 13:** Self-heal missing player enrollments, decouple governance from game simulation
- **Day 14:** Admin visibility, season lifecycle phases, effects system, NarrativeContext, game richness audit, SQLite write lock fix, playoff series, V2 interpreter, e2e verification, workbench
- **Day 15:** Overnight wave execution — amendments, repeal, milestones, drama pacing, effects wave 2, documentation
- **Live at:** https://pinwheel.fly.dev
- **Day 15 (cont):** Dev-mode Discord guard, server welcome DM for new members
- **Day 15 (cont):** V2 tier detection, minimum voting period, Discord channel slug fix
- **Day 15 (cont):** /schedule nudge in new-season Discord embeds
- **Latest commit:** `8b30441` — add /schedule nudge to new-season Discord embeds

## Today's Agenda

- [x] Complete overnight wave execution (Waves 3b-5)
- [x] Push all commits to GitHub
- [x] Deploy to production
- [ ] Reset season history for hackathon demo
- [ ] Demo verification — Showboat/Rodney pipeline

---

## Session 71 — Overnight Wave Completion (Waves 3b-5)

**What was asked:** Continue overnight parallel wave execution. 6 background agents were launched for remaining Wave 3 items + Wave 4.4 + Wave 5. Three had completed before context compaction (amendments, drama pacing, documentation). Three were still running (effects wave 2, repeal, moves/milestones).

**What was built:**

### Wave 3.2: Effects Wave 2 (agent ad9228d)
- Governance hooks (`gov.pre`/`gov.post`) in `tally_pending_governance()` — fire before/after governance tally with HookContext
- Report hooks (`report.commentary.pre`/`report.simulation.pre`) in `_phase_ai()` — inject narrative context from effects into AI prompts
- Hooper meta loading into MetaStore alongside team meta
- Fixed `sim.shot.pre` → `sim.possession.pre` references in V2 system prompt and mock
- Updated `docs/EFFECTS_SYSTEM.md` with governance/report hook documentation
- 6 new tests in `test_game_loop.py`

### Wave 3.6: Repeal Mechanism (agent a5da0fc)
- `EffectRegistry.get_effect()` and `remove_effect()` methods
- `repeal_effect()` — writes `effect.repealed` event, removes from registry
- `submit_repeal_proposal()` — Tier 5, 2 PROPOSE tokens, supermajority (67%) threshold
- `/effects` command — browse active effects with descriptions
- `/repeal` command with autocomplete — propose repealing a non-parameter effect
- `RepealConfirmView` with confirm/cancel + token refund on cancel
- `build_effects_list_embed()` and `build_repeal_confirm_embed()`
- Updated `tally_governance_with_effects()` to execute repeals on passage
- 21 new tests in `test_repeal.py`

### Wave 3.4: Moves Phases 2-4 (agent ae04d42)
- **Milestones** (`core/milestones.py`): `MilestoneDefinition` dataclass, `check_milestones()` pure function, 4 default milestones (Fadeaway at 50 pts, No-Look Pass at 20 ast, Strip Steal at 15 stl, Deep Three at 10 3PM)
- **Repository**: `get_hooper_season_stats()` aggregates box scores, `add_hooper_move()` appends to JSON array
- **Game loop**: `_check_earned_moves()` after each round, publishes `hooper.milestone_reached` events
- **Governed moves**: `move_grant` effect type in `EffectSpec`, AI interpreter V2 updated with move grant vocabulary and mock patterns
- **Narrative**: Commentary includes `[MOVE: name]` tags and "Signature moves activated" context
- 18 new tests in `test_milestones.py`

### Wave 3.7: Amendment Flow (agent a8e779e)
- `/amend` command with proposal autocomplete
- `count_amendments()` — max 2 per proposal
- `AmendConfirmView` with confirm/cancel
- `build_amendment_confirm_embed()`
- 15 new tests in `test_amendment.py`

### Wave 4.4: Dramatic Pacing (agent a44b1c8)
- `core/drama.py` — pure computation module
- `DramaLevel` (routine/elevated/high/peak), `DramaAnnotation` dataclass
- `annotate_drama()` — detects lead changes, scoring runs, move activations, Elam approach, game-winning shots, close late games
- `normalize_delays()` — redistributes time budget for variable-speed replay
- `get_drama_summary()` — counts per level for logging
- 29 new tests in `test_drama.py`

### Wave 5: Documentation Bundle (agent a86862f)
- `docs/API_ARCHITECTURE.md` — all REST endpoints, SSE events, auth requirements
- `docs/TECH_ARCHITECTURE.md` — system overview, request lifecycle, AI pipeline, event bus
- `docs/GOVERNANCE_EVENTS.md` — all event types with payload schemas
- `docs/ELAM_ENDING.md` — trigger mechanics, target calculation, hook points
- 16 plan files archived to `docs/plans/`

**Files modified (16):** `src/pinwheel/ai/commentary.py`, `src/pinwheel/ai/interpreter.py`, `src/pinwheel/core/effects.py`, `src/pinwheel/core/game_loop.py`, `src/pinwheel/core/governance.py`, `src/pinwheel/core/presenter.py`, `src/pinwheel/db/repository.py`, `src/pinwheel/discord/bot.py`, `src/pinwheel/discord/embeds.py`, `src/pinwheel/discord/views.py`, `src/pinwheel/models/governance.py`, `static/css/pinwheel.css`, `templates/pages/arena.html`, `tests/test_game_loop.py`, `docs/EFFECTS_SYSTEM.md`

**New files (12):** `src/pinwheel/core/drama.py`, `src/pinwheel/core/milestones.py`, `tests/test_amendment.py`, `tests/test_drama.py`, `tests/test_milestones.py`, `tests/test_repeal.py`, `docs/API_ARCHITECTURE.md`, `docs/ELAM_ENDING.md`, `docs/GOVERNANCE_EVENTS.md`, `docs/TECH_ARCHITECTURE.md`, + 16 plan files in `docs/plans/`

**1421 tests (258 new across 6 agents), zero lint errors.**

**What could have gone better:** All 6 agents ran in parallel against the same working tree. Each independently reported 1421 tests passing, and the integrated suite also passed 1421 — no cross-agent conflicts. The only issue was the server startup for post-commit demo verification: `pinwheel` module wasn't importable without `PYTHONPATH=src`, indicating the editable install may need a `uv sync` refresh.

---

## Session 72 — Dev-Mode Discord Guard + Server Welcome DM

**What was asked:** Fix duplicate Discord channel creation caused by local dev server connecting to production guild. Also add a first-touch welcome DM for new Discord server members, before they pick a team.

**What was built:**
- `is_discord_enabled()` now returns `False` when `pinwheel_env == "development"` (the default), preventing local dev servers from connecting to the production Discord guild
- `on_member_join()` handler — sends a first-touch DM when a human joins the Discord server, explaining what Pinwheel is and how to get started (`/join`, `/propose`, `/vote`)
- `build_server_welcome_embed()` — gold-colored embed with "Pinwheel starts as basketball, but becomes whatever you want" intro, quick-start commands, and footer "The rules are yours to write"
- 6 new tests: dev-mode guard, server welcome embed content/color, on_member_join for humans/bots/DM-forbidden

**Files modified (3):** `src/pinwheel/discord/bot.py`, `src/pinwheel/discord/embeds.py`, `tests/test_discord.py`

**1427 tests, zero lint errors.**

**What could have gone better:** The duplicate channel issue was caused by running a dev server that picked up `DISCORD_ENABLED=true` from the environment. The guard is simple and effective — check for development mode first.

---

## Session 73 — V2 Tier Detection + Minimum Voting Period + Channel Slug Fix

**What was asked:** Fix two governance production bugs: (1) V2 tier detection gap — proposals with `hook_callback` effects from V2 interpreter were classified as Tier 5 ("wild") because the legacy `detect_tier()` only checked `parameter_change`; (2) No minimum voting period — proposals tallied immediately with 1 vote before other governors could see them. Also fix duplicate Discord channel creation caused by slug normalization mismatch.

**What was built:**

### V2 Tier Detection
- `detect_tier_v2()` in `governance.py` — examines `ProposalInterpretation.effects` directly: `parameter_change` → reuse existing tier logic, `hook_callback`/`meta_mutation`/`move_grant` → Tier 3, `narrative` → Tier 2, empty/injection/rejection → Tier 5, compound = max tier
- Updated `_needs_admin_review()` with optional `interpretation_v2` param — V2 with real effects and no injection flag → not wild; V2 with empty effects or injection → wild; low confidence (< 0.5) always flagged
- Updated `submit_proposal()` and `confirm_proposal()` with optional `interpretation_v2` param
- Wired V2 tier into `bot.py` and `views.py` (ProposalConfirmView, ReviseProposalModal)

### Minimum Voting Period
- Added `proposal.first_tally_seen` event type to `GovernanceEventType` (also added `proposal.vetoed` and `proposal.flagged_for_review` which were used but missing)
- `tally_pending_governance()` now defers new proposals on first tally cycle — emits `first_tally_seen` event, removes from pending list. Proposals only tallied on their second tally cycle
- Added `skip_deferral` param for season-close catch-up tallies in `season.py`
- 19 new tests: `TestTierDetectionV2` (10), `TestNeedsAdminReviewV2` (6), `TestMinimumVotingPeriod` (3)
- Updated 10+ existing tests to account for two-cycle tally pattern

### Discord Channel Slug Fix
- Root cause: `team.name.lower().replace(" ", "-")` produces `st.-johns-ravens` for "St. Johns Ravens", but Discord normalizes to `st-johns-ravens`. Name lookup fails → duplicate created on every deploy
- Fix: `re.sub(r"[^a-z0-9-]", "", raw)` strips periods, apostrophes, and other special chars to match Discord's normalization

**Files modified (8):** `src/pinwheel/core/governance.py`, `src/pinwheel/core/game_loop.py`, `src/pinwheel/core/season.py`, `src/pinwheel/models/governance.py`, `src/pinwheel/discord/bot.py`, `src/pinwheel/discord/views.py`, `tests/test_governance.py`, `tests/test_game_loop.py`, `tests/test_api/test_e2e_workflow.py`, `tests/test_scheduler_runner.py`

**1446 tests, zero lint errors.**

**What could have gone better:** The minimum voting period change required updating 10+ existing tests that assumed immediate tally. The `skip_deferral` parameter for season-close was discovered only after tests failed — should have been identified during planning.

---

## Session 74 — Schedule Nudge in New-Season Embeds

**What was asked:** After running `/new-season`, governors have no prompt to check the schedule. Add a `/schedule` nudge to the new-season response.

**What was built:**
- Added "Run `/schedule` to see the matchups." to the admin-facing `/new-season` response embed (ephemeral)
- Added the same line to the public announcement embed posted to #play-by-play
- Welcome message already referenced `/schedule`, so no change needed there

**Files modified (1):** `src/pinwheel/discord/bot.py`

**1446 tests, zero lint errors.**

**What could have gone better:** Straightforward change — nothing to flag.

---

## Plan/Implementation Alignment Cleanup Checklist (Appended)

### Phase 1: Triage plan artifacts (highest impact)
- [ ] Move non-Pinwheel plans out of active planning scope (archive to `docs/plans/external/` or delete if accidental imports).
- [ ] Triage these clearly foreign files: `docs/plans/2026-02-15-tk-authorship-journey-implementation.md`, `docs/plans/2026-02-15-close-document-navigation-draftstage.md`, `docs/plans/2026-02-15-fix-three-revise-draft-ux-bugs.md`, `docs/plans/2026-02-15-token-counter-animation.md`, `docs/plans/2026-02-15-llm-call-optimization-advisor.md`.
- [ ] Triage these non-Pinwheel project plans: `docs/plans/2026-02-15-linkblog-link-aggregation-syndication.md`, `docs/plans/2026-02-15-feedly-oauth-refresh-token.md`, `docs/plans/2026-02-15-newsletter-ring-implementation.md`.
- [ ] Add a short note at top of each moved file: "Archived as out-of-repo scope."

### Phase 2: Mark implemented plans as implemented
- [ ] Update `docs/plans/2026-02-14-token-cost-tracking-dashboard.md` status from Draft to Implemented (except demo step if still pending).
- [ ] Update `docs/plans/2026-02-14-rate-limiting-proposals.md` status to Implemented (cooldown + window cap + spend-before-confirm landed in `src/pinwheel/discord/bot.py:2026`).
- [ ] Update `docs/plans/2026-02-14-dramatic-pacing-modulation.md` status to Implemented (module + tests exist in `src/pinwheel/core/drama.py`, `tests/test_drama.py`).
- [ ] Update `docs/plans/2026-02-14-season-memorial-system.md` status to Implemented (core + template + tests exist).
- [ ] Update high-level stale checklists (at least add "historical snapshot; see DEV_LOG for completion state") in `docs/plans/2026-02-11-discord-bot-plan.md`, `docs/plans/2026-02-11-frontend-plan.md`, `docs/plans/2026-02-11-day1-implementation-plan.md`.

### Phase 3: True remaining gaps (keep as active TODO)
- [ ] Spectator follow system remains genuinely unimplemented; keep `docs/plans/2026-02-14-spectator-journey-and-team-following.md` active and break into executable tickets.
- [ ] Create missing follow API/module: `src/pinwheel/api/follow.py`.
- [ ] Add DB model + repository methods for follows (`TeamFollowRow`, `follow_team`, `unfollow_team`, etc.) in `src/pinwheel/db/models.py` and `src/pinwheel/db/repository.py`.
- [ ] Add follow/unfollow UI on `templates/pages/team.html` and personalized home highlighting in `templates/pages/home.html`.
- [ ] Add tests for follow flow (`tests/test_follow.py`).

### Phase 4: Lifecycle/data integrity fixes
- [ ] Resolve archive lifecycle mismatch: `close_offseason()` docstring says it archives, but it currently only transitions to complete (`src/pinwheel/core/season.py:883`, `src/pinwheel/core/season.py:922`).
- [ ] Either call `archive_season()` during season close, or revise docs/dev log to reflect manual archive policy.
- [ ] Confirm whether `"series"` report type should be produced; if yes, add generation/store path in `src/pinwheel/core/game_loop.py` (current flow stores simulation/governance/private only).

### Phase 5: Dev log + demo hygiene
- [ ] Close or carry forward open agenda items in `docs/dev_log/DEV_LOG.md:27` and `docs/dev_log/DEV_LOG.md:28`.
- [ ] If cost dashboard is now implemented, add demo capture step for `/admin/costs` in `scripts/run_demo.sh`.
- [ ] Add one "Plan hygiene" entry to `docs/dev_log/DEV_LOG.md` documenting what was archived vs marked complete.
