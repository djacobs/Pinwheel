# TK Authorship Journey Implementation Plan

## Summary

Update the existing journey feature from 5 stages to 4 stages, integrate with the main editor, and connect to the real suggestions API.

**Current State:** 5 stages (Canvas → Draft → Review → Revise → Final), separate `/journey` route, mock data
**Target State:** 4 stages (Canvas → Draft → Revise → Final), integrated with main editor, real API

---

## Files to Modify

### 1. `web/src/lib/stores/journey.ts`
**Changes:**
- Change `JourneyStage` type: remove `'review'`
- Update `requestFeedback()`: transition to `'revise'` instead of `'review'`
- Remove `beginRevising()` method (no longer needed)
- Add `focusModeEnabled: boolean` to `JourneyState`
- Add `toggleFocusMode()` method
- Add per-document persistence: `tk_journey_state_${documentId}`
- Update `progressPercent` derived store for 4 stages
- Update `requestNewReview()` to `getMoreFeedback()` → stays in `'revise'`

### 2. `web/src/lib/components/journey/ReviseStage.svelte`
**Changes (major rewrite):**
- Adopt split-view layout from ReviewStage (editor 60%, comments 40%)
- Add Focus Mode toggle (hides comments panel)
- Add "Get More Feedback" button
- Add progress indicator "X of Y comments addressed"
- Keep Accept/Edit/Skip buttons per comment
- Add comment-to-text linking (click comment → scroll to text)
- Editor remains fully editable

### 3. `web/src/lib/components/journey/JourneyIndicator.svelte`
**Changes:**
- Update stages array: remove `'review'`
- Update to 4 stage indicators: Canvas, Draft, Revise, Final

### 4. `web/src/lib/components/journey/DraftStage.svelte`
**Changes:**
- Replace mock `requestFeedback()` with real API call
- Import `getSuggestions` from suggestions store
- Map `InlineSuggestion[]` to `Comment[]` format
- Determine draft_stage by word count (<500: early, 500-2000: middle, >2000: late)

### 5. `web/src/lib/components/journey/index.ts`
**Changes:**
- Remove `ReviewStage` export
- Update comment from "Five-stage" to "Four-stage"

### 6. `web/src/routes/+page.svelte`
**Changes:**
- Add journey mode integration
- Conditionally render stage components based on `$journeyState.currentStage`
- Add JourneyIndicator to bottom when in journey mode
- Sync TipTapEditor content with journey state

---

## Files to Delete/Deprecate

### `web/src/lib/components/journey/ReviewStage.svelte`
- Functionality merged into ReviseStage
- Keep file but mark deprecated, or delete after ReviseStage updated

---

## Implementation Order

### Phase 1: Store Update (Day 1)
1. Update `journey.ts` - change to 4 stages
2. Add focus mode state and per-document persistence
3. Write tests: `web/src/lib/stores/__tests__/journey.test.ts`

### Phase 2: Component Updates (Days 2-3)
1. Update `JourneyIndicator.svelte` - 4 stages
2. Rewrite `ReviseStage.svelte` - merge Review functionality
3. Update `DraftStage.svelte` - connect to real API
4. Update `index.ts` - remove ReviewStage export
5. Write component tests

### Phase 3: Integration (Days 4-5)
1. Integrate journey mode into `+page.svelte`
2. Sync with TipTapEditor and document store
3. Test full flow end-to-end

---

## Key Code Changes

### journey.ts - Stage Type
```typescript
// Before
export type JourneyStage = 'canvas' | 'draft' | 'review' | 'revise' | 'final';

// After
export type JourneyStage = 'canvas' | 'draft' | 'revise' | 'final';
```

### journey.ts - requestFeedback
```typescript
// Before: transitions to 'review'
requestFeedback(comments: Comment[]) {
  update((state) => ({
    ...state,
    currentStage: 'review',
    // ...
  }));
}

// After: transitions directly to 'revise'
requestFeedback(comments: Comment[]) {
  update((state) => ({
    ...state,
    currentStage: 'revise',
    // ...
  }));
}
```

### journey.ts - Add Focus Mode
```typescript
export interface JourneyState {
  // ... existing fields ...
  focusModeEnabled: boolean;
}

// In createJourneyStore():
toggleFocusMode() {
  update((state) => ({
    ...state,
    focusModeEnabled: !state.focusModeEnabled
  }));
}
```

### DraftStage.svelte - Real API
```typescript
import { getSuggestions } from '$lib/stores/suggestions';
import { selectedRole } from '$lib/stores/editor';

async function handleRequestFeedback() {
  const response = await getSuggestions();
  const comments = mapSuggestionsToComments(response.suggestions);
  journeyState.requestFeedback(comments);
}
```

---

## Verification

### Unit Tests
Run: `cd web && npm test`

**Required test coverage:**
- `journey.test.ts`: 15+ tests for store
- Stage component tests: 5+ tests each

### Integration Test (Manual)
1. Start at Canvas → type text → verify transition to Draft
2. In Draft → click "Ready for feedback?" → verify API call → verify transition to Revise
3. In Revise → toggle Focus Mode → verify comments panel hides/shows
4. In Revise → click "Get More Feedback" → verify new suggestions load
5. In Revise → Accept/Edit/Skip all comments → verify transition to Final
6. In Final → click export → verify file downloads
7. In Final → click "Keep Editing" → verify return to Draft

### Import Validation
Run: `cd web && npm run validate`

---

## Dependencies

- Existing: `web/src/lib/stores/suggestions.ts` - `getSuggestions()`
- Existing: `web/src/lib/stores/editor.ts` - `selectedRole`, `editorContent`
- Existing: `web/src/lib/api/client.ts` - `apiClient.getSuggestions()`
- Existing: TipTapEditor component

---

## Open Questions Resolved

| Question | Decision |
|----------|----------|
| Keep ReviewStage or delete? | Delete after merging into ReviseStage |
| Per-document or global state? | Per-document: `tk_journey_state_${docId}` |
| Integrate into main page or keep separate route? | Integrate into main `+page.svelte` |
