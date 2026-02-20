# Persist Council Feedback & Smart Insertion Points

## Context

Council feedback (advisor suggestions) is lost when the user switches documents or restarts the app. The user must re-request feedback every time, wasting time and LLM tokens. Additionally, "add" type suggestions (e.g., "add a topic sentence") have no opinion about where the new content should go.

**Two changes:**
1. **Cache feedback** — reload persisted `ReviewComment` objects from SwiftData when reopening a document, so feedback survives across sessions. When the document has been edited since feedback was given, detect staleness and offer LLM-powered re-mapping of suggestion positions.
2. **Insertion points for "add" suggestions** — modify the council prompt to request an `insert_after` field so the LLM specifies exactly where new content belongs. Parse it to compute a position, highlight it, and insert there on accept.

## Key Insight

`ReviewComment` is already a SwiftData `@Model` linked to `JourneyState.comments`. Comments **are persisted** — the only missing piece is reloading them into `CouncilViewModel.advisorResults` on document open.

## Changes

### 1. Reload persisted feedback on document open

**`CouncilViewModel.swift`** — Add `loadPersistedFeedback()`:
- Read `document.journeyState.pendingComments`
- Group by `advisorId`, reconstruct `[CouncilAdvisorResult]`
- Set `showPanel = true` if there are results

**`ContentView.swift`** — Call `councilVM?.loadPersistedFeedback()` after creating the view model in `onChange(of: selectedDocument?.id)` and `onAppear`.

### 2. Detect staleness via content hash

**`ReviewComment` (JourneyState.swift)** — Add field:
```swift
public var contentHashAtReview: String = ""
```

**`CouncilViewModel.swift`**:
- Add `computeContentHash(_:)` using `CryptoKit.SHA256` (truncated to 16 hex chars)
- Store hash on each comment in `requestFeedback()` after parsing
- Add computed `feedbackRangesAreStale: Bool` — compares stored hash vs current content hash

### 3. LLM-powered range re-mapping

**`CouncilEngine.swift`** — Add two new methods:
- `buildRemapRequest(comments:documentText:)` — sends current document + list of original text snippets to LLM with `temperature: 0.0`. Asks LLM to find each snippet and return updated `rangeStart`/`rangeEnd`.
- `parseRemapResponse(_:documentText:)` — validates returned ranges against actual document text.

**`CouncilViewModel.swift`** — Add `remapStaleRanges(using:)`:
- Calls `buildRemapRequest` → LLM → `parseRemapResponse`
- Updates `rangeStart`/`rangeEnd` on each comment
- Updates `contentHashAtReview` to current hash
- Reloads `advisorResults` via `loadPersistedFeedback()`

### 4. Insertion points for "add" suggestions

**`ReviewComment` (JourneyState.swift)** — Add field:
```swift
public var insertAfterText: String = ""
```

**`CouncilEngine.swift`**:
- Update `buildSystemPrompt()` — add `insert_after` field to the JSON schema: "the exact sentence from the draft immediately before where the new content should be inserted"
- Update `parseResponse()` — when `type == "add"` and `insert_after` is present, find that text in the document and set `rangeStart`/`rangeEnd` to just after it. Store `insertAfterText` on the comment.

**`CouncilViewModel.swift`**:
- Update `acceptComment()` — when `originalText` is empty and `type == .add`, insert `suggestedText` at `rangeStart` (the computed insertion point). Fall back to `insertAfterText` string search if range is stale.
- Same for `applyEditedComment()`.
- Update `buildHighlights()` — for "add" comments with a valid insertion point, highlight the `insertAfterText` range to show where the addition goes.

### 5. Staleness UI

**`DocumentInspector.swift`** — Add a banner above feedback results when `feedbackRangesAreStale`:
```
⟳ Suggestions may not match your latest edits.  [Refresh]
```
"Refresh" button calls `remapStaleRanges(using:)`.

## Files to modify

| File | Change |
|------|--------|
| `Sources/Core/Models/JourneyState.swift` | Add `contentHashAtReview` and `insertAfterText` fields to `ReviewComment` |
| `Sources/Core/Engine/CouncilEngine.swift` | Add `buildRemapRequest()`/`parseRemapResponse()`, update `buildSystemPrompt()` for `insert_after`, update `parseResponse()` |
| `Sources/Engine/CouncilViewModel.swift` | Add `loadPersistedFeedback()`, `remapStaleRanges()`, `feedbackRangesAreStale`, `computeContentHash()`, update `acceptComment`/`applyEditedComment` for "add" insertion |
| `Sources/Views/DocumentInspector.swift` | Add staleness banner with Refresh button |
| `Sources/Views/ContentView.swift` | Call `loadPersistedFeedback()` on document switch/appear |
| `Tests/TKEditorTests/FeedbackCacheTests.swift` | NEW — ~10 tests for reload, grouping, staleness detection |
| `Tests/TKEditorTests/RangeRemapTests.swift` | NEW — ~8 tests for remap request/response parsing |
| `Tests/TKEditorTests/AddInsertionPointTests.swift` | NEW — ~8 tests for insert_after parsing, acceptance, highlighting |

## Sequencing

1. Add `contentHashAtReview` and `insertAfterText` to `ReviewComment` (model change, no behavior change)
2. Write all test files (tests-first per CLAUDE.md)
3. Implement `loadPersistedFeedback()` + wire in ContentView
4. Implement content hash storage + staleness detection
5. Implement remap request/response in CouncilEngine + `remapStaleRanges()` in ViewModel
6. Update council prompt for `insert_after` + parse + accept logic
7. Wire staleness UI in DocumentInspector

## Verification

1. `swift test` — all tests pass (including ~26 new tests)
2. `./run.sh` — launch app:
   - Open a document that previously had feedback → suggestions appear immediately (cached)
   - Edit the document → staleness banner appears
   - Click Refresh → ranges update, highlights move to correct positions
   - Request new feedback with "add" suggestion → highlight appears at insertion point
   - Accept an "add" suggestion → text inserted at the correct location
