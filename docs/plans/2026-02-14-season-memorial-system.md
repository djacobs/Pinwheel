# Season Memorial System — Implementation Plan

## Status: Implemented
## Date: 2026-02-14
## Priority: P2

---

## 1. Overview

When a season ends, the system generates a rich, permanent, AI-written narrative that captures the full arc: regular season patterns, governance evolution, playoff drama, and the championship moment. This memorial becomes the permanent historical record that players can revisit.

Generated during `archive_season()`, stored on `SeasonArchiveRow`, presented through web and Discord.

---

## 2. Memorial Content Structure

```python
class SeasonMemorial(BaseModel):
    # AI-written narrative sections
    season_narrative: str          # 3-5 paragraph story arc: start to finish
    championship_recap: str        # Detailed playoff bracket + finals account
    champion_profile: str          # The winning team's journey and roster
    governance_legacy: str         # How the rules evolved, who drove changes

    # Computed data sections
    awards: list[dict]             # From compute_awards()
    statistical_leaders: dict      # Season stat leaders: scoring, assists, steals, efficiency
    key_moments: list[dict]        # Biggest upsets, closest games, blowouts, Elam endings
    head_to_head: list[dict]       # Team-vs-team records
    rule_timeline: list[dict]      # Chronological rule changes with round numbers

    # Metadata
    generated_at: str
    model_used: str
```

### Content Breakdown

**Season Narrative (AI):** The overarching story. Who started hot, who fell off, what rule changes reshaped the game, rivalries. 3-5 paragraphs in sports almanac style.

**Championship Recap (AI):** Detailed playoff bracket account. Semifinal matchups, scores, drama. The finals — every shift in momentum, the winning play. Elam Endings get special treatment.

**Champion Profile (AI):** The winning team's season-long journey. Regular season record, hoopers, standout performers, playoff path.

**Governance Legacy (AI):** What governors changed, which rules enacted/failed, coalitions, visible effects on game outcomes.

**Statistical Leaders (computed):** Top 3 in PPG, APG, SPG, FG%, total points from BoxScoreRow data.

**Key Moments (computed):** 5-8 most notable games — closest margin, largest blowout, biggest upset, Elam activations, playoff games.

**Head-to-Head (computed):** Matrix of team-vs-team records (W/L/point differential).

**Rule Timeline (computed):** Chronological rule changes with round number, parameter, old/new values, proposer.

---

## 3. AI Generation Strategy

### Multi-call approach (4 concurrent calls via asyncio.gather)

| Call | Section | Context | Max Tokens |
|------|---------|---------|------------|
| 1 | Season Narrative | Standings, standings progression, rule changes, totals | 2000 |
| 2 | Championship Recap | Playoff bracket, all playoff game results + box scores, commentary | 1500 |
| 3 | Champion Profile | Champion team record, hooper roster + averages, playoff path | 1000 |
| 4 | Governance Legacy | All rule changes, all proposals with outcomes, governor activity, ruleset diff | 1500 |

**Token budget:** ~14,000 tokens total. Under $1 per season. Once-per-season cost.

Each call has a mock variant for dev/testing.

---

## 4. Storage

### Schema change

Add `memorial: JSON` (nullable) to `SeasonArchiveRow` in `db/models.py`.

Migration via existing `_add_column_if_missing()` pattern in `main.py`. Safe, additive, no data loss.

---

## 5. Data Collection

### New file: `src/pinwheel/core/memorial.py`

```python
async def gather_memorial_data(repo, season_id) -> dict:
    """Collect all data needed for memorial generation."""

async def compute_statistical_leaders(repo, season_id) -> dict:
    """Top 3 per category from box scores."""

async def compute_key_moments(repo, season_id) -> list[dict]:
    """5-8 most notable games (closest, blowout, upset, Elam, playoffs)."""

async def compute_head_to_head(repo, season_id) -> list[dict]:
    """Team-vs-team W/L/point differential matrix."""

async def compute_rule_timeline(repo, season_id) -> list[dict]:
    """Chronological rule changes with proposer info."""
```

---

## 6. Lifecycle Integration

### Where it runs

Memorial generates inside `archive_season()` in `season.py`. This function:
1. Already gathers standings, rule changes, champion info
2. Runs after CHAMPIONSHIP phase (awards already computed)
3. Runs exactly once per season
4. Creates the archive row — natural storage location

### Modified signature

```python
async def archive_season(repo, season_id, api_key="", event_bus=None) -> SeasonArchiveRow:
```

### Trigger

In `scheduler_runner.py`, when championship window expires and season transitions to COMPLETE, call `archive_season()` with the API key.

### Event

Publish `season.memorial_generated` after generation for Discord notification.

---

## 7. Web Presentation

### New routes

| Route | Description |
|-------|-------------|
| `/history` | Hall of History — all past seasons as championship banners |
| `/seasons/{season_id}/memorial` | Full memorial page for one season |

### Template: `templates/pages/memorial.html`

```
HERO SECTION — Champion team name (large, team color), season name, key stats bar
SEASON NARRATIVE — AI-written 3-5 paragraphs
CHAMPIONSHIP RECAP — Playoff bracket + AI narrative
AWARDS — Grid of award cards (gameplay + governance)
CHAMPION PROFILE — Team card with hooper roster + AI profile
STATISTICAL LEADERS — Tables: scoring, assists, steals, efficiency
KEY MOMENTS — Cards for notable games
HEAD TO HEAD — Matrix table
GOVERNANCE LEGACY — AI narrative + rule timeline + ruleset diff
```

### Template: `templates/pages/history.html`

Index page with championship banner cards per season. Links to memorial pages.

### Navigation

Add "History" link to `base.html` nav bar.

---

## 8. Discord Integration

### Event handler: `season.memorial_generated`

Posts a gold-colored memorial excerpt embed to the main channel with a link to the web memorial.

### New command: `/history [season]`

- No args: lists all archived seasons
- With season name: shows summary embed with champion, awards excerpt, narrative excerpt, web link

### New embed builder

`build_memorial_embed()` in `embeds.py` — distinctive gold-themed embed for memorials.

---

## 9. Implementation Sequence

1. **Model + Storage** — `SeasonMemorial` model, `memorial` column, migration, repo methods
2. **Data Collection** — `core/memorial.py` with all compute functions + tests
3. **AI Generation** — 4 prompts + `generate_season_memorial()` + mock + tests
4. **Lifecycle Integration** — Wire into `archive_season()` and championship→complete transition
5. **Web** — memorial + history templates, routes, nav link
6. **Discord** — event handler, `/history` command, embed builder

---

## 10. Files Changed

| File | Change | Description |
|------|--------|-------------|
| `models/report.py` | Modify | Add `SeasonMemorial` model |
| `db/models.py` | Modify | Add `memorial` column to `SeasonArchiveRow` |
| `main.py` | Modify | Add migration for `memorial` column |
| `db/repository.py` | Modify | Add `get_all_reports_for_season()`, `get_playoff_games()` |
| `core/memorial.py` | **New** | Data gathering + orchestration |
| `ai/report.py` | Modify | 4 memorial prompts + generation functions + mock |
| `core/season.py` | Modify | `archive_season()` generates memorial |
| `core/scheduler_runner.py` | Modify | Wire archival into championship→complete |
| `api/pages.py` | Modify | `/history` and `/seasons/{id}/memorial` routes |
| `templates/pages/memorial.html` | **New** | Memorial page |
| `templates/pages/history.html` | **New** | History index |
| `templates/base.html` | Modify | Add "History" nav link |
| `discord/bot.py` | Modify | `season.memorial_generated` handler, `/history` command |
| `discord/embeds.py` | Modify | `build_memorial_embed()` |
| `tests/test_memorial.py` | **New** | Memorial system tests |
