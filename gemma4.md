# Dendrophis System Integration: Learnings & Observations

This document summarizes the technical observations and operational learnings gathered during the initial exploration and debugging of the Dendrophis codebase.

## 1. Core Architecture & Configuration

### Configuration-Driven Persona
The agent's identity, operating guidelines, and safety constraints are not hardcoded into the model's weights but are injected via a `system_prompt` defined in the configuration.
- **Location**: `dendrophis/config/defaults.py` (Default YAML template).
- **Mechanism**: The `ConfigLoader` in `dendrophis/config/loader.py` loads the YAML, applies environment variable overrides (e.g., `DENDROPHIS_API_KEY`), and validates the structure against a schema in `dendrophis/config/schema.py`.
- **Implication**: The agent's behavior is highly modular and can be modified at runtime without retraining.

### Project Structure
- `dendrophis/cli.py`: Entry point for CLI argument parsing.
- `dendrophis/ui/`: Manages the Textual-based terminal interface.
- `dendrophis/tools/`: (Inferred) Contains the implementation of agentic capabilities.
- `dendrophis/session/` & `dendrophis/memory/`: Manage ephemeral session state and persistent memory (SQLite/DB).

## 2. Debugging Case Study: SysInfoPanel Memory Bug

### Issue Identified
The `SysInfoPanel` was reporting incorrect memory usage (e.g., reporting ~117GB instead of ~117MB).

### Root Cause
The bug was caused by an incorrect assumption regarding the units returned by `resource.getrusage(resource.RUSAGE_SELF).ru_maxrss` across different operating systems.
- **macOS (Darwin)**: `ru_maxrss` returns values in **bytes**.
- **Linux**: `ru_maxrss` returns values in **kilobytes**.

The original code used a single divisor (`/ 1024`) for both, which resulted in the macOS value being displayed as Kilobytes rather than Megabytes.

### Resolution
Implemented platform-specific scaling logic in `dendrophis/ui/widgets/panels/sysinfo_panel.py`:
```python
if sys.platform == "darwin":
    mem_mb = rusage.ru_maxrss / (1024 * 1024)
else:
    mem_mb = rusage.ru_maxrss / 1024
```

## 3. Tool Usage & Operational Nuances

### The "Double Escaping" Trap
A critical observation was made regarding the `edit` tool. 

**The Problem**: When reading files through the agentic interface, newlines are often represented as the string literal `\n`. If an agent attempts to use these literal characters in the `old_string` parameter of an `edit` call, the tool fails because the actual file contains real newline bytes, not the characters `\` and `n`.

**The Lesson**: 
- `old_string` must match the **raw byte content** of the file.
- Avoid using escaped representations (like `\\n`) in tool calls; use actual newlines to ensure a 100% unique match.

## 4. Summary of Agentic Workflow
1. **Investigate**: Use `ripgrep` and `read` to map the codebase.
2. **Analyze**: Identify discrepancies between expected behavior (e.g., correct units) and actual output.
3. **Execute**: Apply precise edits, being mindful of platform-specific logic and exact string matching requirements.
