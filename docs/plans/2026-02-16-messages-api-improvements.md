# Plan: Messages API Improvements

**Date:** 2026-02-16
**Status:** Proposed
**Depends on:** Existing AI layer (`ai/usage.py`, `ai/report.py`, `ai/interpreter.py`, `ai/commentary.py`, `ai/classifier.py`), SSE infrastructure (`api/events.py`), evals framework (`evals/`)

## Problem

Pinwheel makes ~11 distinct Claude API call types across interpretation, reporting, commentary, classification, and search. All use the same narrow slice of the Messages API: `client.messages.create()` with `system`, `messages`, `max_tokens`. The codebase already tracks `cache_read_input_tokens` in `usage.py` but never triggers caching. JSON responses are parsed manually with markdown fence stripping. Commentary is batch-delivered rather than streamed. Background evals pay full price for non-urgent work.

Six Messages API features would materially improve quality, cost, or UX — ordered by effort and impact.

## Current State: All AI Call Sites

Every call follows the same pattern (from `interpreter.py`, `report.py`, `commentary.py`, `classifier.py`):

```python
client = anthropic.AsyncAnthropic(api_key=api_key)
async with track_latency() as timing:
    response = await client.messages.create(
        model=model,
        max_tokens=N,
        system=system_prompt_string,
        messages=[{"role": "user", "content": user_msg}],
    )
text = response.content[0].text.strip()
# Manual JSON parsing with fence stripping
if text.startswith("```"):
    text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
data = json.loads(text)
```

Usage tracking (`extract_usage`) already reads `cache_read_input_tokens` — the plumbing exists, the features don't.

---

## Phase 1: Prompt Caching

**Impact:** 90% reduction on input token cost for cached prompts. Reduced latency on repeat calls.
**Effort:** Small — change how `system` is passed, no logic changes.
**Risk:** None — additive, backward compatible, fail-safe (uncached calls still work).

### Why

The v2 interpreter system prompt (`INTERPRETER_V2_SYSTEM_PROMPT`) is ~500 lines / ~3,000 tokens and is sent identically on every `/propose` call. Report prompts (`SIMULATION_REPORT_PROMPT`, `GOVERNANCE_REPORT_PROMPT`, `PRIVATE_REPORT_PROMPT`) are 400-600 lines each, sent identically every round. Commentary prompts are shorter (~50-85 lines) but repeated per-game.

Anthropic's prompt caching caches content blocks marked with `cache_control: {"type": "ephemeral"}`. Cached content costs 90% less on subsequent reads within a 5-minute TTL. The minimum cacheable prefix is 1,024 tokens for Sonnet — all interpreter and report system prompts qualify.

### Implementation

The Messages API accepts `system` as either a string or a list of content blocks. To enable caching, switch from string to block format with `cache_control`:

**Before (current pattern in all call sites):**
```python
response = await client.messages.create(
    model=model,
    max_tokens=1500,
    system=system_prompt_string,
    messages=[{"role": "user", "content": user_msg}],
)
```

**After:**
```python
response = await client.messages.create(
    model=model,
    max_tokens=1500,
    system=[
        {
            "type": "text",
            "text": system_prompt_string,
            "cache_control": {"type": "ephemeral"},
        }
    ],
    messages=[{"role": "user", "content": user_msg}],
)
```

#### Files to modify

| File | Call site(s) | System prompt size |
|------|-------------|-------------------|
| `src/pinwheel/ai/interpreter.py` | `interpret_proposal()`, `interpret_strategy()`, `interpret_proposal_v2()` | 55 / 248 / 493 lines |
| `src/pinwheel/ai/report.py` | `_call_claude()` (used by all report generators) | 60-130 lines each |
| `src/pinwheel/ai/commentary.py` | `generate_game_commentary()`, `generate_highlight_reel()` | 49 / 84 lines |
| `src/pinwheel/ai/classifier.py` | `classify_injection()` | 48 lines |
| `src/pinwheel/ai/search.py` | `parse_search_query()`, `format_search_result()` | ~150 / ~200 lines |
| `src/pinwheel/ai/insights.py` | `generate_newspaper_headlines()` + future insight generators | ~100 lines |

For each: change the `system=` parameter from a string to the block-list format. No other changes needed — `extract_usage()` in `usage.py` already reads `cache_read_input_tokens` from the response, and `record_ai_usage()` already stores it. The cost dashboard already computes cache savings.

#### Helper function (optional, reduces repetition)

Add to `src/pinwheel/ai/usage.py`:

```python
def cacheable_system(text: str) -> list[dict[str, object]]:
    """Wrap a system prompt string as a cacheable content block."""
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]
```

Then each call site becomes `system=cacheable_system(prompt)`.

#### Verification

- `cache_read_input_tokens` in `AIUsageLogRow` should be >0 after the second call with the same system prompt within 5 minutes.
- Cost dashboard (`/admin/costs`) should show cache hit rate increasing.
- All existing tests pass unchanged (mocks don't hit the API).

#### Tests

- `test_cacheable_system_format` — verify output structure matches API spec.
- `test_extract_usage_with_cache_tokens` — verify `cache_read_input_tokens` is captured (already partially tested).
- Integration test (marked `@pytest.mark.integration`): two back-to-back interpreter calls → second should have nonzero `cache_read_input_tokens`.

---

## Phase 2: Structured Output (`response_format`)

**Impact:** Eliminates JSON parsing failures, removes markdown fence stripping, enables schema validation at the API level.
**Effort:** Medium — define JSON schemas, update call sites, remove manual parsing.
**Risk:** Low — the API guarantees conformance to the schema; fallback to current parsing if needed.

### Why

Six call sites parse JSON from free text with manual fence stripping:

```python
text = response.content[0].text.strip()
if text.startswith("```"):
    text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
data = json.loads(text)
```

This works, but it's fragile — any unexpected prefix breaks parsing, and the model can return fields the code doesn't expect. The `response_format` parameter with `type: "json_schema"` guarantees the response is valid JSON matching a defined schema.

### Implementation

For each structured-output call site, define a JSON schema and pass it as `response_format`:

```python
response = await client.messages.create(
    model=model,
    max_tokens=500,
    system=cacheable_system(system_prompt),
    messages=[{"role": "user", "content": user_msg}],
    response_format={
        "type": "json_schema",
        "json_schema": {
            "name": "rule_interpretation",
            "strict": True,
            "schema": RULE_INTERPRETATION_SCHEMA,
        },
    },
)
# No fence stripping needed — response is guaranteed valid JSON
data = json.loads(response.content[0].text)
```

#### Schemas to define

| Call site | Schema name | Source Pydantic model |
|-----------|------------|----------------------|
| `interpret_proposal()` | `rule_interpretation` | `RuleInterpretation` in `models/governance.py` |
| `interpret_strategy()` | `team_strategy` | `TeamStrategy` in `models/team.py` |
| `interpret_proposal_v2()` | `proposal_interpretation` | `ProposalInterpretation` in `models/governance.py` |
| `classify_injection()` | `classification_result` | `ClassificationResult` (inline in `classifier.py`) |
| `parse_search_query()` | `query_plan` | `QueryPlan` (inline in `search.py`) |

Use Pydantic's `.model_json_schema()` to generate schemas directly from existing models — no manual schema authoring. Add a helper:

```python
# In src/pinwheel/ai/usage.py or a new src/pinwheel/ai/schemas.py
def pydantic_to_response_format(
    model_class: type[BaseModel], name: str
) -> dict[str, object]:
    """Convert a Pydantic model to a Messages API response_format dict."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": model_class.model_json_schema(),
        },
    }
```

#### What stays as plain text

Report generation (`generate_simulation_report`, `generate_governance_report`, `generate_private_report`) and commentary (`generate_game_commentary`, `generate_highlight_reel`) return prose, not JSON. These do NOT use `response_format` — they stay as-is.

#### Files to modify

| File | Change |
|------|--------|
| `src/pinwheel/ai/interpreter.py` | Add `response_format` to 3 call sites, remove fence stripping |
| `src/pinwheel/ai/classifier.py` | Add `response_format` to 1 call site, remove fence stripping |
| `src/pinwheel/ai/search.py` | Add `response_format` to 1 call site, remove fence stripping |
| `src/pinwheel/ai/usage.py` (or new `schemas.py`) | Add `pydantic_to_response_format()` helper |

#### Tests

- `test_pydantic_to_response_format` — verify schema generation for each Pydantic model.
- Update existing interpreter/classifier tests to verify no fence stripping is needed.
- `test_structured_output_fallback` — if `response_format` is not supported (SDK version), fall back to current parsing.

---

## Phase 3: Streaming Commentary

**Impact:** Real-time "typing" effect for game commentary in web dashboard and Discord. Commentary feels like a live broadcast instead of a batch dump.
**Effort:** Medium — streaming API, SSE integration, Discord chunked editing.
**Risk:** Low — streaming is additive; non-streaming fallback preserved.

### Why

Game commentary (`commentary.py`) generates play-by-play text that players watch on the web dashboard and Discord. Currently the full response is awaited before display — the entire commentary appears at once. Streaming via `messages.create(stream=True)` would pipe tokens to the SSE endpoint (`api/events.py`) as they arrive, creating a live broadcaster effect.

The SSE infrastructure already exists and supports arbitrary event types. The event bus (`core/event_bus.py`) already publishes `commentary.generated` events. This phase adds incremental token delivery.

### Implementation

#### A. Streaming generator in `commentary.py`

Add a streaming variant alongside the existing batch function:

```python
async def stream_game_commentary(
    game: GameResult,
    teams: list[Team],
    ruleset: RuleSet,
    api_key: str,
    narrative: NarrativeContext | None = None,
    *,
    season_id: str = "",
    round_number: int | None = None,
    db_session: object | None = None,
) -> AsyncGenerator[str, None]:
    """Stream commentary tokens as they arrive from Claude."""
    system = _build_commentary_system(game, teams, ruleset, narrative)
    user_msg = _build_commentary_user_msg(game, teams, ruleset, narrative)

    client = anthropic.AsyncAnthropic(api_key=api_key)
    full_text = ""

    async with track_latency() as timing:
        async with client.messages.stream(
            model="claude-sonnet-4-5-20250929",
            max_tokens=400,
            system=cacheable_system(system),
            messages=[{"role": "user", "content": user_msg}],
        ) as stream:
            async for text in stream.text_stream:
                full_text += text
                yield text

    # Record usage after stream completes
    message = await stream.get_final_message()
    input_tok, output_tok, cache_tok = extract_usage(message)
    if db_session is not None:
        await record_ai_usage(
            session=db_session,
            call_type="commentary.game.stream",
            model="claude-sonnet-4-5-20250929",
            input_tokens=input_tok,
            output_tokens=output_tok,
            cache_read_tokens=cache_tok,
            latency_ms=timing["latency_ms"],
            season_id=season_id,
            round_number=round_number,
        )
```

#### B. Event bus streaming

In the game loop, when streaming is enabled, publish incremental `commentary.token` events:

```python
async for token in stream_game_commentary(...):
    await event_bus.publish({
        "type": "commentary.token",
        "game_id": game.id,
        "token": token,
    })
# Publish final event when complete
await event_bus.publish({
    "type": "commentary.complete",
    "game_id": game.id,
    "text": full_text,
})
```

#### C. SSE delivery

No changes to `api/events.py` needed — the SSE endpoint already streams any event type. Clients subscribe to `commentary.token` events and append tokens to the DOM.

#### D. Frontend (HTMX)

Add a `<div id="commentary-stream">` in the game detail template. JavaScript (minimal, no build step) listens to the SSE stream:

```javascript
const source = new EventSource('/api/events/stream?event_type=commentary.token');
source.addEventListener('commentary.token', (e) => {
    const data = JSON.parse(e.data);
    document.getElementById(`commentary-${data.game_id}`).textContent += data.token;
});
```

#### E. Discord chunked delivery

For Discord, use `interaction.edit_original_response()` in a loop, appending tokens every ~500ms to avoid rate limits. This creates a "typing" effect in the Discord embed.

#### Configuration

Add `PINWHEEL_STREAM_COMMENTARY` to `config.py` (default: `true` in development, `true` in production). When disabled, falls back to the existing batch pattern.

#### Files to modify

| File | Change |
|------|--------|
| `src/pinwheel/ai/commentary.py` | Add `stream_game_commentary()`, refactor prompt building into shared helpers |
| `src/pinwheel/core/game_loop.py` | Wire streaming path in `_phase_ai()` when enabled |
| `src/pinwheel/config.py` | Add `pinwheel_stream_commentary: bool = True` |
| `templates/pages/game_detail.html` | Add streaming commentary container + JS listener |
| `src/pinwheel/discord/bot.py` | Chunked edit for streamed commentary (optional, Phase 3b) |

#### Tests

- `test_stream_game_commentary_yields_tokens` — mock streaming response, verify tokens yielded.
- `test_stream_usage_recorded_after_completion` — verify `record_ai_usage` called with correct totals.
- `test_stream_fallback_to_batch` — when streaming disabled, existing batch path used.
- `test_commentary_token_event_published` — verify event bus receives incremental tokens.

---

## Phase 4: Batch API for Background Work

**Impact:** 50% cost reduction on non-urgent AI calls (evals, season memorials, rule evaluator).
**Effort:** Medium — new batch submission/polling infrastructure, async result handling.
**Risk:** Low — batch jobs are already non-blocking background work; 24-hour SLA is acceptable.

### Why

Several AI call types don't need real-time responses:

| Call type | Current timing | Acceptable delay |
|-----------|---------------|-----------------|
| Season memorial (4 calls) | End of season, blocking | Hours — season is over |
| Rule evaluator (M.7) | Post-round, admin-facing | Hours — admin reviews next day |
| A/B comparison (M.2) | Post-hoc eval | 24 hours |
| Golden dataset (M.1) | CI/on-demand | 24 hours |

The Message Batches API processes these at 50% of standard pricing with a 24-hour SLA. For a game that advances rounds on cron schedules, this is a natural fit.

### Implementation

#### A. Batch client wrapper in `src/pinwheel/ai/batch.py` (new file)

```python
"""Batch API wrapper for non-urgent Claude calls.

Submits requests to the Message Batches API for 50% cost reduction.
Results are polled and stored when ready.
"""

async def submit_batch(
    requests: list[BatchRequest],
    api_key: str,
) -> str:
    """Submit a batch of message requests. Returns batch_id."""
    client = anthropic.AsyncAnthropic(api_key=api_key)
    batch = await client.messages.batches.create(requests=requests)
    return batch.id

async def poll_batch(batch_id: str, api_key: str) -> BatchResult:
    """Check batch status. Returns results if complete."""
    client = anthropic.AsyncAnthropic(api_key=api_key)
    batch = await client.messages.batches.retrieve(batch_id)
    if batch.processing_status == "ended":
        results = []
        async for result in client.messages.batches.results(batch_id):
            results.append(result)
        return BatchResult(status="complete", results=results)
    return BatchResult(status="processing", results=[])
```

#### B. Batch job scheduler

Add a periodic APScheduler job that polls pending batches:

```python
# In scheduler_runner.py or a new batch_runner.py
async def poll_pending_batches(session):
    """Check all pending batch jobs, store results when ready."""
    pending = await repo.get_pending_batch_jobs(session)
    for job in pending:
        result = await poll_batch(job.batch_id, api_key)
        if result.status == "complete":
            await _process_batch_results(job, result, session)
            await repo.mark_batch_complete(job.id, session)
```

#### C. Database model for batch tracking

Add `BatchJobRow` to `db/models.py`:

```python
class BatchJobRow(Base):
    __tablename__ = "batch_jobs"
    id: Mapped[str] = mapped_column(primary_key=True, default=new_id)
    batch_id: Mapped[str]           # Anthropic batch ID
    job_type: Mapped[str]           # "memorial", "eval.golden", "eval.ab", "rule_evaluator"
    status: Mapped[str]             # "pending", "complete", "failed"
    season_id: Mapped[str]
    round_number: Mapped[int | None]
    submitted_at: Mapped[str]
    completed_at: Mapped[str | None]
    result_data: Mapped[str | None]  # JSON blob of processed results
```

#### D. Wire batch submission for eligible call types

In `game_loop.py`, when batch mode is enabled for a call type, submit to batch instead of calling synchronously:

```python
# Season memorial — batch instead of 4 synchronous calls
if settings.pinwheel_batch_background_ai:
    batch_requests = build_memorial_batch_requests(season_data, api_key)
    batch_id = await submit_batch(batch_requests, api_key)
    await repo.create_batch_job(session, batch_id, "memorial", season_id)
else:
    # Existing synchronous path
    memorial = await generate_season_memorial(...)
```

#### Configuration

Add to `config.py`:
```python
pinwheel_batch_background_ai: bool = False  # Opt-in, default off
```

#### Files to modify/create

| File | Change |
|------|--------|
| `src/pinwheel/ai/batch.py` | **NEW** — batch submission, polling, result processing |
| `src/pinwheel/db/models.py` | Add `BatchJobRow` |
| `src/pinwheel/db/repository.py` | Add `create_batch_job()`, `get_pending_batch_jobs()`, `mark_batch_complete()` |
| `src/pinwheel/core/scheduler_runner.py` | Add `poll_pending_batches` periodic job |
| `src/pinwheel/core/game_loop.py` | Conditional batch submission for memorials, rule evaluator |
| `src/pinwheel/config.py` | Add `pinwheel_batch_background_ai` setting |

#### Tests

- `test_submit_batch_returns_id` — mock batch creation.
- `test_poll_batch_complete` — mock completed batch, verify results parsed.
- `test_poll_batch_pending` — mock in-progress batch, verify no results.
- `test_memorial_batch_submission` — verify batch requests built correctly from season data.
- `test_batch_results_stored` — verify results flow into report/eval storage.

---

## Phase 5: Extended Thinking for Governance Analysis

**Impact:** Deeper reasoning for complex governance analysis — equilibrium detection, impact prediction, behavioral profiling.
**Effort:** Medium — enable extended thinking on select calls, handle `thinking` content blocks, budget management.
**Risk:** Medium — higher cost (~3x), quality improvement must justify spend. Gate behind config.

### Why

The rule evaluator (`evals/rule_evaluator.py`) analyzes the current rule state for degenerate equilibria, stale parameters, and suggested experiments. Private reports detect swing votes and behavioral patterns. These tasks benefit from chain-of-thought reasoning that extended thinking provides — the model thinks through the analysis before committing to output.

The planned-but-not-built "Rule Impact Prediction" feature (predicting consequences of a proposal before it passes) would especially benefit — counterfactual reasoning about game mechanics is exactly the kind of task where thinking budget helps.

### Implementation

#### A. Extended thinking wrapper

Add to `src/pinwheel/ai/usage.py`:

```python
def with_thinking(
    max_thinking_tokens: int = 4000,
) -> dict[str, object]:
    """Return kwargs to enable extended thinking on a messages.create() call."""
    return {
        "thinking": {
            "type": "enabled",
            "budget_tokens": max_thinking_tokens,
        },
    }
```

#### B. Response parsing for thinking blocks

Extended thinking responses include `thinking` content blocks before the `text` block:

```python
def extract_text_from_thinking_response(response) -> str:
    """Extract the text content from a response that may include thinking blocks."""
    for block in response.content:
        if block.type == "text":
            return block.text
    return ""
```

#### C. Apply to specific call sites

Enable extended thinking selectively — only where reasoning depth matters:

| Call site | Thinking budget | Justification |
|-----------|----------------|--------------|
| Rule evaluator (`evals/rule_evaluator.py`) | 4,000 tokens | Equilibrium analysis requires multi-step reasoning |
| Impact prediction (new, `ai/insights.py`) | 4,000 tokens | Counterfactual "what if this rule passes" reasoning |
| Behavioral profile (`ai/insights.py`) | 2,000 tokens | Longitudinal pattern detection across many rounds |

Do NOT enable for: commentary (speed matters), basic reports (quality is adequate), interpretation (structured output is the goal, not reasoning).

#### D. Usage tracking

Extended thinking tokens are tracked separately in the API response:

```python
def extract_usage(response: object) -> tuple[int, int, int]:
    """Extract (input_tokens, output_tokens, cache_read_tokens) from API response."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return (0, 0, 0)
    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    # Note: thinking tokens are included in output_tokens by the API
    return (input_tokens, output_tokens, cache_read)
```

No change needed to `extract_usage` — thinking tokens are included in `output_tokens`. But update `PRICING` to note that extended thinking output tokens are billed at standard output rates.

#### Configuration

Add to `config.py`:
```python
pinwheel_extended_thinking: bool = False  # Opt-in, higher cost
pinwheel_thinking_budget: int = 4000      # Max thinking tokens per call
```

#### Files to modify

| File | Change |
|------|--------|
| `src/pinwheel/ai/usage.py` | Add `with_thinking()` helper, `extract_text_from_thinking_response()` |
| `src/pinwheel/evals/rule_evaluator.py` | Add `**with_thinking()` to `messages.create()` when enabled |
| `src/pinwheel/ai/insights.py` | Add thinking to impact prediction and behavioral profile |
| `src/pinwheel/config.py` | Add `pinwheel_extended_thinking`, `pinwheel_thinking_budget` |

#### Tests

- `test_with_thinking_kwargs` — verify dict structure.
- `test_extract_text_from_thinking_response` — mock response with thinking + text blocks.
- `test_thinking_disabled_by_default` — verify no thinking kwargs when config is off.
- `test_rule_evaluator_with_thinking` — mock response, verify deeper analysis extracted.

---

## Phase 6: Tool Use for Live Data in Reports

**Impact:** Reports can query the database mid-generation instead of requiring all context pre-computed. Produces more grounded, specific reports.
**Effort:** Large — define tool schemas, implement tool execution loop, handle multi-turn within a single report generation.
**Risk:** Medium — adds complexity to the report generation path; must preserve fail-safe behavior.

### Why

Reports currently receive pre-computed context from the repository layer. The caller must anticipate every data need. This leads to either bloated context (pass everything) or missed opportunities (the model wants to reference a stat that wasn't provided).

With tool use, the report generation call can query live data mid-generation:
- "What was Team X's record in the 5 games before this rule changed?"
- "How many times has this governor voted against the majority this season?"
- "What's the league-wide three-point percentage trend over the last 4 rounds?"

The repository layer (`db/repository.py`) already has clean async query functions that map naturally to tool definitions.

### Implementation

#### A. Define tools from repository functions

Create tool definitions in `src/pinwheel/ai/tools.py` (new file):

```python
REPORT_TOOLS = [
    {
        "name": "get_team_record",
        "description": "Get a team's win-loss record for a season or round range.",
        "input_schema": {
            "type": "object",
            "properties": {
                "team_id": {"type": "string"},
                "season_id": {"type": "string"},
                "from_round": {"type": "integer", "description": "Optional start round"},
                "to_round": {"type": "integer", "description": "Optional end round"},
            },
            "required": ["team_id", "season_id"],
        },
    },
    {
        "name": "get_scoring_averages",
        "description": "Get league-wide or team-specific scoring averages for a round range.",
        "input_schema": {
            "type": "object",
            "properties": {
                "season_id": {"type": "string"},
                "team_id": {"type": "string", "description": "Optional — omit for league-wide"},
                "from_round": {"type": "integer"},
                "to_round": {"type": "integer"},
            },
            "required": ["season_id"],
        },
    },
    {
        "name": "get_governor_voting_record",
        "description": "Get a governor's voting history — alignment rate, swing votes, proposal count.",
        "input_schema": {
            "type": "object",
            "properties": {
                "governor_id": {"type": "string"},
                "season_id": {"type": "string"},
            },
            "required": ["governor_id", "season_id"],
        },
    },
    {
        "name": "get_rule_change_history",
        "description": "Get the history of rule changes for a season — parameter, old/new values, round enacted.",
        "input_schema": {
            "type": "object",
            "properties": {
                "season_id": {"type": "string"},
            },
            "required": ["season_id"],
        },
    },
]
```

#### B. Tool execution loop

The Messages API returns `tool_use` content blocks when the model wants to call a tool. The caller must execute the tool and send the result back:

```python
async def generate_report_with_tools(
    system: str,
    user_msg: str,
    repo: Repository,
    session: AsyncSession,
    api_key: str,
    max_tool_rounds: int = 3,
) -> str:
    """Generate a report with tool use for live data queries."""
    client = anthropic.AsyncAnthropic(api_key=api_key)
    messages = [{"role": "user", "content": user_msg}]

    for _ in range(max_tool_rounds):
        response = await client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=1500,
            system=cacheable_system(system),
            messages=messages,
            tools=REPORT_TOOLS,
        )

        # Check if model wants to use tools
        if response.stop_reason != "tool_use":
            # Model is done — extract final text
            return extract_text(response)

        # Execute tool calls and build result messages
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = await execute_tool(block.name, block.input, repo, session)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                })

        # Add assistant response + tool results to conversation
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

    # Max rounds exceeded — return whatever text we have
    return extract_text(response)
```

#### C. Tool execution dispatch

```python
async def execute_tool(
    name: str,
    input_data: dict,
    repo: Repository,
    session: AsyncSession,
) -> dict:
    """Execute a report tool and return the result."""
    match name:
        case "get_team_record":
            return await repo.get_team_record(session, **input_data)
        case "get_scoring_averages":
            return await repo.get_scoring_averages(session, **input_data)
        case "get_governor_voting_record":
            return await repo.get_governor_voting_record(session, **input_data)
        case "get_rule_change_history":
            return await repo.get_rule_change_history(session, **input_data)
        case _:
            return {"error": f"Unknown tool: {name}"}
```

#### D. Phased rollout

Start with tool use on the simulation report only (most data-hungry). If quality improves, extend to governance and private reports.

```python
# In report.py — opt-in per report type
if settings.pinwheel_report_tool_use and report_type == "simulation":
    text = await generate_report_with_tools(system, user_msg, repo, session, api_key)
else:
    text = await _call_claude(system, user_msg, api_key, ...)
```

#### Configuration

Add to `config.py`:
```python
pinwheel_report_tool_use: bool = False  # Opt-in, experimental
```

#### New repo methods needed

Some of the tool functions require new repo methods that return simple dicts:

| Method | Returns |
|--------|---------|
| `get_team_record(session, team_id, season_id, from_round?, to_round?)` | `{"wins": int, "losses": int, "games": int}` |
| `get_scoring_averages(session, season_id, team_id?, from_round?, to_round?)` | `{"avg_score": float, "avg_margin": float, "three_pt_pct": float}` |
| `get_governor_voting_record(session, governor_id, season_id)` | `{"votes": int, "alignment_rate": float, "swing_votes": int}` |
| `get_rule_change_history(session, season_id)` | `[{"parameter": str, "old": val, "new": val, "round": int}]` |

#### Files to modify/create

| File | Change |
|------|--------|
| `src/pinwheel/ai/tools.py` | **NEW** — tool definitions, execution dispatch |
| `src/pinwheel/ai/report.py` | Add `generate_report_with_tools()`, conditional tool-use path |
| `src/pinwheel/db/repository.py` | Add 4 new query methods for tool execution |
| `src/pinwheel/config.py` | Add `pinwheel_report_tool_use` setting |

#### Tests

- `test_tool_definitions_valid` — verify all tool schemas are valid JSON Schema.
- `test_execute_tool_dispatch` — each tool name routes to correct repo method.
- `test_generate_report_with_tools_no_tools` — model doesn't use tools, text returned directly.
- `test_generate_report_with_tools_one_round` — mock one tool call, verify result fed back.
- `test_generate_report_with_tools_max_rounds` — verify loop terminates at max.
- `test_tool_use_fallback` — when disabled, existing path used.

---

## Summary: Implementation Order and Dependencies

```
Phase 1: Prompt Caching          ← No dependencies, immediate win
    ↓
Phase 2: Structured Output       ← Independent of Phase 1, can parallelize
    ↓
Phase 3: Streaming Commentary    ← Builds on Phase 1 (cached system prompts)
    ↓
Phase 4: Batch API               ← Independent, but lower priority
    ↓
Phase 5: Extended Thinking       ← Independent, experimental
    ↓
Phase 6: Tool Use for Reports    ← Depends on mature repo layer, largest effort
```

Phases 1 and 2 can be done in parallel. Phase 3 benefits from Phase 1's cached prompts. Phases 4, 5, and 6 are independent and can be done in any order based on priority.

## Cost Impact Estimates

| Phase | Input token change | Output token change | Net cost change |
|-------|-------------------|--------------------|-----------------|
| 1. Prompt Caching | -90% on cached prompts | No change | -20-30% overall |
| 2. Structured Output | No change | No change | Neutral (reliability win) |
| 3. Streaming | No change | No change | Neutral (UX win) |
| 4. Batch API | No change | No change | -50% on batch-eligible calls |
| 5. Extended Thinking | No change | +200-300% on enabled calls | +cost on 3 call types |
| 6. Tool Use | +input for tool results | +output for tool calls | +10-20% on tool-enabled reports |

Phases 1 and 4 save money. Phase 2 and 3 are neutral. Phases 5 and 6 cost more but deliver proportionally higher quality. Net effect depends on configuration — all cost-increasing features are opt-in.

## Verification Plan

After each phase:
1. `uv run pytest -x -q` — all tests pass.
2. `uv run ruff check src/ tests/` — clean lint.
3. `uv run python scripts/demo_seed.py seed && uv run python scripts/demo_seed.py step 3` — full game loop runs without errors.
4. Check `/admin/costs` — usage dashboard reflects new patterns (cache hits, batch jobs, etc.).
5. Manually verify: reports still read well, commentary still entertaining, interpretation still accurate.

## What Stays the Same

- All mock fallbacks preserved — tests never hit the API.
- `_call_claude()` in `report.py` continues to work for prose-output reports.
- Event bus, SSE, repository pattern, game loop structure unchanged.
- All existing tests pass without modification (new tests are additive).
- Privacy boundaries (private reports, sandboxed interpretation) fully preserved.
- Graceful degradation — every new feature has a config toggle and a fallback path.
