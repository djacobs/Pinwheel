# Narrative Physics — Making Pinwheel Alive at Runtime

## Context

Pinwheel's promise: "Starts as basketball, finishes as ???." Two gaps prevent this:

1. **Rule interpretation is single-parameter.** The AI maps every proposal to exactly one `RuleSet` field. "Make three-pointers worth 5" works. "Play 5 quarters" doesn't (no `num_quarters` field). "Circular court" has no parameter at all. The system can't handle structural or novel rules.

2. **Outputs are context-blind.** Commentary just got playoff awareness (this session), but everything else — reports, embeds, Discord messages, web pages — has no idea about streaks, standings implications, rule evolution narratives, or season arcs. A playoff game looks identical to a Tuesday night regular season game everywhere except commentary.

Both problems need solving at the **application level** — the app must be smart about this without Claude Code in the loop.

---

## Part 1: CLAUDE.md Game Richness Principle

Add to CLAUDE.md under a new `### Game Richness` section:

> Every player-facing output (commentary, reports, embeds, Discord messages, web pages) should reflect the full dramatic context of the current moment. When touching any output system, audit: *does this system know about playoffs, standings, streaks, rule changes, rivalries, and milestones?*
>
> A playoff game that reads like a regular-season game is a bug. An AI report that doesn't mention a team's 5-game win streak is a missed opportunity. A Discord embed for the championship finals that looks identical to Round 1 is broken.
>
> The simulation has the data — make sure the outputs use it. When adding or modifying any feature that produces player-visible text, check it against `docs/GAME_MOMENTS.md` for dramatic context that should be included.

Also create `docs/GAME_MOMENTS.md` — a checklist of dramatic contexts that outputs should reference when applicable:

- Playoff phase (semifinal, finals, elimination)
- Win/loss streaks (team on 3+ game streak)
- Comeback narratives (down big, rallied to win)
- Underdog upsets (low seed beating high seed)
- Blowouts (margin 15+)
- Rule change effects ("since three-pointers became worth 5, scoring has exploded")
- Individual dominance (player with 20+ in consecutive games)
- Rivalry rematches (teams that split the regular season series)
- Milestone games (first game, last regular season game, clinch/elimination)
- Season arc position (early season, playoff race, postseason)
- Governance narrative (rule just changed, voting window open, controversial proposal)

**Files:** `CLAUDE.md`, `docs/GAME_MOMENTS.md`

---

## Part 2: NarrativeContext — Runtime Game Awareness

### Design

A `NarrativeContext` dataclass computed once per round in `step_round()`, passed to every output system. This is the single "what's interesting right now?" object.

```python
@dataclass
class NarrativeContext:
    # Phase
    phase: str                           # "regular", "semifinal", "finals"
    season_arc: str                      # "early", "mid", "late", "playoff", "championship"
    round_number: int
    total_rounds: int                    # for "Round 5 of 9" context

    # Standings snapshot (computed before this round's games)
    standings: list[dict]                # [{team_id, team_name, wins, losses, rank, ...}]

    # Streaks (per team)
    streaks: dict[str, int]              # team_id → streak length (positive=wins, negative=losses)

    # Rule evolution
    active_rule_changes: list[dict]      # [{parameter, old_value, new_value, round_enacted, narrative}]
    rules_narrative: str                 # Human-readable: "Three-pointers are worth 5 (changed Round 4)"

    # Head-to-head (for this round's matchups)
    head_to_head: dict[str, dict]        # "teamA_vs_teamB" → {wins_a, wins_b, last_result}

    # Individual milestones
    hot_players: list[dict]              # [{hooper_id, name, team, streak_type, streak_length}]

    # Governance state
    governance_window_open: bool
    pending_proposals: int
    next_tally_round: int | None
```

### Where it's computed

New module: `core/narrative.py`

```python
async def build_narrative_context(
    repo: Repository,
    season_id: str,
    round_number: int,
    schedule: list[ScheduleRow],
    teams_cache: dict[str, Team],
    ruleset: RuleSet,
    governance_interval: int,
) -> NarrativeContext:
```

Queries:
- `repo.get_all_games(season_id)` → compute standings, streaks, head-to-head
- `repo.get_events_by_type(["rule.enacted"])` → active rule changes with narratives
- `repo.get_full_schedule(season_id)` → total rounds, season arc
- Schedule phase → playoff context
- Governance interval math → next tally round

### Where it's consumed

Every output function gets `narrative: NarrativeContext | None = None`:

| System | How it uses NarrativeContext |
|--------|---------------------------|
| `commentary.py` `_build_game_context()` | Adds standings, streaks, head-to-head, rule changes to AI context string |
| `commentary.py` mock | Uses phase for dramatic framing, streaks for narrative color |
| `report.py` simulation report | Adds standings snapshot, streak leaders, rule change effects |
| `report.py` governance report | Adds governance window timing, proposal impact context |
| `embeds.py` game result embed | Playoff badge, streak indicator, rule change summary |
| `embeds.py` standings embed | Streak indicators, playoff seed markers |
| `game_loop.py` event payloads | Attach `phase`, `rule_changes`, `streaks` to event data |
| Templates (arena, game) | Playoff badges, rule summaries, streak displays |

### Implementation order

1. `NarrativeContext` dataclass + `build_narrative_context()` in `core/narrative.py`
2. Wire into `step_round()` — compute once, pass everywhere
3. Extend `_build_game_context()` to include narrative data
4. Extend mock commentary to use streaks/standings
5. Extend report prompts to include narrative data
6. Extend embeds with playoff badges and streak indicators
7. Extend event payloads with narrative metadata
8. Template updates (playoff badges, rule summaries)
9. Tests at each layer

**Files:** `core/narrative.py` (new), `core/game_loop.py`, `ai/commentary.py`, `ai/report.py`, `discord/embeds.py`, `api/events.py`, templates

---

## Part 3: Adaptive Rule System — "Narrative Physics"

### The Problem Spectrum

| Level | Example | Current Support | Proposed Solution |
|-------|---------|----------------|-------------------|
| **Parameter tuning** | "Three-pointers worth 5" | Works today | No change needed |
| **Structural parameters** | "5 quarters", "4 players per team" | No field exists | Add fields to RuleSet |
| **Multi-parameter** | "Speed up the game" | Forced to pick ONE param | Multi-parameter interpretation |
| **Novel mechanics** | "Add a 4-point line" | Cannot express | GameEffect implementations |
| **Narrative rules** | "Circular court", "ball is on fire" | Cannot express | Narrative approximation + parameter mapping |

### Layer 1: Expand RuleSet with Structural Parameters

Add fields that represent structural game properties the sim should already respect:

```python
# Game structure
num_quarters: int = Field(default=4, ge=1, le=8)          # "5 quarters" → num_quarters=5
overtime_enabled: bool = True                                # "no overtime" → False
overtime_minutes: int = Field(default=5, ge=1, le=10)

# Roster
active_roster_size: int = Field(default=3, ge=2, le=5)     # "4 players per team" → 4
bench_size: int = Field(default=1, ge=0, le=5)

# Scoring
four_point_enabled: bool = False                             # "add a 4-point line" → True
four_point_value: int = Field(default=4, ge=1, le=10)
four_point_distance: float = Field(default=28.0, ge=25.0, le=35.0)

# Chaos modifiers (for "narrative physics" approximation)
turnover_chaos_factor: float = Field(default=1.0, ge=0.5, le=3.0)  # multiplier on TO probability
scoring_variance: float = Field(default=1.0, ge=0.5, le=2.0)       # multiplier on scoring randomness
fatigue_rate: float = Field(default=1.0, ge=0.5, le=3.0)           # multiplier on stamina drain
```

**Simulation changes:**
- `simulate_game()`: Use `rules.num_quarters` instead of hardcoded `elam_trigger_quarter + 1`
- `simulation.py` `_check_substitution()`: Respect `active_roster_size` for rotation logic
- `possession.py` `select_action()`: If `four_point_enabled`, add four-point shot type
- `scoring.py` `points_for_shot()`: Handle `four_point` shot type
- Apply chaos modifiers as multipliers on existing probabilities

**Key principle:** Every new field must be consumed by the simulation. No dead parameters.

### Layer 2: Multi-Parameter Interpretation

Change the AI interpreter to map proposals to **one or more** parameter changes.

**New model:**
```python
class RuleParameterChange(BaseModel):
    parameter: str
    new_value: int | float | bool
    old_value: int | float | bool | None = None

class RuleInterpretation(BaseModel):
    # CHANGED: list instead of single parameter
    changes: list[RuleParameterChange] = []

    # Keep backward compat
    @property
    def parameter(self) -> str | None:
        return self.changes[0].parameter if self.changes else None

    @property
    def new_value(self):
        return self.changes[0].new_value if self.changes else None

    @property
    def old_value(self):
        return self.changes[0].old_value if self.changes else None

    # Narrative description of the rule (for novel rules)
    narrative: str = ""                    # "The court is now circular"
    impact_analysis: str = ""
    confidence: float = 0.0
    clarification_needed: bool = False
    injection_flagged: bool = False
    rejection_reason: str | None = None
```

**Updated system prompt:**
- Change "EXACTLY ONE parameter" → "one or more related parameters"
- Add: "For novel rules that don't map directly to parameters, approximate the gameplay effect using multiple parameter adjustments and provide a `narrative` description"
- Response format becomes `"changes": [{"parameter": "...", "new_value": ...}, ...]`
- Add: "The `narrative` field should describe what the rule IS in plain language, separate from the parameter adjustments that approximate it"

**Updated enactment:**
- `apply_rule_change()` → `apply_rule_changes()` (iterate over `interpretation.changes`)
- `rule.enacted` event stores all changes + the narrative
- Tier = highest tier among all changed parameters

**Backward compatibility:**
- The `parameter`/`new_value`/`old_value` properties maintain the old interface
- Existing tests continue to work (single-param proposals still produce a 1-element `changes` list)
- `interpret_proposal_mock()` updated to return `changes` list format

### Layer 3: Narrative Rule Descriptions

When the AI interprets a novel proposal, it produces both:
1. **Parameter adjustments** that approximate the gameplay effect
2. **A narrative description** that gets stored and referenced by all output systems

Example: "Play on a circular court"
```json
{
  "changes": [
    {"parameter": "turnover_chaos_factor", "new_value": 1.3},
    {"parameter": "fatigue_rate", "new_value": 1.15}
  ],
  "narrative": "The court is now circular — no corners, no baseline, just a wide open ring. Defensive positioning is chaos, and everyone is running more.",
  "impact_analysis": "Increased turnovers due to unconventional spacing, higher fatigue from constant movement on the circular surface.",
  "confidence": 0.7
}
```

The narrative is:
- Stored in the `rule.enacted` event payload
- Loaded into `NarrativeContext.active_rule_changes`
- Referenced by commentary ("On the circular court, defensive spacing is a nightmare...")
- Referenced by reports ("Since the court went circular, turnovers are up 30%")
- Shown in Discord embeds and web pages

### Layer 4: GameEffect Implementations (Future)

The `GameEffect` Protocol and 11 hook points already exist but have zero implementations. For truly novel mechanics that parameter tuning can't approximate:

```python
class FourPointLineEffect:
    """Effect that adds a 4-point shot option when four_point_enabled=True."""
    def should_fire(self, hook, game_state, agent):
        return hook == HookPoint.POST_ACTION_SELECTION and rules.four_point_enabled

    def apply(self, hook, game_state, agent):
        # Modify shot selection to include 4-point option
        ...
```

**This is Phase 2 work** — the parameter expansion + multi-param interpretation + narrative descriptions handle 90% of cases. GameEffect implementations are for the remaining 10% where parameter approximation isn't enough.

### Layer 5: Rejection and Clarification (Safety Valve)

Not every proposal can or should be implemented. The system already has:
- `clarification_needed: bool` — ask the governor to be more specific
- `confidence < 0.5` → admin review required
- `parameter = null` → "doesn't map to any known rule"

For truly impossible proposals ("the game is now soccer"), the interpreter should:
1. Set confidence low (0.2-0.3)
2. Set clarification_needed = True
3. Provide impact_analysis explaining what it CAN do: "I can't make this soccer, but I can increase turnover rates and change scoring to approximate a different sport feel."
4. Let the governor revise or proceed

The governor always has the final say — they can confirm a low-confidence interpretation if they like the approximation.

---

## Part 4: User Management — Governor Visibility

### Current State

`PlayerRow` has: `discord_id`, `username`, `avatar_url`, `team_id`, `enrolled_season_id`, `created_at`, `last_login`. The event log tracks every governance action with `governor_id` + timestamp. Governor profile pages exist at `/governors/{player_id}`.

**What's missing:** No admin roster view, no "last action" tracking, no `enrolled_at` timestamp, no API endpoint for players, no Discord command to see the full league roster.

### Data Gap Fixes

#### 1. Add `enrolled_at` to PlayerRow

```python
# db/models.py — PlayerRow
enrolled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
```

Set in `enroll_player()` when a player joins a team for a season. Migration: `ALTER TABLE players ADD COLUMN enrolled_at DATETIME` in `main.py` startup.

#### 2. Add `last_action_at` to PlayerRow

```python
# db/models.py — PlayerRow
last_action_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
```

Updated every time a governance event is appended for this governor. Add a helper:

```python
# db/repository.py
async def touch_governor_activity(self, governor_id: str) -> None:
    """Update last_action_at to now. Called after any governance event."""
    player = await self.session.get(PlayerRow, governor_id)
    if player:
        player.last_action_at = datetime.now(UTC)
        await self.session.flush()
```

Call `touch_governor_activity()` from:
- `submit_proposal()` — after proposal event appended
- `cast_vote()` — after vote event appended
- `confirm_proposal()` — after confirm event
- Token trade acceptance — after trade event

Migration: `ALTER TABLE players ADD COLUMN last_action_at DATETIME` in `main.py`.

### API Endpoint

```python
# api/teams.py (or new api/players.py)
GET /api/players?season_id=XXX

Response: [
  {
    "id": "player-uuid",
    "discord_id": "123456789",
    "username": "djacobs",
    "avatar_url": "https://...",
    "team_id": "team-uuid",
    "team_name": "Rose City Thorns",
    "enrolled_at": "2026-02-12T...",
    "last_action_at": "2026-02-13T...",
    "created_at": "2026-02-12T...",
    "activity": {
      "proposals_submitted": 3,
      "proposals_passed": 1,
      "votes_cast": 7,
      "token_balance": {"propose": 2, "amend": 1, "boost": 2}
    }
  },
  ...
]
```

Sorted by `enrolled_at` desc (newest first). Optional query params: `?team_id=X`, `?sort=last_action`.

Repository method:
```python
async def get_players_with_activity(self, season_id: str) -> list[dict]:
    """Get all enrolled players with computed activity stats."""
    players = await self.get_players_for_season(season_id)
    result = []
    for p in players:
        activity = await self.get_governor_activity(p.id, season_id)
        team = await self.get_team(p.team_id) if p.team_id else None
        result.append({
            "id": p.id,
            "discord_id": p.discord_id,
            "username": p.username,
            "avatar_url": p.avatar_url,
            "team_id": p.team_id,
            "team_name": team.name if team else None,
            "enrolled_at": p.enrolled_at,
            "last_action_at": p.last_action_at,
            "created_at": p.created_at,
            "activity": activity,
        })
    return result
```

### Admin Dashboard — `/admin/players`

A web page (auth-gated like `/admin/evals`) showing:

```
┌─────────────────────────────────────────────────────────────┐
│ Governor Roster — Season 1                    [4 governors] │
├─────────────────────────────────────────────────────────────┤
│ Name          │ Team              │ Joined    │ Last Active │
│               │                   │           │ Proposals/  │
│               │                   │           │ Votes       │
├───────────────┼───────────────────┼───────────┼─────────────┤
│ djacobs       │ Rose City Thorns  │ Feb 12    │ 2h ago      │
│               │                   │           │ 3P / 7V     │
├───────────────┼───────────────────┼───────────┼─────────────┤
│ player2       │ Burnside Breakers │ Feb 12    │ 1d ago      │
│               │                   │           │ 1P / 4V     │
├───────────────┼───────────────────┼───────────┼─────────────┤
│ player3       │ Hawthorne Hammers │ Feb 13    │ Never       │
│               │                   │           │ 0P / 0V     │
└─────────────────────────────────────────────────────────────┘
```

Each row links to `/governors/{player_id}` for full detail. "Never" = enrolled but took no governance actions. Color-code "Last Active" (green = today, yellow = 1-3 days, red = 3+ days).

**Template:** `templates/pages/admin_players.html`
**Route:** `api/pages.py` — add `/admin/players` with admin auth check (same pattern as `/admin/evals`)

### Discord `/roster` Command

New slash command for admins:

```python
@app_commands.command(name="roster", description="Show all governors in the league")
async def roster(self, interaction: discord.Interaction):
```

Output embed:
```
📋 Governor Roster — Season 1

Rose City Thorns (2 governors)
  • djacobs — joined Feb 12, last active 2h ago (3P/7V)
  • player4 — joined Feb 13, last active 1d ago (1P/2V)

Burnside Breakers (1 governor)
  • player2 — joined Feb 12, last active 1d ago (1P/4V)

Hawthorne Hammers (1 governor)
  • player3 — joined Feb 13, never active (0P/0V)
```

If roster is large, paginate with Discord button navigation.

### Implementation Steps

1. Add `enrolled_at` + `last_action_at` columns (migration in `main.py`)
2. Set `enrolled_at` in `enroll_player()`
3. Add `touch_governor_activity()` to repository, call from governance functions
4. Add `get_players_with_activity()` repository method
5. Add `GET /api/players` endpoint
6. Add `/admin/players` web page + template
7. Add `/roster` Discord command
8. Tests: player activity tracking, API endpoint, roster command

**Files:** `db/models.py`, `db/repository.py`, `main.py`, `api/players.py` (new) or `api/teams.py`, `api/pages.py`, `templates/pages/admin_players.html` (new), `discord/bot.py`, `core/governance.py`

---

## Implementation Phases

### Phase A: Foundation (NarrativeContext + CLAUDE.md)
1. Add CLAUDE.md game richness principle
2. Create `docs/GAME_MOMENTS.md` checklist
3. Build `core/narrative.py` with `NarrativeContext` + `build_narrative_context()`
4. Wire into `step_round()` — compute and pass to commentary/reports
5. Extend `_build_game_context()` to include narrative data
6. Tests for narrative context computation

### Phase B: Multi-Parameter Interpretation
1. Add `RuleParameterChange` model, update `RuleInterpretation` with `changes` list + backward-compat properties
2. Update AI interpreter system prompt for multi-param + narrative
3. Update `interpret_proposal_mock()` for multi-param
4. Update `apply_rule_change()` → `apply_rule_changes()` for lists
5. Update `rule.enacted` event to store changes list + narrative
6. Update tier detection for multi-param (max tier)
7. Update Discord views to display multi-param interpretations
8. Tests for multi-param interpretation and enactment

### Phase C: Expanded RuleSet
1. Add structural parameters (`num_quarters`, `active_roster_size`, chaos modifiers)
2. Wire each new parameter into simulation (no dead params)
3. Update `_build_parameter_description()` to include new fields
4. Update mock interpreter keyword patterns for new params
5. Tests for each new parameter's simulation effect

### Phase D: Narrative Rules + Output Integration
1. Store and load narrative descriptions from `rule.enacted` events
2. Feed narratives into NarrativeContext
3. Extend embeds with playoff badges, streak indicators, rule summaries
4. Extend report prompts with narrative context
5. Template updates (arena, game detail pages)
6. SSE event payload enrichment
7. Tests for narrative flow through all outputs

### Phase E: User Management
1. Add `enrolled_at` + `last_action_at` columns (migration)
2. Set `enrolled_at` in `enroll_player()`, add `touch_governor_activity()`
3. Call `touch_governor_activity()` from governance functions
4. Add `get_players_with_activity()` repository method
5. Add `GET /api/players` endpoint
6. Add `/admin/players` web page + template
7. Add `/roster` Discord command
8. Tests for player activity tracking, API, roster

### Phase F: GameEffect Implementations (Future)
1. Implement concrete GameEffect classes for specific novel mechanics
2. Wire effects into game_loop (currently empty list)
3. AI interpreter can recommend effects for truly novel proposals
4. Tests for each effect

---

## Verification

- `uv run pytest -x -q` — all tests pass at each phase boundary
- `uv run ruff check src/ tests/` — zero lint errors
- Manual: `/join` a team, verify `enrolled_at` is set
- Manual: `/propose` a rule, verify `last_action_at` updates
- Manual: Visit `/admin/players`, verify roster with activity stats
- Manual: Run `/roster` in Discord, verify governor list with last-active times
- Manual: `GET /api/players?season_id=X`, verify JSON response with activity
- Manual: Submit a multi-parameter proposal via `/propose`, verify interpretation shows multiple changes
- Manual: Submit a novel proposal ("circular court"), verify narrative description appears in commentary and reports
- Manual: Check that regular season games don't mention playoffs, playoff games do mention stakes
- Manual: Check that streaks appear in commentary after 3+ game runs
- Manual: Check that rule change narratives appear in reports after enactment

---

## Key Files

| File | Role |
|------|------|
| `CLAUDE.md` | Game richness principle |
| `docs/GAME_MOMENTS.md` | Dramatic context checklist (new) |
| `core/narrative.py` | NarrativeContext computation (new) |
| `core/game_loop.py` | Wire narrative context into round execution |
| `models/rules.py` | Expanded RuleSet with structural + chaos params |
| `models/governance.py` | Multi-parameter RuleInterpretation |
| `ai/interpreter.py` | Multi-param + narrative interpretation |
| `ai/commentary.py` | Consume NarrativeContext |
| `ai/report.py` | Consume NarrativeContext |
| `core/simulation.py` | Respect new structural parameters |
| `core/possession.py` | Four-point line, chaos modifiers |
| `core/scoring.py` | Four-point scoring |
| `core/governance.py` | Multi-param enactment + touch_governor_activity |
| `discord/views.py` | Display multi-param interpretations |
| `discord/embeds.py` | Playoff badges, streak indicators |
| `discord/bot.py` | `/roster` command |
| `db/models.py` | `enrolled_at`, `last_action_at` columns |
| `db/repository.py` | `get_players_with_activity()`, `touch_governor_activity()` |
| `api/players.py` | `GET /api/players` endpoint (new) |
| `api/pages.py` | `/admin/players` route |
| `templates/pages/admin_players.html` | Admin roster dashboard (new) |
| `templates/` | Playoff badges, rule summaries, streaks |
| `core/hooks.py` | GameEffect implementations (Phase F) |
