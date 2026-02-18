# Accessibility Improvements: Navigation & Contrast

## Context

The TK web client has accessibility gaps: missing focus rings on inputs, low-contrast dark mode text, no skip link (despite CSS existing), icon-only buttons without labels, no focus trapping in modals, missing ARIA attributes on dropdowns, and SettingsModal hardcoded to light-mode colors. This plan addresses all of these systematically using `focus-trap` for modal focus management.

---

## Phase 1: CSS Fixes (app.css)

**File:** `web/src/app.css`

### 1a. Fix input focus outline (line 459-466)
The `outline: none` on `input:focus` removes focus rings for keyboard users — biggest WCAG violation in the codebase. Change `:focus` to `:focus-visible` and replace `outline: none` with the standard focus ring:

```css
/* BEFORE */
input[type="text"]:focus, ... textarea:focus {
  border-color: var(--color-primary);
  outline: none;
}

/* AFTER */
input[type="text"]:focus-visible, ... textarea:focus-visible {
  border-color: var(--color-primary);
  outline: var(--color-focus-ring-width, 2px) solid var(--color-focus-ring);
  outline-offset: 0px;
}
```

### 1b. Fix `--jrn-text-dim` contrast (lines 138, 189)
`#6b7280` on `#0f0f0f` = ~3.6:1 (fails AA). Change to `#848e9b` (~4.8:1) in both dark-mode blocks. Not `#9ca3af` — that's already `--jrn-text-muted` and we need visual hierarchy between dim and muted. Used in 25 places across 5 files.

### 1c. Fix loading-container hardcoded color (+layout.svelte line 47)
Change `color: #718096` to `color: var(--color-text-muted)`.

### 1d. Add success/danger background variables for Phase 12
Add to `:root` and dark-mode blocks:
```css
--color-success-bg: #dcfce7;  /* dark: rgba(34, 197, 94, 0.15) */
--color-success-text: #166534; /* dark: #86efac */
--color-danger-bg: #fee2e2;   /* dark: rgba(239, 68, 68, 0.15) */
--color-danger-text: #991b1b;  /* dark: #fca5a5 */
```

---

## Phase 2: Skip Link (+layout.svelte, +page.svelte)

CSS for `.skip-link` already exists at app.css lines 405-425. Just render the HTML.

**+layout.svelte** — Add before `<slot />` (line 32):
```html
<a href="#main-content" class="skip-link">Skip to main content</a>
```

**+page.svelte** — Add `id="main-content"` to `<main>` (line 345):
```html
<main id="main-content" class="app-main" ...>
```

---

## Phase 3: Icon-Only Button `aria-label` Sweep

| File | Element | Line | Add |
|------|---------|------|-----|
| +page.svelte | Focus Mode button | 375 | `aria-label={focusMode ? 'Exit focus mode' : 'Enter focus mode'}` |
| +page.svelte | Avatar/logout button | 406 | `aria-label={"Log out " + ($currentUser.full_name \|\| $currentUser.email)}` |
| SettingsModal | Show/hide OpenAI key | 224 | `aria-label={showOpenAIKey ? 'Hide OpenAI API key' : 'Show OpenAI API key'}` |
| SettingsModal | Delete OpenAI key | 252 | `aria-label="Remove OpenAI API key"` |
| SettingsModal | Show/hide Anthropic key | ~323 | `aria-label={showAnthropicKey ? 'Hide Anthropic API key' : 'Show Anthropic API key'}` |
| SettingsModal | Delete Anthropic key | ~351 | `aria-label="Remove Anthropic API key"` |
| ProjectSelector | Dropdown arrow (▾) | 181 | `aria-label="Switch project"` |
| ProjectSelector | File browser close (×) | 262 | `aria-label="Close file browser"` |
| CanvasStage | File browser close (×) | 197 | `aria-label="Close file browser"` |
| KeyboardShortcutsModal | Close button | 73 | Change `"Close"` → `"Close keyboard shortcuts"` |
| projects/+page | + sidebar button | 102 | `aria-label="Add new project"` |
| projects/+page | x delete button | 124 | `aria-label="Remove project {project.name}"` |
| projects/+page | + file button | 143 | `aria-label="Add new markdown file"` |

---

## Phase 4: `aria-expanded` on Dropdown (ProjectSelector)

**File:** `web/src/lib/components/ProjectSelector.svelte`

- Dropdown arrow button (line 181): Add `aria-expanded={showDropdown}`, `aria-haspopup="listbox"`
- Dropdown panel (line 217): Add `role="listbox"`, `aria-label="Projects"`
- Add `Escape` key handler to close dropdown:
```typescript
function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    if (showDropdown) { showDropdown = false; event.stopPropagation(); }
    else if (showFileBrowser) { showFileBrowser = false; event.stopPropagation(); }
    else if (showNewProjectModal) { showNewProjectModal = false; event.stopPropagation(); }
  }
}
```
- Add `<svelte:window on:keydown={handleKeydown} />` (alongside existing click handler)

---

## Phase 5: Loading/Error State Announcements

**+layout.svelte** — Loading container (line 34): Add `role="status"` and `aria-live="polite"`

**+page.svelte** — Loading stage (~line 424): Add `role="status"`, `aria-live="polite"`, and `aria-hidden="true"` on the spinner div

---

## Phase 6: Landmark Labels (projects/+page.svelte)

- Line 99: `<aside class="sidebar" aria-label="Project list">`
- Line 139: `<aside class="file-browser" aria-label="File browser">`
- Add visually-hidden page heading: `<h1 class="sr-only">Projects</h1>` at top of `.projects-page`

---

## Phase 7: Install `focus-trap`, Create Svelte Action

```bash
cd web && npm install focus-trap
```

**New file:** `web/src/lib/actions/focusTrap.ts`

A Svelte action wrapping the `focus-trap` library:
- Accepts `{ active: boolean }` parameter
- Creates trap on the node when `active=true`
- `escapeDeactivates: false` (modals handle Escape themselves)
- `allowOutsideClick: true` (backdrop click handled by modal)
- `returnFocusOnDeactivate: true` (auto-returns to trigger element)
- `fallbackFocus: node` (needs `tabindex="-1"` on dialog)
- Uses `requestAnimationFrame` before `activate()` to let Svelte finish rendering
- `destroy()` deactivates without returning focus (component is gone)

Works identically with both Svelte 4 and 5 (`use:` actions are unchanged).

---

## Phase 8: Focus Trap on Proper Modals (SettingsModal, KeyboardShortcutsModal)

Both already have `role="dialog"` + `aria-modal="true"` + `aria-labelledby`. Add:

1. `import { focusTrap } from '$lib/actions/focusTrap'`
2. `tabindex="-1"` on the dialog `<div>`
3. `use:focusTrap={{ active: isOpen }}` on the dialog `<div>`

---

## Phase 9: Upgrade Inline Modals

Five inline modals need `role="dialog"`, `aria-modal="true"`, `aria-labelledby`, `tabindex="-1"`, `use:focusTrap={{ active: true }}`, and Escape handling:

| File | Modal | Heading ID |
|------|-------|-----------|
| ProjectSelector | File browser (~line 259) | `file-browser-title` |
| ProjectSelector | New project (~line 325) | `new-project-title` |
| CanvasStage | File browser (~line 193) | `canvas-file-browser-title` |
| projects/+page | New project (~line 222) | `new-project-page-title` |
| projects/+page | New file (~line 265) | `new-file-title` |

Add `import { focusTrap }` and Escape key handlers to ProjectSelector, CanvasStage, and projects/+page.

---

## Phase 10: Fix Modal Backdrops

**SettingsModal** backdrop (line 141-148): Remove `role="button"`, `tabindex="0"`, `on:keypress`, `aria-label`. Replace with `role="presentation"`.

**KeyboardShortcutsModal** backdrop (line 59-66): Same — remove `role="button"`, `tabindex="-1"`, `onkeypress`, `aria-label`. Replace with `role="presentation"`.

---

## Phase 11: SettingsModal Tab Semantics

- Tab container (line 166): Add `role="tablist"`, `aria-label="Settings sections"`
- Each tab button: Add `role="tab"`, `aria-selected={activeTab === '...'}`, `aria-controls`, `id`
- Tab panels: Add `role="tabpanel"`, `id`, `aria-labelledby`
- Add arrow-key navigation between tabs (ArrowLeft/ArrowRight/Home/End)

---

## Phase 12: SettingsModal Dark Mode CSS

Migrate all hardcoded colors in the `<style>` block (~66 instances) to CSS variables. Key mappings:

| Hardcoded | Variable |
|-----------|----------|
| `white` | `var(--color-surface)` |
| `#1a202c` | `var(--color-text)` |
| `#4a5568` | `var(--color-text-secondary)` |
| `#718096` | `var(--color-text-muted)` |
| `#e2e8f0` | `var(--color-border)` |
| `#f7fafc` | `var(--color-bg-secondary)` |
| `#667eea` | `var(--color-primary)` |
| `#c6f6d5` | `var(--color-success-bg)` |
| `#fed7d7` | `var(--color-danger-bg)` |

Use Python replacement script per CLAUDE.md guideline 9 (tab-indented Svelte files).

---

## Tests

New test files (source-inspection pattern, matching existing conventions):

| File | Covers |
|------|--------|
| `web/src/lib/stores/__tests__/a11y-css.test.ts` | No `outline: none` on inputs, `--jrn-text-dim` value, skip link present |
| `web/src/lib/components/__tests__/a11y-labels.test.ts` | All icon-only buttons have `aria-label` |
| `web/src/lib/components/__tests__/a11y-modals.test.ts` | Dialog roles, aria-modal, tabindex, focusTrap, backdrop roles, tab semantics |
| `web/src/lib/actions/__tests__/focusTrap.test.ts` | Action lifecycle: activate, deactivate, update, destroy (mocked focus-trap) |

---

## Verification

After all phases:
1. `cd web && npx vitest run` — all tests pass
2. `npm run validate` — import validation passes
3. `npm run check` — TypeScript passes
4. Manual: Tab through entire app, verify focus rings always visible
5. Manual: Open/close every modal, verify focus traps and restores
6. Manual: Toggle dark mode, verify SettingsModal renders correctly
7. Manual: Use VoiceOver, verify all buttons announced with labels
