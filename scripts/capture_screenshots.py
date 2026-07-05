#!/usr/bin/env python3
"""
Screenshot capture script for dendrophis release.

This script sets up demo sessions and configurations to capture
screenshots of the dendrophis TUI for GitHub documentation.

Requirements:
    - dendrophis installed (uv run dendrophis)
    - Manual capture: use your OS screenshot tool
    - Optional: asciinema for animated demos

Usage:
    # Setup demo environment and launch for manual screenshot capture
    python capture_screenshots.py --scenario main_chat

    # Launch with specific config override
    python capture_screenshots.py --scenario settings --demo-api-key

    # Record animated demo with asciinema
    python capture_screenshots.py --method asciinema --scenario main_chat

    # Launch in tmux for automated capture prep
    python capture_screenshots.py --method tmux --scenario all

Scenarios:
    - main_chat: Main interface with conversation and sidebar
    - sidebar: Focus on sidebar panels (model, tokens, cache, etc.)
    - settings: Settings/configuration screen (Ctrl+T)
    - tool_confirm: Tool confirmation dialog
    - model_switcher: Model selection dropdown
    - help: Help/usage screen (/help)
    - all: Cycle through all scenarios
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
DENDROPHIS_DIR = PROJECT_ROOT / "dendrophis"
OPENROUTER_CONFIG = PROJECT_ROOT / "openrouter.yaml"
# Default screenshots directory (will be set properly in main)
SCREENSHOTS_DIR = PROJECT_ROOT / "screenshots"

# Demo API key for screenshots (this is a placeholder - replace with your own demo key)
DEMO_API_KEY = "sk-or-v1-demo-key-for-screenshots-only"

# Demo conversation for the main_chat scenario
DEMO_MESSAGES = [
    {"role": "system", "content": "You are Dex, an agentic coding assistant. Be concise, helpful, and direct."},
    {"role": "user", "content": "Create a Python function to calculate fibonacci numbers efficiently"},
    {
        "role": "assistant",
        "content": """Here's an efficient Fibonacci implementation using memoization:

```python
def fibonacci(n: int, memo: dict = None) -> int:
    \"\"\"Calculate fibonacci number with memoization.\"\"\"
    if memo is None:
        memo = {}
    if n in memo:
        return memo[n]
    if n <= 1:
        return n
    memo[n] = fibonacci(n - 1, memo) + fibonacci(n - 2, memo)
    return memo[n]


# Example usage
if __name__ == "__main__":
    for i in range(10):
        print(f"F({i}) = {fibonacci(i)}")
```

This runs in O(n) time due to memoization, avoiding the exponential complexity of naive recursion.""",
    },
    {"role": "user", "content": "Can you also write a CLI for this that accepts user input?"},
    {
        "role": "assistant",
        "content": """I'll create a CLI version with argument parsing.

**File:** `fib_cli.py`
```python
#!/usr/bin/env python3
import argparse


def fibonacci(n: int, memo: dict = None) -> int:
    if memo is None:
        memo = {}
    if n in memo:
        return memo[n]
    if n <= 1:
        return n
    memo[n] = fibonacci(n - 1, memo) + fibonacci(n - 2, memo)
    return memo[n]


def main():
    parser = argparse.ArgumentParser(description="Fibonacci calculator")
    parser.add_argument("n", type=int, help="Calculate F(n)")
    parser.add_argument("-r", "--range", action="store_true",
                        help="Show range from 0 to n")
    args = parser.parse_args()

    if args.range:
        for i in range(args.n + 1):
            print(f"F({i}) = {fibonacci(i)}")
    else:
        print(f"F({args.n}) = {fibonacci(args.args.n)}")


if __name__ == "__main__":
    main()
```

Save it and run: `python fib_cli.py 10 --range`""",
    },
]


def read_openrouter_config() -> str:
    """Read the openrouter.yaml config file."""
    if OPENROUTER_CONFIG.exists():
        return OPENROUTER_CONFIG.read_text()
    raise FileNotFoundError(f"openrouter.yaml not found at {OPENROUTER_CONFIG}")


def setup_demo_config(demo_api_key: bool = False) -> Path:
    """Create a temporary config based on openrouter.yaml."""
    config_dir = Path(tempfile.mkdtemp(prefix="dendrophis_screenshots_"))
    config_path = config_dir / "config.yaml"

    config_content = read_openrouter_config()

    if demo_api_key:
        # Replace API key with demo key for safety during screenshots
        import re
        config_content = re.sub(
            r'api_key:\s*"[^"]*"',
            f'api_key: "{DEMO_API_KEY}"',
            config_content,
        )

    config_path.write_text(config_content)
    return config_path


def setup_demo_session() -> Path:
    """Create a demo session file with sample conversation."""
    session_dir = Path(tempfile.mkdtemp(prefix="dendrophis_session_"))
    session_path = session_dir / "demo-screenshot-session.json"

    session_data = {
        "session_id": "demo-screenshot-session-abc123",
        "timestamp": "2026-06-06T20:00:00",
        "model": "google/gemma-4-31b-it",
        "messages": DEMO_MESSAGES,
        "metadata": {"total_tokens": 1247, "prompt_tokens": 89, "completion_tokens": 1158, "estimated_cost": 0.0012},
    }

    session_path.write_text(json.dumps(session_data, indent=2))
    return session_path


def launch_dendrophis(config_path: Path, session_path: Path = None, scenario: str = "main_chat") -> subprocess.Popen:
    """Launch dendrophis with the specified configuration."""
    env = os.environ.copy()
    env["DENDROPHIS_CONFIG"] = str(config_path)

    cmd = ["uv", "run", "dendrophis"]
    if session_path:
        cmd.extend(["--session", str(session_path)])

    print("Launching dendrophis...")
    print(f"Config: {config_path}")
    if session_path:
        print(f"Session: {session_path}")
    print(f"Scenario: {scenario}")
    print()

    return subprocess.Popen(cmd, cwd=PROJECT_ROOT, env=env)


def capture_manual_instructions(scenario: str) -> str:
    """Return instructions for capturing a specific scenario."""
    instructions = {
        "main_chat": """
┌─────────────────────────────────────────────────────────────┐
│  SCENARIO: Main Chat Interface                              │
├─────────────────────────────────────────────────────────────┤
│  This scenario shows the main chat with sidebar visible.    │
│                                                             │
│  CAPTURE INSTRUCTIONS:                                      │
│  1. Wait for dendrophis to fully load                       │
│  2. The demo session will show the conversation             │
│  3. Capture the full window with sidebar visible            │
│  4. Recommended filename: main_chat.png                     │
│                                                             │
│  HOTKEYS:                                                   │
│  - Press Ctrl+B to toggle sidebar if needed                 │
│  - Press Ctrl+T for settings (next scenario)                │
└─────────────────────────────────────────────────────────────┘
""",
        "sidebar": """
┌─────────────────────────────────────────────────────────────┐
│  SCENARIO: Sidebar Panels                                   │
├─────────────────────────────────────────────────────────────┤
│  This scenario highlights the sidebar with all panels.      │
│                                                             │
│  CAPTURE INSTRUCTIONS:                                      │
│  1. Ensure sidebar is visible (Ctrl+B if hidden)            │
│  2. Hover over different panels to show tooltips            │
│  3. Click on Model panel to show dropdown                   │
│  4. Capture showing Cache, Tokens, Context panels           │
│  5. Recommended filename: sidebar.png                       │
└─────────────────────────────────────────────────────────────┘
""",
        "settings": """
┌─────────────────────────────────────────────────────────────┐
│  SCENARIO: Settings Screen                                  │
├─────────────────────────────────────────────────────────────┤
│  This scenario shows the settings/configuration UI.         │
│                                                             │
│  CAPTURE INSTRUCTIONS:                                      │
│  1. Press Ctrl+T to open settings                           │
│  2. Navigate through different setting categories           │
│  3. Show the LLM configuration section                      │
│  4. Capture with settings visible                           │
│  5. Press Ctrl+T again or Escape to close                   │
│  6. Recommended filename: settings.png                      │
└─────────────────────────────────────────────────────────────┘
""",
        "model_switcher": """
┌─────────────────────────────────────────────────────────────┐
│  SCENARIO: Model Switcher                                   │
├─────────────────────────────────────────────────────────────┤
│  This scenario shows the model selection dropdown.          │
│                                                             │
│  CAPTURE INSTRUCTIONS:                                      │
│  1. Click on the Model panel in the sidebar                 │
│  2. Press Enter to open the model switcher                  │
│  3. Navigate through the model list                         │
│  4. Capture showing available models                        │
│  5. Recommended filename: model_switcher.png                │
└─────────────────────────────────────────────────────────────┘
""",
        "help": """
┌─────────────────────────────────────────────────────────────┐
│  SCENARIO: Help Screen                                      │
├─────────────────────────────────────────────────────────────┤
│  This scenario shows the help/usage screen.                 │
│                                                             │
│  CAPTURE INSTRUCTIONS:                                      │
│  1. Type /help and press Enter                              │
│  2. Wait for help content to display                        │
│  3. Capture showing all available commands                  │
│  4. Recommended filename: help.png                          │
└─────────────────────────────────────────────────────────────┘
""",
        "tool_confirm": """
┌─────────────────────────────────────────────────────────────┐
│  SCENARIO: Tool Confirmation Dialog                         │
├─────────────────────────────────────────────────────────────┤
│  This scenario shows a tool use confirmation dialog.        │
│                                                             │
│  CAPTURE INSTRUCTIONS:                                      │
│  1. Ask dendrophis to write a file, e.g.:                   │
│     "Create a file test.py with a hello world function"     │
│  2. Wait for the tool confirmation dialog to appear         │
│  3. Capture showing the dialog with options                 │
│  4. Recommended filename: tool_confirm.png                  │
└─────────────────────────────────────────────────────────────┘
""",
    }
    return instructions.get(scenario, instructions["main_chat"])


def capture_with_asciinema(scenario: str, demo_api_key: bool = False, output_dir: Path = None) -> int:
    """Record terminal session with asciinema."""
    if output_dir is None:
        output_dir = SCREENSHOTS_DIR
    output_dir.mkdir(exist_ok=True)
    cast_path = output_dir / f"{scenario}.cast"

    config_path = setup_demo_config(demo_api_key=demo_api_key)
    session_path = setup_demo_session() if scenario == "main_chat" else None

    # Build environment
    env = os.environ.copy()
    env["DENDROPHIS_CONFIG"] = str(config_path)

    cmd = ["asciinema", "rec", "-c"]
    if session_path:
        cmd.append(f"uv run dendrophis --session {session_path}")
    else:
        cmd.append("uv run dendrophis")
    cmd.append(str(cast_path))

    print(f"\n{'=' * 60}")
    print(f"Recording '{scenario}' with asciinema...")
    print(f"{'=' * 60}")
    print(capture_manual_instructions(scenario))
    print(f"\nOutput will be saved to: {cast_path}")
    print("\nINSTRUCTIONS:")
    print("1. The terminal will open with dendrophis running")
    print("2. Interact with it to showcase the feature")
    print("3. Type 'exit' or press Ctrl+D to end recording")
    print("4. Convert to GIF: asciicast2gif", cast_path, f"{scenario}.gif")
    print(f"{'=' * 60}\n")

    result = subprocess.run(cmd, env=env, cwd=PROJECT_ROOT)
    return result.returncode


def launch_for_capture(scenario: str, demo_api_key: bool = False, output_dir: Path = None) -> int:
    """Launch dendrophis for manual screenshot capture."""
    if output_dir is None:
        output_dir = SCREENSHOTS_DIR
    output_dir.mkdir(exist_ok=True)

    config_path = setup_demo_config(demo_api_key=demo_api_key)

    # For main_chat and scenarios needing conversation history
    session_path = None
    if scenario in ("main_chat", "sidebar", "all"):
        session_path = setup_demo_session()

    print(f"\n{'=' * 60}")
    print(f"DENDROPHIS SCREENSHOT CAPTURE: {scenario.upper()}")
    print(f"{'=' * 60}")
    print(capture_manual_instructions(scenario))

    print(f"\nSAVING SCREENSHOTS TO: {output_dir}")
    print(f"{'=' * 60}\n")

    # Launch dendrophis
    process = launch_dendrophis(config_path, session_path, scenario)

    print("\nDendrophis is now running. Press Ctrl+C when done capturing.\n")

    try:
        process.wait()
    except KeyboardInterrupt:
        print("\nShutting down dendrophis...")
        process.terminate()
        process.wait()

    print(f"\n{'=' * 60}")
    print("CAPTURE SESSION ENDED")
    print(f"{'=' * 60}")
    print(f"Remember to save your screenshots to: {output_dir}")
    print("\nSuggested files to create:")
    print(f"  - {output_dir}/main_chat.png")
    print(f"  - {output_dir}/sidebar.png")
    print(f"  - {output_dir}/settings.png")
    print(f"  - {output_dir}/model_switcher.png")
    print(f"  - {output_dir}/help.png")
    print(f"  - {output_dir}/tool_confirm.png")

    return 0


def create_tmux_session(scenario: str, demo_api_key: bool = False, output_dir: Path = None) -> int:
    """Create a tmux session prepped for screenshot capture."""
    if output_dir is None:
        output_dir = SCREENSHOTS_DIR
    output_dir.mkdir(exist_ok=True)

    config_path = setup_demo_config(demo_api_key=demo_api_key)
    session_path = setup_demo_session() if scenario == "main_chat" else None

    session_name = f"dendrophis-{scenario}"

    # Build environment setup
    env_setup = f"export DENDROPHIS_CONFIG={config_path}"

    # Build launch command
    launch_cmd = "uv run dendrophis"
    if session_path:
        launch_cmd += f" --session {session_path}"

    # Kill existing session if present
    subprocess.run(["tmux", "kill-session", "-t", session_name], capture_output=True)

    # Create new tmux session
    tmux_cmd = [
        "tmux",
        "new-session",
        "-d",
        "-s",
        session_name,
        "-c",
        str(PROJECT_ROOT),
        f"{env_setup} && {launch_cmd}",
    ]

    result = subprocess.run(tmux_cmd)

    if result.returncode == 0:
        print(f"\n{'=' * 60}")
        print(f"TMUX SESSION CREATED: {session_name}")
        print(f"{'=' * 60}")
        print("\nTo attach and capture:")
        print(f"  tmux attach -t {session_name}")
        print(f"\nScenario: {scenario}")
        print(capture_manual_instructions(scenario))
        print("\nTo kill session when done:")
        print(f"  tmux kill-session -t {session_name}")
        print(f"{'=' * 60}")

    return result.returncode


def main():
    parser = argparse.ArgumentParser(
        description="Capture dendrophis screenshots for release",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Launch main chat interface for manual screenshot capture
  python capture_screenshots.py --scenario main_chat

  # Use demo API key for safety during screenshots
  python capture_screenshots.py --scenario settings --demo-api-key

  # Record with asciinema for animated GIF
  python capture_screenshots.py --method asciinema --scenario main_chat

  # Create tmux session for capture setup
  python capture_screenshots.py --method tmux --scenario all

Recommended workflow:
  1. Run --scenario main_chat to capture the main interface
  2. Press Ctrl+T in the app to open settings, capture that
  3. Use --scenario tool_confirm and trigger a file write
  4. Save all screenshots to the screenshots/ directory
  5. Add them to your README.md with ![](screenshots/main_chat.png)
        """,
    )
    parser.add_argument(
        "--method",
        choices=["manual", "asciinema", "tmux"],
        default="manual",
        help="Screenshot capture method (default: manual)",
    )
    parser.add_argument(
        "--scenario",
        choices=["main_chat", "sidebar", "settings", "model_switcher", "help", "tool_confirm", "all"],
        default="main_chat",
        help="Which UI scenario to capture (default: main_chat)",
    )
    parser.add_argument(
        "--demo-api-key", action="store_true", help="Replace API key with demo key in config (safer for screenshots)"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=SCREENSHOTS_DIR,
        help=f"Output directory for screenshots (default: {SCREENSHOTS_DIR})",
    )

    args = parser.parse_args()

    screenshots_dir = args.output_dir.resolve()
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("DENDROPHIS SCREENSHOT CAPTURE")
    print("=" * 60)
    print(f"Config source: {OPENROUTER_CONFIG}")
    print(f"Output directory: {screenshots_dir}")
    print(f"Method: {args.method}")
    print(f"Scenario: {args.scenario}")
    print("=" * 60)

    if args.method == "asciinema":
        if args.scenario == "all":
            for scenario in ["main_chat", "settings", "model_switcher"]:
                capture_with_asciinema(scenario, args.demo_api_key, screenshots_dir)
        else:
            return capture_with_asciinema(args.scenario, args.demo_api_key, screenshots_dir)

    elif args.method == "tmux":
        if args.scenario == "all":
            for scenario in ["main_chat", "sidebar", "settings", "model_switcher", "help"]:
                create_tmux_session(scenario, args.demo_api_key, screenshots_dir)
        else:
            return create_tmux_session(args.scenario, args.demo_api_key, screenshots_dir)

    else:  # manual
        return launch_for_capture(args.scenario, args.demo_api_key, screenshots_dir)


if __name__ == "__main__":
    sys.exit(main())
