"""In-memory bash command simulator for pre-execution safety analysis.

Parses command strings into typed Effect objects describing what the command
would do, then flags dangerous effects. Handles chaining, subshells, backtick
substitution, process substitution, and common wrapper commands.

Usage:
    result = BashSandbox().simulate(command)
    if result.dangerous:
        block(result.reason)

    # Policy-based allow/deny by category
    result.is_denied({CommandCategory.NETWORK, CommandCategory.SYSTEM_DESTRUCTIVE})
    result.is_allowed({CommandCategory.FILESYSTEM_READ, CommandCategory.FILESYSTEM_WRITE})
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


class CommandCategory(StrEnum):
    FILESYSTEM_READ = "filesystem_read"
    FILESYSTEM_WRITE = "filesystem_write"
    FILESYSTEM_DELETE = "filesystem_delete"
    FILESYSTEM_PERMISSION = "filesystem_permission"
    NETWORK = "network"
    PROCESS_CONTROL = "process_control"
    SYSTEM_DESTRUCTIVE = "system_destructive"
    PACKAGE_MANAGER = "package_manager"
    GIT = "git"
    SHELL_EXECUTION = "shell_execution"
    ENVIRONMENT = "environment"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Effects
# ---------------------------------------------------------------------------


@dataclass
class Effect:
    command: str


@dataclass
class FileReadEffect(Effect):
    paths: list[str] = field(default_factory=list)
    category: CommandCategory = CommandCategory.FILESYSTEM_READ


@dataclass
class FileWriteEffect(Effect):
    path: str = ""
    truncate: bool = False
    category: CommandCategory = CommandCategory.FILESYSTEM_WRITE


@dataclass
class FileDeleteEffect(Effect):
    paths: list[str] = field(default_factory=list)
    recursive: bool = False
    force: bool = False
    category: CommandCategory = CommandCategory.FILESYSTEM_DELETE


@dataclass
class PermissionChangeEffect(Effect):
    mode: str = ""
    paths: list[str] = field(default_factory=list)
    recursive: bool = False
    category: CommandCategory = CommandCategory.FILESYSTEM_PERMISSION


@dataclass
class DeviceAccessEffect(Effect):
    device: str = ""
    category: CommandCategory = CommandCategory.SYSTEM_DESTRUCTIVE


@dataclass
class ForkBombEffect(Effect):
    category: CommandCategory = CommandCategory.SYSTEM_DESTRUCTIVE


@dataclass
class NetworkEffect(Effect):
    url: str = ""
    tool: str = ""
    category: CommandCategory = CommandCategory.NETWORK


@dataclass
class ProcessControlEffect(Effect):
    signal: str = ""
    targets: list[str] = field(default_factory=list)
    category: CommandCategory = CommandCategory.PROCESS_CONTROL


@dataclass
class PackageManagerEffect(Effect):
    manager: str = ""
    operation: str = ""
    packages: list[str] = field(default_factory=list)
    category: CommandCategory = CommandCategory.PACKAGE_MANAGER


@dataclass
class GitEffect(Effect):
    subcommand: str = ""
    flags: list[str] = field(default_factory=list)
    is_destructive: bool = False
    category: CommandCategory = CommandCategory.GIT


@dataclass
class ShellExecutionEffect(Effect):
    """eval, exec, source, or any nested shell invocation."""

    inner_effects: list[Effect] = field(default_factory=list)
    category: CommandCategory = CommandCategory.SHELL_EXECUTION


@dataclass
class EnvironmentEffect(Effect):
    variables: dict[str, str] = field(default_factory=dict)
    category: CommandCategory = CommandCategory.ENVIRONMENT


@dataclass
class UnknownCommandEffect(Effect):
    category: CommandCategory = CommandCategory.UNKNOWN


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class SimResult:
    effects: list[Effect] = field(default_factory=list)
    dangerous: bool = False
    reason: str = ""

    def add(self, effect: Effect) -> None:
        self.effects.append(effect)

    def flag(self, reason: str) -> None:
        self.dangerous = True
        if not self.reason:
            self.reason = reason

    @property
    def categories(self) -> set[CommandCategory]:
        return {e.category for e in self.effects if hasattr(e, "category")}

    def is_denied(self, denied: set[CommandCategory]) -> bool:
        """True if any effect's category is in the denied set."""
        return bool(self.categories & denied) or self.dangerous

    def is_allowed(self, allowed: set[CommandCategory]) -> bool:
        """True if all effect categories are within the allowed set."""
        return self.categories.issubset(allowed) and not self.dangerous


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FORK_BOMB = re.compile(r":\(\)\{")
_DEVICE_REDIRECT = re.compile(r">\s*/dev/(sda\w*|mem|kmem|hd\w+|nvme\w+)", re.IGNORECASE)
_RM_DANGEROUS_TARGETS = {"/", "/*", "~", "~/", "$HOME", "${HOME}", "$HOME/", "${HOME}/"}

# Passthrough wrappers that just delegate to the next command
_DELEGATION_CMDS = {"sudo", "doas", "nice", "nohup", "timeout", "time", "strace", "ltrace", "watch"}

# Package managers and their typical install/remove verbs
_PACKAGE_MANAGERS: dict[str, list[str]] = {
    "pip": ["install", "uninstall", "download"],
    "pip3": ["install", "uninstall", "download"],
    "uv": ["pip", "add", "remove", "sync"],
    "npm": ["install", "uninstall", "ci", "update"],
    "yarn": ["add", "remove", "install"],
    "pnpm": ["add", "remove", "install"],
    "brew": ["install", "uninstall", "remove", "upgrade"],
    "apt": ["install", "remove", "purge", "upgrade"],
    "apt-get": ["install", "remove", "purge", "upgrade"],
    "yum": ["install", "remove", "update"],
    "dnf": ["install", "remove", "update"],
    "pacman": ["-S", "-R", "-U"],
    "apk": ["add", "del"],
}

# Git subcommands that can be destructive
_GIT_DESTRUCTIVE = {"reset", "clean", "checkout", "rebase", "push", "force-push"}


# ---------------------------------------------------------------------------
# Sandbox
# ---------------------------------------------------------------------------


class BashSandbox:
    """Simulate bash command execution and return a categorized SimResult."""

    def simulate(self, command: str) -> SimResult:
        result = SimResult()
        self._simulate_str(command, result)
        return result

    # -- Top-level -----------------------------------------------------------

    def _simulate_str(self, command: str, result: SimResult) -> None:
        if _FORK_BOMB.search(command):
            result.add(ForkBombEffect(command=command))
            result.flag("fork bomb")
            return

        if _DEVICE_REDIRECT.search(command):
            m = _DEVICE_REDIRECT.search(command)
            result.add(DeviceAccessEffect(command=command, device=m.group(0).strip()))
            result.flag(f"direct device write: {m.group(0).strip()}")
            return

        # Normalise backtick substitution → $(...) before further processing
        command = self._normalise_backticks(command)

        for segment in self._split_operators(command):
            self._simulate_segment(segment, result)

    def _simulate_segment(self, segment: str, result: SimResult) -> None:
        segment = segment.strip()
        if not segment:
            return

        # Extract and recurse into all substitution forms before tokenising
        for inner in self._extract_substitutions(segment):
            inner_result = SimResult()
            self._simulate_str(inner, inner_result)
            shell_effect = ShellExecutionEffect(command=inner, inner_effects=inner_result.effects)
            result.add(shell_effect)
            if inner_result.dangerous:
                result.flag(f"dangerous substitution: {inner_result.reason}")

        try:
            tokens = shlex.split(segment)
        except ValueError:
            result.flag(f"unparseable command: {segment[:80]}")
            return

        if not tokens:
            return

        cmd = Path(tokens[0]).name

        # Unwrap pure delegation wrappers
        if cmd in _DELEGATION_CMDS:
            # Skip flags, re-simulate the remainder
            rest = [t for t in tokens[1:] if not t.startswith("-") or "=" in t]
            if rest:
                self._simulate_segment(shlex.join(rest), result)
            return

        # bash/sh/zsh -c "..."
        if cmd in ("bash", "sh", "zsh", "fish", "dash", "ksh") and "-c" in tokens:
            idx = tokens.index("-c")
            if idx + 1 < len(tokens):
                inner_result = SimResult()
                self._simulate_str(tokens[idx + 1], inner_result)
                result.add(ShellExecutionEffect(command=segment, inner_effects=inner_result.effects))
                if inner_result.dangerous:
                    result.flag(f"dangerous shell -c: {inner_result.reason}")
                result.effects.extend(inner_result.effects)
            return

        # eval / exec
        if cmd in ("eval", "exec"):
            inner_cmd = shlex.join(tokens[1:])
            inner_result = SimResult()
            self._simulate_str(inner_cmd, inner_result)
            result.add(ShellExecutionEffect(command=segment, inner_effects=inner_result.effects))
            if inner_result.dangerous:
                result.flag(f"dangerous {cmd}: {inner_result.reason}")
            result.effects.extend(inner_result.effects)
            return

        # source / .
        if cmd in ("source", "."):
            result.add(ShellExecutionEffect(command=segment, inner_effects=[]))
            # Can't know what the file contains statically; flag as shell execution
            return

        # env VAR=val cmd args
        if cmd == "env":
            env_vars = {}
            rest_tokens = []
            for t in tokens[1:]:
                if "=" in t and not t.startswith("-"):
                    k, _, v = t.partition("=")
                    env_vars[k] = v
                else:
                    rest_tokens.append(t)
            if env_vars:
                result.add(EnvironmentEffect(command=segment, variables=env_vars))
            if rest_tokens:
                self._simulate_segment(shlex.join(rest_tokens), result)
            return

        # export / unset
        if cmd == "export":
            env_vars = {}
            for t in tokens[1:]:
                if "=" in t:
                    k, _, v = t.partition("=")
                    env_vars[k] = v
                else:
                    env_vars[t] = ""
            result.add(EnvironmentEffect(command=segment, variables=env_vars))
            return

        self._handle_leaf(cmd, tokens, segment, result)

    # -- Leaf handlers -------------------------------------------------------

    def _handle_leaf(self, cmd: str, tokens: list[str], raw: str, result: SimResult) -> None:
        # --- Filesystem delete ---
        if cmd in ("rm", "rmdir", "unlink"):
            flags = "".join(t.lstrip("-") for t in tokens[1:] if t.startswith("-") and t != "--")
            recursive = "r" in flags.lower()
            force = "f" in flags
            targets = [t for t in tokens[1:] if not t.startswith("-") or t == "--"]
            targets = [t for t in targets if t != "--"]
            result.add(FileDeleteEffect(command=raw, paths=targets, recursive=recursive, force=force))
            if recursive and force and any(t in _RM_DANGEROUS_TARGETS for t in targets):
                result.flag(f"rm -rf on dangerous target: {targets}")

        elif cmd in ("shred", "wipe"):
            targets = [t for t in tokens[1:] if not t.startswith("-")]
            result.add(FileDeleteEffect(command=raw, paths=targets, recursive=False, force=True))
            result.flag(f"{cmd} permanently destroys files: {targets}")

        elif cmd == "truncate":
            targets = [t for t in tokens[1:] if not t.startswith("-") and "=" not in t]
            for t in targets:
                result.add(FileWriteEffect(command=raw, path=t, truncate=True))

        # --- Filesystem write ---
        elif cmd in ("cp", "mv"):
            targets = [t for t in tokens[1:] if not t.startswith("-")]
            for t in targets:
                result.add(FileWriteEffect(command=raw, path=t))
            if cmd == "mv" and len(targets) >= 1:
                # source is deleted
                result.add(FileDeleteEffect(command=raw, paths=targets[:1]))

        elif cmd in ("touch", "mkdir", "ln", "mktemp") or cmd == "tee":
            targets = [t for t in tokens[1:] if not t.startswith("-")]
            for t in targets:
                result.add(FileWriteEffect(command=raw, path=t))

        # --- Filesystem read ---
        elif cmd in ("cat", "less", "more", "head", "tail", "bat", "view"):
            targets = [t for t in tokens[1:] if not t.startswith("-")]
            result.add(FileReadEffect(command=raw, paths=targets))

        elif cmd in ("ls", "find", "stat", "file", "wc", "diff", "grep", "rg", "awk", "sed"):
            # find gets special handling for -exec and -delete
            if cmd == "find":
                if "-delete" in tokens:
                    result.add(FileDeleteEffect(command=raw, paths=[], recursive=True, force=False))
                if "-exec" in tokens:
                    idx = tokens.index("-exec")
                    exec_tokens = []
                    for t in tokens[idx + 1 :]:
                        if t in (";", r"\;", "+"):
                            break
                        exec_tokens.append(t)
                    if exec_tokens:
                        self._simulate_segment(shlex.join(exec_tokens), result)
                if "-execdir" in tokens:
                    idx = tokens.index("-execdir")
                    exec_tokens = []
                    for t in tokens[idx + 1 :]:
                        if t in (";", r"\;", "+"):
                            break
                        exec_tokens.append(t)
                    if exec_tokens:
                        self._simulate_segment(shlex.join(exec_tokens), result)
            else:
                targets = [t for t in tokens[1:] if not t.startswith("-")]
                result.add(FileReadEffect(command=raw, paths=targets))

        # --- xargs ---
        elif cmd == "xargs":
            # xargs rm, xargs chmod, etc.
            rest = [t for t in tokens[1:] if not t.startswith("-")]
            if rest:
                self._simulate_segment(shlex.join(rest), result)

        # --- Permissions ---
        elif cmd in ("chmod", "chown", "chgrp"):
            flags = [t for t in tokens[1:] if t.startswith("-")]
            args = [t for t in tokens[1:] if not t.startswith("-")]
            recursive = any("R" in f for f in flags)
            mode = args[0] if args else ""
            paths = args[1:] if len(args) > 1 else []
            result.add(PermissionChangeEffect(command=raw, mode=mode, paths=paths, recursive=recursive))
            if cmd == "chmod" and recursive and mode == "777":
                result.flag("chmod -R 777")

        # --- System destructive ---
        elif cmd == "mkfs":
            device = next((t for t in tokens[1:] if not t.startswith("-")), "unknown")
            result.add(DeviceAccessEffect(command=raw, device=device))
            result.flag(f"mkfs would format: {device}")

        elif cmd == "dd":
            of = next((t.split("=", 1)[1] for t in tokens if t.startswith("of=")), None)
            if of:
                result.add(FileWriteEffect(command=raw, path=of))
                if of.startswith("/dev/"):
                    result.flag(f"dd writing to device: {of}")

        elif cmd in ("fdisk", "parted", "gdisk", "sgdisk"):
            device = next((t for t in tokens[1:] if not t.startswith("-")), "unknown")
            result.add(DeviceAccessEffect(command=raw, device=device))
            result.flag(f"{cmd} modifies partition table: {device}")

        # --- Network ---
        elif cmd in ("curl", "wget", "http", "httpie"):
            url = next((t for t in tokens[1:] if t.startswith("http")), "")
            result.add(NetworkEffect(command=raw, url=url, tool=cmd))

        elif cmd in ("ssh", "scp", "sftp", "rsync", "nc", "netcat", "ncat"):
            result.add(NetworkEffect(command=raw, tool=cmd))

        # --- Process control ---
        elif cmd in ("kill", "pkill", "killall"):
            signal = next((t for t in tokens[1:] if t.startswith("-")), "")
            targets = [t for t in tokens[1:] if not t.startswith("-")]
            result.add(ProcessControlEffect(command=raw, signal=signal, targets=targets))

        # --- Package managers ---
        elif cmd in _PACKAGE_MANAGERS:
            verbs = _PACKAGE_MANAGERS[cmd]
            operation = next((t for t in tokens[1:] if t in verbs), "")
            packages = [t for t in tokens[1:] if not t.startswith("-") and t != operation]
            result.add(PackageManagerEffect(command=raw, manager=cmd, operation=operation, packages=packages))

        # --- Git ---
        elif cmd == "git":
            subcommand = tokens[1] if len(tokens) > 1 else ""
            flags = [t for t in tokens[2:] if t.startswith("-")]
            is_destructive = False

            if subcommand == "reset" and "--hard" in flags:
                is_destructive = True
                result.flag("git reset --hard discards uncommitted changes")
            elif subcommand == "clean" and any(f in flags for f in ("-f", "--force")):
                is_destructive = True
                result.flag("git clean -f removes untracked files")
            elif subcommand == "push" and any(f in flags for f in ("--force", "-f", "--force-with-lease")):
                is_destructive = True
                result.flag("git push --force rewrites remote history")
            elif subcommand == "checkout" and any(t == "." or t == "--" for t in tokens[2:]):
                is_destructive = True
                result.flag("git checkout -- discards working tree changes")

            result.add(GitEffect(command=raw, subcommand=subcommand, flags=flags, is_destructive=is_destructive))

        # --- Unknown ---
        else:
            result.add(UnknownCommandEffect(command=raw))

    # -- Command substitution extraction -------------------------------------

    def _extract_substitutions(self, text: str) -> list[str]:
        """Extract all $(...), `...`, <(...), >(...) substitution contents."""
        results: list[str] = []
        i = 0
        while i < len(text):
            # $(...) and <(...) and >(...)
            if text[i] in ("$", "<", ">") and i + 1 < len(text) and text[i + 1] == "(":
                inner, consumed = self._extract_parens(text, i + 1)
                if inner is not None:
                    results.append(inner)
                    # Recurse for nested substitutions
                    results.extend(self._extract_substitutions(inner))
                    i += consumed
                    continue
            i += 1
        return results

    def _extract_parens(self, text: str, start: int) -> tuple[str | None, int]:
        """Extract content of balanced parens starting at `start` (the opening paren index).
        Returns (inner_content, chars_consumed) or (None, 0).
        """
        if start >= len(text) or text[start] != "(":
            return None, 0
        depth = 1
        j = start + 1
        while j < len(text) and depth > 0:
            if text[j] == "(":
                depth += 1
            elif text[j] == ")":
                depth -= 1
            j += 1
        if depth == 0:
            return text[start + 1 : j - 1], j - start
        return None, 0

    def _normalise_backticks(self, command: str) -> str:
        """Replace `cmd` with $(cmd) for uniform processing."""
        result = []
        i = 0
        while i < len(command):
            if command[i] == "`":
                j = i + 1
                while j < len(command) and command[j] != "`":
                    j += 1
                inner = command[i + 1 : j]
                result.append(f"$({inner})")
                i = j + 1
            else:
                result.append(command[i])
                i += 1
        return "".join(result)

    # -- Operator splitting --------------------------------------------------

    def _split_operators(self, command: str) -> list[str]:
        """Split on ;, &&, ||, |, &, newlines — respecting quotes and paren depth."""
        segments: list[str] = []
        current: list[str] = []
        i = 0
        depth = 0
        in_single = False
        in_double = False

        while i < len(command):
            c = command[i]

            if c == "'" and not in_double:
                in_single = not in_single
                current.append(c)
            elif c == '"' and not in_single:
                in_double = not in_double
                current.append(c)
            elif in_single or in_double:
                current.append(c)
            elif c in ("(", "{"):
                depth += 1
                current.append(c)
            elif c in (")", "}"):
                depth -= 1
                current.append(c)
            elif depth == 0:
                ahead = command[i : i + 2]
                if c == "\n" or c == ";":
                    segments.append("".join(current))
                    current = []
                elif ahead in ("&&", "||"):
                    segments.append("".join(current))
                    current = []
                    i += 1  # skip second char
                elif c == "|" and ahead != "||":
                    segments.append("".join(current))
                    current = []
                elif c == "&" and ahead != "&&":
                    # background — treat as segment boundary
                    segments.append("".join(current))
                    current = []
                else:
                    current.append(c)
            else:
                current.append(c)

            i += 1

        if current:
            segments.append("".join(current))

        return [s.strip() for s in segments if s.strip()]


def is_heredoc_write_pattern(command: str) -> bool:
    """Return True if the bash command uses heredoc or redirect to write a file.

    Detects patterns like:
    - cat << EOF > file.txt
    - echo "content" > file
    """
    if not command:
        return False

    heredoc_pattern = (
        r"^\s*(?:\/usr\/bin\/env\s+)?(?:\/bin\/)?(?:sh\s+-c\s+[\"\']?)?"
        r"(?:cat|printf)\s+<<\s*[\'\"]?\w+[\'\"]?\s*>\s*\S+"
    )
    echo_redirect_pattern = r'^\s*(?:echo|printf)\s+[\'"].*\n.*[>][>]*[^>]+\S+'
    multiline_redirect = r'<<\s*[\'"]?\w+[\'"]?.*>'

    return bool(
        re.search(heredoc_pattern, command, re.MULTILINE | re.IGNORECASE)
        or re.search(echo_redirect_pattern, command, re.MULTILINE)
        or re.search(multiline_redirect, command, re.MULTILINE)
        or (
            command.count("\n") > 1
            and re.search(r"[>][>]*\s*\S+", command)
            and re.search(r"(?:echo|cat|printf)", command, re.IGNORECASE)
        )
    )
