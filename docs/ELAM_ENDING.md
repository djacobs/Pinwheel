# Elam Ending Mechanics

Reference documentation for Pinwheel Fates' Elam Ending implementation.

**Last updated:** 2026-02-15

---

## What Is the Elam Ending?

The Elam Ending replaces the game clock with a target score in the final period. Instead of playing until time expires, teams play until one team reaches a target score derived from the leading team's score at the end of the penultimate quarter. This eliminates intentional fouling and creates a definitive finish to every game.

Pinwheel's implementation is inspired by the real-world Elam Ending used in The Basketball Tournament (TBT) and the NBA All-Star Game (2020-2024).

---

## Game Structure

**Source:** `src/pinwheel/core/simulation.py`

With default settings (`elam_trigger_quarter=3`, `quarter_minutes=10`):

```
Q1 (10 minutes, timed)  ->  quarter break recovery
Q2 (10 minutes, timed)  ->  halftime recovery (larger stamina boost)
Q3 (10 minutes, timed)  ->  quarter break recovery
Elam Period (no clock, target score)
```

The simulation runs timed quarters in a loop, then transitions to the Elam period:

```python
for q in range(1, elam_trigger_quarter + 1):
    _run_quarter(q)        # timed quarter with clock
    _check_substitution()  # fatigue/foul-out subs
    stamina_recovery()     # quarter break or halftime

if not game_over:
    _run_elam()            # target-score period
```

---

## Governable Parameters

**Source:** `src/pinwheel/models/rules.py`

| Parameter | Default | Range | Tier | Description |
|-----------|:-------:|:-----:|:----:|-------------|
| `elam_trigger_quarter` | 3 | 1-4 | 1 (Game Mechanics) | Quarter after which Elam begins |
| `elam_margin` | 15 | 5-40 | 1 (Game Mechanics) | Points added to leading score for target |
| `quarter_minutes` | 10 | 3-20 | 1 (Game Mechanics) | Length of each timed quarter (Elam has no clock) |

Both `elam_trigger_quarter` and `elam_margin` can be changed by governor proposals with a simple majority vote (Tier 1).

---

## How the Elam Ending Works

### Trigger

The Elam Ending activates after the last timed quarter completes (after `elam_trigger_quarter` quarters have been played). It does NOT activate if the game is already over (which would only happen if the safety cap was hit during timed quarters -- extremely unlikely).

### Target Score Calculation

**Source:** `src/pinwheel/core/simulation.py::_run_elam()`

```python
leading_score = max(game_state.home_score, game_state.away_score)
game_state.elam_target_score = leading_score + elam_margin
```

The target is always based on the **leading** team's score. If the score is tied, the target is `tied_score + elam_margin`, meaning both teams need to score the full margin to win.

### Example (Default Settings)

```
Score after Q3:  Home 35, Away 32
Target:          max(35, 32) + 15 = 50
Home needs:      15 more points
Away needs:      18 more points
Winner:          First team to reach 50
```

### Elam Period Flow

1. Compute `elam_target_score` and set `elam_activated = True`
2. Fire `ELAM_START` hook (legacy) and `sim.elam.start` hook (new effects)
3. Run possessions **without a game clock** (no time limit)
4. After each possession, check:
   - If `home_score >= elam_target_score` -> home wins, game over
   - If `away_score >= elam_target_score` -> away wins, game over
5. Possessions alternate normally between home and away
6. Foul-out substitutions still happen
7. Safety cap (300 possessions) still applies

### How the Game Ends

The first team to reach or exceed the target score wins. The check is `>=`, not `==`, so a team at 48 that scores a 3-pointer (target 50) wins with 51 points. The margin of victory can exceed the point value of the final shot.

---

## Key Behaviors During the Elam Period

### No Overtime

The Elam Ending eliminates ties and overtime entirely. One team **will** reach the target score (unless the safety cap is hit at 300 total possessions, which is an emergency brake for infinite-loop prevention).

### No Clock Pressure

During the Elam period, there is no game clock. `game_clock_seconds` is not decremented. Possession time is still tracked in `PossessionLog.time_used` for analytics but does not affect gameplay.

### Stamina Drain Continues

Hoopers continue to drain stamina during the Elam period based on `stamina_drain_rate`. However, there are **no quarter breaks** for recovery during Elam. Longer Elam endings (when the trailing team is far behind) lead to increasing fatigue, which reduces shooting accuracy via `compute_stamina_modifier()`. This creates natural tension as the period extends.

### Substitutions During Elam

**Foul-out substitutions** still happen -- if a player fouls out during Elam, they are replaced by the best available bench player.

**Fatigue-based substitutions** do NOT happen during Elam because those are triggered at quarter breaks (`_check_substitution(reason="fatigue")` runs after quarters, not during the Elam period).

### Strategy Remains Active

Team strategies set via `/strategy` (three-point bias, pace modifier, defensive intensity, etc.) remain in effect during the Elam period. A team with a "shoot the three" strategy will continue to favor three-pointers.

---

## Scoring During the Elam Period

**Source:** `src/pinwheel/core/scoring.py`

The scoring module is Elam-agnostic. `resolve_shot()` and `compute_shot_probability()` work identically during timed quarters and the Elam period. Shot probability depends on:

1. **Base probability** -- logistic curve based on shooter's `scoring` attribute and shot type
2. **Defense contest** -- defender's `defense` attribute reduces probability (multiplier 0.5-1.0)
3. **IQ modifier** -- shooter's `iq` provides a small bonus (multiplier 0.9-1.1)
4. **Stamina modifier** -- current stamina affects accuracy (multiplier 0.7-1.0)

The only Elam-specific behavior is the target score check in `_run_elam()` after each possession resolves.

### Point Values (Governable)

| Shot Type | Default Value | Governable Parameter | Range |
|-----------|:---:|:---:|:---:|
| Three-pointer | 3 | `three_point_value` | 1-10 |
| Two-pointer (at rim, mid-range) | 2 | `two_point_value` | 1-10 |
| Free throw | 1 | `free_throw_value` | 1-5 |

Point values interact with the Elam margin. If governors increase `three_point_value` to 5, teams accumulate points faster, making the Elam period shorter. If they decrease it to 1, the Elam period extends because each possession generates fewer points.

---

## Hook Points

The simulation fires these hooks during the Elam sequence:

| Hook | When | Available Context |
|------|------|------------------|
| `sim.elam.start` | After target score computed, before first Elam possession | `game_state` with `elam_target_score` and `elam_activated=True` |
| `sim.possession.pre` | Before each Elam possession | Normal possession context |
| `sim.shot.pre` | Before each shot in Elam | Shot type, shooter, defender |
| `sim.shot.post` | After each shot in Elam | Shot result, points scored |
| `sim.game.end` | After the winning basket | Final scores, winner determined |

The legacy `HookPoint.ELAM_START` is also fired for backward compatibility.

---

## Output in GameResult

**Source:** `src/pinwheel/models/game.py`

| Field | Type | Description |
|-------|------|-------------|
| `elam_activated` | `bool` | Whether the Elam Ending was triggered |
| `elam_target_score` | `int | None` | The target score, or `None` if Elam did not activate |

These fields are stored in `GameResultRow` and available in:
- API responses (`GET /api/games/{game_id}`)
- Commentary context (AI and mock)
- Simulation reports
- Arena page display
- Highlight reels

---

## Commentary and Narrative Integration

When `elam_activated` is True:

- **Commentary** (`ai/commentary.py`): References the Elam Ending as a dramatic narrative pivot. Both AI-powered and mock commentary mention the target score and treat the Elam period as sudden death.
- **Simulation report** (`ai/report.py`): Notes Elam activation and how it shaped the outcome.
- **Highlight reel** (`ai/commentary.py`): Marks games with an `[ELAM]` tag.
- **Arena page** (`api/pages.py`): Displays `elam_target` alongside game scores.

---

## Governance Implications

The Elam mechanics are highly governable and can produce dramatically different gameplay:

| Change | Effect |
|--------|--------|
| Increase `elam_margin` (e.g., 15 -> 30) | Longer Elam endings, more fatigue, trailing teams have more time to recover |
| Decrease `elam_margin` (e.g., 15 -> 5) | Shorter, more sudden endings. Team leading after Q3 has a massive advantage |
| Change `elam_trigger_quarter` to 1 | Elam activates after Q1. Most of the game is untimed. Extremely chaotic |
| Change `elam_trigger_quarter` to 4 | All 4 quarters are timed, Elam becomes a "Q5". Standard basketball with an Elam coda |
| Increase `three_point_value` | Teams reach target faster, shorter Elam periods |
| Decrease all point values | Longer Elam periods, more possessions, more fatigue buildup |

---

## Edge Cases

### Both Teams at 0 After Last Timed Quarter

Target = 0 + 15 = 15. First to 15 wins. Mechanically correct but extremely unlikely given standard game parameters.

### Safety Cap During Elam

If 300 total possessions are reached across the entire game, `game_over` is set. The team with the higher score at that point wins. This is an emergency brake to prevent infinite loops, not expected gameplay.

### All Players Fouled Out

If a team has no eligible players, the simulation still runs but with an empty active list. Possession resolution handles this gracefully (auto-turnover).

### Score Exceeds Target

Since the check is `>=`, the winning team's final score can exceed the target. A team at 48 scoring a 3-pointer with target 50 finishes with 51.

---

## Source Files

| File | Purpose |
|------|---------|
| `src/pinwheel/core/simulation.py` | `_run_elam()`, `simulate_game()`, quarter loop |
| `src/pinwheel/core/scoring.py` | `resolve_shot()`, `compute_shot_probability()`, `points_for_shot()` |
| `src/pinwheel/core/possession.py` | `resolve_possession()` -- works identically in timed and Elam periods |
| `src/pinwheel/core/state.py` | `GameState` with `elam_target_score` and `elam_activated` fields |
| `src/pinwheel/core/hooks.py` | `HookPoint.ELAM_START` (legacy) and `sim.elam.start` (new effects) |
| `src/pinwheel/models/rules.py` | `RuleSet` with `elam_trigger_quarter`, `elam_margin` parameters |
| `src/pinwheel/models/game.py` | `GameResult` with `elam_activated`, `elam_target_score` fields |
