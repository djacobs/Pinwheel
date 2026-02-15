# Governance Event Store

Reference documentation for all event types in Pinwheel Fates' append-only governance event store.

**Last updated:** 2026-02-15

---

## Overview

All governance state in Pinwheel Fates is derived from an append-only event store. Events are stored in the `governance_events` table via `Repository.append_event()`. Token balances, proposal outcomes, vote tallies, trade history, and rule changes are all computed from event streams, never stored as mutable state.

This document enumerates every event type, its source, payload schema, and consumers.

---

## Event Store Schema

**Table:** `governance_events`
**ORM Class:** `GovernanceEventRow` in `src/pinwheel/db/models.py`

| Column | Type | Description |
|--------|------|-------------|
| `id` | TEXT PK | Auto-generated UUID |
| `event_type` | TEXT NOT NULL | Event type string (see sections below) |
| `aggregate_id` | TEXT NOT NULL | Entity this event belongs to (proposal ID, governor ID, etc.) |
| `aggregate_type` | TEXT NOT NULL | Entity type: `proposal`, `token`, `rule_change`, `trade`, `strategy`, `effect` |
| `season_id` | TEXT NOT NULL | Season scope |
| `governor_id` | TEXT | Governor who triggered the event (nullable) |
| `team_id` | TEXT | Team context (nullable) |
| `round_number` | INTEGER | Round when event occurred (nullable) |
| `sequence_number` | INTEGER NOT NULL | Monotonically increasing for ordering |
| `payload` | JSON NOT NULL | Event-specific data |
| `created_at` | TEXT | Timestamp |

---

## Proposal Lifecycle Events

| Event Type | aggregate_type | Source | Description |
|-----------|---------------|--------|-------------|
| `proposal.submitted` | `proposal` | `core/governance.py::submit_proposal()` | Governor submitted a rule change proposal |
| `proposal.confirmed` | `proposal` | `core/governance.py::confirm_proposal()` | Governor confirmed the AI interpretation |
| `proposal.flagged_for_review` | `proposal` | `core/governance.py::confirm_proposal()` | Wild proposal flagged for admin audit |
| `proposal.review_cleared` | `proposal` | `core/governance.py::admin_clear_proposal()` | Admin cleared a flagged proposal |
| `proposal.vetoed` | `proposal` | `core/governance.py::admin_veto_proposal()` | Admin vetoed a wild proposal |
| `proposal.cancelled` | `proposal` | `core/governance.py::cancel_proposal()` | Governor cancelled their own proposal |
| `proposal.amended` | `proposal` | `core/governance.py::amend_proposal()` | Governor submitted an amendment |
| `proposal.passed` | `proposal` | `core/governance.py::tally_governance()` | Proposal passed the vote |
| `proposal.failed` | `proposal` | `core/governance.py::tally_governance()` | Proposal failed the vote |

### Payload Schemas

**`proposal.submitted`:**
```json
{
  "id": "<proposal_id>",
  "season_id": "...",
  "governor_id": "...",
  "team_id": "...",
  "window_id": "...",
  "raw_text": "Make three-pointers worth 5 points",
  "sanitized_text": "Make three-pointers worth 5 points",
  "interpretation": {
    "parameter": "three_point_value",
    "new_value": 5,
    "old_value": 3,
    "impact_analysis": "...",
    "confidence": 0.95
  },
  "tier": 1,
  "token_cost": 1,
  "status": "submitted"
}
```

**`proposal.confirmed`:**
```json
{
  "proposal_id": "<proposal_id>"
}
```

**`proposal.flagged_for_review`:** Full `Proposal` model dump (same as `proposal.submitted`).

**`proposal.vetoed`:** Full `Proposal` model dump plus `"veto_reason": "..."`.

**`proposal.cancelled`:**
```json
{
  "proposal_id": "<proposal_id>"
}
```

**`proposal.amended`:**
```json
{
  "id": "<amendment_id>",
  "proposal_id": "<proposal_id>",
  "governor_id": "...",
  "amendment_text": "...",
  "new_interpretation": { ... }
}
```

**`proposal.passed` / `proposal.failed`:**
```json
{
  "proposal_id": "<proposal_id>",
  "weighted_yes": 2.5,
  "weighted_no": 1.0,
  "total_weight": 3.5,
  "passed": true,
  "threshold": 0.5,
  "yes_count": 3,
  "no_count": 1
}
```

---

## Vote Events

| Event Type | aggregate_type | Source | Description |
|-----------|---------------|--------|-------------|
| `vote.cast` | `proposal` | `core/governance.py::cast_vote()` | Governor voted on a proposal |

### Payload Schema

```json
{
  "id": "<vote_id>",
  "proposal_id": "<proposal_id>",
  "governor_id": "...",
  "team_id": "...",
  "vote": "yes",
  "weight": 0.5,
  "boost_used": false
}
```

**Vote weight:** Each team's total weight = 1.0, divided equally among active governors on that team. `boost_used: true` doubles the weight of the vote.

---

## Rule Change Events

| Event Type | aggregate_type | Source | Description |
|-----------|---------------|--------|-------------|
| `rule.enacted` | `rule_change` | `core/governance.py::tally_governance()` | Rule change enacted from a passing proposal |
| `rule.rolled_back` | `rule_change` | `core/governance.py::tally_governance()` | Rule change failed validation and was rolled back |

### Payload Schemas

**`rule.enacted`:**
```json
{
  "parameter": "three_point_value",
  "old_value": 3,
  "new_value": 5,
  "source_proposal_id": "<proposal_id>",
  "round_enacted": 7
}
```

**`rule.rolled_back`:**
```json
{
  "reason": "validation_error",
  "proposal_id": "<proposal_id>",
  "parameter": "three_point_value"
}
```

---

## Token Economy Events

| Event Type | aggregate_type | Source | Description |
|-----------|---------------|--------|-------------|
| `token.spent` | `token` | `core/governance.py`, `core/tokens.py` | Governor spent tokens |
| `token.regenerated` | `token` | `core/governance.py`, `core/tokens.py` | Tokens added to governor |

### Payload Schemas

**`token.spent`:**
```json
{
  "token_type": "propose",
  "amount": 1,
  "reason": "proposal:<proposal_id>"
}
```

Token types: `"propose"`, `"amend"`, `"boost"`.
Reason examples: `"proposal:<id>"`, `"amendment:<id>"`, `"boost:<id>"`, `"trade:<id>"`.

**`token.regenerated`:**
```json
{
  "token_type": "propose",
  "amount": 1,
  "reason": "round_regeneration"
}
```

Reason examples: `"refund"`, `"admin_veto_refund"`, `"round_regeneration"`, `"trade:<id>"`.

**Important:** Token balances are **never stored as mutable state**. `get_token_balance()` computes the current balance by replaying all `token.spent` and `token.regenerated` events for a governor in a season.

---

## Trade Events (Token Trades)

| Event Type | aggregate_type | Source | Description |
|-----------|---------------|--------|-------------|
| `trade.offered` | `trade` | `core/tokens.py::offer_trade()` | Governor proposed a token trade |
| `trade.accepted` | `trade` | `core/tokens.py::accept_trade()` | Trade accepted by target |
| `trade.rejected` | `trade` | `discord/views.py::TradeOfferView` | Trade declined by target |

### Payload Schemas

**`trade.offered` / `trade.accepted`:**
```json
{
  "trade_id": "...",
  "from_governor_id": "...",
  "from_team_id": "...",
  "to_governor_id": "...",
  "to_team_id": "...",
  "offer_type": "propose",
  "offer_amount": 1,
  "request_type": "boost",
  "request_amount": 1,
  "season_id": "..."
}
```

**`trade.rejected`:**
```json
{
  "trade_id": "...",
  "from_governor_id": "...",
  "to_governor_id": "...",
  "reason": "declined"
}
```

---

## Hooper Trade Events

| Event Type | aggregate_type | Source | Description |
|-----------|---------------|--------|-------------|
| `hooper_trade.proposed` | `trade` | `core/tokens.py::propose_hooper_trade()` | Governor proposed trading hoopers |
| `hooper_trade.executed` | `trade` | `core/tokens.py::execute_hooper_trade()` | Hooper trade executed (hoopers swapped teams) |

### Payload Schemas

**`hooper_trade.proposed`:**
```json
{
  "trade_id": "...",
  "proposer_governor_id": "...",
  "proposer_team_id": "...",
  "offer_hooper_id": "...",
  "offer_hooper_name": "...",
  "target_team_id": "...",
  "request_hooper_id": "...",
  "request_hooper_name": "...",
  "season_id": "..."
}
```

**`hooper_trade.executed`:**
```json
{
  "trade_id": "...",
  "offer_hooper_id": "...",
  "offer_hooper_name": "...",
  "offer_team_id": "...",
  "request_hooper_id": "...",
  "request_hooper_name": "...",
  "request_team_id": "..."
}
```

---

## Strategy Events

| Event Type | aggregate_type | Source | Description |
|-----------|---------------|--------|-------------|
| `strategy.set` | `strategy` | `discord/views.py::StrategyConfirmView` | Governor set raw strategy text |
| `strategy.interpreted` | `strategy` | `discord/views.py::StrategyConfirmView` | AI interpreted strategy into parameters |

### Payload Schemas

**`strategy.set`:**
```json
{
  "team_id": "...",
  "governor_id": "...",
  "raw_text": "Push the pace and shoot threes",
  "season_id": "..."
}
```

**`strategy.interpreted`:**
```json
{
  "team_id": "...",
  "governor_id": "...",
  "strategy": {
    "three_point_bias": 0.3,
    "mid_range_bias": -0.1,
    "at_rim_bias": -0.2,
    "defensive_intensity": 0.0,
    "pace_modifier": -0.15,
    "substitution_threshold_modifier": 0.0,
    "raw_text": "Push the pace and shoot threes",
    "confidence": 0.85
  },
  "season_id": "..."
}
```

---

## Effects System Events

| Event Type | aggregate_type | Source | Description |
|-----------|---------------|--------|-------------|
| `effect.registered` | `effect` | `core/effects.py::register_effects_for_proposal()` | V2 effect registered from a passing proposal |
| `effect.expired` | `effect` | `core/effects.py::persist_expired_effects()` | Effect's duration expired |

### Payload Schemas

**`effect.registered`:**
```json
{
  "effect_id": "...",
  "proposal_id": "...",
  "effect_type": "parameter_change",
  "hook_point": "sim.possession.pre",
  "description": "All three-pointers are worth 5 points",
  "duration": "permanent",
  "duration_rounds": null,
  "registered_at_round": 7,
  "parameter": "three_point_value",
  "new_value": 5,
  "old_value": 3
}
```

Additional optional fields: `target_type`, `target_selector`, `meta_field`, `meta_value`, `meta_operation`, `condition`, `action_code`, `narrative_instruction`.

**`effect.expired`:**
```json
{
  "effect_id": "...",
  "expired_at_round": 12
}
```

---

## Event Read Patterns

These are the event types queried via `Repository.get_events_by_type()` throughout the codebase:

| Queried Types | Where | Purpose |
|--------------|-------|---------|
| `["proposal.submitted"]` | `game_loop.py`, `season.py`, `repository.py`, `api/governance.py`, `api/pages.py` | Reconstruct proposals for tally, profile, display |
| `["proposal.confirmed"]` | `game_loop.py`, `repository.py`, `api/pages.py` | Identify proposals ready for vote |
| `["proposal.passed", "proposal.failed"]` | `game_loop.py`, `repository.py`, `season.py`, `api/pages.py` | Determine proposal outcomes |
| `["proposal.vetoed"]` | `game_loop.py`, `repository.py` | Exclude vetoed proposals from tally |
| `["proposal.flagged_for_review"]` | `api/admin_review.py` | Build admin review queue |
| `["proposal.review_cleared", "proposal.vetoed"]` | `api/admin_review.py` | Filter resolved flagged proposals |
| `["vote.cast"]` | `game_loop.py`, `season.py`, `repository.py`, `api/pages.py` | Gather votes for tally, profile |
| `["rule.enacted"]` | `game_loop.py`, `season.py`, `api/governance.py`, `api/pages.py` | Rule change history for reports, archive |
| `["trade.completed"]` | `season.py::compute_awards()` | Count trades for Coalition Builder award |
| `["strategy.set"]` | `api/pages.py` | Display team strategy on profile page |
| `["strategy.interpreted"]` | `game_loop.py` | Load team strategies for simulation |
| `["effect.registered"]` | `core/effects.py::load_effect_registry()` | Reconstruct active effects at round start |
| `["effect.expired"]` | `core/effects.py::load_effect_registry()` | Filter out expired effects |

---

## EventBus Events (Transient, Not Persisted)

The EventBus (`core/event_bus.py`) is an in-process pub/sub system for real-time notifications. These events are **not stored** in the governance event store. They are transient signals consumed by SSE clients and the Discord bot.

| EventBus Event | Source | Purpose |
|---------------|--------|---------|
| `game.completed` | `game_loop.py` | Notify clients a game finished |
| `round.completed` | `game_loop.py` | Round fully processed |
| `report.generated` | `scheduler_runner.py` | A report was stored |
| `governance.window_closed` | `scheduler_runner.py` | Governance tally completed |
| `presentation.game_starting` | `presenter.py` | Game replay beginning |
| `presentation.possession` | `presenter.py` | Live play-by-play for SSE |
| `presentation.game_finished` | `presenter.py` | Game replay completed |
| `presentation.round_finished` | `presenter.py` | Round replay completed |
| `season.regular_season_complete` | `game_loop.py` | All regular-season games played |
| `season.tiebreaker_games_generated` | `season.py` | Tiebreaker games scheduled |
| `season.phase_changed` | `season.py` | Season transitioned to a new phase |
| `season.semifinals_complete` | `game_loop.py` | Both semi series decided |
| `season.playoffs_complete` | `game_loop.py` | Champion determined |
| `season.championship_started` | `season.py` | Championship phase entered |
| `season.offseason_started` | `season.py` | Offseason governance window opened |
| `season.offseason_closed` | `season.py` | Offseason window closed |

---

## Known Gaps and Inconsistencies

### Gap 1: `trade.completed` vs `trade.accepted` Mismatch

`compute_awards()` in `season.py` queries for `["trade.completed"]` events, but `accept_trade()` in `tokens.py` emits `"trade.accepted"`. The Coalition Builder award always shows 0 trades.

**Fix:** Change the query in `compute_awards()` to use `"trade.accepted"`.

### Gap 2: No Event for Governor Enrollment

When a governor joins a team via `/join`, enrollment is stored directly in `PlayerRow`. No governance event is emitted. There is no audit trail of when governors joined, and the event store cannot reconstruct enrollment history.

**Recommendation:** Add an `enrollment.joined` event type.

### Gap 3: No Event for Season Creation

`start_new_season()` creates a season and carries over teams but emits no governance events. The season's existence is only discoverable via the `seasons` table.

**Recommendation:** Add a `season.created` event.

### Gap 4: No Event for Bio Submission

The `/bio` command updates a hooper's backstory directly via `update_hooper_backstory()` without any event store record.

**Recommendation:** Add a `hooper.bio_updated` event.

### Gap 5: Payload Inconsistency

Some events use `proposal_id` as a top-level key (e.g., `proposal.confirmed`), while others embed the full model dump where `id` is the proposal ID (e.g., `proposal.submitted`). This requires different extraction logic in consuming code: `e.payload.get("proposal_id", e.aggregate_id)`.

**Recommendation:** Standardize on always including `proposal_id` as a top-level key in proposal-related events, even when the full model dump is also present.

---

## Source Files

| File | Role |
|------|------|
| `src/pinwheel/db/models.py` | `GovernanceEventRow` ORM model |
| `src/pinwheel/db/repository.py` | `append_event()`, `get_events_by_type()`, `get_events_for_aggregate()` |
| `src/pinwheel/core/governance.py` | Proposal lifecycle, voting, tallying (writes proposal.*, vote.*, rule.* events) |
| `src/pinwheel/core/tokens.py` | Token economy, trading (writes token.*, trade.* events) |
| `src/pinwheel/core/effects.py` | Effect registration and expiry (writes effect.* events) |
| `src/pinwheel/discord/views.py` | Strategy confirmation (writes strategy.* events), trade rejection |
| `src/pinwheel/core/event_bus.py` | Transient pub/sub (NOT the governance event store) |
