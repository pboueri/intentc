#!/usr/bin/env bash
# run_one.sh — Run a single intentc build for the specification sensitivity experiment.
#
# Usage: ./run_one.sh <specificity_level> <run_number>
#
# Creates a temp directory, copies intent files, runs intentc build,
# and copies the result to the output directory.

set -euo pipefail

SPEC_LEVEL="$1"
RUN_NUM="$2"

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
EXPERIMENT_DIR="${REPO_ROOT}/experiments/specification_sensitivity"
INPUT_DIR="${EXPERIMENT_DIR}/inputs/specificity_${SPEC_LEVEL}/intent"
OUTPUT_BASE="${EXPERIMENT_DIR}/output/runs/specificity_${SPEC_LEVEL}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
RUN_NAME="src_${TIMESTAMP}_run${RUN_NUM}"

# Validate input exists
if [[ ! -d "${INPUT_DIR}" ]]; then
    echo "ERROR: Input directory not found: ${INPUT_DIR}"
    exit 1
fi

# Create a clean temp directory
TMPDIR="$(mktemp -d)"
trap 'rm -rf "${TMPDIR}"' EXIT

echo "=== Specificity ${SPEC_LEVEL}, Run ${RUN_NUM} ==="
echo "Temp dir: ${TMPDIR}"
echo "Output:   ${OUTPUT_BASE}/${RUN_NAME}"

# Copy intent files
cp -r "${INPUT_DIR}" "${TMPDIR}/intent"

# Run intentc build
echo "--- Running intentc build ---"
cd "${TMPDIR}"
intentc build --force 2>&1 | tee "${TMPDIR}/build.log"
BUILD_EXIT=$?

# Create output directory and copy results
mkdir -p "${OUTPUT_BASE}/${RUN_NAME}"

# Copy src/ if it exists
if [[ -d "${TMPDIR}/src" ]]; then
    cp -r "${TMPDIR}/src" "${OUTPUT_BASE}/${RUN_NAME}/src"
fi

# Copy build log
cp "${TMPDIR}/build.log" "${OUTPUT_BASE}/${RUN_NAME}/build.log"

# Copy intent for reference
cp -r "${TMPDIR}/intent" "${OUTPUT_BASE}/${RUN_NAME}/intent"

echo ""
echo "=== Done: specificity_${SPEC_LEVEL} run ${RUN_NUM} (exit: ${BUILD_EXIT}) ==="
echo "Output at: ${OUTPUT_BASE}/${RUN_NAME}"
