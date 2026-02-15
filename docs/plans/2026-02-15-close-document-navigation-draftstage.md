# Plan: Add Close Document / Navigation to DraftStage Toolbar

## Context

When a document is open in DraftStage, there's no way to close it or navigate back to the file browser/canvas. The "+ New" button is an `<a href="/">` link but it doesn't work because the journey state remains in `draft` stage — so navigating to `/` just re-renders DraftStage. There's also no way to browse other project files from within the editor.

## Changes

### 1. Fix "+ New" button in DraftStage (line 651)

**File:** `web/src/lib/components/journey/DraftStage.svelte`

Change `<a href="/">` to a `<button>` that calls `journeyState.startNew()` to reset state back to canvas, then navigates to `/`.

```svelte
<button class="new-btn" title="Start a new document" onclick={handleNewDocument}>
  <svg ...>+</svg>
  New
</button>
```

Add handler:
```ts
import { goto } from '$app/navigation';

function handleNewDocument() {
  journeyState.startNew();
  goto('/');
}
```

### 2. Add close (×) button to the filename badge

**File:** `web/src/lib/components/journey/DraftStage.svelte`

Add a small × button inside the `.source-file-badge` (line 658) that closes the current document and returns to canvas. This gives users an obvious way to close the current file.

```svelte
{#if $journeyState.sourceFile}
  <span class="source-file-badge" title="Editing: {$journeyState.sourceFile.filePath}">
    <span class="file-icon">📄</span>
    <span class="file-name">{$journeyState.sourceFile.fileName}</span>
    <button class="close-file-btn" onclick={handleCloseDocument} title="Close file">×</button>
  </span>
{/if}
```

Add handler:
```ts
function handleCloseDocument() {
  journeyState.startNew();
  goto('/');
}
```

Style the × button subtly (muted color, hover brightens, no border).

### 3. Same fix in ReviseStage if it has a similar toolbar

Check `ReviseStage.svelte` for the same issue and apply consistent fix.

## Files to modify

- `web/src/lib/components/journey/DraftStage.svelte` — fix "+ New", add close button
- `web/src/lib/components/journey/ReviseStage.svelte` — check for same toolbar issue

## Verification

1. Open a project file → verify document loads in DraftStage
2. Click × on filename badge → verify returns to canvas with file browser available
3. Click "+ New" → verify returns to canvas for a blank document
4. From canvas, re-open the same file → verify it loads correctly
5. Run `cd web && npx vitest run` to confirm existing tests pass
