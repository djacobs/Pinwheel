# Pinwheel Dev Log — 2026-06-12

Previous logs: [DEV_LOG_2026-02-10.md](DEV_LOG_2026-02-10.md) (Sessions 1-5), [DEV_LOG_2026-02-11.md](DEV_LOG_2026-02-11.md) (Sessions 6-16), [DEV_LOG_2026-02-12.md](DEV_LOG_2026-02-12.md) (Sessions 17-33), [DEV_LOG_2026-02-13.md](DEV_LOG_2026-02-13.md) (Sessions 34-47), [DEV_LOG_2026-02-14.md](DEV_LOG_2026-02-14.md) (Sessions 48-70), [DEV_LOG_2026-02-15.md](DEV_LOG_2026-02-15.md) (Sessions 71-89), [DEV_LOG_2026-02-16.md](DEV_LOG_2026-02-16.md) (Sessions 90-106), [DEV_LOG_2026-02-17.md](DEV_LOG_2026-02-17.md) (Sessions 107-111), [DEV_LOG_2026-02-18.md](DEV_LOG_2026-02-18.md) (Session 112), [DEV_LOG_2026-02-19.md](DEV_LOG_2026-02-19.md) (Sessions 113-115), [DEV_LOG_2026-02-20.md](DEV_LOG_2026-02-20.md) (Sessions 116-125), [DEV_LOG_2026-02-24.md](DEV_LOG_2026-02-24.md) (Sessions 126-128), [DEV_LOG_2026-02-25.md](DEV_LOG_2026-02-25.md) (Sessions 129-131)

## Where We Are

- **2657 tests**, zero lint errors (Session 132)
- **Days 1-26 complete** plus the codegen frontier (Phase 6) infrastructure
- **Day 27 (this session):** Full-codebase audit against the original brief, nine
  sim/game-loop bug fixes, game summary pipeline overhaul, and the codegen
  frontier wiring plan (`docs/plans/2026-06-12-codegen-frontier-wiring.md`)
- **Live at:** https://pinwheel.fly.dev
- **Latest commit:** see git log — `fix/sim-engine-bugs` and
  `feat/game-summary-overhaul` merged to main

## Today's Agenda

- [x] Audit: do sim/game-loop/scoring match the spec? (8 confirmed bugs + 1 mechanism bug)
- [x] Audit: why are game summaries poor? (5 stacked causes, mostly plumbing)
- [x] Audit: can players really change *anything*? (codegen frontier exists but is unreachable)
- [x] Fix all nine confirmed sim/game-loop bugs with regression tests
- [x] Overhaul the summary pipeline (persist commentary, full-game context, Opus round report)
- [x] Write the codegen-frontier wiring plan (5 phases, pre-execution human gate)
- [ ] Implement codegen wiring Phase 1 (opponent_score_modifier + meta_writes) — next session
- [ ] Implement codegen wiring Phases 2-5 per the plan

## Session 132 — Audit + Sim Bug Fixes + Summary Overhaul

**What was asked:** Re-examine the product against its original brief (basketball
that governance can transform into *anything*; older models couldn't generate the
needed code), confirm and fix bugs in the game loop and scoring, and fix the poor
written game summaries. Work was delegated to background workers; they were
permission-blocked from editing, so their verified findings were implemented in
the main session.

**What was built:**

Sim/game-loop fixes (`fix/sim-engine-bugs`):
- Heat Check now arms per-team and only boosts the next *three-point* attempt —
  previously the opponent's ball handler consumed the flag on the very next
  possession (simulation.py, moves.py)
- Team fouls reset entering the Elam period; Elam-period minutes now accrue
- Negative `shot_value_modifier` effects clamp at 0 — `sum(box.points)` always
  equals the team score
- Tied games go to sudden death (`_run_sudden_death`) instead of silently
  awarding the home team; sudden-death points fold into the final period row
- Effect-driven ejections reach the play-by-play via `PossessionResult.extra_logs`
- BOOST tokens regenerate at tally again (2/2/2 per GAME_LOOP.md)
- Game seeds are deterministic: sha256(season, round, matchup, ruleset_hash)
  per the GAME_LOOP.md replay contract (`derive_game_seed`)
- Partially played rounds: `_check_season_complete` compares (round, matchup)
  pairs, `step_round` skips already-stored matchups, `tick_round` resumes an
  incomplete regular-season round

Summary overhaul (`feat/game-summary-overhaul`):
- Per-game commentary is persisted (`report_type="commentary"`, keyed by game
  row id) and rendered on the game page ("Courtside Commentary"); previously it
  was generated, sent to SSE/Discord, and discarded — the game page showed the
  round editorial instead, unformatted
- `prose` filter applied on game.html and arena.html report renders
- Commentary prompt now sees the whole game: quarter scores, lead changes,
  largest lead, team strategies, starter archetypes, key plays sampled
  start-to-finish (ending guaranteed), and the game-deciding play
- max_tokens raised (commentary 800, reel 500) with `stop_reason` truncation
  trimming; all player-facing report generators fall back to mocks on
  `anthropic.APIError` instead of storing bracketed error strings
- Flagship round report upgraded to `claude-opus-4-6`; volatile round/governance
  data moved to the user message so the cached system prompt actually caches
- Mock reports compose real paragraphs; mock commentary gains seed-keyed opener
  variation

Codegen frontier plan (`docs/plans/2026-06-12-codegen-frontier-wiring.md`):
- Verified the Phase 6 council pipeline has **zero callers** — `/propose`
  dead-ends beyond-primitive proposals as `custom_mechanic` placeholders, and
  nothing produces `GameDefinitionPatch` effects
- 5-phase plan: engine correctness fixes → pre-execution admin gate (ships
  dark, also fixes `/disable-effect` non-persistence) → proposal router behind
  `PINWHEEL_CODEGEN_ENABLED` → sandbox hardening (subprocess pre-flight,
  thread timeout replacing SIGALRM) → structural change path (interpreter emits
  GameDefinitionPatches with invariant validation + smoke sim)

**Decisions made:**
- Sudden death's absolute last resort (both teams ejected, 100 possessions
  scoreless) is a seeded coin flip — deterministic under replay
- Commentary persists through the existing reports table with the game id in
  `metadata_json` (no schema change, no prod migration risk)
- Codegen wiring is planned-not-built: the pre-execution human gate must land
  before generated code is reachable from player proposals

**Files modified (19):** `core/simulation.py`, `core/possession.py`,
`core/moves.py`, `core/game_loop.py`, `core/scheduler_runner.py`,
`ai/commentary.py`, `ai/report.py`, `db/repository.py`, `api/pages.py`,
`templates/pages/game.html`, `templates/pages/arena.html`,
`tests/test_simulation.py`, `tests/test_game_loop.py`,
`tests/test_scheduler_runner.py`, `tests/test_commentary.py`,
`tests/test_reports.py`, `tests/test_pages.py`, `docs/dev_log/UX_NOTES.md`,
`docs/plans/2026-06-12-codegen-frontier-wiring.md` (new)

**Tests:** 2657 passing (38 new), zero lint errors

**What could have gone better:** Background worker agents were denied
Edit/Write/Bash in their isolated worktrees, so both code phases had to be
re-implemented in the main session — the workers' value ended up being their
verified audits and line-level fix plans, which made the reimplementation fast.
The commentary persistence initially keyed on the sim's synthetic game id
(`g-{round}-{matchup}`) instead of the DB row id the web page uses; caught by
the page-level test, fixed by carrying `game_row_id` on the summary.
