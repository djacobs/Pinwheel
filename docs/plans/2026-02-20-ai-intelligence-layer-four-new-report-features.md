# AI Intelligence Layer — Four New Report Features

## Context

The AI ideas doc (`docs/AI-IDEAS-02-14.md`) identifies 12 code-grounded AI features. Four are ready to build now, ordered by dependency:

1. **5.4 Proposal Impact Validation** — Did the prediction match reality?
2. **5.7 Hidden Leverage Detection** — You have more power than you know
3. **5.1 Behavioral Pattern Detection** — AI as self-awareness mirror (longitudinal)
4. **5.11 The Pinwheel Post** — Newspaper-style round summary page

All four follow the existing report infrastructure pattern: query data → format prompt → call Claude (or mock) → store as Report → display on web + Discord. The codebase already has this fully wired (`ai/report.py`, `game_loop.py:_phase_ai`, `repository.store_report`, `templates/pages/reports.html`).

## Shared Infrastructure (before Phase 1)

### New file: `src/pinwheel/ai/insights.py`

All four features produce "insight" reports. Rather than bloating `ai/report.py` further (already 1090 lines), create a new module for the intelligence layer. Same pattern: prompt templates at top, async generators below, mock fallbacks for each.

### New report types in `models/report.py`

Add to `ReportType` Literal:
```python
ReportType = Literal[
    ...,
    "impact_validation",    # Phase 1: proposal prediction vs reality
    "leverage",             # Phase 2: governor influence analysis (private)
    "behavioral",           # Phase 3: longitudinal governance patterns (private)
]
```

The Pinwheel Post (Phase 4) is a page, not a stored report type — it aggregates existing reports.

### Discord embed support in `discord/embeds.py`

Add entries to `type_labels` dict in `build_report_embed()`:
```python
"impact_validation": "Impact Validation",
"leverage": "Your Influence",
"behavioral": "Your Governance Pattern",
```

---

## Phase 1: Proposal Impact Validation

**The idea:** When a proposal predicted "perimeter-heavy teams will dominate" and it passed, check what actually happened. Grade the prediction. Show governors whether their understanding of the game was correct.

### Data assembly: `compute_impact_validation()` in `ai/insights.py`

Pure function. For each rule change enacted this round (from `governance_data["rules_changed"]`):

1. Find the original proposal via `repo.get_events_for_aggregate("proposal", proposal_id)` — extract `interpretation.impact_analysis` from the `proposal.confirmed` event payload.
2. Compute gameplay stats for games played under this rule:
   - Query game results from the round the rule was enacted through the current round
   - Compute: avg score, avg margin, three-point %, paint scoring %, pace (possessions/game), Elam activation rate
3. Compare to stats from rounds *before* the rule change (same metrics).
4. Build a dict: `{proposal_text, impact_prediction, parameter, old_value, new_value, rounds_under_rule, stats_before, stats_after, deltas}`.

**Key repo methods to use:**
- `repo.get_events_by_type(season_id, ["rule.enacted"])` — find enacted rules
- `repo.get_events_for_aggregate("proposal", proposal_id)` — get original proposal + interpretation
- Game stats: query `GameResultRow` for the season, partition by round number vs rule enactment round

**New repo method needed:** `repo.get_game_stats_for_rounds(season_id, round_start, round_end)` — returns aggregate stats (avg score, avg margin, 3pt%, etc.) for games in a round range. Keeps the stats computation in the data layer.

### Prompt: `IMPACT_VALIDATION_PROMPT`

```
You are validating a governance prediction in Pinwheel Fates.

A governor proposed a rule change. The AI predicted consequences. Now we have data.

## Rules
1. Grade the prediction honestly but without judgment of the proposer.
2. Note what was predicted correctly and what surprised everyone.
3. If the rule had unintended consequences, describe them vividly.
4. Be specific — use the actual numbers. "Three-point shooting rose 12%" not "shooting increased."
5. End with what this reveals about the league's understanding of its own rules.

## Proposal & Prediction
{proposal_data}

## Gameplay Before Rule Change
{stats_before}

## Gameplay After Rule Change
{stats_after}
```

### Generation: `generate_impact_validation()` / `generate_impact_validation_mock()`

- Called from `_phase_ai()` after governance report, only if `governance_data["rules_changed"]` is non-empty
- Mock: template-based deterministic output referencing the parameter name and delta values
- Stored as `report_type="impact_validation"`, `round_number=current_round`

### Game loop integration

In `game_loop.py:_phase_ai()`, after the governance report block (line ~1635):

```python
# Impact validation — only when rules changed this round
impact_report = None
if sim.governance_data.get("rules_changed"):
    impact_data = await compute_impact_validation(...)
    if impact_data:
        if api_key:
            impact_report = await generate_impact_validation(impact_data, ...)
        else:
            impact_report = generate_impact_validation_mock(impact_data, ...)
```

Add `impact_report: Report | None` to `_AIPhaseResult`. Store in Phase 3 alongside other reports.

### Display

- **Web:** Reports page already auto-renders any report type via `{{ m.report_type|title }} Report`. Impact validation reports will show as "Impact Validation Report."
- **Discord:** Add to `type_labels`. Consider a dedicated `/impact` command later (not in this plan).

### Tests: `tests/test_insights.py`

- `test_compute_impact_validation_with_rule_change` — verifies stats assembly
- `test_compute_impact_validation_no_changes` — returns None
- `test_generate_impact_validation_mock` — check Report fields
- `test_impact_validation_in_game_loop` — verify it's generated when rules change and skipped when they don't

---

## Phase 2: Hidden Leverage Detection

**The idea:** Show each governor their actual influence — swing-vote frequency, vote prediction accuracy, proposal success rate. Private report, visible only to them.

### Data assembly: `compute_governor_leverage()` in `ai/insights.py`

Pure function. For a given governor:

1. **Vote alignment:** Query all `vote.cast` events for the season. For each proposal that reached outcome (passed/failed), check if this governor's vote matched the outcome. Compute: `alignment_rate = correct_predictions / total_votes`.

2. **Swing vote detection:** For each proposal, compute the margin (weighted yes - weighted no). If removing this governor's vote would flip the outcome, they were a swing vote. Compute: `swing_count`, `swing_rate`.

3. **Proposal success rate:** From `get_governor_activity()` — `proposals_passed / proposals_submitted`.

4. **Cross-team voting:** How often does this governor vote against their own team's proposals? Query proposals by team, cross-reference with this governor's votes.

5. **Token velocity:** Compare this governor's token spending to league average. `spent / regenerated` ratio.

**Key repo methods to use:**
- `repo.get_events_by_type(season_id, ["vote.cast"])` — all votes
- `repo.get_events_by_type(season_id, ["proposal.passed", "proposal.failed"])` — outcomes
- `repo.get_governor_activity(gov_id, season_id)` — proposal stats + token balance
- `repo.get_events_by_type(season_id, ["token.spent", "token.regenerated"])` — token velocity

### Prompt: `LEVERAGE_DETECTION_PROMPT`

```
You are generating a private influence analysis for governor "{governor_id}" in Pinwheel Fates.

This report shows a governor how their votes and proposals actually shaped outcomes.
Only they see this. Be honest and specific.

## Rules
1. DESCRIBE their influence patterns. Never PRESCRIBE what they should do.
2. If they're a swing voter, tell them — that's powerful information.
3. If their proposals always pass, note what that means about their read of the league.
4. If they vote against their team often, note the pattern without judgment.
5. Compare to league averages where relevant, but never name other governors.

## Governor Influence Data
{leverage_data}
```

### Generation

- Called from `_phase_ai()` alongside private reports — one per active governor
- Stored as `report_type="leverage"`, `governor_id=gov_id` (private)
- Only generated every N rounds (configurable, default every 3 rounds) to avoid noise. Check: `round_number % leverage_interval == 0`.

### Tests

- `test_compute_governor_leverage_swing_voter` — governor whose vote flips outcomes
- `test_compute_governor_leverage_always_majority` — high alignment, no swings
- `test_compute_governor_leverage_inactive` — governor with no votes
- `test_leverage_skipped_when_not_interval` — only runs every Nth round

---

## Phase 3: Behavioral Pattern Detection (Longitudinal)

**The idea:** The existing private report reflects per-round activity. This enriches it with longitudinal patterns — philosophy drift, risk appetite over time, engagement arc.

### Data assembly: `compute_behavioral_profile()` in `ai/insights.py`

Pure function. For a given governor across the full season:

1. **Proposal philosophy drift:** Query all `proposal.submitted` events for this governor. Extract `interpretation.parameter` from each. Group by round. Track which parameters they target over time. Detect shift: "You started with mechanical tweaks, now you're proposing meta-governance changes."

2. **Risk appetite:** Track proposal tier distribution over time. Compute average tier per window (every 3 rounds). Detect trend: increasing, decreasing, stable.

3. **Engagement arc:** Actions per round over time. Detect: warming up, consistent, fading, bursty.

4. **Coalition signals:** Query all `vote.cast` events. Compute pairwise vote correlation with other governors (without naming them). Report: "Your votes align 87% with one other governor" (anonymized).

5. **Reuse existing evals:**
   - `evals/behavioral.py:compute_baseline()` — rolling action average
   - `evals/behavioral.py:detect_behavioral_shift()` — shift detection
   - `evals/gqi.py:_shannon_entropy()` — proposal diversity for this governor

### Prompt: `BEHAVIORAL_PROFILE_PROMPT`

```
You are generating a longitudinal behavioral profile for governor "{governor_id}" in Pinwheel Fates.

Unlike the per-round private report, this looks at their ENTIRE season arc.
What patterns emerge over time? Only they see this.

## Rules
1. DESCRIBE patterns across time. Never PRESCRIBE future actions.
2. Note trajectory: are they getting bolder? More conservative? More engaged?
3. If their proposal focus shifted, name the shift specifically.
4. Note coalition patterns without naming other governors ("one other governor" is fine).
5. Be reflective and insightful — this should feel like a coaching session, not a report card.

## Governor Season Profile
{profile_data}
```

### Generation

- Called from `_phase_ai()` alongside private reports, every N rounds (default 3, same interval as leverage)
- Stored as `report_type="behavioral"`, `governor_id=gov_id` (private)
- Mock: template referencing proposal count, tier trend, engagement arc

### Tests

- `test_compute_behavioral_profile_proposal_drift` — governor who shifts from Tier 1 to Tier 4
- `test_compute_behavioral_profile_stable` — consistent governor
- `test_compute_behavioral_profile_inactive` — minimal activity
- `test_coalition_detection` — verify anonymous pairwise correlation

---

## Phase 4: The Pinwheel Post (Newspaper Page)

**The idea:** A single page that aggregates all AI outputs into a newspaper-style layout after each round. Headlines, governance desk, stats, editorial. One lightweight AI call for headlines + editorial framing; everything else is composition of existing reports.

### New route: `GET /post`

In `api/pages.py`, add `newspaper_page()`:

1. Query latest round number
2. Fetch: simulation report, governance report, highlight reel, impact validation (if exists), standings, hot players from NarrativeContext
3. One AI call (or mock) to generate:
   - **Headline** (1 line, punchy — "UPSET! Thorns end Breakers' 7-game streak")
   - **Subhead** (1 line — "Rule meta shifts as defense proposals dominate The Floor")
4. Compose the page from existing data — no new AI calls for body content

### Template: `templates/pages/newspaper.html`

Layout sections:

```
┌─────────────────────────────────────────┐
│         THE PINWHEEL POST               │
│         Round N — Date                  │
├─────────────────────────────────────────┤
│ [HEADLINE]                              │
│ [Subhead]                               │
├──────────────────┬──────────────────────┤
│ GAME REPORTS     │ THE FLOOR            │
│                  │                      │
│ Simulation       │ Governance report    │
│ report content   │ content              │
│                  │                      │
│                  │ Impact validation    │
│                  │ (if exists)          │
├──────────────────┴──────────────────────┤
│ HIGHLIGHT REEL                          │
│ (highlight reel content)                │
├──────────────────┬──────────────────────┤
│ STANDINGS        │ HOT PLAYERS          │
│ Mini table       │ Season leaders       │
├──────────────────┴──────────────────────┤
│ "The AI observes. Humans decide."       │
└─────────────────────────────────────────┘
```

### AI call: `generate_newspaper_headlines()` in `ai/insights.py`

Minimal prompt — receives simulation report excerpt + governance report excerpt + standings. Returns: `{headline: str, subhead: str}`. Mock returns headline from game data (winner/loser/margin).

### Navigation

- Add "The Post" link to the nav bar (top nav in `base.html`)
- Add a card on the home page linking to `/post`
- Discord: not a separate command — link in `/reports` response

### Tests

- `test_newspaper_page_renders` — basic page test
- `test_newspaper_page_empty_state` — no rounds played yet
- `test_generate_newspaper_headlines_mock` — mock returns valid structure
- `test_newspaper_includes_impact_validation` — when rule changed, impact section appears

---

## Files Touched (Summary)

| File | Change |
|------|--------|
| `src/pinwheel/ai/insights.py` | **NEW** — all data assembly + generation functions |
| `src/pinwheel/models/report.py` | Add 3 new report types to `ReportType` |
| `src/pinwheel/core/game_loop.py` | Wire new reports into `_phase_ai()` and storage |
| `src/pinwheel/db/repository.py` | Add `get_game_stats_for_rounds()` |
| `src/pinwheel/api/pages.py` | Add `newspaper_page()` route |
| `src/pinwheel/discord/embeds.py` | Add new types to `build_report_embed()` labels |
| `templates/pages/newspaper.html` | **NEW** — newspaper layout |
| `templates/base.html` | Add "The Post" nav link |
| `tests/test_insights.py` | **NEW** — tests for all 4 phases |

## What Stays the Same

- Existing report generation (simulation, governance, private) is untouched
- No schema changes — all new reports use existing `ReportRow` model
- Mock fallbacks for every AI call — tests never hit the API
- `_phase_ai` / `_phase_db` separation preserved
- Private reports remain private (leverage + behavioral use `governor_id`)

## Verification

1. `uv run pytest -x -q` — all tests pass after each phase
2. `uv run ruff check src/ tests/` — clean lint after each phase
3. After Phase 1: `demo_seed.py step 3` then check `/reports` — impact validation appears when rules change
4. After Phase 2-3: check private reports for a governor — leverage and behavioral reports appear
5. After Phase 4: visit `/post` — newspaper layout with aggregated content
