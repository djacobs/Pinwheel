# Plan: Governance Event Store Enumeration

**Date:** 2026-02-14
**Status:** Draft (reference documentation)

## Overview

All governance state in Pinwheel Fates is derived from an append-only event store. Events are stored in the `governance_events` table via `Repository.append_event()`. This document enumerates every event type, its source, its payload schema, and which modules read it.

## Event Store Schema

**Table:** `governance_events` (mapped by `GovernanceEventRow` in `db/models.py`)

| Column | Type | Description |
|--------|------|-------------|
| `id` | TEXT PK | Auto-generated UUID |
| `event_type` | TEXT NOT NULL | Event type string (see below) |
| `aggregate_id` | TEXT NOT NULL | The entity this event belongs to (proposal ID, governor ID, etc.) |
| `aggregate_type` | TEXT NOT NULL | Entity type: "proposal", "token", "rule_change", "trade", "strategy", "effect" |
| `season_id` | TEXT NOT NULL | Season scope |
| `governor_id` | TEXT | Governor who triggered the event (nullable) |
| `team_id` | TEXT | Team context (nullable) |
| `round_number` | INTEGER | Round when event occurred (nullable) |
| `sequence_number` | INTEGER NOT NULL | Monotonically increasing sequence for ordering |
| `payload` | JSON NOT NULL | Event-specific data |
| `created_at` | TEXT | Timestamp |

## Complete Event Type Enumeration

### Proposal Lifecycle Events

| Event Type | Source | aggregate_type | Payload Schema | Description |
|------------|--------|---------------|---------------|-------------|
| `proposal.submitted` | `core/governance.py::submit_proposal()` | `proposal` | Full `Proposal` model dump: `{id, season_id, governor_id, team_id, window_id, raw_text, sanitized_text, interpretation: {parameter, new_value, old_value, impact_analysis, confidence, ...}, tier, token_cost, status}` | A governor submitted a rule change proposal |
| `proposal.confirmed` | `core/governance.py::confirm_proposal()` | `proposal` | `{proposal_id}` | Governor confirmed the AI interpretation; voting is now open |
| `proposal.flagged_for_review` | `core/governance.py::confirm_proposal()` | `proposal` | Full `Proposal` model dump (same as submitted) | Wild proposal (Tier 5+ or confidence < 0.5) flagged for admin audit |
| `proposal.review_cleared` | `core/governance.py::admin_clear_proposal()` | `proposal` | `{proposal_id}` | Admin cleared a flagged proposal (audit trail only) |
| `proposal.vetoed` | `core/governance.py::admin_veto_proposal()` | `proposal` | Full `Proposal` model dump + `{veto_reason}` | Admin vetoed a wild proposal |
| `proposal.cancelled` | `core/governance.py::cancel_proposal()` | `proposal` | `{proposal_id}` | Governor cancelled their own proposal |
| `proposal.amended` | `core/governance.py::amend_proposal()` | `proposal` | Full `Amendment` model dump: `{id, proposal_id, governor_id, amendment_text, new_interpretation}` | Governor submitted an amendment to an existing proposal |
| `proposal.passed` | `core/governance.py::tally_governance()` | `proposal` | `VoteTally` model dump: `{proposal_id, weighted_yes, weighted_no, total_weight, passed: true, threshold, yes_count, no_count}` | Proposal passed the vote |
| `proposal.failed` | `core/governance.py::tally_governance()` | `proposal` | `VoteTally` model dump: `{proposal_id, weighted_yes, weighted_no, total_weight, passed: false, threshold, yes_count, no_count}` | Proposal failed the vote |

### Vote Events

| Event Type | Source | aggregate_type | Payload Schema | Description |
|------------|--------|---------------|---------------|-------------|
| `vote.cast` | `core/governance.py::cast_vote()` | `proposal` | `Vote` model dump: `{id, proposal_id, governor_id, team_id, vote: "yes"\|"no", weight, boost_used}` | A governor voted on a proposal |

### Rule Change Events

| Event Type | Source | aggregate_type | Payload Schema | Description |
|------------|--------|---------------|---------------|-------------|
| `rule.enacted` | `core/governance.py::tally_governance()`, `tally_governance_with_effects()` | `rule_change` | `RuleChange` model dump: `{parameter, old_value, new_value, source_proposal_id, round_enacted}` | A rule change was enacted from a passing proposal |
| `rule.rolled_back` | `core/governance.py::tally_governance()`, `tally_governance_with_effects()` | `rule_change` | `{reason: "validation_error", proposal_id, [parameter]}` | A rule change failed validation and was rolled back |

### Token Economy Events

| Event Type | Source | aggregate_type | Payload Schema | Description |
|------------|--------|---------------|---------------|-------------|
| `token.spent` | `core/governance.py` (propose, amend, vote boost), `core/tokens.py` (trades) | `token` | `{token_type: "propose"\|"amend"\|"boost", amount, reason}` | Governor spent tokens. Reason examples: `"proposal:<id>"`, `"amendment:<id>"`, `"boost:<id>"`, `"trade:<id>"` |
| `token.regenerated` | `core/governance.py` (refunds), `core/tokens.py::regenerate_tokens()` | `token` | `{token_type: "propose"\|"amend"\|"boost", amount, reason}` | Tokens added. Reason examples: `"refund"`, `"admin_veto_refund"`, `"round_regeneration"`, `"trade:<id>"` |

### Trade Events (Token Trades)

| Event Type | Source | aggregate_type | Payload Schema | Description |
|------------|--------|---------------|---------------|-------------|
| `trade.offered` | `core/tokens.py::offer_trade()` | `trade` | `{trade_id, from_governor_id, from_team_id, to_governor_id, to_team_id, offer_type, offer_amount, request_type, request_amount, season_id}` | Governor proposed a token trade |
| `trade.accepted` | `core/tokens.py::accept_trade()` | `trade` | Same as `trade.offered` payload | Trade was accepted by the target governor |
| `trade.rejected` | `discord/views.py::TradeOfferView` | `trade` | `{trade_id, from_governor_id, to_governor_id, reason: "declined"}` | Trade was declined by the target governor |

### Hooper Trade Events

| Event Type | Source | aggregate_type | Payload Schema | Description |
|------------|--------|---------------|---------------|-------------|
| `hooper_trade.proposed` | `core/tokens.py::propose_hooper_trade()` | `trade` | `{trade_id, proposer_governor_id, proposer_team_id, offer_hooper_id, offer_hooper_name, target_team_id, request_hooper_id, request_hooper_name, season_id}` | Governor proposed trading hoopers between teams |
| `hooper_trade.executed` | `core/tokens.py::execute_hooper_trade()` | `trade` | `{trade_id, offer_hooper_id, offer_hooper_name, offer_team_id, request_hooper_id, request_hooper_name, request_team_id}` | Hooper trade was executed (hoopers swapped teams) |

### Strategy Events

| Event Type | Source | aggregate_type | Payload Schema | Description |
|------------|--------|---------------|---------------|-------------|
| `strategy.set` | `discord/views.py::StrategyConfirmView` | `strategy` | `{team_id, governor_id, raw_text, season_id}` | Governor set a raw strategy text |
| `strategy.interpreted` | `discord/views.py::StrategyConfirmView` | `strategy` | `{team_id, governor_id, strategy: {three_point_bias, mid_range_bias, at_rim_bias, defensive_intensity, pace_modifier, substitution_threshold_modifier, raw_text, confidence}, season_id}` | AI interpreted the strategy into structured parameters |

### Effects System Events

| Event Type | Source | aggregate_type | Payload Schema | Description |
|------------|--------|---------------|---------------|-------------|
| `effect.registered` | `core/effects.py::register_effects_for_proposal()` | `effect` | `{effect_id, proposal_id, effect_type, hook_point, description, duration, duration_rounds, registered_at_round, [parameter, new_value, old_value, target_type, target_selector, meta_field, meta_value, meta_operation, condition, action_code, narrative_instruction]}` | A v2 effect was registered from a passing proposal |
| `effect.expired` | `core/effects.py::persist_expired_effects()` | `effect` | `{effect_id, expired_at_round}` | An effect's duration expired and it was deactivated |

## Event Types Queried (Read Patterns)

These are the event types that code queries via `get_events_by_type()`:

| Queried Event Types | Where | Purpose |
|--------------------|-------|---------|
| `["proposal.submitted"]` | `game_loop.py`, `season.py`, `repository.py` | Reconstruct proposals for tally, profile, archive |
| `["proposal.confirmed"]` | `game_loop.py`, `repository.py` | Identify proposals ready for vote |
| `["proposal.passed", "proposal.failed"]` | `game_loop.py`, `repository.py`, `season.py` | Determine proposal outcomes |
| `["proposal.vetoed"]` | `game_loop.py`, `repository.py` | Exclude vetoed proposals from tally |
| `["proposal.pending_review", "proposal.rejected", "proposal.vetoed"]` | `repository.py` | Full lifecycle status for display |
| `["vote.cast"]` | `game_loop.py`, `season.py`, `repository.py` | Gather votes for tally, profile |
| `["rule.enacted"]` | `game_loop.py`, `season.py` | Rule change history for reports, archive |
| `["trade.completed"]` | `season.py::compute_awards()` | Count trades for Coalition Builder award |
| `["strategy.interpreted"]` | `game_loop.py` | Load team strategies for simulation |
| `["effect.registered"]` | `core/effects.py::load_effect_registry()` | Reconstruct active effects at round start |
| `["effect.expired"]` | `core/effects.py::load_effect_registry()` | Filter out expired effects |

## EventBus Events (Distinct from Event Store)

The EventBus (`core/event_bus.py`) is an in-process pub/sub system for real-time notifications. These are NOT persisted in the governance event store -- they are transient signals consumed by SSE clients and the Discord bot.

| EventBus Event | Source | Purpose |
|----------------|--------|---------|
| `game.completed` | `game_loop.py` | Notify clients a game finished |
| `round.completed` | `game_loop.py` | Round fully processed |
| `report.generated` | `scheduler_runner.py` | A report was stored |
| `season.regular_season_complete` | `game_loop.py` | All regular-season games played |
| `season.tiebreaker_games_generated` | `season.py` | Tiebreaker games scheduled |
| `season.phase_changed` | `season.py` | Season transitioned to a new phase |
| `season.semifinals_complete` | `game_loop.py` | Both semi series decided |
| `season.playoffs_complete` | `game_loop.py` | Champion determined |
| `season.championship_started` | `season.py` | Championship phase entered |
| `season.offseason_started` | `season.py` | Offseason governance window opened |
| `season.offseason_closed` | `season.py` | Offseason window closed |
| `presentation.possession` | `presenter.py` | Live play-by-play for SSE streaming |
| `presentation.game_finished` | `presenter.py` | Game presentation completed |

## Gaps and Recommendations

### 1. Missing Event: `trade.completed` vs `trade.accepted`

`compute_awards()` queries for `["trade.completed"]` events, but the actual event type emitted by `accept_trade()` is `"trade.accepted"`. This means the Coalition Builder award would always show 0 trades.

**Recommendation:** Fix the query in `compute_awards()` to use `"trade.accepted"` or add a `"trade.completed"` alias.

### 2. No Event for Governor Enrollment

When a governor joins a team via `/join`, the enrollment is stored directly in the `PlayerRow` table (`enroll_player()`). No governance event is emitted. This means:
- There is no audit trail of when governors joined.
- The event store cannot reconstruct enrollment history.

**Recommendation:** Add an `enrollment.joined` event type when `enroll_player()` is called from the Discord bot.

### 3. No Event for Season Creation

`start_new_season()` creates a season and carries over teams but emits no governance events. The season's existence is only discoverable via the `seasons` table, not the event store.

**Recommendation:** Add a `season.created` event for audit trail completeness.

### 4. No Event for Bio Submission

The `/bio` command updates a hooper's backstory directly via `update_hooper_backstory()` without any event store record.

**Recommendation:** Add a `hooper.bio_updated` event for audit trail and potential future features (bio change history).

### 5. Event Payload Inconsistency

Some events use `proposal_id` as a top-level key in the payload (e.g., `proposal.confirmed`), while others use the full model dump where `id` is the proposal ID (e.g., `proposal.submitted`). This requires different extraction logic in consuming code (`e.payload.get("proposal_id", e.aggregate_id)`).

**Recommendation:** Standardize on always including `proposal_id` as a top-level key in proposal-related events, even when the full model dump is also present.
