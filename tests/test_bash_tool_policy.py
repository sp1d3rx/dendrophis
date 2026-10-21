"""Behavior-pinning tests for SessionToolExecutor._process_bash_tool.

These pin the 2026-10-20 fix that narrowed the broad ``except Exception: pass``
in ``_process_bash_tool`` so it no longer swallows sandbox/argument errors into
a silent user-confirmation prompt. The new contract:

- Malformed ``arguments`` (bad JSON / not an object / non-string command) is
  rejected as an *invalid* tool call, never sent to confirmation.
- If ``BashSandbox().simulate()`` raises, the call fails *closed* (rejected with
  a visible error) instead of silently falling through to confirmation.
- The ALLOW / DENY / CONFIRM / heredoc paths are unchanged.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from dendrophis.config.schema import DendrophisConfig
from dendrophis.permissions.policy import Decision, PermissionPolicy
from dendrophis.session.tools import SessionToolExecutor
from dendrophis.tools.bash_sandbox import BashSandbox


class FakePolicy:
    """Policy stub that returns a fixed (decision, reason) for check_bash."""

    def __init__(self, decision: Decision, reason: str = "") -> None:
        self._decision = decision
        self._reason = reason

    def check_bash(self, sim: Any) -> tuple[Decision, str]:
        return (self._decision, self._reason)


class MockToolCall:
    def __init__(self, id: str, name: str, arguments: str, index: int = 0) -> None:
        self.id = id
        self.name = name
        self.arguments = arguments
        self.index = index


def _make_executor(debug_logger=None) -> SessionToolExecutor:
    return SessionToolExecutor(
        tool_registry=None,
        tool_executor=None,
        event_bus=None,
        config=DendrophisConfig(),
        pending_confirmations={},
        confirmation_results={},
        cancel_flag=threading.Event(),
        emit=lambda event: None,
        debug_logger=debug_logger,
    )


def _run(executor: SessionToolExecutor, arguments: str, policy: FakePolicy):
    """Invoke _process_bash_tool and return the three output buckets."""
    invalid: list[tuple[int, Any, str]] = []
    approved: list[tuple[int, Any, bool]] = []
    pending: list[tuple[int, Any, str]] = []
    tool_call = MockToolCall(id="call_1", name="bash", arguments=arguments)
    executor._process_bash_tool(0, tool_call, policy, invalid, approved, pending)
    return invalid, approved, pending


def _only_invalid_message(invalid: list) -> str:
    assert len(invalid) == 1
    return invalid[0][2]


# --- Malformed arguments: reject as invalid, never confirm -------------------


def test_invalid_json_rejected_not_confirmed() -> None:
    invalid, approved, pending = _run(_make_executor(), "this is not json", FakePolicy(Decision.CONFIRM))
    assert "Invalid arguments format" in _only_invalid_message(invalid)
    assert approved == []
    assert pending == []  # must NOT fall through to confirmation


def test_non_object_arguments_rejected_not_confirmed() -> None:
    # Valid JSON, but a list not an object -> .get would raise; must be caught.
    invalid, approved, pending = _run(_make_executor(), "[1, 2, 3]", FakePolicy(Decision.CONFIRM))
    assert "expected a JSON object" in _only_invalid_message(invalid)
    assert approved == []
    assert pending == []


def test_non_string_command_rejected_not_confirmed() -> None:
    invalid, approved, pending = _run(_make_executor(), '{"command": 123}', FakePolicy(Decision.CONFIRM))
    assert "'command' must be a string" in _only_invalid_message(invalid)
    assert approved == []
    assert pending == []


def test_empty_arguments_treated_as_empty_command() -> None:
    # Empty/whitespace arguments -> {} -> command "" -> reaches policy (no crash).
    invalid, approved, pending = _run(_make_executor(), "", FakePolicy(Decision.ALLOW))
    assert invalid == []
    assert len(approved) == 1
    assert pending == []


# --- Sandbox failure: fail closed, never silently confirm ---------------------


def test_simulation_failure_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The core audit finding: a simulate() error must NOT become a confirmation."""

    def _explode(self, command: str):
        raise RuntimeError("sandbox exploded")

    monkeypatch.setattr(BashSandbox, "simulate", _explode)

    debug_lines: list[str] = []
    executor = _make_executor(debug_logger=debug_lines.append)

    invalid, approved, pending = _run(executor, '{"command": "ls -la"}', FakePolicy(Decision.CONFIRM))
    assert "Bash simulation failed" in _only_invalid_message(invalid)
    assert "sandbox exploded" in _only_invalid_message(invalid)
    assert approved == []
    assert pending == []  # the bug: this used to be populated (silent confirmation)
    # The failure is surfaced to the debug log, not swallowed.
    assert any("Bash simulation failed" in line for line in debug_lines)


def test_simulation_failure_without_debug_logger_still_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def _explode(self, command: str):
        raise RuntimeError("sandbox exploded")

    monkeypatch.setattr(BashSandbox, "simulate", _explode)

    executor = _make_executor(debug_logger=None)
    invalid, _approved, pending = _run(executor, '{"command": "ls -la"}', FakePolicy(Decision.CONFIRM))
    assert "Bash simulation failed" in _only_invalid_message(invalid)
    assert pending == []


# --- Preserved decision paths -------------------------------------------------


@pytest.mark.anyio
async def test_confirm_still_falls_through() -> None:
    # Async: the CONFIRM path calls _request_confirmation -> asyncio.get_running_loop().
    invalid, approved, pending = _run(_make_executor(), '{"command": "ls"}', FakePolicy(Decision.CONFIRM))
    assert invalid == []
    assert approved == []
    assert len(pending) == 1
    assert pending[0][0] == 0  # call_index preserved


def test_allow_still_approved() -> None:
    invalid, approved, pending = _run(_make_executor(), '{"command": "ls"}', FakePolicy(Decision.ALLOW))
    assert invalid == []
    assert len(approved) == 1
    assert approved[0][2] is True  # silent flag
    assert pending == []


def test_deny_still_invalid() -> None:
    invalid, approved, pending = _run(
        _make_executor(), '{"command": "rm -rf /"}', FakePolicy(Decision.DENY, "dangerous")
    )
    assert "Blocked by permission policy" in _only_invalid_message(invalid)
    assert "dangerous" in _only_invalid_message(invalid)
    assert approved == []
    assert pending == []


def test_heredoc_write_rejected() -> None:
    invalid, approved, pending = _run(
        _make_executor(),
        '{"command": "cat << EOF > file.txt\\nhello\\nEOF"}',
        FakePolicy(Decision.ALLOW),
    )
    assert "should use the 'write' tool" in _only_invalid_message(invalid)
    assert approved == []
    assert pending == []


# --- Integration: end-to-end via execute() ------------------------------------


@pytest.mark.anyio
async def test_execute_bash_simulation_failure_yields_error_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """Through the real execute() flow, a sandbox failure is an error result,
    not a confirmation prompt."""

    def _explode(self, command: str):
        raise RuntimeError("sandbox exploded")

    monkeypatch.setattr(BashSandbox, "simulate", _explode)

    class _BashTool:
        name = "bash"
        self_confirming = False
        silent = False

    class _Registry:
        def get(self, name: str):
            return _BashTool() if name == "bash" else None

    class _Executor:
        async def execute(self, tool_call: Any):
            raise AssertionError("tool must NOT execute when simulation fails")

    events: list[Any] = []
    executor = SessionToolExecutor(
        tool_registry=_Registry(),
        tool_executor=_Executor(),
        event_bus=None,
        config=DendrophisConfig(),
        pending_confirmations={},
        confirmation_results={},
        cancel_flag=threading.Event(),
        emit=lambda event: events.append(event),
    )

    tool_calls = [MockToolCall(id="call_sim", name="bash", arguments='{"command": "ls -la"}')]
    results = await executor.execute(tool_calls)

    assert len(results) == 1
    assert "Bash simulation failed" in results[0].content
    # No confirmation request was emitted for the un-analyzable command.
    from dendrophis.events import ToolConfirmationRequestEvent

    assert not any(isinstance(e, ToolConfirmationRequestEvent) for e in events)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
