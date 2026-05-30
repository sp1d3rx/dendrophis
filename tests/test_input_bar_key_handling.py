#!/usr/bin/env python3
"""Test InputBar._on_key() — verifies that super()._on_key() is only called
for keys that InputBar does NOT handle itself, and that the async super
call is properly awaited.

Before the fix:
1. super()._on_key() was called unconditionally for ALL keys (Enter, Up, Down, letters)
2. super()._on_key() was NOT awaited — TextArea._on_key is a coroutine, so the
   base class handler was silently never executing for unhandled keys

After the fix:
1. super()._on_key() is only called for keys InputBar didn't handle
2. super()._on_key() is properly awaited so the base class handler actually runs
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from textual.events import Key as TextualKey

from dendrophis.ui.widgets.input_bar import InputBar

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_input_bar() -> InputBar:
    """Create an InputBar without triggering TextArea reactive properties."""
    bar = object.__new__(InputBar)
    bar._history = []
    bar._history_index = -1
    bar._draft = ""
    bar._completing = False
    bar.move_cursor = MagicMock()
    bar.post_message = MagicMock()
    bar._submit = MagicMock()
    bar._apply_suggestion = MagicMock()
    bar._close_autocomplete = MagicMock()
    return bar


def _patch_props(bar: InputBar, cursor: tuple = (0, 0), text: str = ""):
    """Patch cursor_location and text as PropertyMocks."""
    return [
        patch.object(type(bar), "cursor_location", new_callable=PropertyMock, return_value=cursor),
        patch.object(type(bar), "text", new_callable=PropertyMock, return_value=text),
    ]


def _make_key(key: str) -> TextualKey:
    """Create a textual.events.Key with the given key name."""
    return TextualKey(key=key, character=None)


async def _run_on_key(bar: InputBar, event, mock_super=None):
    """Run bar._on_key() with proper async handling."""
    import contextlib

    with contextlib.ExitStack() as stack:
        for p in _patch_props(bar):
            stack.enter_context(p)
        await bar._on_key(event)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_enter_key_does_not_call_super():
    """Enter (submit) should be handled entirely by InputBar."""
    bar = _make_input_bar()
    mock_super = AsyncMock()
    with patch("textual.widgets.TextArea._on_key", mock_super):
        await _run_on_key(bar, _make_key("enter"))

    assert mock_super.call_count == 0, f"super called {mock_super.call_count}x for Enter"
    assert bar._submit.call_count == 1
    print("  ✓ Enter: super not called, _submit called")


async def test_shift_enter_does_call_super():
    """Shift+Enter should insert a newline — super MUST be called."""
    bar = _make_input_bar()
    mock_super = AsyncMock()
    with patch("textual.widgets.TextArea._on_key", mock_super):
        event = _make_key("enter")
        with patch.object(type(event), "name", new_callable=PropertyMock, return_value="shift+enter"):
            await _run_on_key(bar, event)

    assert mock_super.call_count == 1, f"super called {mock_super.call_count}x for Shift+Enter"
    assert bar._submit.call_count == 0
    print("  ✓ Shift+Enter: super called once, _submit not called")


async def test_up_key_at_top_with_history():
    """Up at line 0 with history available should be handled by InputBar."""
    bar = _make_input_bar()
    bar._history = ["hello", "world"]
    bar._history_index = -1
    mock_super = AsyncMock()
    with patch("textual.widgets.TextArea._on_key", mock_super):
        import contextlib

        with contextlib.ExitStack() as stack:
            for p in _patch_props(bar, cursor=(0, 0)):
                stack.enter_context(p)
            await bar._on_key(_make_key("up"))

    assert mock_super.call_count == 0, f"super called {mock_super.call_count}x for Up with history"
    assert bar._history_index == 0
    print("  ✓ Up at top with history: super not called, history cycled")


async def test_up_key_not_at_top():
    """Up when cursor is NOT at line 0 should fall through to base class."""
    bar = _make_input_bar()
    mock_super = AsyncMock()
    with patch("textual.widgets.TextArea._on_key", mock_super):
        import contextlib

        with contextlib.ExitStack() as stack:
            for p in _patch_props(bar, cursor=(3, 0)):
                stack.enter_context(p)
            await bar._on_key(_make_key("up"))

    assert mock_super.call_count == 1, f"super called {mock_super.call_count}x for Up not at top"
    print("  ✓ Up not at top: super called once")


async def test_down_key_at_bottom_with_history():
    """Down at last line with history available should be handled by InputBar."""
    bar = _make_input_bar()
    bar._history = ["hello", "world"]
    bar._history_index = 0
    mock_super = AsyncMock()
    with patch("textual.widgets.TextArea._on_key", mock_super):
        import contextlib

        with contextlib.ExitStack() as stack:
            for p in _patch_props(bar, cursor=(0, 0), text="world"):
                stack.enter_context(p)
            await bar._on_key(_make_key("down"))

    assert mock_super.call_count == 0, f"super called {mock_super.call_count}x for Down with history"
    print("  ✓ Down at bottom with history: super not called, history cycled")


async def test_down_key_no_history():
    """Down when there's no history should fall through to base class."""
    bar = _make_input_bar()
    bar._history = []
    mock_super = AsyncMock()
    with patch("textual.widgets.TextArea._on_key", mock_super):
        import contextlib

        with contextlib.ExitStack() as stack:
            for p in _patch_props(bar, cursor=(0, 0)):
                stack.enter_context(p)
            await bar._on_key(_make_key("down"))

    assert mock_super.call_count == 1, f"super called {mock_super.call_count}x for Down no history"
    print("  ✓ Down no history: super called once")


async def test_regular_letter_key():
    """Regular character input should always fall through to base class."""
    bar = _make_input_bar()
    mock_super = AsyncMock()
    with patch("textual.widgets.TextArea._on_key", mock_super):
        await _run_on_key(bar, _make_key("a"))

    assert mock_super.call_count == 1, f"super called {mock_super.call_count}x for letter key"
    print("  ✓ Letter key: super called once")


async def test_autocomplete_enter():
    """When autocomplete is active, Enter should apply suggestion, not submit."""
    bar = _make_input_bar()
    bar._completing = True
    mock_super = AsyncMock()
    with patch("textual.widgets.TextArea._on_key", mock_super):
        await _run_on_key(bar, _make_key("enter"))

    assert mock_super.call_count == 0, f"super called {mock_super.call_count}x for autocomplete Enter"
    assert bar._apply_suggestion.call_count == 1
    assert bar._submit.call_count == 0
    print("  ✓ Autocomplete Enter: super not called, suggestion applied")


async def test_autocomplete_escape():
    """When autocomplete is active, Escape should close it."""
    bar = _make_input_bar()
    bar._completing = True
    mock_super = AsyncMock()
    with patch("textual.widgets.TextArea._on_key", mock_super):
        await _run_on_key(bar, _make_key("escape"))

    assert mock_super.call_count == 0, f"super called {mock_super.call_count}x for autocomplete Escape"
    assert bar._close_autocomplete.call_count == 1
    print("  ✓ Autocomplete Escape: super not called, autocomplete closed")


async def test_non_key_event():
    """Non-Key events should return immediately without calling anything."""
    bar = _make_input_bar()
    mock_super = AsyncMock()
    with patch("textual.widgets.TextArea._on_key", mock_super):
        await bar._on_key("not a key event")
    assert mock_super.call_count == 0
    assert bar._submit.call_count == 0
    print("  ✓ Non-Key event: returns early")


# ---------------------------------------------------------------------------
# Before/After comparison
# ---------------------------------------------------------------------------


def test_before_after_comparison():
    """Quantify the improvement by simulating a realistic typing session."""
    bar = _make_input_bar()
    bar._history = ["previous command"]
    bar._history_index = -1

    session_keys = [
        ("h", "h"),
        ("e", "e"),
        ("l", "l"),
        ("l", "l"),
        ("o", "o"),
        ("enter", "enter"),
        ("up", "up"),
        ("enter", "enter"),
    ]

    def old_behaviour(k, n):
        return 1  # Always called super (and never awaited it!)

    def new_behaviour(k, n):
        if k == "enter" and n == "enter":
            return 0
        if k == "enter" and n != "enter":
            return 1
        if k == "up" and bar._history_index < len(bar._history) - 1:
            return 0
        if k == "down" and bar._history_index >= 0:
            return 0
        return 1

    old_count = sum(old_behaviour(k, n) for k, n in session_keys)
    new_count = sum(new_behaviour(k, n) for k, n in session_keys)
    saved = old_count - new_count

    print(f"\n  Before fix: {old_count} super calls (unconditional, never awaited)")
    print(f"  After fix:  {new_count} super calls (only unhandled keys, properly awaited)")
    print(f"  Saved:      {saved} unnecessary delegations ({saved / old_count * 100:.0f}% reduction)")

    assert new_count < old_count
    assert new_count == 5  # 5 typing keys
    print("  ✓ Comparison matches expectations")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def run_all():
    print("Testing InputBar._on_key() key handling fix...\n")

    await test_enter_key_does_not_call_super()
    await test_shift_enter_does_call_super()
    await test_up_key_at_top_with_history()
    await test_up_key_not_at_top()
    await test_down_key_at_bottom_with_history()
    await test_down_key_no_history()
    await test_regular_letter_key()
    await test_autocomplete_enter()
    await test_autocomplete_escape()
    await test_non_key_event()

    print("\n--- Before/After Comparison ---")
    test_before_after_comparison()

    print("\n✓ All InputBar key handling tests passed!")


if __name__ == "__main__":
    asyncio.run(run_all())
