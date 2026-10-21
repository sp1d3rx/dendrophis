"""Tests pinning BashSandbox's regex-based safety heuristics.

The sandbox is a pre-execution signal (categorization + danger flags); the
real boundary is the permission system plus human confirmation. These tests
pin the current heuristic behavior so refactors cannot silently change it.
"""

from __future__ import annotations

from dendrophis.tools.bash_sandbox import BashSandbox, CommandCategory, is_heredoc_write_pattern


class TestHeredocWritePattern:
    """is_heredoc_write_pattern is a UX guard: steer heredoc/redirect writes to the write tool."""

    def test_heredoc_redirect_same_line(self) -> None:
        assert is_heredoc_write_pattern("cat << EOF > file.txt")

    def test_heredoc_quoted_delimiter(self) -> None:
        assert is_heredoc_write_pattern("cat << 'EOF' > file.txt")

    def test_heredoc_with_env_prefix(self) -> None:
        assert is_heredoc_write_pattern("/usr/bin/env cat << 'EOF' > file.txt")

    def test_heredoc_multiline_then_redirect(self) -> None:
        assert is_heredoc_write_pattern("cat << EOF\nline\nEOF\n> file.txt")

    def test_echo_multiline_redirect(self) -> None:
        assert is_heredoc_write_pattern('echo "hello"\n> file.txt')

    def test_multiline_writer_with_redirect(self) -> None:
        assert is_heredoc_write_pattern("cd /tmp\ncat a.txt\ncat b.txt > merged.txt")

    def test_plain_heredoc_without_redirect_allowed(self) -> None:
        assert not is_heredoc_write_pattern("cat << EOF\ncontent\nEOF")

    def test_oneliner_redirect_allowed(self) -> None:
        assert not is_heredoc_write_pattern("ls -la > listing.txt")

    def test_multiline_read_only_allowed(self) -> None:
        assert not is_heredoc_write_pattern("cd /tmp\nls -la\nwc -l a.txt")

    def test_empty_allowed(self) -> None:
        assert not is_heredoc_write_pattern("")

    def test_returns_bool(self) -> None:
        for cmd in ("cat << EOF > f", "ls", "cat a\ncat b > c"):
            assert isinstance(is_heredoc_write_pattern(cmd), bool)


class TestSandboxRegexGuards:
    """The top-level regex guards in BashSandbox._simulate_str."""

    def test_fork_bomb_flagged(self) -> None:
        result = BashSandbox().simulate(":(){ :|:& };:")
        assert result.dangerous
        assert result.reason == "fork bomb"

    def test_device_redirect_flagged(self) -> None:
        result = BashSandbox().simulate("cat /dev/zero > /dev/sda")
        assert result.dangerous
        assert "direct device write" in result.reason

    def test_rm_rf_root_flagged(self) -> None:
        result = BashSandbox().simulate("rm -rf /")
        assert result.dangerous
        assert "rm -rf" in result.reason

    def test_benign_command_not_dangerous(self) -> None:
        result = BashSandbox().simulate("ls -la")
        assert not result.dangerous
        assert result.categories == {CommandCategory.FILESYSTEM_READ}
