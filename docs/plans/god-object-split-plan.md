# God Object Split Plan (P2.5)

**Date:** 2026-02-20
**Status:** Plan (no code changes)
**Priority:** P2.5

## Executive Summary

Four modules in Pinwheel Fates have grown into god objects -- single files that absorb too many responsibilities, making them hard to navigate, test in isolation, and safely modify:

| File | Lines | Responsibilities |
|------|-------|-----------------|
| `src/pinwheel/discord/bot.py` | 4,343 | 22 slash commands, event dispatch, server setup, channel management, role sync, autocomplete handlers, DM delivery |
| `src/pinwheel/api/pages.py` | 3,357 | 30+ route handlers, series context computation, streaks, what-changed signals, narrative callouts, standings helpers |
| `src/pinwheel/core/game_loop.py` | 2,635 | Game simulation, governance tallying, AI report generation, playoff bracket advancement, evaluation framework, milestone checking, dataclass definitions |
| `src/pinwheel/db/repository.py` | 1,599 | 70+ methods spanning leagues, seasons, teams, hoopers, games, box scores, governance events, reports, players, schedules, evals, bot state, archives, meta |

The splits must be **additive and non-breaking**: each extracted module re-exports its symbols from the original location via `__init__.py` or the original file, so existing imports continue to work. The original file thins out by delegating to the new modules.

---

## 1. `src/pinwheel/discord/bot.py` (4,343 lines)

### 1.1 Natural Groupings

The PinwheelBot class contains:

**A. Command Registration (~370 lines, lines 102-469)**
- `_setup_commands()` with 22 `@self.tree.command` decorators + autocomplete wiring

**B. Server Setup & Infrastructure (~600 lines, lines 470-1056)**
- `setup_hook`, `on_ready`, `on_member_join`
- `_setup_server`, `_load_persisted_channel_ids`, `_persist_bot_state`, `_persist_bot_state_delete`
- `_get_or_create_shared_channel`, `_setup_team_channel_and_role`
- `_sync_role_enrollments`, `_post_welcome_message`
- `_try_acquire_setup_lock`, `_release_setup_lock`

**C. Event Bus Dispatch (~200 lines, lines 1455-1666)**
- `_dispatch_event` with handlers for game_finished, round_finished, report.generated, governance.window_closed, championship_started, memorial_generated, phase_changed
- `_send_private_report`

**D. Governance Commands (~900 lines)**
- `_handle_propose` (lines 2080-2368)
- `_handle_amend` (lines 2370-2629)
- `_handle_vote` (lines 3049-3247)
- `_handle_effects` (lines 2631-2698)
- `_handle_repeal` (lines 2700-2830)
- `_autocomplete_proposals`, `_autocomplete_effects`

**E. Trading Commands (~370 lines)**
- `_handle_trade` (lines 3360-3479)
- `_handle_trade_hooper` (lines 3520-3682)
- `_autocomplete_hoopers`

**F. Information / Query Commands (~450 lines)**
- `_handle_standings`, `_query_standings`
- `_handle_schedule`, `_query_schedule`
- `_handle_reports`, `_query_latest_reports`
- `_handle_tokens`
- `_handle_profile`
- `_handle_roster`
- `_handle_proposals`
- `_handle_status`
- `_handle_history`
- `_handle_ask`

**G. Team Management Commands (~280 lines)**
- `_handle_join` (lines 1137-1402)
- `_handle_strategy` (lines 3684-3740)
- `_handle_bio` (lines 3742-3834)

**H. Admin Commands (~200 lines)**
- `_handle_new_season` (lines 3836-3952)
- `_handle_activate_mechanic` (lines 3954-4048)
- `_handle_edit_series` (lines 4127-4202)
- `_autocomplete_pending_mechanics`, `_autocomplete_series_reports`

**I. Channel Helpers (~40 lines)**
- `_get_channel_for`, `_get_team_channel`, `_get_unique_team_channels`, `_send_to_team_channel`

### 1.2 Proposed Module Structure

```
src/pinwheel/discord/
    __init__.py              # Unchanged — re-export PinwheelBot, is_discord_enabled, start_discord_bot
    bot.py                   # PinwheelBot class (thin), _setup_commands, setup_hook, on_ready,
                             #   on_member_join, close, channel helpers, event listener start.
                             #   Delegates to handler modules.
    setup.py                 # Server setup: channel creation, role management, lock, welcome message,
                             #   role enrollment sync. Extracted as standalone async functions
                             #   taking (bot, guild) params.
    event_dispatch.py        # _dispatch_event routing + _send_private_report.
                             #   Receives (bot, event) and posts to channels.
    handlers/
        __init__.py
        governance.py        # _handle_propose, _handle_amend, _handle_vote,
                             #   _handle_effects, _handle_repeal + autocompletes
        trading.py           # _handle_trade, _handle_trade_hooper, _autocomplete_hoopers
        info.py              # _handle_standings, _handle_schedule, _handle_reports,
                             #   _handle_tokens, _handle_profile, _handle_roster,
                             #   _handle_proposals, _handle_status, _handle_history,
                             #   _handle_ask + _query_standings, _query_schedule, _query_latest_reports
        team.py              # _handle_join, _handle_strategy, _handle_bio
        admin.py             # _handle_new_season, _handle_activate_mechanic,
                             #   _handle_edit_series + autocompletes
    embeds.py                # Unchanged
    helpers.py               # Unchanged
    views.py                 # Unchanged
```

### 1.3 Shared State / Dependencies (What Makes It Hard)

- **All handlers access `self.engine`, `self.settings`, `self.event_bus`, `self.channel_ids`**, `self._proposal_cooldowns`, `self._ask_cooldowns`, `self._team_names_cache`. These are PinwheelBot instance attributes.
- **Solution:** Each handler module defines functions that take the bot instance (or a typed protocol/dataclass with the needed attributes) as the first argument. The PinwheelBot class remains the single owner of state; handlers are pure functions of `(bot, interaction, ...)`.
- **Channel helpers** (`_get_channel_for`, `_get_team_channel`, etc.) are used by event_dispatch and some handlers. These stay on the bot class as thin wrappers, or move to a shared `channels.py` utility.
- **Setup methods** directly mutate `self.channel_ids` and call `self._persist_bot_state`. Pass the bot instance and let the setup module call these methods.

### 1.4 Import Changes

**Before:**
```python
from pinwheel.discord.bot import PinwheelBot, is_discord_enabled, start_discord_bot
```

**After (same -- re-exported from bot.py):**
```python
from pinwheel.discord.bot import PinwheelBot, is_discord_enabled, start_discord_bot
```

Internal only:
```python
# Inside PinwheelBot._setup_commands:
from pinwheel.discord.handlers.governance import handle_propose, handle_vote, ...
from pinwheel.discord.handlers.info import handle_standings, ...
```

Tests that import `_gather_season_context`, `PROPOSAL_COOLDOWN_SECONDS`, `ASK_COOLDOWN_SECONDS` continue to import from `bot.py` (these stay or are re-exported).

### 1.5 Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| Discord interaction timeout regressions | Medium | Each handler is a standalone function -- testable in isolation. Integration tests verify response times. |
| Circular imports between handlers and bot | Low | Handlers only import from `pinwheel.db`, `pinwheel.core`, `pinwheel.discord.embeds`. Bot imports handlers. No cycles. |
| Channel dispatch misrouting after extraction | Low | Event dispatch is already a clean switch statement. Extract as-is. |
| Tests break on import paths | Low | Keep re-exports from `bot.py` for all public symbols. |

---

## 2. `src/pinwheel/api/pages.py` (3,357 lines)

### 2.1 Natural Groupings

**A. Template Setup & Shared Utilities (lines 1-130)**
- Template configuration, Jinja filters (`_light_safe`, `_prose_to_html`)
- `_auth_context`, `_get_active_season_id`, `_get_active_season`
- `_get_slot_start_times`

**B. Standings & Streaks Helpers (lines 143-477, 1688-1770, 1866-1895)**
- `_get_standings`, `_get_season_phase`, `_get_game_phase`
- `_compute_streaks_from_games`, `_compute_standings_callouts`, `_ordinal_suffix`
- `_compute_game_standings`

**C. Series Context (lines 245-448)**
- `_generate_series_description`, `_build_series_description_fallback`
- `build_series_context`, `_compute_series_context_for_game`

**D. What Changed Signals (lines 479-766)**
- `_compute_what_changed` (pure function, ~290 lines)

**E. Home Page (lines 769-1134)**
- `home_page` route handler
- `what_changed_partial` HTMX partial (lines 1137-1301)

**F. Arena / Game Pages (lines 1434-2185)**
- `arena_page`
- `game_page`

**G. Team & Hooper Pages (lines 2188-2536)**
- `team_page`
- `hooper_page`
- `hooper_bio_edit_form`, `hooper_bio_view`, `update_hooper_bio`

**H. Governance & Rules Pages (lines 2538-2953)**
- `governor_profile_page`
- `governance_page`
- `_compute_rule_impact`
- `rules_page`

**I. Reports & Newspaper (lines 2955-3143)**
- `reports_page`
- `newspaper_page`

**J. Playoffs, Archives, History, Memorial (lines 3145-3318)**
- `playoffs_page`
- `season_archives_page`, `season_archive_detail`
- `history_page`
- `memorial_page`

**K. Info Pages (lines 1303-1431, 3319-3357)**
- `play_page`
- `admin_landing_page`
- `terms_page`, `privacy_page`

### 2.2 Proposed Module Structure

```
src/pinwheel/api/
    __init__.py
    pages.py                    # Thin: imports and includes all sub-routers.
                                #   Re-exports build_series_context, _compute_what_changed, etc.
                                #   for backward-compatible test imports.
    page_helpers.py             # Template config, Jinja filters, _auth_context,
                                #   _get_active_season_id, _get_active_season,
                                #   _get_standings, _get_season_phase, _get_game_phase,
                                #   _get_slot_start_times
    page_series.py              # Series context: _generate_series_description,
                                #   _build_series_description_fallback,
                                #   build_series_context, _compute_series_context_for_game
    page_signals.py             # _compute_what_changed, _compute_streaks_from_games,
                                #   _compute_standings_callouts, _compute_game_standings,
                                #   _ordinal_suffix
    pages/
        __init__.py
        home.py                 # home_page, what_changed_partial
        arena.py                # arena_page
        game.py                 # game_page
        standings.py            # standings_page
        team.py                 # team_page, hooper_page, hooper_bio_*, update_hooper_bio
        governance.py           # governance_page, governor_profile_page, rules_page
        reports.py              # reports_page, newspaper_page
        history.py              # playoffs_page, season_archives_page, season_archive_detail,
                                #   history_page, memorial_page
        info.py                 # play_page, admin_landing_page, terms_page, privacy_page
    deps.py                     # Unchanged
    charts.py                   # Unchanged
    events.py                   # Unchanged
    games.py                    # Unchanged
    governance.py               # Unchanged (API routes)
    reports.py                  # Unchanged (API routes)
    standings.py                # Unchanged (API routes)
    teams.py                    # Unchanged (API routes)
```

### 2.3 Shared State / Dependencies

- **`templates` object** (Jinja2Templates) and **`router`** (APIRouter) must be importable by all sub-modules.
  - **Solution:** `page_helpers.py` owns the `templates` object and `_auth_context`. Each page sub-module creates its own `router = APIRouter()`, and `pages.py` includes all of them via `router.include_router(...)`.
- **Helper functions** like `_get_standings`, `_get_season_phase` are used across home, arena, standings, game, and team pages.
  - **Solution:** These live in `page_helpers.py`, imported by each page module.
- **`_compute_what_changed`** is a pure function (no DB access) used by `home_page` and `what_changed_partial`, plus tested extensively.
  - **Solution:** Lives in `page_signals.py`. Tests import from there (with a re-export in `pages.py` for backward compat).

### 2.4 Import Changes

**Before:**
```python
from pinwheel.api.pages import router as pages_router
from pinwheel.api.pages import _compute_what_changed, build_series_context
```

**After:**
```python
# main.py -- unchanged, pages.py re-exports the composite router
from pinwheel.api.pages import router as pages_router

# Tests -- either path works (re-exported for backward compat)
from pinwheel.api.pages import _compute_what_changed
from pinwheel.api.page_signals import _compute_what_changed  # new canonical path
```

### 2.5 Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| 50+ test imports break | Medium | Re-export all previously-public symbols from `pages.py`. Deprecation warnings optional. |
| Template path resolution changes | Low | `templates` object is created once in `page_helpers.py` and imported everywhere. |
| `router` registration order changes behavior | Low | FastAPI routers are order-independent for unique paths. All paths are unique. |
| Cross-page circular imports (e.g., arena imports standings helpers) | Low | All shared helpers in `page_helpers.py` -- no cross-page imports needed. |

---

## 3. `src/pinwheel/core/game_loop.py` (2,635 lines)

### 3.1 Natural Groupings

**A. Data Conversion Helpers (lines 77-107)**
- `_row_to_team`

**B. Milestone Checking (lines 109-169)**
- `_check_earned_moves`

**C. Season Completion Checks (lines 172-186)**
- `_check_season_complete`

**D. Playoff Series Management (lines 188-551)**
- `_series_wins_needed`
- `_get_playoff_series_record`
- `_schedule_next_series_game`
- `_advance_playoff_series` (~300 lines)

**E. Standings & Bracket Generation (lines 552-668)**
- `compute_standings_from_repo`
- `generate_playoff_bracket`

**F. Evaluation Framework (lines 670-794)**
- `_run_evals` (~125 lines)

**G. Governance Tallying (lines 795-1002)**
- `tally_pending_governance` (~210 lines)

**H. Simulation & Governance Phase (lines 1003-1594)**
- `_phase_simulate_and_govern` (~590 lines -- the largest single function)

**I. AI Phase (lines 1595-1982)**
- `_phase_ai` (~390 lines -- report generation, commentary, highlights, insights)

**J. Series Report Generation (lines 1983-2214)**
- `_get_series_games`, `_generate_series_reports` (~230 lines)

**K. Persist & Finalize Phase (lines 2215-2575)**
- `_phase_persist_and_finalize` (~360 lines)

**L. Entry Points (lines 2576-2682)**
- `step_round` (single-session)
- `step_round_multisession` (multi-session for scheduler)

**M. Dataclasses (lines 2683-end)**
- `RoundResult`, `_SimPhaseResult`, `_AIPhaseResult`

### 3.2 Proposed Module Structure

```
src/pinwheel/core/
    game_loop.py                # Thin orchestrator: step_round, step_round_multisession,
                                #   RoundResult, _SimPhaseResult, _AIPhaseResult.
                                #   Re-exports all public symbols for backward compat.
    game_loop_sim.py            # _phase_simulate_and_govern + helpers:
                                #   _row_to_team, _check_earned_moves
    game_loop_ai.py             # _phase_ai, _get_series_games, _generate_series_reports
    game_loop_persist.py        # _phase_persist_and_finalize
    game_loop_playoffs.py       # _series_wins_needed, _get_playoff_series_record,
                                #   _schedule_next_series_game, _advance_playoff_series,
                                #   generate_playoff_bracket, compute_standings_from_repo,
                                #   _check_season_complete
    game_loop_governance.py     # tally_pending_governance
    game_loop_evals.py          # _run_evals
```

### 3.3 Shared State / Dependencies

- **`_row_to_team`** is used by `_phase_simulate_and_govern` and `_phase_ai`. Also imported by tests. Move to `game_loop_sim.py`, re-export from `game_loop.py`.
- **`_get_playoff_series_record`** is used internally AND by `pages.py` (imported at runtime inside a function). Move to `game_loop_playoffs.py`, re-export from `game_loop.py`.
- **Phase functions pass large result dataclasses** (`_SimPhaseResult`, `_AIPhaseResult`) between them. These dataclasses must be importable by all phase modules.
  - **Solution:** Dataclasses stay in `game_loop.py` (the orchestrator) or move to a tiny `game_loop_types.py`.
- **`tally_pending_governance`** is imported by `core/season.py` and `core/scheduler_runner.py` at runtime. Re-export from `game_loop.py`.
- **`step_round` and `step_round_multisession`** are the public API. They stay in `game_loop.py` and call the phase functions.

### 3.4 Import Changes

**Before:**
```python
from pinwheel.core.game_loop import step_round, tally_pending_governance, _row_to_team
from pinwheel.core.game_loop import _get_playoff_series_record
```

**After (same -- re-exported from game_loop.py):**
```python
from pinwheel.core.game_loop import step_round, tally_pending_governance, _row_to_team
```

New canonical paths (for new code):
```python
from pinwheel.core.game_loop_playoffs import _get_playoff_series_record
from pinwheel.core.game_loop_governance import tally_pending_governance
```

### 3.5 Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| Phase-boundary bugs (data not passed correctly between phases) | High | Dataclass contracts enforce the handoff. Existing integration tests cover the full pipeline. |
| Circular imports between phase modules | Medium | Phase modules import from `pinwheel.core`, `pinwheel.ai`, `pinwheel.db`, `pinwheel.models` -- never from each other. Orchestrator imports phase modules. |
| `tally_pending_governance` has runtime imports from `core/season.py` which also imports from `game_loop` | Medium | Already uses runtime imports to break cycle. Keep that pattern. |
| `_phase_simulate_and_govern` is 590 lines of sequential logic | Low | Extract as-is first. Internal refactoring (e.g., splitting sim from gov) is a follow-up. |

---

## 4. `src/pinwheel/db/repository.py` (1,599 lines)

### 4.1 Natural Groupings (by Entity)

**A. League & Season (lines 36-131)**
- `create_league`, `get_league`
- `create_season`, `get_season`, `get_active_season`, `get_latest_completed_season`, `get_all_seasons`
- `get_all_players`, `get_players_for_season`

**B. Teams & Hoopers (lines 133-209)**
- `create_team`, `get_team`, `get_teams_for_season`
- `create_hooper`, `create_agent`, `get_hooper`, `get_agent`, `get_hoopers_for_team`

**C. Game Results & Box Scores (lines 211-408)**
- `store_game_result`, `store_box_score`
- `get_game_result`, `get_games_for_round`, `get_latest_round_number`
- `mark_game_presented`
- `get_game_stats_for_rounds`, `get_avg_total_game_score_for_rounds`

**D. Governance Events (lines 410-733)**
- `append_event`
- `get_events_for_aggregate`, `get_events_by_type_and_governor`, `get_events_by_type`, `get_events_by_governor`
- `get_governor_activity`
- `get_all_proposals`

**E. Season Config & Schedule (lines 735-807)**
- `update_season_ruleset`
- `create_schedule_entry`, `get_schedule_for_round`, `get_full_schedule`

**F. Reports (lines 809-928)**
- `store_report`, `get_reports_for_round`, `get_private_reports`
- `update_report_content`, `get_series_reports`, `get_latest_report`
- `get_public_reports_for_season`

**G. Players & Enrollment (lines 930-1073)**
- `get_player`, `get_player_by_discord_id`, `get_or_create_player`
- `enroll_player`, `get_players_for_team`, `swap_hooper_team`
- `get_governors_for_team`, `get_governor_counts_by_team`, `get_player_enrollment`

**H. Stats & Queries (lines 1075-1327)**
- `get_stat_leaders`, `get_head_to_head`, `get_games_for_team`
- `get_team_game_results`, `get_box_scores_for_hooper`
- `get_league_attribute_averages`
- `update_hooper_backstory`, `get_hooper_season_stats`, `add_hooper_move`

**I. Evals & Bot State (lines 1329-1389)**
- `store_eval_result`, `get_eval_results`
- `get_bot_state`, `set_bot_state`

**J. Aggregate Queries (lines 1386-1509)**
- `get_all_games`, `get_all_governors_for_season`
- `update_season_status`, `get_season_row`
- `store_season_archive`, `get_season_archive`, `get_all_archives`
- `update_team_meta`, `update_hooper_meta`, `update_season_meta`, `update_game_result_meta`, `update_player_meta`
- `flush_meta_store`
- `get_rule_change_timeline`

**K. Meta Store Loaders (lines 1602-end)**
- `load_team_meta`, `load_all_team_meta`, `load_hooper_meta`, `load_hoopers_meta_for_teams`

### 4.2 Proposed Module Structure

Use **mixin classes** -- this is the cleanest pattern for splitting a single class that shares a `self.session` across all methods:

```
src/pinwheel/db/
    __init__.py
    repository.py               # Repository class inherits from all mixins.
                                #   from pinwheel.db.repo_season import SeasonRepoMixin
                                #   from pinwheel.db.repo_team import TeamRepoMixin
                                #   ...
                                #   class Repository(SeasonRepoMixin, TeamRepoMixin, ...):
                                #       def __init__(self, session): self.session = session
    repo_season.py              # SeasonRepoMixin: league, season, players-for-season queries
    repo_team.py                # TeamRepoMixin: team, hooper CRUD
    repo_game.py                # GameRepoMixin: game results, box scores, game stats
    repo_governance.py          # GovernanceRepoMixin: events, proposals, governor activity
    repo_schedule.py            # ScheduleRepoMixin: schedule entries, season ruleset
    repo_report.py              # ReportRepoMixin: reports, series reports
    repo_player.py              # PlayerRepoMixin: player CRUD, enrollment, governor queries
    repo_stats.py               # StatsRepoMixin: stat leaders, h2h, team results, hooper stats,
                                #   league averages, hooper moves
    repo_admin.py               # AdminRepoMixin: evals, bot state, archives, meta store,
                                #   rule change timeline
    engine.py                   # Unchanged
    models.py                   # Unchanged
```

### 4.3 Shared State / Dependencies

- **Every method uses `self.session`**. The mixin pattern handles this naturally -- all mixins operate on `self.session` which is set by Repository.__init__.
- **No cross-method dependencies within the repository.** Methods are independent -- they query the DB and return data. No method calls another repository method (except `create_agent` aliasing `create_hooper`).
- **All imports use `from pinwheel.db.repository import Repository`**. This path stays the same -- the Repository class still lives in `repository.py`, just composed from mixins.

### 4.4 Import Changes

**No external import changes required.** The Repository class remains in `repository.py`. The mixins are an internal implementation detail.

### 4.5 Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| Mixin MRO (method resolution order) issues | Low | Mixins have no overlapping method names. Python MRO handles this cleanly. |
| IDE autocomplete degrades for Repository | Low | Type stubs or explicit `__all__` in repository.py. Most IDEs follow mixin inheritance fine. |
| Accidentally breaking the class hierarchy | Low | One test that asserts `isinstance(repo, Repository)` and `hasattr(repo, method_name)` for all 70+ methods. |
| Import cycle between mixins | None | Mixins import only from `pinwheel.db.models` and `sqlalchemy`. No cross-mixin imports. |

---

## 5. Dependency Graph Between Proposed Modules

```
                    +-----------------+
                    |   main.py       |
                    +--------+--------+
                             |
              +--------------+--------------+
              |                             |
     +--------v--------+         +---------v---------+
     | discord/bot.py   |         | api/pages.py      |
     | (orchestrator)   |         | (includes sub-     |
     +--------+---------+         |  routers)          |
              |                   +---------+----------+
     +--------+--------+                   |
     | handlers/        |         +---------+---------+
     |  governance.py   |         | pages/home.py     |
     |  trading.py      |         | pages/arena.py    |
     |  info.py         |         | pages/game.py     |
     |  team.py         |         | pages/standings.py|
     |  admin.py        |         | pages/team.py     |
     +--------+---------+         | pages/governance.py|
              |                   | pages/reports.py  |
     +--------v--------+         | pages/history.py  |
     | discord/setup.py |         | pages/info.py     |
     | discord/         |         +---------+---------+
     |  event_dispatch  |                   |
     +---------+--------+         +---------v---------+
               |                  | page_helpers.py    |
               |                  | page_signals.py    |
               |                  | page_series.py     |
               |                  +--------------------+
               |
     +---------v------------------------------------------+
     |              core/game_loop.py (orchestrator)       |
     +--------+--------+--------+--------+--------+-------+
              |        |        |        |        |
     +--------v---+ +--v-----+ +--v----+ +--v---+ +v------+
     |game_loop   | |game_   | |game_  | |game_ | |game_  |
     |_sim.py     | |loop_   | |loop_  | |loop_ | |loop_  |
     |            | |ai.py   | |persist| |play  | |gov.py |
     +------------+ +--------+ |.py    | |offs  | +-------+
                                +------+ |.py   |
                                         +------+
     +---------------------------------------------------+
     |         db/repository.py (composed of mixins)      |
     +-----+-----+------+------+------+------+-----+-----+
           |     |      |      |      |      |     |
    repo_  repo_ repo_  repo_  repo_  repo_  repo_ repo_
    season team  game   gov    sched  report player admin
    .py    .py   .py    .py    .py    .py    .py    .py
```

---

## 6. Risk / Effort Matrix

| Split | Effort | Risk | Lines Moved | Import Surface |
|-------|--------|------|-------------|----------------|
| **repository.py** (mixins) | Low | Low | ~1,500 | 0 external changes |
| **pages.py** (sub-routers) | Medium | Medium | ~3,200 | ~50 test imports need re-export |
| **bot.py** (handlers) | Medium | Medium | ~3,900 | ~10 test imports need re-export |
| **game_loop.py** (phases) | High | High | ~2,500 | ~40 imports across src + tests |

---

## 7. Recommended Execution Order

### Phase 1: Repository Mixins (Safest, Low Risk)

**Why first:** Zero external import changes. The Repository class stays in `repository.py`. Mixins are purely internal. If anything goes wrong, revert is trivial.

**Steps:**
1. Create `repo_season.py`, `repo_team.py`, etc. with mixin classes.
2. Move methods from Repository into the appropriate mixin.
3. Change Repository to inherit from all mixins.
4. Run `uv run pytest -x -q`. All tests pass with no import changes.
5. Commit.

**Acceptance Criteria:**
- [ ] All 70+ Repository methods still accessible via `repo.method_name()`
- [ ] `isinstance(repo, Repository)` still True
- [ ] No test file changes required
- [ ] `uv run pytest -x -q` passes
- [ ] `uv run ruff check src/ tests/` passes
- [ ] Each mixin file is under 250 lines

### Phase 2: Pages Sub-Routers (Medium Risk)

**Why second:** Pages are the most-tested module after the game loop. The router composition pattern is well-established in FastAPI. Risk is limited to import path changes.

**Steps:**
1. Create `page_helpers.py` with template config, filters, and shared helpers.
2. Create `page_signals.py`, `page_series.py` with pure functions.
3. Create `pages/` subdirectory with one module per page group.
4. Each page module creates `router = APIRouter()` and registers its routes.
5. `pages.py` becomes a thin aggregator: imports all sub-routers, includes them into a single `router`, and re-exports all symbols that tests import.
6. Run tests. Fix any import issues.
7. Commit.

**Acceptance Criteria:**
- [ ] `from pinwheel.api.pages import router as pages_router` still works
- [ ] All test imports from `pages.py` still work (via re-exports)
- [ ] All page routes serve identical HTML
- [ ] `uv run pytest -x -q` passes
- [ ] `uv run ruff check src/ tests/` passes
- [ ] No page module exceeds 500 lines
- [ ] `page_signals.py` and `page_series.py` are testable without a running server

### Phase 3: Bot Handlers (Medium Risk)

**Why third:** The bot is harder to test (requires Discord mocks) but has fewer external consumers than the game loop. Handler extraction follows the same pattern as pages.

**Steps:**
1. Create `discord/handlers/` directory with governance.py, trading.py, info.py, team.py, admin.py.
2. Extract handler methods as standalone async functions: `async def handle_propose(bot: PinwheelBot, interaction: discord.Interaction, text: str) -> None`.
3. Create `discord/setup.py` with server setup logic.
4. Create `discord/event_dispatch.py` with event routing.
5. Thin PinwheelBot: `_setup_commands` wires to handler functions, `_dispatch_event` delegates to event_dispatch module.
6. Re-export public symbols from `bot.py`.
7. Run tests.
8. Commit.

**Acceptance Criteria:**
- [ ] `from pinwheel.discord.bot import PinwheelBot, is_discord_enabled, start_discord_bot` still works
- [ ] All slash commands function identically
- [ ] Event dispatch posts to correct channels
- [ ] Server setup creates channels/roles correctly
- [ ] `uv run pytest -x -q` passes
- [ ] `uv run ruff check src/ tests/` passes
- [ ] No handler file exceeds 600 lines
- [ ] PinwheelBot class is under 300 lines

### Phase 4: Game Loop Phases (Highest Risk, Do Last)

**Why last:** The game loop is the most imported module, has the most complex inter-phase dependencies, and is the hardest to test in isolation. The other three splits reduce overall codebase complexity first, making this split easier to reason about.

**Steps:**
1. Create `game_loop_playoffs.py` first (most self-contained -- series record, bracket generation).
2. Create `game_loop_governance.py` with `tally_pending_governance`.
3. Create `game_loop_evals.py` with `_run_evals`.
4. Create `game_loop_sim.py` with `_phase_simulate_and_govern` + `_row_to_team` + `_check_earned_moves`.
5. Create `game_loop_ai.py` with `_phase_ai` + series report generation.
6. Create `game_loop_persist.py` with `_phase_persist_and_finalize`.
7. `game_loop.py` becomes the orchestrator: imports phase modules, defines dataclasses, provides `step_round` / `step_round_multisession` entry points.
8. Re-export all public symbols from `game_loop.py`.
9. Run the full test suite. Run `demo_seed.py step 3` to verify end-to-end.
10. Commit.

**Acceptance Criteria:**
- [ ] `from pinwheel.core.game_loop import step_round, tally_pending_governance` still works
- [ ] `from pinwheel.core.game_loop import _row_to_team, _get_playoff_series_record` still works
- [ ] `step_round` produces identical game results (deterministic seed verification)
- [ ] Playoff bracket advancement works end-to-end
- [ ] AI reports generate correctly
- [ ] Governance tallying works correctly
- [ ] `uv run pytest -x -q` passes
- [ ] `uv run ruff check src/ tests/` passes
- [ ] No phase file exceeds 700 lines
- [ ] `game_loop.py` orchestrator is under 200 lines

---

## 8. General Principles for All Splits

1. **Re-export everything.** The original module must re-export all symbols that any external code imports. Test files should not need changes during the split.

2. **Move code, don't rewrite.** Each extraction should be a pure move -- cut from the original, paste into the new file, add the import. No logic changes during the split.

3. **One PR per phase.** Each phase is a separate PR. Do not combine phases. This keeps reviews manageable and reverts clean.

4. **Test before and after.** Run `uv run pytest -x -q` before starting each phase (baseline) and after (verification). If any test fails, the split introduced a regression.

5. **No functional changes.** The split is a refactoring exercise. No bug fixes, no feature additions, no behavioral changes. If you find a bug during the split, file it and fix it in a separate commit.

6. **Commit the re-export shims permanently.** These are not temporary -- they prevent churn across the codebase. New code should use the canonical (new) import paths; existing code continues to work via re-exports.
