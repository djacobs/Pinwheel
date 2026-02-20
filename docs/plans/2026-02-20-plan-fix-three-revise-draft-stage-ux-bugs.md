# Plan: Fix Three Revise/Draft Stage UX Bugs

## Bug 1: "Get More Feedback" clears comments but never fetches new ones

**Root cause:** `getMoreFeedback()` in `journey.ts:557` clears the comments array but never triggers a new API call. ReviseStage has no mechanism to call `getSuggestions()` or dispatch an event to the parent.

**Fix:** Add an `onRequestFeedback` callback prop to ReviseStage and wire it up in both parent pages.

### Files to modify:

**`web/src/lib/components/journey/ReviseStage.svelte`**
- Add `onRequestFeedback?: () => void` to the Props interface
- Import `loadingSuggestion` from suggestions store
- In `getMoreFeedback()`: call `journeyState.getMoreFeedback()` then invoke `onRequestFeedback?.()`
- Add loading state: when `$loadingSuggestion` is true, show a spinner/animation in the comments panel instead of an empty list
- When loading finishes and comments are still empty, show a notice: "No feedback received. Check your LLM connection."

**`web/src/routes/p/[projectId]/[...path]/+page.svelte`** (~line 154)
- Pass `onRequestFeedback={handleGetMoreFeedback}` to ReviseStage
- Create `handleGetMoreFeedback()` that mirrors the feedback-fetching logic from DraftStage's `requestFeedback()` — calls `getSuggestions()`, maps results to comments via `journeyState.requestFeedback()`

**`web/src/routes/+page.svelte`** (~line where ReviseStage is rendered)
- Same pattern: pass `onRequestFeedback` callback

---

## Bug 2: Footer elements overlap when returning from Revise to Draft

**Root cause (two issues):**
1. `suggestionError` store is NOT cleared when transitioning back to Draft. `returnToDraft()` in journey.ts doesn't touch suggestion stores, and `clearSuggestions()` in suggestions.ts doesn't clear `suggestionError`.
2. CSS conflict: `.feedback-cta` is `position: absolute` (centered via transform), while `.error-message` uses flex auto-margins — both try to occupy the same footer space.

**Fix:**

**`web/src/lib/stores/suggestions.ts`** (line 126)
- Add `suggestionError.set(null)` to `clearSuggestions()`
- Add `failedAdvisors.set([])` too for completeness

**`web/src/lib/components/journey/DraftStage.svelte`**
- Clear suggestion error on mount or when stage becomes active: call `suggestionError.set(null)` in `onMount`
- CSS fix: make error message and feedback CTA mutually exclusive (show error OR button, not both overlapping), or stack them vertically

---

## Bug 3: Comment selection and highlighting broken in ReviseStage

**Symptoms (from screenshot):**
- User clicks comment 3, but comment 1 stays red/active
- No text is highlighted in the document panel
- Clicking a comment card does nothing — only the small numbered circles in the margin have click handlers

**Root cause:** In `ReviseStage.svelte`:
- The `<article class="comment-card">` elements have no `onclick` handler. Only the margin indicator buttons (`<button class="comment-indicator">`, line 342) call `handleCommentClick()`.
- There's no text highlighting in the rendered document — the content is rendered as static `{@html renderedContent}` with no mechanism to highlight ranges.

**Fix in `web/src/lib/components/journey/ReviseStage.svelte`:**
1. Add `onclick={() => handleCommentClick(comment.id)}` to each `.comment-card` article element (line 374)
2. Add `cursor: pointer` to `.comment-card` CSS
3. Fix the active card styling — currently `.comment-card.active` only changes background/border subtly. Make it visually distinct with a brighter left border or different background.

Text highlighting in the document is a larger feature (would require parsing rendered HTML and wrapping ranges). For now, the indicator circles and scrolling-to-card behavior provide the connection. We can skip document-side highlighting in this pass.

---

## Bug 4: Only 1 suggestion per advisor (expected 3-5) — lower priority

**Root cause:** In `engine.py:290-315`, each suggestion's `original_text` must be found verbatim in the document. With markdown documents (headers, bold markers, list prefixes), the LLM often returns slightly different text. Most suggestions get filtered out with a warning log.

**Fix in `core/tk/suggestions/engine.py`:**
- Add whitespace-normalized matching as a fallback: strip extra spaces/newlines from both `original_text` and document content before matching
- Add a substring match fallback: if exact match fails and case-insensitive fails, try matching the first 40+ characters as a prefix search
- Keep position calculation accurate by finding the match in the original (non-normalized) text

---

## Verification

1. Restart API server (`./scripts/dev.sh restart api`)
2. Open a document, select 3 advisors, click "Get Feedback"
3. Verify: multiple suggestions per advisor appear in Revise stage
4. Click "Get More Feedback" — should show loading spinner, then new comments
5. Click "Back to Draft" — footer should show clean state, no overlapping error
6. Build check: `cd web && npx vite build`
