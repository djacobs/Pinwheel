# Commenting and AI Selection Review Plan

## Context

The past week's review found that Pinwheel has review gates and editing flows, but not a first-class commenting interaction. Current surfaces include static report prose, whole-report Discord edit modals, admin approve/reject gates, and AI council verdicts. Reviewers and editors cannot leave anchored notes on text, and they cannot invite the AI to comment on a specific selected passage.

This plan adds a small anchored review layer over reports and commentary first, then extends the same model to generated-code review.

## Current Evidence

- `templates/pages/reports.html` renders report content as static prose.
- `templates/pages/game.html` renders courtside commentary and the round report as static prose.
- `src/pinwheel/discord/views.py` exposes `EditSeriesModal`, which replaces an entire series report.
- `src/pinwheel/db/repository.py` exposes `update_report_content()`, which performs a last-write-wins blob update.
- `src/pinwheel/ai/codegen_council.py` produces whole-artifact AI reviewer verdicts and rationales.
- `src/pinwheel/discord/embeds.py` shows codegen review as an admin embed with a truncated code preview and approve/reject controls.

## Product Goals

1. Reviewers and editors can leave comments on specific report/commentary text.
2. Reviewers and editors can select text and ask the AI to comment on that exact selection.
3. Comments persist, reload, and can be resolved.
4. Private report privacy is preserved.
5. Whole-report edits cannot silently overwrite newer edits.
6. Codegen AI reviewer rationales can later be surfaced as review comments.

## Non-Goals for the First Pass

- Full Google Docs-style collaborative editing.
- Character-perfect reanchoring across arbitrary rewrites.
- Real-time multi-user presence.
- Inline comments on every play-by-play row.
- AI autonomous commenting without a human selecting text first.

## Phase 1: Data Model

Add two additive tables in `src/pinwheel/db/models.py`.

### `CommentThreadRow`

Fields:

- `id`: primary key UUID.
- `season_id`: season id, indexed.
- `target_type`: `report`, `commentary`, `codegen_effect`, or future target types.
- `target_id`: id of the report row, game commentary report row, or effect id.
- `anchor_type`: initially `text_range`.
- `start_offset`: integer character offset in the target content.
- `end_offset`: integer character offset in the target content.
- `selected_text`: the original selected passage.
- `quote_hash`: short hash of selected text for stale-anchor detection.
- `status`: `open`, `resolved`, or `stale`.
- `created_by`: Discord id or player id for the creator.
- `created_at`: UTC timestamp.
- `resolved_at`: nullable UTC timestamp.

Indexes:

- `(target_type, target_id)`
- `(season_id, status)`

### `CommentRow`

Fields:

- `id`: primary key UUID.
- `thread_id`: foreign key to comment thread.
- `author_type`: `human`, `ai`, or `system`.
- `author_id`: Discord id, player id, or AI/system id.
- `body`: comment body.
- `metadata_json`: optional JSON for prompt/call metadata.
- `created_at`: UTC timestamp.

Indexes:

- `thread_id`
- `created_at`

Schema changes are additive, so `auto_migrate_schema()` can add missing columns, but new tables should be created by metadata startup like existing tables. Do not modify or rewrite existing report rows.

## Phase 2: Repository API

Add methods in `src/pinwheel/db/repository.py`.

- `create_comment_thread(...) -> CommentThreadRow`
- `add_comment(thread_id, author_type, author_id, body, metadata_json=None) -> CommentRow`
- `get_comment_threads(target_type, target_id, include_resolved=False) -> list[CommentThreadRow]`
- `get_comments_for_threads(thread_ids) -> dict[str, list[CommentRow]]`
- `resolve_comment_thread(thread_id, resolver_id) -> CommentThreadRow | None`
- `mark_stale_comment_threads(target_type, target_id, stale_thread_ids) -> int`

Add a helper:

- `find_stale_report_comment_threads(report_id, content) -> list[str]`

First-pass stale detection can be simple: if `selected_text` no longer appears in the current target content, mark the thread `stale`. This is intentionally conservative.

## Phase 3: API Routes

Create `src/pinwheel/api/comments.py` and include it in `src/pinwheel/main.py`.

Routes:

- `GET /api/comments/{target_type}/{target_id}`
  - Returns open threads and their comments.
- `POST /api/comments/thread`
  - Creates a thread and first human comment.
- `POST /api/comments/{thread_id}/reply`
  - Adds a human reply.
- `POST /api/comments/{thread_id}/resolve`
  - Resolves a thread.
- `POST /api/comments/thread/ai`
  - Creates a thread and asks the AI to respond to the selected text.

Request models should live in `src/pinwheel/models/comments.py`.

Access control:

- Public reports and game commentary: logged-in governor or admin can comment.
- Private reports: only the owning governor can view/comment/request AI.
- Codegen effects: admin only.
- Development mode may follow existing relaxed auth behavior, but tests should cover production-style restrictions.

Validation:

- `target_type` must be known.
- `start_offset` and `end_offset` must be within content bounds.
- `selected_text` must match the target content at the submitted range.
- Comment body cannot be empty.
- AI request must include selected text and may include an optional user question.

## Phase 4: Web UI

Add `static/js/comments.js` without introducing a build step.

Behavior:

1. User selects text inside an element marked with `data-comment-target-type` and `data-comment-target-id`.
2. A compact floating toolbar appears with icon buttons:
   - Comment
   - Ask AI
3. Comment opens an inline form.
4. Ask AI posts the selected text, target metadata, and optional question.
5. Open threads render in a comment panel below the report on mobile and as a right-side panel on wider screens.
6. Resolved threads collapse by default.
7. Stale threads display as stale rather than disappearing.

Update templates:

- `templates/pages/reports.html`
- `templates/pages/game.html`
- `templates/base.html` if a shared script include is needed.

Use `data-*` attributes on report prose containers:

```html
<div
  class="report-content"
  data-comment-target-type="report"
  data-comment-target-id="{{ m.id }}"
>
  {{ m.content | prose | safe }}
</div>
```

For game commentary, use the stored commentary report row id when available. If the page currently passes only content, update `src/pinwheel/api/pages.py` to pass `commentary.id`.

CSS additions should live in `static/css/pinwheel.css`:

- Selection toolbar.
- Comment panel.
- Thread cards.
- Resolved/stale states.

## Phase 5: AI-On-Selection

Add `src/pinwheel/ai/comment_assistant.py`.

Function:

- `generate_comment_on_selection(...) -> str`

Inputs:

- `target_type`
- `target_id`
- `report_type`
- `season_id`
- `round_number`
- `selected_text`
- `nearby_context`
- optional user question

Prompt rules:

- Observational, not prescriptive.
- Grounded only in the selected text and supplied context.
- Brief enough to fit as a comment.
- If the selection is ambiguous, say what context is missing.
- Do not reveal private report content outside its owner.

The API route stores the AI response as a `CommentRow` with:

- `author_type="ai"`
- `author_id="comment_assistant"`
- `metadata_json` containing model, prompt kind, selected range, and usage reference if available.

Track token usage with the existing AI usage helpers.

## Phase 6: Editing Safety

Improve whole-report editing before expanding it.

Changes:

- Add an `updated_at` column or use a content hash submitted with the modal.
- Include the original content hash when opening `EditSeriesModal`.
- On submit, reject if the stored report hash differs from the modal's original hash.
- Include `previous_hash`, `new_hash`, and editor id in `report.edited` events.
- After successful edit, run stale detection for threads targeting that report.

This prevents silent clobbering when two editors open the same series report.

## Phase 7: Codegen Review Comments

After report comments work, adapt the same model for generated-code review.

Steps:

1. Store council reviewer rationales as comments targeting `codegen_effect:{effect_id}`.
2. Add a codegen review page or richer admin view that shows full code plus comment threads.
3. Keep Discord approve/reject as the fast path, but link to the full review page.
4. Later, add line anchors if generated code is displayed with stable line numbers.

First pass can use whole-artifact anchors. Line-level anchors are useful but not required to solve the immediate commenting gap.

## Tests

Add or update tests:

- `tests/test_comments.py`
  - Repository create thread, reply, resolve.
  - API create/list/reply/resolve.
  - Stale detection after target content changes.
- `tests/test_comment_assistant.py`
  - Mock AI response persists as an AI comment.
  - Empty/invalid selections rejected.
  - Private report context is not exposed.
- `tests/test_pages.py`
  - Reports page emits comment target attributes.
  - Game page emits comment target attributes for commentary and round report.
  - Existing pages still render when there are no comments.
- `tests/test_discord.py`
  - Series edit stale hash rejection.
  - Series edit appends enriched `report.edited` event.
- `tests/test_auth.py` or API-specific tests
  - Non-owner cannot comment on private report.
  - Admin-only codegen comments are enforced.

Before commit:

```bash
uv run pytest -x -q
uv run ruff check src/ tests/
```

## Acceptance Criteria

- A logged-in eligible reviewer can select text in a public report or game commentary and leave a comment.
- The comment persists and reloads on the same page.
- A reviewer can select text and ask the AI to comment on that exact selection.
- The AI response appears in the same thread and is stored as an AI-authored comment.
- A thread can be resolved.
- Private report comments are visible only to the report owner.
- Whole-report edits cannot silently overwrite a newer edit.
- Existing report and game pages still render without JavaScript.
- New code has tests covering permissions, persistence, UI attributes, and AI comment creation.

## Suggested Implementation Order

1. Add comment models and repository methods.
2. Add API routes and permission checks.
3. Add template `data-*` attributes and basic thread rendering.
4. Add selection toolbar JavaScript and CSS.
5. Add AI comment assistant and `/ai` route.
6. Add stale-anchor detection and edit conflict protection.
7. Extend codegen review to use comments.
8. Update docs/dev log and UX notes after UI work.

## Risks and Mitigations

- **Anchor drift:** first pass marks stale instead of guessing. Reanchoring can improve later.
- **Privacy leakage:** private report routes must use owner checks before fetching or sending selected context to AI.
- **JS fragility:** pages must remain readable without comment JavaScript.
- **AI overreach:** prompt must keep comments observational and local to the selection.
- **Edit conflicts:** use hashes before expanding editor workflows.

