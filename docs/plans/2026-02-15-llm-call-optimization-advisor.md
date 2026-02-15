# Plan: LLM Call Optimization + Advisor Conversations

## Context

During testing, TK makes excessive LLM calls when multiple advisors are selected. With 3 advisors, 3 separate API calls are made — each sending the full document. The frontend staggers these by 500ms to avoid rate limits, but this is slow and wasteful. Additionally, Anthropic supports prompt caching that we're not using, leaving ~90% token savings on the table. Finally, the user wants advisors to respond to each other when they comment on overlapping text, creating a richer review experience.

## Implementation Order

### Change 1: Anthropic Prompt Caching (lowest risk, immediate savings)
### Change 2: Batch Multi-Advisor Suggestions (biggest impact on call volume)
### Change 3: Advisor Conversations (new feature, depends on Change 2)

---

## Change 1: Anthropic Prompt Caching

**Goal:** Cache the system prompt and document text in Anthropic's API so repeated calls for the same document pay ~10% of input token cost.

### Files to modify

**`core/tk/llm/base.py`** (line 15)
- Add `**kwargs` to `generate()` signature to allow provider-specific options
- Signature becomes: `generate(self, system_prompt, user_prompt, temperature=0.7, max_tokens=1000, **kwargs) -> str`

**`core/tk/llm/anthropic_client.py`** (lines 40-91)
- Check for `cache_content=True` in kwargs
- When true, convert `system` param from string to content block array with `cache_control`:
  ```python
  system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]
  ```
- Pass through normally when `cache_content` is not set

**`core/tk/llm/openai_client.py`** (line 32)
- Add `**kwargs` to `generate()` — ignore extra kwargs (no-op for OpenAI)

**`core/tk/llm/ollama_client.py`** (line 128)
- Add `**kwargs` to `generate()` — ignore extra kwargs (no-op for Ollama)

**`core/tk/api/server.py`** (lines 702-744)
- When provider is Anthropic, create callback that passes `cache_content=True`:
  ```python
  if isinstance(provider, AnthropicProvider):
      llm_callback = lambda sys, usr: provider.generate(sys, usr, cache_content=True)
  ```

### Tests (new file: `core/tests/test_prompt_caching.py`)
- Test that `**kwargs` passes through to Anthropic generate
- Test that `cache_content=True` produces content block array with `cache_control`
- Test that `cache_content=False` or missing uses plain string system prompt
- Test that OpenAI/Ollama ignore unknown kwargs gracefully
- ~10 tests

---

## Change 2: Batch Multi-Advisor Suggestions

**Goal:** Replace N separate LLM calls (one per advisor) with 1 call that produces suggestions from all advisor perspectives.

### New API endpoint

**`core/tk/api/server.py`** — Add `POST /suggestions/batch`

```python
class BatchSuggestionRequest(BaseModel):
    current_text: str = Field(..., max_length=100000)
    cursor_position: int = Field(default=0, ge=0)
    selected_text: str = Field(default="", max_length=10000)
    document_title: str = Field(default="", max_length=500)
    role_ids: list[str] = Field(..., min_length=1, max_length=6)
    llm_config: LLMConfig | None = None
```

Returns: `{"advisors": {"general": InlineSuggestionsResponse, "technical_writer": InlineSuggestionsResponse, ...}}`

### New engine method

**`core/tk/suggestions/engine.py`** — Add `generate_batch_inline_suggestions()`

- Takes `WritingContext` + list of `Role` objects + `llm_callback`
- If only 1 role, delegates to existing `generate_inline_suggestions()`
- For 2+ roles, builds a single prompt that asks the LLM to produce suggestions from each perspective:
  ```
  You are a panel of writing advisors reviewing a document.
  For each advisor below, provide 3-5 inline suggestions from their perspective.

  ## Advisor 1: Technical Writer
  {technical_writer.system_prompt summary}

  ## Advisor 2: Creative Writer
  {creative_writer.system_prompt summary}

  Respond in JSON:
  {
    "advisors": {
      "technical_writer": {"suggestions": [...]},
      "creative_writer": {"suggestions": [...]}
    }
  }
  ```
- Reuses existing `_generate_fallback_suggestions()` on failure
- Reuses existing 4-level text matching logic (extract into a helper method `_find_text_position()`)
- Uses same retry logic (delays [3, 9, 15])
- Passes `cache_content=True` for Anthropic providers

### Frontend changes

**`web/src/lib/stores/suggestions.ts`** — Modify `getSuggestions()`

- When `selectedAdvisors.length >= 2`, call `/suggestions/batch` with all role_ids
- When `selectedAdvisors.length === 1`, call existing `/suggestions` endpoint (no change)
- Remove 500ms stagger logic for multi-advisor case
- Parse batch response and attribute each suggestion to its advisor

**`web/src/lib/api/client.ts`** — Add `getBatchSuggestions()` method

### Refactor: Extract text matching helper

**`core/tk/suggestions/engine.py`** — Extract `_find_text_position(text, document)` from the inline matching logic at lines 314-370 into a standalone method. Reuse it in both `generate_inline_suggestions()` and `generate_batch_inline_suggestions()`.

### Tests

**New file: `core/tests/test_batch_suggestions.py`** (~20 tests)
- Single advisor delegates to existing method
- Multiple advisors produces single LLM call
- JSON parsing of multi-advisor response
- Fallback when batch fails (falls back to sequential)
- Text position matching preserved for each advisor
- Empty/malformed response handling
- `cache_content=True` passed when Anthropic

**Modify: `web/src/lib/stores/__tests__/suggestions.test.ts`** (~8 tests)
- Test that 2+ advisors calls batch endpoint
- Test that 1 advisor calls single endpoint
- Test response aggregation with advisor attribution
- Test error handling when batch fails

---

## Change 3: Advisor Conversations

**Goal:** When multiple advisors comment on overlapping text ranges, generate a follow-up "discussion" where advisors respond to each other.

### How it works

1. After batch suggestions come back, identify **overlapping clusters** — groups of suggestions where `range_start..range_end` overlaps
2. For each cluster with 2+ advisors, make one additional LLM call asking the advisors to discuss each other's suggestions
3. Attach the discussion as `replies` on each suggestion in the cluster

### New models

**`core/tk/models.py`** — Add:

```python
class AdvisorReply(BaseModel):
    """A reply from one advisor to another's suggestion."""
    advisor_id: str
    advisor_name: str
    reply_to_advisor_id: str  # Who they're responding to
    text: str  # The response
    stance: str = "agree"  # "agree", "disagree", "extend"

class SuggestionThread(BaseModel):
    """A thread of discussion about overlapping suggestions."""
    anchor_text: str  # The text range being discussed
    range_start: int
    range_end: int
    suggestion_ids: list[str]  # Suggestions in this thread
    replies: list[AdvisorReply] = Field(default_factory=list)
```

### New engine method

**`core/tk/suggestions/engine.py`** — Add `generate_advisor_discussions()`

- Input: list of `InlineSuggestion` (with advisor attribution) + original document + list of `Role` objects + `llm_callback`
- Step 1: Find overlapping suggestion clusters (sort by range_start, group when ranges overlap)
- Step 2: For each cluster with 2+ unique advisors, build a prompt:
  ```
  The following advisors have commented on the same passage:

  Passage: "{anchor_text}"

  - Technical Writer suggested: "{suggestion}" because "{reasoning}"
  - Creative Writer suggested: "{suggestion}" because "{reasoning}"

  As each advisor, write a brief (1-2 sentence) response to the other's suggestion.
  Do they agree? Disagree? Want to build on it?

  JSON: {"replies": [{"from": "technical_writer", "to": "creative_writer", "text": "...", "stance": "agree|disagree|extend"}, ...]}
  ```
- Step 3: Parse replies and return `SuggestionThread` objects
- Passes `cache_content=True` for Anthropic

### API endpoint

**`core/tk/api/server.py`** — Extend `POST /suggestions/batch` response

- After generating batch suggestions, automatically detect overlaps and generate discussions
- Add `threads` field to batch response:
  ```json
  {
    "advisors": {...},
    "threads": [
      {
        "anchor_text": "...",
        "range_start": 0,
        "range_end": 50,
        "suggestion_ids": ["inline:abc", "inline:def"],
        "replies": [...]
      }
    ]
  }
  ```

### Frontend

**`web/src/lib/stores/suggestions.ts`** — Add thread handling
- New store: `suggestionThreads` writable
- Populated from batch response `threads` field

**`web/src/lib/components/journey/ReviseStage.svelte`** — Thread UI
- When rendering a comment card, check if it belongs to a thread
- If yes, show a "Discussion" section below the suggestion with advisor replies
- Each reply shows: advisor emoji + name, their stance badge (Agrees/Disagrees/Extends), and their response text
- Visual grouping: comments in the same thread get a subtle left-border color connector

### Tests

**Add to `core/tests/test_batch_suggestions.py`** (~15 tests)
- Overlap detection: non-overlapping ranges produce no threads
- Overlap detection: overlapping ranges from same advisor produce no threads
- Overlap detection: overlapping ranges from different advisors produce thread
- Discussion prompt includes correct advisor names and suggestions
- JSON parsing of replies
- Graceful failure (if discussion generation fails, suggestions still returned without threads)
- Empty cluster handling

**Add to `web/src/lib/stores/__tests__/suggestions.test.ts`** (~5 tests)
- Thread store populated from batch response
- Thread store cleared on clearSuggestions()

---

## File Summary

### New files (3)
| File | Purpose |
|------|---------|
| `core/tests/test_prompt_caching.py` | Tests for prompt caching kwargs |
| `core/tests/test_batch_suggestions.py` | Tests for batch + conversations |
| (no new frontend test files — extend existing) | |

### Modified files (10)
| File | Changes |
|------|---------|
| `core/tk/llm/base.py` | Add `**kwargs` to abstract generate |
| `core/tk/llm/anthropic_client.py` | Implement `cache_content` kwarg |
| `core/tk/llm/openai_client.py` | Add `**kwargs` passthrough |
| `core/tk/llm/ollama_client.py` | Add `**kwargs` passthrough |
| `core/tk/models.py` | Add `AdvisorReply`, `SuggestionThread` |
| `core/tk/suggestions/engine.py` | Add batch method, extract text matcher, add discussion generator |
| `core/tk/api/server.py` | Add `/suggestions/batch` endpoint |
| `web/src/lib/stores/suggestions.ts` | Use batch endpoint for 2+ advisors, add thread store |
| `web/src/lib/api/client.ts` | Add `getBatchSuggestions()` |
| `web/src/lib/components/journey/ReviseStage.svelte` | Thread discussion UI |

### Test modifications (2)
| File | Changes |
|------|---------|
| `web/src/lib/stores/__tests__/suggestions.test.ts` | Batch + thread tests |
| `docs/prds/PRD_edits.md` | Session log |

## Estimated new tests: ~58

---

## Verification

1. **Unit tests pass:**
   ```bash
   cd core && uv run pytest tests/test_prompt_caching.py tests/test_batch_suggestions.py -v
   cd web && npx vitest run
   ```

2. **Manual smoke test:**
   - Start dev servers: `./scripts/dev.sh start`
   - Open web client, select 2-3 advisors, write a paragraph
   - Click "Get Feedback" — should see single network call to `/suggestions/batch`
   - Verify suggestions appear attributed to correct advisors
   - If overlapping suggestions exist, verify discussion thread appears

3. **Anthropic caching verification:**
   - With Anthropic key configured, make 2 requests for same document
   - Check API response headers or Anthropic dashboard for cache hit metrics
   - Second request should show reduced input token billing

4. **Backward compatibility:**
   - Single advisor still uses `/suggestions` endpoint (unchanged)
   - All existing tests pass unchanged
