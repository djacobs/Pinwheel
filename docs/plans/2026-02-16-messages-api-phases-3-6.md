# Messages API Phases 3-6 — Design Review and Open Questions

**Date:** 2026-02-16
**Status:** Deferred — needs design work before implementation
**Parent plan:** `docs/plans/2026-02-16-messages-api-improvements.md`
**Depends on:** Phase 1 (Prompt Caching) and Phase 2 (Structured Output) from the parent plan

## Context

The parent plan defines six phases of Messages API improvements. Phases 1 and 2 (Prompt Caching and Structured Output) are ready to implement — small diffs, clear wins, no architectural questions. Phases 3-6 each have design issues identified during review that need resolution before implementation can begin.

This document captures the review feedback, open questions, and prerequisite work for each deferred phase. It also introduces a "Phase 0" cross-cutting refactor that should precede all of phases 3-6.

---

## Phase 0: Client Consolidation (Prerequisite for Phases 3-6)

**Status: Ready to implement. Should be done before any phase below.**

### Problem

All ~11 AI call sites create a new `AsyncAnthropic` instance per call:

```python
client = anthropic.AsyncAnthropic(api_key=api_key)
```

This pattern appears in `interpreter.py`, `report.py`, `commentary.py`, `classifier.py`, `search.py`, `insights.py`, and `rule_evaluator.py`. Each subsequent phase (streaming, batch, thinking, tool use) would need to modify every call site individually. A shared client reduces the diff for every future phase.

### Implementation

Add a `get_client()` singleton to `src/pinwheel/ai/usage.py` (or a new `src/pinwheel/ai/client.py`):

```python
_client: AsyncAnthropic | None = None

def get_client(api_key: str | None = None) -> AsyncAnthropic:
    """Return a shared AsyncAnthropic client instance.

    Creates the singleton on first call. Enables connection reuse
    and provides a single place to configure client defaults.
    """
    global _client
    if _client is None:
        _client = AsyncAnthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY", ""),
            timeout=60.0,
            max_retries=2,
        )
    return _client
```

### Benefits

- Reduces the diff for every subsequent phase — new API features are configured in one place.
- Enables HTTP connection reuse across calls within the same process.
- Provides a single location for timeout, retry, and default configuration.
- Makes it trivial to swap in a test/mock client for the entire AI layer.

### Files to modify

| File | Change |
|------|--------|
| `src/pinwheel/ai/usage.py` (or new `client.py`) | Add `get_client()` singleton |
| `src/pinwheel/ai/interpreter.py` | Replace 3 `AsyncAnthropic()` calls with `get_client()` |
| `src/pinwheel/ai/report.py` | Replace `AsyncAnthropic()` in `_call_claude()` with `get_client()` |
| `src/pinwheel/ai/commentary.py` | Replace 2 `AsyncAnthropic()` calls with `get_client()` |
| `src/pinwheel/ai/classifier.py` | Replace 1 `AsyncAnthropic()` call with `get_client()` |
| `src/pinwheel/ai/search.py` | Replace 2 `AsyncAnthropic()` calls with `get_client()` |
| `src/pinwheel/ai/insights.py` | Replace `AsyncAnthropic()` calls with `get_client()` |
| `src/pinwheel/evals/rule_evaluator.py` | Replace `AsyncAnthropic()` call with `get_client()` |

### Tests

- `test_get_client_returns_singleton` — two calls return the same instance.
- `test_get_client_configures_defaults` — verify timeout and retry settings.
- All existing AI tests continue to pass (mocks still intercept the client).

### Verification

- `uv run pytest -x -q` passes.
- `uv run ruff check src/ tests/` clean.
- `demo_seed.py step 1` runs the full game loop without errors.

---

## Phase 3: Streaming Commentary

**Status: Needs design work. Three issues must be resolved.**

### Review from parent plan

The parent plan proposes streaming Claude API responses for game commentary, piping tokens through the event bus to SSE and Discord for a live broadcast effect. The core idea is sound — the SSE infrastructure exists, and the event bus already handles arbitrary event types. However, three issues need resolution.

### Issue 1: Context manager bug in code sample

The parent plan's code sample calls `get_final_message()` outside the `async with` block:

```python
async with client.messages.stream(...) as stream:
    async for text in stream.text_stream:
        full_text += text
        yield text

# BUG: stream is closed here — get_final_message() will fail
message = await stream.get_final_message()
```

`get_final_message()` must be called inside the `async with` block, before the context manager closes the stream. The corrected pattern:

```python
async with client.messages.stream(...) as stream:
    async for text in stream.text_stream:
        full_text += text
        yield text
    # Must be inside the async with block
    message = stream.get_final_message()
```

This also creates a tension with the generator pattern — the caller controls when the generator is exhausted, but usage recording must happen inside the context manager. The implementation needs to either: (a) record usage as a side effect inside the generator before it returns, or (b) use a callback pattern where the caller signals completion.

### Issue 2: Architecture conflict with `_phase_ai()`

`_phase_ai()` in `game_loop.py` is designed as a pure function with no side effects — no database access, no event bus interaction. It receives data, calls the AI layer, and returns results. The game loop's orchestrator handles persistence and event publishing.

Streaming commentary breaks this design because token-by-token delivery requires publishing `commentary.token` events during generation, which means the AI phase needs event bus access.

**Options (decision required):**

- **(a) Move streaming outside `_phase_ai()`:** Commentary streaming becomes a separate step in the game loop orchestrator, after `_phase_ai()` returns the non-streamed results. `_phase_ai()` stays pure. Streaming commentary runs as a post-AI-phase hook. This preserves the architecture but means commentary is generated twice (once for storage in `_phase_ai`, once for streaming) unless we skip the batch generation when streaming is enabled.

- **(b) Pass event bus into `_phase_ai()` explicitly:** Add an optional `event_bus` parameter. When present and streaming is enabled, `_phase_ai()` publishes token events during commentary generation. This is pragmatic but erodes the pure-function boundary.

- **(c) Return an async generator from `_phase_ai()`:** Instead of returning the final commentary text, return a generator that the orchestrator can consume and publish from. `_phase_ai()` creates the generator but doesn't drive it — the caller does. This preserves purity (the phase doesn't publish) while enabling streaming.

**Recommendation:** Option (c) is the cleanest — it keeps `_phase_ai()` as a data-returning function while enabling the caller to stream. The orchestrator would do:

```python
commentary_stream = ai_results.commentary_stream
if commentary_stream is not None:
    full_text = ""
    async for token in commentary_stream:
        await event_bus.publish({"type": "commentary.token", ...})
        full_text += token
    ai_results.commentary = full_text
```

### Issue 3: Discord rate limits

The parent plan suggests editing Discord messages every ~500ms to create a typing effect. This produces ~10 edits per 5 seconds. Discord's rate limit for `edit_original_response()` is approximately 5 requests per 5 seconds.

**Fix:** Throttle to 1 edit per second maximum. Buffer tokens and flush on a 1-second interval rather than on every token arrival. This stays safely within rate limits while still producing a visible streaming effect.

```python
# Pseudocode for Discord chunked delivery
buffer = ""
last_edit = time.monotonic()
async for token in commentary_stream:
    buffer += token
    if time.monotonic() - last_edit >= 1.0:
        await interaction.edit_original_response(content=accumulated_text)
        last_edit = time.monotonic()
# Final edit with complete text
await interaction.edit_original_response(content=accumulated_text)
```

### Prerequisites

- [ ] Phase 0 (Client Consolidation) — shared client simplifies streaming client creation.
- [ ] Architectural decision on Issue 2 — which option (a/b/c) to pursue.

### Files to modify (unchanged from parent plan, plus adjustments)

| File | Change |
|------|--------|
| `src/pinwheel/ai/commentary.py` | Add `stream_game_commentary()` with corrected context manager usage |
| `src/pinwheel/core/game_loop.py` | Wire streaming path per the chosen architecture option |
| `src/pinwheel/config.py` | Add `pinwheel_stream_commentary: bool` setting |
| `templates/pages/game_detail.html` | Streaming commentary container + JS listener |
| `src/pinwheel/discord/bot.py` | Chunked edit with 1-second throttle |

---

## Phase 4: Batch API for Background Work

**Status: Needs design work. Data flow gap and scale justification required.**

### Review from parent plan

The parent plan proposes using the Message Batches API for non-urgent calls (season memorials, rule evaluator, A/B evals, golden dataset) at 50% cost reduction. The infrastructure involves a new DB model, polling scheduler, and result processing pipeline.

### Issue 1: Data flow gap — season memorials

`generate_season_memorial()` feeds directly into `SeasonArchiveRow` creation in the game loop. The current flow:

```
generate_season_memorial() -> memorial text -> create SeasonArchiveRow(memorial=text)
```

If memorials are batched, the archive row would be created before the memorial text is available. The parent plan doesn't address this gap.

**Options (decision required):**

- **(a) Defer archive creation until batch completes.** The archive row is created by the batch result handler when the memorial comes back. This changes the season lifecycle — the archive isn't immediately available after the season ends. Downstream code that expects `SeasonArchiveRow` to exist right after `end_season()` would break.

- **(b) Create archive without memorial, backfill later.** Create `SeasonArchiveRow` with `memorial=None` at season end. When the batch completes, update the row with the memorial text. This requires making `memorial` nullable in the schema and adding a backfill path in the batch result handler. Simplest option but means the archive page may show an incomplete state for up to 24 hours.

- **(c) Only batch eval calls, not memorials.** Keep memorials synchronous (they happen once per season — cost savings are negligible). Batch only the eval calls (rule evaluator, A/B comparison, golden dataset). This sidesteps the data flow issue entirely at the cost of smaller savings.

**Recommendation:** Option (c). Season memorials are 4 calls per season — batching saves pennies. The infrastructure cost of handling deferred memorials exceeds the savings. Focus batching on eval calls, which are more frequent and truly non-urgent.

### Issue 2: Scale justification

Current call volumes for batch-eligible work:

| Call type | Frequency | Calls per week (est.) |
|-----------|-----------|----------------------|
| Season memorial | Once per season | ~4 |
| Rule evaluator (M.7) | Per round (when enabled) | ~20-40 |
| A/B comparison (M.2) | Post-hoc | ~10-20 |
| Golden dataset (M.1) | CI/on-demand | ~5-10 |

Total: ~40-75 calls per week. At ~$0.01-0.05 per call, the 50% batch savings is $0.20-$1.88/week. The infrastructure cost (new DB model `BatchJobRow`, polling scheduler, result processing, error handling, monitoring) is significant relative to these savings.

**Recommendation:** Defer until call volume grows. Revisit when either (a) the league has multiple concurrent seasons generating more eval calls, or (b) a new high-volume background AI workload is added. Document the batch API pattern so it's ready when needed, but don't build the infrastructure yet.

### Issue 3: Missing retry and error handling

The parent plan's polling loop has no retry logic:

```python
result = await poll_batch(job.batch_id, api_key)
if result.status == "complete":
    await _process_batch_results(job, result, session)
```

If `poll_batch` throws an exception (network failure, API error), the job stays in "pending" forever with no alert. If `_process_batch_results` partially fails, results could be lost.

**Required additions if this phase is built:**

- Retry with exponential backoff on poll failures (max 3 retries).
- Dead-letter state: after N consecutive poll failures, mark the job as "failed" and alert via Discord notification to the admin.
- Idempotent result processing: `_process_batch_results` should be safe to re-run.
- `completed_at` and `error_message` columns on `BatchJobRow` for observability.

### Prerequisites

- [ ] Phase 0 (Client Consolidation).
- [ ] Decision on Issue 1 — which option for memorial data flow (recommend option c).
- [ ] Scale justification — defer unless call volume warrants it.

---

## Phase 5: Extended Thinking for Governance Analysis

**Status: Needs cost controls and integration design.**

### Review from parent plan

The parent plan proposes enabling extended thinking on the rule evaluator, impact prediction, and behavioral profile calls. The thinking budget would produce deeper chain-of-thought reasoning for analytical tasks.

### Issue 1: Cost risk — no budget ceiling

Extended thinking produces ~3x output tokens on enabled calls. With no budget ceiling, a bad round (many proposals, complex rule state) could produce unexpectedly large costs.

**Required:** Add a per-round cost ceiling:

```python
# In config.py
pinwheel_thinking_max_cost_per_round: float = 0.50  # USD ceiling for thinking-enabled calls
```

Implementation: track cumulative thinking-call costs within each round. If the next call would exceed the ceiling, fall back to a standard (non-thinking) call. Log the fallback for observability.

```python
# Pseudocode in the thinking-enabled call path
estimated_cost = estimate_thinking_call_cost(input_tokens, thinking_budget)
if round_cost_tracker.total + estimated_cost > settings.pinwheel_thinking_max_cost_per_round:
    logger.warning("Thinking budget exceeded for round %d, falling back to standard call", round_number)
    return await _call_standard(...)
round_cost_tracker.total += actual_cost
return await _call_with_thinking(...)
```

### Issue 2: Integration gap with `insights.py`

`insights.py` uses `_call_claude()` from `report.py` for its AI calls. This shared helper currently accepts `system`, `user_msg`, `api_key`, `max_tokens`, and usage-tracking parameters. It does not accept a `thinking` parameter.

**Options:**

- **(a) Thread `thinking` through `_call_claude()`:** Add an optional `thinking: dict | None = None` parameter to `_call_claude()`. When present, pass it through to `messages.create()`. This keeps the single-call-site pattern but grows the parameter list further.

- **(b) Bypass `_call_claude()` in insights.py:** Have thinking-enabled calls in `insights.py` call `get_client().messages.create()` directly, with their own usage tracking. This avoids modifying the shared helper but duplicates the call pattern.

- **(c) Create a new `_call_claude_with_thinking()` helper:** Separate function that wraps the thinking-specific API parameters. Both `report.py` and `insights.py` can use it for thinking-enabled calls while `_call_claude()` stays unchanged.

**Recommendation:** Option (a) is simplest. `_call_claude()` already has many optional parameters; one more (`thinking`) is consistent with the pattern. The alternative helpers add indirection without clear benefit.

### Issue 3: Which call sites actually benefit?

Extended thinking adds cost and latency. Not all analytical calls benefit equally.

| Call site | Benefits from thinking? | Justification |
|-----------|------------------------|---------------|
| Rule evaluator (`evals/rule_evaluator.py`) | **Yes** | Multi-step equilibrium analysis, counterfactual reasoning about rule interactions |
| Impact prediction (`ai/insights.py`) | **Yes** | "What happens if this rule passes?" requires reasoning through game mechanics |
| Behavioral profile (`ai/insights.py`) | **Maybe** | Pattern detection across voting history — benefits from reasoning but could also be handled with richer context |
| Commentary (`ai/commentary.py`) | **No** | Generative prose, speed matters, quality is already good |
| Reports (`ai/report.py`) | **No** | Narrative generation, not analytical reasoning |
| Interpreter (`ai/interpreter.py`) | **No** | Structured output extraction, not deep reasoning |

**Recommendation:** Start with rule evaluator and impact prediction only. Measure quality improvement (use the existing eval framework — M.1 golden dataset, M.2 A/B comparison) before expanding to behavioral profiles. Don't enable for generative call sites.

### Prerequisites

- [ ] Phase 0 (Client Consolidation).
- [ ] Cost ceiling config parameter implemented and tested.
- [ ] Decision on `_call_claude()` integration approach (recommend option a).
- [ ] Baseline eval scores for rule evaluator and impact prediction (to measure thinking improvement).

---

## Phase 6: Tool Use for Live Data in Reports

**Status: Needs security design. Highest risk phase.**

### Review from parent plan

The parent plan proposes giving report generation calls access to database tools, allowing the model to query live data mid-generation. The model could ask "what was Team X's record before this rule changed?" and get a real answer rather than relying on pre-computed context.

### Issue 1: Input validation gap — `**input_data` is dangerous

The parent plan's `execute_tool()` function passes AI-generated arguments directly to repository methods:

```python
case "get_team_record":
    return await repo.get_team_record(session, **input_data)
```

If the model hallucinates an argument name (e.g., `{"team_id": "abc", "drop_table": true}`), the `**` unpacking passes it directly to the repo method. While Python would raise a `TypeError` for unexpected keyword arguments in most cases, this is a fragile safety boundary.

**Required:** Pydantic input schemas for every tool. Validate before dispatch:

```python
from pydantic import BaseModel

class GetTeamRecordInput(BaseModel):
    team_id: str
    season_id: str
    from_round: int | None = None
    to_round: int | None = None

async def execute_tool(name: str, input_data: dict, repo: Repository, session: AsyncSession) -> dict:
    match name:
        case "get_team_record":
            validated = GetTeamRecordInput(**input_data)
            return await repo.get_team_record(
                session,
                team_id=validated.team_id,
                season_id=validated.season_id,
                from_round=validated.from_round,
                to_round=validated.to_round,
            )
        # ... etc
```

This ensures only expected fields with correct types reach the repository layer.

### Issue 2: Latency risk — multi-turn report generation

Each tool use round is a full API call. The parent plan allows up to 3 tool rounds, meaning a single report could require 4 API calls (1 initial + 3 tool rounds). At ~2-3 seconds per call, report generation could take 8-12 seconds instead of 2-3 seconds.

**Required additions:**

- **Latency budget:** Add a `max_tool_time_seconds: float = 10.0` parameter. If cumulative tool-round time exceeds the budget, stop issuing tool calls and use whatever text the model has generated so far.
- **Circuit breaker:** If tool-use reports consistently exceed the latency budget (e.g., 3 out of the last 5 reports), automatically fall back to the non-tool-use path and log a warning.
- **Monitoring:** Track tool-use reports separately in the cost dashboard — call count, latency, and tools invoked per report.

### Issue 3: Repository method gap

The parent plan lists 4 new repo methods:

| Method | Overlap with existing code |
|--------|---------------------------|
| `get_team_record()` | Partially covered by `get_standings()` and `get_team_games()` |
| `get_scoring_averages()` | Some overlap with box score aggregation in `get_game_results()` |
| `get_governor_voting_record()` | Partially covered by existing voting queries in `get_governor_profile()` |
| `get_rule_change_history()` | Exists conceptually in `get_governance_events()` but not in the right return format |

Before implementing new methods, audit existing repository functions to determine whether they can be adapted (with return-type wrappers) rather than duplicated. New methods should return simple dicts (as the parent plan specifies) for clean JSON serialization in tool results.

### Issue 4: Scope question — is tool use the right pattern?

The Amplify Human Judgment features (private reports, leverage detection, behavioral profiles) already pre-compute rich context and pass it to the AI in the prompt. The report quality is high because the context is comprehensive and curated.

Tool use adds value when the AI needs data that the caller can't anticipate. But in Pinwheel, the data domain is well-understood — the same categories of information (standings, scoring stats, voting records, rule changes) are relevant to every report. Pre-computing all of it and passing it in context is arguably simpler and more predictable than letting the model query on-demand.

**When tool use becomes worth it:**

- When the data domain expands beyond what can be reasonably pre-computed (e.g., cross-season historical analysis, multi-hop queries like "governors who voted for rules that reduced scoring by teams that later made the playoffs").
- When reports need to be customizable (e.g., a governor asks "tell me about my team's defense" and the model needs to fetch defense-specific stats).
- When context windows become a constraint and selective data fetching reduces token costs compared to passing everything.

**Recommendation:** Defer tool use until one of the above conditions is met. The pre-computed context pattern is working well and is simpler to test, debug, and reason about. Document the tool use architecture so it's ready when the need arises.

### Prerequisites

- [ ] Phase 0 (Client Consolidation).
- [ ] Pydantic input validation schemas for all tools.
- [ ] Latency budget and circuit breaker design.
- [ ] Repository method audit — identify reuse vs. new methods.
- [ ] Clear use case where pre-computed context is insufficient.

---

## Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Premature optimization — building infrastructure for call volumes that don't justify it | Medium | Phase 4 (Batch) and Phase 6 (Tool Use) are explicitly deferred until scale or need warrants them |
| Architecture erosion — streaming breaks `_phase_ai()` purity | Medium | Phase 3 requires an architectural decision before implementation; option (c) preserves the boundary |
| Cost overrun from extended thinking | High | Phase 5 requires a per-round cost ceiling before any thinking-enabled calls go live |
| Security gap from tool use `**kwargs` pattern | High | Phase 6 requires Pydantic input validation before any tool dispatch code is written |
| Discord rate limiting on streaming edits | Low | Phase 3 specifies 1-second throttle, well within limits |

## Implementation Order

```
Phase 0: Client Consolidation        <-- Do first, prerequisite for all below
    |
    +-- Phase 3: Streaming Commentary <-- After architecture decision on _phase_ai()
    |
    +-- Phase 5: Extended Thinking    <-- After cost ceiling design
    |
    +-- Phase 4: Batch API            <-- Deferred until scale warrants
    |
    +-- Phase 6: Tool Use             <-- Deferred until pre-computed context is insufficient
```

Phase 0 is the only immediate action item. Phases 3 and 5 need design decisions documented in this file before implementation. Phases 4 and 6 are deferred — revisit when conditions change.

## Verification Plan

When any of these phases moves to implementation:

1. `uv run pytest -x -q` — all tests pass.
2. `uv run ruff check src/ tests/` — clean lint.
3. `uv run python scripts/demo_seed.py seed && uv run python scripts/demo_seed.py step 3` — full game loop runs.
4. Check `/admin/costs` — usage dashboard reflects new API patterns.
5. For Phase 3: verify SSE streaming works on the game detail page.
6. For Phase 5: verify thinking-enabled calls produce measurably better output (eval scores).
7. For Phase 4: verify batch jobs complete and results are processed correctly.
8. For Phase 6: verify tool-use reports are grounded in real data and don't exceed latency budget.
