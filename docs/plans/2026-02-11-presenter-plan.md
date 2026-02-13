---
title: "feat: Game Presenter & Streaming System"
type: feat
date: 2026-02-11
---

# Game Presenter & Streaming System

## Problem

The simulation computes a full game in ~100ms. Fans experience it over 20-30 minutes. The presenter is the bridge — a stateful, time-based system that paces GameResult data through SSE to connected clients and coordinates with the AI commentary engine.

## Architecture

```
simulate_game() → GameResult (instant)
        │
        ▼
GamePresenter (asyncio task per game)
        │
        ├─ Loads GameResult
        ├─ Requests commentary batch from CommentaryEngine (ahead of current position)
        ├─ Paces possessions through EventBus at configured intervals
        ├─ Adjusts pace for dramatic moments
        │
        ▼
EventBus (in-memory pub/sub)
        │
        ├─ SSE endpoint reads from EventBus → pushes to connected HTTP clients
        ├─ Discord bot reads from EventBus → posts to channels
        └─ Presenter reads from EventBus → triggers reports when games complete
```

## EventBus

The EventBus is a simple in-memory async pub/sub. All real-time events flow through it.

```python
class EventBus:
    """In-memory async pub/sub for real-time events."""

    def __init__(self):
        self._subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)

    def subscribe(self, event_types: list[str] | None = None) -> AsyncIterator[GameEvent]:
        """Subscribe to events. None = all events."""
        queue = asyncio.Queue()
        key = ",".join(sorted(event_types)) if event_types else "*"
        self._subscribers[key].append(queue)
        return self._iterate(queue)

    async def publish(self, event: GameEvent) -> None:
        """Publish an event to all matching subscribers."""
        for key, queues in self._subscribers.items():
            if key == "*" or event.event_type in key.split(","):
                for queue in queues:
                    await queue.put(event)

    async def _iterate(self, queue: asyncio.Queue) -> AsyncIterator[GameEvent]:
        while True:
            event = await queue.get()
            yield event
```

The SSE endpoint reads from the EventBus:

```python
@app.get("/api/events/stream")
async def event_stream(
    games: bool = True,
    commentary: bool = True,
    governance: bool = True,
    reports: bool = True,
    game_id: str | None = None,
    team_id: str | None = None,
):
    event_types = []
    if games: event_types.extend(["game.possession", "game.move", "game.highlight", ...])
    if commentary: event_types.append("game.commentary")
    if governance: event_types.extend(["governance.open", "governance.close", ...])
    if reports: event_types.extend(["report.simulation", "report.governance", ...])

    async def generate():
        async for event in event_bus.subscribe(event_types or None):
            if game_id and event.game_id != game_id:
                continue
            if team_id and event.team_id != team_id:
                continue
            yield f"event: {event.event_type}\ndata: {event.model_dump_json()}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
```

## GamePresenter

One `GamePresenter` instance per active game. It's an asyncio task that reads a pre-computed `GameResult` and publishes events at a pace.

```python
class GamePresenter:
    """Paces a pre-computed GameResult through the EventBus."""

    def __init__(
        self,
        game_result: GameResult,
        event_bus: EventBus,
        commentary_engine: CommentaryEngine | None,
        pace: PaceMode,
    ):
        self.game = game_result
        self.bus = event_bus
        self.commentary = commentary_engine
        self.pace = pace
        self._commentary_cache: dict[int, CommentaryLine] = {}

    async def present(self) -> None:
        """Main presentation loop. Runs as an asyncio task."""
        # Pre-fetch first commentary batch
        if self.commentary:
            await self._prefetch_commentary(0, 8)

        for i, possession in enumerate(self.game.play_by_play):
            # Determine pace for this possession
            interval = self._compute_interval(i, possession)

            # Wait (this is where pacing happens)
            await asyncio.sleep(interval)

            # Publish possession event
            await self.bus.publish(GameEvent(
                event_type="game.possession",
                game_id=self.game.game_id,
                data=possession,
            ))

            # Publish commentary if available
            if i in self._commentary_cache:
                await self.bus.publish(GameEvent(
                    event_type="game.commentary",
                    game_id=self.game.game_id,
                    data=self._commentary_cache[i],
                ))

            # Publish move triggers
            for move in possession.triggered_moves:
                await self.bus.publish(GameEvent(
                    event_type="game.move",
                    game_id=self.game.game_id,
                    data=move,
                ))

            # Publish highlights (lead changes, runs, etc.)
            if self._is_highlight(i, possession):
                await self.bus.publish(GameEvent(
                    event_type="game.highlight",
                    game_id=self.game.game_id,
                    data=self._build_highlight(i, possession),
                ))

            # Quarter transitions
            if possession.is_quarter_end:
                await self.bus.publish(GameEvent(
                    event_type="game.quarter_end",
                    game_id=self.game.game_id,
                    data={"quarter": possession.quarter},
                ))

            # Elam activation
            if possession.elam_just_activated:
                await self.bus.publish(GameEvent(
                    event_type="game.elam_start",
                    game_id=self.game.game_id,
                    data={"target": self.game.elam_target},
                ))

            # Prefetch next commentary batch when buffer gets low
            if self.commentary and i % 5 == 0:
                await self._prefetch_commentary(i + 5, i + 13)

        # Game complete
        await self.bus.publish(GameEvent(
            event_type="game.result",
            game_id=self.game.game_id,
            data=self.game.to_summary(),
        ))
        await self.bus.publish(GameEvent(
            event_type="game.boxscore",
            game_id=self.game.game_id,
            data=self.game.box_scores,
        ))
```

## Dramatic Pacing

The presenter adjusts intervals based on game state. The key insight: it knows the future.

```python
def _compute_interval(self, possession_index: int, possession: PossessionLog) -> float:
    base = self.pace.base_interval  # e.g., 60s for production, 15s for fast

    # Look ahead to know what's coming
    remaining = len(self.game.play_by_play) - possession_index
    is_final_5 = remaining <= 5
    is_final_possession = remaining == 1

    # Score context
    diff = abs(self.game.play_by_play[possession_index].score_diff)
    is_close = diff <= 5
    is_blowout = diff >= 15

    # Momentum
    is_run = self._detect_run(possession_index)  # 5-0+ run in progress

    # Adjust
    modifier = 1.0

    if is_blowout and not self.game.elam_target:
        modifier *= 0.6  # Speed through blowouts

    if is_run:
        modifier *= 0.8  # Momentum feels urgent

    if possession.is_lead_change:
        modifier *= 1.3  # Pause — let it register

    if possession.elam_just_activated:
        modifier *= 2.0  # Long pause — set the scene

    if is_final_5 and is_close:
        modifier *= 1.5  # Slow down — every play matters

    if is_final_possession:
        modifier *= 2.0  # The big moment — dramatic pause

    return base * modifier
```

### Pace Modes

```python
class PaceMode(BaseModel):
    name: str
    base_interval: float  # seconds between possessions

PACE_PRODUCTION = PaceMode(name="production", base_interval=60.0)  # ~20-30 min game
PACE_FAST = PaceMode(name="fast", base_interval=15.0)              # ~5-8 min game
PACE_DEMO = PaceMode(name="demo", base_interval=5.0)               # ~2-3 min game
PACE_INSTANT = PaceMode(name="instant", base_interval=0.0)         # immediate
```

## Commentary Engine Integration

The commentary engine receives the full `GameResult` up front and generates commentary in batches. The presenter prefetches batches ahead of its current position.

```python
async def _prefetch_commentary(self, start: int, end: int) -> None:
    """Request commentary for possessions [start, end) from the engine."""
    if not self.commentary:
        return
    end = min(end, len(self.game.play_by_play))
    # Only request possessions we haven't cached yet
    needed_start = start
    while needed_start < end and needed_start in self._commentary_cache:
        needed_start += 1
    if needed_start >= end:
        return

    lines = await self.commentary.generate_batch(needed_start, end)
    for i, line in enumerate(lines):
        self._commentary_cache[needed_start + i] = line
```

Commentary is generated 5-8 possessions ahead. At production pace (60s/possession), this gives the AI ~5-8 minutes to respond — more than enough for a single batch API call.

## Round Presenter

The `RoundPresenter` manages all games in a round:

```python
class RoundPresenter:
    """Manages presentation of all games in a simulation round."""

    def __init__(
        self,
        game_results: list[GameResult],
        event_bus: EventBus,
        commentary_engines: dict[str, CommentaryEngine],
        pace: PaceMode,
    ):
        self.presenters = [
            GamePresenter(
                game_result=result,
                event_bus=event_bus,
                commentary_engine=commentary_engines.get(result.game_id),
                pace=pace,
            )
            for result in game_results
        ]

    async def present_all(self) -> None:
        """Present all games concurrently."""
        tasks = [
            asyncio.create_task(presenter.present())
            for presenter in self.presenters
        ]
        await asyncio.gather(*tasks)
        # All games complete — publish standings update
        await self.event_bus.publish(GameEvent(
            event_type="standings.update",
            data=await compute_standings(),
        ))
```

All 4 games in a round present simultaneously. The Arena shows all 4 in a 2x2 grid. Each game has its own presenter, its own pace adjustments, and its own commentary stream.

## Late-Join / Catch-Up

When a client connects mid-game:

```python
@app.get("/api/games/{game_id}/state")
async def get_current_game_state(game_id: str):
    """Returns the current presentation state for a game in progress."""
    presenter = active_presenters.get(game_id)
    if not presenter:
        # Game not currently presenting — return full result
        return await repository.get_game_result(game_id)
    return {
        "game_id": game_id,
        "current_possession": presenter.current_index,
        "score": presenter.current_score,
        "quarter": presenter.current_quarter,
        "recent_plays": presenter.recent_plays(5),  # last 5 possessions
        "status": "live",
    }
```

The client fetches current state via REST, then subscribes to SSE for real-time updates from that point forward. A "Watch from start" button triggers the replay path (same presenter, faster pace, commentary from cache).

## Replay

Replay uses the same `GamePresenter` with a stored `GameResult` and cached commentary:

```python
async def replay_game(game_id: str, pace: PaceMode = PACE_FAST):
    game_result = await repository.get_game_result(game_id)
    commentary = await repository.get_cached_commentary(game_id)
    # Commentary already cached from live presentation — no new API calls
    presenter = GamePresenter(
        game_result=game_result,
        event_bus=replay_event_bus,  # separate bus for replay (doesn't affect live)
        commentary_engine=CachedCommentaryEngine(commentary),
        pace=pace,
    )
    await presenter.present()
```

## Integration with Game Loop

```python
# In the game loop scheduler:
async def on_game_clock_fire(season, round_number):
    # 1. Snapshot rules
    rules = await repository.get_current_ruleset(season.id)
    effects = await repository.get_active_game_effects(season.id)

    # 2. Generate matchups
    matchups = await repository.get_schedule(season.id, round_number)

    # 3. Simulate all games (instant, parallel)
    game_results = await asyncio.gather(*[
        asyncio.to_thread(
            simulate_game,
            home=m.home_team, away=m.away_team,
            rules=rules, seed=compute_seed(season, round_number, m),
            effects=effects,
        )
        for m in matchups
    ])

    # 4. Store results
    for result in game_results:
        await repository.store_game_result(result)

    # 5. Generate commentary (parallel, one engine per game)
    commentary_engines = {}
    for result in game_results:
        engine = CommentaryEngine(game_result=result, rules=rules)
        commentary_engines[result.game_id] = engine

    # 6. Start presenting (this runs for 20-30 minutes)
    round_presenter = RoundPresenter(
        game_results=game_results,
        event_bus=event_bus,
        commentary_engines=commentary_engines,
        pace=get_current_pace(),
    )
    asyncio.create_task(round_presenter.present_all())

    # 7. Trigger simulation report (runs in parallel with presentation)
    asyncio.create_task(generate_simulation_report(game_results, rules))
```

## File Structure

```
core/
├── presenter.py        # GamePresenter, RoundPresenter, PaceMode
├── event_bus.py        # EventBus, GameEvent
ai/
├── commentary.py       # CommentaryEngine, CachedCommentaryEngine
api/
├── events.py           # SSE endpoint (/api/events/stream)
```

## Acceptance Criteria

- [ ] EventBus pub/sub works with multiple subscribers and filtered event types
- [ ] GamePresenter paces possessions at configurable intervals
- [ ] Dramatic pacing adjusts for lead changes, Elam, blowouts, final possessions
- [ ] 4 games present simultaneously without blocking each other
- [ ] SSE endpoint streams events to connected clients with correct filtering
- [ ] Commentary prefetch stays ahead of current presentation position
- [ ] Late-join clients can catch up via REST + SSE
- [ ] Replay works from stored GameResult + cached commentary (no new API calls)
- [ ] Tests: pacing math, EventBus delivery, concurrent presenters, SSE format
