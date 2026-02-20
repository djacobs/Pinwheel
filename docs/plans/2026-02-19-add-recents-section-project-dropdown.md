# Plan: Add "Recents" Section to Project Dropdown

## Context

Standalone documents (not part of a project) are saved to the SQLite backend via `saveDocument()`, but there's no UI to find them again. The user created a new untitled story, hit save, and it vanished — the document exists in the database but nothing in the app surfaces it. Adding a "Recents" section to the project dropdown gives these orphaned documents a home.

---

## Files to modify

- `web/src/lib/components/ProjectSelector.svelte` — add Recents section to dropdown, load recent docs on open
- `web/src/lib/stores/document.ts` — ensure `loadUserDocuments()` is accessible; add `openDocument()` that loads + transitions to draft
- `web/src/lib/components/__tests__/ProjectSelector.test.ts` — new tests for Recents section

## Implementation

### 1. `ProjectSelector.svelte` — Add Recents section to dropdown

**When dropdown opens**, load recent standalone documents:
- Import `userDocuments`, `loadUserDocuments`, `loadDocument` from `$lib/stores/document`
- Import `journeyState` from `$lib/stores/journey`
- Import `editorContent` from `$lib/stores/editor`
- On dropdown toggle (when opening), call `loadUserDocuments()` to fetch recent docs
- State: `let loadingRecents = false`

**In the dropdown markup** — after the project list `</ul>`, before `<div class="dropdown-footer">`:
- Add a "Recents" section header (styled like `dropdown-header` but smaller)
- Show up to 5 most recent documents sorted by `updated_at` desc
- Each item shows: title (or "Untitled"), relative time (e.g. "2 hours ago")
- Clicking a recent doc calls `handleOpenRecentDoc(doc)`

**`handleOpenRecentDoc(doc: Document)`:**
1. Close dropdown
2. Call `loadDocument(doc.id)` — populates `currentDocument`, `editorContent`, `documentTitle`
3. Call `journeyState.beginDraft(doc.title)` — transitions to draft stage with the loaded content
4. The editor will pick up the content from `editorContent` store

**Time formatting:** Simple relative time helper inline — no library needed:
```typescript
function timeAgo(dateStr: string): string {
  const seconds = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000);
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  if (seconds < 604800) return `${Math.floor(seconds / 86400)}d ago`;
  return new Date(dateStr).toLocaleDateString();
}
```

### 2. `document.ts` — No changes needed

`loadUserDocuments()` and `loadDocument()` already exist and work correctly. `userDocuments` store is already exported.

### 3. Tests

- Module validation: component imports correctly
- Source code structure: verify "Recents" section markup exists
- Verify `loadUserDocuments` is imported
- Verify `handleOpenRecentDoc` function exists
- Verify truncation to 5 items (source check for `.slice(0, 5)`)

---

## Verification

1. `cd web && npx vitest run` — all tests pass
2. `cd web && npx svelte-check --threshold error` — no type errors
3. Manual: save a standalone doc → open project dropdown → see it under "Recents" → click → loads into editor
