# Plan: CLI Story Type Integration

## Context

The Story Types feature (Phases 1-4) is complete on both backend and frontend. The API has three endpoints (`GET /story-types`, `GET /story-types/{id}`, `POST /story-types/assemble`) and the `SuggestionRequest`/`BatchSuggestionRequest` models accept `genre_context`. But the CLI (`tk suggest`) has no story type awareness — users must manually specify `--role` or `--advisors`. The user wants the same "pick a template or describe your own" experience available via CLI.

---

## Changes

### 1. Add `genre_context` to `TKClient` methods

**File: `core/tk/cli/client.py`**

- Add `genre_context: str = ""` parameter to `get_suggestions()` and `get_batch_suggestions()` — pass through in the JSON body.
- Add three new methods:
  - `list_story_types() -> list[dict]` — `GET /story-types`
  - `get_story_type(story_type_id: str) -> dict` — `GET /story-types/{id}`
  - `assemble_team(description: str) -> dict` — `POST /story-types/assemble`

### 2. Add `--story-type` and `--describe` flags to `tk suggest`

**File: `core/tk/cli/commands/suggest.py`**

Add two new options to the `suggest()` command:

```python
story_type: Optional[str] = typer.Option(
    None, "--story-type", "--type",
    help="Story type template (e.g., brd_prd, blog_personal). Use 'tk story-types list' to see all.",
)
describe: Optional[str] = typer.Option(
    None, "--describe",
    help="Describe what you're writing; LLM will assemble the right advisor team.",
)
```

Logic (inserted before the "Process through API" block):
1. If `--story-type` is given:
   - Fetch the story type via `client.get_story_type(story_type_id)`
   - Set `advisor_ids` to `story_type["default_advisor_ids"]`
   - Set `genre_context` to `story_type["genre_context"]`
   - Print a brief confirmation: `"Using story type: Blog Post (Professional) — 3 advisors"`
   - Error if `--advisors` is also set (mutually exclusive)
2. If `--describe` is given:
   - Call `client.assemble_team(description)`
   - Set `advisor_ids` from the response's `advisors[].role_id`
   - Set `genre_context` from the response
   - Print the assembled team with reasons (Rich table)
   - Error if `--story-type` is also set (mutually exclusive)
3. Pass `genre_context` through to `_process_suggestions()` and `_process_batch_suggestions()`, which pass it to the client methods.

### 3. New command group: `tk story-types`

**New file: `core/tk/cli/commands/story_types.py`**

Commands:
- `tk story-types list` — Rich table of all 9 types (columns: ID, Name, Category, Advisors)
- `tk story-types show <id>` — Full details of one type (name, description, icon, advisors, genre_context)
- `tk story-types assemble "<description>"` — Call LLM team assembly, display result

**File: `core/tk/cli/__init__.py`** — Register the new command group:
```python
from tk.cli.commands.story_types import app as story_types_app
app.add_typer(story_types_app, name="story-types")
```

### 4. Thread `genre_context` through processing functions

**File: `core/tk/cli/commands/suggest.py`**

- `_process_suggestions()` — add `genre_context` param, pass to `client.get_suggestions()`
- `_process_batch_suggestions()` — add `genre_context` param, pass to `client.get_batch_suggestions()`

### 5. Tests

**New file: `core/tests/test_cli_story_types.py`**

Tests (12+):
- `test_story_type_flag_sets_advisors_and_genre_context` — mock client, verify correct advisor_ids and genre_context passed
- `test_describe_flag_calls_assemble_endpoint` — mock client, verify assemble called
- `test_story_type_and_describe_mutually_exclusive` — should exit with error
- `test_story_type_and_advisors_mutually_exclusive` — should exit with error
- `test_invalid_story_type_shows_error` — 404 from API → friendly error
- `test_list_command_shows_table` — verify all 9 types appear
- `test_show_command_shows_details` — verify fields displayed
- `test_assemble_command_shows_team` — mock LLM response, verify output
- `test_genre_context_passed_to_suggestions` — verify the client method receives genre_context
- `test_genre_context_passed_to_batch_suggestions` — same for batch

---

## Files Changed

| File | Change |
|---|---|
| `core/tk/cli/client.py` | Add `genre_context` to suggestions methods; add 3 story type methods |
| `core/tk/cli/commands/suggest.py` | Add `--story-type` and `--describe` flags; thread `genre_context` |
| `core/tk/cli/commands/story_types.py` | **NEW** — `list`, `show`, `assemble` commands |
| `core/tk/cli/__init__.py` | Register `story-types` command group |
| `core/tests/test_cli_story_types.py` | **NEW** — 12+ tests |

---

## Verification

1. `cd core && uv run pytest tests/test_cli_story_types.py -v` — all new tests pass
2. `cd core && uv run pytest -x -q` — full suite passes
3. Manual: `tk story-types list` shows 9 types
4. Manual: `tk suggest README.md --story-type brd_prd` uses correct advisors
5. Manual: `tk suggest README.md --describe "API documentation"` assembles team and runs suggestions
