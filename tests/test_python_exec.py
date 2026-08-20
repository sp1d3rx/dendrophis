import pytest

from dendrophis.tools.builtins.python_exec import execute_code


@pytest.mark.anyio
async def test_python_exec_tool_success() -> None:
    code_to_run = "result = 1 + 2\nprint('hello world')"
    result = await execute_code.execute(code=code_to_run, description="Run a simple test case")
    assert result["success"] is True
    assert result["stdout"].strip() == "hello world"
    assert result["stderr"] == ""
    assert result["exception"] == ""
    assert result["local_variables"]["result"] == 3


@pytest.mark.anyio
async def test_python_exec_tool_failure() -> None:
    code_to_run = "raise ValueError('some error')"
    result = await execute_code.execute(code=code_to_run, description="Run a failing test case")
    assert result["success"] is False
    assert "ValueError: some error" in result["exception"]
