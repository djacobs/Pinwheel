# Resilient Proposal Pipeline: Every Proposal Gets a Real Interpretation

## Context

Every interpreter call on production is timing out — 4 proposals in a row, 100% failure rate. The cascade (Sonnet 25s → retry 25s → Haiku 15s) exhausts all attempts, falls through to a keyword-matching mock. The mock produces bad interpretations that either confuse players or enact wrong rules.

The classifier (same API key, same network) succeeds every time at ~2s. The difference: the classifier has a 3-field Pydantic schema; the interpreter's `EffectSpec` has 28 fields including nested `dict[str, MetaValue | dict[str, MetaValue]]` union types. The `output_config` constrained decoding is almost certainly the cause — the transformed schema may forbid the model from populating `action_code` dicts correctly.

Goal: **Every proposal gets a real AI interpretation — and results in an impact on the game.** Even if that means Claude has to open tickets to build new features into the game (human in the loop: the admin reviews and implements custom mechanics). No proposal should ever be a dead end.

## Changes

### 1. Drop `output_config`, parse JSON from response text
**File:** `src/pinwheel/ai/interpreter.py`

Remove `output_config=pydantic_to_response_format(...)` from all interpreter call sites:
- `interpret_proposal_v2()` Sonnet call (~line 685)
- `interpret_proposal_v2()` Haiku fallback (~line 707)
- `_opus_escalate()` (~line 604)
- `interpret_proposal()` v1 (~line 142)
- `interpret_strategy()` (~line 328)

Add a Response Format section back to `INTERPRETER_V2_SYSTEM_PROMPT` (replacing the one-liner "Respond with a JSON object matching the provided schema"). This is purely an output formatting instruction — it tells the model what JSON shape to return. It does NOT limit what proposals the model can interpret or what effects it can produce. All 7 effect types, all hook points, all action primitives, custom_mechanic, basketball intelligence — everything that gives the model its interpretive range — stays exactly as-is. The Response Format is just the envelope.

Extract `_parse_json_response(text, model_class)` helper for the repeated strip-fences + `json.loads()` + Pydantic pattern.

### 2. Flag mock fallback results
**File:** `src/pinwheel/models/governance.py`

Add `is_mock_fallback: bool = False` to `ProposalInterpretation`. Set `True` in `interpret_proposal_v2()` when falling back to mock (~line 759). Callers can distinguish real interpretations from keyword matches.

### 3. Queue failed interpretations instead of showing mock
**File:** `src/pinwheel/discord/bot.py` (`_handle_propose`, ~line 2191)

After `interpret_proposal_v2()` returns, check `interpretation_v2.is_mock_fallback`. If true:
- Append a `proposal.pending_interpretation` event with: `raw_text`, `discord_user_id`, `discord_channel_id`, `season_id`, `governor_id`, `team_id`, `ruleset` snapshot, `token_cost`
- Edit the thinking message: "The Interpreter is overwhelmed right now. Your proposal has been queued — you'll get a DM when it's ready. Your PROPOSE token is reserved."
- Return (no confirm buttons, no mock embed)

Also: guard at top of `_handle_propose` — if governor already has a pending interpretation, tell them and return.

### 4. New event types
**File:** `src/pinwheel/models/governance.py`

Add to `GovernanceEventType`:
- `"proposal.pending_interpretation"` — queued for background retry
- `"proposal.interpretation_ready"` — retry succeeded
- `"proposal.interpretation_expired"` — gave up after max age

### 5. Background retry job
**New file:** `src/pinwheel/core/deferred_interpreter.py`

- `get_pending_interpretations(repo)` — find `pending_interpretation` events without a corresponding `ready` or `expired` event
- `retry_pending_interpretation(repo, pending, api_key)` — call `interpret_proposal_v2()`, on success append `interpretation_ready` event
- `expire_stale_pending(repo, max_age_hours=4)` — expire old items, refund tokens
- `tick_deferred_interpretations(engine, api_key, bot)` — scheduler entry point, called every 60s

### 6. DM player when ready
**File:** `src/pinwheel/core/deferred_interpreter.py`

When retry succeeds:
- `bot.fetch_user(discord_user_id)` → build interpretation embed → send DM with `ProposalConfirmView` (Confirm/Revise/Cancel)
- Player has 5 minutes to respond (existing view timeout)
- Edge cases: player left server → expire + refund; season ended → expire + refund; view timeout → `on_timeout` refunds token

### 7. Register scheduler job
**File:** `src/pinwheel/main.py`

Add `tick_deferred_interpretations` as a second APScheduler job (IntervalTrigger, 60s). Register it **unconditionally** — not gated behind `pinwheel_auto_advance`. Proposals should retry even in manual pace.

### 8. Add `on_timeout` to ProposalConfirmView
**File:** `src/pinwheel/discord/views.py`

Add `on_timeout()`: if `self.token_already_spent`, refund PROPOSE token via `token.regenerated` event. Covers the DM case where player doesn't respond.

### 9. Every proposal fires — eliminate inert custom_mechanic
**Files:** `src/pinwheel/ai/interpreter.py`, `src/pinwheel/core/effects.py`, `src/pinwheel/discord/bot.py`, `src/pinwheel/discord/embeds.py`

The current `custom_mechanic` is a dead end: it registers as "PENDING MECHANIC", injects `[Pending mechanic] {description}` into narrative, and does nothing mechanically. A player who proposes something creative and gets this result sees no impact. That's broken.

**Principle: the interpreter must ALWAYS produce at least one concrete firing effect (types 1-6).**

Even for wild proposals, there is almost always an approximation:
- "Shoot from half court after 3 in a row" → `hook_callback` at `sim.possession.pre`, condition: "scorer has 3+ consecutive makes", action: `modify_probability` with large boost. Not literally "half court" but the gameplay dynamic (streaks are rewarded) is real.
- "Players can tag out mid-quarter" → `hook_callback` at `sim.quarter.pre`, action: `modify_stamina` recovery. Not literal substitution, but captures the fatigue relief intent.
- "Make the ball on fire" → `parameter_change` on `stamina_drain_rate` + `narrative` instruction. Captures the danger/intensity intent.

**Prompt change in `INTERPRETER_V2_SYSTEM_PROMPT`:**

Replace the `custom_mechanic` type definition:
```
7. **custom_mechanic** — ONLY when types 1-6 cannot express the intent...
```
With:
```
7. **custom_mechanic** — Use ALONGSIDE types 1-6 when the full vision needs new code.
   EVERY proposal MUST include at least one concrete effect (types 1-6) that approximates
   the gameplay intent and fires immediately. If the ideal implementation needs code beyond
   what types 1-6 can express, ALSO include a custom_mechanic describing the full vision.
   The concrete effect gives players an immediate impact; the custom_mechanic is a request
   for the admin to build the complete version later.
   Never produce a custom_mechanic as the ONLY effect — there is always an approximation.
```

**Effect registration change in `effects.py`:**

When a proposal passes with both concrete effects + a custom_mechanic:
- Concrete effects register and fire normally (hook_callbacks, parameter_changes, etc.)
- The custom_mechanic registers with its existing "PENDING" label but also gets a narrative instruction: the `mechanic_observable_behavior` field becomes a narrative that fires at report hooks, so players see it referenced in game commentary even before full implementation

**Admin notification on custom_mechanic enactment:**

When `tally_governance_with_effects()` enacts a proposal containing a `custom_mechanic`:
- DM the admin with the full spec: `mechanic_description`, `mechanic_implementation_spec`, `mechanic_observable_behavior`
- Include a note: "The approximation effects are already live. The custom mechanic describes the full version — implement when ready."
- New event type: `"effect.implementation_requested"` — tracks that the admin has been notified

**New Discord command: `/activate-mechanic`** (admin only)
- Autocomplete from pending custom_mechanic effects
- Admin provides a `hook_point` + `action_code` (the real implementation) or just confirms the approximation is good enough
- Updates the RegisteredEffect: sets real hook_points and action_code, removes the "PENDING" label
- Appends `effect.activated` event
- Posts announcement: "A new mechanic is now live: {description}"

### 10. Tests

- `tests/test_messages_api.py` — remove `output_config` expectations, test JSON parsing
- `tests/test_deferred_interpreter.py` — **NEW**: pending creation, retry success, expiry + refund, no double-processing, governor blocked while pending

## Files Modified
- `src/pinwheel/ai/interpreter.py` — drop `output_config`, add Response Format, `_parse_json_response`, `is_mock_fallback`, update custom_mechanic prompt instructions
- `src/pinwheel/models/governance.py` — `is_mock_fallback` field, new event types (deferred + `effect.activated` + `effect.implementation_requested`)
- `src/pinwheel/discord/bot.py` — detect mock fallback, queue pending, pending guard, `/activate-mechanic` command, admin DM on custom_mechanic enactment
- `src/pinwheel/discord/views.py` — `on_timeout` for token refund
- `src/pinwheel/core/deferred_interpreter.py` — **NEW**: background retry + DM delivery
- `src/pinwheel/core/effects.py` — custom_mechanic fires narrative from `mechanic_observable_behavior`, activate_custom_mechanic function
- `src/pinwheel/core/governance.py` — admin notification after custom_mechanic passes tally
- `src/pinwheel/main.py` — register deferred interpreter scheduler job
- `tests/test_messages_api.py` — update for JSON parsing
- `tests/test_deferred_interpreter.py` — **NEW**: deferred flow tests
- `tests/test_discord.py` — `/activate-mechanic` tests

## Verification
1. `uv run pytest -x -q` — all tests pass
2. `uv run ruff check src/ tests/` — lint clean
3. Deploy and submit a test proposal — should succeed immediately (Layer 1 fix)
4. To test Layer 2: temporarily set interpreter timeout to 1s, verify queued message + background retry DM
