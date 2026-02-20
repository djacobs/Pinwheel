# Plan: Light/Dark Mode Toggle

## Context

Pinwheel's frontend is dark-mode-only. The user wants a simple light/dark toggle with **light as default**. Two team colors are problematic on light backgrounds: gold (#FFD700 — Alberta Monarchs) and light blue (#88BBDD — Sellwood Drift). The CSS is well-architected with custom properties in `:root`, making this a clean lift.

## Approach

### 1. CSS: Light mode variables (default) + dark mode scoped

**File:** `static/css/pinwheel.css`

- Current `:root` block becomes `[data-theme="dark"]`
- New `:root` block with light-mode palette:
  - Backgrounds: white/near-white (`#f8f8fa`, `#ffffff`, etc.)
  - Text: dark (`#1a1a2e`, `#555570`, `#888898`)
  - Accents: darkened versions of current accents for readability on white (e.g., governance cyan `#53d8fb` → `#0077aa`, score gold `#f0c040` → `#9a7000`)
  - Borders: light grays (`#d8d8e0`, `#c0c0d0`)
  - Shadows: reduced opacity
- Fix ~15 `rgba(255,255,255,...)` overlay values that become white-on-white in light mode — replace with CSS variables (`--overlay-subtle`, `--overlay-light`)

### 2. Anti-FOUC script in `<head>`

**File:** `templates/base.html`

Inline `<script>` before the CSS `<link>` tag that reads `localStorage.getItem('pinwheel-theme')` and sets `data-theme="dark"` on `<html>` if stored. No attribute = light mode (the default).

### 3. Toggle button in nav

**File:** `templates/base.html`

Small sun/moon button in the nav bar (after nav links, before the spacer). Pure HTML + CSS + minimal inline JS:
- Click toggles `data-theme` attribute on `<html>`
- Saves preference to `localStorage`
- Icon swaps between sun (light mode active) and moon (dark mode active)

### 4. Team colors: CSS custom properties on elements

**Files:** ~8 template files with team-colored inline styles

Replace `style="color: {{ team.color }}"` pattern with:
```html
<span class="tc" style="--tc: {{ color }}; --tcl: {{ color|light_safe }};">
```

CSS classes:
- `.tc` — `color: var(--tcl)` in light mode, `color: var(--tc)` in dark mode
- `.tc-bg` — same for backgrounds
- `.tc-border` — same for borders

### 5. Jinja2 `light_safe` filter

**File:** `src/pinwheel/api/pages.py` (where Jinja2 env is configured)

Simple filter that darkens high-luminance colors for light backgrounds:
- Computes relative luminance from hex
- If luminance > 0.5: darken by ~40% and boost saturation
- Otherwise: return as-is

This means most team colors pass through unchanged. Only gold (#FFD700 → ~#9A7B00) and light blue (#88BBDD → ~#4A7A9B) get adjusted.

### 6. Team secondary color (background tints) in light mode

Current `color_secondary` values are dark tints (e.g., `#1a1a2e`). In light mode, use CSS `color-mix()` to auto-compute light tints from the primary team color:

```css
[data-theme="dark"] .tc-bg { background: var(--tc2); }
:root .tc-bg { background: color-mix(in srgb, var(--tc) 10%, white); }
```

This eliminates the need for a separate `color_secondary_light` field.

## Files to modify

1. `static/css/pinwheel.css` — Light/dark variable scoping, team color classes, overlay variables
2. `templates/base.html` — Anti-FOUC script, toggle button
3. `src/pinwheel/api/pages.py` — Register `light_safe` Jinja2 filter
4. `templates/pages/game.html` — Team color CSS custom properties (~4 places)
5. `templates/pages/arena.html` — Team color CSS custom properties (~8 places)
6. `templates/pages/home.html` — Team color CSS custom properties (~5 places)
7. `templates/pages/standings.html` or `newspaper.html` — Team dots (~1 place)
8. `templates/pages/hooper.html` — Team dot + spider chart (~2 places)
9. `templates/pages/governor.html` — Team dot (~1 place)
10. `templates/pages/playoffs.html` — Bracket team colors (~5 places)
11. `templates/components/spider_chart.html` — Chart colors (~3 places)

## No changes needed

- No model/DB changes
- No API changes
- No new dependencies

## Verification

1. `uv run pytest -x -q` — all existing tests pass (CSS/template changes shouldn't break logic tests)
2. Start dev server, verify light mode is default
3. Toggle to dark — verify it matches the current look
4. Refresh — verify localStorage persists the choice
5. Check Alberta Monarchs (gold) and Sellwood Drift (light blue) are readable in light mode
6. Check game detail page team panels render correctly in both modes
7. `uv run ruff check src/ tests/` — lint clean
