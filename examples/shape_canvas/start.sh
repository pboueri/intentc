#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Starting Shape Canvas server..."
echo "Open http://localhost:8000 in your browser"
echo ""

python3 "$SCRIPT_DIR/src/server.py"
