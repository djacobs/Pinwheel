# Test Suite Pruning Plan

## Goal

Reduce test count and runtime while preserving confidence in core behavior (simulation correctness, season lifecycle, governance integrity, auth/security, API contract behavior).

---

## Success Criteria

- Reduce total test count by **~15-30%** (target band, not strict).
- Reduce CI runtime meaningfully (target: **20%+** faster).
- Preserve confidence in:
  - simulation invariants and determinism
  - game loop and season transitions
  - governance/token correctness
  - auth/session security behavior
  - API/page route availability and essential rendering

---

## Pruning Principles

### Keep (high signal)

- State machine/transition tests (season and game loop).
- Persistence and event-ordering invariants.
- Auth/authz and security-critical behavior.
- Deterministic simulation invariants.
- API status/schema contract tests.

### Merge or Remove (low signal / high maintenance)

- Repeated copy/text assertions for the same page/flow.
- Repeated embed phrasing assertions in Discord tests.
- Large stochastic loops duplicated across files.
- Very granular micro-unit tests that restate library behavior.

---

## Priority Workstreams

## 1) Heavy stochastic duplication (highest runtime win)

### Candidate

- `tests/test_observe.py`

### Guidance

- Keep 1-2 representative distribution checks.
- Move very large batch tests (1000/500 loops) to `@pytest.mark.slow` nightly, or remove if already covered by simulation batch checks.
- Preserve one integrity check (e.g., box score totals).

### Why

- Biggest runtime reduction with low risk when invariants remain covered elsewhere.

---

## 2) Page text assertion reduction (highest count + maintenance win)

### Candidate

- `tests/test_pages.py`

### Guidance

- Keep route status checks and 1-2 sentinel assertions per page.
- Keep data-driven assertions (actual seeded entities appear).
- Remove excess branding/copy phrase checks likely to churn with UX edits.

### Why

- Reduces brittle failures from harmless copy changes.

---

## 3) Discord embed test consolidation

### Candidate

- `tests/test_discord.py`

### Guidance

- Keep command-path behavior tests (defer/followup/ephemeral/public routing).
- Keep event-routing tests (play-by-play vs big-plays).
- Consolidate embed-content tests to minimal schema/sentinel checks per embed type.
- Prefer parametrized tests over many near-identical examples.

### Why

- Very large file with many low-value string-level checks.

---

## 4) Effects/meta micro-test consolidation

### Candidate

- `tests/test_effects.py`

### Guidance

- Collapse tiny operation tests into behavior-focused bundles:
  - store lifecycle
  - dirty tracking
  - snapshot immutability
  - registry load/register/fire
- Keep end-to-end proposal -> effect registration -> hook fire coverage.

### Why

- Preserve subsystem confidence with fewer fragmented tests.

---

## 5) Duplicate helper coverage across modules

### Candidates

- `tests/test_narrative.py`
- `tests/test_season_lifecycle.py`
- `tests/test_memorial.py`

### Guidance

- Keep one canonical unit suite per helper.
- Keep integration checks at consumer level, but remove repeated helper edge-case matrices.

### Why

- Avoid triple-testing the same helper semantics.

---

## Execution Sequence

1. Prune `test_observe.py` first (largest runtime gains).
2. Prune `test_pages.py` and `test_discord.py` next (largest count/maintenance gains).
3. Consolidate `test_effects.py` micro-tests.
4. Remove duplicate helper-level tests across narrative/season/memorial.
5. Run targeted test groups after each phase, then full suite once.

---

## Validation Checklist

After each pruning phase:

- Fast group checks pass:
  - simulation/effects/governance groups
  - pages/auth/api groups
  - discord groups
- No critical-path regressions in:
  - season transitions
  - playoff progression
  - governance vote/tally paths
  - auth/session flows
- Full suite green before merge.

---

## Risk Controls

- Prefer merging over deleting when uncertain.
- Never remove tests covering known historical regressions without replacement.
- Keep at least one end-to-end path per major subsystem.
- If confidence drops, restore one targeted test instead of broad rollback.

---

## Deliverables

- Reduced test inventory with rationale in PR description.
- Runtime comparison (before/after).
- Short "coverage confidence" note listing retained invariants per subsystem.
