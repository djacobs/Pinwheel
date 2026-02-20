# Fix: Duplicate Discord Channels and Notifications

## Context

Multiple Fly.io machines can run simultaneously (during rolling deploys or autoscaling). Each machine runs its own Discord bot + APScheduler + in-memory EventBus. When two machines fire `tick_round` at the same cron tick, both simulate the same round and both bots post the same notifications to Discord. During concurrent startup, both bots race to create team channels, producing duplicates (see: two `st-johns-herons` channels).

The fix: a DB-level distributed lock so only one machine executes `tick_round` at a time. Since the EventBus is process-local, preventing duplicate `tick_round` execution also prevents duplicate Discord notifications (only the winning machine's bot receives events).

## Plan

### 1. Add `tick_round` distributed lock (`src/pinwheel/core/scheduler_runner.py`)

Use the existing `BotStateRow` table as a coordination point. At the start of `tick_round`:

```python
TICK_LOCK_KEY = "tick_round_lock"
TICK_LOCK_TIMEOUT_SECONDS = 300  # 5 minutes — stale lock recovery

async def _try_acquire_tick_lock(engine, machine_id: str) -> bool:
    """Atomically try to claim the tick_round lock. Returns True if acquired."""
    async with get_session(engine) as session:
        repo = Repository(session)
        existing = await repo.get_bot_state(TICK_LOCK_KEY)
        if existing:
            data = json.loads(existing)
            age = time.time() - data.get("acquired_at", 0)
            if age < TICK_LOCK_TIMEOUT_SECONDS:
                return False  # Lock held by another instance, not stale
            # Stale lock — take it over
        await repo.set_bot_state(TICK_LOCK_KEY, json.dumps({
            "machine_id": machine_id,
            "acquired_at": time.time(),
        }))
        return True

async def _release_tick_lock(engine, machine_id: str) -> None:
    """Release the lock only if we still own it."""
    async with get_session(engine) as session:
        repo = Repository(session)
        existing = await repo.get_bot_state(TICK_LOCK_KEY)
        if existing:
            data = json.loads(existing)
            if data.get("machine_id") == machine_id:
                # Delete the row instead of setting value to None
                from pinwheel.db.models import BotStateRow
                row = await session.get(BotStateRow, TICK_LOCK_KEY)
                if row:
                    await session.delete(row)
                    await session.flush()
```

In `tick_round()`:
- Generate a machine_id from `os.environ.get("FLY_MACHINE_ID", uuid4())`
- Call `_try_acquire_tick_lock()` before any work
- If lock not acquired, `logger.info("tick_round_skip: lock held")` and return
- Wrap the rest in `try/finally` to release the lock

### 2. Add Discord setup lock (`src/pinwheel/discord/bot.py`)

Same pattern for `_setup_server()` — prevent two bots from creating channels concurrently:

- Before creating channels, acquire `discord_setup_lock` (same BotStateRow pattern)
- If lock held and recent (< 60s), skip setup entirely (the other instance is handling it)
- Release after setup completes

### 3. Manual cleanup

After deploying:
- Delete the duplicate `st-johns-herons` channel in Discord
- Run `fly scale count 1` if multiple machines are running (optional — the lock handles it)

## Files to modify

1. `src/pinwheel/core/scheduler_runner.py` — add `tick_round` lock
2. `src/pinwheel/discord/bot.py` — add setup lock
3. `tests/test_scheduler_runner.py` — test lock acquisition/release/staleness
4. `tests/test_game_loop.py` — if affected

## Verification

1. `uv run pytest -x -q` passes
2. Deploy to Fly.io
3. Watch logs: second machine should log `tick_round_skip: lock held`
4. Only single notifications per event in Discord
5. No new duplicate channels created
