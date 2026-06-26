"""CLI entry point."""

from __future__ import annotations

import argparse
import sys


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
        help="Session ID (short or full) or path to session file",
    )
    parser.add_argument(
        "--list-sessions",
        action="store_true",
        help="List available saved sessions and exit",
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
        "--log",
        action="store_true",
        help="Enable detailed tool execution logging to tool_log.txt",
    )
    parser.add_argument(
        "--no-parallel-tools",
        action="store_true",
        help="Execute tools sequentially instead of in parallel",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 0.6.1",
    )
    return parser


def _list_sessions() -> None:
    """List available saved sessions."""
    from pathlib import Path

    sessions_dir = Path.home() / ".config" / "dendrophis" / "sessions"
    if not sessions_dir.exists():
        print("No saved sessions found.")
        return

    session_files = sorted(sessions_dir.glob("session-*.json*"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not session_files:
        print("No saved sessions found.")
        return

    print(f"{'Session ID':12} {'Date':20} {'Model':40} {'Messages':>8}")
    print("-" * 90)
    for path in session_files[:20]:  # Show last 20
        import json

        try:
            if path.suffix == ".xz":
                import lzma

                with lzma.open(path, "rb") as f:
                    data = json.loads(f.read().decode())
            else:
                data = json.loads(path.read_text())
            session_id = data.get("session_id", "unknown")[:8]
            timestamp = data.get("timestamp", "unknown")[:19].replace("T", " ")
            model = data.get("model", "unknown")[:38]
            msg_count = len([m for m in data.get("messages", []) if m.get("role") != "system"])
            print(f"{session_id:12} {timestamp:20} {model:40} {msg_count:>8}")
        except Exception:
            continue


def _resolve_session(id_or_path: str) -> str:
    """Resolve a session ID (short or full) or path to an absolute path."""
    from pathlib import Path

    p = Path(id_or_path).expanduser()
    if p.exists():
        return str(p)

    sessions_dir = Path.home() / ".config" / "dendrophis" / "sessions"
    matches = sorted(sessions_dir.glob(f"session-{id_or_path}*.json*"), key=lambda f: f.stat().st_mtime, reverse=True)
    if matches:
        return str(matches[0])

    print(f"No session found for: {id_or_path}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Model Calibration Functions
# ---------------------------------------------------------------------------


def _cmd_calibrate(model_id: str, config_path: str | None = None, force: bool = False) -> None:
    """Calibrate a model and display results."""
    import asyncio
    import os

    # Set config path in environment BEFORE any imports
    if config_path:
        os.environ["DENDROPHIS_CONFIG"] = config_path

    from dendrophis.llm.calibration import (
        calibrate_model,
    )

    async def run():
        try:
            capabilities = await calibrate_model(
                model_id=model_id,
                force=force,
            )
            print(capabilities)

            # Show recommended config
            config = capabilities.get_recommended_config()
            if config:
                print("\nRecommended overrides:")
                for k, v in config.items():
                    if not k.startswith("_"):
                        print(f"  {k}: {v}")
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    asyncio.run(run())


def _cmd_list_models(config_path: str | None = None) -> None:
    """List available models from the provider."""
    import asyncio
    import os

    # Set config path in environment BEFORE any imports
    if config_path:
        os.environ["DENDROPHIS_CONFIG"] = config_path

    from dendrophis.llm.calibration import (
        ModelOverrideStore,
        format_model_list,
        list_available_models,
    )

    store = ModelOverrideStore()

    async def run():
        try:
            models = await list_available_models()
            print(format_model_list(models, store))
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    asyncio.run(run())


def _cmd_model_info(model_id: str, config_path: str | None = None) -> None:
    """Show detailed info for a calibrated model."""
    import os

    # Set config path in environment BEFORE any imports
    if config_path:
        os.environ["DENDROPHIS_CONFIG"] = config_path

    from dendrophis.llm.calibration import (
        ModelOverrideStore,
        format_model_info,
    )

    store = ModelOverrideStore()
    print(format_model_info(model_id, store))


def _cmd_model_config(model_id: str, config_path: str | None = None) -> None:
    """Show recommended config for a model."""
    import os

    # Set config path in environment BEFORE any imports
    if config_path:
        os.environ["DENDROPHIS_CONFIG"] = config_path

    from dendrophis.llm.calibration import (
        ModelOverrideStore,
        format_recommended_config,
    )

    store = ModelOverrideStore()
    print(format_recommended_config(model_id, store))


def _setup_exception_log() -> None:
    import logging
    from pathlib import Path

    log_path = Path.home() / ".config" / "dendrophis" / "exceptions.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_path)
    handler.setLevel(logging.WARNING)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s\n%(message)s\n"))
    logging.root.addHandler(handler)
    logging.root.setLevel(logging.WARNING)


def main() -> None:
    _setup_exception_log()
    parser = build_parser()
    args = parser.parse_args()

    # Handle calibration/list commands first (they don't need full UI)
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

    from dendrophis.config.loader import ConfigLoader
    from dendrophis.ui.app import DendrophisApp

    loader = ConfigLoader.load(config_path=args.config)
    if args.model:
        loader.config.llm.model = args.model

    # Set up tool logging if --log flag is provided
    if args.log:
        import os

        os.environ["DENDROPHIS_TOOL_LOG"] = "1"
        print("🔍 Tool execution logging enabled - check tool_log.txt")

    # Set parallel tools config based on --no-parallel-tools flag
    if args.no_parallel_tools:
        loader.config.tools.parallel_tools = False
        print("🔧 Parallel tool execution disabled")

    session_path = _resolve_session(args.session) if args.session else None
    app = DendrophisApp(config_loader=loader, session_path=session_path)
    app.run()

    saved = app._session.save_session()
    if saved:
        print(f"Session: {app._session.session_id[:8]}")

    sys.exit(0)
