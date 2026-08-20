import shutil
import uuid
from pathlib import Path

import pytest

from dendrophis.tools.builtins.filesystem import GlobTool, RipgrepTool


@pytest.fixture
def temporary_test_directory():
    directory_path = Path.cwd() / f"temporary_test_relativization_{uuid.uuid4().hex}"
    directory_path.mkdir(parents=True, exist_ok=True)
    yield directory_path
    if directory_path.exists():
        shutil.rmtree(directory_path)


@pytest.mark.anyio
async def test_ripgrep_tool_returns_relative_paths(temporary_test_directory):
    # Create a subfolder and a file within the temporary directory
    subfolder_path = temporary_test_directory / "nested_folder"
    subfolder_path.mkdir(parents=True, exist_ok=True)
    test_file_path = subfolder_path / "test_file.txt"
    test_file_content = "This is a search target content with unique token xyz123."
    test_file_path.write_text(test_file_content, encoding="utf-8")

    ripgrep_tool = RipgrepTool()

    # Search with search path as relative path to the subfolder
    result = await ripgrep_tool.execute(
        pattern="xyz123",
        path=str(temporary_test_directory.relative_to(Path.cwd())),
    )

    assert "error" not in result, result.get("error")
    matches = result.get("matches", [])
    assert len(matches) > 0

    # Ensure all file paths returned are relative (do not start with "/")
    for match_item in matches:
        matched_file_path = match_item["file"]
        assert not matched_file_path.startswith("/")
        # Verify the path actually exists relative to CWD
        assert (Path.cwd() / matched_file_path).exists()
        # Verify it has correct relative structure
        assert "nested_folder" in matched_file_path


@pytest.mark.anyio
async def test_ripgrep_tool_default_path_returns_relative_paths(temporary_test_directory):
    # Create a file directly in temporary_test_directory
    test_file_path = temporary_test_directory / "test_file_default.txt"
    test_file_content = "This is default path search target content with unique token abc987."
    test_file_path.write_text(test_file_content, encoding="utf-8")

    ripgrep_tool = RipgrepTool()

    # Search without specifying a path (defaults to CWD, which is absolute search path)
    result = await ripgrep_tool.execute(
        pattern="abc987",
    )

    assert "error" not in result, result.get("error")
    matches = result.get("matches", [])
    assert len(matches) > 0

    for match_item in matches:
        matched_file_path = match_item["file"]
        assert not matched_file_path.startswith("/")
        assert (Path.cwd() / matched_file_path).exists()


@pytest.mark.anyio
async def test_glob_tool_returns_relative_paths(temporary_test_directory):
    # Create a subfolder and a file within the temporary directory
    subfolder_path = temporary_test_directory / "nested_folder"
    subfolder_path.mkdir(parents=True, exist_ok=True)
    test_file_path = subfolder_path / "glob_target.py"
    test_file_path.write_text("print('hello')", encoding="utf-8")

    glob_tool = GlobTool()

    # Search with search path as relative path
    result = await glob_tool.execute(
        pattern="**/*.py",
        path=str(temporary_test_directory.relative_to(Path.cwd())),
    )

    assert "error" not in result, result.get("error")
    matched_files = result.get("files", [])
    assert len(matched_files) > 0

    # Ensure all file paths returned are relative (do not start with "/")
    for matched_file_path in matched_files:
        assert not matched_file_path.startswith("/")
        # Verify the path actually exists relative to CWD
        assert (Path.cwd() / matched_file_path).exists()
        # Verify it has correct relative structure
        assert "nested_folder" in matched_file_path


@pytest.mark.anyio
async def test_glob_tool_default_path_returns_relative_paths(temporary_test_directory):
    # Create a file within temporary_test_directory
    test_file_path = temporary_test_directory / "glob_target_default.py"
    test_file_path.write_text("print('hello')", encoding="utf-8")

    glob_tool = GlobTool()

    # Search without specifying path (defaults to CWD)
    pattern = f"{temporary_test_directory.name}/*.py"
    result = await glob_tool.execute(
        pattern=pattern,
    )

    assert "error" not in result, result.get("error")
    matched_files = result.get("files", [])
    assert len(matched_files) > 0

    for matched_file_path in matched_files:
        assert not matched_file_path.startswith("/")
        assert (Path.cwd() / matched_file_path).exists()
