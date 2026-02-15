# Plan: Playoff Bracket and Seeding

**Date:** 2026-02-14
**Status:** Draft (documents current behavior + identifies gaps)

## Current Implementation

The playoff system is fully implemented across `game_loop.py`, `season.py`, and `scheduler.py`. Here is how it works end-to-end.

### Regular Season Completion Detection

**File:** `src/pinwheel/core/game_loop.py` -- `_check_season_complete()`

After each round, the game loop checks whether all scheduled regular-season games have been played by comparing the set of round numbers in the `phase="regular"` schedule against round numbers with stored game results. Returns `True` when every scheduled round has at least one played game.

### Tiebreaker Resolution

**File:** `src/pinwheel/core/season.py` -- `check_and_handle_tiebreakers()`

When the regular season ends, the system checks for ties at the playoff cutoff boundary. The tiebreaker order is:

1. **Head-to-head record** between tied teams
2. **Point differential** (higher is better)
3. **Points scored** (higher is better)

If all three criteria are identical between two or more teams straddling the playoff cutoff, the season transitions to `TIEBREAKERS` phase and tiebreaker games are scheduled (`phase="tiebreaker"`). Once tiebreaker games are played, the system transitions to `PLAYOFFS`.

If ties can be resolved without games, the season skips directly to `PLAYOFFS`.

### Bracket Generation

**File:** `src/pinwheel/core/game_loop.py` -- `generate_playoff_bracket()`

Called after the regular season completes and tiebreakers (if any) are resolved.

**4-team bracket (standard):**
- Semifinal 1: #1 seed vs #4 seed (higher seed = home)
- Semifinal 2: #2 seed vs #3 seed (higher seed = home)
- Finals: Winners of semis play each other (matchup created when semis conclude)
- A finals placeholder (`home_team_id="TBD"`) is added to the bracket return value but NOT stored in the database.

**2-team bracket (fallback):**
- Direct finals: #1 seed vs #2 seed

**3-team bracket:** Not explicitly handled. If `len(playoff_teams) == 3`, it falls through to the 2-team case (only top 2 make it). This is governed by `RuleSet.playoff_teams` (default: 4, range: 2-8).

Seeding is determined by `compute_standings()` in `scheduler.py`, which sorts teams by wins descending, then point differential descending.

### Series Format

Controlled by `RuleSet` parameters:
- `playoff_semis_best_of`: default 3, range 1-7
- `playoff_finals_best_of`: default 5, range 1-7

### Series Advancement

**File:** `src/pinwheel/core/game_loop.py` -- `_advance_playoff_series()`

Called in `_phase_persist_and_finalize()` when `season.status` is `"regular_season_complete"` or `"playoffs"`. After each round of playoff games:

1. **Semi series check:** For each semifinal matchup, counts wins for each team across all playoff rounds. If a team reaches `wins_needed = (best_of + 1) // 2`, that team is the semi winner.
2. **Finals creation:** When both semi series are decided, creates a finals schedule entry for the next round. Winner of semi 0 is home (higher overall seed bracket).
3. **Finals series check:** Same win-counting logic. When a team clinches, calls `enter_championship()`.
4. **Home court alternation:** Higher seed has home court in odd-numbered games (1, 3, 5...). `_schedule_next_series_game()` uses `games_played % 2` to determine home/away.

### Championship Phase

**File:** `src/pinwheel/core/season.py` -- `enter_championship()`

When the finals are decided:
1. Season transitions to `CHAMPIONSHIP` phase.
2. Awards are computed (MVP, Defensive Player, Most Efficient, Most Active Governor, Coalition Builder, Rule Architect).
3. Championship config is stored on the season (`season.config`).
4. `season.championship_started` event is published.

### Playoff Context in Simulation

The game loop detects whether current games are semifinal or finals by comparing the current round's team pairs against the initial playoff round's team pairs. This context is passed to:
- `simulate_game()` (no gameplay effect, just metadata)
- `generate_game_commentary()` / `generate_game_commentary_mock()` (dramatic commentary)
- `generate_highlight_reel()` / `generate_highlight_reel_mock()` (playoff framing)
- `generate_simulation_report()` / `generate_simulation_report_mock()` (narrative context)

### Event Bus Events During Playoffs

| Event | When |
|-------|------|
| `season.regular_season_complete` | All regular-season rounds played |
| `season.tiebreaker_games_generated` | Tiebreaker games scheduled |
| `season.phase_changed` | Any phase transition |
| `season.semifinals_complete` | Both semi series decided, finals created |
| `season.playoffs_complete` | Champion determined |
| `season.championship_started` | Championship phase entered with awards |

## Identified Gaps

### 1. No Bracket Visualization

There is no web page or Discord embed showing the playoff bracket visually. Players can only see standings and individual game results. A bracket page showing:
- Semi matchups with series scores
- Finals matchup with series score
- Champion highlight

Would significantly improve the playoff experience.

**Recommendation:** Add a `GET /playoffs` page that renders the bracket from schedule + game data. Template: `templates/pages/playoffs.html`.

### 2. Bracket Size > 4 Not Supported

`RuleSet.playoff_teams` allows values up to 8, but `generate_playoff_bracket()` only handles 4-team and 2-team brackets. An 8-team bracket would need:
- Quarterfinals: 4 matchups
- Semifinals: 2 matchups
- Finals: 1 matchup

`_advance_playoff_series()` identifies semi vs. finals by checking if current matchup pairs appear in the "initial" playoff round. This logic would break with quarterfinals because the initial round would have 4 matchups, and the semifinals (which also have 2 matchups) would not be in the initial set -- they would be correctly detected as "finals" when they are actually semis.

**Recommendation:** If the league grows to need 8-team brackets, refactor `_advance_playoff_series()` to track bracket rounds explicitly rather than inferring from team pair presence. Store `playoff_round_label` on each schedule entry.

### 3. No Bye Support in Playoffs

If `playoff_teams=3` or `playoff_teams=5-7`, there is no bye logic. Top seeds should get a bye to the next round.

**Recommendation:** Defer until the league actually has more than 4 teams. The current 4-team bracket is correct.

### 4. Reseeding After Semis

Currently, the semi 0 winner (from the #1 vs #4 matchup) is always home in the finals. There is no reseeding where the higher overall seed from the two semi winners gets home court.

**Recommendation:** Minor enhancement. Store seed information on schedule entries and reseed for finals.

### 5. Series Record Not Exposed in API

`_get_playoff_series_record()` computes wins per team in a series but this information is not exposed via any API endpoint. Players cannot see "Series: Team A leads 2-1" without looking at individual game results.

**Recommendation:** Add a `/api/playoffs/bracket` endpoint that returns structured bracket data including series records.

### 6. Governance During Playoffs

Governance tallying continues during playoffs (governed by `governance_interval`). This is intentional -- rule changes during playoffs add dramatic tension. However, there is no documentation or player-facing indication that this is the case.

**Recommendation:** Document in `RUN_OF_PLAY.md` and surface in governance report narrative.

## Implementation Priority

| Gap | Priority | Effort | Impact |
|-----|----------|--------|--------|
| Bracket visualization page | High | Medium | Players need to see the bracket |
| Series record in API | High | Low | Simple data exposure |
| Governance during playoffs docs | Medium | Low | Player clarity |
| Bracket size > 4 | Low | High | Not needed until league grows |
| Bye support | Low | Medium | Not needed until > 4 teams |
| Reseeding after semis | Low | Low | Minor fairness improvement |
