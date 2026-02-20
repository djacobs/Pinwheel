# Fix Click-to-Scroll & Implement Gutter Circles

## Context

Two UX features needed for council feedback to feel complete:

1. **Click-to-scroll is broken** — clicking a suggestion card in the inspector should scroll the editor to the relevant passage and highlight it. The wiring exists but `NSViewRepresentable.updateNSView` is never called when `focusedCommentId` changes, so the scroll never fires.

2. **Gutter circles are missing** — numbered, color-coded circles should appear in the editor's right margin at the Y position of each suggestion, giving the writer a spatial map of where feedback lives.

## Root Cause: Click-to-Scroll

The chain is:
1. `SuggestionCard.onFocus` → `councilVM.focusedCommentId = commentId` ✓
2. `EditorView.onChange(of: councilVM?.focusedCommentId)` → `editorState.focusedCommentId = newId` ✓
3. `MarkdownTextView(editorState: editorState)` — `editorState` is a reference type (`@Observable class`). Changing a property on the object doesn't change the struct's identity. **SwiftUI doesn't detect a structural change → `updateNSView` is never called** ✗
4. `applyCouncilHighlights(to:)` (which contains `scrollRangeToVisible`) is never re-invoked ✗

The same problem affects `councilHighlights` — new highlights also don't trigger `updateNSView` unless something else causes a re-render (like text changing).

## Changes

### 1. Fix click-to-scroll by passing trigger values as struct properties

**`Sources/Editor/MarkdownTextView.swift`** — Add value-type properties that SwiftUI can structurally compare:

```swift
struct MarkdownTextView: NSViewRepresentable {
    @Binding var text: String
    var theme: MarkdownTheme = .default
    var editorState: EditorState?
    var councilHighlights: [CouncilHighlight] = []   // NEW
    var focusedCommentId: UUID? = nil                  // NEW
```

In `updateNSView`, pass these to the coordinator:
```swift
context.coordinator.applyCouncilHighlights(
    to: textView,
    highlights: councilHighlights,
    focusedId: focusedCommentId
)
```

Update `applyCouncilHighlights` to take parameters instead of reading from `editorState`:
```swift
func applyCouncilHighlights(to textView: NSTextView,
                             highlights: [CouncilHighlight],
                             focusedId: UUID?)
```

Also need to **clear previous highlight attributes** before applying new ones, so unfocusing a card removes the background/underline.

**`Sources/Views/EditorView.swift`** — Pass the new properties:
```swift
MarkdownTextView(
    text: $document.content,
    theme: theme,
    editorState: editorState,
    councilHighlights: editorState.councilHighlights,
    focusedCommentId: editorState.focusedCommentId
)
```

Same changes for the iOS (`UIViewRepresentable`) path.

### 2. Implement gutter circles

**`Sources/Editor/CouncilGutterView.swift`** — NEW file, `NSView` subclass:

- Added as a subview of the `NSTextView` in `makeNSView`
- Constrained to the right edge, full height of the text view
- Width: ~28pt (enough for a numbered circle)
- For each highlight, computes the Y position via `NSLayoutManager`:
  ```swift
  let glyphRange = layoutManager.glyphRange(forCharacterRange: range, actualCharacterRange: nil)
  let lineRect = layoutManager.boundingRect(forGlyphRange: glyphRange, in: textContainer)
  ```
- Draws a filled circle at `(center, lineRect.midY)` with the advisor's color
- Draws the suggestion number (1-based index) in white, centered in the circle
- Clicking a circle calls a `onCircleTapped: (UUID) -> Void` callback that sets `focusedCommentId`

**`Sources/Editor/MarkdownTextView.swift`** — Wire the gutter:
- In `makeNSView`: create `CouncilGutterView`, add as subview, store reference on coordinator
- Increase right `textContainerInset` to `NSSize(width: 20, height: 20)` → add 28pt to right for gutter space. Or use `textContainer.exclusionPaths` to keep text out of the gutter area.
- In `updateNSView`: pass highlights to the gutter view, call `setNeedsDisplay()` to redraw
- On gutter circle tap: set `editorState?.focusedCommentId = commentId`

For iOS: create `CouncilGutterUIView: UIView` with equivalent logic using `UITextView.layoutManager`.

### 3. Attribute cleanup pass

Currently `applyCouncilHighlights` only adds attributes — it never removes them. When the writer unfocuses a card or highlights change, stale backgrounds and underlines remain.

**Fix**: Before applying new highlight attributes, strip council highlight attributes from the entire text. Add a custom attribute key (e.g., `.councilHighlight`) to mark which attributes are council-related, and remove all ranges with that key before reapplying.

Alternatively, keep a `lastAppliedHighlights` set on the coordinator and only update changed ranges.

## Files to modify

| File | Change |
|------|--------|
| `Sources/Editor/MarkdownTextView.swift` | Add `councilHighlights` + `focusedCommentId` properties, wire gutter, attribute cleanup |
| `Sources/Editor/CouncilGutterView.swift` | **NEW** — NSView drawing numbered circles at highlight Y positions |
| `Sources/Views/EditorView.swift` | Pass `councilHighlights` and `focusedCommentId` to MarkdownTextView |
| `Tests/TKEditorTests/GutterTests.swift` | **NEW** — tests for gutter position computation, numbering |
| `Tests/TKEditorTests/ClickToScrollTests.swift` | **NEW** — tests for highlight attribute application and cleanup |

## Sequencing

1. Write tests first (per CLAUDE.md)
2. Add `councilHighlights` + `focusedCommentId` as struct properties on `MarkdownTextView` (both platforms)
3. Update `EditorView` to pass them
4. Refactor `applyCouncilHighlights` to use parameters + add attribute cleanup
5. Create `CouncilGutterView` (macOS) with Y-position computation and drawing
6. Wire gutter into `MarkdownTextView.makeNSView` / `updateNSView`
7. Create iOS equivalent (`CouncilGutterUIView`)

## Verification

1. `swift test` — all tests pass
2. `./run.sh` — launch app:
   - Request feedback on a document → highlights appear in editor with underlines
   - Click a suggestion card → editor scrolls to the passage, background highlights
   - Click away → previous highlight clears
   - Numbered colored circles appear in right gutter at correct line positions
   - Click a gutter circle → focuses that suggestion in the inspector
   - Resize window → gutter repositions correctly
