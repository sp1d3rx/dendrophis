#!/usr/bin/env python3
"""Test Phase 1 token caching implementation."""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from dataclasses import MISSING, fields

from dendrophis.caching.file_tracker import FileBlock, FileBlockTracker
from dendrophis.caching.understanding import UnderstandingPhaseDetector
from dendrophis.config.schema import CachingConfig, DendrophisConfig
from dendrophis.context.manager import ContextManager
from dendrophis.llm.client import LLMClient


def test_caching_config():
    """Test that CachingConfig can be created and has correct defaults."""
    config = CachingConfig()
    assert config.enabled is True, "Caching should be enabled by default"
    assert config.tier1_system_prompt is True, "System prompt caching should be enabled by default"
    assert config.tier1_tool_definitions is True, "Tool definitions caching should be enabled by default"
    print("✓ CachingConfig defaults are correct")


def test_dendrophis_config_with_caching():
    """Test that DendrophisConfig includes caching."""
    config = DendrophisConfig()
    assert hasattr(config, "caching"), "DendrophisConfig should have caching attribute"
    assert config.caching.enabled is True
    print("✓ DendrophisConfig includes caching")


def test_system_prompt_cache_control():
    """Test that system prompt gets cache_control when enabled."""
    config = DendrophisConfig(
        system_prompt="Test system prompt", caching=CachingConfig(enabled=True, tier1_system_prompt=True)
    )

    context = ContextManager(config)
    context.update_system_prompt_caching(caching_enabled=True)

    # Check that first message (system prompt) has cache_control
    assert len(context.messages) > 0, "Context should have system message"
    system_msg = context.messages[0]
    assert system_msg["role"] == "system", "First message should be system message"
    assert "cache_control" in system_msg, "System message should have cache_control"
    assert system_msg["cache_control"]["type"] == "ephemeral", "Cache control type should be ephemeral"
    print("✓ System prompt gets cache_control when enabled")


def test_system_prompt_no_cache_control_when_disabled():
    """Test that system prompt doesn't get cache_control when disabled."""
    config = DendrophisConfig(system_prompt="Test system prompt", caching=CachingConfig(enabled=False))

    context = ContextManager(config)

    system_msg = context.messages[0]
    assert "cache_control" not in system_msg, "System message should not have cache_control when disabled"
    print("✓ System prompt has no cache_control when disabled")


def test_tool_cache_control():
    """Test that tools get cache_control in stream_chat."""
    config = DendrophisConfig()
    client = LLMClient(config.llm)

    # Test that enable_cache_control=True adds cache_control
    # (We can't easily test async here, but we can check the parameter exists)
    import inspect

    sig = inspect.signature(client.stream_chat)
    assert "enable_cache_control" in sig.parameters, "stream_chat should have enable_cache_control parameter"
    print("✓ stream_chat has enable_cache_control parameter")


def _assert_no_none_sentinel_defaults(cls):
    """C1/C2 invariant: no dataclass field may use None as an implicit empty-container default."""
    for f in fields(cls):
        if f.default_factory is not MISSING:
            continue  # default_factory fields get a fresh container per construction
        assert f.default is not None, f"{cls.__name__}.{f.name}: None sentinel default (use field(default_factory=...))"


def test_tracker_and_detector_have_no_none_sentinel_defaults():
    """FileBlockTracker and UnderstandingPhaseDetector must not default container fields to None."""
    _assert_no_none_sentinel_defaults(FileBlockTracker)
    _assert_no_none_sentinel_defaults(UnderstandingPhaseDetector)


def test_file_block_tracker_instances_are_independent():
    """Each FileBlockTracker gets its own _files dict; one instance's state never leaks to another."""
    t1 = FileBlockTracker()
    t2 = FileBlockTracker()
    t1.track_file("a.py", "print(1)", turn=1, message_index=0)
    assert t1.get_stats()["total_files_tracked"] == 1
    assert t2.get_stats()["total_files_tracked"] == 0, "instances must not share mutable state"

    # Explicit constructor injection is still honored
    seeded = FileBlockTracker(_files={"b.py": FileBlock(path="b.py", content_hash="h", turn_added=1)})
    assert seeded.get_stats()["total_files_tracked"] == 1


def test_understanding_detector_instances_are_independent():
    """Each UnderstandingPhaseDetector gets its own message-type list; no cross-instance leakage."""
    d1 = UnderstandingPhaseDetector()
    d2 = UnderstandingPhaseDetector()
    d1.record_user_message("fix the bug", turn=5)
    assert d1.is_established()
    assert not d2.is_established(), "instances must not share mutable state"
    assert d2._last_user_message_types == []


if __name__ == "__main__":
    print("Testing Phase 1 token caching implementation...\n")

    test_caching_config()
    test_dendrophis_config_with_caching()
    test_system_prompt_cache_control()
    test_system_prompt_no_cache_control_when_disabled()
    test_tool_cache_control()
    test_tracker_and_detector_have_no_none_sentinel_defaults()
    test_file_block_tracker_instances_are_independent()
    test_understanding_detector_instances_are_independent()

    print("\n✓ All Phase 1 caching tests passed!")
