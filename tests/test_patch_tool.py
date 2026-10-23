from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any

import pytest

from dendrophis.events import EventBus
from dendrophis.tools.builtins.filesystem.patch import PatchTool
from dendrophis.tools.discovery import discover_tool_classes, resolve_dependencies_and_instantiate
from dendrophis.tools.interactive.patch import InteractivePatchTool


@pytest.fixture
def local_tmp_dir():
    path = Path.cwd() / f"tmp_test_patch_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    yield path
    if path.exists():
        shutil.rmtree(path)


@pytest.mark.anyio
async def test_patch_tool_success(local_tmp_dir) -> None:
    sample_file_path = local_tmp_dir / "test_file.txt"
    initial_content = "line one\nline two\nline three\nline four\n"
    sample_file_path.write_text(initial_content, encoding="utf-8")

    patch_tool_instance = PatchTool()

    # Multi-block replacements
    edit_list = [
        {"search": "line two", "replace": "line two updated"},
        {"search": "line four", "replace": "line four updated"},
    ]

    relative_path = sample_file_path.relative_to(Path.cwd())
    execution_result = await patch_tool_instance.execute(
        file_path=str(relative_path),
        edits=edit_list,
    )

    assert execution_result.get("success") is True
    assert execution_result.get("applied_edits_count") == 2

    updated_content = sample_file_path.read_text(encoding="utf-8")
    expected_content = "line one\nline two updated\nline three\nline four updated\n"
    assert updated_content == expected_content


@pytest.mark.anyio
async def test_patch_tool_not_found(local_tmp_dir) -> None:
    sample_file_path = local_tmp_dir / "test_file.txt"
    initial_content = "line one\nline two\n"
    sample_file_path.write_text(initial_content, encoding="utf-8")

    patch_tool_instance = PatchTool()

    edit_list = [
        {"search": "line non_existent", "replace": "replacement"},
    ]

    relative_path = sample_file_path.relative_to(Path.cwd())
    execution_result = await patch_tool_instance.execute(
        file_path=str(relative_path),
        edits=edit_list,
    )

    assert "error" in execution_result
    assert "not found in file" in execution_result["error"]


@pytest.mark.anyio
async def test_patch_tool_ambiguous(local_tmp_dir) -> None:
    sample_file_path = local_tmp_dir / "test_file.txt"
    initial_content = "duplicate line\nother line\nduplicate line\n"
    sample_file_path.write_text(initial_content, encoding="utf-8")

    patch_tool_instance = PatchTool()

    edit_list = [
        {"search": "duplicate line", "replace": "replaced line"},
    ]

    relative_path = sample_file_path.relative_to(Path.cwd())
    execution_result = await patch_tool_instance.execute(
        file_path=str(relative_path),
        edits=edit_list,
    )

    assert "error" in execution_result
    assert "Ambiguous edit" in execution_result["error"]


@pytest.mark.anyio
async def test_interactive_patch_tool_silent(local_tmp_dir) -> None:
    sample_file_path = local_tmp_dir / "test_file.txt"
    initial_content = "first line\nsecond line\n"
    sample_file_path.write_text(initial_content, encoding="utf-8")

    event_bus_instance = EventBus()
    interactive_patch_tool = InteractivePatchTool(event_bus=event_bus_instance)
    interactive_patch_tool.silent = True

    edit_list = [
        {"search": "first line", "replace": "first line updated"},
    ]

    relative_path = sample_file_path.relative_to(Path.cwd())
    execution_result = await interactive_patch_tool.execute(
        file_path=str(relative_path),
        edits=edit_list,
    )

    assert execution_result.get("success") is True
    assert execution_result.get("applied_edits_count") == 1

    updated_content = sample_file_path.read_text(encoding="utf-8")
    assert "first line updated" in updated_content


def test_dynamic_discovery_and_di() -> None:
    # Discover all tool classes
    discovered_classes = discover_tool_classes(
        [
            "dendrophis.tools.builtins",
            "dendrophis.tools.builtins.filesystem",
            "dendrophis.tools.interactive",
        ]
    )

    # Ensure PatchTool and InteractivePatchTool are discovered
    discovered_names = {cls.__name__ for cls in discovered_classes}
    assert "PatchTool" in discovered_names
    assert "InteractivePatchTool" in discovered_names

    # Test DI resolution
    event_bus_instance = EventBus()
    dependency_dictionary = {
        "event_bus": event_bus_instance,
    }

    # InteractivePatchTool should instantiate successfully when event_bus is provided
    interactive_patch_class = next(cls for cls in discovered_classes if cls.__name__ == "InteractivePatchTool")
    instance = resolve_dependencies_and_instantiate(interactive_patch_class, dependency_dictionary)
    assert instance is not None
    assert isinstance(instance, InteractivePatchTool)

    # SaveMemoryTool should return None if memory_store is missing
    save_memory_class = next(cls for cls in discovered_classes if cls.__name__ == "SaveMemoryTool")
    empty_dependencies: dict[str, Any] = {}
    failed_instance = resolve_dependencies_and_instantiate(save_memory_class, empty_dependencies)
    assert failed_instance is None

    # SaveMemoryTool should resolve successfully when memory_store is provided (as string annotation)
    from dendrophis.memory import MemoryStore

    class DummyMemoryStore(MemoryStore):
        def __init__(self) -> None:
            pass

    dummy_store = DummyMemoryStore()
    memory_dependencies = {
        "memory_store": dummy_store,
    }
    save_memory_instance = resolve_dependencies_and_instantiate(save_memory_class, memory_dependencies)
    assert save_memory_instance is not None


@pytest.mark.anyio
async def test_patch_tool_auto_lint(local_tmp_dir) -> None:
    sample_file_path = local_tmp_dir / "test_file.py"
    initial_content = "import os\n\ndef foo():\n    x  =  1\n    return x\n"
    sample_file_path.write_text(initial_content, encoding="utf-8")

    patch_tool_instance = PatchTool()

    edit_list = [
        {"search": "x  =  1", "replace": "x  =  2"},
    ]

    relative_path = sample_file_path.relative_to(Path.cwd())
    execution_result = await patch_tool_instance.execute(
        file_path=str(relative_path),
        edits=edit_list,
    )

    assert execution_result.get("success") is True

    # Verify formatting is fixed and unused import is preserved
    updated_content = sample_file_path.read_text(encoding="utf-8")
    assert "import os" in updated_content
    assert "x = 2" in updated_content


@pytest.mark.anyio
async def test_patch_tool_fallback_keys_and_backup(local_tmp_dir) -> None:
    sample_file_path = local_tmp_dir / "test_fallback.txt"
    initial_content = "alpha line\nbeta line\ngamma line\n"
    sample_file_path.write_text(initial_content, encoding="utf-8")

    patch_tool_instance = PatchTool()

    edit_list = [
        {"old_string": "alpha line", "new_string": "alpha updated"},
        {"old": "gamma line", "new": "gamma updated"},
    ]

    relative_path = sample_file_path.relative_to(Path.cwd())
    execution_result = await patch_tool_instance.execute(
        file_path=str(relative_path),
        edits=edit_list,
    )

    assert execution_result.get("success") is True
    assert execution_result.get("applied_edits_count") == 2
    assert sample_file_path.read_text(encoding="utf-8") == "alpha updated\nbeta line\ngamma updated\n"

    backup_file_path = sample_file_path.with_suffix(".txt.bak")
    assert backup_file_path.exists()
    assert backup_file_path.read_text(encoding="utf-8") == initial_content


@pytest.mark.anyio
async def test_patch_tool_rejects_empty_search(local_tmp_dir) -> None:
    sample_file_path = local_tmp_dir / "test_empty_search.txt"
    sample_file_path.write_text("line one\nline two\n", encoding="utf-8")

    patch_tool_instance = PatchTool()
    relative_path = sample_file_path.relative_to(Path.cwd())

    execution_result = await patch_tool_instance.execute(
        file_path=str(relative_path),
        edits=[{"search": "", "replace": "replacement"}],
    )

    assert "error" in execution_result
    assert "Missing or empty 'search' string" in execution_result["error"]


@pytest.mark.anyio
async def test_python_exec_shared_namespace_and_bytes_serialization() -> None:
    from dendrophis.tools.builtins.python_exec import PythonExecTool

    python_exec_tool = PythonExecTool()
    code_snippet = (
        "import math\n"
        "multiplier = 3\n"
        "def compute_value(input_number):\n"
        "    return int(math.sqrt(input_number)) * multiplier\n"
        "computed_result = compute_value(16)\n"
        "raw_bytes_list = [b'hello', b'world']\n"
    )
    execution_result = await python_exec_tool.execute(
        code=code_snippet,
        description="Test function closure over imports and bytes list serialization",
    )

    assert execution_result["success"] is True
    assert execution_result["exception"] == ""
    local_vars = execution_result["local_variables"]
    assert local_vars["computed_result"] == 12
    assert local_vars["raw_bytes_list"] == "[b'hello', b'world']"
