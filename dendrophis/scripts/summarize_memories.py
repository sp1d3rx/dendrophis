#!/usr/bin/env python3
"""Backfill summaries for existing memories without them."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dendrophis.config.loader import ConfigLoader
from dendrophis.llm.client import LLMClient
from dendrophis.memory.memory import MemoryStore


async def summarize_memories(config_path: str | None = None):
    """Generate summaries for all memories that lack them."""
    loader = ConfigLoader.load(config_path)
    config = loader.config
    store = MemoryStore(config.memory_db)

    # Get all memories
    all_memories = store.list_memories(limit=10000)

    # Filter to those without summaries
    to_summarize = [m for m in all_memories if not m.summary]

    if not to_summarize:
        print("No memories need summarization.")
        return

    print(f"Found {len(to_summarize)} memories without summaries.")

    # Initialize LLM client for summarization
    from dendrophis.llm.client import LLMConfig

    llm_config = LLMConfig(
        api_key=config.llm.api_key,
        base_url=config.llm.base_url,
        model=config.llm.model,
    )
    llm = LLMClient(llm_config)

    for i, memory in enumerate(to_summarize, 1):
        print(f"\n[{i}/{len(to_summarize)}] Summarizing memory {memory.id[:8]}...")

        # Generate summary
        prompt = f"""Summarize this memory in one concise sentence:

{memory.content[:500]}

Summary:"""

        try:
            response = await llm.complete(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=100,
            )
            summary = response.text.strip()

            # Remove quotes if present
            summary = summary.strip("\"'")

            # Update the memory
            store.update_memory(memory.id, summary=summary)
            print(f"  → {summary}")

        except Exception as e:
            print(f"  ✗ Failed: {e}")

    print(f"\nDone! Summarized {len(to_summarize)} memories.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill memory summaries")
    parser.add_argument("--config", "-c", help="Path to config YAML file")
    args = parser.parse_args()
    asyncio.run(summarize_memories(args.config))
