#!/usr/bin/env python3
"""Script to insert the first memory entry."""

import asyncio

from dendrophis.config.loader import ConfigLoader
from dendrophis.events import EventBus
from dendrophis.session.session import Session
from dendrophis.tools.builtins.memory import SaveMemoryTool


async def main():
    # Load user's config
    config_loader = ConfigLoader.load()
    bus = EventBus()
    session = Session(config_loader, event_bus=bus)

    # Create and execute save_memory tool
    tool = SaveMemoryTool(session._memory_store)
    result = await tool.execute(
        content="""
        Dendrophis v0.3.1: The first persistent memory entry.

        This marks the beginning of the memory system being available as tools to the LLM.
        The system now supports:
        - save_memory: Store information with automatic embedding computation via /v1/embeddings
        - search_memory: Hybrid vector + ngram search, returns 200-char summaries
        - display_memory: Show full content of a specific memory
        - delete_memory: Remove a memory (requires user confirmation)

        Embeddings are computed using the LLM provider's /v1/embeddings endpoint
        and stored as 1536-dimensional float32 vectors in SQLite.
        Search uses 50% vector similarity, 20% ngram overlap, 15% recency, 15% usage score.

        Created as the first test of the memory tool integration.
        """,
        tags=["system", "dendrophis", "memory", "v0.3.1"],
        project_id="",
    )
    print(f"Result: {result}")
    return result


if __name__ == "__main__":
    result = asyncio.run(main())
    if result.get("success"):
        print("\n✓ Memory saved successfully!")
        print(f"  Memory ID: {result['memory_id']}")
    else:
        print(f"\n✗ Failed to save memory: {result.get('error')}")
