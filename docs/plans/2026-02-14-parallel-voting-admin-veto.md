# Plan: Parallel Voting + Admin Veto for Wild Proposals

## Context

Currently, "wild" proposals (Tier 5+ or AI confidence < 0.5) are **gated behind admin approval** before they can go to vote. This creates a bottleneck — JudgeJedd's Season 1 proposal sat in `pending_review` forever because the admin DM was missed, and the community never got to weigh in.

The fix: change admin review from an **approval gate** to a **veto power**. All proposals go to vote immediately. Wild proposals also notify the admin, who can veto before tally. If the admin doesn't act, the democratic process proceeds normally.

**New flow:**
```
Governor submits → AI interprets → Governor confirms
  ↓
ALL proposals → "confirmed" → public announcement → voting opens
  ↓
Wild proposals ALSO → admin DM with Veto/Clear buttons
  ↓
At tally time:
  - Vetoed proposals → skipped (refund tokens)
  - Everything else → tallied normally
```

## Changes

### 1. `src/pinwheel/core/governance.py` — `confirm_proposal()` (lines 203-230)

**Current:** Two paths — wild proposals get `pending_review`, normal get `confirmed`.
**New:** ALL proposals get `confirmed`. Wild proposals ALSO get a `proposal.flagged_for_review` event (audit trail).

```python
async def confirm_proposal(repo, proposal):
    # Always confirm — opens voting
    await repo.append_event(
        event_type="proposal.confirmed",
        aggregate_id=proposal.id, ...
    )
    proposal.status = "confirmed"

    # Wild proposals also get flagged for admin review
    if _needs_admin_review(proposal):
        await repo.append_event(
            event_type="proposal.flagged_for_review",
            aggregate_id=proposal.id, ...
        )
    return proposal
```

Remove the `pending_review` path entirely. The `_needs_admin_review()` function stays (still useful for deciding whether to send admin DM).

### 2. `src/pinwheel/discord/views.py` — `ProposalConfirmView.confirm()` (lines 81-189)

**Current:** Two UI paths — pending_review shows orange embed + no public announcement; confirmed shows green embed + public announcement.
**New:** Always show green "Proposal Submitted" embed + post public announcement. If wild, ALSO send admin DM.

- Remove the `if proposal.status == "pending_review"` branch (lines 121-146)
- Always run the public announcement path (lines 148-183)
- Add: if `_needs_admin_review(proposal)`, call `_notify_admin_for_review()` AND add a "(Wild — Admin may veto)" note to the public announcement embed

### 3. `src/pinwheel/discord/views.py` — `AdminReviewView` (lines 804-972)

**Current:** Approve/Reject buttons. Approve emits `proposal.confirmed`; Reject emits `proposal.rejected`.
**New:** Rename to **Veto/Clear** semantic:

- **"Clear" button** (was Approve): Emits `proposal.review_cleared` event. No functional change needed since proposal is already confirmed and votable. Sends DM to proposer: "Admin has cleared your proposal."
- **"Veto" button** (was Reject): Emits `proposal.vetoed` event + refunds tokens. Removes proposal from future tally. Sends DM to proposer with reason.

### 4. `src/pinwheel/core/governance.py` — add `admin_veto_proposal()`

New function replacing `admin_reject_proposal()`:
- Emits `proposal.vetoed` event
- Refunds PROPOSE tokens
- If proposal already passed/enacted, no-op (too late to veto)

Keep `admin_approve_proposal()` but rename to `admin_clear_proposal()` — just emits `proposal.review_cleared`.

### 5. `src/pinwheel/core/game_loop.py` — `tally_pending_governance()` (lines 355-455)

Add veto exclusion. After gathering confirmed proposals (line 370-373), also query for vetoed proposals:

```python
vetoed_events = await repo.get_events_by_type(
    season_id=season_id,
    event_types=["proposal.vetoed"],
)
vetoed_ids = {e.aggregate_id for e in vetoed_events}
```

Then filter them out when building the proposal list (around line 385):
```python
if pid not in resolved_ids and pid not in seen_ids and pid not in vetoed_ids:
```

### 6. `src/pinwheel/discord/embeds.py` — update announcement embed

- `build_proposal_announcement_embed()`: Add optional `wild: bool` parameter. If wild, add a field: "This is a wild proposal (Tier 5). Admin may veto before tally."

### 7. `src/pinwheel/db/repository.py` — update `get_governor_activity()`

Add `"proposal.vetoed"` to the status detection (alongside `pending_review` and `rejected`). Map to status `"vetoed"`.

### 8. `src/pinwheel/discord/embeds.py` — update `_STATUS_LABELS`

Add: `"vetoed": "Vetoed by Admin"` and `"flagged_for_review": "On the Floor (wild — admin may veto)"`.

## Files to modify

1. `src/pinwheel/core/governance.py` — confirm_proposal(), add admin_veto_proposal(), rename admin_approve → admin_clear
2. `src/pinwheel/discord/views.py` — ProposalConfirmView.confirm(), AdminReviewView, _notify_admin_for_review()
3. `src/pinwheel/core/game_loop.py` — tally_pending_governance() veto exclusion
4. `src/pinwheel/discord/embeds.py` — announcement embed wild badge, _STATUS_LABELS, admin review embed text
5. `src/pinwheel/db/repository.py` — get_governor_activity() and get_all_proposals() vetoed detection

## Backward compatibility

- Existing `proposal.pending_review` events in the DB (JudgeJedd's Season 1 proposal) remain valid — they just won't block anything anymore since that proposal is already rejected.
- `proposal.confirmed` events already exist for all normal proposals — no migration needed.
- The `pending_review` status string stays in `_STATUS_LABELS` for historical display.

## Verification

1. `uv run pytest -x -q` — all tests pass
2. `uv run ruff check src/ tests/` — zero lint errors
3. New tests:
   - Wild proposal goes to vote immediately (status = "confirmed", not "pending_review")
   - Admin veto excludes proposal from tally
   - Admin clear is a no-op on already-confirmed proposal
   - Vetoed proposal shows "Vetoed by Admin" in profile/proposals
   - Public announcement posted for wild proposals with badge
   - Tally skips vetoed proposals
4. Deploy and test `/propose` with a wild proposal in Discord — verify it goes to vote + admin gets DM
