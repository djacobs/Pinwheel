# Plan: Earned and Governed Move Acquisition

**Date:** 2026-02-14
**Status:** Draft
**Scope:** Simulation engine, governance layer, database, AI interpreter

## Context

SIMULATION.md (Decision #13) specifies three channels for move acquisition:

1. **Seeded at creation** (archetype moves) -- BUILT
2. **Earned through play** (stat milestone unlocks) -- NOT BUILT
3. **Governed** (proposed via governance) -- NOT BUILT

The `Move` model already has a `source: Literal["archetype", "earned", "governed"]` field,
so the data model is ready. But no code path exists to create moves with source `"earned"` or
`"governed"`, and the simulation engine has no mechanism to check milestone thresholds or
apply governance-granted moves to hoopers mid-season.

## What Exists Today

### Move Model (`src/pinwheel/models/team.py`, line 31-38)

```python
class Move(BaseModel):
    name: str
    trigger: str
    effect: str
    attribute_gate: dict[str, int] = Field(default_factory=dict)
    source: Literal["archetype", "earned", "governed"] = "archetype"
```

The `source` field already supports all three acquisition types. No changes needed here.

### Move Definitions (`src/pinwheel/core/moves.py`)

Eight moves are defined as module-level constants, all with `source="archetype"`. The functions
`check_gate()`, `check_trigger()`, `get_triggered_moves()`, and `apply_move_modifier()` operate
on any `Move` regardless of source -- they are source-agnostic. This is good: once a move is on
a hooper, it just works.

### Archetype Moves (`src/pinwheel/core/archetypes.py`, line 117-190)

Each archetype maps to 1 signature move via `ARCHETYPE_MOVES`. These are assigned during seeding
(`src/pinwheel/core/seeding.py`, line 63).

### Hooper DB Row (`src/pinwheel/db/models.py`, line 102)

```python
moves: Mapped[list] = mapped_column(JSON, default=list)
```

Moves are stored as JSON on the hooper row. The schema supports arbitrary move lists.

### Critical Gap: Moves Not Loaded in Game Loop

In `src/pinwheel/core/game_loop.py`, line 67, `_row_to_team()` creates hoopers with
`moves=[]`:

```python
hoopers.append(
    Hooper(
        ...
        moves=[],
        is_starter=idx < 3,
    )
)
```

This means **no moves are active during simulation today** unless they come from the
seeding path (which populates from `ARCHETYPE_MOVES`). The game loop discards DB-stored
moves. This is a prerequisite bug that must be fixed before either earned or governed
moves can work.

### Box Score Stats (`src/pinwheel/db/models.py`, `src/pinwheel/models/game.py`)

Per-game stats are stored in `BoxScoreRow` and `HooperBoxScore`: points, FGM, FGA,
3PM, 3PA, FTM, FTA, assists, steals, turnovers, minutes. These are the stats that
earned-move milestones would check against.

There is no season-aggregate stats table or function. Milestone checks would need to
sum box scores across all games in a season for a given hooper.

### Governance Interpreter (`src/pinwheel/ai/interpreter.py`)

The v2 interpreter already supports `effect_type: "meta_mutation"` which can write
metadata to hoopers. A governed move could be implemented as a meta_mutation that adds
a move to a hooper's move list. However, the current meta system writes to a JSON
`meta` column, not to the `moves` column directly. The action primitives include
`write_meta` but not `add_move`.

## What Needs to Be Built

### Phase 1: Fix Move Loading (Prerequisite)

**Goal:** Moves stored in the DB actually get loaded into simulation.

**Files to modify:**
- `src/pinwheel/core/game_loop.py` -- `_row_to_team()` must deserialize the hooper's
  `moves` JSON column into `Move` objects instead of hardcoding `moves=[]`.
- `src/pinwheel/db/repository.py` -- Verify that `create_hooper()` persists move data
  correctly as serializable dicts.

**Behavior:**
```python
# In _row_to_team():
move_data = a.moves or []  # type: ignore[attr-defined]
moves = [Move(**m) if isinstance(m, dict) else m for m in move_data]
hoopers.append(
    Hooper(
        ...
        moves=moves,
        ...
    )
)
```

**Why this comes first:** Without this fix, any moves added via earned or governed paths
would be stored in the DB but silently ignored during simulation.

### Phase 2: Earned Moves

**Goal:** Hoopers unlock new moves by hitting cumulative stat milestones across a season.

#### 2a. Milestone Definitions

Create a new module `src/pinwheel/core/milestones.py` with a registry of unlock conditions:

```python
@dataclass
class MoveMilestone:
    """A stat threshold that unlocks a new move."""
    move: Move                    # The move to grant
    stat: str                     # Box score stat name (e.g., "three_pointers_made")
    threshold: int                # Cumulative season total required
    description: str              # Human-readable unlock condition
    archetype_affinity: str = ""  # Optional: only hoopers of this archetype qualify

MILESTONES = [
    MoveMilestone(
        move=Move(
            name="Heat Check",
            trigger="made_three_last_possession",
            effect="+15% three-point, ignore IQ modifier",
            attribute_gate={"ego": 30},
            source="earned",
        ),
        stat="three_pointers_made",
        threshold=15,
        description="Hit 15 three-pointers in a season",
    ),
    MoveMilestone(
        move=Move(
            name="Pickpocket",
            trigger="opponent_iso",
            effect="+15% steal probability on ball handler",
            attribute_gate={"defense": 50},
            source="earned",
        ),
        stat="steals",
        threshold=20,
        description="Record 20 steals in a season",
    ),
    # ... additional milestones for assists, rebounds, points, etc.
]
```

Design considerations:
- Milestones should be achievable but meaningful. 15 three-pointers over a 21-game season
  is roughly 0.7 per game -- a sharpshooter hits this naturally, but other archetypes must
  stretch.
- Some milestones grant moves that overlap with archetype moves (e.g., a slasher earning
  Heat Check). This is fine -- if a hooper already has the move, skip the grant.
- `attribute_gate` on earned moves should be lower than archetype gates, since the hooper
  has already demonstrated the skill through performance.

#### 2b. Season Stats Aggregation

Add a repository method to aggregate box score stats for a hooper across a season:

**File:** `src/pinwheel/db/repository.py`

```python
async def get_hooper_season_stats(
    self, hooper_id: str, season_id: str
) -> dict[str, int]:
    """Sum box score stats for a hooper across all games in a season."""
    # Query: SELECT SUM(points), SUM(three_pointers_made), ...
    # FROM box_scores JOIN game_results ON ...
    # WHERE hooper_id = ? AND season_id = ?
```

#### 2c. Milestone Check in Game Loop

After each round's games are stored (end of `_phase_simulate_and_govern` or beginning
of `_phase_persist_and_finalize`), check all hoopers against all milestones:

**File:** `src/pinwheel/core/game_loop.py` (new function, called from `_phase_persist_and_finalize`)

```python
async def _check_earned_moves(
    repo: Repository,
    season_id: str,
    round_number: int,
    teams_cache: dict[str, Team],
) -> list[dict]:
    """Check all hoopers for newly earned moves. Returns list of grants."""
    grants = []
    for team in teams_cache.values():
        for hooper in team.hoopers:
            season_stats = await repo.get_hooper_season_stats(hooper.id, season_id)
            current_move_names = {m.name for m in hooper.moves}
            for milestone in MILESTONES:
                if milestone.move.name in current_move_names:
                    continue  # Already has this move
                if season_stats.get(milestone.stat, 0) >= milestone.threshold:
                    if check_gate(milestone.move, ...):  # Verify attribute gate
                        await repo.add_hooper_move(hooper.id, milestone.move.model_dump())
                        grants.append({
                            "hooper_id": hooper.id,
                            "hooper_name": hooper.name,
                            "move_name": milestone.move.name,
                            "milestone": milestone.description,
                        })
    return grants
```

#### 2d. Repository: Add Move to Hooper

**File:** `src/pinwheel/db/repository.py`

```python
async def add_hooper_move(self, hooper_id: str, move_data: dict) -> None:
    """Append a move to a hooper's moves JSON array."""
    # Load current moves, append new move, save back
```

This is a JSON array update on the `moves` column of `HooperRow`.

#### 2e. Event Bus Notification

When a move is earned, publish an event so the AI reporter and Discord can narrate it:

```python
await event_bus.publish("hooper.move_earned", {
    "hooper_id": hooper.id,
    "hooper_name": hooper.name,
    "team_id": hooper.team_id,
    "move_name": milestone.move.name,
    "milestone_description": milestone.description,
    "round_number": round_number,
})
```

### Phase 3: Governed Moves

**Goal:** Governors can propose granting specific moves to specific hoopers.

#### 3a. Extend AI Interpreter

The v2 interpreter already handles `meta_mutation` and `hook_callback` effect types.
Add a new effect type or extend `meta_mutation` to support move grants:

**Option A: New effect type `"move_grant"`**

Add to `EffectType` in `src/pinwheel/models/governance.py`:

```python
EffectType = Literal[
    "parameter_change",
    "meta_mutation",
    "hook_callback",
    "narrative",
    "composite",
    "move_grant",  # NEW
]
```

Add fields to `EffectSpec`:

```python
# move_grant
move_name: str | None = None
move_trigger: str | None = None
move_effect: str | None = None
move_attribute_gate: dict[str, int] | None = None
target_hooper_id: str | None = None    # Specific hooper, or...
target_archetype: str | None = None    # All hoopers of this archetype
target_team_id: str | None = None      # All hoopers on a team
```

**Option B: Use `meta_mutation` with a special `meta_field: "moves"`**

Less clean but avoids schema expansion. The effect system would recognize
`meta_field="moves"` and handle it specially in the enactment code.

**Recommendation: Option A.** Move grants are a first-class gameplay mechanic described
in SIMULATION.md. They deserve a first-class effect type that the interpreter can target
confidently.

#### 3b. Interpreter Prompt Update

Add move-grant patterns to `INTERPRETER_V2_SYSTEM_PROMPT` in
`src/pinwheel/ai/interpreter.py`:

```
## Move Grants

Governors can propose granting moves to hoopers. Examples:
- "Give all guards the crossover dribble" → move_grant to all hoopers with speed >= 60
- "Teach Nakamura the Clutch Gene" → move_grant to specific hooper
- "All lockdowns learn Pickpocket" → move_grant to all hoopers with archetype=lockdown

Available moves: {move_list}
```

Add a `_build_move_list()` helper that formats `ALL_MOVES` for the prompt.

#### 3c. Move Grant Enactment

When a governance proposal with a `move_grant` effect passes the vote:

**File:** `src/pinwheel/core/governance.py` (extend `_enact_effects()` or equivalent)

```python
if effect.effect_type == "move_grant":
    move = Move(
        name=effect.move_name,
        trigger=effect.move_trigger,
        effect=effect.move_effect,
        attribute_gate=effect.move_attribute_gate or {},
        source="governed",
    )
    target_hoopers = resolve_move_grant_targets(effect, repo, season_id)
    for hooper_id in target_hoopers:
        await repo.add_hooper_move(hooper_id, move.model_dump())
```

The target resolution function handles the three targeting modes:
- Specific hooper by ID
- All hoopers of an archetype
- All hoopers on a team

#### 3d. Mock Interpreter Patterns

Add patterns to `interpret_proposal_v2_mock()` for testing:

```python
# Pattern: "give X the Y move" or "teach X Y"
if any(k in text for k in ("give", "teach", "learn", "grant")):
    # Extract hooper name and move name from text
    ...
```

### Phase 4: Integration and Narrative

#### 4a. AI Report Context

When generating simulation and governance reports, include move acquisition events:

- Earned moves: "Nakamura earned Heat Check this round after hitting her 15th
  three-pointer of the season."
- Governed moves: "The Floor voted to grant all Lockdowns the Pickpocket move.
  Three hoopers across the league just got dangerous."

**Files:**
- `src/pinwheel/ai/report.py` -- Add move events to the context passed to the
  simulation report prompt.
- `src/pinwheel/ai/commentary.py` -- When a newly-acquired move triggers for the first
  time, the commentary should reference it.

#### 4b. Discord Notifications

Publish move grants to the #league-feed Discord channel:

**File:** `src/pinwheel/discord/bot.py`

Subscribe to `hooper.move_earned` and `hooper.move_governed` events and post embeds
showing the new move, the hooper who got it, and how (milestone or governance).

#### 4c. Web UI

Add move acquisition history to the hooper profile page. Show source (archetype icon,
trophy icon for earned, gavel icon for governed) and when it was acquired.

**File:** `templates/pages/team.html` or a new `templates/pages/hooper.html`

## Files to Create

| File | Purpose |
|------|---------|
| `src/pinwheel/core/milestones.py` | Milestone definitions and earned-move checking logic |

## Files to Modify

| File | Change |
|------|--------|
| `src/pinwheel/core/game_loop.py` | Fix `_row_to_team()` to load moves from DB; add `_check_earned_moves()` call |
| `src/pinwheel/db/repository.py` | Add `get_hooper_season_stats()`, `add_hooper_move()` |
| `src/pinwheel/models/governance.py` | Add `move_grant` to `EffectType`; add move-grant fields to `EffectSpec` |
| `src/pinwheel/ai/interpreter.py` | Add move-grant vocabulary to v2 system prompt; add mock patterns |
| `src/pinwheel/core/governance.py` | Enact `move_grant` effects on proposal passage |
| `src/pinwheel/ai/report.py` | Include move acquisition events in report context |
| `src/pinwheel/ai/commentary.py` | Reference newly acquired moves when they first trigger |
| `src/pinwheel/discord/bot.py` | Subscribe to move-earned/governed events for notifications |

## Testing Strategy

### Unit Tests

- **`tests/test_milestones.py`** (new)
  - Milestone check with stats below threshold returns no grants
  - Milestone check with stats at threshold returns the correct move
  - Milestone check skips moves the hooper already has
  - Attribute gate prevents unqualified hoopers from earning moves
  - Multiple milestones can trigger in the same round

- **`tests/test_moves.py`** (extend existing)
  - Earned and governed moves activate identically to archetype moves
  - `check_gate()`, `check_trigger()`, `apply_move_modifier()` work for all source types

- **`tests/test_governance.py`** (extend)
  - Move-grant effect is correctly parsed by mock interpreter
  - Move-grant effect targets the correct hoopers (by ID, archetype, team)
  - Governed move is persisted to the hooper's DB record

### Integration Tests

- **`tests/test_game_loop.py`** (extend)
  - After `step_round()`, hoopers who crossed a milestone have the earned move
  - Moves loaded from DB are used during simulation (not discarded)
  - A governance proposal granting a move results in the move appearing in the
    next round's simulation

- **`tests/test_api/test_e2e.py`** (extend)
  - Full round cycle with a hooper crossing a milestone; verify the move is present
    in the next round's box score data or play-by-play

### Determinism Tests

- Earned-move grants are deterministic given the same game results (no randomness
  in milestone checking)
- The simulation with earned moves is still deterministic given the same seed

## Risks and Open Questions

1. **Move balance:** Earned moves could create runaway effects -- a team that is
   already winning earns more moves, which makes them win more. Consider: should
   earned moves have weaker effects than archetype moves? Or should milestones be
   calibrated so struggling hoopers can earn them too (e.g., "10 turnovers unlocks
   Iron Will" as a consolation mechanic)?

2. **Move stacking:** Can a hooper accumulate many moves? The current system checks
   all moves each possession. 5+ moves on one hooper could produce outsized probability
   shifts. Consider: a cap of 3-4 moves per hooper, or diminishing returns when multiple
   moves trigger simultaneously.

3. **Governor targeting in move grants:** "Give my best player the best move" is a
   natural governance request. The interpreter must handle specific hooper references
   (names, not IDs) and validate they exist. The grounding eval should check move-grant
   proposals for valid hooper references.

4. **Move deduplication:** If archetype and earned moves have the same name (e.g.,
   Heat Check), should they stack? Current design says no -- skip if already present.
   But a governed move might have a modified version of an archetype move (different
   trigger or effect). The dedup check should be by name only.
