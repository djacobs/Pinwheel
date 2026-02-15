# Plan: Token Counter Animation + Token Space Indicator

## Context
When users click "Get Feedback," the button shows static "Getting feedback..." text with no progress indication. For long documents, there's no warning that the document may exceed the model's context window. Two new P0 requirements (already in TK_03 PRD) need implementation:

1. **Token counter animation** — visual progress during feedback gathering
2. **Token space indicator** — persistent display of how much context budget the document uses

## Key constraints discovered
- Backend returns no token usage data; the API is a simple request/response with no streaming
- `loadingSuggestion` is a boolean writable store (`suggestions.ts:21`)
- Backend enforces `max_length=100000` chars on `current_text` (`server.py:686,755`)
- `AVAILABLE_MODELS` in `settings.ts` has cost info but NO context window sizes
- DraftStage already shows `{wordCount()} words` in the header (`DraftStage.svelte:666`)
- Token estimation: ~4 chars/token for English text (industry standard heuristic)

## Approach

### Part 1: Token estimation utility
Create a small utility that estimates token count from text length. This is purely client-side — no API changes needed.

**File**: `web/src/lib/utils/tokens.ts` (new)
```ts
// Context window sizes per model (input tokens)
const MODEL_CONTEXT_WINDOWS: Record<string, number> = {
  'gpt-4o': 128000,
  'gpt-4o-mini': 128000,
  'gpt-4-turbo': 128000,
  'claude-sonnet-4-20250514': 200000,
  'claude-3-5-sonnet-20241022': 200000,
  'claude-3-haiku-20240307': 200000,
  'llama3.2': 128000,
  'llama3.1': 128000,
  'mistral': 32000,
  'gemma2': 8192,
  'qwen2.5': 32000,
};

export function estimateTokens(text: string): number
export function getContextWindow(modelId: string): number
export function getTokenBudgetPercent(text: string, modelId: string): number
```

### Part 2: Token counter animation (DraftStage footer)
Replace the static "Getting feedback..." text with an animated token counter that shows:
- Estimated tokens being analyzed (e.g., "Analyzing ~2,450 tokens...")
- A subtle progress animation (CSS shimmer/pulse, not a fake progress bar)
- The selected model name

**File**: `web/src/lib/components/journey/DraftStage.svelte`
- In the footer's feedback button area (lines 752-766), replace:
  ```
  Getting feedback...
  ```
  with:
  ```
  Analyzing ~{estimatedTokens} tokens...
  ```
  plus a CSS shimmer animation on the button
- Compute `estimatedTokens` from `editorPlainText` using the utility
- Add the model name as secondary text (e.g., "via Claude Sonnet 4")

### Part 3: Token space indicator (DraftStage header + ReviseStage header)
Show a small token budget indicator next to the existing word count in the header. This indicator:
- Always visible, shows "~X tokens / Y context"
- Changes color when approaching limits: green (<50%), amber (50-80%), red (>80%)
- Shows a warning tooltip when >80%

**Files**: Both `DraftStage.svelte` and `ReviseStage.svelte` headers
- In `header-right` next to the word count, add:
  ```
  <span class="token-budget" class:warning={budgetPercent > 80} class:caution={budgetPercent > 50}>
    ~{estimatedTokens} tokens
  </span>
  ```
- Use color coding via CSS classes (no separate component needed — it's just a `<span>`)

## Files to modify

| File | Change |
|------|--------|
| `web/src/lib/utils/tokens.ts` | **NEW** — token estimation + context window lookup |
| `web/src/lib/components/journey/DraftStage.svelte` | Add token counter in footer loading state; add token budget in header |
| `web/src/lib/components/journey/ReviseStage.svelte` | Add token budget in header |
| `web/src/lib/stores/__tests__/tokens.test.ts` | **NEW** — tests for token estimation utility |

## Reused patterns
- `editorPlainText` store from `$lib/stores/editor` — already available in DraftStage
- `selectedModel` store from `$lib/stores/settings` — for context window lookup
- `wordCount()` pattern already in DraftStage header — token display sits next to it
- `loadingSuggestion` store — triggers the animated state

## Implementation steps

### 1. Create `web/src/lib/utils/tokens.ts`
- `estimateTokens(text)`: returns `Math.ceil(text.length / 4)`
- `getContextWindow(modelId)`: lookup from `MODEL_CONTEXT_WINDOWS`, default 128000
- `getTokenBudgetPercent(text, modelId)`: `(estimateTokens(text) / getContextWindow(modelId)) * 100`
- `formatTokenCount(n)`: formats with commas (e.g., `2,450`)

### 2. Create `web/src/lib/stores/__tests__/tokens.test.ts`
- Test `estimateTokens` with various lengths
- Test `getContextWindow` for known models and unknown fallback
- Test `getTokenBudgetPercent` calculations
- Test `formatTokenCount` formatting

### 3. Update DraftStage footer loading state
- Import `estimateTokens`, `formatTokenCount`, `getContextWindow` from `$lib/utils/tokens`
- Import `selectedModel` from `$lib/stores/settings`
- Replace `Getting feedback...` with `Analyzing ~{formatTokenCount(estimateTokens($editorPlainText))} tokens...`
- Add a CSS shimmer animation on `.feedback-btn.loading` for visual progress
- Below the token text, show model name: `via {modelName}`

### 4. Add token budget indicator to DraftStage header
- In `header-right` (line 665), between word count and save button, add a token budget `<span>`
- Compute `budgetPercent` using `getTokenBudgetPercent($editorPlainText, $selectedModel)`
- Color classes: normal (inherited), `.caution` (amber, >50%), `.warning` (red, >80%)
- Tooltip on hover showing full context: "~X of Y tokens (Z%)"

### 5. Add token budget indicator to ReviseStage header
- Same pattern as DraftStage — import utilities, add `<span>` in header next to word count
- ReviseStage uses `content` prop rather than `$editorPlainText`, so compute from that

### 6. Add styles
- `.token-budget` — base style (subtle, matches word count aesthetic)
- `.token-budget.caution` — amber color
- `.token-budget.warning` — red color with subtle pulse
- `@keyframes shimmer` — for the loading button animation

## Verification
1. `npx vitest run` — new token utility tests pass, no regressions
2. `npx vite build` — compiles cleanly
3. Manual testing:
   - Open DraftStage with ~500 words → token indicator shows green ~2,000 tokens
   - Paste a very long document → indicator turns amber then red
   - Click "Get Feedback" → button shows "Analyzing ~X tokens..." with shimmer animation
   - Check ReviseStage also shows token budget in header
