import json

from dendrophis.config.schema import DendrophisConfig
from dendrophis.context.manager import ContextManager


def test_context_manager_file_deduplication() -> None:
    config = DendrophisConfig()
    context = ContextManager(config=config)

    # 1. Read first file: "file1.py" with content "print('hello')"
    result1 = '{"type": "file", "path": "file1.py", "content": "print(\'hello\')"}'
    context.append_tool_result("call1", "read", result1)
    assert len(context.messages) == 2

    # 2. Read second file: "file2.py" with identical content "print('hello')"
    # Since it's a different file path, it should NOT be deduped
    result2 = '{"type": "file", "path": "file2.py", "content": "print(\'hello\')"}'
    context.append_tool_result("call2", "read", result2)
    assert len(context.messages) == 3

    # 3. Read first file: "file1.py" again with unchanged content
    # Must append a compact stub tool message (so call3 is never orphaned) rather than repeating content
    context.append_tool_result("call3", "read", result1)
    assert len(context.messages) == 4
    stub_message = context.messages[-1]
    assert stub_message["role"] == "tool"
    assert stub_message["tool_call_id"] == "call3"
    stub_payload = json.loads(stub_message["content"])
    assert stub_payload == {"status": "unchanged_since_last_read", "path": "file1.py"}
    assert "print('hello')" not in stub_message["content"]

    # 4. Read first file: "file1.py" again with modified content
    # This should NOT be deduped since the hash has changed
    result1_modified = '{"type": "file", "path": "file1.py", "content": "print(\'hello world\')"}'
    context.append_tool_result("call4", "read", result1_modified)
    assert len(context.messages) == 5
    assert "print('hello world')" in context.messages[-1]["content"]


def test_context_manager_invalidates_read_hash_on_file_mutation() -> None:
    config = DendrophisConfig()
    context = ContextManager(config=config)

    original_read = '{"type": "file", "path": "solver.py", "content": "score = 1"}'
    context.append_tool_result("call_read_1", "read", original_read)

    # Mutate solver.py via edit, then revert back to original content without intermediate read
    context.append_tool_result("call_edit_1", "edit", '{"success": true, "file_path": "solver.py"}')
    context.append_tool_result("call_read_2", "read", original_read)

    last_message = context.messages[-1]
    assert last_message["tool_call_id"] == "call_read_2"
    assert "score = 1" in last_message["content"]


def test_context_manager_capping() -> None:
    config = DendrophisConfig()
    context = ContextManager(config=config)

    # Add 502 unique file reads to exceed the cap
    for index in range(502):
        result = f'{{"type": "file", "path": "file_{index}.py", "content": "print({index})"}}'
        context.append_tool_result(f"call_{index}", "read", result)

    # Check that the dict size is capped at 500
    assert len(context._read_file_hashes) == 500

    # Check that the oldest file ("file_0.py") was evicted
    assert "file_0.py" not in context._read_file_hashes

    # Check that newer files are still present
    assert "file_501.py" in context._read_file_hashes


def test_merge_last_turns_collapses_nudge() -> None:
    config = DendrophisConfig()
    context = ContextManager(config=config)

    # Simulate: assistant turn 1 (intent, no tool call) -> nudge user msg -> assistant turn 2
    context.append_user("user: read the file")
    context.append_assistant("Let me read the file first.")
    context.append_user(
        "You described an action but did not call a tool. "
        "Proceed with the tool call now, or state clearly that you are finished."
    )
    context.append_assistant("I'll read the file now.")

    before = len(context.messages)
    context.merge_last_turns()

    # Nudge user message removed, two assistant turns merged into one: -2 messages
    assert len(context.messages) == before - 2
    roles = [m["role"] for m in context.messages]
    assert roles == ["system", "user", "assistant"]
    merged = context.messages[-1]
    assert "Let me read the file first." in merged["content"]
    assert "I'll read the file now." in merged["content"]


def test_merge_last_turns_keeps_tool_calls_from_post_nudge_turn() -> None:
    config = DendrophisConfig()
    context = ContextManager(config=config)

    context.append_user("user: read the file")
    context.append_assistant("Let me read the file first.")
    context.append_user("nudge text")
    # Post-nudge turn makes a tool call
    context.append_assistant(
        "Reading now.",
        tool_calls=[{"function": {"name": "read", "arguments": "{}"}, "id": "call_1"}],
    )

    context.merge_last_turns()

    merged = context.messages[-1]
    assert merged["role"] == "assistant"
    assert merged["tool_calls"] is not None
    assert merged["tool_calls"][0]["function"]["name"] == "read"
    assert "Let me read the file first." in merged["content"]


def test_merge_last_turns_noop_without_nudge() -> None:
    config = DendrophisConfig()
    context = ContextManager(config=config)

    context.append_user("user: hello")
    context.append_assistant("Hi there!")
    before = len(context.messages)

    # No user message sandwiched between assistant turns -> no-op
    context.merge_last_turns()
    assert len(context.messages) == before
