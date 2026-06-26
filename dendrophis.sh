#!/usr/bin/env bash
set -euo pipefail

# Run dendrophis with its virtual environment
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILING_DIR="${SCRIPT_DIR}/.profiling"
RUN_TIMESTAMP=$(date +%Y%m%d_%H%M%S)
ORIGINAL_DIR="$(pwd)"

# The venv path is absolute, so no need to cd into SCRIPT_DIR.
# Always run dendrophis from the user's original working directory.
cd "$ORIGINAL_DIR"

# Parse arguments
UNBUFFERED=0
REMAINING_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --unbuffered)
            UNBUFFERED=1
            shift
            ;;
        *)
            REMAINING_ARGS+=("$1")
            shift
            ;;
    esac
done

if [[ "$UNBUFFERED" == "1" ]]; then
    export PYTHONUNBUFFERED=1
fi

# Enable profiling if DENDROPHIS_PROFILE=1 is set
if [[ "${DENDROPHIS_PROFILE:-0}" == "1" ]]; then
    mkdir -p "$PROFILING_DIR"
    echo "Profiling enabled - output will be written to $PROFILING_DIR"
    
    # Run with cProfile, output to timestamped file
    "$SCRIPT_DIR/.venv/bin/python" -m cProfile -o "$PROFILING_DIR/profile_${RUN_TIMESTAMP}.prof" -m dendrophis "${REMAINING_ARGS[@]}"
    
    # Generate human-readable stats summary
    "$SCRIPT_DIR/.venv/bin/python" -c "
import pstats
import sys
stats_file = '$PROFILING_DIR/profile_${RUN_TIMESTAMP}.prof'
p = pstats.Stats(stats_file)
p.sort_stats('cumulative')
with open('$PROFILING_DIR/profile_${RUN_TIMESTAMP}_summary.txt', 'w') as f:
    sys.stdout = f
    p.print_stats(50)
    sys.stdout = sys.__stdout__
print(f'Profile summary written to: $PROFILING_DIR/profile_${RUN_TIMESTAMP}_summary.txt')
"
else
    "$SCRIPT_DIR/.venv/bin/dendrophis" "${REMAINING_ARGS[@]}"
fi
