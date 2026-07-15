"""Protocol definition for the event bus to support dependency injection."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from dendrophis.events.types import AnyEvent

EventType = TypeVar("EventType", bound=AnyEvent)
EventHandler = Callable[[AnyEvent], None]
AsyncEventHandler = Callable[[AnyEvent], Coroutine[Any, Any, None]]


class IEventBus(ABC):
    """Interface for the event bus."""

    @abstractmethod
    def subscribe(
        self,
        event_type: type[EventType],
        handler: EventHandler | AsyncEventHandler,
        *,
        priority: int = 0,
    ) -> Any:
        """Subscribe to an event type."""
        ...

    @abstractmethod
    def unsubscribe(
        self,
        subscription_or_event_type: Any,
        handler: EventHandler | AsyncEventHandler | None = None,
    ) -> None:
        """Unsubscribe from an event type."""
        ...

    @abstractmethod
    def publish(self, event: AnyEvent) -> None:
        """Publish an event to all subscribers."""
        ...
