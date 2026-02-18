# Plan: Resolve 8 UX Journey Gaps

## Context

A deep analysis of TK's authorship journey (Canvas → Draft → Revise → Final) against the PRDs revealed 8 issues: 2 active bugs (P0), 2 quick wins (P1), 2 UX improvements (P2), and 2 maintenance items (P3). This plan resolves all of them across 4 phases.

---

## Phase 1: P0 Bug Fixes (~30 min)

### 1A — FinalStage missing tables plugin

**Bug:** `FinalStage.svelte` creates a TurndownService without the `tables` plugin. Exporting markdown from Final Stage silently strips all tables. DraftStage and ReviseStage already have the fix.

**File:** `web/src/lib/components/journey/FinalStage.svelte`
- Line 8: Add `import { tables } from 'turndown-plugin-gfm';`
- After line 20: Add `turndownService.use(tables);`

*Note: Phase 2B replaces this with a shared factory, but fixing it here first prevents data loss during the rest of the work.*

### 1B — Auto-save is fake

**Bug:** DraftStage lines 489-502 toggle `isSaving` cosmetically without actually saving. Users see a "saving" indicator that lies. The real `handleSave()` exists at lines 92-132.

**File:** `web/src/lib/components/journey/DraftStage.svelte`
- Remove the fake simulation (lines 489-502)
- Remove dead `isSaving` variable (line 78) — only used by the simulation; the header uses `isAnySaving` (line 135) which tracks real saves
- Add a content change tracker: `let previousContent = $state<string | null>(null);`
- Replace with real debounced auto-save (30s interval, only when content changed):
  ```
  $effect: if content !== previousContent and previousContent !== null →
    debounce 30s → call handleSave() → update previousContent
  Initialize previousContent on first content load
  ```

---

## Phase 2: P1 Quick Wins (~3 hrs)

### 2A — Render JourneyIndicator

**Gap:** `JourneyIndicator.svelte` (216 lines) is fully built and exported but never rendered. Users have no stage indicator.

The component is `position: fixed; bottom: 0`, reads `$journeyState.currentStage` directly (no props needed), and supports backward navigation via `journeyState.transitionTo()`.

**File 1:** `web/src/routes/+page.svelte`
- Add `JourneyIndicator` to import from `$lib/components/journey`
- Render after the stage switch `{/if}`, inside `.journey-container`:
  ```svelte
  {#if $journeyState.currentStage !== 'canvas'}
    <JourneyIndicator />
  {/if}
  ```

**File 2:** `web/src/routes/p/[projectId]/[...path]/+page.svelte`
- Add `JourneyIndicator` to import
- Render after the stage switch (no canvas check needed — this route starts at Draft)

**New test file:** `web/src/lib/components/journey/__tests__/JourneyIndicator.test.ts` (~5 tests: renders stages, active class on current, past stages clickable, future stages disabled)

### 2B — Extract shared utilities

**Problem:** 5 functions duplicated across 4+ files (180+ lines of copy-paste).

**New file: `web/src/lib/utils/advisors.ts`**

Extract from `+page.svelte`, `p/+page.svelte`, `DraftStage.svelte`, `ReviseStage.svelte`:

| Function | Duplicated in | Signature change |
|----------|--------------|-----------------|
| `getAdvisorInfo` | 4 files | Add `roles: Role[]` param (decouples from store) |
| `mapSuggestionType` | 3 files | None |
| `charPosToLine` | 3 files | None |
| `mapSuggestionToComment` | 3 files | Add `content: string` and `roles: Role[]` params |
| `fetchAndMapFeedback` | 2 files (as `handleGetMoreFeedback`) | New: takes `content` + `roles`, returns `Comment[]` |

Callers pass `$roles` and their local `content` variable at call sites.

**New file: `web/src/lib/utils/markdown.ts`**

```typescript
export function createTurndownService(): TurndownService
// Factory that always includes tables plugin. Prevents the FinalStage bug class.

export function markdownToHtml(markdown: string): string
export function htmlToMarkdown(html: string): string
```

Replace TurndownService config in: DraftStage (lines 22-37), ReviseStage (lines 28-43), FinalStage (lines 8-20).

**New file: `web/src/lib/utils/focusMode.ts`**

Extract `toggleFocusMode` and `handleFullscreenChange` (duplicated in both routes).

**New test files:**
- `web/src/lib/utils/__tests__/advisors.test.ts` (~8 tests)
- `web/src/lib/utils/__tests__/markdown.test.ts` (~6 tests, including table roundtrip)

**Update 6 consumer files:**
1. `web/src/routes/+page.svelte` — remove lines 139-200, import from utils
2. `web/src/routes/p/[projectId]/[...path]/+page.svelte` — remove lines 118-199, import from utils
3. `web/src/lib/components/journey/DraftStage.svelte` — remove lines 22-37 (TurndownService), 400-475 (advisor utils), import from utils
4. `web/src/lib/components/journey/ReviseStage.svelte` — remove lines 28-43 (TurndownService), 214-225 (getAdvisorInfo), import from utils
5. `web/src/lib/components/journey/FinalStage.svelte` — remove lines 8-20 (TurndownService), import from utils

---

## Phase 3: P2 UX Improvements (~1-2 days)

### 3A — "Just start writing" on Canvas

**Gap:** PRD says typing auto-transitions to Draft. Code has no text input on Canvas — users must pick a story type first.

**File:** `web/src/lib/components/journey/CanvasStage.svelte`
- Add `handleJustStartWriting()` → calls `journeyState.beginDraft()` with no args (already supports all-optional params, defaults to title "Untitled", no story type)
- Between the story type grid (line 107) and the divider (line 109), add:
  ```svelte
  <button class="quick-start-link" onclick={handleJustStartWriting}>
    or just start writing →
  </button>
  ```
- Style: subtle text link, `--jrn-text-dim` color, hover brightens

**Update test:** `web/src/lib/components/journey/__tests__/CanvasStage.test.ts` — add test for quick-start button

### 3B — Route consolidation (extract, don't merge)

**Strategy:** Extract shared logic into utilities (done in 2B), keep both routes. Full route merge risks breaking URLs/bookmarks for low benefit.

After 2B, the remaining duplications are:
- `toggleFocusMode` / `handleFullscreenChange` → extracted to `focusMode.ts` in 2B
- `handleGetMoreFeedback` → replaced by `fetchAndMapFeedback` from `advisors.ts` in 2B
- Each route's remaining unique logic (setup wizard, file loading, breadcrumbs) stays in its route

**Net result:** Each route drops ~80 lines of duplicated code. The routes remain separate but share all business logic through utilities.

---

## Phase 4: P3 Maintenance (~1 day)

### 4A — Extract DraftFooter from DraftStage

**File:** `web/src/lib/components/journey/DraftStage.svelte` (1,793 lines)

Extract lines 776-873 (footer template) + supporting state into:

**New file:** `web/src/lib/components/journey/DraftFooter.svelte`

Props: `selectedStoryType`, `advisorsCustomized`, `roles`, `selectedAdvisors`, `isRequestingFeedback`, `showFeedbackPrompt`, `displayTypes`, `onRequestFeedback`, `onPickStoryType`, `onToggleAdvisor`

DraftStage drops to ~1,600 lines. Further extraction (header) has diminishing returns.

**New test file:** `web/src/lib/components/journey/__tests__/DraftFooter.test.ts` (~4 tests)

### 4B — Update acceptance criteria tracker

**File:** `docs/prds/TK_15_Acceptance_Criteria.md`

Re-audit all criteria. Known shipped items still marked "Not Started": keyboard shortcuts (Cmd+Shift+R), toast notifications (failed advisors), token budget indicator, story types, JourneyIndicator (after 2A). Update counts.

---

## Files modified (summary)

| Phase | Files modified | Files created |
|-------|---------------|--------------|
| 1A | FinalStage.svelte | — |
| 1B | DraftStage.svelte | — |
| 2A | +page.svelte, p/+page.svelte | JourneyIndicator.test.ts |
| 2B | +page.svelte, p/+page.svelte, DraftStage.svelte, ReviseStage.svelte, FinalStage.svelte | advisors.ts, markdown.ts, focusMode.ts, advisors.test.ts, markdown.test.ts |
| 3A | CanvasStage.svelte, CanvasStage.test.ts | — |
| 3B | +page.svelte, p/+page.svelte | — |
| 4A | DraftStage.svelte | DraftFooter.svelte, DraftFooter.test.ts |
| 4B | TK_15_Acceptance_Criteria.md | — |

## Verification

After each phase:
```bash
cd /Users/djacobs/Documents/GitHub/TK/web && npx vitest run
cd /Users/djacobs/Documents/GitHub/TK/core && uv run pytest tests/ -v
```

End-to-end checks:
- Phase 1A: Create a document with tables, reach Final Stage, export as Markdown → tables preserved
- Phase 1B: Type in Draft, wait 30s, check that content is saved (reload page to verify)
- Phase 2A: Navigate through stages, confirm indicator shows at bottom with correct active state
- Phase 2B: All existing tests still pass after utility extraction
- Phase 3A: Open app, click "just start writing", confirm Draft stage loads with empty content
- Phase 4A: Footer renders identically after extraction (visual regression check)
