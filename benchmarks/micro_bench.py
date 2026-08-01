import sys
import time
from dataclasses import dataclass

from dendrophis.context.tokenizer import count_tokens
from dendrophis.events import EventBus, publish


@dataclass
class BenchEvent:
    data: str


def handle_bench(event):
    pass


def micro_benchmark():
    print(f"Python version: {sys.version}")

    # 1. Tokenization benchmark (CPU intensive)
    text = "The quick brown fox jumps over the lazy dog. " * 1000
    iterations = 500

    start = time.perf_counter()
    for _ in range(iterations):
        count_tokens(text)
    end = time.perf_counter()
    print(f"Tokenization (tiktoken/fallback) {iterations} iterations: {end - start:.4f}s")

    # 2. Event Bus benchmark (Concurrency/Overhead)
    bus = EventBus(max_workers=4)
    bus.subscribe(BenchEvent, handle_bench)

    event_count = 10000
    start = time.perf_counter()
    for i in range(event_count):
        publish(BenchEvent(data=str(i)))

    # Note: this doesn't wait for all handlers to finish, just the publish overhead
    end = time.perf_counter()
    print(f"Event Publish {event_count} iterations: {end - start:.4f}s")

    bus.shutdown(wait=True)


if __name__ == "__main__":
    micro_benchmark()
