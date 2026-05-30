#!/usr/bin/env bash
set -euo pipefail

# Run dendrophis with PyPy
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILING_DIR="${SCRIPT_DIR}/.profiling"
RUN_TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# Prefer the installed package in the venv
cd "$SCRIPT_DIR"

# Enable profiling if DENDROPHIS_PROFILE=1 is set
if [[ "${DENDROPHIS_PROFILE:-0}" == "1" ]]; then
    mkdir -p "$PROFILING_DIR"
    echo "Profiling enabled with PyPy - output will be written to $PROFILING_DIR"
    
    # Run with pstats directly on .pyc files (PyPy generates .pyc, not .prof)
    # Note: PyPy's cProfile output is a .pyc file, so we analyze that directly.
    pypy -m pstats "$PROFILING_DIR/profile_${RUN_TIMESTAMP}.pyc" > "$PROFILING_DIR/profile_${RUN_TIMESTAMP}_summary.txt" 2>&1 || true
    
    # Fallback summary if pstats fails or to ensure output is captured
    echo "Profile summary written to: $PROFILING_DIR/profile_${RUN_TIMESTAMP}_summary.txt"
else
    # Run with PyPy from the specific venv if it exists
if [[ -f "${SCRIPT_DIR}/.venv_pypy/bin/python" ]]; then
    "${SCRIPT_DIR}/.venv_pypy/bin/python" -m dendrophis "$@"
else
    # Fallback to system pypy
    pypy -m dendrophis "$@"
fi
fi
