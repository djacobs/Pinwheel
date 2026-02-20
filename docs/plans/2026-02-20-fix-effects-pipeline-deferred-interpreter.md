# Fix Effects Pipeline + Deferred Interpreter

## Context

Two critical production bugs prevent the governance system from working as designed:

**Bug 1: Effects never register.** Every proposal that passes a vote has its game effects silently discarded. The v2 interpretation (with structured `EffectSpec` data) is computed by the AI interpreter but never persisted to the event store. When the game loop tallies votes later, it can't find any effects data. Zero `effect.*` events exist in production. This means proposals like "each pass before a shot adds 1 point" pass democratically but nothing changes in the simulation.

**Bug 2: Deferred proposals get permanently stuck.** When the AI interpreter fails, proposals are queued as `proposal.pending_interpretation` for background retry. But: (a) the retry ticker only checks the active season, so proposals from completed seasons are orphaned; (b) the 4-hour expiry never fires for orphaned proposals; (c) tokens are permanently lost. JudgeJedd has 2 stuck proposals with spent tokens and zero visibility on any admin page.

---

## Track A: Fix Effects Pipeline

### A1. Persist `effects_v2` in the proposal event payload

**File:** `src/pinwheel/core/governance.py` — `submit_proposal()` (lines 237-245)

When `interpretation_v2` is provided, add its effects to the event payload. Don't change the `Proposal` model — just enrich the payload dict after `model_dump()`:

```python
payload = proposal.model_dump(mode="json")
if interpretation_v2 is not None:
    payload["effects_v2"] = [e.model_dump(mode="json") for e in interpretation_v2.effects]
    payload["interpretation_v2_confidence"] = interpretation_v2.confidence
    payload["interpretation_v2_impact"] = interpretation_v2.impact_analysis
```

This is the minimal fix — the payload already accepts arbitrary keys, so no model change needed.

Also apply the same fix to `confirm_proposal()` (line 337) where `proposal.model_dump()` is used for the `proposal.flagged_for_review` event.

### A2. Extract effects during game loop tally

**File:** `src/pinwheel/core/game_loop.py` — around lines 897-932

After reconstructing proposals from submitted events, build the `effects_v2_by_proposal` map:

```python
effects_v2_by_proposal: dict[str, list[EffectSpec]] = {}
for se in submitted_events:
    pid = se.payload.get("id", se.aggregate_id)
    if pid in seen_ids:
        v2_effects = get_proposal_effects_v2(se.payload)
        if v2_effects:
            effects_v2_by_proposal[pid] = v2_effects
```

Then pass it to `tally_governance_with_effects()`:

```python
new_ruleset, round_tallies = await tally_governance_with_effects(
    ...
    effects_v2_by_proposal=effects_v2_by_proposal,
)
```

Import `get_proposal_effects_v2` from `pinwheel.core.governance` (already exists at line 954).

### A3. Fix the `_extract_effects_from_proposal()` stub

**File:** `src/pinwheel/core/governance.py` — lines 937-951

This function is currently a stub that always returns `[]`. It should be the fallback for proposals that don't have `effects_v2` in the payload. Since A2 handles extraction at the game_loop level, this stub can remain as-is (it's only called when `effects_v2_by_proposal` doesn't have data for a proposal, which would be legacy proposals). No change needed here — A1+A2 solve the pipeline.

### A4. Tests

- **Test that `submit_proposal()` persists `effects_v2`** — mock a `ProposalInterpretation` with effects, call `submit_proposal()`, verify the event payload contains `effects_v2`.
- **Test that the game loop builds `effects_v2_by_proposal`** — create a proposal with effects in its submitted event, run the tally path, verify `register_effects_for_proposal()` is called.
- **Test end-to-end** — submit a proposal with effects, confirm it, step a round, verify `effect.registered` events appear.

---

## Track B: Fix Deferred Interpreter

### B1. Query ALL seasons for pending interpretations (not just active)

**File:** `src/pinwheel/core/deferred_interpreter.py` — `tick_deferred_interpretations()` (lines 293-298)

Replace the single-season lookup with a multi-season scan:

```python
# Check all seasons for pending interpretations (proposals can be
# submitted in a season that later completes while still pending)
all_seasons = await repo.get_all_seasons()
for season in all_seasons:
    expired = await expire_stale_pending(repo, season.id)
    pending = await get_pending_interpretations(repo, season.id)
    # ... process each
```

This ensures proposals in completed seasons are found and either retried or expired.

### B2. Add max retry count with token refund

**File:** `src/pinwheel/core/deferred_interpreter.py` — `retry_pending_interpretation()` (lines 55-121)

Track retry count in the pending event or via a separate counter event. After N failures (e.g. 5 retries = 5 minutes), expire the pending interpretation and refund the token instead of retrying forever.

Simplest approach: count existing `proposal.interpretation_retry_failed` events for the aggregate_id. If count >= MAX_RETRIES, call `expire_stale_pending()` logic for that specific event:

```python
MAX_RETRIES = 10  # ~10 minutes of retries

# Count prior failed retries
retry_failed = await repo.get_events_by_type(
    season_id=pending.season_id,
    event_types=["proposal.interpretation_retry_failed"],
)
retry_count = sum(1 for e in retry_failed if e.aggregate_id == pending.aggregate_id)

if retry_count >= MAX_RETRIES:
    # Expire and refund instead of retrying
    ...
    return False
```

On each failed retry, append a `proposal.interpretation_retry_failed` event.

### B3. Add pending interpretations to admin roster

**File:** `src/pinwheel/api/admin_roster.py` — `admin_roster()` route (lines 72-108)

Query `proposal.pending_interpretation` and `proposal.interpretation_expired` events alongside submitted events. Show stuck proposals in JudgeJedd's governor row with a "PENDING INTERPRETATION" or "EXPIRED" badge.

Also update the template to display these with a distinctive style.

**File:** `templates/pages/admin_roster.html` — add a section for pending proposals per governor.

### B4. Manually refund JudgeJedd's stuck tokens

Write a one-time script (or run via `fly ssh console`) to:
1. Append `proposal.interpretation_expired` events for the 2 stuck proposals
2. Append `token.regenerated` events to refund JudgeJedd's PROPOSE tokens
3. The events should use the correct season_ids (Season 7: `58fa5666-8f8d-40ee-bfbb-fdeee4e86009`, Season "I ate the sandbox": `ab5505f2-136c-411a-8a8f-305d286ae0d7`)

### B5. Tests

- **Test retry expiry** — create a pending interpretation, simulate MAX_RETRIES failures, verify token is refunded.
- **Test cross-season scanning** — create pending interpretations in a completed season, verify the ticker finds and expires them.
- **Test admin roster visibility** — create a pending interpretation, verify it appears on the admin roster page.

---

## Execution Order

1. **A1** — Persist effects_v2 in payload (5 lines changed in governance.py)
2. **A2** — Build effects map in game_loop (10 lines added)
3. **B1** — Multi-season deferred ticker scan (refactor tick function)
4. **B2** — Max retry with token refund (add retry counting)
5. **B3** — Admin visibility for pending proposals
6. **B4** — Manual JudgeJedd token refund on prod
7. **A4 + B5** — Tests for both tracks

## Verification

1. Run existing tests: `uv run pytest -x -q`
2. Seed locally: `uv run python scripts/demo_seed.py seed`
3. Submit a proposal with effects via `demo_seed.py propose "each pass adds 1 point"`
4. Step a round: `uv run python scripts/demo_seed.py step 1`
5. Verify `effect.registered` events appear in the local database
6. Verify the deferred interpreter test cases pass (mock fallback → retry → expiry)
7. Check admin roster shows pending interpretations
