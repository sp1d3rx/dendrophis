"""Tests for tool arguments formatting in UI components."""

from __future__ import annotations

import json

from dendrophis.ui.widgets.chat_view import _format_tool_args


def test_format_tool_args_bash() -> None:
    arguments_json = json.dumps({"command": "pytest tests/"})
    formatted_result = _format_tool_args("bash", arguments_json)
    assert "[cyan]pytest tests/[/cyan]" in formatted_result


def test_format_tool_args_search_memory_with_query_and_tags() -> None:
    arguments_json = json.dumps({"query": "architectural conventions", "tags": ["architecture", "design"]})
    formatted_result = _format_tool_args("search_memory", arguments_json)
    assert "[cyan]architectural conventions[/cyan]" in formatted_result
    assert "[dim](architecture, design)[/dim]" in formatted_result


def test_format_tool_args_search_memory_tags_only() -> None:
    arguments_json = json.dumps({"tags": ["convention"]})
    formatted_result = _format_tool_args("search_memory", arguments_json)
    assert "[dim](convention)[/dim]" in formatted_result


def test_format_tool_args_search_memory_empty_query() -> None:
    arguments_json = json.dumps({"query": "", "tag": "project-rule"})
    formatted_result = _format_tool_args("search_memory", arguments_json)
    assert "[dim](project-rule)[/dim]" in formatted_result


def test_format_tool_args_search_memory_limit_only() -> None:
    arguments_json = json.dumps({"limit": 5})
    formatted_result = _format_tool_args("search_memory", arguments_json)
    assert "[dim]limit: 5[/dim]" in formatted_result


def test_format_tool_args_read_file() -> None:
    arguments_json = json.dumps({"file_path": "dendrophis/ui/widgets/chat_view.py", "offset": 1, "limit": 100})
    formatted_result = _format_tool_args("read_file", arguments_json)
    assert "chat_view.py" in formatted_result
    assert "[1:100]" in formatted_result


def test_format_tool_args_unknown_tool_key_value_fallback() -> None:
    arguments_json = json.dumps({"custom_key": "custom_value", "number_setting": 42})
    formatted_result = _format_tool_args("unknown_custom_tool", arguments_json)
    assert "[dim]custom_key=[/dim][cyan]custom_value[/cyan]" in formatted_result
    assert "[dim]number_setting=[/dim][cyan]42[/cyan]" in formatted_result


def test_format_tool_args_empty_arguments() -> None:
    formatted_result = _format_tool_args("search_memory", "")
    assert formatted_result == ""
    formatted_result_empty_object = _format_tool_args("search_memory", "{}")
    assert formatted_result_empty_object == ""
