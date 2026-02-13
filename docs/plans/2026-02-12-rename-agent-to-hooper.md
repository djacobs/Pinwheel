# Rename Agent → Hooper + Add Substitution Logic

## Context

Two problems:

1. **"Agent" is the wrong word.** In an AI project, "Agent" reads as "AI agent." But these are simulated basketball players — not AI actors. The user suggested "Hooper" — basketball slang, unambiguous, fun. The rename is system-wide (~400+ occurrences across models, DB, API, templates, tests, docs).

2. **Bench players never enter the game.** Each team has 4 players (3 starters + 1 bench) but the simulation only uses starters. The bench player exists in `GameState` but `home_starters`/`away_starters` filters on `is_starter=True`, and nothing ever promotes a bench player. When a starter fouls out, the team just plays short-handed. No fatigue-based rotation exists either.

## Work Item 1: Substitution Logic

Add substitution mechanics to the simulation engine. Two triggers:

### 1a. Foul-out substitution (immediate)

When a starter is ejected (`possession.py:325`), immediately promote the bench player.

**File:** `src/pinwheel/core/state.py`
- Add `on_court: bool` field to `AgentState`, initialized from `agent.is_starter`
- Change `home_starters` property: filter by `on_court and not ejected` (not `is_starter`)
- Rename property to `home_active` / `away_active` (since they're no longer necessarily "starters")
- Add `home_bench` / `away_bench` properties: `on_court == False and not ejected`
- Add method `substitute(out: HooperState, in_: HooperState)` on GameState — sets `out.on_court = False`, `in_.on_court = True`

**File:** `src/pinwheel/core/simulation.py`
- Add `_check_substitution(game_state, rules)` function
- Called after each possession in `_run_quarter` and `_run_elam`
- Logic: for each team, if any active player is ejected and bench has available players, sub in the best-fit bench player (highest stamina)
- Also called at quarter breaks for fatigue rotation

**File:** `src/pinwheel/core/possession.py`
- After ejection at line 326, no change needed — the substitution check happens back in simulation.py after the possession resolves

### 1b. Fatigue substitution (quarter breaks)

At quarter breaks (simulation.py lines 223-227), check if any starter's stamina is below a threshold. If so, swap them with a bench player who has higher stamina.

**File:** `src/pinwheel/models/rules.py`
- Add governable parameter: `substitution_stamina_threshold: float = Field(default=0.35, ge=0.1, le=0.8)` in Tier 1
- This is the stamina level below which a player gets subbed out at a break

**File:** `src/pinwheel/core/simulation.py`
- In the quarter break logic (after `_quarter_break_recovery` / `_halftime_recovery`):
  - For each team, find the active player with lowest stamina
  - If that stamina < `rules.substitution_stamina_threshold` AND a bench player exists with higher stamina, swap them
  - The bench player enters at their current stamina (they recover during quarters too since they're in `home_agents`)

### 1c. Substitution logging

- Add `substitution` as a possible action type in `PossessionLog` or add a separate substitution entry in the play-by-play
- Log: who went out, who came in, reason (foul-out / fatigue)

### 1d. Update `offense`/`defense` properties

**File:** `src/pinwheel/core/state.py`
- `offense` and `defense` properties already delegate to `home_starters`/`away_starters` — once those are updated to use `on_court`, everything flows through

### 1e. Tests

- Test foul-out triggers bench promotion
- Test fatigue threshold triggers rotation at quarter break
- Test team with no bench (all ejected) plays short-handed
- Test bench player stamina is correct (they recover during breaks too)
- Test substitution appears in play-by-play
- Update existing simulation tests for the `on_court` field

## Work Item 2: Rename Agent → Hooper

Mechanical rename across the full stack. Do this AFTER substitution logic is in place so we only touch each file once.

### 2a. Models

| File | Changes |
|------|---------|
| `models/team.py` | `Agent` → `Hooper`, docstrings, field descriptions |
| `models/game.py` | `AgentBoxScore` → `HooperBoxScore`, `agent_id` → `hooper_id`, `agent_name` → `hooper_name` |
| `models/tokens.py` | `AgentTrade` → `HooperTrade`, `AgentTradeStatus` stays (or → `HooperTradeStatus`), field names `offered_agent_ids` → `offered_hooper_ids` etc. |

### 2b. State & simulation

| File | Changes |
|------|---------|
| `core/state.py` | `AgentState` → `HooperState`, `agent: Agent` → `hooper: Hooper`, all `self.agent.*` → `self.hooper.*` |
| `core/simulation.py` | `_build_agent_states` → `_build_hooper_states`, `AgentBoxScore` → `HooperBoxScore`, all `agent_state` vars |
| `core/possession.py` | All `AgentState` types, `agent` param names, `handler.agent.id` → `handler.hooper.id` |
| `core/defense.py` | All `AgentState` types, `.agent.id` → `.hooper.id` |
| `core/scoring.py` | All `AgentState` types |
| `core/moves.py` | All `AgentState` types, `.agent.attributes` → `.hooper.attributes` |
| `core/seeding.py` | `Agent(...)` → `Hooper(...)`, `agents_per_team` → `hoopers_per_team` |
| `core/tokens.py` | `AgentTrade` refs, trade function names |
| `core/game_loop.py` | `Agent` import, `_team_row_to_domain`, `agent_data`/`agent_names` vars |

### 2c. Database layer

| File | Changes |
|------|---------|
| `db/models.py` | `AgentRow` → `HooperRow`, `__tablename__ = "hoopers"`, `TeamRow.agents` → `TeamRow.hoopers`, column `agent_id` → `hooper_id` in BoxScoreRow |
| `db/repository.py` | `create_agent` → `create_hooper`, `get_agent` → `get_hooper`, `swap_agent_team` → `swap_hooper_team`, etc. All ~10 methods |

**Note:** Table rename from `agents` to `hoopers` means existing databases need re-seeding. Acceptable for hackathon — no migration needed.

### 2d. API routes

| File | Changes |
|------|---------|
| `api/pages.py` | Routes `/agents/{agent_id}` → `/hoopers/{hooper_id}`, function names, template vars `agent` → `hooper`, `agents` → `hoopers`, `total_agents` removed (already done), `_AGENT_BEHAVIOR_RULES` → `_HOOPER_BEHAVIOR_RULES` |
| `api/games.py` | `agent_id` → `hooper_id` in box score dicts |
| `api/teams.py` | `agent_count` → `hooper_count`, `team.agents` → `team.hoopers` |

### 2e. Templates

| File | Changes |
|------|---------|
| `templates/pages/agent.html` | Rename file → `hooper.html`, all `agent.*` → `hooper.*`, CSS classes `.agent-*` → `.hooper-*` |
| `templates/pages/team.html` | `{% for agent in agents %}` → `{% for hooper in hoopers %}`, CSS classes |
| `templates/pages/game.html` | `agent_name` → `hooper_name` |
| `templates/pages/play.html` | "agents" → "hoopers" in copy text |
| `templates/pages/rules.html` | "agents" → "hoopers" in example text |
| `templates/pages/home.html` | "agents" → "hoopers" in descriptions |
| `templates/components/spider_chart.html` | `agent_points` → `hooper_points` etc. |

### 2f. CSS

**File:** `static/css/pinwheel.css`
- Rename all `.agent-*` selectors to `.hooper-*`

### 2g. Tests

All test files with Agent references: `test_simulation.py`, `test_seeding.py`, `test_models.py`, `test_pages.py`, `test_game_loop.py`, `test_db.py`, `test_commentary.py`, `test_discord.py`, `conftest.py`, `test_evals/*`

### 2h. Scripts

- `scripts/demo_seed.py` — `"agents"` keys in team data, `create_agent` → `create_hooper`

### 2i. Docs & CLAUDE.md

- `docs/GLOSSARY.md` — rename primary term from Agent to Hooper
- `CLAUDE.md` — update all references
- Other docs: SIMULATION.md, GAME_LOOP.md, PLAYER.md, VIEWER.md, etc.

### 2j. Discord bot

- `discord/bot.py`, `discord/embeds.py`, `discord/views.py` — agent references in commands and embeds

## Execution Order

1. **Substitution logic** (Work Item 1) — state.py, simulation.py, rules.py, tests
2. **Rename Agent → Hooper** (Work Item 2) — bottom-up: models → state → DB → core → API → templates → tests → docs
3. **Re-seed and verify** — drop DB, re-seed, run full test suite, run demo

Cannot parallelize — the rename must happen after substitution logic is complete, and the rename itself is sequential (each layer depends on the previous).

## Verification

1. `uv run pytest -x -q` — all tests pass
2. `uv run ruff check src/ tests/` — zero lint
3. Simulation tests confirm bench player enters on foul-out
4. Simulation tests confirm fatigue rotation at quarter breaks
5. No remaining references to "Agent" in code (except `ANTHROPIC_API_KEY` and similar non-game uses)
6. `/hoopers/{id}` route works, `/agents/{id}` no longer exists
7. Demo re-run shows correct naming in UI
