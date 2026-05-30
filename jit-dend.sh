#!/usr/bin/env bash
set -euo pipefail

# Run dendrophis with CPython 3.13 JIT
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Prefer the installed package in the venv
cd "$SCRIPT_DIR"

if [[ -f ".venv/bin/python" ]]; then
    .venv/bin/python -X jit -m dendrophis "$@"
else
    python3.13 -X jit -m dendrophis "$@"
fi
