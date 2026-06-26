"""Tests for the event bus system."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from dendrophis.events import EventBus


@dataclass
class DummyEvent:
    """Test event for verification."""

    value: int


def test_basic_publish_subscribe():
    """Test basic publish/subscribe functionality."""
    bus = EventBus()
    received = []

    def handler(event: DummyEvent) -> None:
        received.append(event.value)

    bus.subscribe(DummyEvent, handler)
    bus.publish(DummyEvent(value=1))
    bus.publish(DummyEvent(value=2))
    bus.publish(DummyEvent(value=3))

    # Give thread pool time to process
    time.sleep(0.1)

    # Order not guaranteed with thread pool, check set equality
    assert set(received) == {1, 2, 3}, f"Expected {{1, 2, 3}}, got {set(received)}"
    assert len(received) == 3, f"Expected 3 events, got {len(received)}"
    bus.shutdown()
    print("✓ Basic publish/subscribe test passed")


def test_multiple_subscribers():
    """Test multiple subscribers to same event."""
    bus = EventBus()
    received1 = []
    received2 = []

    def handler1(event: DummyEvent) -> None:
        received1.append(event.value)

    def handler2(event: DummyEvent) -> None:
        received2.append(event.value * 2)

    bus.subscribe(DummyEvent, handler1)
    bus.subscribe(DummyEvent, handler2)
    bus.publish(DummyEvent(value=5))

    time.sleep(0.1)

    assert received1 == [5], f"Expected [5], got {received1}"
    assert received2 == [10], f"Expected [10], got {received2}"
    bus.shutdown()
    print("✓ Multiple subscribers test passed")


async def test_async_handler():
    """Test async event handlers."""
    bus = EventBus()
    loop = asyncio.get_event_loop()
    bus.set_event_loop(loop)
    received = []

    async def async_handler(event: DummyEvent) -> None:
        await asyncio.sleep(0.01)
        received.append(event.value)

    bus.subscribe(DummyEvent, async_handler)
    bus.publish(DummyEvent(value=42))

    # Wait for async handler to complete
    await asyncio.sleep(0.1)

    assert received == [42], f"Expected [42], got {received}"
    bus.shutdown()
    print("✓ Async handler test passed")


def test_inheritance():
    """Test that base class subscribers receive derived events."""
    bus = EventBus()

    @dataclass
    class BaseEvent:
        value: int

    @dataclass
    class DerivedEvent(BaseEvent):
        extra: str = ""

    base_received = []
    derived_received = []

    def base_handler(event: BaseEvent) -> None:
        base_received.append(event.value)

    def derived_handler(event: DerivedEvent) -> None:
        derived_received.append((event.value, event.extra))

    bus.subscribe(BaseEvent, base_handler)
    bus.subscribe(DerivedEvent, derived_handler)

    # Publish derived event - both handlers should receive it
    bus.publish(DerivedEvent(value=10, extra="test"))

    time.sleep(0.1)

    assert base_received == [10], f"Expected [10], got {base_received}"
    assert derived_received == [(10, "test")], f"Expected [(10, 'test')], got {derived_received}"
    bus.shutdown()
    print("✓ Inheritance test passed")


def test_thread_safety():
    """Test thread-safe publishing."""
    import threading

    bus = EventBus()
    received = []
    lock = threading.Lock()

    def handler(event: DummyEvent) -> None:
        with lock:
            received.append(event.value)

    bus.subscribe(DummyEvent, handler)

    def publisher(thread_id: int) -> None:
        for i in range(10):
            bus.publish(DummyEvent(value=thread_id * 100 + i))

    # Start multiple threads
    threads = [threading.Thread(target=publisher, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    time.sleep(0.1)

    assert len(received) == 50, f"Expected 50 events, got {len(received)}"
    bus.shutdown()
    print("✓ Thread safety test passed")


if __name__ == "__main__":
    test_basic_publish_subscribe()
    test_multiple_subscribers()
    asyncio.run(test_async_handler())
    test_inheritance()
    test_thread_safety()
    print("\n✅ All tests passed!")
