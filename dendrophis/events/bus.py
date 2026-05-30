"""Event bus system for decoupled communication between components."""

from __future__ import annotations

import asyncio
import bisect
import contextvars
import heapq
import logging
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, TypeVar

from dendrophis.events.protocol import IEventBus
from dendrophis.events.types import AnyEvent

EventType = TypeVar("EventType", bound=AnyEvent)

logger = logging.getLogger(__name__)

# Type alias for event handlers
EventHandler = Callable[[AnyEvent], None]
AsyncEventHandler = Callable[[AnyEvent], Coroutine[Any, Any, None]]


@dataclass(frozen=True)
class EventMetadata:
    """Metadata attached to every event for tracing and debugging."""

    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    timestamp: float = field(default_factory=time.monotonic)


class EventBus(IEventBus):
    """Thread-safe event bus for publishing and subscribing to events.

    Supports both sync and async handlers with priority ordering.
    Sync handlers run in a thread pool; async handlers are scheduled
    on the event loop.
    """

    __slots__ = (
        "_error_count",
        "_insertion_counter",
        "_lock",
        "_loop",
        "_loop_and_not_closed",
        "_shutdown",
        "_sorted_handlers_cache",
        "_subscribers",
        "_thread_pool",
    )

    def __init__(self, max_workers: int = 4) -> None:
        self._subscribers: dict[
            type[AnyEvent], list[tuple[int, int, EventHandler | AsyncEventHandler, contextvars.Context, bool]]
        ] = defaultdict(list)
        self._sorted_handlers_cache: dict[
            type[AnyEvent], list[tuple[int, int, EventHandler | AsyncEventHandler, contextvars.Context, bool]]
        ] = {}
        self._lock = threading.RLock()
        self._thread_pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="event-bus")
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_and_not_closed: bool = False  # Optimized check: loop is set and not closed
        self._shutdown = False
        self._error_count: int = 0
        self._insertion_counter: int = 0

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Set the event loop for async handler execution."""
        self._loop = loop
        self._loop_and_not_closed = loop is not None and not loop.is_closed()

    def subscribe(
        self,
        event_type: type[EventType],
        handler: EventHandler | AsyncEventHandler,
        *,
        priority: int = 0,
    ) -> None:
        """Subscribe to an event type.

        Args:
            event_type: The type of event to subscribe to.
            handler: A callable that accepts the event as its only argument.
            priority: Lower values fire first. Default 0.
        """
        ctx = contextvars.copy_context()
        is_async = asyncio.iscoroutinefunction(handler)
        with self._lock:
            # Insert in sorted order using bisect (O(n) insertion but maintains sorted list)
            # Tuple: (priority, insertion_counter, handler, context, is_async)
            # insertion_counter ensures stable sorting when priorities are equal
            subscribers = self._subscribers[event_type]
            self._insertion_counter += 1
            bisect.insort(subscribers, (priority, self._insertion_counter, handler, ctx, is_async))
            self._invalidate_cache(event_type)

    def unsubscribe(
        self,
        event_type: type[EventType],
        handler: EventHandler | AsyncEventHandler,
    ) -> None:
        """Unsubscribe from an event type."""
        with self._lock:
            self._subscribers[event_type] = [
                (prio, ins_cnt, h_ctx, ctx, is_async)
                for prio, ins_cnt, h_ctx, ctx, is_async in self._subscribers[event_type]
                if h_ctx is not handler
            ]
            if not self._subscribers[event_type]:
                del self._subscribers[event_type]
            self._invalidate_cache(event_type)

    def unsubscribe_all(self, event_type: type[AnyEvent]) -> None:
        """Remove all subscribers for an event type."""
        with self._lock:
            self._subscribers.pop(event_type, None)
            self._invalidate_cache(event_type)

    def _invalidate_cache(self, event_type: type[AnyEvent]) -> None:
        """Invalidate the sorted handlers cache for an event type and its subclasses."""
        # Invalidate cache for this type and all its base classes
        types_to_invalidate = [event_type]
        types_to_invalidate.extend(base_type for base_type in event_type.__mro__[1:] if base_type is not object)
        for t in types_to_invalidate:
            self._sorted_handlers_cache.pop(t, None)

    def _sorted_handlers(
        self, event_type: type[AnyEvent]
    ) -> list[tuple[int, EventHandler | AsyncEventHandler, contextvars.Context, bool]]:
        """Get handlers sorted by priority (lower = first). Uses cache and heapq.merge for performance."""
        # Check cache first
        if event_type in self._sorted_handlers_cache:
            return self._sorted_handlers_cache[event_type]

        with self._lock:
            # Collect all sorted lists (each is already sorted by priority via bisect.insort)
            # Tuples in _subscribers are (priority, insertion_counter, handler, ctx, is_async)
            # We need to strip insertion_counter for the return value
            sorted_lists: list[list[tuple[int, int, EventHandler | AsyncEventHandler, contextvars.Context, bool]]] = []
            if event_type in self._subscribers:
                sorted_lists.append(self._subscribers[event_type])
            sorted_lists.extend(
                self._subscribers[base_type] for base_type in event_type.__mro__[1:] if base_type in self._subscribers
            )

            # Merge sorted lists using heapq.merge (O(n) instead of O(n log n))
            # Single list: return directly. Multiple lists: merge.
            if not sorted_lists:
                merged = []
            elif len(sorted_lists) == 1:
                merged = sorted_lists[0]
            else:
                merged = list(heapq.merge(*sorted_lists))
            # Strip insertion_counter (index 1) from tuples:
            # (prio, ins_cnt, handler, ctx, is_async) -> (prio, handler, ctx, is_async)
            merged_stripped = [(t[0], t[2], t[3], t[4]) for t in merged]
            self._sorted_handlers_cache[event_type] = merged_stripped
        return merged_stripped

    def publish(self, event: AnyEvent) -> None:
        """Publish an event to all subscribers.

        Thread-safe and non-blocking. Handlers are sorted by priority
        before dispatch.
        """
        if self._shutdown:
            return

        # Cache event type lookup
        event_type = type(event)
        handlers = self._sorted_handlers(event_type)
        loop_and_not_closed = self._loop_and_not_closed
        loop = self._loop
        for _prio, handler, ctx, is_async in handlers:
            # Inlined dispatch for performance
            if is_async:
                if loop_and_not_closed:

                    def _create_and_run(h=handler, e=event):
                        task = loop.create_task(h(e))
                        task.add_done_callback(self._async_safe_call)

                    loop.call_soon_threadsafe(ctx.run, _create_and_run)
                else:
                    logger.warning("No event loop set; dropping async handler for %s", type(event).__name__)
            else:
                if loop_and_not_closed:
                    loop.call_soon_threadsafe(ctx.run, self._safe_call, handler, event)
                else:
                    self._thread_pool.submit(ctx.run, self._safe_call, handler, event)

    def _async_safe_call(self, task: asyncio.Task) -> None:
        """Handle exceptions from async event handlers."""
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            self._error_count += 1
            logger.exception(
                "Event handler error (event=Async, errors_so_far=%d)",
                self._error_count,
            )

    def _safe_call(self, handler: EventHandler, event: AnyEvent) -> None:
        """Safely call a handler, catching and logging exceptions."""
        try:
            handler(event)
        except Exception:
            self._error_count += 1
            logger.exception(
                "Event handler error (event=%s, handler=%s, errors_so_far=%d)",
                type(event).__name__,
                handler.__qualname__,
                self._error_count,
            )

    def shutdown(self, wait: bool = True) -> None:
        """Shutdown the event bus."""
        self._shutdown = True
        if wait:
            self._thread_pool.shutdown(wait=True)
        else:
            self._thread_pool.shutdown(wait=False)

    def __enter__(self) -> EventBus:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.shutdown(wait=True)


# Global event bus instance
_event_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Get the global event bus instance, creating it if necessary."""
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus


def set_event_bus(bus: EventBus) -> None:
    """Set the global event bus instance."""
    global _event_bus
    _event_bus = bus


def publish(event: AnyEvent) -> None:
    """Publish an event to the global event bus."""
    get_event_bus().publish(event)


def subscribe(
    event_type: type[AnyEvent],
    handler: EventHandler | AsyncEventHandler,
    *,
    priority: int = 0,
) -> None:
    """Subscribe to an event type on the global event bus."""
    get_event_bus().subscribe(event_type, handler, priority=priority)
