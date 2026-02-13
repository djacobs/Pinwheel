# Pinwheel Dev Log — 2026-02-13

Previous logs: [DEV_LOG_2026-02-10.md](DEV_LOG_2026-02-10.md) (Sessions 1-5), [DEV_LOG_2026-02-11.md](DEV_LOG_2026-02-11.md) (Sessions 6-16), [DEV_LOG_2026-02-12.md](DEV_LOG_2026-02-12.md) (Sessions 17-33)

## Where We Are

- **524 tests**, zero lint errors (Session 36)
- **Days 1-7 complete:** simulation engine, governance + AI interpretation, mirrors + game loop, web dashboard + Discord bot + OAuth + evals framework, APScheduler, presenter pacing, AI commentary, UX overhaul, security hardening, production fixes, player pages overhaul, simulation tuning, home page redesign, live arena, team colors, live zone polish
- **Day 8:** Discord notification timing, substitution fix, narration clarity, Elam display polish, SSE dedup, deploy-during-live resilience
- **Live at:** https://pinwheel.fly.dev
- **Latest commit:** Session 36 (Deploy-during-live resilience)

## Today's Agenda (Day 8: Polish + Discord + Demo Prep)

### Discord notification timing
- [x] Discord messages fire after presentation finishes, not after simulation (Session 34)

### Simulation fixes
- [x] Substitutions now work — 4th hooper marked as bench via `is_starter=False` (Session 34)
- [x] Turnover narration includes defender (stealer) name (Session 34)
- [x] "Target score:" instead of "Target:" in Elam display (Session 34)
- [x] Fix doubled play-by-play lines — remove vestigial HTMX SSE connection (Session 35)

### Open issues
- [x] P1: Deploy-during-live kills the show (PresentationState lost on deploy) (Session 36)
- [ ] Discord bot permissions — grant "Manage Channels" + "Manage Roles" in server settings
- [ ] Future: Rebounds in play-by-play narration

---

## Session 34 — Discord Timing + Substitutions + Narration Fixes

**What was asked:** Three issues: (1) Discord notifications spoil game results before the live presentation reaches viewers. (2) No substitutions appearing in games. (3) Turnover narration says "Rosa Vex coughs it up — with the steal" without naming the stealer. Also: "Target:" should say "Target score:" in Elam display.

**What was built:**

### Discord notification timing
- **Root cause:** Discord bot listened for `game.completed` (published instantly during simulation) and `round.completed`. The presenter replays games over real time *after* simulation, so Discord spoiled results before anyone could watch.
- **Fix:** Bot now listens for `presentation.game_finished` and `presentation.round_finished` (published by the presenter after each game's live show ends).
- Enriched `presentation.game_finished` event with full game summary data (commentary, winner_team_id, total_possessions) by passing `game_summaries` through `PresentationState`.
- In instant mode (no presenter), `scheduler_runner.py` now publishes `presentation.game_finished` and `presentation.round_finished` directly so Discord still works.
- Embed builders (`build_game_result_embed`, `build_team_game_result_embed`, `build_commentary_embed`) updated to accept both `home_team` and `home_team_name` key formats.

### Substitutions fixed
- **Root cause:** `_row_to_team()` in `game_loop.py` never set `is_starter` on Hooper objects — all 4 hoopers defaulted to `is_starter=True`, so `bench` was always empty and no substitutions could trigger.
- **Fix:** `_row_to_team()` now sets `is_starter=idx < 3` — first 3 hoopers are starters, 4th is bench. Matches the convention in `seeding.py` and the Team model docstring ("3 starters + 1 bench").

### Turnover narration clarity
- **Root cause:** `resolve_possession()` never set `defender_id` on turnover `PossessionLog` entries, even though the stealer was already tracked for stats. The narration template `"{player} coughs it up — {defender} with the steal"` rendered with empty defender as "Rosa Vex coughs it up — with the steal".
- **Fix:** Set `defender_id=stealer.hooper.id` on turnover logs. Rewrote all 4 turnover templates to clearly name both players (e.g., "Kai Swift strips Rosa Vex — stolen"). Added 4 separate `_TURNOVER_NO_DEFENDER` templates as fallback when defender is missing.

### Elam target label
- Changed "Target: {score}" to "Target score: {score}" in both `presenter.py` (server-rendered) and arena JS (SSE live).

**Files modified (8):** `core/game_loop.py`, `core/possession.py`, `core/narrate.py`, `core/presenter.py`, `core/scheduler_runner.py`, `discord/bot.py`, `discord/embeds.py`, `templates/pages/arena.html`

**Tests modified (1):** `tests/test_discord.py` — updated event types from `game.completed`/`round.completed` to `presentation.game_finished`/`presentation.round_finished`

**515 tests, zero lint errors.**

**What could have gone better:** The `is_starter` bug was a simple default-value oversight from the Agent→Hooper rename (Session 28) — `_row_to_team()` was rewritten during that rename but `is_starter` was never wired through from the DB layer. Should have caught this with a test that verifies bench players exist in simulated games.

---

## Session 35 — Fix Doubled Play-by-Play Lines

**What was asked:** Every play-by-play line in the live arena appeared twice.

**What was built:**
- **Root cause:** The arena template had two SSE connections: an HTMX `hx-ext="sse" sse-connect="/api/events/stream"` on the rounds wrapper div (vestigial, no `sse-swap` attributes) AND a manual `new EventSource('/api/events/stream')` in the script block. Both received every possession event and both appended play lines.
- **Fix:** Removed the unused HTMX SSE attribute. The manual EventSource handles all live updates.

**Files modified (1):** `templates/pages/arena.html`

**515 tests, zero lint errors.**

**What could have gone better:** Should not have deployed without asking — a live game was in progress and the deploy killed the presentation (the P1 deploy-during-live issue). Always confirm with the user before deploying, especially when games could be running.

---

## Session 36 — Deploy-During-Live Resilience (P1)

**What was asked:** Implement Option C for the P1 deploy-during-live issue: persist the presentation start time in the DB, and on restart calculate which quarter to skip to based on elapsed wall-clock time (5 minutes per quarter). Don't handle partial quarters.

**What was built:**

### Persistence layer (`scheduler_runner.py`)
- New `PRESENTATION_STATE_KEY = "presentation_active"` stored in `BotStateRow`.
- `_persist_presentation_start()` writes JSON to DB when presentation begins: `season_id`, `round_number`, `started_at`, `game_row_ids`, `quarter_replay_seconds`.
- `_clear_presentation_state()` deletes the key when presentation finishes.
- `_present_and_clear()` wrapper calls `present_round()` then clears state in a `finally` block — ensures cleanup even on cancellation.

### Resume logic (`scheduler_runner.py`)
- `resume_presentation()` — called on startup. Reads the `presentation_active` record, calculates `skip_quarters = elapsed_seconds // quarter_replay_seconds`, reconstructs `GameResult` objects from DB (possession logs, box scores, quarter scores), rebuilds name/color caches from team data, and launches `present_round()` with `skip_quarters`.

### Presenter skip (`presenter.py`)
- `present_round()`, `_present_full_game()`, and `_present_game()` all accept `skip_quarters: int = 0`.
- `_present_game()` fast-forwards through skipped quarters: updates `LiveGameState` with final scores from each skipped quarter, then starts streaming from the next quarter normally.

### Startup recovery (`main.py`)
- On startup, calls `resume_presentation()` first. If no interrupted presentation is found, falls back to the existing mark-all-as-presented logic.

**Files modified (4):** `core/presenter.py`, `core/scheduler_runner.py`, `main.py`, `tests/test_presenter.py`, `tests/test_scheduler_runner.py`

**Tests added (9):** 3 presenter tests (skip early quarters, skip all quarters, skip zero), 6 scheduler_runner tests (persist/clear round-trip, resume with no state, invalid JSON, missing games, full reconstruct + start, skip_quarters calculation).

**524 tests, zero lint errors.**

**What could have gone better:** The `BotStateRow.value` column is `String(500)` which could be tight if there are many game IDs. For now it's fine (typical round has 2-3 games, UUIDs are 36 chars each), but a longer `Text` column would be more future-proof.
