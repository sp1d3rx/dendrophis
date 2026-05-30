#!/usr/bin/env python3
"""Script to verify memories in the database."""

from dendrophis.config.loader import ConfigLoader
from dendrophis.memory.memory import MemoryStore

# Load config and create memory store
config_loader = ConfigLoader.load()
store = MemoryStore(config_loader.config.memory_db)

# List all memories
memories = store.list_memories(limit=10)
print(f"\nFound {len(memories)} memories in database:\n")

for i, mem in enumerate(memories, 1):
    print(f"{i}. [{mem.id}]")
    print(f"   Tags: {mem.tags}")
    print(f"   Source: {mem.source}")
    print(f"   Created: {mem.created_at}")
    content_preview = mem.content[:100] + "..." if len(mem.content) > 100 else mem.content
    print(f"   Content: {content_preview}\n")

    # Check if embedding was stored
    has_embedding = mem.embedding_blob is not None
    print(f"   Has embedding: {has_embedding}")
    if has_embedding:
        print(f"   Embedding size: {len(mem.embedding_blob)} bytes")
    print()
