# Plan: Proposal Amendment Flow

**Date:** 2026-02-14
**Status:** Draft

## Context

The amendment system allows governors to modify active proposals before they are tallied. Instead of voting down a proposal and resubmitting, a governor can spend an AMEND token to replace the proposal's interpretation with a new one. This creates a richer governance dynamic: proposals are living documents that can be refined through negotiation, not just binary accept/reject.

This plan documents what exists today and identifies the missing pieces needed for a complete end-to-end amendment flow.

## What Exists Today

### Data models (complete)

**`Amendment` model** (`src/pinwheel/models/governance.py`, line 95-105):
```python
class Amendment(BaseModel):
    id: str = ""
    proposal_id: str
    governor_id: str
    amendment_text: str
    new_interpretation: RuleInterpretation | None = None
    token_cost: int = 1
    status: Literal["submitted", "confirmed", "cancelled"] = "submitted"
    created_at: datetime
```

**AMEND token type** (`src/pinwheel/models/tokens.py`, line 13):
`TokenType = Literal["propose", "amend", "boost"]`

**Token defaults** (`src/pinwheel/core/tokens.py`, line 19):
`DEFAULT_AMEND_PER_WINDOW = 2` — each governor gets 2 AMEND tokens per governance window.

**Proposal statuses** include `"amended"` (`src/pinwheel/models/governance.py`, line 83).

**GovernanceEventType** includes `"proposal.amended"` (line 23).

### Core logic (complete)

**`amend_proposal()`** (`src/pinwheel/core/governance.py`, lines 330-373):
- Accepts a repo, proposal, governor_id, team_id, amendment_text, and new_interpretation.
- Creates an `Amendment` object with a new UUID.
- Writes a `proposal.amended` event to the event store.
- Deducts 1 AMEND token via `token.spent` event.
- Updates the proposal's interpretation and sets status to `"amended"`.
- Returns the Amendment.

**Tally handles amended proposals** (`src/pinwheel/core/governance.py`, line 509):
`if proposal.status not in ("confirmed", "amended", "submitted"):` — amended proposals are eligible for tally, using whatever interpretation is current.

**Token balance tracking** (`src/pinwheel/core/tokens.py`):
`get_token_balance()` correctly tracks AMEND tokens via the event store.

### Token trading (complete)

**AMEND tokens are tradeable** (`src/pinwheel/discord/bot.py`, lines 173, 178):
The `/trade` command includes `AMEND` as a valid `offer_type` and `request_type`. Governors can trade AMEND tokens for PROPOSE or BOOST tokens.

### What is missing

| Missing Piece | Description | Impact |
|---------------|-------------|--------|
| **No `/amend` slash command** | There is no Discord command to amend a proposal. The `/propose` command creates new proposals; there is no command to modify an existing one. | Governors cannot amend proposals via Discord. |
| **No amendment UI flow** | There is no `AmendConfirmView` in `views.py`. The proposal confirm view only handles new proposals. | Even if a command existed, there is no interactive confirmation step. |
| **No AI re-interpretation for amendments** | `amend_proposal()` accepts a `new_interpretation` parameter, but there is no code path that calls the AI interpreter on amendment text to produce that interpretation. | The amendment flow would need to either run the AI interpreter on the amendment text or allow the amender to directly specify parameter changes (which breaks the natural-language-first principle). |
| **No amendment display in Discord** | The `/proposals` command shows proposal status and raw text, but does not show amendment history or the current interpretation after amendment. | Governors cannot see if a proposal has been amended or what the current version says. |
| **No amendment display on web** | The governance page shows proposals but does not render amendment history. | Same visibility gap on the web frontend. |
| **Amendment authorship rules unclear** | Can any governor amend any proposal, or only the original proposer? Can you amend your own proposal? The core logic does not enforce any authorship constraints — `amend_proposal()` accepts any `governor_id`. | Design decision needed. |

## Design Decisions

### Who can amend?

**Decision: Any governor can amend any confirmed proposal (except their own).** Rationale:
- Allowing only the proposer to amend makes AMEND tokens useless for other governors — it becomes just a "revise my own proposal" token.
- Allowing any governor to amend creates a negotiation dynamic: "I like your proposal but the value should be different."
- Preventing self-amendment ensures that amendments represent genuine cross-governor collaboration, not just a proposer fixing their own text.
- The AMEND token cost (1 per amendment) prevents frivolous amendments.

### Does an amendment reset votes?

**Decision: Yes, amending a proposal resets all existing votes.** Rationale:
- The amendment changes the interpretation that voters voted on. Keeping old votes would mean governors are bound to a version they never saw.
- This creates strategic tension: amending a popular proposal risks losing votes, but amending an unpopular one might save it.
- Implementation: when `proposal.amended` event is written, existing votes on that proposal are invalidated. `tally_votes()` only counts votes cast after the most recent amendment.

### Can a proposal be amended multiple times?

**Decision: Yes, up to a maximum of 3 amendments per proposal.** Rationale:
- Unlimited amendments would let a group keep changing a proposal indefinitely, preventing it from ever being tallied.
- A cap of 3 gives enough room for genuine refinement without enabling obstruction.
- Each amendment costs 1 AMEND token, so the total cost is self-limiting anyway.

### What happens to the AI interpretation?

**Decision: The amendment text is interpreted by the AI just like a new proposal.** The amender writes natural language (e.g., "change the three-point value to 4 instead of 5"), the AI produces a new `RuleInterpretation` or `ProposalInterpretation`, and the governor confirms via an interactive view. This maintains the natural-language-first principle.

## What Needs to Be Built

### 1. `/amend` Slash Command

**Implementation:**
- New slash command: `/amend proposal text` where `proposal` is an autocomplete field (showing open proposals) and `text` is the amendment description.
- Autocomplete reuses `_autocomplete_proposals()` from `bot.py`.
- Flow:
  1. Governor types `/amend [proposal] [text]`.
  2. Bot defers the interaction.
  3. Bot verifies: governor is enrolled, has AMEND tokens, is not the original proposer, proposal is in `confirmed` or `amended` status, proposal has < 3 amendments.
  4. Bot calls the AI interpreter on the amendment text (with the current ruleset context).
  5. Bot presents an `AmendConfirmView` showing the original proposal text, the amendment text, and the new AI interpretation.
  6. Governor confirms or cancels.
  7. On confirm: calls `amend_proposal()`, resets votes, posts to the governance channel.

### 2. `AmendConfirmView` (Discord UI)

**Implementation:**
- Similar to `ProposalConfirmView` but with amendment-specific messaging.
- Shows: original proposal text, amendment text, new interpretation, AMEND token cost, remaining AMEND tokens.
- Buttons: Confirm (green), Cancel (red).
- On confirm: calls `amend_proposal()`, writes `proposal.amended` event, deducts AMEND token.
- On cancel: no action, view dismissed.

### 3. Vote Reset on Amendment

**Implementation:**
- After `amend_proposal()` succeeds, write a `votes.reset` event (or similar) to mark the amendment timestamp.
- In `tally_pending_governance()`, when reconstructing votes for a proposal, only count votes cast after the most recent `proposal.amended` event for that proposal.
- Alternative: write `vote.invalidated` events for each existing vote. This is more explicit but creates more events. The timestamp-based filtering approach is simpler and consistent with the event-sourcing model.

### 4. Amendment Count Enforcement

**Implementation:**
- Before allowing an amendment, query `proposal.amended` events for the target proposal.
- If count >= 3, reject with message: "This proposal has already been amended 3 times."
- Store the amendment count or derive it from events (event-sourced).

### 5. Amendment History Display

**Discord:**
- Update the `/proposals` command to show amendment status: "Amended 2x — last amended by @Governor."
- Update proposal embeds to show amendment history when viewing a specific proposal.

**Web:**
- Update the governance page template to show amendment history for each proposal.
- Show the current interpretation (post-amendment) prominently, with a collapsible "Amendment History" section.

### 6. Self-Amendment Prevention

**Implementation:**
- In the `/amend` handler, check if `governor_id == proposal.governor_id`. If so, reject: "You cannot amend your own proposal. Ask another governor to amend it."

## Files to Create/Modify

| File | Change |
|------|--------|
| `src/pinwheel/discord/bot.py` | Add `/amend` slash command with `proposal` autocomplete and `text` parameter. Add `_handle_amend()` handler. |
| `src/pinwheel/discord/views.py` | Add `AmendConfirmView` with Confirm/Cancel buttons. |
| `src/pinwheel/discord/embeds.py` | Add `build_amendment_confirm_embed()`. Update `build_proposals_embed()` to show amendment status. |
| `src/pinwheel/core/governance.py` | Add `count_amendments()` helper. Add vote timestamp filtering to handle vote reset. |
| `src/pinwheel/core/game_loop.py` | Update `tally_pending_governance()` to filter votes by amendment timestamp. |
| `src/pinwheel/db/repository.py` | Add query for `proposal.amended` event count per proposal. |
| `src/pinwheel/models/governance.py` | No model changes needed — `Amendment` and event types already exist. |
| `templates/pages/governance.html` | Add amendment history display to proposal cards. |
| `tests/test_governance.py` | Test amendment flow, vote reset, amendment cap, self-amendment prevention. |
| `tests/test_discord.py` | Test `/amend` command behavior. |

## Testing Strategy

### Unit tests (governance logic)
1. **Basic amendment:** Submit a proposal. Amend it with a new interpretation. Verify the proposal's interpretation is updated, status is `"amended"`, and `proposal.amended` event is in the store.
2. **AMEND token deduction:** Amend a proposal. Verify 1 AMEND token was deducted from the amender's balance.
3. **Vote reset:** Submit a proposal. Cast 2 votes. Amend the proposal. Tally. Verify only votes cast after the amendment are counted (zero in this case, so the proposal should fail).
4. **Amendment cap:** Amend a proposal 3 times. Fourth amendment attempt fails with appropriate error.
5. **Self-amendment prevention:** Governor A submits a proposal. Governor A attempts to amend it. Verify rejection.
6. **Amended proposal in tally:** Submit a proposal with interpretation "three_point_value = 5". Amend to "three_point_value = 4". Vote yes. Tally. Verify the enacted rule uses value 4, not 5.
7. **Amendment count from events:** Submit and amend twice. Query amendment count. Verify it returns 2.

### Integration tests
8. **End-to-end Discord flow:** Governor A proposes. Governor B amends. Governor C votes on the amended version. Round tally enacts the amended interpretation.
9. **Amendment with v2 effects:** Propose a swagger effect. Amend it to change the swagger increment from 1 to 2. Verify the enacted effect uses the amended specification.
10. **Token balance consistency:** Start with 2 AMEND tokens. Amend twice. Verify balance is 0. Third amendment attempt fails.

### Edge cases
11. **Amend a proposal that has already been tallied:** Proposal passes. Attempt to amend. Verify rejection (status is `"passed"`, not `"confirmed"` or `"amended"`).
12. **Amend during active voting:** Votes exist. Amendment resets them. New votes are cast. Tally uses only new votes.
13. **Multiple governors amend the same proposal:** Governor B amends. Then Governor C amends the same proposal. Both amendments should succeed (within the cap). The final interpretation is Governor C's amendment.
