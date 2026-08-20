"""Test tool description clarity with LFM2.5-1.2B via omlx.yaml.

Runs ambiguous prompts with OLD vs NEW descriptions and reports which tool was chosen.
Usage: DENDROPHIS_CONFIG=omlx.yaml python3 test_tool_descriptions.py
"""

from __future__ import annotations

import asyncio
import copy
import os

os.environ.setdefault("DENDROPHIS_CONFIG", "omlx.yaml")

from dendrophis.config.loader import ConfigLoader
from dendrophis.debug_chat import run_single_chat

# ---------------------------------------------------------------------------
# Tool schemas: old descriptions
# ---------------------------------------------------------------------------

OLD_DESCRIPTIONS: dict[str, str] = {
    "glob": "Find files by glob pattern. Returns a list of matching file paths sorted by modification time.",
    "ripgrep": (
        "Search file contents using ripgrep (rg). "
        "PREFERRED over 'bash' for searching code as it is faster and handles common ignore patterns. "
        "Returns matching file paths with line numbers and context."
    ),
    "read": "Read a file or directory. For files, returns content. For directories, returns entries.",
    "edit": (
        "Edit a file by replacing exact text. To avoid 'multiple occurrences' errors, "
        "you MUST include several lines of surrounding context in 'old_string' to "
        "make the match 100% unique. IMPORTANT: Use ACTUAL raw characters "
        "(including literal newlines). Do NOT use escaped representations like \\n."
    ),
    "write": "Create a completely new file. Fails if file already exists. Provide the FULL file content.",
    "bash": (
        "Execute a non-interactive bash command. DO NOT run commands that "
        "require user input (like 'vim' or 'top') as they will hang. "
        "Use with caution."
    ),
    "analyze_functions": "Analyze Python file and return function locations and indentation as YAML",
    "get_function": "Extract a function's source code by name from a Python file",
    "replace_function": "Replace a function's implementation by reading from a file",
}

# ---------------------------------------------------------------------------
# Tool schemas: new descriptions
# ---------------------------------------------------------------------------

NEW_DESCRIPTIONS: dict[str, str] = {
    "glob": (
        "Find files by name pattern (e.g. src/**/*.py). "
        "Use this to locate files by path or extension; "
        "use ripgrep instead when you need to search inside file contents."
    ),
    "ripgrep": (
        "Search inside file contents for a text string or regex pattern. "
        "Use instead of glob (which matches filenames) or bash grep; "
        "returns matching lines with file path and line number."
    ),
    "read": (
        "Return the full contents of a file, or list entries in a directory. "
        "Use for reading complete files; "
        "use ripgrep to search for specific text without loading the whole file."
    ),
    "edit": (
        "Replace an exact block of text in an existing file. "
        "The old_string must match exactly — include several lines of surrounding context to make it unique. "
        "Fails if the file does not exist; use write to create new files."
    ),
    "write": (
        "Write content to a file, creating it if it doesn't exist, or overwriting it if it does. "
        "Provide the complete file content."
    ),
    "bash": (
        "Run a non-interactive shell command and return its output. "
        "Last resort — prefer glob, ripgrep, read, edit, or write for file tasks. "
        "Never use for commands that require user input (e.g. vim, top)."
    ),
    "analyze_functions": (
        "List every function in a Python file with its name, start line, end line, and indent level. "
        "Call this first to discover function names before using get_function."
    ),
    "get_function": (
        "Extract one named function from a Python file and save it to a temporary file for editing. "
        "Returns the temp file path — edit it, then call replace_function to write it back. "
        "Has a side effect: writes a .temp/ file. "
        "Do not use just to inspect source; use read instead for that."
    ),
    "replace_function": (
        "Overwrite a named function in a Python file with the contents of a temp file. "
        "Use after get_function + editing the temp file. "
        "Does not create new functions — the function name must already exist in the source file."
    ),
}

# ---------------------------------------------------------------------------
# Test cases: (prompt, expected_tool)
# ---------------------------------------------------------------------------

TEST_CASES = [
    ("find all .py files in the dendrophis/tools directory", "glob"),
    ("search the codebase for the string 'compaction_threshold'", "ripgrep"),
    ("read the file dendrophis/config/schema.py", "read"),
    ("list all functions defined in dendrophis/tools/builtins/filesystem.py", "analyze_functions"),
    ("show me the append_tool_result function from dendrophis/context/manager.py so I can edit it", "get_function"),
    ("search for any file whose name contains 'tokenizer'", "glob"),
    ("find all places in the code that call count_tokens", "ripgrep"),
    ("what functions exist in dendrophis/llm/client.py?", "analyze_functions"),
    ("I want to look at the truncate_to_tokens function in context/tokenizer.py", "read"),
]

SYSTEM_PROMPT = (
    "You are a coding assistant with access to tools. "
    "When the user asks you to do something, call the most appropriate tool immediately. "
    "Do not explain — just call the tool."
)

# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def run_test(prompt: str, tool_schemas: list[dict], label: str) -> str | None:
    """Run one prompt and return the first tool called, or None."""
    config_loader = ConfigLoader.load()
    config = config_loader.config
    config.llm.max_tokens = 4096

    result = await run_single_chat(
        message=prompt,
        config=config,
        system_prompt=SYSTEM_PROMPT,
        tools=tool_schemas,
        verbose=False,
        tool_choice="required",
    )
    calls = result["tool_calls"]
    return calls[0]["name"] if calls else f"[no tool — text: {result['text'][:60]!r}]"


def build_schemas(descriptions: dict[str, str], base_schemas: list[dict]) -> list[dict]:
    """Clone base schemas and swap in the given descriptions."""
    schemas = []
    for schema in base_schemas:
        s = copy.deepcopy(schema)
        name = s.get("function", {}).get("name") or s.get("name", "")
        if name in descriptions:
            if "function" in s:
                s["function"]["description"] = descriptions[name]
            else:
                s["description"] = descriptions[name]
        schemas.append(s)
    return schemas


async def main() -> None:
    # Load base schemas from registry
    from dendrophis.events.bus import EventBus
    from dendrophis.tools import create_builtin_registry

    bus = EventBus()
    bus.set_event_loop(asyncio.get_event_loop())
    reg = create_builtin_registry(bus, interactive=False)
    base_schemas = [t.schema for t in reg.all()]

    old_schemas = build_schemas(OLD_DESCRIPTIONS, base_schemas)
    new_schemas = build_schemas(NEW_DESCRIPTIONS, base_schemas)

    print(f"\n{'PROMPT':<55} {'EXPECTED':<20} {'OLD':<20} {'NEW':<20}")
    print("-" * 115)

    for prompt, expected in TEST_CASES:
        tagged = f"/no_think {prompt}"
        old_tool = await run_test(tagged, old_schemas, "OLD")
        new_tool = await run_test(tagged, new_schemas, "NEW")

        old_mark = "✓" if old_tool == expected else "✗"
        new_mark = "✓" if new_tool == expected else "✗"

        short_prompt = prompt[:54]
        print(f"{short_prompt:<55} {expected:<20} {old_mark} {old_tool:<18} {new_mark} {new_tool:<18}")

    bus.shutdown(wait=False)


if __name__ == "__main__":
    asyncio.run(main())
