# Final Retrospective: What Could Have Gone Better

A synthesis of retrospectives across ~60 development sessions (Sessions 24-100), distilled into recurring failure patterns and structural recommendations.

---

## Recurring Failure Patterns

### 1. Day-1 shortcuts that festered (most frequent)

**Sessions:** 56, 59, 80, 90, 93, 95, 100

Bugs that existed since a feature was first written but only surfaced weeks later. The code worked well enough to pass initial tests and never got revisited.

- `_get_active_season_id()` was wrong from day one — every page on the site was affected for the entire life of the project (S56)
- Duplicate Discord channels existed since first deploy — `on_ready` reconnect behavior was overlooked (S59)
- Blank team page only surfaced when cross-season links were added, because tests always used the active season (S80)
- `showboat image` was always called with the wrong arguments — both demo scripts had the bug since they were written (S90)
- Stale `channel_team_*` entries accumulated across season resets — the iteration pattern assumed a 1:1 mapping that broke across seasons (S93)
- Report ordering was ascending from the start — regenerated reports showed the old version first (S95)
- Interpreter error messages leaked internals to players — `f"V2 interpretation failed: {e}"` was there since the V2 interpreter was written (S100)

**Root cause:** We ship fast and move on. Nothing circles back to stress-test old assumptions against new context.

### 2. Cross-cutting changes that break distant tests

**Sessions:** 28, 32, 34, 43, 54, 57, 61, 62, 63, 73, 78, 88

Adding a column, renaming a concept, or changing a default cascades into 5-15 test failures in unrelated files.

- Agent-to-Hooper rename required coordination across ~50 files (S28)
- `create_team()` positional args broke when `color_secondary` was inserted between existing params (S32)
- `is_starter` was never wired through from the DB layer after the rename — a simple default-value oversight (S34)
- Every new ORM column needs a matching `_add_column_if_missing` call — missed repeatedly (S43)
- Default ruleset change (`playoff_semis_best_of=3`) broke season lifecycle tests that assumed 1-game series (S61)
- Minimum voting period change required updating 10+ tests that assumed immediate tally (S73)
- `extract_usage()` 3-to-4 tuple change broke all callers that unpacked as 3 values (S88)

**Root cause:** The local change is correct but the blast radius isn't proactively audited. `pytest` is used as the thinking tool rather than the verification tool.

### 3. Prompt examples treated as instructions

**Sessions:** 83, 92, 94

The AI interpreter followed prompt *examples* literally instead of the *intent*. Negative guardrails were less effective than positive direction.

- A player's creative proposal was reduced to parameter tweaks because every prompt example mapped creative language to parameters — the interpreter followed the examples, not the intent (S83)
- Reports should have been on Opus from the start — the editorial voice is the game's personality (S92)
- The early-season guard added "don't do X" rules without restructuring the prompt's energy — the model needs "find what's surprising" more than "don't use cliches" (S94)

**Root cause:** Prompts are written like code (precise, literal) when the model reading them treats examples as the strongest signal. Prompt QA doesn't get the same rigor as code QA.

### 4. Deploying without confirming context

**Sessions:** 35, 47, 72

Acting before asking "is now the right time?" or "is this the right scope?"

- Deployed while a live game was in progress — the deploy killed the presentation (S35)
- Started implementing a full Narrative Physics plan before the user redirected to the immediate broken-users fix (S47)
- Running a dev server that picked up `DISCORD_ENABLED=true` from the environment and connected to production Discord (S72)

**Root cause:** The task as stated is executed without pausing to assess situational context — what's running, who's affected, what's the actual priority.

### 5. Parallel agents on overlapping files

**Sessions:** 24, 27, 40, 53

Rate limits, permission denials, context exhaustion, and lucky non-conflicts when agents touch shared files.

- Hit the hourly rate limit with 3 background agents running simultaneously — agents couldn't self-correct lint/test issues (S24)
- Background agents couldn't write files due to permission denials — did valuable research but couldn't implement (S27)
- 9 agents in parallel on overlapping files worked by luck — each edited different functions in the same files (S40)
- Context ran out mid-session due to many large background agents running in parallel (S53)

**Root cause:** Parallel execution is powerful but work isn't partitioned by file ownership. When two agents need the same file, it's a coin flip.

---

## Structural Recommendations

These map directly to the failure patterns above. The common thread: **reactive posture when proactive investigation would have prevented the failure.**

### A. Proactive blast-radius audit (fixes pattern 2)

Before making any cross-cutting change (rename, new column, default change), automatically grep for all downstream consumers and list them *before writing a single line of code*. Not when tests fail — before starting. A full-codebase impact scan before any structural change.

### B. Periodic "Day-1 debt" sweeps (fixes pattern 1)

Every ~10 sessions, run a health check: scan for hardcoded assumptions, untested edge cases in old code, query patterns that don't match the current schema. The bugs in S56, S59, S80 were all findable by static analysis — nobody asked. A dedicated `/audit` skill could formalize this.

### C. Pre-deploy situational awareness (fixes pattern 4)

Before any deploy, automatically check: is a game in progress? Are there active SSE connections? Is this a dev environment connecting to production services? This should be a pre-deploy hook, not a hope.

### D. Prompt review as a first-class task (fixes pattern 3)

When writing AI prompts, test them against adversarial examples before shipping — not just the happy path. "What will the model do if the input is creative? Weird? Minimal?" Generate those test cases proactively. Treat prompt QA like test QA.

### E. Smarter parallel partitioning (fixes pattern 5)

When running parallel agents, pre-compute a file-ownership map and assign non-overlapping file sets to each agent. If two tasks need the same file, they run sequentially. Plan the partition before launching agents.

### F. Post-change regression prediction

After any change, before running tests, predict which tests will break and why. This forces thinking about the dependency graph rather than using `pytest` as the discovery mechanism. If the failures can't be predicted, the change isn't understood well enough.

---

## The Meta-Observation

Most of these failures share a single root: **reactivity where proactivity was needed.** Waiting for tests to fail, deploys to break, prompts to misbehave. The information to prevent these failures was always available — it just wasn't sought.

The unlock isn't more tools or more agents. It's **standing permission to investigate before acting and to flag concerns before they become bugs.** The cost of a 30-second grep is always less than the cost of debugging a production failure.

---

*Compiled 2026-02-16 from dev logs spanning Sessions 24-100.*
