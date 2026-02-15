# Plan: Rate Limiting Proposals

**Date:** 2026-02-14
**Status:** Implemented
**Ref:** ACCEPTANCE_CRITERIA.md 5.4.5 — "Rate limiting prevents a single governor from submitting more proposals than their token balance allows"

## Context

The acceptance criteria call out rate limiting to prevent a single governor from flooding the governance pipeline with excessive proposals. The current system already has a natural throttle — PROPOSE tokens — but it has no enforcement beyond checking `has_token()` at submission time. There is no per-round cap, no time-based cooldown, and no protection against rapid-fire submissions that could congest the AI interpreter or overwhelm voters.

## What Exists Today

### Token-based throttle (partial)
- Each governor starts with 2 PROPOSE tokens per governance window (`DEFAULT_PROPOSE_PER_WINDOW = 2` in `src/pinwheel/core/tokens.py`).
- The `/propose` handler in `src/pinwheel/discord/bot.py` (line 1624) calls `has_token()` before proceeding. If the governor has 0 PROPOSE tokens, the command returns an error.
- `submit_proposal()` in `src/pinwheel/core/governance.py` (line 184-193) deducts PROPOSE tokens via `token.spent` events in the append-only event store.
- Higher-tier proposals cost more: `token_cost_for_tier()` returns 1 for tiers 1-4, 2 for tiers 5-6, and 3 for tier 7+.
- Tokens regenerate at each governance interval (every Nth round, controlled by `PINWHEEL_GOVERNANCE_INTERVAL`).

### What is NOT enforced
1. **No per-round submission cap.** The `proposals_per_window` field exists on `RuleSet` (default 3, range 1-10) but is never read or enforced anywhere in the submission flow. It is a governable parameter that does nothing.
2. **No time-based cooldown.** A governor can submit, confirm, submit, confirm in rapid succession — limited only by token balance and the 3-second Discord interaction timeout.
3. **No global throughput protection.** Every `/propose` triggers an Opus API call for interpretation. A coordinated group could fire many proposals simultaneously, creating expensive parallel AI calls.
4. **Token balance is checked but the submit+confirm flow has a race window.** The `has_token()` check happens at `/propose` time, but the actual `token.spent` event is appended during confirm (in `ProposalConfirmView`). Two rapid `/propose` calls could both pass the balance check before either deducts.

### Relevant files
- `/src/pinwheel/discord/bot.py` — `_handle_propose()` (line 1576+), the entry point
- `/src/pinwheel/discord/views.py` — `ProposalConfirmView`, where confirm triggers `submit_proposal()`
- `/src/pinwheel/core/governance.py` — `submit_proposal()`, `token_cost_for_tier()`
- `/src/pinwheel/core/tokens.py` — `has_token()`, `get_token_balance()`, `regenerate_tokens()`
- `/src/pinwheel/models/tokens.py` — `TokenBalance` model
- `/src/pinwheel/models/rules.py` — `proposals_per_window` field (unused)

## What Needs to Be Built

### 1. Enforce `proposals_per_window` (the governable cap)

The `proposals_per_window` parameter already exists on `RuleSet` and is already a governable tier-4 parameter (meta-governance). It just needs to be enforced.

**Implementation:**
- In `_handle_propose()` in `bot.py`, after the `has_token()` check, query the event store for `proposal.submitted` events by this governor in the current season.
- Count how many proposals the governor has submitted in the current governance window. A "governance window" is the interval between token regenerations, defined by `governance_interval` rounds.
- If the count >= `proposals_per_window` from the current ruleset, reject with a clear message: "You've used all your proposals for this governance window. Your limit resets after the next governance tally."
- This is a per-governor-per-window cap, not a per-round cap.

**Design note:** This makes the rate limit itself governable. Players could vote to increase or decrease it. A proposal to "allow 10 proposals per window" is a Tier 4 change requiring supermajority. This is on-brand for Pinwheel — the governors control the meta-rules.

### 2. Per-governor cooldown between submissions

**Implementation:**
- Add a simple in-memory dict on the bot: `_proposal_cooldowns: dict[str, float]` mapping governor_id to the timestamp of their last proposal submission.
- In `_handle_propose()`, check if the governor has submitted within the last N seconds. Suggested default: 60 seconds.
- This prevents rapid-fire submissions that waste AI interpreter capacity.
- The cooldown is NOT governable — it is a system-level protection against abuse, not a gameplay mechanic.

**Alternative considered:** Store cooldown in the event store. Rejected because this is an ephemeral rate-limit, not a governance state change. In-memory is sufficient; a bot restart clears all cooldowns, which is acceptable.

### 3. Fix the token balance race condition

**Implementation:**
- Move the `token.spent` event from the confirm step to the propose step. When a governor runs `/propose`, immediately deduct the PROPOSE token, then present the confirm/cancel UI.
- If the governor cancels, refund the token (as `cancel_proposal()` already does).
- This eliminates the window where two rapid `/propose` calls can both pass the balance check.

**Alternative considered:** Use a per-governor asyncio lock. Rejected because the in-memory lock would not survive bot restarts and does not work across multiple Fly.io instances. Event-store-level deduction is the correct fix.

### 4. AI interpreter call budget (optional, lower priority)

**Implementation:**
- Track in-flight Opus calls with a semaphore. If more than N proposal interpretations are in flight simultaneously, queue additional requests.
- Suggested limit: 3 concurrent interpreter calls.
- This prevents a burst of proposals from creating expensive parallel API calls.

**Alternative considered:** Reject proposals while the interpreter is busy. Rejected because it would create a poor UX — governors would see "the AI is busy, try again later" which feels like a system failure, not a game mechanic.

## Files to Create/Modify

| File | Change |
|------|--------|
| `src/pinwheel/discord/bot.py` | Add cooldown dict to `PinwheelBot.__init__()`. Add per-governor cooldown check and `proposals_per_window` enforcement to `_handle_propose()`. |
| `src/pinwheel/discord/views.py` | Move token deduction to propose-time (before confirm UI). Adjust cancel to include refund. |
| `src/pinwheel/core/governance.py` | Add `count_proposals_in_window()` helper that queries the event store. |
| `src/pinwheel/db/repository.py` | Add query method for counting `proposal.submitted` events by governor within a round range (governance window). |
| `tests/test_governance.py` | Test `proposals_per_window` enforcement. |
| `tests/test_discord.py` | Test cooldown behavior, test race condition fix. |

## Testing Strategy

### Unit tests
1. **Token exhaustion:** Submit 2 proposals with 2 PROPOSE tokens. Third attempt fails with clear error message. (This mostly works today but needs the race condition fix.)
2. **`proposals_per_window` enforcement:** Set `proposals_per_window = 1`. Submit 1 proposal. Second attempt is rejected even if governor has PROPOSE tokens remaining.
3. **Cooldown:** Submit a proposal. Immediately submit another. Second is rejected with "please wait" message. Wait 60+ seconds (mock time). Third succeeds.
4. **Window reset:** Submit proposals up to the limit. Advance rounds past the governance interval. Verify the count resets and new proposals are accepted.
5. **Governable rate limit:** Change `proposals_per_window` via a passing proposal. Verify the new limit is enforced on the next governance window.

### Integration tests
6. **Race condition:** Simulate two concurrent `/propose` calls from the same governor with 1 PROPOSE token. Verify exactly one succeeds and one fails.
7. **End-to-end flow:** Full propose->confirm->vote->tally cycle with rate limiting active. Verify normal governance still works.

### Acceptance criteria coverage
- AC 5.4.5: "attempt to submit 5 proposals with 2 PROPOSE tokens, assert last 3 fail" — covered by test 1 above.
