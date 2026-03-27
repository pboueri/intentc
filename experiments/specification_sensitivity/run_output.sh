#!/usr/bin/env bash
# run_output.sh — Serve a completed experiment run for viewing in the browser.
#
# Usage: ./run_output.sh <specificity_level> <run_number> [port]
#
# Example: ./run_output.sh 5 3
#          ./run_output.sh 4 1 8080

set -euo pipefail

SPEC_LEVEL="${1:?Usage: ./run_output.sh <level> <run#> [port]}"
RUN_NUM="${2:?Usage: ./run_output.sh <level> <run#> [port]}"
PORT="${3:-8080}"

EXPERIMENT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNS_DIR="${EXPERIMENT_DIR}/output/runs/specificity_${SPEC_LEVEL}"

if [[ ! -d "${RUNS_DIR}" ]]; then
    echo "ERROR: No runs found for specificity ${SPEC_LEVEL}"
    exit 1
fi

# Find the matching run directory
RUN_DIR=""
for d in "${RUNS_DIR}"/src_*_run${RUN_NUM}; do
    if [[ -d "$d" ]]; then
        RUN_DIR="$d"
        break
    fi
done

if [[ -z "${RUN_DIR}" ]]; then
    echo "ERROR: Run ${RUN_NUM} not found in ${RUNS_DIR}"
    echo "Available runs:"
    ls "${RUNS_DIR}" 2>/dev/null
    exit 1
fi

SRC_DIR="${RUN_DIR}/src"

# Determine what to serve
if [[ -f "${SRC_DIR}/dist/index.html" ]]; then
    SERVE_DIR="${SRC_DIR}/dist"
    echo "Serving from dist/ (built output)"
elif [[ -f "${SRC_DIR}/index.html" ]]; then
    SERVE_DIR="${SRC_DIR}"
    echo "Serving from src/ root"
else
    echo "ERROR: No index.html found in ${SRC_DIR} or ${SRC_DIR}/dist/"
    echo "Files in src/:"
    ls "${SRC_DIR}"
    exit 1
fi

echo ""
echo "  Specificity: ${SPEC_LEVEL}"
echo "  Run:         ${RUN_NUM}"
echo "  Directory:   ${SERVE_DIR}"
echo ""
echo "  → http://localhost:${PORT}"
echo ""
echo "Press Ctrl+C to stop."
echo ""

python3 -m http.server "${PORT}" --directory "${SERVE_DIR}"
