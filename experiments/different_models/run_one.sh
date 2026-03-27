#!/usr/bin/env bash
# run_one.sh — Run a single intentc build for the different models experiment.
#
# Usage: ./run_one.sh <model> <effort>
#   model:  haiku, sonnet, opus
#   effort: low, medium, high, max, or "none" (skips --effort flag)
#
# Creates a temp directory, copies intent files, runs intentc build
# with the specified model/effort, and copies the result to the output directory.

set -euo pipefail

MODEL="$1"
EFFORT="$2"

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
EXPERIMENT_DIR="${REPO_ROOT}/experiments/different_models"
INPUT_DIR="${EXPERIMENT_DIR}/inputs/intent"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"

if [[ "${EFFORT}" == "none" ]]; then
    RUN_NAME="${MODEL}_${TIMESTAMP}"
else
    RUN_NAME="${MODEL}_${EFFORT}_${TIMESTAMP}"
fi

OUTPUT_DIR="${EXPERIMENT_DIR}/outputs/runs/${RUN_NAME}"

# Validate input exists
if [[ ! -d "${INPUT_DIR}" ]]; then
    echo "ERROR: Input directory not found: ${INPUT_DIR}"
    exit 1
fi

# Create a clean temp directory
TMPDIR="$(mktemp -d)"
trap 'rm -rf "${TMPDIR}"' EXIT

echo "=== Model: ${MODEL}, Effort: ${EFFORT} ==="
echo "Temp dir: ${TMPDIR}"
echo "Output:   ${OUTPUT_DIR}"

# Copy intent files
cp -r "${INPUT_DIR}" "${TMPDIR}/intent"

# Build config.yaml with the right model/effort
mkdir -p "${TMPDIR}/.intentc"
if [[ "${EFFORT}" == "none" ]]; then
    cat > "${TMPDIR}/.intentc/config.yaml" <<EOF
default_profile:
  name: default
  provider: claude
  model_id: ${MODEL}
  timeout: 3600
  retries: 3
default_output_dir: src
EOF
else
    cat > "${TMPDIR}/.intentc/config.yaml" <<EOF
default_profile:
  name: default
  provider: claude
  model_id: ${MODEL}
  effort: ${EFFORT}
  timeout: 3600
  retries: 3
default_output_dir: src
EOF
fi

# Run intentc build
echo "--- Running intentc build ---"
cd "${TMPDIR}"
intentc build --force 2>&1 | tee "${TMPDIR}/build.log"
BUILD_EXIT=$?

# Create output directory and copy results
mkdir -p "${OUTPUT_DIR}"

# Copy src/ if it exists
if [[ -d "${TMPDIR}/src" ]]; then
    cp -r "${TMPDIR}/src" "${OUTPUT_DIR}/src"
fi

# Copy build log
cp "${TMPDIR}/build.log" "${OUTPUT_DIR}/build.log"

# Copy intent and config for reference
cp -r "${TMPDIR}/intent" "${OUTPUT_DIR}/intent"
cp -r "${TMPDIR}/.intentc" "${OUTPUT_DIR}/.intentc"

echo ""
echo "=== Done: ${MODEL} ${EFFORT} (exit: ${BUILD_EXIT}) ==="
echo "Output at: ${OUTPUT_DIR}"
