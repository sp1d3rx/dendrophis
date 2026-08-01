# Contributing to Dendrophis

Thank you for your interest in contributing to **Dendrophis**! We welcome bug fixes, documentation improvements, and new features.

Please read through these guidelines to ensure a smooth contribution process.

---

## 🚀 Getting Started

### Prerequisites

- **Python 3.13+**
- **[uv](https://github.com/astral-sh/uv)** (recommended virtual environment and package manager)

### Development Setup

1. **Fork and clone the repository:**
   ```bash
   git clone https://github.com/sp1d3rx/dendrophis.git
   cd dendrophis
   ```

2. **Create a virtual environment and install in editable mode:**
   ```bash
   uv venv
   source .venv/bin/activate
   uv pip install -e .
   ```

---

## 🌿 Git & Pull Request Workflow

1. **Work on `main` branch or topic branches:**
   - Create a descriptive branch for your changes:
     ```bash
     git checkout -b feature/my-feature
     # or
     git checkout -b fix/bug-description
     ```
2. **Keep commits focused:**
   - Write clear, concise commit messages.
   - Group related changes into single, logical commits.

3. **Submit a Pull Request:**
   - Push your branch to your fork and submit a PR against `main`.
   - Provide a clear description of the bug fixed or feature added.

---

## 🎨 Code Quality & Testing Standards

Before committing or submitting a PR, ensure all linting, formatting, and test checks pass.

### 1. Formatting & Linting (Ruff)

We use **Ruff** for linting and formatting. Run:

```bash
# Check code style and linting
ruff check .

# Check formatting
ruff format --check .

# Auto-format code
ruff format .
```

### 2. Testing (pytest)

Run the test suite to ensure no regressions:

```bash
pytest
```

---

## 🔒 Scope Boundaries & Architectural Rules

To maintain codebase stability and performance, please adhere to the following scope guidelines:

- **Surgical Edits:** Prefer minimal, targeted changes over broad rewrites. Change only what is necessary to fix the bug or implement the requested feature.
- **Protected Protocol Layer:** Do **not** modify `dendrophis/events/` (`IEventBus` interface and event type definitions) without prior discussion. These form a stable internal API across many subsystems.
- **Architectural Changes:** Open an issue to discuss major refactoring, module restructuring, or changes to core dependency injection patterns before submitting code.

---

## 🐛 Reporting Bugs & Suggesting Features

- Search existing issues before creating a new one.
- When filing a bug report, include reproduction steps, your OS, Python version, and any relevant log traces.
