# Plan: Token Cost Tracking Dashboard

**Date:** 2026-02-14
**Status:** Draft
**Depends on:** INSTRUMENTATION.md spec, existing eval dashboard pattern

## Problem

Pinwheel Fates makes multiple Claude API calls per round (commentary per game, highlight reel, simulation report, governance report, private reports per governor, rule evaluation, and injection classification). There is no visibility into how much these calls cost, no way to track daily/weekly spend, and no way to identify which call types dominate the budget. INSTRUMENTATION.md describes a token cost dashboard but it has not been built.

## Current State: All AI Call Sites

After auditing the codebase, the following AI call sites exist:

### 1. `src/pinwheel/ai/interpreter.py`

| Function | Model | max_tokens | Purpose |
|----------|-------|------------|---------|
| `interpret_proposal()` | `claude-sonnet-4-5-20250929` | 500 | Translate natural language proposal to structured rule change (v1) |
| `interpret_strategy()` | `claude-sonnet-4-5-20250929` | 300 | Translate natural language strategy to structured parameters |
| `interpret_proposal_v2()` | `claude-sonnet-4-5-20250929` | 1500 | Translate proposal to structured effects (v2, supports hooks/meta/narrative) |

### 2. `src/pinwheel/ai/report.py`

| Function | Model | max_tokens | Purpose |
|----------|-------|------------|---------|
| `generate_simulation_report()` | `claude-sonnet-4-5-20250929` | 1500 | Round simulation report |
| `generate_governance_report()` | `claude-sonnet-4-5-20250929` | 1500 | Round governance report |
| `generate_private_report()` | `claude-sonnet-4-5-20250929` | 1500 | Per-governor private mirror |
| `generate_report_with_prompt()` | `claude-sonnet-4-5-20250929` | 1500 | A/B testing variant (used by M.2 eval) |

All report functions delegate to `_call_claude()` which makes the actual API call.

### 3. `src/pinwheel/ai/commentary.py`

| Function | Model | max_tokens | Purpose |
|----------|-------|------------|---------|
| `generate_game_commentary()` | `claude-sonnet-4-5-20250929` | 400 | Per-game broadcaster commentary |
| `generate_highlight_reel()` | `claude-sonnet-4-5-20250929` | 300 | Round highlights summary |

### 4. `src/pinwheel/ai/classifier.py`

| Function | Model | max_tokens | Purpose |
|----------|-------|------------|---------|
| `classify_injection()` | `claude-haiku-4-5-20251001` | 200 | Pre-flight prompt injection classification |

### 5. `src/pinwheel/evals/rule_evaluator.py` (inferred from game_loop import)

| Function | Model | max_tokens | Purpose |
|----------|-------|------------|---------|
| `evaluate_rules()` | Opus or Sonnet | ~1500 | Admin-level rule analysis after each round |

### Per-Round Cost Profile (estimated)

With 2 games per round, 3 active governors, and API key set:

| Call Type | Count/Round | max_tokens | Model |
|-----------|-------------|------------|-------|
| Game commentary | 2 | 400 | Sonnet |
| Highlight reel | 1 | 300 | Sonnet |
| Simulation report | 1 | 1500 | Sonnet |
| Governance report | 1 | 1500 | Sonnet |
| Private reports | 3 | 1500 | Sonnet |
| Rule evaluation | 1 | ~1500 | Sonnet/Opus |
| **Total Sonnet calls/round** | **~9** | | |

Injection classification and interpretation calls happen on-demand (when governors `/propose`), not per-round.

## Design

### 1. Token Usage Logging

Create a lightweight wrapper that records usage metadata for every AI call.

**New file: `src/pinwheel/ai/usage.py`**

```python
@dataclass
class AICallRecord:
    call_type: str          # "commentary", "report.simulation", "interpreter.v2", etc.
    model: str              # "claude-sonnet-4-5-20250929"
    input_tokens: int       # from response.usage.input_tokens
    output_tokens: int      # from response.usage.output_tokens
    cache_read_tokens: int  # from response.usage.cache_read_input_tokens (if present)
    latency_ms: float       # wall clock time
    season_id: str
    round_number: int | None
    timestamp: datetime
```

**Storage:** New DB table `ai_usage_log` (append-only). Schema:

```sql
CREATE TABLE ai_usage_log (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    call_type TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cache_read_tokens INTEGER DEFAULT 0,
    latency_ms REAL NOT NULL,
    season_id TEXT NOT NULL,
    round_number INTEGER,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX idx_ai_usage_season ON ai_usage_log(season_id);
CREATE INDEX idx_ai_usage_type ON ai_usage_log(call_type);
CREATE INDEX idx_ai_usage_created ON ai_usage_log(created_at);
```

**Integration approach:** Modify `_call_claude()` in `report.py` and each direct `client.messages.create()` call to:
1. Record `time.monotonic()` before and after.
2. Extract `response.usage.input_tokens` and `response.usage.output_tokens`.
3. Extract `response.usage.cache_read_input_tokens` if present (for prompt caching).
4. Call `await record_ai_usage(...)` to store the record.

Alternatively, create a wrapper function `ai_call_with_tracking()` that all modules use instead of calling the Anthropic client directly. This centralizes tracking and avoids modifying each call site individually.

### 2. Cost Computation

Pricing constants (update when prices change):

```python
PRICING = {
    "claude-sonnet-4-5-20250929": {
        "input_per_mtok": 3.00,
        "output_per_mtok": 15.00,
        "cache_read_per_mtok": 0.30,
    },
    "claude-haiku-4-5-20251001": {
        "input_per_mtok": 0.80,
        "output_per_mtok": 4.00,
        "cache_read_per_mtok": 0.08,
    },
}
```

Cost per call = `(input_tokens * input_rate + output_tokens * output_rate + cache_read_tokens * cache_rate) / 1_000_000`

### 3. Dashboard Route

Follow the existing eval dashboard pattern (`src/pinwheel/api/eval_dashboard.py`):

**New file: `src/pinwheel/api/cost_dashboard.py`**
**New template: `templates/pages/cost_dashboard.html`**
**Route: `GET /admin/costs`**

Dashboard sections:

1. **Summary Cards**
   - Total spend today / this week / all-time
   - Average cost per round
   - Total API calls today / this week
   - Cache hit rate (cache_read_tokens / total_input_tokens)

2. **Spend by Call Type** (bar chart or table)
   - commentary, highlight_reel, report.simulation, report.governance, report.private, interpreter, classifier, rule_evaluator
   - Each shows: call count, total input tokens, total output tokens, estimated cost

3. **Cost per Round** (trend line)
   - X-axis: round number
   - Y-axis: total cost
   - Helps identify cost spikes from governance-heavy rounds (more private reports)

4. **Cost per Governor** (estimated)
   - Private reports scale linearly with governor count
   - Show: fixed cost per round vs. variable cost per governor

5. **Latency Distribution**
   - Average latency by call type
   - Helps identify slow calls that could benefit from prompt caching

### 4. Auth Gating

Same pattern as eval dashboard: redirects to login if OAuth is enabled, accessible in dev mode without credentials. Admin-only in production.

## Implementation Steps

1. [ ] Create `AICallRecord` dataclass and `ai_usage_log` DB model in `db/models.py`
2. [ ] Add `record_ai_usage()` and `query_ai_usage()` to `db/repository.py`
3. [ ] Create `src/pinwheel/ai/usage.py` with the tracked wrapper function
4. [ ] Modify `report.py::_call_claude()` to use tracked wrapper
5. [ ] Modify `commentary.py::generate_game_commentary()` and `generate_highlight_reel()` to use tracked wrapper
6. [ ] Modify `interpreter.py::interpret_proposal()`, `interpret_strategy()`, `interpret_proposal_v2()` to use tracked wrapper
7. [ ] Modify `classifier.py::classify_injection()` to use tracked wrapper
8. [ ] Create `src/pinwheel/api/cost_dashboard.py` route handler
9. [ ] Create `templates/pages/cost_dashboard.html` template
10. [ ] Add "Costs" link to admin nav in `templates/base.html`
11. [ ] Write tests for usage recording and cost computation
12. [ ] Add demo step to `scripts/run_demo.sh` with Rodney screenshot

## Testing

- Unit tests for cost computation (known token counts -> expected cost)
- Integration test: mock AI call -> verify usage record stored
- Dashboard route test: verify template renders with mock data
- Cache hit rate computation test

## Non-Goals

- Real-time cost alerts (future feature)
- Budget limits / circuit breakers (future feature)
- Per-governor billing (not applicable -- this is internal ops visibility)

## Open Questions

- Should usage records be stored in the governance event store (append_event) or a dedicated table? Dedicated table is cleaner since these are operational metrics, not governance state.
- Should the dashboard include a "projected monthly cost" based on current pace? Useful but could be misleading with variable governor counts.
