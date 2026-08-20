import json
import lzma
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dendrophis.ui.screens.session_picker import SessionPickerScreen


@pytest.mark.asyncio
async def test_session_picker_loading(tmp_path: Path):
    """Test that SessionPickerScreen correctly loads and parses session files."""
    # Create fake session directory structure under tmp_path
    sessions_directory = tmp_path / ".config" / "dendrophis" / "sessions"
    sessions_directory.mkdir(parents=True)

    # Write a fake uncompressed session file
    session1_path = sessions_directory / "session-abc12345.2026-06-01.120000.json"
    session1_data = {
        "session_id": "abc1234567890",
        "timestamp": "2026-06-01T12:00:00",
        "model": "google/gemini-2.5-flash",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello from session one!"},
        ],
    }
    session1_path.write_text(json.dumps(session1_data))

    # Write a fake compressed session file
    session2_path = sessions_directory / "session-xyz67890.2026-06-01.130000.json.xz"
    session2_data = {
        "session_id": "xyz6789012345",
        "timestamp": "2026-06-01T13:00:00",
        "model": "anthropic/claude-3-opus",
        "messages": [
            {"role": "user", "content": "Is there a compressed session here?"},
        ],
    }
    with lzma.open(session2_path, "wb") as compressed_file:
        compressed_file.write(json.dumps(session2_data).encode())

    # Mock Session class and Path.home
    mock_session = MagicMock()

    with patch("pathlib.Path.home", return_value=tmp_path):
        screen = SessionPickerScreen(mock_session)

        # Mock the Textual query_one calls to avoid DOM matching errors
        mock_status_label = MagicMock()
        mock_option_list = MagicMock()

        def mock_query_one(selector, expect_type=None):
            if "status" in selector:
                return mock_status_label
            if "list" in selector:
                return mock_option_list
            return MagicMock()

        screen.query_one = mock_query_one

        # Call the background loader manually and await it
        await screen._load_sessions()

        # Check loaded sessions list
        assert len(screen._all_sessions) == 2

        # Verify both sessions were successfully parsed
        sessions_by_id = {session["session_id"]: session for session in screen._all_sessions}
        assert "abc1234567890" in sessions_by_id
        assert "xyz6789012345" in sessions_by_id

        assert sessions_by_id["abc1234567890"]["preview"] == "Hello from session one!"
        assert sessions_by_id["xyz6789012345"]["preview"] == "Is there a compressed session here?"
        assert sessions_by_id["abc1234567890"]["message_count"] == 1
        assert sessions_by_id["xyz6789012345"]["message_count"] == 1


@pytest.mark.asyncio
async def test_session_picker_deletion(tmp_path: Path):
    """Test that SessionPickerScreen allows deleting empty sessions but prevents deleting non-empty sessions."""
    sessions_directory = tmp_path / ".config" / "dendrophis" / "sessions"
    sessions_directory.mkdir(parents=True)

    # Empty session
    empty_path = sessions_directory / "session-abc12345.2026-06-01.120000.json"
    empty_data = {
        "session_id": "abc1234567890",
        "timestamp": "2026-06-01T12:00:00",
        "messages": [],
    }
    empty_path.write_text(json.dumps(empty_data))

    # Non-empty session
    non_empty_path = sessions_directory / "session-xyz67890.2026-06-01.130000.json"
    non_empty_data = {
        "session_id": "xyz6789012345",
        "timestamp": "2026-06-01T13:00:00",
        "messages": [
            {"role": "user", "content": "Hello"},
        ],
    }
    non_empty_path.write_text(json.dumps(non_empty_data))

    mock_session = MagicMock()

    with patch("pathlib.Path.home", return_value=tmp_path):
        screen = SessionPickerScreen(mock_session)

        # Mock the Textual query_one calls
        mock_status_label = MagicMock()
        mock_option_list = MagicMock()
        mock_search_input = MagicMock(value="")

        def mock_query_one(selector, expect_type=None):
            if "status" in selector:
                return mock_status_label
            if "list" in selector:
                return mock_option_list
            if "search" in selector:
                return mock_search_input
            return MagicMock()

        screen.query_one = mock_query_one
        screen.notify = MagicMock()

        await screen._load_sessions()
        assert len(screen._all_sessions) == 2

        # Verify which one is which in screen._all_sessions
        # Sorted reverse-chronologically by mtime (which is basically write order)
        # 1. Try deleting the non-empty session
        # Mock highlight and selected option
        mock_option_list.highlighted = 0
        mock_option_list.get_option_at_index = MagicMock(return_value=MagicMock(id=str(non_empty_path)))

        screen.action_delete_session()

        # Should show a warning and NOT delete the file
        screen.notify.assert_called_once_with("Only sessions with 0 messages can be deleted", severity="warning")
        assert non_empty_path.exists()
        assert len(screen._all_sessions) == 2

        # Reset notify mock
        screen.notify.reset_mock()

        # 2. Delete the empty session
        mock_option_list.highlighted = 1
        mock_option_list.get_option_at_index = MagicMock(return_value=MagicMock(id=str(empty_path)))

        screen.action_delete_session()

        # Should delete the file and remove from all_sessions
        assert not empty_path.exists()
        assert len(screen._all_sessions) == 1
        assert screen._all_sessions[0]["session_id"] == "xyz6789012345"
        assert screen.notify.call_count == 0
