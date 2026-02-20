# Plan: Three Features — Inline Highlighting, Apple Foundation Models, Edit Suggestions

## Context

Three gaps identified from comparing the native app to the web version:

1. **No inline highlighting.** The web version highlights the specific text each suggestion refers to, with a colored border. The native app shows feedback only in the inspector panel — the writer has to mentally match "Original" text to their document. This breaks flow.

2. **No zero-config AI.** When no API keys are set, the app can't do anything useful. Apple ships on-device Foundation Models (macOS 26 / iOS 26) that are free, private, and require zero configuration. The existing `AppleIntelligenceProvider` is a placeholder.

3. **No edit-before-accept.** The "Edit" button on suggestion cards just marks the status as `.edited` — there's no actual UI for the writer to modify a suggestion before applying it. Writers need to own their revisions.

---

## Phase A: Edit Suggestions (simplest, fewest dependencies)

### A1. Tests — `Tests/TKEditorTests/EditSuggestionTests.swift` (NEW)

~7 tests:
- `applyEditedComment` replaces original text with writer's edited version
- Uses stored `rangeStart`/`rangeEnd` for precise targeting (duplicate text)
- Falls back to first-match when range is stale
- Sets status to `.edited`
- Increments `totalSuggestionsAccepted`
- Handles empty `originalText` (type `.add`) gracefully
- Handles original text not found in document

### A2. `Sources/Engine/CouncilViewModel.swift` — Add `applyEditedComment(_:withText:in:)`

New method identical to `acceptComment(_:in:)` (line 216) but takes a `withText: String` parameter instead of using `comment.suggestedText`. Same range-aware replacement logic. Calls `comment.edit()` instead of `comment.accept()`.

### A3. `Sources/Views/CouncilPanel.swift` — Inline edit UI in `SuggestionCard`

- Add `@State private var isEditing = false` and `@State private var editingText = ""`
- Add `var onApplyEdit: ((String) -> Void)?` callback
- When writer taps "Edit": set `editingText = comment.suggestedText`, show TextEditor (green-tinted background) with "Apply" + "Cancel" buttons
- "Apply" calls `onApplyEdit?(editingText)`
- Three buttons when not editing: Accept | Skip | Edit

### A4. `Sources/Views/DocumentInspector.swift` — Wire `onApplyEdit` in `advisorResultSection`

Pass `onApplyEdit` callback that calls `vm.applyEditedComment(comment, withText: editedText, in: modelContext)`.

---

## Phase B: Apple Foundation Models (independent, LLM layer only)

### B1. Tests — `Tests/TKEditorTests/AppleIntelligenceTests.swift` (NEW)

~6 tests:
- Provider properties: `id == "apple"`, `name == "Apple Intelligence"`
- `estimateCost` always returns 0
- `defaultModels` has one entry with zero cost
- ProviderManager fallback ordering prefers Apple over arbitrary first provider
- Health check reflects availability

### B2. `Sources/Core/LLM/AppleIntelligenceProvider.swift` — Full implementation

Reference: `/tmp/FoundationModelsDemo/FoundationModelsDemo/ContentView.swift`

```swift
#if canImport(FoundationModels)
import FoundationModels
#endif
```

Changes:
- `isFullyImplemented`: `true` when `canImport(FoundationModels)`, `false` otherwise
- `isAvailable`: check `macOS 26.0` / `iOS 26.0` + `arch(arm64)` on macOS
- `_generateWithSession()`: call `LanguageModelSession().respond(to:)` combining `System: \(request.system)\n\nUser: \(request.user)`. Return `response.content` as text, estimate tokens from word count * 1.3

### B3. `Sources/Core/LLM/ProviderManager.swift` — Fallback ordering

In `refreshProviders()` (line 78), when preferred provider not found, prefer `"apple"` then `"ollama"` instead of arbitrary `providers.values.first`.

---

## Phase C: Inline Text Highlighting (most complex, builds on A)

### C1. Tests — `Tests/TKEditorTests/InlineHighlightTests.swift` (NEW)

~8 tests:
- `buildHighlights()` creates highlights from pending comments with valid ranges
- Only pending comments produce highlights (accepted/skipped/edited excluded)
- Zero-range comments (rangeStart == 0 && rangeEnd == 0) excluded
- `focusedCommentId` propagation from CouncilViewModel to EditorState
- Highlights cleared on `dismissPanel()`
- Highlight removed when comment accepted/skipped

### C2. `Sources/Editor/CouncilHighlight.swift` (NEW)

```swift
struct CouncilHighlight: Identifiable, Equatable {
    let id: UUID
    let commentId: UUID
    let range: NSRange
    let color: PlatformColor  // From advisor's role color
    let advisorId: String
}
```

Where `PlatformColor` is `NSColor` on macOS, `UIColor` on iOS (already used in MarkdownTheme).

### C3. `Sources/Editor/EditorActions.swift` — Extend `EditorState`

Add to `EditorState` (line 44):
- `var councilHighlights: [CouncilHighlight] = []`
- `var focusedCommentId: UUID? = nil`
- `var highlightsAreStale: Bool = false`

### C4. `Sources/Engine/CouncilViewModel.swift` — Highlight computation + focus

- Add `var focusedCommentId: UUID?` property
- Add `func buildHighlights() -> [CouncilHighlight]` — iterates `advisorResults`, creates highlight for each pending comment with `rangeEnd > rangeStart`
- Clear `focusedCommentId` in `acceptComment`, `skipComment`, `editComment`, `applyEditedComment`, `dismissPanel`

### C5. `Sources/Editor/MarkdownTextView.swift` — Render highlights

Add `applyCouncilHighlights(to:)` in both macOS and iOS Coordinators. Called after `applyHighlighting(to:)`.

For each highlight in `parent.editorState?.councilHighlights`:
- **Focused** (matches `focusedCommentId`): `.backgroundColor` at 25% opacity + `.underlineStyle: .thick`
- **Unfocused**: `.underlineStyle: .single` + `.underlineColor` at 40% opacity
- Scroll to focused range via `scrollRangeToVisible(_:)`

Also update `updateNSView`/`updateUIView` to reapply council highlights when state changes.

### C6. `Sources/Views/EditorView.swift` — Wire highlights

- `onChange(of: councilVM?.advisorResults.count)` → rebuild highlights via `editorState.councilHighlights = vm.buildHighlights()`
- `onChange(of: councilVM?.focusedCommentId)` → sync to `editorState.focusedCommentId`
- `onChange(of: document.content)` → mark `editorState.highlightsAreStale = true`

### C7. `Sources/Views/CouncilPanel.swift` + `DocumentInspector.swift` — Focus on tap

Add `onFocus: ((UUID) -> Void)?` to `SuggestionCard`. Tap gesture on the card body calls `onFocus?(comment.id)`.

In `CouncilPanel.advisorSection` and `DocumentInspector.advisorResultSection`, wire:
```swift
onFocus: { commentId in councilVM.focusedCommentId = commentId }
```

---

## Files Summary

### New (4 source + 3 test)
| File | Purpose |
|------|---------|
| `Sources/Editor/CouncilHighlight.swift` | Highlight data type |
| `Tests/TKEditorTests/EditSuggestionTests.swift` | Edit-before-accept tests |
| `Tests/TKEditorTests/AppleIntelligenceTests.swift` | Foundation Models tests |
| `Tests/TKEditorTests/InlineHighlightTests.swift` | Inline highlight tests |

### Modified (7)
| File | Changes |
|------|---------|
| `Sources/Engine/CouncilViewModel.swift` | `applyEditedComment`, `buildHighlights`, `focusedCommentId` |
| `Sources/Views/CouncilPanel.swift` | SuggestionCard: inline edit UI, `onApplyEdit`, `onFocus` |
| `Sources/Views/DocumentInspector.swift` | Wire `onApplyEdit` and `onFocus` |
| `Sources/Editor/EditorActions.swift` | `councilHighlights`, `focusedCommentId`, `highlightsAreStale` on EditorState |
| `Sources/Editor/MarkdownTextView.swift` | `applyCouncilHighlights(to:)` in both platform Coordinators |
| `Sources/Views/EditorView.swift` | Observe councilVM, sync highlights + focus |
| `Sources/Core/LLM/AppleIntelligenceProvider.swift` | Full FoundationModels implementation |
| `Sources/Core/LLM/ProviderManager.swift` | Fallback ordering prefers Apple |

---

## Verification

1. `swift build` — clean
2. `swift test` — all pass (existing + ~21 new)
3. `./run.sh` — app launches
4. Edit a suggestion before accepting → writer's text replaces the original
5. Request feedback → editor highlights referenced text with colored underlines
6. Click a suggestion card → editor scrolls to that text, highlight intensifies
7. Accept/skip a suggestion → its highlight disappears
8. On macOS 26 with no API keys → Apple Intelligence provider is available and works
