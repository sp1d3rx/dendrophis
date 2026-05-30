#!/usr/bin/env python3
"""
Event Bus Demo - Shows how to use the event bus system.

This demonstrates:
1. Creating an event bus
2. Subscribing to events (sync and async)
3. Publishing events from multiple threads
4. Event inheritance
5. Graceful shutdown
"""

import asyncio
import sys
import threading
import time
from dataclasses import dataclass

from dendrophis.events import EventBus, publish


# Define custom events
@dataclass
class UserMessageEvent:
    """User sent a message."""

    text: str
    timestamp: float = time.time()


@dataclass
class BotResponseEvent:
    """Bot responded with text."""

    text: str
    tokens: int = 0


@dataclass
class StatusEvent:
    """Base class for status events."""

    message: str


@dataclass
class LoadingEvent(StatusEvent):
    """Loading started."""

    progress: float = 0.0


@dataclass
class CompleteEvent(StatusEvent):
    """Operation completed."""

    success: bool = True


def sync_handler(event: UserMessageEvent) -> None:
    """Synchronous event handler."""
    print(f"[SYNC] User said: {event.text} at {time.strftime('%H:%M:%S', time.localtime(event.timestamp))}", flush=True)
    sys.stdout.flush()


async def async_handler(event: UserMessageEvent) -> None:
    """Async event handler - simulates async processing."""
    await asyncio.sleep(0.05)
    print(f"[ASYNC] Processing: {event.text}", flush=True)
    sys.stdout.flush()


def status_handler(event: StatusEvent) -> None:
    """Handler for base StatusEvent - receives all derived events too."""
    print(f"[STATUS] {event.message}", flush=True)
    sys.stdout.flush()


def loading_handler(event: LoadingEvent) -> None:
    """Handler specifically for LoadingEvent."""
    print(f"[LOADING] {event.message} ({event.progress:.0%})", flush=True)
    sys.stdout.flush()


def main():
    # Create event bus with 4 worker threads
    bus = EventBus(max_workers=4)

    # Set event loop for async handlers
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bus.set_event_loop(loop)

    print("=" * 60, flush=True)
    print("Event Bus Demo", flush=True)
    print("=" * 60, flush=True)

    # Subscribe to events
    print("\n1. Subscribing to events...", flush=True)
    bus.subscribe(UserMessageEvent, sync_handler)
    bus.subscribe(UserMessageEvent, async_handler)
    bus.subscribe(StatusEvent, status_handler)  # Base class
    bus.subscribe(LoadingEvent, loading_handler)  # Derived class

    # Publish events from main thread
    print("\n2. Publishing events from main thread...", flush=True)
    publish(UserMessageEvent(text="Hello, world!"))
    publish(UserMessageEvent(text="How are you?"))

    time.sleep(0.3)  # Let handlers process

    # Publish from worker thread
    print("\n3. Publishing from worker thread...", flush=True)

    def worker_thread():
        publish(LoadingEvent(message="Processing request", progress=0.25))
        time.sleep(0.1)
        publish(LoadingEvent(message="Still processing", progress=0.75))
        time.sleep(0.1)
        publish(CompleteEvent(message="Done!", success=True))

    thread = threading.Thread(target=worker_thread)
    thread.start()
    thread.join()

    time.sleep(0.3)

    # Demonstrate inheritance - both StatusEvent and LoadingEvent handlers fire
    print("\n4. Demonstrating event inheritance...", flush=True)
    publish(LoadingEvent(message="Final load", progress=1.0))

    time.sleep(0.3)

    # Shutdown
    print("\n5. Shutting down event bus...", flush=True)
    bus.shutdown(wait=True)

    print("\n" + "=" * 60, flush=True)
    print("Demo complete!", flush=True)
    print("=" * 60, flush=True)


if __name__ == "__main__":
    main()
