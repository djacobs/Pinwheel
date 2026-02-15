---
name: post-commit
description: Run tests, update demo artifacts, dev log, and UX notes after completing work
---

# Post-Commit Checklist

Run this after completing a unit of work. Execute each step in order.

## Step 1: Run and Update Tests

1. Run `uv run pytest -x -q` from the project root.
2. Note the test count from the output (e.g. "465 passed").
3. Run `uv run ruff check src/ tests/` and confirm zero lint errors.
4. If any tests fail or lint errors exist, fix them before continuing.

## Step 2: Run Rodney and Showboat

1. Check if the dev server is running: `curl -sf http://localhost:8000/health > /dev/null 2>&1`
   - If NOT running, start it in the background: `DATABASE_URL="sqlite+aiosqlite:///demo_pinwheel.db" PYTHONPATH=src uv run uvicorn pinwheel.main:app --port 8000 --log-level warning &` and wait 3 seconds.
2. If visual or UI changes were made this session, run the demo pipeline:
   ```
   bash scripts/run_demo.sh
   ```
3. If no visual changes were made, skip this step and note "No visual changes — demo artifacts unchanged."
4. **Always shut down the local server when done.** Run: `lsof -ti:8000 | xargs kill 2>/dev/null` and stop Rodney if it was started (`uvx rodney stop 2>/dev/null`). The local server must not be left running.

## Step 3: Update Dev Log

There must be exactly **one** dev log file per day. Today's file is `docs/DEV_LOG.md`.

1. Read `docs/DEV_LOG.md`.
2. Determine the current date from the file's title line (e.g. "# Pinwheel Dev Log — 2026-02-12").
3. **If today's date does not match the file title:**
   - Rename the current `docs/DEV_LOG.md` to `docs/DEV_LOG_<old-date>.md` (archive it).
   - Create a new `docs/DEV_LOG.md` with today's date, linking to the archived file in the "Previous logs" line.
   - Copy the "Where We Are" section, updating the test count and latest commit info.
   - Create a fresh "Today's Agenda" section.
4. **If today's date matches the file title**, update the existing file:
   - Update the test count in the "Where We Are" section to match Step 1's result.
   - Check off any completed agenda items.
   - Add a new session entry at the bottom following this format:

```markdown
## Session N — <Short Title>

**What was asked:** <1-2 sentences>

**What was built:**
- <bullet points of what changed>

**Files modified (N):** `file1.py`, `file2.py`

**<test_count> tests, zero lint errors.**

**What could have gone better**
```

5. Determine the session number by incrementing the highest session number already in the file.

## Step 4: Archive Claude Code Plans

1. Check if any plan files exist in `~/.claude/plans/`.
2. For each `.md` file found there, read its first line to determine the plan title.
3. **Only archive plans that are related to Pinwheel.** Skip plans for other projects (e.g. LinkBlog, Feedly, Newsletter Ring, DraftStage, token counter animation, LLM call optimization). If the plan title doesn't reference Pinwheel concepts (governance, hoopers, arena, Discord bot, schedule, simulation, etc.), skip it.
4. For each relevant Pinwheel plan, copy it to `docs/plans/` with a descriptive filename: `<date>-<slugified-title>.md` (e.g., `2026-02-12-show-game-clock-in-play-by-play.md`).
   - If a plan with the same content already exists in `docs/plans/`, skip it.
5. If no new relevant plans exist, note "No new plans to archive."

## Step 5: Update UX Notes

1. Read `docs/UX_NOTES.md`.
2. If any **visual or interaction changes** were made this session (templates, CSS, embeds, Discord UX):
   - Find the highest numbered entry in UX_NOTES.md.
   - Add new numbered entries at the appropriate section, following the existing format:
     ```
     ### N. [DONE] <Short description>
     <Problem description>
     **Fix:** <What changed and how>
     ```
3. If no visual/interaction changes were made, skip this step and note "No UX changes this session."

## Final Output

Summarize what was done:
- Test count and lint status
- Whether demo artifacts were updated
- Dev log session number and title
- Plans archived (count and names)
- Whether UX notes were updated
