# Plan: Fix the Governance User Journey (P0)

## Context

The app has never worked end-to-end for real users. After `/join`, governors enter a dead-end: they have zero governance tokens and cannot propose, so the governance loop never begins.

**Root cause:** `regenerate_tokens()` only runs in two places:
1. `start_new_season()` in `season.py:115` — runs before anyone has joined (0 governors enrolled)
2. `step_round()` governance interval in `game_loop.py:683-696` — every 3rd game round (45+ min with slow pace)

A governor who `/join`s has 0 PROPOSE, 0 AMEND, 0 BOOST tokens. They can't propose until round 3 fires. Most users give up.

Same issue in the self-heal flow (`_sync_role_enrollments`): re-enrolled governors after a DB reseed also get zero tokens.

## Changes

### 1. Grant initial tokens when a governor `/join`s

**File:** `src/pinwheel/discord/bot.py` — `_handle_join()`, after line 912

```python
await repo.enroll_player(player.id, target_team.id, season.id)

# Grant initial governance tokens so the governor can propose immediately
from pinwheel.core.tokens import regenerate_tokens
await regenerate_tokens(repo, player.id, target_team.id, season.id)

await session.commit()
```

Tokens are event-sourced (append-only). Calling `regenerate_tokens()` appends `token.regenerated` events. If the governor already has tokens from a prior window, they get an additional set — same as what happens at each governance interval. No duplication risk, no idempotency concern.

### 2. Grant tokens during self-heal enrollment sync

**File:** `src/pinwheel/discord/bot.py` — `_sync_role_enrollments()`, after line 604

```python
await repo.enroll_player(player.id, team.id, season.id)  # type: ignore[union-attr]
# Grant tokens for healed governor
from pinwheel.core.tokens import regenerate_tokens
await regenerate_tokens(repo, player.id, team.id, season.id)  # type: ignore[union-attr]
```

### 3. Add tests

**File:** `tests/test_discord.py` — Test that `/join` grants tokens:
- Mock the DB session to verify `regenerate_tokens` is called after enrollment
- OR: integration-style test using the existing test DB setup

**File:** `tests/test_governance.py` — Test mid-season propose flow:
- Create season, advance 1 round (so governance hasn't fired yet)
- Enroll new governor + regenerate tokens (simulating the /join fix)
- Verify `has_token(repo, governor_id, season_id, "propose")` returns True

## Files Modified

| File | Change |
|------|--------|
| `src/pinwheel/discord/bot.py` | Add `regenerate_tokens()` call after enrollment in `_handle_join()` (~line 912) and `_sync_role_enrollments()` (~line 604) |
| `tests/test_discord.py` | Test that `/join` triggers token grant |
| `tests/test_governance.py` | Test that mid-season governor has tokens and can propose |

## Verification

1. `uv run pytest -x -q` — all existing + new tests pass
2. `uv run ruff check src/ tests/` — zero lint errors
3. After deploy: new user `/join`s → `/tokens` shows 2 PROPOSE, 2 AMEND, 2 BOOST → `/propose` works
