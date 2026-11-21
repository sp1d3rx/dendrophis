"""CLI entry point."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import lzma
import os
import sys
from pathlib import Path
from typing import Any

from dendrophis import __version__
from dendrophis.utils import ensure_secure_dir, ensure_secure_file

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dendrophis",
        description="Python-native terminal coding agent",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="Path to config.yaml (overrides default search)",
    )
    parser.add_argument(
        "--model",
        metavar="MODEL",
        help="Model to use (overrides config)",
    )
    parser.add_argument(
        "--session",
        metavar="ID_OR_PATH",
        help="Session ID (short or full), fork name, or path to session file",
    )
    parser.add_argument(
        "--fork",
        metavar="ID_OR_NAME",
        help="Fork context from a saved session or fork name into a new session branch",
    )
    parser.add_argument(
        "--list-sessions",
        action="store_true",
        help="List available saved sessions and exit",
    )
    parser.add_argument(
        "--list-forks",
        action="store_true",
        help="List available saved forks and exit",
    )
    parser.add_argument(
        "--calibrate",
        metavar="MODEL",
        help="Calibrate a model: detect capabilities and test parameter support",
    )
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="List available models from the configured provider",
    )
    parser.add_argument(
        "--model-info",
        metavar="MODEL",
        help="Show detailed capability info for a model (requires prior calibration)",
    )
    parser.add_argument(
        "--model-config",
        metavar="MODEL",
        help="Show recommended config for a model (requires prior calibration)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-calibration (use with --calibrate)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode and detailed tool execution logging",
    )
    parser.add_argument(
        "--log",
        action="store_true",
        help="Enable detailed tool execution logging",
    )
    parser.add_argument(
        "--no-parallel-tools",
        action="store_true",
        help="Execute tools sequentially instead of in parallel",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="Enable web observability interface",
    )
    parser.add_argument(
        "--web-port",
        type=int,
        default=9320,
        metavar="PORT",
        help="Port for web observability interface (default: 9320)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def _read_session_archive(session_file: Path) -> dict[str, Any]:
    """Read and parse a plain or LZMA-compressed session archive."""
    if session_file.suffix == ".xz":
        with lzma.open(session_file, "rb") as file_handle:
            return json.loads(file_handle.read().decode("utf-8"))
    return json.loads(session_file.read_text(encoding="utf-8"))


def _sorted_session_files(sessions_dir: Path) -> list[Path]:
    """Return saved session files ordered newest-first."""
    return sorted(
        sessions_dir.glob("session-*.json*"),
        key=lambda session_file: session_file.stat().st_mtime,
        reverse=True,
    )


def _list_sessions() -> None:
    """List available saved sessions."""
    sessions_dir = Path.home() / ".config" / "dendrophis" / "sessions"
    if not sessions_dir.exists():
        print("No saved sessions found.")
        return

    session_files = _sorted_session_files(sessions_dir)
    if not session_files:
        print("No saved sessions found.")
        return

    print(f"{'Session ID':12} {'Fork Name':18} {'Date':20} {'Model':32} {'Messages':>8}")
    print("-" * 95)
    for session_file in session_files[:20]:
        try:
            archive_payload = _read_session_archive(session_file)
            session_id = archive_payload.get("session_id", "unknown")[:8]
            fork_name = (archive_payload.get("fork_name") or "")[:16]
            timestamp = archive_payload.get("timestamp", "unknown")[:19].replace("T", " ")
            model = archive_payload.get("model", "unknown")[:30]
            message_count = sum(1 for message in archive_payload.get("messages", []) if message.get("role") != "system")
            print(f"{session_id:12} {fork_name:18} {timestamp:20} {model:32} {message_count:>8}")
        except Exception as read_error:
            print(f"Warning: skipping unreadable session file {session_file.name}: {read_error}", file=sys.stderr)
            continue


def _list_forks() -> None:
    """List available saved forks."""
    sessions_dir = Path.home() / ".config" / "dendrophis" / "sessions"
    if not sessions_dir.exists():
        print("No saved forks found.")
        return

    session_files = _sorted_session_files(sessions_dir)
    if not session_files:
        print("No saved forks found.")
        return

    forks_found = False
    print(f"{'Fork Name':20} {'Session ID':12} {'Date':20} {'Model':30} {'Messages':>8}")
    print("-" * 95)
    for session_file in session_files:
        try:
            archive_payload = _read_session_archive(session_file)
            fork_name = archive_payload.get("fork_name")
            if not fork_name:
                continue

            forks_found = True
            session_id = archive_payload.get("session_id", "unknown")[:8]
            timestamp = archive_payload.get("timestamp", "unknown")[:19].replace("T", " ")
            model = archive_payload.get("model", "unknown")[:28]
            message_count = sum(1 for message in archive_payload.get("messages", []) if message.get("role") != "system")
            print(f"{fork_name[:20]:20} {session_id:12} {timestamp:20} {model:30} {message_count:>8}")
        except Exception as read_error:
            print(f"Warning: skipping unreadable session file {session_file.name}: {read_error}", file=sys.stderr)
            continue

    if not forks_found:
        print("No named forks found.")


def _resolve_session(id_or_path: str) -> str:
    """Resolve a session ID, fork name, or file path to an absolute path."""
    file_path = Path(id_or_path).expanduser()
    if file_path.exists():
        return str(file_path)

    sessions_dir = Path.home() / ".config" / "dendrophis" / "sessions"
    if not sessions_dir.exists():
        print(f"No session found for: {id_or_path}", file=sys.stderr)
        sys.exit(1)

    matches = sorted(
        sessions_dir.glob(f"session-{id_or_path}*.json*"),
        key=lambda session_file: session_file.stat().st_mtime,
        reverse=True,
    )
    if matches:
        return str(matches[0])

    for session_file in _sorted_session_files(sessions_dir):
        try:
            archive_payload = _read_session_archive(session_file)
            if archive_payload.get("fork_name") == id_or_path:
                return str(session_file)
            if archive_payload.get("session_id", "").startswith(id_or_path):
                return str(session_file)
        except Exception as read_error:
            print(f"Warning: skipping unreadable session file {session_file.name}: {read_error}", file=sys.stderr)
            continue

    print(f"No session found for: {id_or_path}", file=sys.stderr)
    sys.exit(1)


def _cmd_calibrate(model_id: str, config_path: str | None = None, force: bool = False) -> None:
    """Calibrate a model and display results."""
    if config_path:
        os.environ["DENDROPHIS_CONFIG"] = config_path

    from dendrophis.llm.calibration import calibrate_model

    async def run() -> None:
        try:
            capabilities = await calibrate_model(
                model_id=model_id,
                force=force,
            )
            print(capabilities)

            recommended = capabilities.get_recommended_config()
            if recommended:
                print("\nRecommended overrides:")
                for key, value in recommended.items():
                    if not key.startswith("_"):
                        print(f"  {key}: {value}")
        except Exception as error:
            logger.error("Error calibrating model: %s", error, exc_info=True)
            print(f"Error: {error}", file=sys.stderr)
            sys.exit(1)

    asyncio.run(run())


def _cmd_list_models(config_path: str | None = None) -> None:
    """List available models from the provider."""
    if config_path:
        os.environ["DENDROPHIS_CONFIG"] = config_path

    from dendrophis.llm.calibration import (
        ModelOverrideStore,
        format_model_list,
        list_available_models,
    )

    store = ModelOverrideStore()

    async def run() -> None:
        try:
            models = await list_available_models()
            print(format_model_list(models, store))
        except Exception as error:
            logger.error("Error listing models: %s", error, exc_info=True)
            print(f"Error: {error}", file=sys.stderr)
            sys.exit(1)

    asyncio.run(run())


def _cmd_model_info(model_id: str, config_path: str | None = None) -> None:
    """Show detailed info for a calibrated model."""
    if config_path:
        os.environ["DENDROPHIS_CONFIG"] = config_path

    from dendrophis.llm.calibration import ModelOverrideStore, format_model_info

    store = ModelOverrideStore()
    print(format_model_info(model_id, store))


def _cmd_model_config(model_id: str, config_path: str | None = None) -> None:
    """Show recommended config for a model."""
    if config_path:
        os.environ["DENDROPHIS_CONFIG"] = config_path

    from dendrophis.llm.calibration import ModelOverrideStore, format_recommended_config

    store = ModelOverrideStore()
    print(format_recommended_config(model_id, store))


def _setup_exception_log() -> None:
    log_path = Path.home() / ".config" / "dendrophis" / "exceptions.log"
    ensure_secure_dir(log_path.parent)
    handler = logging.FileHandler(log_path)
    handler.setLevel(logging.WARNING)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s\n%(message)s\n"))
    logging.root.addHandler(handler)
    logging.root.setLevel(logging.WARNING)
    ensure_secure_file(log_path)


def main() -> None:
    _setup_exception_log()
    parser = build_parser()
    args = parser.parse_args()

    if args.config:
        os.environ["DENDROPHIS_CONFIG"] = str(args.config)

    if args.calibrate:
        _cmd_calibrate(args.calibrate, config_path=args.config, force=args.force)
        sys.exit(0)

    if args.list_models:
        _cmd_list_models(config_path=args.config)
        sys.exit(0)

    if args.model_info:
        _cmd_model_info(args.model_info, config_path=args.config)
        sys.exit(0)

    if args.model_config:
        _cmd_model_config(args.model_config, config_path=args.config)
        sys.exit(0)

    if args.list_sessions:
        _list_sessions()
        sys.exit(0)

    if args.list_forks:
        _list_forks()
        sys.exit(0)

    from dendrophis.config.loader import ConfigLoader
    from dendrophis.ui.app import DendrophisApp

    try:
        loader = ConfigLoader.load(config_path=args.config)
    except FileNotFoundError as config_error:
        logger.error("Error loading config: %s", config_error, exc_info=True)
        print(f"Error: {config_error}", file=sys.stderr)
        sys.exit(1)
    if args.model:
        loader.config.llm.model = args.model

    if args.debug or args.log:
        os.environ["DENDROPHIS_TOOL_LOG"] = "1"
        if args.debug:
            logging.root.setLevel(logging.DEBUG)
        print("🔍 Tool execution logging enabled - check tool_log_<session_id>.txt")

    if args.no_parallel_tools:
        loader.config.tools.parallel_tools = False
        print("🔧 Parallel tool execution disabled")

    session_path = None
    as_fork = False
    if args.session:
        session_path = _resolve_session(args.session)
    elif args.fork:
        session_path = _resolve_session(args.fork)
        as_fork = True

    app = DendrophisApp(
        config_loader=loader,
        session_path=session_path,
        as_fork=as_fork,
        system_prompt_source=loader.system_prompt_source,
        enable_web=args.web,
        web_port=args.web_port,
    )
    app.run()

    saved = app._session.save_session()
    if saved:
        print(f"Session: {app._session.session_id[:8]}")

    sys.exit(0)
