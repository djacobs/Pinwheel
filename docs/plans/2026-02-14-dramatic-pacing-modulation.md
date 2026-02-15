# Plan: Dramatic Pacing Modulation

**Date:** 2026-02-14
**Status:** Implemented
**Scope:** Presenter layer, SSE events, arena templates

## Context

VIEWER.md (section "Dramatic Pacing") describes variable-speed replay where the presenter
adjusts pacing based on game state:

> The presenter doesn't space possessions evenly. It adjusts pace for drama:
> - **Routine possessions:** Normal interval
> - **Run in progress** (one team on a 5-0+ run): Slightly faster -- momentum feels urgent
> - **Lead change:** Brief pause before -- let the audience register the stakes
> - **Entering Elam:** Longer pause. Commentary sets the scene.
> - **Final Elam possessions:** Slower. Every play gets space.
> - **Game-winning shot:** Pause before resolution. The commentary foreshadows.

This is listed as implementation priority #8 in VIEWER.md. The presenter exists and streams
possessions over real time, but currently uses a **flat delay** -- every possession in a
quarter gets exactly the same amount of time. There is no awareness of dramatic moments.

## What Exists Today

### Presenter (`src/pinwheel/core/presenter.py`)

The presenter is fully functional for flat-rate replay. Key architecture:

**`present_round()`** (line 84): Entry point. Receives pre-computed `GameResult` objects
and streams them concurrently via `asyncio.gather()`. All games in a round play
simultaneously -- the arena shows them side by side.

**`_present_game()`** (line 307): Drips a single game's possessions. Groups possessions by
quarter, then iterates with a fixed delay:

```python
# Line 365 -- THE FLAT DELAY CALCULATION
delay = quarter_replay_seconds / max(len(quarter_possessions), 1)
```

Every possession in a quarter gets `quarter_replay_seconds / num_possessions` seconds.
With the default `quarter_replay_seconds=300` (5 minutes per quarter) and a typical 20
possessions per quarter, each possession gets ~15 seconds.

**There is no code anywhere in the presenter that examines the possession content** --
no checks for lead changes, scoring runs, Elam proximity, move activations, or
game-winning shots. The presenter treats every possession identically.

### SSE Events (`src/pinwheel/api/events.py`)

A single SSE endpoint at `/api/events/stream` with optional `event_type` filtering.
The presenter publishes `presentation.possession` events (one per possession) and
`presentation.game_starting`/`presentation.game_finished` events.

The `presentation.possession` event payload includes (from presenter line 402-417):
```python
{
    "game_index": ...,
    "quarter": ...,
    "action": ...,
    "result": ...,
    "points_scored": ...,
    "home_score": ...,
    "away_score": ...,
    "game_clock": ...,
    "elam_target": ...,
    "narration": ...,
}
```

Score data is present but not used for pacing decisions.

### Commentary Line Model (`src/pinwheel/models/game.py`, lines 111-119)

```python
class CommentaryLine(BaseModel):
    game_id: str
    possession_index: int
    quarter: int
    commentary: str
    energy: Literal["low", "medium", "high", "peak"] = "low"
    tags: list[str] = Field(default_factory=list)
```

The `energy` field is already defined! VIEWER.md says: "The energy field tells the
frontend how to render the commentary -- low energy gets normal text, peak energy gets
animation/emphasis." This model exists but is **never populated** by the current
commentary system. The commentary generator in `src/pinwheel/ai/commentary.py` produces
plain text, not `CommentaryLine` objects with energy ratings.

### Configuration (`src/pinwheel/config.py`)

```python
pinwheel_presentation_mode: str = "replay"        # "instant" or "replay"
pinwheel_game_interval_seconds: int = 1800         # 30 min between games
pinwheel_quarter_replay_seconds: int = 300         # 5 min per quarter
```

`quarter_replay_seconds` controls the total wall-clock time per quarter. Dramatic pacing
would redistribute this budget unevenly across possessions rather than changing the total.

### PACE_CRON_MAP (`src/pinwheel/config.py`, lines 58-63)

```python
PACE_CRON_MAP = {
    "fast": "*/1 * * * *",
    "normal": "*/5 * * * *",
    "slow": "*/15 * * * *",
    "manual": None,
}
```

This controls how often `tick_round()` fires (round-level scheduling), not
possession-level pacing. The dramatic pacing feature operates at a finer granularity
than the cron scheduler.

### Arena Templates

The arena template receives `presentation.possession` events via SSE and renders them.
There is no logic in the template that would vary visual treatment based on possession
importance. The `narration` field is displayed as plain text.

### PossessionLog Model (`src/pinwheel/models/game.py`, lines 13-32)

Each possession record includes:
- `quarter`, `possession_number`
- `action` (drive, three_point, mid_range, etc.)
- `result` (made, missed, turnover, foul)
- `points_scored`
- `move_activated`
- `home_score`, `away_score`

All the data needed to classify dramatic moments is already present in the
`PossessionLog`.

## What Needs to Be Built

### Phase 1: Dramatic Moment Classification

**Goal:** Given a game's possession log, classify each possession's dramatic weight
before the presenter starts streaming.

Create a new module `src/pinwheel/core/drama.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pinwheel.models.game import GameResult, PossessionLog

DramaLevel = Literal["routine", "elevated", "high", "peak"]


@dataclass
class DramaAnnotation:
    """Dramatic weight for a single possession."""
    possession_index: int
    level: DramaLevel
    tags: list[str]        # What makes this dramatic: "lead_change", "run", "elam", etc.
    delay_multiplier: float  # 1.0 = normal, 0.7 = faster, 2.0 = slower


def annotate_drama(game_result: GameResult) -> list[DramaAnnotation]:
    """Pre-classify every possession in a game for dramatic pacing.

    Because the presenter has the full GameResult, this runs once before
    streaming begins. The annotations drive both pacing and visual treatment.
    """
    annotations: list[DramaAnnotation] = []
    possessions = game_result.possession_log
    if not possessions:
        return annotations

    # Pre-compute game-level context
    elam_target = game_result.elam_target_score
    total_possessions = len(possessions)
    last_possession_index = total_possessions - 1

    # Track running state
    prev_leader: str | None = None    # "home" | "away" | "tied"
    run_team: str | None = None       # Team on a scoring run
    run_points: int = 0
    consecutive_scores: int = 0

    for idx, poss in enumerate(possessions):
        tags: list[str] = []
        multiplier = 1.0

        # --- Detect lead changes ---
        if poss.home_score > poss.away_score:
            leader = "home"
        elif poss.away_score > poss.home_score:
            leader = "away"
        else:
            leader = "tied"

        if prev_leader is not None and leader != prev_leader and leader != "tied":
            if prev_leader != "tied":
                tags.append("lead_change")
                multiplier = max(multiplier, 1.8)
            else:
                tags.append("tie_broken")
                multiplier = max(multiplier, 1.4)

        if leader == "tied" and prev_leader != "tied" and prev_leader is not None:
            tags.append("game_tied")
            multiplier = max(multiplier, 1.5)

        prev_leader = leader

        # --- Detect scoring runs ---
        if poss.points_scored > 0:
            scoring_team = poss.offense_team_id
            if scoring_team == run_team:
                run_points += poss.points_scored
                consecutive_scores += 1
            else:
                run_team = scoring_team
                run_points = poss.points_scored
                consecutive_scores = 1

            if run_points >= 8:
                tags.append("big_run")
                multiplier = max(multiplier, 0.75)  # Faster -- momentum
            elif run_points >= 5:
                tags.append("run")
                multiplier = max(multiplier, 0.85)  # Slightly faster
        elif poss.result == "turnover" or poss.result == "missed":
            # Non-scoring possession breaks runs (only if other team had ball)
            pass  # Run tracking resets when the other team scores

        # --- Detect move activations ---
        if poss.move_activated:
            tags.append("move")
            tags.append(f"move:{poss.move_activated}")
            multiplier = max(multiplier, 1.3)

        # --- Detect Elam approach ---
        if elam_target and poss.quarter >= 4:  # Elam period
            tags.append("elam")
            home_to_go = elam_target - poss.home_score
            away_to_go = elam_target - poss.away_score
            closest = min(home_to_go, away_to_go)

            if closest <= 3:
                tags.append("elam_climax")
                multiplier = max(multiplier, 2.5)  # Very slow -- savor it
            elif closest <= 7:
                tags.append("elam_tension")
                multiplier = max(multiplier, 1.8)
            else:
                multiplier = max(multiplier, 1.2)  # Elam is always slightly slower

        # --- Detect game-winning shot ---
        if idx == last_possession_index and poss.points_scored > 0:
            tags.append("game_winner")
            multiplier = max(multiplier, 3.0)  # Long pause -- the big moment

        # --- Detect entering Elam (transition possession) ---
        if idx > 0:
            prev_poss = possessions[idx - 1]
            if prev_poss.quarter < 4 and poss.quarter >= 4:
                tags.append("elam_start")
                multiplier = max(multiplier, 2.0)  # Scene-setting pause

        # --- Detect close game in 4th quarter ---
        if poss.quarter == 3:  # Q3 is the last regulation quarter before Elam
            score_diff = abs(poss.home_score - poss.away_score)
            if score_diff <= 3:
                tags.append("close_late")
                multiplier = max(multiplier, 1.3)

        # --- Classify drama level ---
        if multiplier >= 2.0:
            level: DramaLevel = "peak"
        elif multiplier >= 1.4:
            level = "high"
        elif multiplier < 1.0:
            level = "elevated"  # Fast-paced excitement (runs)
        else:
            level = "routine"

        annotations.append(DramaAnnotation(
            possession_index=idx,
            level=level,
            tags=tags,
            delay_multiplier=multiplier,
        ))

    return annotations
```

**Design principle:** The total wall-clock time per quarter stays approximately the same.
Dramatic moments get more time, routine moments get less. The `delay_multiplier` values
are normalized so the average multiplier across a quarter is close to 1.0.

#### Normalization

After classifying all possessions in a quarter, normalize multipliers so total time
stays constant:

```python
def normalize_delays(
    annotations: list[DramaAnnotation],
    quarter_possessions: list[PossessionLog],
    quarter_seconds: float,
) -> list[float]:
    """Convert drama annotations into actual delay values (seconds).

    Normalizes so total delay across the quarter equals quarter_seconds.
    """
    raw_multipliers = [a.delay_multiplier for a in annotations]
    total_raw = sum(raw_multipliers)
    if total_raw == 0:
        base_delay = quarter_seconds / max(len(annotations), 1)
        return [base_delay] * len(annotations)

    base_delay = quarter_seconds / total_raw
    return [m * base_delay for m in raw_multipliers]
```

This ensures a quarter with many dramatic moments does not run longer than a quarter
with few -- the drama "budget" is redistributed, not expanded.

### Phase 2: Integrate Drama into Presenter

**File:** `src/pinwheel/core/presenter.py`, `_present_game()` function

Currently:
```python
for quarter_num in sorted_quarter_nums:
    quarter_possessions = quarters[quarter_num]
    delay = quarter_replay_seconds / max(len(quarter_possessions), 1)

    for possession in quarter_possessions:
        ...
        await asyncio.sleep(delay)
```

**Change:** Pre-annotate the full game, then use per-possession delays:

```python
from pinwheel.core.drama import annotate_drama, normalize_delays

# At the start of _present_game(), annotate the full game
annotations = annotate_drama(game_result)
annotation_map = {a.possession_index: a for a in annotations}

for quarter_num in sorted_quarter_nums:
    quarter_possessions = quarters[quarter_num]

    # Get annotations for this quarter's possessions
    quarter_annotations = []
    for p in quarter_possessions:
        # Map possession to its global index in the full possession log
        global_idx = game_result.possession_log.index(p)
        ann = annotation_map.get(global_idx)
        if ann:
            quarter_annotations.append(ann)
        else:
            quarter_annotations.append(DramaAnnotation(
                possession_index=global_idx,
                level="routine",
                tags=[],
                delay_multiplier=1.0,
            ))

    delays = normalize_delays(
        quarter_annotations,
        quarter_possessions,
        quarter_replay_seconds,
    )

    for i, possession in enumerate(quarter_possessions):
        ...
        # Include drama metadata in the SSE event
        ann = quarter_annotations[i]
        play_dict["drama_level"] = ann.level
        play_dict["drama_tags"] = ann.tags

        await event_bus.publish("presentation.possession", play_dict)
        await asyncio.sleep(delays[i])
```

### Phase 3: Drama-Aware SSE Events

Add `drama_level` and `drama_tags` to the `presentation.possession` event payload.
This enables frontend visual treatment without additional API calls.

**SSE event enrichment:**
```python
play_dict["drama_level"] = ann.level       # "routine" | "elevated" | "high" | "peak"
play_dict["drama_tags"] = ann.tags         # ["lead_change", "move:Heat Check", "elam_climax"]
```

Add a new event type for dramatic pauses that precede big moments. Before a "peak"
possession, the presenter can emit a suspense event:

```python
if ann.level == "peak" and delays[i] > base_delay * 1.5:
    await event_bus.publish("presentation.suspense", {
        "game_index": game_idx,
        "drama_tags": ann.tags,
        "seconds_until_play": delays[i],
    })
    # The frontend can show a visual buildup during this pause
```

### Phase 4: Frontend Visual Treatment

**File:** `templates/pages/arena.html` (and related JS/CSS)

The arena should respond to `drama_level` in SSE events:

| Drama Level | Visual Treatment |
|-------------|-----------------|
| `routine` | Normal play-by-play text, standard font |
| `elevated` | Slightly larger text, team color accent |
| `high` | Highlighted background, bold text, optional animation |
| `peak` | Full-width expansion, color flash, dramatic font, optional sound cue |

The `drama_tags` enable specific treatments:
- `lead_change`: Score display flashes with the new leader's color
- `move:*`: Move badge animation
- `elam_climax`: Target score counter pulses
- `game_winner`: Full celebration treatment (confetti CSS, expanded panel)
- `big_run`: Running score counter with "X-0 RUN" badge

CSS classes mapped from drama level:

```css
.play--routine { }
.play--elevated { font-weight: 500; }
.play--high { background: var(--highlight-bg); font-weight: 600; font-size: 1.1em; }
.play--peak { background: var(--peak-bg); font-weight: 700; font-size: 1.2em;
              animation: drama-pulse 0.5s ease-in-out; }
```

The HTMX swap handler reads `drama_level` from the SSE data and applies the
appropriate class.

### Phase 5: Suspense Markers for Commentary Integration

When the AI commentary engine is wired to the presenter (currently commentary is
generated as a single block in the game loop, not per-possession), the drama annotations
would tell the commentary engine which possessions deserve more elaborate narration.

For now, the drama annotations can be attached to the game result as metadata so
commentary generation can reference them:

```python
# Store annotations as game metadata for commentary to use
game_annotations = [
    {"idx": a.possession_index, "level": a.level, "tags": a.tags}
    for a in annotations
    if a.level != "routine"
]
```

The commentary generator could use this to vary its energy: routine possessions get
one sentence; peak possessions get 2-3 sentences with foreshadowing.

## Files to Create

| File | Purpose |
|------|---------|
| `src/pinwheel/core/drama.py` | Dramatic moment classification and delay normalization |

## Files to Modify

| File | Change |
|------|--------|
| `src/pinwheel/core/presenter.py` | Use per-possession delays from drama annotations instead of flat delay; emit drama metadata in SSE events; emit suspense events before peak moments |
| `templates/pages/arena.html` | Handle `drama_level` and `drama_tags` in SSE event handler; apply CSS classes for visual treatment |
| `static/css/style.css` (or equivalent) | Add `.play--routine`, `.play--elevated`, `.play--high`, `.play--peak` classes and `drama-pulse` animation |

## Testing Strategy

### Unit Tests

- **`tests/test_drama.py`** (new)
  - `annotate_drama()` on a game with no lead changes returns all `routine` or `elevated`
  - `annotate_drama()` on a game with a lead change marks that possession as `high` or `peak`
  - `annotate_drama()` on an Elam game marks final possessions approaching the target as
    `peak`
  - `annotate_drama()` marks the game-winning possession as `peak`
  - `annotate_drama()` detects scoring runs and marks them as `elevated`
  - `annotate_drama()` marks move activations as at least `high`
  - `normalize_delays()` preserves total quarter time within 1% tolerance
  - `normalize_delays()` produces shorter delays for `elevated` (run) possessions
  - `normalize_delays()` produces longer delays for `peak` possessions
  - With all-routine possessions, delays are approximately uniform
  - Edge case: empty possession log returns empty annotations
  - Edge case: single-possession quarter

- **`tests/test_presenter.py`** (extend)
  - Presenter with dramatic pacing still completes within expected wall-clock time
    (use `asyncio.sleep` mocking to verify delay values)
  - SSE events include `drama_level` and `drama_tags` fields
  - `presentation.suspense` event is emitted before peak possessions

### Integration Tests

- **`tests/test_pages.py`** (extend)
  - Arena page renders correctly when receiving possession events with drama metadata
  - CSS classes for drama levels are present in the rendered HTML

### Simulation-to-Drama Pipeline Test

- Generate a game with a known seed that produces a lead change and an Elam ending
- Run `annotate_drama()` on the result
- Verify specific possessions are classified correctly (deterministic because the
  game seed is fixed)
- Verify `normalize_delays()` on the annotated game produces expected delay distribution

## Risks and Open Questions

1. **Multiplier tuning:** The proposed multipliers (0.75 for runs, 3.0 for game-winners)
   are initial guesses. If a quarter has many dramatic moments, the normalization could
   compress routine possessions to very short delays (< 5 seconds), which might feel
   jarring. Consider: a minimum delay floor (e.g., 5 seconds) that caps how compressed
   routine possessions can get. This would slightly extend the quarter total time for
   drama-heavy quarters.

2. **Concurrent game pacing:** All games in a round run concurrently. If one game has
   a dramatic ending (slow pacing) while another finishes normally (normal pacing), the
   first game will continue streaming after the second finishes. This is fine -- the
   arena can show "FINAL" for the finished game while the other game builds toward its
   climax. But the `presentation.round_finished` event should wait for all games to
   complete, which `asyncio.gather()` already ensures.

3. **Elam period length variance:** The Elam period can have vastly different possession
   counts depending on the score gap. A close game might have 5 Elam possessions; a
   blowout might have 30. The normalization handles this naturally (budget = quarter
   replay seconds / possessions), but the visual pacing of a 5-possession Elam ending
   should feel very different from a 30-possession one. Consider: for very short Elam
   periods (< 8 possessions), apply a bonus time budget (e.g., 1.5x normal quarter
   time) since every possession is inherently dramatic.

4. **Frontend animation timing:** If the presenter pauses 15 seconds before a
   game-winning shot, the frontend needs to show something during that pause. The
   `presentation.suspense` event signals the pause, but the arena template needs a
   visual treatment for "something big is about to happen." Options: a pulsing "..."
   indicator, a tension meter filling up, or a brief split-screen showing the other
   games' final scores before returning to the climax.

5. **Commentary alignment:** The dramatic pacing feature and the commentary engine
   (from VIEWER.md) are designed to work together. Commentary batches should align with
   drama annotations: routine possessions get brief commentary, peak possessions get
   elaborate narration. This plan does not implement the commentary-drama alignment
   (that belongs to the commentary engine build), but the `drama_tags` and `drama_level`
   are designed to be consumed by both the presenter and the commentary system.

6. **Performance of `annotate_drama()`:** The function iterates once over the possession
   log (O(n)) and uses only simple comparisons. For a typical game (60-80 possessions),
   this is sub-millisecond. No performance concern.
