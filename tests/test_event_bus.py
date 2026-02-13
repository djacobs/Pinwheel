"""Tests for the in-memory async EventBus."""

from pinwheel.core.event_bus import EventBus


class TestEventBusPublish:
    async def test_publish_no_subscribers(self):
        bus = EventBus()
        count = await bus.publish("test.event", {"key": "value"})
        assert count == 0

    async def test_publish_to_typed_subscriber(self):
        bus = EventBus()
        received = []

        async with bus.subscribe("test.event") as sub:
            await bus.publish("test.event", {"n": 1})
            event = await sub.get(timeout=1.0)
            assert event is not None
            received.append(event)

        assert len(received) == 1
        assert received[0]["type"] == "test.event"
        assert received[0]["data"]["n"] == 1

    async def test_publish_to_wildcard_subscriber(self):
        bus = EventBus()

        async with bus.subscribe(None) as sub:
            await bus.publish("game.completed", {"id": "g-1"})
            await bus.publish("report.generated", {"id": "m-1"})

            e1 = await sub.get(timeout=1.0)
            e2 = await sub.get(timeout=1.0)

        assert e1["type"] == "game.completed"
        assert e2["type"] == "report.generated"

    async def test_typed_subscriber_filters(self):
        bus = EventBus()

        async with bus.subscribe("game.completed") as sub:
            await bus.publish("report.generated", {"id": "m-1"})
            await bus.publish("game.completed", {"id": "g-1"})

            event = await sub.get(timeout=1.0)
            assert event["type"] == "game.completed"

            # Should timeout — no more game events
            missed = await sub.get(timeout=0.1)
            assert missed is None


class TestEventBusSubscription:
    async def test_subscriber_count(self):
        bus = EventBus()
        assert bus.subscriber_count == 0

        async with bus.subscribe("a"):
            assert bus.subscriber_count == 1
            async with bus.subscribe("b"):
                assert bus.subscriber_count == 2
            assert bus.subscriber_count == 1
        assert bus.subscriber_count == 0

    async def test_wildcard_subscriber_count(self):
        bus = EventBus()
        async with bus.subscribe(None):
            assert bus.subscriber_count == 1

    async def test_unsubscribe_cleanup(self):
        bus = EventBus()
        async with bus.subscribe("test"):
            pass
        # After context exit, queue is removed
        count = await bus.publish("test", {})
        assert count == 0

    async def test_multiple_subscribers_same_type(self):
        bus = EventBus()
        async with bus.subscribe("x") as s1, bus.subscribe("x") as s2:
            count = await bus.publish("x", {"v": 1})
            assert count == 2

            e1 = await s1.get(timeout=1.0)
            e2 = await s2.get(timeout=1.0)
            assert e1 == e2


class TestEventBusBackpressure:
    async def test_full_queue_drops_event(self):
        bus = EventBus()
        async with bus.subscribe("x", max_size=2) as sub:
            await bus.publish("x", {"n": 1})
            await bus.publish("x", {"n": 2})
            # Queue is full, this should be dropped
            count = await bus.publish("x", {"n": 3})
            assert count == 0

            e1 = await sub.get(timeout=1.0)
            e2 = await sub.get(timeout=1.0)
            assert e1["data"]["n"] == 1
            assert e2["data"]["n"] == 2


class TestSubscriptionGet:
    async def test_get_with_timeout_returns_none(self):
        bus = EventBus()
        async with bus.subscribe("x") as sub:
            result = await sub.get(timeout=0.05)
            assert result is None

    async def test_get_returns_event(self):
        bus = EventBus()
        async with bus.subscribe("x") as sub:
            await bus.publish("x", {"val": 42})
            result = await sub.get(timeout=1.0)
            assert result is not None
            assert result["data"]["val"] == 42
