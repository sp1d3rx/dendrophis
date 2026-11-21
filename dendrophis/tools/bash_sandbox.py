"""In-memory bash command simulator for pre-execution safety analysis.

Parses command strings into typed Effect objects describing what the command
would do, then flags dangerous effects. Handles chaining, subshells, backtick
substitution, process substitution, redirections, and common wrapper commands.
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
        return {effect.category for effect in self.effects if hasattr(effect, "category")}

    def is_denied(self, denied: set[CommandCategory]) -> bool:
        """True if any effect's category is in the denied set."""
        return bool(self.categories & denied) or self.dangerous

    def is_allowed(self, allowed: set[CommandCategory]) -> bool:
        """True if all effect categories are within the allowed set."""
        return bool(self.categories) and self.categories.issubset(allowed) and not self.dangerous


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FORK_BOMB = re.compile(r":\(\)\{")
_DEVICE_REDIRECT = re.compile(r">\s*/dev/(sda\w*|mem|kmem|hd\w+|nvme\w+)", re.IGNORECASE)
_RM_DANGEROUS_TARGETS = {"/", "/*", "~", "~/", "$HOME", "${HOME}", "$HOME/", "${HOME}/"}
_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# Passthrough wrappers and the flags on each wrapper that consume the following argument.
_DELEGATION_CMDS: dict[str, set[str]] = {
    "sudo": {"-u", "-g", "-C", "-D", "-R", "-T", "-p", "-h"},
    "doas": {"-u", "-C"},
    "nice": {"-n"},
    "nohup": set(),
    "timeout": {"-k", "-s"},
    "time": {"-f", "-o"},
    "strace": {"-e", "-o", "-p", "-s", "-u"},
    "ltrace": {"-e", "-o", "-p", "-s", "-u"},
    "watch": {"-n"},
}

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

# Git global flags that take a separate value token before the subcommand
_GIT_GLOBAL_VALUE_FLAGS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}


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

        device_match = _DEVICE_REDIRECT.search(command)
        if device_match:
            device_target = device_match.group(0).strip()
            result.add(DeviceAccessEffect(command=command, device=device_target))
            result.flag(f"direct device write: {device_target}")
            return

        command = self._normalise_backticks(command)

        for segment in self._split_operators(command):
            self._simulate_segment(segment, result)

    def _simulate_segment(self, segment: str, result: SimResult) -> None:
        segment = segment.strip()
        if not segment:
            return

        # Unwrap plain subshells (...) and brace groups { ...; }
        if (segment.startswith("(") and segment.endswith(")")) or (segment.startswith("{") and segment.endswith("}")):
            inner_body = segment[1:-1].strip()
            if inner_body:
                self._simulate_str(inner_body, result)
            return

        # Extract and recurse into all substitution forms before tokenising
        for inner_sub in self._extract_substitutions(segment):
            inner_result = SimResult()
            self._simulate_str(inner_sub, inner_result)
            shell_effect = ShellExecutionEffect(command=inner_sub, inner_effects=inner_result.effects)
            result.add(shell_effect)
            if inner_result.dangerous:
                result.flag(f"dangerous substitution: {inner_result.reason}")

        # Detect output redirections (>, >>, &>, 2>) outside quotes before shlex splitting
        for redirect_target, is_truncate in self._extract_output_redirections(segment):
            if redirect_target != "/dev/null" and not redirect_target.startswith("&"):
                result.add(FileWriteEffect(command=segment, path=redirect_target, truncate=is_truncate))

        try:
            tokens = shlex.split(segment)
        except ValueError:
            result.flag(f"unparseable command: {segment[:80]}")
            return

        if not tokens:
            return

        # Strip leading VAR=val inline environment assignments
        leading_env: dict[str, str] = {}
        token_index = 0
        while token_index < len(tokens) and _ENV_ASSIGNMENT.match(tokens[token_index]):
            var_name, _, var_value = tokens[token_index].partition("=")
            leading_env[var_name] = var_value
            token_index += 1

        if leading_env:
            result.add(EnvironmentEffect(command=segment, variables=leading_env))
            tokens = tokens[token_index:]
            if not tokens:
                return

        cmd = Path(tokens[0]).name

        # Unwrap delegation wrappers (sudo, timeout, nice, etc.)
        if cmd in _DELEGATION_CMDS:
            rest_tokens = self._unwrap_delegation(cmd, tokens[1:])
            if rest_tokens:
                self._simulate_segment(shlex.join(rest_tokens), result)
            return

        # bash/sh/zsh -c "..."
        if cmd in ("bash", "sh", "zsh", "fish", "dash", "ksh") and "-c" in tokens:
            flag_index = tokens.index("-c")
            if flag_index + 1 < len(tokens):
                inner_result = SimResult()
                self._simulate_str(tokens[flag_index + 1], inner_result)
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
            return

        # env VAR=val cmd args
        if cmd == "env":
            env_vars: dict[str, str] = {}
            rest_tokens = []
            found_command = False
            for token_item in tokens[1:]:
                if not found_command and "=" in token_item and not token_item.startswith("-"):
                    env_key, _, env_val = token_item.partition("=")
                    env_vars[env_key] = env_val
                elif not found_command and token_item.startswith("-"):
                    continue
                else:
                    found_command = True
                    rest_tokens.append(token_item)
            if env_vars:
                result.add(EnvironmentEffect(command=segment, variables=env_vars))
            if rest_tokens:
                self._simulate_segment(shlex.join(rest_tokens), result)
            return

        # export
        if cmd == "export":
            export_vars: dict[str, str] = {}
            for token_item in tokens[1:]:
                if "=" in token_item:
                    env_key, _, env_val = token_item.partition("=")
                    export_vars[env_key] = env_val
                else:
                    export_vars[token_item] = ""
            result.add(EnvironmentEffect(command=segment, variables=export_vars))
            return

        self._handle_leaf(cmd, tokens, segment, result)

    @staticmethod
    def _unwrap_delegation(wrapper_cmd: str, args: list[str]) -> list[str]:
        """Strip wrapper flags (and positional duration for `timeout`) to find the delegated command."""
        value_flags = _DELEGATION_CMDS.get(wrapper_cmd, set())
        cursor = 0
        while cursor < len(args):
            arg_token = args[cursor]
            if arg_token == "--":
                cursor += 1
                break
            if arg_token in value_flags:
                cursor += 2
                continue
            if arg_token.startswith("-"):
                cursor += 1
                continue
            break
        # `timeout [FLAGS] DURATION COMMAND ...` has one positional duration argument before COMMAND
        if wrapper_cmd == "timeout" and cursor < len(args):
            cursor += 1
        return args[cursor:]

    # -- Leaf handlers -------------------------------------------------------

    def _handle_leaf(self, cmd: str, tokens: list[str], raw: str, result: SimResult) -> None:
        if cmd in ("rm", "rmdir", "unlink"):
            combined_flags = "".join(
                token_item.lstrip("-") for token_item in tokens[1:] if token_item.startswith("-") and token_item != "--"
            )
            recursive = "r" in combined_flags.lower()
            force = "f" in combined_flags
            targets = [token_item for token_item in tokens[1:] if not token_item.startswith("-") and token_item != "--"]
            result.add(FileDeleteEffect(command=raw, paths=targets, recursive=recursive, force=force))
            if recursive and force and any(target_path in _RM_DANGEROUS_TARGETS for target_path in targets):
                result.flag(f"rm -rf on dangerous target: {targets}")

        elif cmd in ("shred", "wipe"):
            targets = [token_item for token_item in tokens[1:] if not token_item.startswith("-")]
            result.add(FileDeleteEffect(command=raw, paths=targets, recursive=False, force=True))
            result.flag(f"{cmd} permanently destroys files: {targets}")

        elif cmd == "truncate":
            targets = [
                token_item for token_item in tokens[1:] if not token_item.startswith("-") and "=" not in token_item
            ]
            for target_path in targets:
                result.add(FileWriteEffect(command=raw, path=target_path, truncate=True))

        elif cmd in ("cp", "mv"):
            targets = [token_item for token_item in tokens[1:] if not token_item.startswith("-")]
            for target_path in targets:
                result.add(FileWriteEffect(command=raw, path=target_path))
            if cmd == "mv" and len(targets) >= 1:
                result.add(FileDeleteEffect(command=raw, paths=targets[:1]))

        elif cmd in ("touch", "mkdir", "ln", "mktemp", "tee"):
            targets = [token_item for token_item in tokens[1:] if not token_item.startswith("-")]
            for target_path in targets:
                result.add(FileWriteEffect(command=raw, path=target_path))

        elif cmd in ("cat", "less", "more", "head", "tail", "bat", "view"):
            targets = [token_item for token_item in tokens[1:] if not token_item.startswith("-")]
            result.add(FileReadEffect(command=raw, paths=targets))

        elif cmd in ("ls", "find", "stat", "file", "wc", "diff", "grep", "rg", "awk", "sed"):
            if cmd == "find":
                if "-delete" in tokens:
                    result.add(FileDeleteEffect(command=raw, paths=[], recursive=True, force=False))
                for exec_flag in ("-exec", "-execdir"):
                    if exec_flag in tokens:
                        exec_index = tokens.index(exec_flag)
                        exec_tokens = []
                        for token_item in tokens[exec_index + 1 :]:
                            if token_item in (";", r"\;", "+"):
                                break
                            exec_tokens.append(token_item)
                        if exec_tokens:
                            self._simulate_segment(shlex.join(exec_tokens), result)
                if "-delete" not in tokens and "-exec" not in tokens and "-execdir" not in tokens:
                    targets = [token_item for token_item in tokens[1:] if not token_item.startswith("-")]
                    result.add(FileReadEffect(command=raw, paths=targets))
            elif (
                cmd == "sed"
                and any(
                    token_item == "-i" or token_item.startswith("-i") or token_item.startswith("--in-place")
                    for token_item in tokens[1:]
                )
            ) or (
                cmd == "awk"
                and any(token_item in ("-i", "--include") and "inplace" in tokens for token_item in tokens[1:])
            ):
                targets = [token_item for token_item in tokens[1:] if not token_item.startswith("-")]
                target_file = targets[-1] if targets else ""
                result.add(FileWriteEffect(command=raw, path=target_file))
            else:
                targets = [token_item for token_item in tokens[1:] if not token_item.startswith("-")]
                result.add(FileReadEffect(command=raw, paths=targets))

        elif cmd == "xargs":
            rest_tokens = [token_item for token_item in tokens[1:] if not token_item.startswith("-")]
            if rest_tokens:
                self._simulate_segment(shlex.join(rest_tokens), result)

        elif cmd in ("chmod", "chown", "chgrp"):
            flag_tokens = [token_item for token_item in tokens[1:] if token_item.startswith("-")]
            arg_tokens = [token_item for token_item in tokens[1:] if not token_item.startswith("-")]
            recursive = any("R" in flag_item for flag_item in flag_tokens)
            mode = arg_tokens[0] if arg_tokens else ""
            paths = arg_tokens[1:] if len(arg_tokens) > 1 else []
            result.add(PermissionChangeEffect(command=raw, mode=mode, paths=paths, recursive=recursive))
            if cmd == "chmod" and recursive and mode == "777":
                result.flag("chmod -R 777")

        elif cmd == "mkfs":
            device = next((token_item for token_item in tokens[1:] if not token_item.startswith("-")), "unknown")
            result.add(DeviceAccessEffect(command=raw, device=device))
            result.flag(f"mkfs would format: {device}")

        elif cmd == "dd":
            output_file = next(
                (token_item.split("=", 1)[1] for token_item in tokens if token_item.startswith("of=")),
                None,
            )
            if output_file:
                result.add(FileWriteEffect(command=raw, path=output_file))
                if output_file.startswith("/dev/"):
                    result.flag(f"dd writing to device: {output_file}")

        elif cmd in ("fdisk", "parted", "gdisk", "sgdisk"):
            device = next((token_item for token_item in tokens[1:] if not token_item.startswith("-")), "unknown")
            result.add(DeviceAccessEffect(command=raw, device=device))
            result.flag(f"{cmd} modifies partition table: {device}")

        elif cmd in ("curl", "wget", "http", "httpie"):
            url = next((token_item for token_item in tokens[1:] if token_item.startswith("http")), "")
            result.add(NetworkEffect(command=raw, url=url, tool=cmd))

        elif cmd in ("ssh", "scp", "sftp", "rsync", "nc", "netcat", "ncat"):
            result.add(NetworkEffect(command=raw, tool=cmd))

        elif cmd in ("kill", "pkill", "killall"):
            signal = next((token_item for token_item in tokens[1:] if token_item.startswith("-")), "")
            targets = [token_item for token_item in tokens[1:] if not token_item.startswith("-")]
            result.add(ProcessControlEffect(command=raw, signal=signal, targets=targets))

        elif cmd in _PACKAGE_MANAGERS:
            verbs = _PACKAGE_MANAGERS[cmd]
            operation = next((token_item for token_item in tokens[1:] if token_item in verbs), "")
            packages = [
                token_item for token_item in tokens[1:] if not token_item.startswith("-") and token_item != operation
            ]
            result.add(PackageManagerEffect(command=raw, manager=cmd, operation=operation, packages=packages))

        elif cmd == "git":
            subcommand, sub_tokens = self._parse_git_subcommand(tokens[1:])
            flag_tokens = [token_item for token_item in sub_tokens if token_item.startswith("-")]
            short_flags = "".join(
                token_item[1:]
                for token_item in flag_tokens
                if token_item.startswith("-") and not token_item.startswith("--")
            )
            is_destructive = False

            if subcommand == "reset" and "--hard" in flag_tokens:
                is_destructive = True
                result.flag("git reset --hard discards uncommitted changes")
            elif subcommand == "clean" and ("f" in short_flags or "--force" in flag_tokens):
                is_destructive = True
                result.flag("git clean -f removes untracked files")
            elif subcommand == "push" and (
                "f" in short_flags or any(flag_item in flag_tokens for flag_item in ("--force", "--force-with-lease"))
            ):
                is_destructive = True
                result.flag("git push --force rewrites remote history")
            elif subcommand == "checkout" and any(token_item in (".", "--") for token_item in sub_tokens):
                is_destructive = True
                result.flag("git checkout -- discards working tree changes")

            result.add(GitEffect(command=raw, subcommand=subcommand, flags=flag_tokens, is_destructive=is_destructive))

        else:
            result.add(UnknownCommandEffect(command=raw))

    @staticmethod
    def _parse_git_subcommand(git_args: list[str]) -> tuple[str, list[str]]:
        """Skip global git flags like `-C dir` or `-c k=v` to locate the subcommand."""
        cursor = 0
        while cursor < len(git_args):
            token_item = git_args[cursor]
            if token_item in _GIT_GLOBAL_VALUE_FLAGS:
                cursor += 2
                continue
            if token_item.startswith("-"):
                cursor += 1
                continue
            return token_item, git_args[cursor + 1 :]
        return "", []

    # -- Redirection & Substitution extraction -------------------------------

    @staticmethod
    def _extract_output_redirections(segment: str) -> list[tuple[str, bool]]:
        """Return (target_path, truncate) pairs for unquoted >, >>, &>, 2> redirections."""
        redirections: list[tuple[str, bool]] = []
        cursor = 0
        in_single = False
        in_double = False
        text_len = len(segment)

        while cursor < text_len:
            char = segment[cursor]
            if char == "\\" and not in_single and cursor + 1 < text_len:
                cursor += 2
                continue
            if char == "'" and not in_double:
                in_single = not in_single
                cursor += 1
                continue
            if char == '"' and not in_single:
                in_double = not in_double
                cursor += 1
                continue
            if not in_single and not in_double and char == ">":
                # Ignore process substitution >(...)
                if cursor + 1 < text_len and segment[cursor + 1] == "(":
                    cursor += 2
                    continue
                is_append = cursor + 1 < text_len and segment[cursor + 1] == ">"
                target_start = cursor + (2 if is_append else 1)
                while target_start < text_len and segment[target_start] in (" ", "\t"):
                    target_start += 1
                target_end = target_start
                while target_end < text_len and segment[target_end] not in (" ", "\t", ";", "|", "&", "\n", ")"):
                    target_end += 1
                target_token = segment[target_start:target_end].strip("'\"")
                if target_token:
                    redirections.append((target_token, not is_append))
                cursor = target_end
                continue
            cursor += 1
        return redirections

    def _extract_substitutions(self, text: str) -> list[str]:
        """Extract all $(...), <(...), >(...) substitution contents outside single quotes."""
        results: list[str] = []
        cursor = 0
        in_single = False
        in_double = False
        while cursor < len(text):
            char = text[cursor]
            if char == "\\" and not in_single and cursor + 1 < len(text):
                cursor += 2
                continue
            if char == "'" and not in_double:
                in_single = not in_single
                cursor += 1
                continue
            if char == '"' and not in_single:
                in_double = not in_double
                cursor += 1
                continue
            if not in_single and char in ("$", "<", ">") and cursor + 1 < len(text) and text[cursor + 1] == "(":
                inner, consumed = self._extract_parens(text, cursor + 1)
                if inner is not None:
                    results.append(inner)
                    results.extend(self._extract_substitutions(inner))
                    cursor += consumed
                    continue
            cursor += 1
        return results

    def _extract_parens(self, text: str, start: int) -> tuple[str | None, int]:
        """Extract content of balanced parens starting at `start` (the opening paren index)."""
        if start >= len(text) or text[start] != "(":
            return None, 0
        depth = 1
        scan_index = start + 1
        while scan_index < len(text) and depth > 0:
            if text[scan_index] == "\\" and scan_index + 1 < len(text):
                scan_index += 2
                continue
            if text[scan_index] == "(":
                depth += 1
            elif text[scan_index] == ")":
                depth -= 1
            scan_index += 1
        if depth == 0:
            return text[start + 1 : scan_index - 1], scan_index - start
        return None, 0

    def _normalise_backticks(self, command: str) -> str:
        """Replace unquoted/double-quoted `cmd` with $(cmd), leaving single-quoted literals untouched."""
        result: list[str] = []
        cursor = 0
        in_single = False
        in_double = False
        while cursor < len(command):
            char = command[cursor]
            if char == "\\" and not in_single and cursor + 1 < len(command):
                result.append(command[cursor : cursor + 2])
                cursor += 2
                continue
            if char == "'" and not in_double:
                in_single = not in_single
                result.append(char)
                cursor += 1
                continue
            if char == '"' and not in_single:
                in_double = not in_double
                result.append(char)
                cursor += 1
                continue
            if char == "`" and not in_single:
                end_index = cursor + 1
                while end_index < len(command) and command[end_index] != "`":
                    if command[end_index] == "\\" and end_index + 1 < len(command):
                        end_index += 2
                        continue
                    end_index += 1
                if end_index < len(command):
                    inner = command[cursor + 1 : end_index]
                    result.append(f"$({inner})")
                    cursor = end_index + 1
                    continue
            result.append(char)
            cursor += 1
        return "".join(result)

    # -- Operator splitting --------------------------------------------------

    def _split_operators(self, command: str) -> list[str]:
        """Split on ;, &&, ||, |, &, newlines — respecting quotes, escapes, and paren depth."""
        segments: list[str] = []
        current: list[str] = []
        cursor = 0
        depth = 0
        in_single = False
        in_double = False

        while cursor < len(command):
            char = command[cursor]

            if char == "\\" and not in_single and cursor + 1 < len(command):
                current.append(command[cursor : cursor + 2])
                cursor += 2
                continue
            if char == "'" and not in_double:
                in_single = not in_single
                current.append(char)
            elif char == '"' and not in_single:
                in_double = not in_double
                current.append(char)
            elif in_single or in_double:
                current.append(char)
            elif char in ("(", "{"):
                depth += 1
                current.append(char)
            elif char in (")", "}"):
                depth = max(0, depth - 1)
                current.append(char)
            elif depth == 0:
                ahead = command[cursor : cursor + 2]
                prev_char = command[cursor - 1] if cursor > 0 else ""
                if char in ("\n", ";"):
                    segments.append("".join(current))
                    current = []
                elif ahead in ("&&", "||"):
                    segments.append("".join(current))
                    current = []
                    cursor += 1
                elif (char == "|" and ahead != "||") or (
                    char == "&" and ahead not in ("&&", "&>") and prev_char != ">"
                ):
                    segments.append("".join(current))
                    current = []
                else:
                    current.append(char)
            else:
                current.append(char)

            cursor += 1

        if current:
            segments.append("".join(current))

        return [segment_item.strip() for segment_item in segments if segment_item.strip()]


_HEREDOC_REDIRECT = re.compile(r"<<\s*['\"]?\w+['\"]?.*>", re.MULTILINE)
_ECHO_LINE_REDIRECT = re.compile(r"^\s*(?:echo|printf)\s+[\'\"].*\n.*[>][>]*[^>]+\S+", re.MULTILINE)


def is_heredoc_write_pattern(command: str) -> bool:
    """Return True if the bash command uses heredoc or redirect to write a file."""
    if not command:
        return False

    if _HEREDOC_REDIRECT.search(command):
        return True
    if _ECHO_LINE_REDIRECT.search(command):
        return True
    return bool(
        command.count("\n") > 1
        and re.search(r"[>][>]*\s*\S+", command)
        and re.search(r"(?:echo|cat|printf)", command, re.IGNORECASE)
    )
