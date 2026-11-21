#!/usr/bin/env python3
"""Backfill summaries for existing memories without them."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dendrophis.config.loader import ConfigLoader
from dendrophis.config.schema import LLMConfig
from dendrophis.llm.client import LLMClient
from dendrophis.memory.memory import MemoryStore

logger = logging.getLogger(__name__)


async def summarize_memories(config_path: str | None = None) -> None:
    """Generate summaries for all memories that lack them."""
    loader = ConfigLoader.load(config_path)
    config = loader.config
    store = MemoryStore(config.memory_db)

    all_memories = store.list_memories(limit=10000)
    to_summarize = [memory_item for memory_item in all_memories if not memory_item.summary]

    if not to_summarize:
        print("No memories need summarization.")
        return

    print(f"Found {len(to_summarize)} memories without summaries.")

    llm_config = LLMConfig(
        api_key=config.llm.api_key,
        base_url=config.llm.base_url,
        model=config.llm.model,
        temperature=0.3,
        max_tokens=100,
    )
    llm = LLMClient(llm_config)
    succeeded_count = 0

    try:
        for index, memory in enumerate(to_summarize, 1):
            print(f"\n[{index}/{len(to_summarize)}] Summarizing memory {memory.id[:8]}...")
            prompt = f"Summarize this memory in one concise sentence:\n\n{memory.content[:500]}\n\nSummary:"

            try:
                response = await llm.complete(
                    messages=[{"role": "user", "content": prompt}],
                )
                summary = response.text.strip().strip("\"'")
                if not summary:
                    print("  ✗ Empty summary returned; skipping.")
                    continue
                store.update_memory(memory.id, summary=summary)
                succeeded_count += 1
                print(f"  → {summary}")
            except Exception as memory_error:
                logger.error("Failed to summarize memory %s: %s", memory.id, memory_error, exc_info=True)
                print(f"  ✗ Failed: {memory_error}")
    finally:
        await llm.aclose()

    print(f"\nDone! Summarized {succeeded_count}/{len(to_summarize)} memories.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill memory summaries")
    parser.add_argument("--config", "-c", help="Path to config YAML file")
    args = parser.parse_args()
    asyncio.run(summarize_memories(args.config))
