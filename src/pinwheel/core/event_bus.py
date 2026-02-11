"""In-memory async event bus for SSE streaming.

Pub/sub pattern: game loop publishes events, SSE endpoints subscribe.
Each subscriber gets an asyncio.Queue. Events are fire-and-forget —
if no subscribers are listening, events are silently dropped.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)


class EventBus:
    """Async pub/sub event bus.

    Usage:
        bus = EventBus()

        # Subscriber (SSE endpoint)
        async for event in bus.subscribe("game.completed"):
            yield f"data: {event}\\n\\n"

        # Publisher (game loop)
        await bus.publish("game.completed", {"game_id": "g-1"})
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = defaultdict(list)
        self._wildcard_subscribers: list[asyncio.Queue[dict[str, Any]]] = []

    async def publish(self, event_type: str, data: dict[str, Any]) -> int:
        """Publish an event to all subscribers of this type + wildcard subscribers.

        Returns the number of subscribers that received the event.
        """
        envelope = {"type": event_type, "data": data}
        count = 0

        for queue in self._subscribers.get(event_type, []):
            try:
                queue.put_nowait(envelope)
                count += 1
            except asyncio.QueueFull:
                logger.warning("Dropping event %s for slow subscriber", event_type)

        for queue in self._wildcard_subscribers:
            try:
                queue.put_nowait(envelope)
                count += 1
            except asyncio.QueueFull:
                logger.warning("Dropping wildcard event %s for slow subscriber", event_type)

        return count

    def subscribe(self, event_type: str | None = None, max_size: int = 100) -> Subscription:
        """Create a subscription for a specific event type (or all events if None).

        Returns a Subscription that works as an async iterator.
        Must be used as an async context manager to ensure cleanup.
        """
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=max_size)
        return Subscription(self, queue, event_type)

    def _register(self, queue: asyncio.Queue[dict[str, Any]], event_type: str | None) -> None:
        if event_type is None:
            self._wildcard_subscribers.append(queue)
        else:
            self._subscribers[event_type].append(queue)

    def _unregister(self, queue: asyncio.Queue[dict[str, Any]], event_type: str | None) -> None:
        if event_type is None:
            with contextlib.suppress(ValueError):
                self._wildcard_subscribers.remove(queue)
        else:
            with contextlib.suppress(ValueError):
                self._subscribers[event_type].remove(queue)

    @property
    def subscriber_count(self) -> int:
        """Total number of active subscriptions."""
        typed = sum(len(subs) for subs in self._subscribers.values())
        return typed + len(self._wildcard_subscribers)


class Subscription:
    """An active subscription to the event bus. Use as async context manager + async iterator."""

    def __init__(
        self,
        bus: EventBus,
        queue: asyncio.Queue[dict[str, Any]],
        event_type: str | None,
    ) -> None:
        self._bus = bus
        self._queue = queue
        self._event_type = event_type
        self._active = False

    async def __aenter__(self) -> Subscription:
        self._bus._register(self._queue, self._event_type)
        self._active = True
        return self

    async def __aexit__(self, *args: object) -> None:
        self._active = False
        self._bus._unregister(self._queue, self._event_type)

    def __aiter__(self) -> Subscription:
        return self

    async def __anext__(self) -> dict[str, Any]:
        if not self._active:
            raise StopAsyncIteration
        try:
            return await self._queue.get()
        except asyncio.CancelledError:
            raise StopAsyncIteration from None

    async def get(self, timeout: float | None = None) -> dict[str, Any] | None:
        """Get next event with optional timeout. Returns None on timeout."""
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except TimeoutError:
            return None
