# Plan: Make the Reporter Smarter — Bedrock Facts + Season Memory

## Context

The AI reporter (The Pinwheel Post) hallucinates league facts. Examples from production:
- Says a team is "going in 2-1" when the series was 2-0 before the game (confuses pre/post-game record)
- Invents playoff "byes" — Pinwheel has no byes
- General basketball knowledge fills gaps where Pinwheel-specific facts should be

Root causes: (1) no structural league facts in prompts, (2) head-to-head conflates regular season + playoffs, (3) NarrativeContext doesn't carry playoff series records, (4) zero prior-season context is ever provided to the reporter.

Also: switch all AI models to `claude-sonnet-4-6` per user request.

---

## Changes

### 1. Add bedrock facts and playoff series to NarrativeContext

**File: `src/pinwheel/core/narrative.py`**

- Add two new fields to `NarrativeContext`: `bedrock_facts: str` and `playoff_series: dict`
- Add `_build_bedrock_facts(ruleset_values) -> str` — ~8 lines of verified structural facts:
  - Team count, hoopers per team (3v3)
  - "No byes — every team plays every round"
  - Semifinal/finals series format with wins-needed
  - Elam Ending trigger conditions
  - Current scoring values, quarter length, shot clock
- Add optional `ruleset: RuleSet | None = None` param to `compute_narrative_context()` signature
- At end of function, if ruleset provided, compute and set `ctx.bedrock_facts`

### 2. Fix head-to-head to separate regular season from playoff series

**File: `src/pinwheel/core/narrative.py`**

- Add `phase_filter: str | None = None` param to `_compute_head_to_head()` — when set, only counts games matching that phase
- In `compute_narrative_context()` matchup section: during playoffs, also compute playoff-only series records into `ctx.playoff_series` dict with keys: `home_wins`, `away_wins`, `best_of`, `wins_needed`, `phase_label`, `description`
- Uses `getattr(g, "phase", None)` to filter — games without phase (old data) are excluded from playoff filter, which is correct

### 3. Add prior-season memory to NarrativeContext

**File: `src/pinwheel/core/narrative.py`**

**The problem:** The reporter gets zero prior-season context. Season archives exist in `SeasonArchiveRow` (`db/models.py:284`) with rich data — champion team name, final standings, total games/proposals/rule changes, governor count, rule timeline, and AI-generated memorial narratives (`season_narrative`, `championship_recap`, `governance_legacy`). But none of this is ever queried during report generation. So the reporter invents history or ignores it entirely.

**What's already stored per season archive:**
- `season_name`, `champion_team_name`, `champion_team_id`
- `final_standings` (JSON), `final_ruleset` (JSON)
- `rule_change_history` (JSON list)
- `total_games`, `total_proposals`, `total_rule_changes`, `governor_count`
- `memorial` (JSON dict with `season_narrative`, `championship_recap`, `governance_legacy`, `awards`, `key_moments`, `rule_timeline`)

**What to surface to the reporter:**
- Add new field: `prior_seasons: list[dict[str, object]]` — lightweight summaries of completed seasons
- In `compute_narrative_context()`, query `repo.get_all_archives()` (at most 3, newest first) and extract per season:
  - `season_name` — e.g. "Season 1: Genesis"
  - `champion_team_name` — e.g. "Rose City Thorns"
  - `total_games`, `total_rule_changes`
  - `governance_legacy` excerpt (first ~100 chars from `memorial.governance_legacy`) — gives the reporter a flavor of what governance looked like
  - `notable_rules` — top 2-3 rule changes from `rule_change_history` (parameter + value)
- Token cost: ~60-80 tokens per prior season, max ~240 tokens for 3 seasons. Worth it for real history vs hallucinated history.

**How it flows:** `compute_narrative_context()` → `ctx.prior_seasons` → `format_narrative_for_prompt()` emits a "League history:" section → appears in every AI prompt alongside bedrock facts

### 4. Update `format_narrative_for_prompt()` to emit all new context

**File: `src/pinwheel/core/narrative.py`**

- Emit bedrock facts at the TOP: `=== LEAGUE FACTS (do not contradict) ===`
- Label standings as "Regular-season standings (for seeding reference)" during playoffs
- Label existing h2h as "Season head-to-head (all games this season)"
- Emit playoff series as `PLAYOFF SERIES (current series only — NOT season h2h):`
- Emit prior seasons as "League history:" at the bottom

### 5. Add constraint language to AI prompts

**File: `src/pinwheel/ai/report.py`**

- Add to `SIMULATION_REPORT_PROMPT` after "Specificity Test" section:
  - "LEAGUE FACTS" are ground truth, never contradict
  - During playoffs, use "PLAYOFF SERIES" not "Season head-to-head" for series context
  - Don't invent concepts not in the data

**File: `src/pinwheel/ai/commentary.py`**

- Add similar short constraint to `COMMENTARY_SYSTEM_PROMPT` and `HIGHLIGHT_REEL_SYSTEM_PROMPT`

### 6. Pass ruleset in game_loop.py

**File: `src/pinwheel/core/game_loop.py`**

- Single line change: add `ruleset=ruleset` to the `compute_narrative_context()` call (~line 1481). The `ruleset` variable is already in scope.

### 7. Switch all AI models to `claude-sonnet-4-6`

**Files (model string changes):**
- `src/pinwheel/ai/report.py:1129` — `_call_claude`: `"claude-opus-4-6"` → `"claude-sonnet-4-6"`
- `src/pinwheel/ai/report.py:2300` — memorial: `"claude-sonnet-4-5-20250929"` → `"claude-sonnet-4-6"`
- `src/pinwheel/ai/commentary.py:453,805` — game/highlight: `"claude-sonnet-4-5-20250929"` → `"claude-sonnet-4-6"`
- `src/pinwheel/ai/interpreter.py:143,321,658` — Sonnet calls → `"claude-sonnet-4-6"`
- `src/pinwheel/ai/interpreter.py:563` — Opus escalation → `"claude-sonnet-4-6"`
- `src/pinwheel/ai/search.py:347,1049` → `"claude-sonnet-4-6"`
- `src/pinwheel/ai/mirror.py:234` → `"claude-sonnet-4-6"`

**File: `src/pinwheel/ai/usage.py`**
- Add `"claude-sonnet-4-6"` pricing entry (same rates as Sonnet 4.5: $3/$15 per MTok)

### 8. Tests

**New/modified tests:**
- `test_bedrock_facts_built` — `_build_bedrock_facts()` with known inputs, verify each key fact
- `test_narrative_includes_bedrock_facts` — compute context with ruleset, verify `bedrock_facts` non-empty
- `test_format_includes_bedrock_and_series` — format a NarrativeContext with all new fields, verify output
- `test_head_to_head_phase_filter` — `_compute_head_to_head` with filter only counts matching phase
- `test_playoff_series_computed` — during playoff phase, `playoff_series` dict populated correctly
- `test_prior_seasons_in_context` — verify prior season summaries included
- Update any test that asserts on specific model strings

---

## Implementation Order

1. `usage.py` — add Sonnet 4.6 pricing (prerequisite for model switch)
2. `narrative.py` — extend NarrativeContext, add bedrock facts builder, fix h2h, add prior seasons, update formatter
3. `game_loop.py` — pass ruleset to compute_narrative_context
4. `report.py` — add constraint language to prompts
5. `commentary.py` — add constraint language to prompts
6. All AI files — switch model strings to `claude-sonnet-4-6`
7. Tests — write and run
8. `uv run pytest -x -q && uv run ruff check src/ tests/`

## Verification

- Run full test suite
- Inspect formatted narrative output for a playoff round: should show bedrock facts, labeled standings, playoff series separate from h2h
- Verify no test references old model strings
