# Plan: Fix Mock Simulation Report Writing Quality

## Context

The mock simulation report (`generate_simulation_report_mock` in `src/pinwheel/ai/report.py`) produces generic, always-true platitudes instead of specific observations. User feedback with example:

> "SEMIFINAL PLAYOFFS — win or go home. The pressure of elimination hangs over every possession. Hawthorne Hammers dominated Burnside Breakers by 19 in a semifinal rout. Burnside Breakers's season ends in decisive fashion. The courts ran hot — 117 points per game on average. Defenses are struggling or offenses are evolving. Maybe both. Rose City Thorns are riding a 4-game win streak. St. Johns Herons have lost 3 straight. Every game from here on out is elimination basketball. Steel Voss (Hawthorne Hammers) is on fire with 21 points. Ember Kine (Hawthorne Hammers) is on fire with 30 points."

User's critique: "Good writing is specific, timely, and surprising."

Specific problems:
1. **"win or go home"** — always true of playoffs, adds nothing
2. **"pressure of elimination"** — contradicted by the 19-point blowout that follows
3. **"season ends in decisive fashion"** — mock doesn't know if the series is over (no series state data)
4. **"The courts ran hot — 117 PPG"** — fires whenever avg >= 60, which is basically always in 3v3
5. **"Every game from here on out is elimination basketball"** — false for best-of series, generic
6. **"is on fire with N points"** — generic superlative disconnected from the game narrative

## Data Available to the Mock

The mock receives:
- `round_data["games"]` — list of game summaries with `home_team`, `away_team`, `home_score`, `away_score`, `winner_team_id`, `playoff_context` ("semifinal"/"finals")
- `narrative: NarrativeContext` — phase, season_arc, standings, streaks, hot_players, rule_changes, etc.

**NOT available:** series state (best_of, games played in series, series record). The mock cannot claim elimination, clinching, or "season ends."

## Changes

### 1. Remove the generic playoff openers (lines 361-371)
**Current:** Static strings like "win or go home" and "pressure of elimination" for all semis.
**Fix:** No separate playoff opener paragraph. Let the game results speak. The phase context is established by the lead game description (e.g., "In the semifinal, Hammers dismantled Breakers 65-46.")

### 2. Rewrite blowout descriptions for playoffs (lines 415-428)
**Current:** `"{lo}'s season ends in decisive fashion"` — claims elimination the mock can't verify.
**Fix:** Describe the blowout specifically. Integrate the phase into the game description:
- Semifinal: `"{w} rolled through {lo} in the semifinal — {ws}-{ls}. The {m}-point margin tells you everything."`
- Finals: `"{w} seized control of the championship with a {m}-point dismantling of {lo}, {ws}-{ls}."`
- No claims about season ending or elimination.

### 3. Fix the scoring pace section (lines 437-489)
**Current:** "The courts ran hot" fires at avg >= 60 (always true in 3v3). Generic filler.
**Fix:**
- **During playoffs:** Skip scoring pace entirely. Playoff reports should focus on results and stakes, not meta-commentary about pace.
- **During regular season:** Raise thresholds substantially (avg >= 80 = hot, avg <= 35 = cold) and make observations game-specific: reference the highest-scoring and lowest-scoring games by name.

### 4. Remove "Every game from here on out is elimination basketball" (line 545)
**Current:** Always fires for `season_arc == "playoff"`. False and generic.
**Fix:** Delete this line. Replace with nothing — the playoff context is already established by the game descriptions.

### 5. Rewrite hot player mentions (lines 550-557)
**Current:** `"{name} ({team}) is on fire with {pts} points."` — disconnected from context.
**Fix:** Connect hot players to the game they played in. Cross-reference `hot_players` with the game summaries to say something like:
- `"{name} led {team}'s {ws}-{ls} win with {pts} points."` (on the winning team)
- `"{name} put up {pts} for {team} in a losing effort."` (on the losing team)
- `"{name} poured in {pts} to power {team}'s semifinal rout."` (playoff with blowout)

### 6. Connect streaks to current results (lines 510-530)
**Current:** Streaks are appended as disconnected facts.
**Fix:** Weave streaks into the game descriptions when the team played this round:
- If a winning team has a 4-game streak: `"That's 4 straight for {team} now."`
- If a losing team has lost 3 straight: `"{team} have now dropped 3 in a row."`
- Only mention streaks for teams that played THIS round.

### 7. General structure improvement
Rewrite the mock report builder to follow a narrative arc instead of a grab bag:
1. **Lead:** The most dramatic result (closest game OR biggest upset)
2. **Secondary:** Other game results with context
3. **Thread:** One forward-looking observation (streak, standings shift, rule change)

Each game gets ONE sentence that includes score, teams, and relevant context. No atmosphere padding.

## File Changes

- `src/pinwheel/ai/report.py` — Rewrite `generate_simulation_report_mock()` (~lines 310-564)

## Test Assertions to Preserve

These tests assert against mock report output and must continue to pass:

**tests/test_reports.py:**
- `test_basic_generation` — team name in content, len > 20
- `test_close_game_narrative` — team name in content
- `test_no_games` — non-empty content
- `test_blowout_narrative` — at least one team name in content

**tests/test_narrative.py:**
- `test_sim_report_includes_streaks` — "5-game win streak" in content (wording may change to "5 straight" etc. — update test if needed)
- `test_sim_report_includes_late_season` — "winding down" in content
- `test_reports_work_without_narrative` — content exists

**tests/test_commentary.py:**
- Semifinal report contains "semifinal" or "playoff" (lowercase)
- Finals report contains "championship" or "finals" (lowercase)
- Regular season report does NOT contain "semifinal"
- Hot player name + points value appear in content
- Late season mentions "winding down"

## Verification

1. `uv run pytest -x -q` — all tests pass
2. `uv run ruff check src/ tests/` — clean
3. Manual check: seed a season, step through playoffs, read the mock reports — they should be specific to what happened, not generic atmosphere
