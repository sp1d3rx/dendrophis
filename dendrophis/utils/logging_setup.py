import logging
import sys
from pathlib import Path


def setup_logging(debug_log_path: str | Path) -> None:
    """Configure global logging for the Dendrophis framework."""
    log_path = Path(debug_log_path).expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Root logger configuration
    root_logger = logging.getLogger("dendrophis")
    root_logger.setLevel(logging.DEBUG)

    # Formatter
    formatter = logging.Formatter("[%(asctime)s] %(levelname)s [%(name)s] %(message)s", datefmt="%H:%M:%S")

    # File Handler (Main Debug Log)
    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)

    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)
    root_logger.addHandler(console_handler)

    # Tool Log Handler (Specific for tool execution)
    # We use a separate file for tool logs to keep them clean
    tool_log_path = log_path.parent / "tool_execution.log"
    tool_handler = logging.FileHandler(tool_log_path)
    tool_handler.setFormatter(formatter)
    tool_handler.setLevel(logging.DEBUG)

    tool_logger = logging.getLogger("dendrophis.tools")
    tool_logger.addHandler(tool_handler)
    tool_logger.propagate = False  # Don't double-log to root handlers


def get_logger(name: str) -> logging.Logger:
    """Return a named logger instance."""
    return logging.getLogger(f"dendrophis.{name}")
