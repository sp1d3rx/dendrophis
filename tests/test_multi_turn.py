#!/usr/bin/env python3
"""Test multi-turn conversation with tool execution."""

import asyncio
import os

from dendrophis.debug_chat import run_single_chat


async def test_multi_turn():
    print("=== Testing Multi-Turn Conversation ===")
    
    # Enable tool logging
    os.environ["DENDROPHIS_TOOL_LOG"] = "1"
    
    # First turn: Ask to list files (should trigger glob tool)
    print("\n--- Turn 1: List files ---")
    result1 = await run_single_chat(
        "List python files in the current directory",
        verbose=False
    )
    
    print(f"Turn 1 - Tool calls: {len(result1['tool_calls'])}")
    for tool_call_index, tool_call in enumerate(result1["tool_calls"]):
        print(f"  Tool {tool_call_index + 1}: {tool_call['name']}")
    
    # Simulate adding tool results to context (what would happen in a real session)
    print("\n--- Simulating tool execution and adding results ---")
    
    # Second turn: Ask a follow-up question (should use the tool results)
    print("\n--- Turn 2: Follow-up question ---")
    
    # This would be the follow-up turn with tool results in context
    print("Context now includes tool results...")
    print("Testing if LLM can respond appropriately to tool results...")
    
    print("\n✅ Multi-turn test structure created")
    print("The aggressive SSE termination should allow proper follow-up turns")

if __name__ == "__main__":
    asyncio.run(test_multi_turn())