import asyncio
import shutil
from pathlib import Path

import pytest

from dendrophis.events.bus import EventBus
from dendrophis.tools.builtins.filesystem import WriteTool
from dendrophis.tools.interactive.write import InteractiveWriteTool


@pytest.fixture
def local_tmp_dir():
    import uuid

    path = Path.cwd() / f"tmp_test_write_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    yield path
    if path.exists():
        shutil.rmtree(path)


@pytest.mark.anyio
async def test_write_tool_overwrites(local_tmp_dir):
    target_file = local_tmp_dir / "test_file.txt"
    tool = WriteTool()

    # 1. Write to non-existent file
    result1 = await tool.execute(file_path=str(target_file.relative_to(Path.cwd())), content="first content")
    assert "error" not in result1, result1.get("error")
    assert result1["success"] is True
    assert target_file.read_text(encoding="utf-8") == "first content"

    # 2. Write to existing file should overwrite it
    result2 = await tool.execute(file_path=str(target_file.relative_to(Path.cwd())), content="second content")
    assert "error" not in result2, result2.get("error")
    assert result2["success"] is True
    assert target_file.read_text(encoding="utf-8") == "second content"


@pytest.mark.anyio
async def test_interactive_write_tool_silent_overwrites(local_tmp_dir):
    target_file = local_tmp_dir / "interactive_test_file.txt"
    event_bus = EventBus()
    tool = InteractiveWriteTool(event_bus=event_bus)
    tool.silent = True

    # 1. Write to non-existent file
    result1 = await tool.execute(file_path=str(target_file.relative_to(Path.cwd())), content="initial content")
    assert "error" not in result1, result1.get("error")
    assert result1["success"] is True
    assert target_file.read_text(encoding="utf-8") == "initial content"

    # 2. Write to existing file should overwrite it
    result2 = await tool.execute(file_path=str(target_file.relative_to(Path.cwd())), content="overwritten content")
    assert "error" not in result2, result2.get("error")
    assert result2["success"] is True
    assert target_file.read_text(encoding="utf-8") == "overwritten content"


@pytest.mark.anyio
async def test_interactive_write_tool_approval(local_tmp_dir):
    target_file = local_tmp_dir / "approval_test_file.txt"
    event_bus = EventBus()
    event_bus.set_event_loop(asyncio.get_running_loop())
    tool = InteractiveWriteTool(event_bus=event_bus)
    tool.silent = False

    from dendrophis.events.types import WriteApprovalEvent, WriteProposalEvent

    proposal_received = asyncio.Event()
    proposal_event_captured = None

    def on_proposal(event):
        nonlocal proposal_event_captured
        proposal_event_captured = event
        proposal_received.set()

    event_bus.subscribe(WriteProposalEvent, on_proposal)

    async def run_tool():
        return await tool.execute(file_path=str(target_file.relative_to(Path.cwd())), content="approved content")

    tool_task = asyncio.create_task(run_tool())

    # Wait for proposal event
    await proposal_received.wait()
    assert proposal_event_captured is not None
    assert proposal_event_captured.content == "approved content"

    # Publish approval response
    event_bus.publish(WriteApprovalEvent(request_id=proposal_event_captured.request_id, approved=True))

    result = await tool_task
    assert "error" not in result, result.get("error")
    assert result["success"] is True
    assert target_file.read_text(encoding="utf-8") == "approved content"
    event_bus.shutdown()


@pytest.mark.anyio
async def test_interactive_append_tool_silent_and_approval(local_tmp_dir):
    from dendrophis.events.types import AppendApprovalEvent, AppendProposalEvent
    from dendrophis.tools.interactive.append import InteractiveAppendTool

    target_file = local_tmp_dir / "interactive_append_file.txt"
    target_file.write_text("line 1\n", encoding="utf-8")
    event_bus = EventBus()
    event_bus.set_event_loop(asyncio.get_running_loop())

    # 1. Silent mode
    tool_silent = InteractiveAppendTool(event_bus=event_bus)
    tool_silent.silent = True
    result_silent = await tool_silent.execute(file_path=str(target_file.relative_to(Path.cwd())), content="line 2\n")
    assert result_silent.get("success") is True
    assert target_file.read_text(encoding="utf-8") == "line 1\nline 2\n"

    # 2. Approval mode
    tool_approval = InteractiveAppendTool(event_bus=event_bus)
    tool_approval.silent = False

    proposal_received = asyncio.Event()
    proposal_event_captured = None

    def on_proposal(event):
        nonlocal proposal_event_captured
        proposal_event_captured = event
        proposal_received.set()

    event_bus.subscribe(AppendProposalEvent, on_proposal)

    async def run_tool():
        return await tool_approval.execute(file_path=str(target_file.relative_to(Path.cwd())), content="line 3\n")

    tool_task = asyncio.create_task(run_tool())
    await proposal_received.wait()
    assert proposal_event_captured is not None
    assert proposal_event_captured.content == "line 3\n"

    event_bus.publish(AppendApprovalEvent(request_id=proposal_event_captured.request_id, approved=True))
    result_approved = await tool_task
    assert result_approved.get("success") is True
    assert target_file.read_text(encoding="utf-8") == "line 1\nline 2\nline 3\n"
    event_bus.shutdown()


@pytest.mark.anyio
async def test_interactive_edit_tool_silent_and_approval(local_tmp_dir):
    from dendrophis.events.types import EditApprovalEvent, EditProposalEvent
    from dendrophis.tools.interactive.edit import InteractiveEditTool

    target_file = local_tmp_dir / "interactive_edit_file.txt"
    target_file.write_text("hello world\n", encoding="utf-8")
    event_bus = EventBus()
    event_bus.set_event_loop(asyncio.get_running_loop())

    # 1. Silent mode
    tool_silent = InteractiveEditTool(event_bus=event_bus)
    tool_silent.silent = True
    result_silent = await tool_silent.execute(
        file_path=str(target_file.relative_to(Path.cwd())),
        old_string="hello world\n",
        new_string="hello universe\n",
    )
    assert result_silent.get("success") is True
    assert target_file.read_text(encoding="utf-8") == "hello universe\n"

    # 2. Approval mode
    tool_approval = InteractiveEditTool(event_bus=event_bus)
    tool_approval.silent = False

    proposal_received = asyncio.Event()
    proposal_event_captured = None

    def on_proposal(event):
        nonlocal proposal_event_captured
        proposal_event_captured = event
        proposal_received.set()

    event_bus.subscribe(EditProposalEvent, on_proposal)

    async def run_tool():
        return await tool_approval.execute(
            file_path=str(target_file.relative_to(Path.cwd())),
            old_string="hello universe\n",
            new_string="hello dendrophis\n",
        )

    tool_task = asyncio.create_task(run_tool())
    await proposal_received.wait()
    assert proposal_event_captured is not None
    assert "-hello universe" in proposal_event_captured.diff
    assert "+hello dendrophis" in proposal_event_captured.diff

    event_bus.publish(EditApprovalEvent(request_id=proposal_event_captured.request_id, approved=True))
    result_approved = await tool_task
    assert result_approved.get("success") is True
    assert target_file.read_text(encoding="utf-8") == "hello dendrophis\n"
    event_bus.shutdown()


def test_run_auto_lint_preserves_unused_imports(local_tmp_dir):
    from dendrophis.tools.builtins.filesystem.utils import run_auto_lint

    target_python_file = local_tmp_dir / "unused_import_test.py"
    target_python_file.write_text("import os\nimport sys\n\ndef my_func():\n    return 1\n", encoding="utf-8")

    run_auto_lint(str(target_python_file))

    file_content = target_python_file.read_text(encoding="utf-8")
    assert "import os" in file_content
    assert "import sys" in file_content
