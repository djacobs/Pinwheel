# Pinwheel Dev Log — 2026-02-16

Previous logs: [DEV_LOG_2026-02-10.md](DEV_LOG_2026-02-10.md) (Sessions 1-5), [DEV_LOG_2026-02-11.md](DEV_LOG_2026-02-11.md) (Sessions 6-16), [DEV_LOG_2026-02-12.md](DEV_LOG_2026-02-12.md) (Sessions 17-33), [DEV_LOG_2026-02-13.md](DEV_LOG_2026-02-13.md) (Sessions 34-47), [DEV_LOG_2026-02-14.md](DEV_LOG_2026-02-14.md) (Sessions 48-70), [DEV_LOG_2026-02-15.md](DEV_LOG_2026-02-15.md) (Sessions 71-89)

## Where We Are

- **1966 tests**, zero lint errors (Session 93)
- **Days 1-7 complete:** simulation engine, governance + AI interpretation, reports + game loop, web dashboard + Discord bot + OAuth + evals framework, APScheduler, presenter pacing, AI commentary, UX overhaul, security hardening, production fixes, player pages overhaul, simulation tuning, home page redesign, live arena, team colors, live zone polish
- **Day 8:** Discord notification timing, substitution fix, narration clarity, Elam display polish, SSE dedup, deploy-during-live resilience
- **Day 9:** The Floor rename, voting UX, admin veto, profiles, trades, seasons, doc updates, mirror→report rename
- **Day 10:** Production bugfixes — presentation mode, player enrollment, Discord invite URL
- **Day 11:** Discord defer/timeout fixes, get_active_season migration, playoff progression pipeline
- **Day 12:** P0 fixes — /join, score spoilers, strategy system, trade verification, substitution verification
- **Day 13:** Self-heal missing player enrollments, decouple governance from game simulation
- **Day 14:** Admin visibility, season lifecycle phases, effects system, NarrativeContext, game richness audit, SQLite write lock fix, playoff series, V2 interpreter, e2e verification, workbench
- **Day 15:** Overnight wave execution — amendments, repeal, milestones, drama pacing, effects wave 2, documentation, Discord guard, V2 tier detection, tick-based scheduling, SSE dedup, team links, playoff series banners
- **Day 16:** AI intelligence layer, Amplify Human Judgment (9 features), P0/P1 security hardening, doc reconciliation, Messages API phases 1-2, performance optimization, video demo pipeline
- **Live at:** https://pinwheel.fly.dev
- **Day 17:** Repo cleanup — excluded demo PNGs from git, showboat image fix, deployed
- **Latest commit:** `9accd69` — fix: deduplicate Discord team channel notifications

## Today's Agenda

- [ ] Reset season history for hackathon demo
- [ ] Record demo video (3-minute hackathon submission)
- [ ] Final deploy and verification

---

## Session 90 — Repo Cleanup + Deploy

**What was asked:** Complete post-commit checklist from previous session, fix showboat image bug in both demo scripts, run video demo pipeline, exclude demo PNGs from git, deploy to production.

**What was built:**
- Fixed `showboat image` calls in `run_video_demo.sh` and `run_demo.sh` — split command strings into separate `rodney screenshot` + `showboat image` calls
- Ran video demo pipeline end-to-end: 10 screenshots captured, storyboard at `demo/video_demo.md`
- Added `demo/*.png` to `.gitignore`, removed 141 PNGs from git tracking (files kept locally)
- Checked off completed dev log items (demo verification, doc reconciliation, doc alignment)
- Archived 1 new plan (agent-native-proposal-interpreter), rejected 2 non-Pinwheel plans
- Deployed to Fly.io production

**Files modified (4):** `scripts/run_demo.sh`, `scripts/run_video_demo.sh`, `.gitignore`, `docs/dev_log/DEV_LOG.md`

**1964 tests, zero lint errors.**

**What could have gone better:** The `showboat image` bug existed in both demo scripts since they were written — `showboat image` takes a file path, not a command to execute. The first video demo run failed on this; fixed and re-ran successfully.

---

## Session 91 — Post-Cleanup Code Review (Demo Readiness)

**What was asked:** Run one more code review pass and capture the remaining high-impact gaps before demo.

**What was verified in code:**
- `get_latest_round_number()` is implemented in `src/pinwheel/db/repository.py` and now used across key page handlers in `src/pinwheel/api/pages.py`.
- Security hardening remains in place: governance write API routes removed, private reports auth-gated, and centralized admin auth checks wired in admin modules.
- Legacy mirror stack remains deleted.

**Remaining gaps to close:**
1. [ ] `reports_page` still uses fixed reverse round scanning with per-round queries in `src/pinwheel/api/pages.py`.
2. [ ] Two handlers still query each prior round in loops for previous-round context instead of deriving from already-fetched game data in `src/pinwheel/api/pages.py`.
3. [ ] AI client consolidation (Messages API Phase 0) appears incomplete: several AI call sites still instantiate `anthropic.AsyncAnthropic(...)` directly (e.g., `src/pinwheel/ai/interpreter.py`).
4. [ ] Keep plan/docs sync tight for demo Q&A: deferred phases docs and shipped work should be explicitly aligned.

**Recommended next demo-safe polish:**
- Optimize the `reports_page` retrieval path first (largest remaining page-latency hotspot with minimal product-surface risk).

---

## Session 92 — Reports Upgrade to Opus + Early-Season Guard

**What was asked:** Round 1 simulation report claimed "the league is as tight as it has ever been" and "just 1 game separates" teams — meaningless after a single round. Also duplicated a game result line. Reports were using Sonnet, not Opus.

**What was built:**
- Upgraded report model from `claude-sonnet-4-5-20250929` to `claude-opus-4-6` in `_call_claude()`
- Added "Early-Season Awareness" section to all 3 report prompts (simulation, governance, private):
  - Simulation: Don't claim patterns, tight races, or trends from 1-2 games
  - Governance: Don't claim coalitions from fewer than 3 shared votes
  - Private: Don't pad with generic advice when governor has no activity
- Added Opus pricing to `usage.py` ($15/$75 per Mtok input/output)

**Files modified (3):** `src/pinwheel/ai/report.py`, `src/pinwheel/ai/usage.py`, `docs/dev_log/DEV_LOG.md`

**1964 tests, zero lint errors.**

**What could have gone better:** Reports should have been on Opus from the start — the editorial voice is the game's personality. The early-season guard is an obvious prompt gap that should have been caught during the Session 86 prompt rewrite.

---

## Session 93 — Fix Duplicate Discord Notifications

**What was asked:** "The Floor Has Spoken -- Round 2" was appearing 5 times in team channels. Investigate and fix duplicate Discord governance notifications.

**What was built:**
- **Root cause:** `_load_persisted_channel_ids()` loads ALL `channel_team_*` entries from `bot_state`, including stale entries from previous seasons with different team UUIDs. Multiple entries (e.g., `team_abc123`, `team_def456`) all pointed to the same Discord channel, so the governance handler sent the embed once per stale entry.
- **`_get_unique_team_channels()` helper** — deduplicates team channels by channel ID so each Discord channel receives at most one message, regardless of how many stale `team_*` entries exist
- **All 3 team-channel iteration sites updated** — governance (`governance.window_closed`), championship (`season.championship_started`), and memorial (`season.memorial_generated`) handlers now use the dedup helper
- **Stale entry cleanup during setup** — `_setup_server()` now prunes `team_*` entries that don't belong to the current season's teams, removing them from both `self.channel_ids` and the database via new `_persist_bot_state_delete()` method
- **2 new tests** — `test_governance_dedup_stale_team_channels` (verifies 3 stale entries only produce 1 send), `test_get_unique_team_channels_deduplicates` (unit test for the helper)

**Files modified (2):** `src/pinwheel/discord/bot.py`, `tests/test_discord.py`

**1966 tests, zero lint errors.**

**What could have gone better:** This bug existed since the first season reset — any time a new season created teams with new UUIDs, stale `channel_team_*` entries accumulated in `bot_state`. The original iteration pattern (`for key, chan_id in self.channel_ids.items() if key.startswith("team_")`) assumed a 1:1 mapping between team keys and Discord channels, which broke across seasons.
