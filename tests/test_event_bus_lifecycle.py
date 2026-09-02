"""Tests for deterministic EventBus thread-pool release (global bus lifecycle).

Covers the leak fixed in dendrophis/events/bus.py: the lazily created global
event bus (get_event_bus()) was never shut down, so its ThreadPoolExecutor
survived for the process lifetime. Also covers set_event_bus(), which
previously replaced the global without releasing the previous bus.
"""

from __future__ import annotations

import asyncio

import pytest

import dendrophis.events.bus as bus_module
from dendrophis.events import EventBus, get_event_bus, set_event_bus, shutdown_global_event_bus
from dendrophis.events.types import ConfigEvent


@pytest.fixture(autouse=True)
def _reset_global_bus():
    """Ensure each test starts and ends with a clean global bus state."""
    shutdown_global_event_bus()
    yield
    shutdown_global_event_bus()


def test_set_event_bus_shuts_down_replaced_bus() -> None:
    """Replacing the global bus deterministically releases the previous one."""
    old_bus = EventBus(max_workers=1)
    set_event_bus(old_bus)
    assert get_event_bus() is old_bus

    new_bus = EventBus(max_workers=1)
    set_event_bus(new_bus)

    assert get_event_bus() is new_bus
    # The replaced bus is shut down: its pool is released and publish is a no-op.
    assert old_bus._shutdown is True
    handler_ran = False

    def _handler(_event: ConfigEvent) -> None:
        nonlocal handler_ran
        handler_ran = True

    old_bus.subscribe(ConfigEvent, _handler)
    old_bus.publish(ConfigEvent())
    assert handler_ran is False
    # The new bus still works.
    assert get_event_bus() is new_bus
    new_bus.shutdown(wait=True)


def test_set_event_bus_same_bus_is_noop() -> None:
    """Passing the current global back must not shut it down."""
    bus = EventBus(max_workers=1)
    set_event_bus(bus)
    set_event_bus(bus)
    assert bus._shutdown is False
    assert get_event_bus() is bus


@pytest.mark.asyncio
async def test_publish_on_replaced_bus_is_dropped() -> None:
    """A replaced (shut-down) bus silently drops events; the live bus delivers."""
    old_bus = EventBus(max_workers=1)
    set_event_bus(old_bus)
    new_bus = EventBus(max_workers=1)
    new_bus.set_event_loop(asyncio.get_running_loop())
    set_event_bus(new_bus)

    received: list[ConfigEvent] = []
    new_bus.subscribe(ConfigEvent, received.append)
    new_bus.publish(ConfigEvent())
    await asyncio.sleep(0.05)
    assert len(received) == 1

    old_bus.publish(ConfigEvent())
    assert len(received) == 1  # dropped by the shut-down bus

    new_bus.shutdown(wait=True)


def test_shutdown_global_event_bus_releases_and_clears() -> None:
    """shutdown_global_event_bus releases the pool and the next get is fresh."""
    original = get_event_bus()
    assert original._shutdown is False

    shutdown_global_event_bus()

    assert original._shutdown is True

    # A fresh, working bus is created on next access (not the dead one).
    replacement = get_event_bus()
    assert replacement is not original
    assert replacement._shutdown is False


def test_shutdown_global_event_bus_when_none_is_noop() -> None:
    """Calling with no global bus set must not raise."""
    bus_module._event_bus = None
    shutdown_global_event_bus()
    assert bus_module._event_bus is None


def test_get_event_bus_is_stable_until_shutdown() -> None:
    """Regression: the global is created once and reused across calls."""
    first = get_event_bus()
    second = get_event_bus()
    assert first is second
