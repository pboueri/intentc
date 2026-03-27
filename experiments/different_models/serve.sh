#!/usr/bin/env bash
# serve.sh — Serve a built output for a given model and effort level.
#
# Usage: ./serve.sh <model> <effort>
#   e.g.: ./serve.sh opus high
#         ./serve.sh sonnet low

set -euo pipefail

MODEL="$1"
EFFORT="$2"

EXPERIMENT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNS_DIR="${EXPERIMENT_DIR}/outputs/runs"
PORT="${PORT:-4567}"

# Find the matching run directory (model_effort_timestamp)
PREFIX="${MODEL}_${EFFORT}_"
RUN_DIR=""
for d in "${RUNS_DIR}"/${PREFIX}*/; do
    if [[ -d "$d" ]]; then
        RUN_DIR="$d"
    fi
done

if [[ -z "${RUN_DIR}" ]]; then
    echo "ERROR: No run found matching ${MODEL}_${EFFORT}_*"
    echo "Available runs:"
    ls "${RUNS_DIR}/" 2>/dev/null | sed 's/^/  /'
    exit 1
fi

SRC_DIR="${RUN_DIR}src"

if [[ ! -d "${SRC_DIR}" ]]; then
    echo "ERROR: No src/ directory in ${RUN_DIR}"
    exit 1
fi

echo "Serving: $(basename "${RUN_DIR%/}")"
echo "Source:  ${SRC_DIR}"

# Check for pre-built dist/
DIST_DIR="${SRC_DIR}/dist"
if [[ -d "${DIST_DIR}" && -f "${DIST_DIR}/index.html" ]]; then
    echo "Mode:    static (dist/)"
    echo "URL:     http://localhost:${PORT}"
    echo ""
    cd "${DIST_DIR}"
    exec python3 -m http.server "${PORT}"
fi

# Check for vite
if [[ -x "${SRC_DIR}/node_modules/.bin/vite" ]]; then
    echo "Mode:    vite dev server"
    echo "URL:     http://localhost:${PORT}"
    echo ""
    cd "${SRC_DIR}"
    exec npx vite --port "${PORT}"
fi

# Check for server.js
if [[ -f "${SRC_DIR}/server.js" ]]; then
    echo "Mode:    node server.js"
    echo "URL:     http://localhost:${PORT}"
    echo ""
    cd "${SRC_DIR}"
    # Try to use PORT env var; if the server ignores it, it'll use its hardcoded port
    PORT="${PORT}" exec node server.js
fi

# Fallback: serve the src directory directly
if [[ -f "${SRC_DIR}/index.html" ]]; then
    echo "Mode:    static (src/)"
    echo "URL:     http://localhost:${PORT}"
    echo ""
    cd "${SRC_DIR}"
    exec python3 -m http.server "${PORT}"
fi

echo "ERROR: Don't know how to serve ${SRC_DIR}"
exit 1
