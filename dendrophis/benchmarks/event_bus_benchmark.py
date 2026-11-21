"""Event Bus Benchmark Suite.

Run with: python -m dendrophis.benchmarks.event_bus_benchmark

Tests:
  - Publish performance (events/sec) with varying subscriber counts
  - Subscribe/unsubscribe performance
  - Async handler performance
  - Memory usage under load
  - Stress test with realistic streaming patterns
  - Comparison with pydispatcher
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
import time
import tracemalloc
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from dendrophis.events import DoneEvent, EventBus, TextDeltaEvent

# ---------------------------------------------------------------------------
# pydispatcher support (optional)
# Note: PyPI package name is 'pydispatcher', but import name is 'pydispatch'
# ---------------------------------------------------------------------------

_PYDISPATCHER_AVAILABLE = False
try:
    import pydispatch

    _PYDISPATCHER_AVAILABLE = True
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Test Event Types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BenchmarkEvent:
    """Simple event for benchmarking."""

    id: int
    data: str = ""


@dataclass(frozen=True, slots=True)
class BenchmarkEvent2:
    """Second event type for testing multiple types."""

    id: int


# ---------------------------------------------------------------------------
# Benchmark Results
# ---------------------------------------------------------------------------


@dataclass
class BenchmarkResult:
    name: str
    iterations: int
    total_time: float
    avg_time: float  # microseconds
    ops_per_second: float
    min_time: float = 0.0
    max_time: float = 0.0
    memory_before: int = 0
    memory_after: int = 0
    memory_delta: int = 0

    def __str__(self) -> str:
        return (
            f"{self.name:<40} "
            f"{self.iterations:>10,} iter  "
            f"{self.avg_time:>8.2f} μs/op  "
            f"{self.ops_per_second:>15,.0f} ops/s  "
            f"{self.memory_delta:>+10} B"
        )


def _make_distinct_sync_handlers(count: int) -> list[Callable[[BenchmarkEvent], None]]:
    """Create ``count`` distinct handler functions so deduplicating buses retain all of them."""
    handlers_list: list[Callable[[BenchmarkEvent], None]] = []
    for _ in range(count):

        def _handler(_event: BenchmarkEvent) -> None:
            return None

        handlers_list.append(_handler)
    return handlers_list


# ---------------------------------------------------------------------------
# Benchmark Functions
# ---------------------------------------------------------------------------


def benchmark_publish_performance(subscriber_counts: list[int]) -> list[BenchmarkResult]:
    """Benchmark publish performance with varying subscriber counts."""
    results: list[BenchmarkResult] = []

    for count in subscriber_counts:
        bus = EventBus()
        handlers_list = _make_distinct_sync_handlers(count)

        for handler_fn in handlers_list:
            bus.subscribe(BenchmarkEvent, handler_fn)

        for _ in range(100):
            bus.publish(BenchmarkEvent(id=0))

        gc.collect()
        gc.disable()

        iterations = 100000 if count <= 100 else 10000

        start = time.perf_counter()
        for event_index in range(iterations):
            bus.publish(BenchmarkEvent(id=event_index))
        end = time.perf_counter()

        gc.enable()

        total_time = end - start
        avg_time = (total_time / iterations) * 1_000_000
        ops_per_sec = iterations / total_time

        results.append(
            BenchmarkResult(
                name=f"publish ({count} subscribers)",
                iterations=iterations,
                total_time=total_time,
                avg_time=avg_time,
                ops_per_second=ops_per_sec,
            )
        )

        bus.shutdown(wait=False)

    return results


def benchmark_subscribe_performance(subscriber_counts: list[int]) -> list[BenchmarkResult]:
    """Benchmark subscribe/unsubscribe performance."""
    results: list[BenchmarkResult] = []

    for count in subscriber_counts:
        bus = EventBus()
        handlers_list = _make_distinct_sync_handlers(count)

        gc.collect()
        gc.disable()

        start = time.perf_counter()
        for handler_fn in handlers_list:
            bus.subscribe(BenchmarkEvent, handler_fn)
        end = time.perf_counter()

        gc.enable()

        total_time = end - start
        avg_time = (total_time / count) * 1_000_000
        ops_per_sec = count / total_time

        results.append(
            BenchmarkResult(
                name=f"subscribe ({count} handlers)",
                iterations=count,
                total_time=total_time,
                avg_time=avg_time,
                ops_per_second=ops_per_sec,
            )
        )

        gc.collect()
        gc.disable()

        start = time.perf_counter()
        for handler_fn in handlers_list:
            bus.unsubscribe(BenchmarkEvent, handler_fn)
        end = time.perf_counter()

        gc.enable()

        total_time = end - start
        avg_time = (total_time / count) * 1_000_000
        ops_per_sec = count / total_time

        results.append(
            BenchmarkResult(
                name=f"unsubscribe ({count} handlers)",
                iterations=count,
                total_time=total_time,
                avg_time=avg_time,
                ops_per_second=ops_per_sec,
            )
        )

        bus.shutdown(wait=False)

    return results


async def benchmark_async_handlers(bus: EventBus, handler_count: int, event_count: int) -> BenchmarkResult:
    """Benchmark async handler performance."""
    handlers_completed = 0

    for _ in range(handler_count):

        async def async_handler(_event: BenchmarkEvent) -> None:
            nonlocal handlers_completed
            handlers_completed += 1

        bus.subscribe(BenchmarkEvent, async_handler)

    bus.set_event_loop(asyncio.get_running_loop())

    for _ in range(10):
        bus.publish(BenchmarkEvent(id=0))
    await asyncio.sleep(0.01)

    handlers_completed = 0
    start = time.perf_counter()

    for event_index in range(event_count):
        bus.publish(BenchmarkEvent(id=event_index))

    await asyncio.sleep(0.1)

    end = time.perf_counter()

    total_time = end - start
    avg_time = (total_time / event_count) * 1_000_000
    ops_per_sec = event_count / total_time

    bus.shutdown(wait=False)

    return BenchmarkResult(
        name=f"async ({handler_count} handlers, {event_count} events)",
        iterations=event_count,
        total_time=total_time,
        avg_time=avg_time,
        ops_per_second=ops_per_sec,
    )


def benchmark_memory_usage(subscriber_counts: list[int]) -> list[BenchmarkResult]:
    """Benchmark memory usage with varying subscriber counts."""
    results: list[BenchmarkResult] = []

    for count in subscriber_counts:
        tracemalloc.start()
        bus = EventBus()

        for handler_fn in _make_distinct_sync_handlers(count):
            bus.subscribe(BenchmarkEvent, handler_fn)

        for event_index in range(1000):
            bus.publish(BenchmarkEvent(id=event_index))

        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        results.append(
            BenchmarkResult(
                name=f"memory ({count} subscribers, 1000 events)",
                iterations=count,
                total_time=0,
                avg_time=0,
                ops_per_second=0,
                memory_before=0,
                memory_after=current,
                memory_delta=peak,
            )
        )

        bus.shutdown(wait=False)

    return results


def benchmark_multiple_event_types(type_count: int, handlers_per_type: int, events_per_type: int) -> BenchmarkResult:
    """Benchmark with multiple event types."""
    bus = EventBus()

    event_types: list[type[Any]] = []
    for type_index in range(type_count):
        event_type = type(
            f"DynamicEvent{type_index}",
            (),
            {
                "__slots__": (),
                "__dataclass_fields__": {},
            },
        )
        event_types.append(event_type)

    handlers_called = [0] * type_count

    def make_handler(target_index: int) -> Callable[[Any], None]:
        def handler(_event: Any) -> None:
            handlers_called[target_index] += 1

        return handler

    for type_index, evt_type in enumerate(event_types):
        for _ in range(handlers_per_type):
            bus.subscribe(evt_type, make_handler(type_index))

    for evt_type in event_types:
        for _ in range(10):
            bus.publish(evt_type())

    gc.collect()
    gc.disable()

    start = time.perf_counter()
    for _ in range(events_per_type):
        for evt_type in event_types:
            bus.publish(evt_type())
    end = time.perf_counter()

    gc.enable()

    total_events = type_count * events_per_type
    total_time = end - start
    avg_time = (total_time / total_events) * 1_000_000
    ops_per_sec = total_events / total_time

    bus.shutdown(wait=False)

    return BenchmarkResult(
        name=f"multi-type ({type_count} types, {handlers_per_type} handlers/type)",
        iterations=total_events,
        total_time=total_time,
        avg_time=avg_time,
        ops_per_second=ops_per_sec,
    )


def benchmark_realistic_streaming() -> BenchmarkResult:
    """Benchmark realistic streaming pattern: many TextDelta + Done events."""
    bus = EventBus()
    temp_loop = asyncio.new_event_loop()
    bus.set_event_loop(temp_loop)

    text_delta_count = 0
    done_count = 0

    for _ in range(5):

        def text_handler(_event: TextDeltaEvent) -> None:
            nonlocal text_delta_count
            text_delta_count += 1

        bus.subscribe(TextDeltaEvent, text_handler)

    for _ in range(3):

        def done_handler(_event: DoneEvent) -> None:
            nonlocal done_count
            done_count += 1

        bus.subscribe(DoneEvent, done_handler)

    for _ in range(100):
        bus.publish(TextDeltaEvent(delta="test"))

    gc.collect()
    gc.disable()

    start = time.perf_counter()

    for token_index in range(100):
        bus.publish(TextDeltaEvent(delta=f"token{token_index}"))

    bus.publish(DoneEvent(finish_reason="stop"))

    end = time.perf_counter()
    gc.enable()

    total_time = end - start
    total_events = 101
    avg_time = (total_time / total_events) * 1_000_000
    ops_per_sec = total_events / total_time

    bus.shutdown(wait=False)
    temp_loop.close()

    return BenchmarkResult(
        name="realistic streaming (101 events)",
        iterations=total_events,
        total_time=total_time,
        avg_time=avg_time,
        ops_per_second=ops_per_sec,
    )


# ---------------------------------------------------------------------------
# Stress Test
# ---------------------------------------------------------------------------


def stress_test(duration_seconds: float = 5.0, handlers: int = 50) -> BenchmarkResult:
    """Stress test: publish as many events as possible in a time period."""
    bus = EventBus()

    for handler_fn in _make_distinct_sync_handlers(handlers):
        bus.subscribe(BenchmarkEvent, handler_fn)

    for _ in range(1000):
        bus.publish(BenchmarkEvent(id=0))

    gc.collect()
    gc.disable()

    start = time.perf_counter()
    end_time = start + duration_seconds
    published = 0

    while time.perf_counter() < end_time:
        bus.publish(BenchmarkEvent(id=published))
        published += 1

    end = time.perf_counter()
    gc.enable()

    total_time = end - start
    avg_time = (total_time / published) * 1_000_000 if published > 0 else 0
    ops_per_sec = published / total_time if total_time > 0 else 0

    bus.shutdown(wait=False)

    return BenchmarkResult(
        name=f"stress test ({handlers} handlers, {duration_seconds}s)",
        iterations=published,
        total_time=total_time,
        avg_time=avg_time,
        ops_per_second=ops_per_sec,
    )


# ---------------------------------------------------------------------------
# pydispatcher Benchmarks
# ---------------------------------------------------------------------------


def _pydispatcher_publish_benchmark(subscriber_counts: list[int]) -> list[BenchmarkResult]:
    """Benchmark pydispatcher publish performance."""
    if not _PYDISPATCHER_AVAILABLE:
        return []

    import pydispatch.dispatcher as dispatcher

    results: list[BenchmarkResult] = []
    for count in subscriber_counts:
        signal = "benchmark_signal"
        sender = object()

        handlers_list = []
        for _ in range(count):

            def make_handler() -> Callable[..., None]:
                return lambda sender, **kwargs: None

            handlers_list.append(make_handler())

        for handler_fn in handlers_list:
            dispatcher.connect(handler_fn, signal=signal, sender=sender)

        for _ in range(100):
            dispatcher.send(signal=signal, sender=sender, id=0)

        gc.collect()
        gc.disable()

        iterations = 100000 if count <= 100 else 10000

        start = time.perf_counter()
        for event_index in range(iterations):
            dispatcher.send(signal=signal, sender=sender, id=event_index)
        end = time.perf_counter()

        gc.enable()

        total_time = end - start
        avg_time = (total_time / iterations) * 1_000_000
        ops_per_sec = iterations / total_time

        results.append(
            BenchmarkResult(
                name=f"pydispatch publish ({count} subscribers)",
                iterations=iterations,
                total_time=total_time,
                avg_time=avg_time,
                ops_per_second=ops_per_sec,
            )
        )

        with contextlib.suppress(Exception):
            dispatcher.disconnect(pydispatch.dispatcher.Any, signal=signal, sender=sender)

    return results


def _pydispatcher_subscribe_benchmark(subscriber_counts: list[int]) -> list[BenchmarkResult]:
    """Benchmark pydispatcher subscribe/unsubscribe performance."""
    if not _PYDISPATCHER_AVAILABLE:
        return []

    import pydispatch.dispatcher as dispatcher

    results: list[BenchmarkResult] = []
    for count in subscriber_counts:
        signal = "benchmark_signal"
        sender = object()

        def make_handler() -> Callable[..., None]:
            return lambda sender, **kwargs: None

        handlers_list = [make_handler() for _ in range(count)]

        gc.collect()
        gc.disable()

        start = time.perf_counter()
        for handler_fn in handlers_list:
            dispatcher.connect(handler_fn, signal=signal, sender=sender)
        end = time.perf_counter()

        gc.enable()

        total_time = end - start
        avg_time = (total_time / count) * 1_000_000
        ops_per_sec = count / total_time

        results.append(
            BenchmarkResult(
                name=f"pydispatch subscribe ({count} handlers)",
                iterations=count,
                total_time=total_time,
                avg_time=avg_time,
                ops_per_second=ops_per_sec,
            )
        )

        gc.collect()
        gc.disable()

        start = time.perf_counter()
        dispatcher.disconnect(pydispatch.dispatcher.Any, signal=signal, sender=sender)
        end = time.perf_counter()

        gc.enable()

        total_time = end - start
        avg_time = (total_time / count) * 1_000_000
        ops_per_sec = count / total_time

        results.append(
            BenchmarkResult(
                name=f"pydispatch unsubscribe ({count} handlers)",
                iterations=count,
                total_time=total_time,
                avg_time=avg_time,
                ops_per_second=ops_per_sec,
            )
        )

    return results


def _pydispatcher_stress_test(duration_seconds: float = 2.0, handlers: int = 50) -> BenchmarkResult:
    """Stress test pydispatcher."""
    if not _PYDISPATCHER_AVAILABLE:
        return BenchmarkResult(
            name="pydispatch stress test (N/A - not installed)",
            iterations=0,
            total_time=0,
            avg_time=0,
            ops_per_second=0,
        )

    import pydispatch.dispatcher as dispatcher

    signal = "stress_signal"
    sender = object()

    def make_handler() -> Callable[..., None]:
        return lambda sender, **kwargs: None

    handlers_list = [make_handler() for _ in range(handlers)]
    for handler_fn in handlers_list:
        dispatcher.connect(handler_fn, signal=signal, sender=sender)

    for _ in range(1000):
        dispatcher.send(signal=signal, sender=sender, id=0)

    gc.collect()
    gc.disable()

    start = time.perf_counter()
    end_time = start + duration_seconds
    published = 0

    while time.perf_counter() < end_time:
        dispatcher.send(signal=signal, sender=sender, id=published)
        published += 1

    end = time.perf_counter()
    gc.enable()

    total_time = end - start
    avg_time = (total_time / published) * 1_000_000 if published > 0 else 0
    ops_per_sec = published / total_time if total_time > 0 else 0

    for handler_fn in handlers_list:
        with contextlib.suppress(Exception):
            dispatcher.disconnect(handler_fn, signal=signal, sender=sender)

    return BenchmarkResult(
        name=f"pydispatch stress ({handlers} handlers, {duration_seconds}s)",
        iterations=published,
        total_time=total_time,
        avg_time=avg_time,
        ops_per_second=ops_per_sec,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def print_header() -> None:
    print("\n" + "=" * 100)
    print("DENDROPHIS EVENT BUS BENCHMARK")
    print("=" * 100)


def print_results(results: list[BenchmarkResult], title: str) -> None:
    print(f"\n{title}")
    print("-" * 100)
    print(f"{'Name':<40} {'Iterations':>10} {'μs/op':>8} {'ops/s':>15} {'Memory Δ':>10}")
    print("-" * 100)
    for result in results:
        print(result)


def run_memory_tests() -> None:
    print_header()
    print("\n[MEMORY TESTS]")
    results = benchmark_memory_usage([10, 100, 500])
    print_results(results, "Memory Usage")


def run_pydispatcher_comparison() -> None:
    """Run side-by-side comparison with pydispatcher."""
    if not _PYDISPATCHER_AVAILABLE:
        print("\n[pydispatch not installed - install with: pip install pydispatcher]")
        return

    print_header()
    print("\n[COMPARISON: DENDROPHIS vs PYDISPATCHER]")
    print("=" * 100)

    print("\n--- Publish Performance ---")
    sub_counts = [1, 10, 50, 100, 200]

    dendro_results = benchmark_publish_performance(sub_counts)
    pydisp_results = _pydispatcher_publish_benchmark(sub_counts)

    print(f"{'':<40} {'Dendrophis':>20} {'pydispatch':>20}")
    print(f"{'Subscribers':<40} {'ops/s':>20} {'ops/s':>20}")
    print("-" * 82)
    for result_index, count in enumerate(sub_counts):
        dendro_ops = dendro_results[result_index].ops_per_second
        pydisp_ops = pydisp_results[result_index].ops_per_second if result_index < len(pydisp_results) else 0
        winner = "✓" if dendro_ops >= pydisp_ops else "✗"
        print(f"{count} subscribers{'':<28} {dendro_ops:>20,.0f} {pydisp_ops:>20,.0f} {winner}")

    print("\n--- Subscribe Performance ---")
    sub_counts = [10, 100, 1000]

    all_dendro_sub = benchmark_subscribe_performance(sub_counts)
    all_pydisp_sub = _pydispatcher_subscribe_benchmark(sub_counts)
    dendro_sub = [result_item for result_item in all_dendro_sub if result_item.name.startswith("subscribe")]
    pydisp_sub = [result_item for result_item in all_pydisp_sub if result_item.name.startswith("pydispatch subscribe")]

    print(f"{'':<40} {'Dendrophis':>20} {'pydispatch':>20}")
    print(f"{'Handlers':<40} {'ops/s':>20} {'ops/s':>20}")
    print("-" * 82)
    for result_index, count in enumerate(sub_counts):
        dendro_ops = dendro_sub[result_index].ops_per_second
        pydisp_ops = pydisp_sub[result_index].ops_per_second if result_index < len(pydisp_sub) else 0
        winner = "✓" if dendro_ops >= pydisp_ops else "✗"
        print(f"{count} handlers{'':<32} {dendro_ops:>20,.0f} {pydisp_ops:>20,.0f} {winner}")

    print("\n--- Unsubscribe Performance ---")
    dendro_unsub = [result_item for result_item in all_dendro_sub if result_item.name.startswith("unsubscribe")]
    pydisp_unsub = [
        result_item for result_item in all_pydisp_sub if result_item.name.startswith("pydispatch unsubscribe")
    ]

    print(f"{'':<40} {'Dendrophis':>20} {'pydispatch':>20}")
    print(f"{'Handlers':<40} {'ops/s':>20} {'ops/s':>20}")
    print("-" * 82)
    for result_index, count in enumerate(sub_counts):
        dendro_ops = dendro_unsub[result_index].ops_per_second
        pydisp_ops = pydisp_unsub[result_index].ops_per_second if result_index < len(pydisp_unsub) else 0
        winner = "✓" if dendro_ops >= pydisp_ops else "✗"
        print(f"{count} handlers{'':<32} {dendro_ops:>20,.0f} {pydisp_ops:>20,.0f} {winner}")

    print("\n--- Stress Test (2s, 50 handlers) ---")
    dendro_stress = stress_test(duration_seconds=2.0, handlers=50)
    pydisp_stress = _pydispatcher_stress_test(duration_seconds=2.0, handlers=50)
    print(f"{'':<40} {'Dendrophis':>20} {'pydispatch':>20}")
    print(f"{'ops/s':<40} {'ops/s':>20} {'ops/s':>20}")
    print("-" * 82)
    winner = "✓" if dendro_stress.ops_per_second >= pydisp_stress.ops_per_second else "✗"
    print(f"{'':<40} {dendro_stress.ops_per_second:>20,.0f} {pydisp_stress.ops_per_second:>20,.0f} {winner}")

    print("\n--- Summary ---")
    print("✓ = Dendrophis faster, ✗ = pydispatch faster")

    print("\n" + "=" * 100)


def run_all_benchmarks() -> None:
    print_header()

    print("\n[PUBLISH PERFORMANCE]")
    results = benchmark_publish_performance([0, 1, 10, 50, 100, 200])
    print_results(results, "Publish Benchmark")

    print("\n[SUBSCRIBE/UNSUBSCRIBE PERFORMANCE]")
    results = benchmark_subscribe_performance([10, 100, 1000])
    print_results(results, "Subscribe Benchmark")

    print("\n[MULTIPLE EVENT TYPES]")
    results = [benchmark_multiple_event_types(5, 10, 100)]
    print_results(results, "Multiple Types")

    print("\n[REALISTIC STREAMING]")
    results = [benchmark_realistic_streaming()]
    print_results(results, "Streaming Simulation")

    run_memory_tests()

    print("\n[STRESS TEST]")
    results = [stress_test(duration_seconds=2.0, handlers=50)]
    print_results(results, "Stress Test")

    print("\n" + "=" * 100)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Event Bus Benchmark")
    parser.add_argument("--quick", action="store_true", help="Run quick benchmarks only")
    parser.add_argument("--stress", action="store_true", help="Run stress tests only")
    parser.add_argument("--memory", action="store_true", help="Run memory tests only")
    parser.add_argument("--compare", action="store_true", help="Run comparison with pydispatcher")

    args = parser.parse_args()

    if args.compare:
        run_pydispatcher_comparison()
    elif args.stress:
        print_header()
        print("\n[STRESS TEST]")
        for handler_count in [10, 50, 100, 200]:
            result = stress_test(duration_seconds=2.0, handlers=handler_count)
            print(f"\n{result}")
    elif args.memory:
        run_memory_tests()
    elif args.quick:
        print_header()
        print("\n[QUICK BENCHMARK]")
        results = benchmark_publish_performance([10, 50, 100])
        print_results(results, "Publish")
        results = [stress_test(duration_seconds=1.0, handlers=50)]
        print_results(results, "Stress")
    else:
        run_all_benchmarks()
