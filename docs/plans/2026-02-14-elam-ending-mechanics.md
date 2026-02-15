# Plan: Elam Ending Mechanics Documentation

**Date:** 2026-02-14
**Status:** Draft (reference documentation for existing implementation)

## What Is the Elam Ending?

The Elam Ending replaces the game clock with a target score in the final period. Instead of playing until time expires, teams play until one team reaches a target score derived from the leading team's score at the end of the penultimate quarter. This eliminates intentional fouling and creates a definitive finish to every game.

Pinwheel's implementation is inspired by the real-world Elam Ending used in The Basketball Tournament (TBT) and the NBA All-Star Game.

## Governable Parameters

**File:** `src/pinwheel/models/rules.py` -- `RuleSet`

| Parameter | Default | Range | Description |
|-----------|---------|-------|-------------|
| `elam_trigger_quarter` | 3 | 1-4 | The quarter AFTER which the Elam Ending begins. Default 3 means: play Q1, Q2, Q3 with normal clock, then switch to Elam for the "4th quarter." |
| `elam_margin` | 15 | 5-40 | Points added to the leading team's score to compute the target. |
| `quarter_minutes` | 10 | 3-20 | Length of each timed quarter (Elam period has no clock). |

Both `elam_trigger_quarter` and `elam_margin` are Tier 1 (Game Mechanics) and can be changed by governor proposals with a simple majority.

## Game Structure

**File:** `src/pinwheel/core/simulation.py` -- `simulate_game()`

The simulation runs quarters in a loop:

```
for q in range(1, elam_trigger_quarter + 1):
    run_quarter(q)        # timed quarter
    quarter_break()       # stamina recovery

if not game_over:
    run_elam()            # target-score period
```

With default `elam_trigger_quarter=3`:
- **Q1** (10 minutes, timed) -> quarter break recovery
- **Q2** (10 minutes, timed) -> halftime recovery (larger stamina boost)
- **Q3** (10 minutes, timed) -> quarter break recovery
- **Elam Period** (no clock, target score)

### Quarter Flow

**File:** `src/pinwheel/core/simulation.py` -- `_run_quarter()`

Each timed quarter:
1. Sets `game_clock_seconds = quarter_minutes * 60`
2. Runs possessions in a loop, decrementing the clock by each possession's `time_used`
3. Stops when `game_clock_seconds <= 0` or `game_over` flag is set
4. Each possession alternates offense between home and away
5. Safety cap: `safety_cap_possessions` (default 300) prevents infinite loops

Possession time is computed from dead ball time and shot clock mechanics in `resolve_possession()`.

## Elam Ending Mechanics

**File:** `src/pinwheel/core/simulation.py` -- `_run_elam()`

### Trigger

The Elam Ending activates after the last timed quarter completes (after `elam_trigger_quarter` quarters have been played). It does NOT activate if the game is already over (which would only happen if the safety cap was hit during timed quarters).

### Target Score Computation

```python
leading_score = max(game_state.home_score, game_state.away_score)
game_state.elam_target_score = leading_score + elam_margin
```

The target is always based on the LEADING team's score. If the score is tied at the end of Q3, the target is `tied_score + elam_margin`. This means both teams need to score `elam_margin` more points to win, creating a fair sudden-death-style finish even from a tie.

**Example with defaults (margin=15):**
- Score after Q3: Home 35, Away 32
- Target: max(35, 32) + 15 = 50
- Home needs 15 more points; Away needs 18 more points
- First team to reach 50 wins

### Elam Period Flow

1. Sets `elam_target_score` and `elam_activated = True`
2. Fires `ELAM_START` hook (for legacy effects) and `sim.elam.start` (for new effects)
3. Runs possessions WITHOUT a game clock (no time limit)
4. After each possession, checks:
   - If `home_score >= elam_target_score` -> home wins, game over
   - If `away_score >= elam_target_score` -> home wins, game over
5. Possessions alternate normally
6. Foul-out substitutions still happen
7. Safety cap still applies

### Key Behaviors

**No overtime:** The Elam Ending eliminates the possibility of a tie or overtime. One team WILL reach the target score (unless the safety cap is hit, which is set at 300 possessions total for the game -- extremely unlikely).

**Scoring can exceed target:** If a team is at 48 and scores a 3-pointer (target is 50), their score becomes 51. The check is `>=`, not `==`. This means the margin of victory can be larger than the point value of the final shot if the final shot puts the team past the target by more than 1.

**No clock pressure:** During the Elam period, there is no game clock. `game_clock_seconds` is not decremented. Possession time is still consumed for the `time_used` tracking in `PossessionLog` but it does not affect gameplay.

**Stamina drain continues:** Hoopers continue to drain stamina during the Elam period based on `stamina_drain_rate`. However, there are no quarter breaks for recovery. This means longer Elam endings (when the trailing team is far behind) lead to increasing fatigue, which reduces shooting accuracy via `compute_stamina_modifier()`.

**Substitutions during Elam:** Foul-out substitutions still happen. Fatigue-based substitutions do NOT happen during Elam because those are triggered at quarter breaks (`_check_substitution(reason="fatigue")` is called after quarters, not during Elam).

## Hook Points

The simulation fires these hooks during the Elam sequence:

| Hook | When | Available Context |
|------|------|------------------|
| `sim.elam.start` | After target score is computed, before first Elam possession | `game_state` has `elam_target_score` and `elam_activated=True` |
| `sim.possession.pre` | Before each Elam possession | Normal possession context |
| `sim.shot.pre` | Before each shot in Elam | Shot type, shooter, defender |
| `sim.shot.post` | After each shot in Elam | Shot result, points scored |
| `sim.game.end` | After the winning basket | Final scores, winner determined |

The legacy `HookPoint.ELAM_START` is also fired for backward compatibility.

## Impact on Scoring Module

**File:** `src/pinwheel/core/scoring.py`

The scoring module is Elam-agnostic. `resolve_shot()` and `compute_shot_probability()` work identically during timed quarters and the Elam period. The only Elam-specific behavior is the target score check in `_run_elam()`.

Shot probability is affected by:
1. **Base probability** -- logistic curve based on shooter's `scoring` attribute and shot type
2. **Defense contest** -- defender's `defense` attribute reduces probability (multiplier 0.5-1.0)
3. **IQ modifier** -- shooter's `iq` provides a small bonus (multiplier 0.9-1.1)
4. **Stamina modifier** -- current stamina affects accuracy (multiplier 0.7-1.0)

During the Elam ending, stamina gradually decreases (no quarter break recovery), so shots become slightly less accurate over time. This creates natural tension as the period extends.

## Point Values Under Current Rules

**File:** `src/pinwheel/core/scoring.py` -- `points_for_shot()`

| Shot Type | Default Value | Governable Parameter | Range |
|-----------|:---:|:---:|:---:|
| Three-pointer | 3 | `three_point_value` | 1-10 |
| Two-pointer (at rim, mid-range) | 2 | `two_point_value` | 1-10 |
| Free throw | 1 | `free_throw_value` | 1-5 |

Point values interact with the Elam margin. If governors increase `three_point_value` to 5, teams can reach the target score faster with three-pointers, making the Elam period shorter. If they decrease it to 1, the Elam period extends because each possession generates fewer points.

## Output in GameResult

**File:** `src/pinwheel/models/game.py` -- `GameResult`

| Field | Type | Description |
|-------|------|-------------|
| `elam_activated` | `bool` | Whether the Elam Ending was triggered |
| `elam_target_score` | `int \| None` | The target score, or None if Elam did not activate |

These fields are stored in `GameResultRow` and available in API responses, commentary context, and report data.

## Commentary and Narrative Integration

When `elam_activated` is True:
- **Commentary** (`commentary.py`): References the Elam Ending as a dramatic narrative pivot. Both AI-powered and mock commentary mention the target score and treat the Elam period as sudden death.
- **Simulation report** (`report.py`): Notes Elam activation and how it shaped the outcome.
- **Highlight reel** (`commentary.py`): Marks games with `[ELAM]` tag.

## Governance Implications

The Elam mechanics are highly governable:

- **Increasing `elam_margin`** (e.g., 15 -> 30): Makes Elam endings longer, more dramatic, with more fatigue buildup. Trailing teams have more time to come back.
- **Decreasing `elam_margin`** (e.g., 15 -> 5): Makes Elam endings shorter and more sudden. The team leading after Q3 has a massive advantage.
- **Changing `elam_trigger_quarter`** (e.g., 3 -> 1): Activates Elam after Q1 instead of Q3. Most of the game would be played without a clock. Extremely chaotic.
- **Setting `elam_trigger_quarter` to 4**: Would run all 4 standard quarters timed, then Elam as a "Q5." Makes the timed portion longer.
- **Changing point values**: Indirectly affects Elam duration by changing how quickly teams accumulate points toward the target.

## Edge Cases

1. **Both teams at 0 after Q3:** Target = 0 + 15 = 15. First to 15 wins. (Extremely unlikely but mechanically correct.)
2. **Safety cap during Elam:** If 300 total possessions are reached, `game_over` is set. The team with the higher score at that point wins. This is an emergency brake, not expected gameplay.
3. **All players fouled out:** If a team has no eligible players, the simulation still runs but with an empty active list. The possession resolution handles this gracefully (auto-turnover).
4. **Strategy during Elam:** Team strategies (from `/strategy`) remain in effect during the Elam period. A team with a "shoot the three" strategy will continue to favor three-pointers during Elam.
