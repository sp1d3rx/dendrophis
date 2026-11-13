"""Tests for explain_confirmation — the 'why' shown on tool confirmation prompts.

The confirmation event protocol carries no reason field, so the UI re-derives
a human-readable explanation from config + arguments via explain_confirmation.
These pin the wording for bash category reasons, tool-level reasons, and the
fallback paths (malformed arguments, unknown tools).
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, ClassVar

from dendrophis.config.schema import DendrophisConfig
from dendrophis.events import ToolConfirmationRequestEvent
from dendrophis.permissions import PermissionPolicy, explain_confirmation
from dendrophis.permissions.policy import Decision
from dendrophis.session.tools import SessionToolExecutor
from dendrophis.tools.bash_sandbox import BashSandbox


def _bash_args(command: str) -> str:
    return json.dumps({"command": command})


def test_bash_package_manager_reason() -> None:
    reason = explain_confirmation(DendrophisConfig(), "bash", _bash_args("pip install requests"))
    assert "package_manager" in reason
    assert "not auto-approved" in reason
    # Default config auto-approves filesystem_read, which should be mentioned.
    assert "filesystem_read" in reason


def test_bash_multiple_categories_listed() -> None:
    reason = explain_confirmation(DendrophisConfig(), "bash", _bash_args("curl https://x.com && git status"))
    assert "git" in reason
    assert "network" in reason


def test_bash_auto_approved_categories_fall_back_to_tool_level() -> None:
    # 'ls' is filesystem_read, which is auto-approved by default — such a
    # command would not prompt at all; the fallback names the tool-level rule.
    reason = explain_confirmation(DendrophisConfig(), "bash", _bash_args("ls -la"))
    assert "require_confirmation" in reason


def test_require_confirmation_tool_reason() -> None:
    reason = explain_confirmation(DendrophisConfig(), "delete_memory", json.dumps({"key": "k"}))
    assert "delete_memory" in reason
    assert "require_confirmation" in reason


def test_unknown_tool_generic_reason() -> None:
    reason = explain_confirmation(DendrophisConfig(), "some_tool", "{}")
    assert "some_tool" in reason
    assert "requires confirmation" in reason


def test_malformed_arguments_fall_back_without_raising() -> None:
    assert "require_confirmation" in explain_confirmation(DendrophisConfig(), "bash", "not json")
    assert "require_confirmation" in explain_confirmation(DendrophisConfig(), "bash", "")


def test_custom_auto_approve_categories_change_reason() -> None:
    config = DendrophisConfig()
    config.permissions.bash.auto_approve_categories = ["filesystem_read", "package_manager"]
    reason = explain_confirmation(config, "bash", _bash_args("pip install requests"))
    assert "require_confirmation" in reason
    reason = explain_confirmation(config, "bash", _bash_args("curl https://x.com"))
    assert "network" in reason


async def test_screen_renders_reason_label() -> None:
    from textual.app import App

    from dendrophis.ui.screens.tool_confirmation import ToolConfirmationScreen

    class _App(App[None]):
        pass

    app = _App()
    async with app.run_test():
        screen = ToolConfirmationScreen("rid", "bash", _bash_args("pip install requests"), None, reason="why text")  # type: ignore[arg-type]
        await app.push_screen(screen)
        label = screen.query_one("#reason")
        assert "why text" in str(label.render())


async def test_screen_omits_reason_label_when_empty() -> None:
    from textual.app import App

    from dendrophis.ui.screens.tool_confirmation import ToolConfirmationScreen

    class _App(App[None]):
        pass

    app = _App()
    async with app.run_test():
        screen = ToolConfirmationScreen("rid", "bash", _bash_args("ls"), None)  # type: ignore[arg-type]
        await app.push_screen(screen)
        assert not screen.query("#reason")


# ---------------------------------------------------------------------------
# Reason carried on the event itself
# ---------------------------------------------------------------------------


def test_event_reason_defaults_to_empty() -> None:
    event = ToolConfirmationRequestEvent(request_id="r", tool_name="bash", arguments="{}")
    assert event.reason == ""


def test_check_bash_confirm_carries_explanation() -> None:
    policy = PermissionPolicy.from_config(DendrophisConfig())
    decision, reason = policy.check_bash(BashSandbox().simulate("pip install requests"))
    assert decision == Decision.CONFIRM
    assert "package_manager" in reason


def test_check_bash_allow_reason_stays_empty() -> None:
    policy = PermissionPolicy.from_config(DendrophisConfig())
    decision, reason = policy.check_bash(BashSandbox().simulate("ls -la"))
    assert decision == Decision.ALLOW
    assert reason == ""


def _config_bash_tool_allowed() -> DendrophisConfig:
    """Config matching the reported bug: bash tool-level allowed (not in
    require_confirmation), filesystem_delete not auto-approved."""
    config = DendrophisConfig()
    config.permissions.require_confirmation = []
    return config


def test_check_bash_non_auto_approved_category_confirms_even_when_bash_allowed() -> None:
    # Regression: 'rm file' (filesystem_delete) must prompt even when bash is
    # removed from require_confirmation — category-level "require confirm"
    # must not fall through to a tool-level ALLOW.
    policy = PermissionPolicy.from_config(_config_bash_tool_allowed())
    decision, reason = policy.check_bash(BashSandbox().simulate("rm test-ping.txt"))
    assert decision == Decision.CONFIRM
    assert "filesystem_delete" in reason


def test_check_bash_auto_approved_still_allows_when_bash_tool_allowed() -> None:
    policy = PermissionPolicy.from_config(_config_bash_tool_allowed())
    decision, _reason = policy.check_bash(BashSandbox().simulate("ls -la"))
    assert decision == Decision.ALLOW


def test_check_bash_denied_category_still_denies_when_bash_tool_allowed() -> None:
    policy = PermissionPolicy.from_config(_config_bash_tool_allowed())
    decision, reason = policy.check_bash(BashSandbox().simulate("rm -rf /"))
    assert decision == Decision.DENY
    assert reason


def test_check_bash_tool_level_deny_rules_still_apply() -> None:
    config = _config_bash_tool_allowed()
    config.permissions.denied_tools = ["bash"]
    policy = PermissionPolicy.from_config(config)
    decision, reason = policy.check_bash(BashSandbox().simulate("rm test-ping.txt"))
    assert decision == Decision.DENY
    assert "denied_tools" in reason

    config = _config_bash_tool_allowed()
    config.permissions.allowed_tools = ["read"]
    policy = PermissionPolicy.from_config(config)
    decision, reason = policy.check_bash(BashSandbox().simulate("rm test-ping.txt"))
    assert decision == Decision.DENY
    assert "allowed_tools" in reason


class _StubTool:
    self_confirming = False
    parameters: ClassVar[dict[str, Any]] = {}
    silent = False


class _StubRegistry:
    def get(self, name: str) -> _StubTool | None:
        return _StubTool()


class _StubToolCall:
    def __init__(self, name: str, arguments: str) -> None:
        self.id = "call_1"
        self.name = name
        self.arguments = arguments
        self.index = 0


def _make_executor(config: DendrophisConfig, events: list[Any]) -> SessionToolExecutor:
    return SessionToolExecutor(
        tool_registry=_StubRegistry(),
        tool_executor=None,
        event_bus=None,
        config=config,
        pending_confirmations={},
        confirmation_results={},
        cancel_flag=threading.Event(),
        emit=events.append,
    )


async def _run_until_confirmation(executor: SessionToolExecutor, events: list[Any], tool_call: _StubToolCall) -> Any:
    task = asyncio.create_task(executor.execute([tool_call]))
    while not events and not task.done():
        await asyncio.sleep(0.01)
    assert events, "executor finished without emitting a confirmation request"
    event = events[0]
    executor.respond_to_confirmation(event.request_id, False)
    await task
    return event


async def test_bash_confirmation_event_carries_category_reason() -> None:
    events: list[Any] = []
    executor = _make_executor(DendrophisConfig(), events)
    event = await _run_until_confirmation(executor, events, _StubToolCall("bash", _bash_args("pip install requests")))
    assert isinstance(event, ToolConfirmationRequestEvent)
    assert "package_manager" in event.reason


async def test_tool_confirmation_event_carries_require_confirmation_reason() -> None:
    events: list[Any] = []
    executor = _make_executor(DendrophisConfig(), events)
    event = await _run_until_confirmation(executor, events, _StubToolCall("delete_memory", json.dumps({"key": "k"})))
    assert isinstance(event, ToolConfirmationRequestEvent)
    assert "delete_memory" in event.reason
    assert "require_confirmation" in event.reason


async def test_bash_rm_prompts_when_bash_tool_allowed_but_category_not_auto_approved() -> None:
    # Regression for the reported bug: with require_confirmation=[] the rm
    # call used to be silently approved; it must now raise a confirmation.
    events: list[Any] = []
    executor = _make_executor(_config_bash_tool_allowed(), events)
    event = await _run_until_confirmation(executor, events, _StubToolCall("bash", _bash_args("rm test-ping.txt")))
    assert isinstance(event, ToolConfirmationRequestEvent)
    assert "filesystem_delete" in event.reason


# ---------------------------------------------------------------------------
# Confirmation modal dismissed on timeout/cancel
# ---------------------------------------------------------------------------


async def test_confirmation_screen_dismissed_on_cancel_event() -> None:
    from types import SimpleNamespace

    from textual.app import App

    from dendrophis.events import ToolConfirmationCancelledEvent
    from dendrophis.ui.screens.main_events import MainScreenEventHandler
    from dendrophis.ui.screens.tool_confirmation import ToolConfirmationScreen

    class _App(App[None]):
        pass

    app = _App()
    async with app.run_test() as pilot:
        screen = ToolConfirmationScreen("r1", "bash", _bash_args("rm x"), None)  # type: ignore[arg-type]
        await app.push_screen(screen)
        assert app.screen is screen
        handler = SimpleNamespace(app=app)
        MainScreenEventHandler._on_tool_confirmation_cancelled(
            handler,
            ToolConfirmationCancelledEvent(request_id="r1", reason="timeout"),  # type: ignore[arg-type]
        )
        await pilot.pause()
        assert app.screen is not screen


async def test_confirmation_screen_kept_when_cancel_event_is_for_other_request() -> None:
    from types import SimpleNamespace

    from textual.app import App

    from dendrophis.events import ToolConfirmationCancelledEvent
    from dendrophis.ui.screens.main_events import MainScreenEventHandler
    from dendrophis.ui.screens.tool_confirmation import ToolConfirmationScreen

    class _App(App[None]):
        pass

    app = _App()
    async with app.run_test() as pilot:
        screen = ToolConfirmationScreen("r1", "bash", _bash_args("rm x"), None)  # type: ignore[arg-type]
        await app.push_screen(screen)
        handler = SimpleNamespace(app=app)
        MainScreenEventHandler._on_tool_confirmation_cancelled(
            handler,
            ToolConfirmationCancelledEvent(request_id="other", reason="timeout"),  # type: ignore[arg-type]
        )
        await pilot.pause()
        assert app.screen is screen
