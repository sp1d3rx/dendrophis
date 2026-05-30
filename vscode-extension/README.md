# Py-Style VS Code Extension

A Visual Studio Code extension that integrates the **py-style** skill for analyzing Python code based on Raymond Hettinger's principles.

## Features

- Real-time analysis of Python files for unpythonic patterns
- Automatic suggestions in diff/patch format
- Auto-fix for safe cases (boolean prefixes, vague names, comprehensions, context managers)
- Configurable severity levels
- Works on file save or manually via commands

## Installation

1. Install the extension from the VS Code Marketplace (TBD)
2. Or build from source:
   ```bash
   npm install
   npm run compile
   code --install-extension py-style-1.0.0.vsix
   ```

## Usage

### Commands

- `Py-Style: Analyze Current File` - Analyze the active Python file
- `Py-Style: Fix Safe Issues` - Automatically fix safe issues in current file
- `Py-Style: Analyze Workspace` - Analyze all Python files in workspace
- `Py-Style: Toggle` - Enable/disable py-style on file save

### Settings

Add to your VS Code `settings.json`:

```json
{
  "py-style.enabled": true,
  "py-style.runOnSave": true,
  "py-style.autoFix": false,
  "py-style.severity": "style",
  "py-style.include": ["**/*.py"],
  "py-style.exclude": ["**/tests/**", "**/venv/**"]
}
```

## Supported Rules

This extension enforces Raymond Hettinger's Pythonic principles:

| Rule | Description |
|------|-------------|
| No single-letter variables | All variables must have meaningful names |
| Meaningful naming | Names should describe what they store |
| Boolean prefixes | Use `is_`, `has_`, `can_`, `should_` for booleans |
| Verbs for functions | Functions do things (verbs) |
| Nouns for variables | Variables hold things (nouns) |
| EAFP over LBYL | Use try/except over if/else for checks |
| Comprehensions | Use list/dict/set comprehensions over loops |
| Generators | Use generator expressions for memory efficiency |
| Context managers | Always use `with` for resource management |
| Unpacking | Prefer unpacking over index access |
| Dictionary techniques | Use defaultdict, Counter, get(), setdefault |
| Class design | Use ABCs, dataclasses, duck typing |

## Development

1. Clone this repository
2. Run `npm install`
3. Press F5 to launch the extension in a new VS Code window

## License

MIT
