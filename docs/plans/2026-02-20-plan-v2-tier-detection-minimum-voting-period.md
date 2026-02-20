# Plan: V2 Tier Detection + Minimum Voting Period

## Context

Two governance bugs observed in production:

1. **V2 tier detection gap**: `/propose` already uses the V2 interpreter, but tier detection still runs through `to_rule_interpretation()` → `detect_tier()`, which returns Tier 5 for any proposal without a `parameter_change` effect. A proposal like "ball has to come back to the foul line extended" gets a `hook_callback` effect from V2, but the tier logic sees `parameter=None` → Tier 5 → flagged as "wild." This is wrong — the AI understood the proposal.

2. **No minimum voting period**: A proposal submitted at 10:43 passed at 11:00 with 1 vote. `tally_pending_governance()` tallies ALL confirmed proposals immediately. Governors need at least one tally cycle to see, discuss, and vote.

## Changes

### 1. Add `detect_tier_v2()` for V2 interpretations
**File:** `src/pinwheel/core/governance.py` (after `detect_tier()`, ~line 122)

New function that looks at `ProposalInterpretation.effects` directly:
- `parameter_change` → reuse existing tier 1-4 logic per parameter name
- `hook_callback` / `meta_mutation` / `move_grant` → Tier 3
- Only `narrative` effects → Tier 2
- No effects / `injection_flagged` / `rejection_reason` → Tier 5
- Compound proposals: highest tier wins

### 2. Update `_needs_admin_review()` for V2
**File:** `src/pinwheel/core/governance.py` (line 211)

Add optional `interpretation_v2: ProposalInterpretation | None = None` param:
- If V2 present with real effects and not injection-flagged → NOT wild (return False)
- If V2 present but empty effects or injection-flagged → wild
- Low confidence (< 0.5) still flagged regardless
- Legacy path (no V2) unchanged: tier 5+ = wild

### 3. Wire V2 tier into `submit_proposal()`
**File:** `src/pinwheel/core/governance.py` (line 148)

Add optional `interpretation_v2` param. When present, call `detect_tier_v2()` instead of `detect_tier()`.

### 4. Wire V2 into `confirm_proposal()`
**File:** `src/pinwheel/core/governance.py` (line 223)

Add optional `interpretation_v2` param, pass through to `_needs_admin_review()`.

### 5. Use `detect_tier_v2` in bot.py
**File:** `src/pinwheel/discord/bot.py` (line 2167)

Replace `tier = detect_tier(interpretation, ruleset)` with:
```python
if interpretation_v2 is not None:
    tier = detect_tier_v2(interpretation_v2, ruleset)
else:
    tier = detect_tier(interpretation, ruleset)
```

### 6. Pass V2 through ProposalConfirmView.confirm
**File:** `src/pinwheel/discord/views.py` (lines 110-131)

- Pass `interpretation_v2=self.interpretation_v2` to `submit_proposal()`
- Pass `interpretation_v2=self.interpretation_v2` to `confirm_proposal()`
- Pass `interpretation_v2=self.interpretation_v2` to `_needs_admin_review()`

### 7. Use `detect_tier_v2` in ReviseProposalModal
**File:** `src/pinwheel/discord/views.py` (~line 363)

Same pattern as bot.py: use `detect_tier_v2` when `interpretation_v2` available.

### 8. Add `proposal.first_tally_seen` event type
**File:** `src/pinwheel/models/governance.py` (line 13-35)

Add to `GovernanceEventType` literal. Also add `proposal.vetoed` and `proposal.flagged_for_review` which are already used but missing from the type.

### 9. Add minimum voting period deferral
**File:** `src/pinwheel/core/game_loop.py` (after line 826, before `if pending_proposal_ids:`)

After building `pending_proposal_ids`, insert deferral logic:
1. Query all `proposal.first_tally_seen` events for the season
2. For each pending proposal NOT in that set: emit `proposal.first_tally_seen` event, remove from pending list
3. For proposals already seen: proceed to tally normally

This ensures every proposal sits for at least one full tally cycle. The deferral is auditable (event trail with round number).

### 10. Tests
**File:** `tests/test_governance.py`

- `TestTierDetectionV2` — parameter_change tier logic, hook_callback=T3, meta_mutation=T3, narrative=T2, empty=T5, injection=T5, compound=max, move_grant=T3
- `TestNeedsAdminReviewV2` — hook_callback not flagged, injection still flagged, low confidence still flagged, legacy path unchanged
- `TestMinimumVotingPeriod` — proposal deferred on first tally, tallied on second tally, already-resolved not re-tallied

## Verification

1. `uv run pytest -x -q` — all tests pass
2. `uv run ruff check src/ tests/` — zero lint errors
3. Mock test: propose "ball resets to foul line" → should be Tier 3 (not Tier 5), no admin flag
4. Mock test: propose "make three pointers worth 5" → deferred round 1, tallied round 2
