# TK Native: Universal Apple Writing App

## Context

TK has a working web client (SvelteKit), Python API server (FastAPI), and a 25%-complete Swift macOS app. The goal is to build a standalone, Apple-quality universal app (macOS + iOS/iPadOS) that covers the full writer lifecycle — from blank page to polished export — without requiring the Python server. The app calls LLM APIs directly, embeds all role/prompt/story-type logic, syncs via iCloud, and supports local AI (Ollama + Apple Intelligence). It should feel like Apple built it.

The existing Swift code (`editor/TKEditor/`) has ~4,100 lines across 14 source files. Most of it (models, API client, views) was built around a server-dependent architecture and will be substantially rewritten. The models and some view patterns are salvageable.

---

## Architecture

```
TKEditor/
├── Sources/
│   ├── TKCore/                    # Shared library (App + CLI both depend on this)
│   │   ├── Models/                # SwiftData models + Codable DTOs
│   │   ├── Engine/                # AI engines (suggestions, council, prompts)
│   │   ├── LLM/                   # Provider protocol + implementations
│   │   ├── Resources/             # Embedded data (roles, story types, prompts)
│   │   └── Services/              # Keychain, config, text analysis
│   ├── TKEditor/                  # GUI app target (macOS + iOS)
│   │   ├── App/                   # Entry point, app lifecycle
│   │   ├── Editor/                # TextKit 2 live-markdown editor
│   │   ├── Journey/               # Canvas → Draft → Revise → Final
│   │   ├── Views/                 # Shared SwiftUI views
│   │   └── Platform/              # macOS/iOS-specific code
│   └── TKCLI/                     # CLI executable target (macOS only)
│       ├── Commands/              # suggest, council, ask, pipe, export, roles, story-types, config
│       ├── Formatters/            # Text, JSON, NDJSON output formatting
│       └── CLI.swift              # @main entry point
└── Tests/
    ├── TKCoreTests/               # Shared logic tests
    ├── TKEditorTests/             # GUI-specific tests
    └── TKCLITests/                # CLI command tests
```

**Key architectural decisions:**

| Decision | Choice | Rationale |
|----------|--------|-----------|
| UI framework | SwiftUI (multiplatform) | Single codebase, Apple's direction |
| Persistence | SwiftData + CloudKit | Native iCloud sync, zero config |
| Editor | TextKit 2 (NSTextView/UITextView) bridged to SwiftUI | Only way to get live markdown with Apple-quality text handling |
| LLM calls | Direct HTTP via URLSession | No Python server needed |
| Secrets | Keychain Services | Apple-standard secure storage |
| AI providers | Anthropic, OpenAI, Ollama, Apple Intelligence | Full coverage: cloud + local |
| Markdown parsing | swift-markdown (Apple's) + custom AttributedString renderer | Native, maintained by Apple |
| Package manager | SPM (within Xcode project) | Needed for entitlements, CloudKit, App Store |
| CLI framework | swift-argument-parser (Apple's) | Subcommand routing, typed options, auto-generated help |
| Target split | TKCore (library) + TKEditor (app) + TKCLI (tool) | CLI and app share all engine/LLM/prompt code |

---

## Feature-by-Feature: Local vs API Tradeoffs

### Features rebuilt locally (no server needed)

| Feature | API Complexity | Local Implementation | Recommendation |
|---------|---------------|---------------------|----------------|
| **Role definitions** (11 built-in) | GET `/roles` | Embed as JSON bundle. ~300 lines of Swift. | **Local.** Static data, no reason to fetch. |
| **Story types** (9 types) | GET `/story-types` | Embed as JSON bundle. ~150 lines. | **Local.** Static data. |
| **Prompt construction** (VOICE→CONSTRAINTS→STRUCTURE→FOCUS→OUTPUT) | Built into server suggestion engine | Port `build_prompt()` — 50 lines of Swift string interpolation. | **Local.** Trivial to port, and keeps prompts versioned with the app. |
| **Custom role creation** | POST/DELETE `/roles` | SwiftData model + validation. ~200 lines. Sanitization regex from Python. | **Local.** No server round-trip for something that's just local data. |
| **Document CRUD** | POST/GET/PUT/DELETE `/documents` | SwiftData `@Model` with content, title, timestamps. | **Local.** Documents are the app's core data — must be local-first. |
| **Project management** | POST/GET/DELETE `/projects`, GET files | FileManager + security-scoped bookmarks (macOS), UIDocumentPickerViewController (iOS). | **Local.** Native file APIs are superior to HTTP file ops. |
| **Journey state** (Canvas→Draft→Revise→Final) | GET/PUT/DELETE `/projects/{id}/journey/{path}` | SwiftData model linked to Document. State machine with transitions. ~400 lines. | **Local.** Journey state is per-document metadata — belongs in the data model. |
| **Settings & preferences** | N/A (localStorage in web) | `@AppStorage` + Keychain for secrets. | **Local.** Native Apple settings patterns. |
| **Theme (light/dark)** | N/A | System automatic + `preferredColorScheme` override. | **Local.** 5 lines of SwiftUI. |
| **Export** (markdown, PDF, DOCX) | POST `/documents/{id}/export` | Native: `NSAttributedString` → PDF, markdown serializer, UIDOCX via `UIDocumentInteractionController`. | **Local.** Native export is better than server-generated. |
| **Spell check** | N/A | `NSSpellChecker` (macOS) / `UITextChecker` (iOS) — built into the OS. | **Local.** Free from the OS. |
| **Bookmark import** | POST `/bookmarks/sync` | `NSSharingService` or direct SQLite read of Safari bookmarks on macOS. Not available on iOS. | **Local (macOS only).** Direct access is faster and doesn't need a server. |
| **Folder watching** | POST `/context/folders` | `DispatchSource.makeFileSystemObjectSource` (macOS), `NSMetadataQuery` for iCloud. | **Local.** Native APIs are more reliable than polling an HTTP endpoint. |

### Features that call LLM directly (server eliminated)

| Feature | Current Flow | New Flow | Complexity |
|---------|-------------|----------|------------|
| **Inline suggestions** | Web → API → LLM | App → LLM API directly | Port prompt construction + JSON response parsing. ~600 lines. Retry logic with exponential backoff. |
| **Batch suggestions** (multi-advisor) | Web → API → single LLM call | App → LLM API directly | Port multi-advisor prompt template + response parser. ~400 lines. Includes fallback to sequential calls. |
| **Quick suggestions** | Web → API → template-based | App → local heuristics | Port text analysis heuristics (passive voice, weak words, sentence length). ~200 lines. No LLM needed. |
| **Council debate** | Web → API → parallel LLM calls | App → parallel async LLM calls via TaskGroup | Port `CouncilOrchestrator`: parallel feedback → consensus identification → disagreement extraction → unique insights. ~500 lines. Most complex single feature. |
| **Role Q&A** | Web → API → LLM with role context | App → LLM API with embedded role prompt | Build conversation history, inject role system prompt. ~150 lines. |
| **Team assembly from description** | Web → API → LLM | App → LLM API | Port assembler prompt. ~100 lines. |
| **Apply suggestion** (smart merge) | Web → API → LLM | App → LLM API | Port intelligent suggestion application when anchor text has drifted. ~100 lines. |
| **Intent detection** | Web → API → LLM + heuristics | App → local heuristics first, LLM fallback | Port keyword matching. LLM only for ambiguous cases. ~200 lines. |

### Features that require meaningful trade-offs

| Feature | Tradeoff | Recommendation |
|---------|----------|----------------|
| **Full-text search** | Server has SQLite FTS + vector embeddings. Rebuilding locally: SQLite FTS via GRDB.swift (~300 lines), but no semantic search without embedding model. | **Local FTS only.** Ship SQLite FTS for fast keyword search. Skip semantic search initially — it requires running an embedding model which is heavy for mobile. Add later via Apple's NaturalLanguage framework or on-device embeddings. |
| **Content indexing** | Server watches folders and generates embeddings. Locally: use Spotlight (`CSSearchableItem`) + SQLite FTS. No embeddings. | **Local Spotlight + FTS.** Index project files into Spotlight for system-wide search, plus local FTS for in-app search. Skip embeddings. |
| **Token counting** | Server uses tiktoken (Python). Swift has no official tokenizer. | **Approximate.** Use `words * 1.3` heuristic for token estimation. Good enough for cost display. Or ship a BPE tokenizer (~500 lines of Swift). |
| **Cost tracking** | Server tracks exact token usage from API responses. | **Extract from API responses.** Both Anthropic and OpenAI return `usage` in response headers/body. Parse and accumulate locally. |

---

## The Writer's Lifecycle (Complete Journey)

### Stage 0: Launch & Project Selection

**What the writer sees:** A clean window. Left sidebar shows projects (folders on disk). Right area shows recent documents or a "New Document" prompt. No clutter.

**Implementation:**
- `ProjectBrowser` view with `FileManager` integration
- macOS: `NSOpenPanel` for folder selection, security-scoped bookmarks for persistent access
- iOS: `UIDocumentPickerViewController` + Files app integration
- SwiftData `Project` model: id, name, folderURL, bookmark data, last opened
- Recent documents list from SwiftData query sorted by `lastModifiedAt`

### Stage 1: Canvas — "What are you writing?"

**What the writer sees:** A story type grid (9 cards with icons and descriptions). Below it, a free-text field: "Or describe what you're writing..." with a team assembly button. At the bottom: "Just start writing" to skip.

**Implementation:**
- `CanvasView` with embedded `STORY_TYPES` data
- Story type selection auto-configures advisor team (mapped by `default_advisor_ids`)
- Free-text team assembly calls LLM to recommend advisors based on description
- Selection persisted to `JourneyState` SwiftData model
- Transition: selecting a type or clicking "Just start writing" advances to Draft

**Embedded story types (from Python `definitions.py`):**

| Type | Default Advisors | Genre Context |
|------|-----------------|---------------|
| Blog (Personal) | Creative Writer, Editor | Authentic voice, narrative flow, emotional resonance |
| Blog (Professional) | Technical Writer, UX Writer, Editor | Credibility, argumentation, scannability |
| BRD / PRD | Product Executive, Business Analyst, Recent Graduate | Requirements completeness, acceptance criteria, gap identification |
| Creative Fiction | Creative Writer, Editor | Show-don't-tell, sensory detail, character voice, pacing |
| Biography | Creative Writer, Academic Researcher, Editor | Narrative arc, factual accuracy, chronological clarity |
| Academic Paper | Academic Researcher, Editor, Data Scientist | Argument structure, evidence, methodology, hedging |
| Marketing Copy | Copywriter, UX Writer, Editor | Headline impact, benefits, CTAs, social proof |
| Technical Docs | Technical Writer, Editor, Recent Graduate | Accuracy, completeness, scannability, code examples |
| Email / Memo | UX Writer, Editor | Clarity, tone, action items, brevity |

### Stage 2: Draft — Write

**What the writer sees:** A full-screen live-markdown editor. Formatting toolbar at top. Word/character count in footer. Quick suggestions appear as a subtle bar below the editor. The advisor team is visible as small avatars in the toolbar — tap one to ask a question.

**The editor** is the hardest piece of this project. It must:
- Render markdown inline as you type (headings grow, bold appears bold, links become tappable)
- Support: headings (H1-H6), bold, italic, strikethrough, links, lists (ordered/unordered), blockquotes, code (inline + fenced blocks), tables, horizontal rules
- Handle undo/redo natively
- Support system spell check and autocorrect
- Autosave every 3 seconds (debounced) to SwiftData
- Track cursor position and selected text for AI context
- Support iOS keyboard toolbar with formatting shortcuts

**Implementation:**
- `MarkdownTextView`: NSViewRepresentable (macOS) / UIViewRepresentable (iOS) wrapping NSTextView/UITextView with TextKit 2
- `MarkdownParser`: Uses Apple's `swift-markdown` to parse the document, then applies `NSAttributedString` attributes for live rendering
- `MarkdownTheme`: Defines fonts, colors, spacing for each element (headings, code, links, etc.)
- `MarkdownSerializer`: Converts attributed text back to markdown on save
- `EditorToolbar`: SwiftUI toolbar with formatting buttons that insert markdown syntax
- `QuickSuggestionsBar`: Debounced (2 sec) heuristic-based suggestions (no LLM call — uses ported `_generate_fallback_suggestions` logic)

**Draft stage selector:** Early / Middle / Late — affects the feedback style when requesting review.

### Stage 3: Revise — Get Feedback

**What the writer sees:** The editor shifts left. A suggestions panel appears on the right (macOS) or as a sheet (iOS). Inline highlights appear on suggested text. Comments are numbered in the margin. Navigation arrows move between suggestions.

**How feedback works:**
1. Writer clicks "Request Feedback" (or keyboard shortcut)
2. App builds prompt: role system prompt + CONSTRAINTS + STRUCTURE + FOCUS + OUTPUT + genre context + document text + selected text
3. Sends to LLM API (Anthropic/OpenAI/Ollama) directly
4. Parses JSON response into `InlineSuggestion` objects with `range_start`, `range_end`, `original_text`, `suggested_text`, `type`, `reasoning`, `priority`
5. Highlights are rendered in the editor using TextKit 2 annotation attributes
6. Writer addresses each: Accept (applies suggestion), Edit (opens inline editor), Skip (marks as skipped)
7. When all comments are addressed, option to transition to Final or request another round

**Multi-advisor batch flow:**
- Single LLM call with all selected advisors' perspectives
- Response parsed per-advisor with `advisor_id` attribution
- If batch fails, falls back to sequential per-advisor calls
- Advisor discussion threads generated for overlapping suggestions

**Council debate flow:**
- Writer selects 2-10 advisors + enters focus question
- `CouncilEngine` fires parallel `TaskGroup` LLM calls (one per advisor)
- Results synthesized locally: consensus (>50% word overlap grouping), disagreements (conflicting sentiments on same topic), unique insights (mentioned by only one advisor)
- Displayed in two tabs: "Opinions" (per-advisor cards with sentiment) and "Analysis" (consensus, disagreements, insights)

### Stage 4: Final — Polish & Export

**What the writer sees:** Clean editor view. Stats bar shows: total suggestions received, accepted, skipped, revision cycles completed. Export options: Markdown, PDF, Plain Text. Share sheet for sending.

**Implementation:**
- `FinalView` with export actions
- PDF generation: `NSAttributedString` → `NSPrintOperation` (macOS) / `UIMarkupTextPrintFormatter` (iOS)
- Markdown export: serialize from SwiftData
- Share sheet: `NSSharingServicePicker` (macOS) / `UIActivityViewController` (iOS)
- "Request more feedback" button returns to Revise stage (increments revision cycle counter)
- Save to project folder option

---

## LLM Provider Architecture

```swift
protocol LLMProvider: Sendable {
    var name: String { get }
    var modelId: String { get }
    func generate(system: String, user: String, temperature: Double, maxTokens: Int) async throws -> LLMResponse
    func estimateCost(inputTokens: Int, outputTokens: Int) -> Double
}

struct LLMResponse {
    let text: String
    let inputTokens: Int
    let outputTokens: Int
    let model: String
}
```

### Providers

**AnthropicProvider** (~200 lines)
- Direct HTTPS to `api.anthropic.com/v1/messages`
- Models: Claude Sonnet 4.6, Claude Opus 4, Claude Haiku 4.5
- Supports prompt caching (`cache_control: ephemeral` on system prompt)
- API key from Keychain
- Streaming support via SSE for real-time feedback display

**OpenAIProvider** (~200 lines)
- Direct HTTPS to `api.openai.com/v1/chat/completions`
- Models: GPT-4o, GPT-4o Mini, GPT-4 Turbo
- API key from Keychain
- Streaming support via SSE

**OllamaProvider** (~150 lines)
- HTTP to `localhost:11434/api/chat`
- Auto-detect running Ollama via health check
- Models: dynamically fetched from `/api/tags`
- No API key needed
- Best for privacy-conscious users

**AppleIntelligenceProvider** (~200 lines)
- Available on macOS 15.1+ with Apple Silicon, iOS 18.1+
- Uses Foundation Models framework (`LanguageModelSession`)
- On-device processing — no data leaves the device
- Limited model capabilities but zero cost and full privacy
- Availability detection: check `MLModel.isAvailable` at runtime
- Graceful fallback to other providers when unavailable

**ProviderManager** (~150 lines)
- Stores user's preferred provider + model in `@AppStorage`
- API keys in Keychain
- Auto-selection: tries Apple Intelligence → Ollama → cloud APIs
- Provider health checking (can the selected provider respond?)
- Retry with fallback: if primary fails, try next available provider

---

## Prompt Engine (Ported from Python)

All prompt logic from `core/tk/roles/prompts.py` is embedded in the app:

```swift
enum PromptEngine {
    static let universalConstraints = """
    CONSTRAINTS:
    - No AI cliches: delve, landscape, robust, leverage...
    - No generic openings: "In today's world..."...
    """

    static let universalStructure = """
    STRUCTURE:
    - Hook: First sentence must stop the reader...
    """

    static let universalOutput = """
    OUTPUT:
    - Read your suggestions twice before responding...
    """

    static func buildPrompt(voiceAndTone: String, focus: String = "") -> String {
        [voiceAndTone, universalConstraints, universalStructure, focus, universalOutput]
            .filter { !$0.isEmpty }
            .joined(separator: "\n\n")
    }
}
```

**SuggestionEngine** (~600 lines, ported from `engine.py`)
- `generateInlineSuggestions()`: builds prompt, calls LLM, parses JSON response
- `generateBatchSuggestions()`: multi-advisor single-call prompt
- `generateQuickSuggestions()`: local heuristics (passive voice, weak words, sentence length) — no LLM
- `findTextPosition()`: multi-level text matching (exact → case-insensitive → whitespace-normalized → prefix)
- `applySuggestion()`: LLM-powered smart merge when anchor text has drifted
- Retry logic: exponential backoff at 3s, 9s, 15s

**CouncilEngine** (~500 lines, ported from `council.py`)
- `conductDebate()`: parallel `TaskGroup` LLM calls → consensus → disagreements → insights
- `identifyConsensus()`: word overlap grouping (>50% match = same point)
- `identifyDisagreements()`: topic-keyword matching + sentiment comparison
- `extractUniqueInsights()`: points mentioned by exactly one advisor
- `calculateOverallSentiment()`: positive/critical/neutral word counting

---

## Data Models (SwiftData)

```swift
@Model class TKDocument {
    var id: UUID
    var title: String
    var content: String                // Raw markdown
    var createdAt: Date
    var lastModifiedAt: Date
    var wordCount: Int                 // Derived, updated on save
    var project: TKProject?
    var journeyState: JourneyState?
    var storyType: String?             // Story type ID
    var selectedAdvisors: [String]     // Advisor IDs
}

@Model class TKProject {
    var id: UUID
    var name: String
    var folderBookmark: Data?          // Security-scoped bookmark
    var lastOpenedAt: Date
    var documents: [TKDocument]
}

@Model class JourneyState {
    var id: UUID
    var stage: JourneyStage            // canvas, draft, revise, final
    var draftStage: DraftStage?        // early, middle, late
    var comments: [ReviewComment]      // Inline suggestions with status
    var revisionCycle: Int
    var totalSuggestionsReceived: Int
    var totalSuggestionsAccepted: Int
    var document: TKDocument?
}

@Model class ReviewComment {
    var id: UUID
    var advisorId: String
    var type: SuggestionType           // fix, improve, rephrase, add, remove
    var originalText: String
    var suggestedText: String
    var reasoning: String
    var priority: Int
    var status: CommentStatus          // pending, accepted, edited, skipped
    var rangeStart: Int
    var rangeEnd: Int
}

@Model class CustomRole {
    var id: UUID
    var roleId: String                 // "custom_my-role"
    var name: String
    var expertiseArea: String
    var focusAreas: [String]
    var perspective: String
    var guidelines: [String]
    var exampleGood: String?
    var exampleBad: String?
    var systemPrompt: String           // Generated from template
}

@Model class Conversation {
    var id: UUID
    var roleId: String
    var messages: [ConversationMessage]
    var createdAt: Date
    var document: TKDocument?
}

@Model class ConversationMessage {
    var id: UUID
    var question: String
    var answer: String
    var timestamp: Date
}

@Model class UsageRecord {
    var id: UUID
    var provider: String
    var model: String
    var inputTokens: Int
    var outputTokens: Int
    var cost: Double
    var timestamp: Date
}
```

All models use CloudKit sync via SwiftData's `modelConfiguration` with `.cloudKit` container.

---

## iCloud Sync Strategy

- **SwiftData + CloudKit** for structured data (documents, journey state, custom roles, conversations, usage records)
- **iCloud Drive** for project folders (optional — user chooses where to store projects)
- **Conflict resolution:** Last-writer-wins for simple fields. For document content, use operational transform or prompt user to choose version if both devices edited since last sync.
- **Offline support:** Full functionality offline. Sync when connection returns. SwiftData handles this automatically.

---

## Search

**Phase 1 (ship with):** SQLite FTS5 via `GRDB.swift`
- Index document content, titles, project names
- Fast keyword search with ranking
- ~300 lines of Swift

**Phase 2 (post-launch):** Core Spotlight integration
- `CSSearchableItem` for system-wide search
- Users find TK documents from Spotlight

**Phase 3 (future):** On-device semantic search
- Apple's `NaturalLanguage` framework for sentence embeddings
- Store embeddings alongside FTS index
- Hybrid keyword + semantic ranking

---

## Platform-Specific Considerations

### macOS

- **Menu bar:** File (New, Open, Save, Export), Edit (Undo, Redo, formatting), View (Focus Mode, Toggle Sidebar), AI (Request Feedback, Quick Suggestions, Council Debate)
- **Keyboard shortcuts:** Cmd+N (new doc), Cmd+S (save), Cmd+B/I/U (formatting), Cmd+Shift+F (feedback), Cmd+Shift+D (council debate), Cmd+Enter (send Q&A), Cmd+1/2/3/4 (journey stages)
- **Touch Bar:** Formatting shortcuts (if available)
- **Window management:** Supports Stage Manager, full screen, split view
- **NSTextView** bridge for TextKit 2 editor

### iOS / iPadOS

- **Keyboard toolbar:** Formatting buttons above keyboard (bold, italic, heading, list, link)
- **iPad:** Split view with editor left, suggestions right. Supports Stage Manager on M-chip iPads.
- **iPhone:** Full-screen editor. Suggestions appear as bottom sheet. Swipe to navigate between journey stages.
- **Apple Pencil:** Scribble support in text editor (free from TextKit 2)
- **UITextView** bridge for TextKit 2 editor
- **Share extension:** "Send to TK" from other apps
- **Shortcuts app integration:** "Create TK Document" action

---

## CLI Architecture — `tk` Command-Line Tool

### Overview

The `tk` CLI is a native macOS command-line tool that shares all core logic with the GUI app via the `TKCore` library. It replaces the Python CLI (`core/tk/cli/`) — same commands, same output contracts, no server needed. The CLI calls LLM APIs directly, reads/writes files from disk, and outputs to stdout/stderr with JSON, NDJSON, or human-readable formatting.

**Why a Swift CLI?**
- Single codebase: prompt engine, LLM providers, roles, story types, suggestion engine, council engine are all shared between app and CLI
- No Python dependency: writers install one thing, not a Python environment
- Pipe-friendly: `cat essay.md | tk suggest --quiet` works like the Python version
- CI-friendly: `tk suggest --strict` exits non-zero if suggestions exist (for linting pipelines)
- Installable via `swift build` or Homebrew

### Package.swift Changes

```swift
// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "TKEditor",
    platforms: [.macOS(.v14), .iOS(.v17)],
    products: [
        .executable(name: "TKEditor", targets: ["TKEditor"]),
        .executable(name: "tk", targets: ["TKCLI"]),
        .library(name: "TKCore", targets: ["TKCore"]),
    ],
    dependencies: [
        .package(url: "https://github.com/swiftlang/swift-markdown.git", from: "0.4.0"),
        .package(url: "https://github.com/apple/swift-argument-parser.git", from: "1.3.0"),
    ],
    targets: [
        .target(
            name: "TKCore",
            dependencies: [
                .product(name: "Markdown", package: "swift-markdown"),
            ],
            path: "Sources/TKCore"
        ),
        .executableTarget(
            name: "TKEditor",
            dependencies: ["TKCore"],
            path: "Sources/TKEditor"
        ),
        .executableTarget(
            name: "TKCLI",
            dependencies: [
                "TKCore",
                .product(name: "ArgumentParser", package: "swift-argument-parser"),
            ],
            path: "Sources/TKCLI"
        ),
        .testTarget(name: "TKCoreTests", dependencies: ["TKCore"], path: "Tests/TKCoreTests"),
        .testTarget(name: "TKEditorTests", dependencies: ["TKEditor"], path: "Tests/TKEditorTests"),
        .testTarget(name: "TKCLITests", dependencies: ["TKCLI"], path: "Tests/TKCLITests"),
    ]
)
```

### Command Map: Python → Swift CLI

| Python Command | Swift CLI | Input | Output | LLM? |
|---------------|-----------|-------|--------|------|
| `tk suggest <file>` | `tk suggest <file>` | File, `--text`, stdin | text, `--json`, `--ndjson`, `--quiet` | Yes |
| `tk suggest --advisors a,b` | `tk suggest --advisors a,b` | Same | Multi-advisor colored output | Yes |
| `tk suggest --interactive` | `tk suggest --interactive` | Same | Interactive accept/edit/skip/quit | Yes |
| `tk suggest --story-type brd_prd` | `tk suggest --story-type brd_prd` | Same | Auto-selects advisors + genre context | Yes |
| `tk suggest --describe "..."` | `tk suggest --describe "..."` | Same | LLM team assembly → suggestions | Yes |
| `tk council <file>` | `tk council <file>` | File, `--text`, stdin | Perspectives + synthesis | Yes |
| `tk ask <question>` | `tk ask <question>` | `--file`, `--text`, stdin | Answer with role attribution | Yes |
| `tk pipe <file>` | `tk pipe <file>` | File, stdin | Rewritten text, `--diff`, `--apply` | Yes |
| `tk export <file>` | `tk export <file>` | File | md, txt, html | No |
| `tk roles list` | `tk roles list` | — | Table or `--json` | No |
| `tk roles show <id>` | `tk roles show <id>` | — | Role details | No |
| `tk roles create` | `tk roles create` | Options | Confirmation | No |
| `tk roles delete <id>` | `tk roles delete <id>` | — | Confirmation | No |
| `tk story-types list` | `tk story-types list` | — | Table or `--json` | No |
| `tk story-types show <id>` | `tk story-types show <id>` | — | Details | No |
| `tk story-types assemble "..."` | `tk story-types assemble "..."` | Description | Assembled team | Yes |
| `tk config show` | `tk config show` | — | Effective config | No |
| `tk config set <k> <v>` | `tk config set <k> <v>` | Key/value | Confirmation | No |
| `tk config init` | `tk config init` | — | Creates `~/.tk/config.json` | No |
| `tk serve` | **Dropped** | — | — | — |
| `tk index` | **Dropped** | — | — | — |
| `tk bookmarks` | **Dropped** | — | — | — |
| `tk search` | **Dropped** | — | — | — |
| `tk stats` | **Dropped** | — | — | — |
| `tk project *` | **Dropped** | — | — | — |
| `tk journey *` | **Dropped** | — | — | — |

### CLI Architecture Detail

```swift
// Entry point
@main
struct TK: ParsableCommand {
    static let configuration = CommandConfiguration(
        commandName: "tk",
        abstract: "TK — Writing assistant with AI advisors",
        subcommands: [
            Suggest.self,
            Council.self,
            Ask.self,
            Pipe.self,
            Export.self,
            Roles.self,
            StoryTypes.self,
            Config.self,
        ]
    )
}
```

**Output formatting** (shared `OutputFormatter` protocol):
- `TextFormatter`: Human-readable with ANSI colors (detects TTY)
- `JSONFormatter`: `{"version":"1.0", "suggestions": [...], "metadata": {...}}`
- `NDJSONFormatter`: One JSON object per line (streaming-friendly)
- `QuietFormatter`: Suggested text only, one per line

**Input resolution** (shared `InputResolver`):
- Reads files from arguments
- Reads from `--text` option
- Reads from stdin if not a TTY
- Returns `[(name: String, content: String)]`

**LLM integration**: Uses `TKCore.LLMProvider` protocol directly — same code the app uses. Config comes from `~/.tk/config.json` or `--provider`/`--model` flags.

**Custom roles storage**: CLI stores custom roles in `~/.tk/roles/` as JSON files (not SwiftData, which requires an app sandbox). The `CustomRoleStore` protocol has two implementations: `SwiftDataRoleStore` (app) and `FileSystemRoleStore` (CLI).

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Error (API failure, invalid input, file not found) |
| 2 | `--strict` mode: suggestions exist (for CI linting) |

### Config Precedence (CLI)

```
1. CLI flags (--provider anthropic --model claude-sonnet-4-6)
2. Environment variables (TK_PROVIDER, TK_MODEL, TK_API_KEY)
3. Project-local config (./.tk/config.json)
4. User global config (~/.tk/config.json)
5. Built-in defaults (anthropic, claude-sonnet-4-6)
```

### Installation

```bash
# From source
cd editor/TKEditor && swift build -c release
cp .build/release/tk /usr/local/bin/

# First run
tk config init                    # Creates ~/.tk/config.json
tk config set api_key sk-...      # Stored in Keychain via TKCore.KeychainService
tk roles list                     # Verify setup
tk suggest README.md              # First suggestion
```

---

## Implementation Phases

### Phase 1: Foundation (files: ~15)

**Goal:** App launches, persists documents, has navigation shell.

- [ ] Xcode project: universal app (macOS 14+ / iOS 17+), CloudKit entitlement, SPM dependencies
- [ ] SwiftData models: `TKDocument`, `TKProject`, `JourneyState`
- [ ] `ContentView` with sidebar (projects/documents) + main editor area
- [ ] `ProjectBrowser`: list projects, create new, open folder
- [ ] `DocumentList`: list documents in project, create new
- [ ] Basic `TextEditor` (SwiftUI native) as placeholder — replaced in Phase 2
- [ ] Autosave (3-sec debounce) to SwiftData
- [ ] Tests: model creation, persistence, basic navigation (20+ tests)

### Phase 2: Live Markdown Editor (files: ~8)

**Goal:** Replace placeholder editor with live-markdown TextKit 2 editor.

- [ ] `MarkdownTextView`: NSViewRepresentable/UIViewRepresentable bridging NSTextView/UITextView
- [ ] TextKit 2 setup: `NSTextContentManager`, `NSTextLayoutManager`, custom `NSTextContentStorage`
- [ ] `MarkdownParser`: parse markdown via `swift-markdown`, produce `NSAttributedString`
- [ ] Live rendering: headings (scaled fonts), bold/italic (font traits), code (monospace + background), links (colored + tappable), lists (indented with bullets/numbers), blockquotes (indented + gray bar), tables (monospace alignment), horizontal rules
- [ ] `MarkdownSerializer`: convert `NSAttributedString` back to markdown for storage
- [ ] `MarkdownTheme`: configurable fonts, colors, spacing (with font picker support)
- [ ] `EditorToolbar`: formatting buttons that insert markdown syntax at cursor
- [ ] Word/character count (derived from plain text)
- [ ] Cursor position + selected text tracking (for AI context)
- [ ] Undo/redo (native from NSTextView/UITextView)
- [ ] System spell check integration
- [ ] Tests: markdown parsing roundtrip, formatting insertion, word count (30+ tests)

### Phase 3: LLM Providers (files: ~8)

**Goal:** App can call LLM APIs directly.

- [ ] `LLMProvider` protocol
- [ ] `AnthropicProvider`: direct HTTPS, streaming SSE, prompt caching
- [ ] `OpenAIProvider`: direct HTTPS, streaming SSE
- [ ] `OllamaProvider`: local HTTP, auto-detection, model listing
- [ ] `AppleIntelligenceProvider`: Foundation Models framework, availability detection
- [ ] `ProviderManager`: selection, health check, fallback chain, retry with exponential backoff
- [ ] `KeychainService`: secure storage for API keys
- [ ] Settings UI: provider selection, API key entry (masked), model picker, test connection
- [ ] Tests: provider protocol conformance, response parsing, error handling, retry logic (25+ tests)

### Phase 4: Journey — Canvas & Draft (files: ~10)

**Goal:** Full Canvas and Draft stages working.

- [ ] Embedded `BuiltinRoles` (all 11 roles with full prompts, ported from `prompts.py`)
- [ ] Embedded `StoryTypes` (all 9 types with genre contexts, ported from `definitions.py`)
- [ ] `CanvasView`: story type grid, free-text team assembly, "just start writing"
- [ ] `PromptEngine`: `buildPrompt()`, universal sections, genre context injection
- [ ] `DraftView`: editor + toolbar + quick suggestions bar + advisor avatars
- [ ] `QuickSuggestionsEngine`: local heuristics (passive voice, weak words, sentence length, role-specific tips) — no LLM
- [ ] `DraftStageSelector`: early/middle/late toggle affecting feedback type
- [ ] Journey state machine: `canvas → draft` transition with story type + advisors persisted
- [ ] `AdvisorPicker`: multi-select (1-10) with role cards showing name, icon, color, description
- [ ] Tests: prompt construction, story type mapping, journey transitions, quick suggestions (30+ tests)

### Phase 5: AI Features — Suggestions & Council (files: ~8)

**Goal:** Full AI-powered feedback loop.

- [ ] `SuggestionEngine`: port from Python — build prompt, call LLM, parse JSON response, find text positions (4-level matching)
- [ ] `BatchSuggestionEngine`: multi-advisor single-call prompt, fallback to sequential
- [ ] `CouncilEngine`: parallel `TaskGroup` calls, consensus identification, disagreement extraction, unique insights, overall sentiment
- [ ] `SuggestionApplier`: LLM-powered smart merge for drifted anchor text
- [ ] `RoleQAEngine`: conversation with role context, history injection
- [ ] Streaming UI: show LLM response as it arrives (token-by-token for feedback feel)
- [ ] Tests: prompt building, JSON parsing, text position finding, consensus algorithm, sentiment analysis (40+ tests)

### Phase 6: Target Split — Extract TKCore Library (files: ~5 new/moved)

**Goal:** Split the monolithic executable into TKCore (library) + TKEditor (app) so the CLI can share all engine code.

- [ ] Create `Sources/TKCore/` directory, move shared code: Models/, Engine/, Resources/, Services/
- [ ] Create `Sources/TKEditor/` directory, move GUI code: App/, Editor/, Views/
- [ ] Update `Package.swift`: add TKCore library target, TKEditor depends on TKCore
- [ ] Add `swift-argument-parser` dependency
- [ ] Create `Sources/TKCLI/` with stub `@main` entry point
- [ ] Add `TKCLI` executable target depending on TKCore + ArgumentParser
- [ ] Split test targets: `Tests/TKCoreTests/` (shared logic) + `Tests/TKEditorTests/` (GUI) + `Tests/TKCLITests/` (CLI)
- [ ] Verify `swift build` succeeds for all three targets
- [ ] Verify `swift test` passes all existing tests under new structure
- [ ] Tests: target split validation — imports, shared code accessibility (5+ tests)

### Phase 7: CLI — Core Commands (files: ~12)

**Goal:** `tk suggest`, `tk ask`, `tk council`, `tk pipe` work from the terminal, matching Python CLI output contracts.

- [ ] `InputResolver`: file args, `--text`, stdin detection — returns `[(name, content)]`
- [ ] `OutputFormatter` protocol + implementations: `TextFormatter` (ANSI colors, TTY detection), `JSONFormatter` (v1.0 schema), `NDJSONFormatter`, `QuietFormatter`
- [ ] `CLIConfig`: load from `~/.tk/config.json`, env vars, CLI flags — precedence chain
- [ ] `Suggest` command: single file, multi-file with `--parallel`, `--role`, `--advisors` multi-advisor batch, `--story-type`, `--describe` LLM assembly, `--categories` filter, `--min-confidence`, `--max-suggestions`, `--strict` exit code 2, `--interactive` accept/edit/skip/quit loop
- [ ] `Council` command: 2-10 advisors, `--question` focus, parallel `TaskGroup` calls, perspectives + synthesis output
- [ ] `Ask` command: question + optional `--file`/`--text`/stdin context, `--role` advisor, `--json`/`--quiet` output
- [ ] `Pipe` command: apply suggestions and output rewritten text, `--diff` unified diff, `--apply` write-back, `--role`, `--categories`
- [ ] `Export` command: md/txt/html format conversion, `--output` file or stdout, `--title` for HTML
- [ ] All commands share `--provider`/`--model` overrides from `CLIConfig`
- [ ] Error handling: stderr for errors/warnings, stdout for data — clean pipe separation
- [ ] Tests: command parsing, input resolution, output formatting, exit codes, interactive mode simulation (35+ tests)

### Phase 8: CLI — Roles, Story Types & Config Commands (files: ~6)

**Goal:** `tk roles`, `tk story-types`, `tk config` complete the CLI feature set.

- [ ] `Roles` command group: `list` (table + `--json`), `show <id>` (detail panel), `create` (options → JSON file in `~/.tk/roles/`), `delete` (with `--force` confirmation skip)
- [ ] `FileSystemRoleStore`: read/write custom roles as JSON in `~/.tk/roles/` (CLI equivalent of SwiftData `CustomRole`)
- [ ] `CustomRoleStore` protocol with two implementations: `FileSystemRoleStore` (CLI) + future `SwiftDataRoleStore` (app)
- [ ] `StoryTypes` command group: `list` (table + `--json`), `show <id>` (detail with genre context), `assemble "description"` (LLM team assembly)
- [ ] `Config` command group: `show` (effective config with sources), `set <key> <value>` (writes to `~/.tk/config.json`), `get <key>`, `init` (create default config)
- [ ] API key management: `tk config set api_key <key>` stores in Keychain via `TKCore.KeychainService`
- [ ] Tests: role CRUD, story type lookup, config precedence, file-system role store (20+ tests)

### Phase 9: Journey — Revise & Final (files: ~8)

**Goal:** Complete writing lifecycle in the GUI app.

- [ ] `ReviseView`: editor with inline suggestion highlights (TextKit 2 annotation marks)
- [ ] Suggestion highlights: yellow/orange overlays on `range_start..range_end`
- [ ] Comment panel (right side on Mac, sheet on iOS): numbered list, accept/edit/skip buttons
- [ ] Comment navigation: previous/next, keyboard arrows
- [ ] Accept action: replace `original_text` with `suggested_text` in editor
- [ ] Edit action: inline text field to modify suggestion before applying
- [ ] Skip action: mark as skipped, move to next
- [ ] `FinalView`: stats display, export options (Markdown, PDF, Plain Text), share sheet
- [ ] PDF export: `NSAttributedString` rendering with proper typography
- [ ] "Request more feedback" → back to Revise with incremented cycle
- [ ] Auto-transition: all comments addressed → prompt to move to Final
- [ ] Tests: comment CRUD, accept/skip flow, export (25+ tests)

### Phase 10: Custom Roles & Search (files: ~6)

**Goal:** User can create custom advisors and search across documents.

- [ ] `CustomRoleCreator`: name, expertise, focus areas (multi-select), perspective, guidelines, examples
- [ ] Validation: role name regex `^[a-zA-Z0-9 \-]+$`, 1-5 focus areas, 1-5 guidelines, 10-200 char perspective
- [ ] Prompt generation from template (matching Python's `build_prompt`)
- [ ] `SearchView`: query input, results list with title/snippet/relevance
- [ ] `SearchEngine`: SQLite FTS5 via GRDB.swift indexing document content + titles
- [ ] Core Spotlight integration for system-wide search
- [ ] Tests: role validation, prompt generation, search indexing, FTS queries (20+ tests)

### Phase 11: iCloud Sync & Platform Polish (files: ~6)

**Goal:** Seamless sync across devices + Apple-quality polish.

- [ ] SwiftData CloudKit configuration for all models
- [ ] Conflict resolution UI for simultaneous document edits
- [ ] macOS: full menu bar, keyboard shortcuts, Touch Bar support
- [ ] iOS: keyboard toolbar, compact layouts, bottom sheets for panels
- [ ] iPad: split view (editor + suggestions), Stage Manager support
- [ ] Animations: smooth transitions between journey stages, suggestion highlight fades, panel slides
- [ ] Haptic feedback on iOS: suggestion accept, stage transition, export complete
- [ ] Dynamic Type support throughout
- [ ] VoiceOver accessibility audit
- [ ] Focus state management (keyboard navigation)
- [ ] Handoff: start editing on Mac, continue on iPhone
- [ ] Tests: sync, accessibility, layout adaptation (20+ tests)

### Phase 12: Token Budget & Usage Tracking (files: ~3)

**Goal:** Cost awareness without getting in the way.

- [ ] `UsageTracker`: parse `inputTokens`/`outputTokens` from LLM API responses, accumulate per model
- [ ] Cost calculation using embedded pricing tables (matching Python's `estimate_cost`)
- [ ] UI: hidden by default, shows when >50% of user-set budget used
- [ ] CLI: `tk suggest` shows token count + cost in stderr when `--verbose`
- [ ] Settings: monthly budget input, per-model cost display, reset usage
- [ ] Tests: cost calculation, budget threshold, accumulation (10+ tests)

### Phase 13: Testing & Ship (files: ~10 test files)

**Goal:** 80%+ coverage, TestFlight, App Store, Homebrew.

- [ ] Integration tests: full journey flow (canvas → draft → revise → final)
- [ ] LLM integration tests with mock providers
- [ ] UI tests: navigation, editor formatting, suggestion acceptance
- [ ] CLI integration tests: end-to-end `tk suggest`, `tk council`, `tk pipe` with mock provider
- [ ] Performance profiling: editor with 10K+ word documents, concurrent LLM calls
- [ ] Memory profiling: no leaks in long editing sessions
- [ ] TestFlight beta distribution
- [ ] Homebrew formula for `tk` CLI (`brew install tk`)
- [ ] App Store metadata, screenshots, privacy policy
- [ ] Total target: **300+ tests** (core: 100+, editor: 80+, CLI: 60+, integration: 60+)

---

## Dependencies (SPM)

| Package | Purpose | Used By | URL |
|---------|---------|---------|-----|
| swift-markdown | Markdown parsing (Apple's official) | TKCore | github.com/swiftlang/swift-markdown |
| swift-argument-parser | CLI command routing + typed options (Apple's) | TKCLI | github.com/apple/swift-argument-parser |
| GRDB.swift | SQLite FTS5 for search | TKCore | github.com/groue/GRDB.swift |
| KeychainAccess | Simpler Keychain API | TKCore | github.com/kishikawakatsumi/KeychainAccess |

Four dependencies total. Everything else is Apple frameworks.

---

## What We Keep from Existing Swift Code

| File | Disposition |
|------|-------------|
| `Models.swift` | **Rewrite** — SwiftData `@Model` replaces Codable structs. Some type names preserved. |
| `AuthModels.swift` | **Delete** — No auth needed (standalone app). |
| `APIClient.swift` | **Delete** — No TK API server. Direct LLM calls instead. |
| `TokenStorage.swift` | **Evolve** — Generalize to `KeychainService` for API keys. |
| `AppState.swift` | **Rewrite** — SwiftData replaces `@Published` properties. `@Observable` for view state. |
| `ContentView.swift` | **Rewrite** — New navigation structure with journey stages. |
| `EditorView.swift` | **Rewrite** — TextKit 2 live-markdown replaces plain `TextEditor`. |
| `SuggestionsPanel.swift` | **Evolve** — UI patterns preserved, data source changes to local engine. |
| `RoleQAView.swift` | **Evolve** — Good conversation UI. Wire to local LLM calls. |
| `CouncilDebateView.swift` | **Evolve** — Complex visualization is good. Wire to local engine. |
| `CustomRoleCreatorView.swift` | **Evolve** — Form is solid. Wire to SwiftData persistence. |
| `LoginView.swift` | **Delete** — No auth. |
| `SettingsView.swift` | **Rewrite** — New tabs: LLM Providers, Editor, iCloud, Usage. |

---

## Verification

After each phase:
1. `swift test` — all tests pass
2. Build and run on macOS + iOS Simulator
3. Manual walkthrough of the new functionality
4. Check iCloud sync between devices (Phase 11+)

### End-to-end acceptance test (GUI):
1. Launch app → Create project → Select story type (Canvas)
2. Write 500+ words in live-markdown editor (Draft)
3. Request feedback → See inline suggestions with highlights (Revise)
4. Accept 3 suggestions, skip 2, edit 1
5. Move to Final → Export as PDF and Markdown
6. Verify exported files contain accepted changes
7. Open on another device → Verify document synced via iCloud

### End-to-end acceptance test (CLI):
1. `tk config init && tk config set api_key <key>` — setup works
2. `tk roles list` — shows 11 built-in roles
3. `tk story-types list` — shows 9 story types
4. `echo "The system can be utilized to process data" | tk suggest --quiet` — returns suggestions
5. `tk suggest README.md --json | jq '.suggestions | length'` — JSON output parses correctly
6. `tk suggest README.md --advisors technical_writer,editor` — multi-advisor colored output
7. `tk suggest README.md --strict; echo $?` — exits 2 if suggestions found
8. `tk council README.md --advisors general,technical_writer,editor -q "Is this clear?"` — shows perspectives + synthesis
9. `tk ask "How should I structure this?" --file draft.md --role academic` — returns advisor answer
10. `cat draft.md | tk pipe > draft.improved.md` — rewritten text output
11. `tk pipe draft.md --diff` — shows unified diff
12. `tk export draft.md --format html --output draft.html` — produces valid HTML
