#!/usr/bin/env bash
# challenges/run_self_compilation_less_specificity.sh
#
# Runs the self-compilation less-specificity challenge. Builds from the
# (modified, less-specific) intent files in a hermetic temp directory,
# then compares against existing src/ for functional equivalence.
#
# Usage:
#   ./challenges/run_self_compilation_less_specificity.sh [options]
#
# Options:
#   --keep          Keep the temp directory after the run (for inspection)
#   --skip-compare  Build only; do not run `intentc compare`
#   --target <t>    Build a single target instead of the full DAG
#   --no-force      Don't pass --force (respect existing build state)
#   -h, --help      Show this help message
#
set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
KEEP=false
SKIP_COMPARE=false
TARGET=""
FORCE="--force"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --keep)          KEEP=true; shift ;;
        --skip-compare)  SKIP_COMPARE=true; shift ;;
        --target)        TARGET="$2"; shift 2 ;;
        --no-force)      FORCE=""; shift ;;
        -h|--help)
            sed -n '2,/^$/{ s/^# \?//; p }' "$0"
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Resolve paths
# ---------------------------------------------------------------------------
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
WORK_DIR="/tmp/intentc-lessspec-${TIMESTAMP}"

echo "=== Self-Compilation Less Specificity Challenge ==="
echo "Repo:       ${REPO_ROOT}"
echo "Work dir:   ${WORK_DIR}"
echo ""

# ---------------------------------------------------------------------------
# Set up hermetic work directory
# ---------------------------------------------------------------------------
echo "--- Setting up hermetic environment ---"
mkdir -p "${WORK_DIR}"

# Intent files (the only source of truth the agent should see)
cp -R "${REPO_ROOT}/intent" "${WORK_DIR}/intent"

# Config (agent profile, output dir defaults)
if [[ -f "${REPO_ROOT}/.intentc/config.yaml" ]]; then
    mkdir -p "${WORK_DIR}/.intentc"
    cp "${REPO_ROOT}/.intentc/config.yaml" "${WORK_DIR}/.intentc/config.yaml"
fi

# GitVersionControl requires a git repo
git -C "${WORK_DIR}" init --quiet
git -C "${WORK_DIR}" commit --allow-empty -m "empty root" --quiet

echo "Copied intent/ into ${WORK_DIR}"
echo "No src/, no .git history, no .intentc/state from the real repo."
echo ""

# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
echo "--- Building from less-specific intent files ---"
BUILD_CMD=(intentc build --output-dir src/)
[[ -n "${FORCE}" ]] && BUILD_CMD+=(${FORCE})
[[ -n "${TARGET}" ]] && BUILD_CMD+=("${TARGET}")

echo "Running: ${BUILD_CMD[*]}"
echo ""

(cd "${WORK_DIR}" && "${BUILD_CMD[@]}")

BUILD_EXIT=$?
if [[ ${BUILD_EXIT} -ne 0 ]]; then
    echo ""
    echo "Build failed (exit ${BUILD_EXIT})."
    echo "Work dir preserved at: ${WORK_DIR}"
    exit ${BUILD_EXIT}
fi

echo ""
echo "Build complete. Generated code at: ${WORK_DIR}/src/"
echo ""

# ---------------------------------------------------------------------------
# Compare against existing src/
# ---------------------------------------------------------------------------
COMPARE_EXIT=0

if [[ "${SKIP_COMPARE}" == "false" ]]; then
    echo "--- Comparison: temp build ↔ existing src/ ---"
    echo "Running: intentc compare ${REPO_ROOT}/src/ ${WORK_DIR}/src/"
    echo ""

    (cd "${REPO_ROOT}" && intentc compare "${REPO_ROOT}/src/" "${WORK_DIR}/src/")
    COMPARE_EXIT=$?

    if [[ ${COMPARE_EXIT} -eq 0 ]]; then
        echo ""
        echo "=== EQUIVALENT ==="
    else
        echo ""
        echo "=== DIVERGENT (exit ${COMPARE_EXIT}) ==="
    fi
    echo ""
fi

# ---------------------------------------------------------------------------
# Final report
# ---------------------------------------------------------------------------
echo "--- Results ---"
if [[ "${SKIP_COMPARE}" == "false" ]]; then
    if [[ ${COMPARE_EXIT} -eq 0 ]]; then
        echo "temp build ↔ existing src/:  EQUIVALENT"
        echo ""
        echo "=== CHALLENGE PASSED ==="
    else
        echo "temp build ↔ existing src/:  DIVERGENT"
        echo ""
        echo "=== CHALLENGE NOT YET COMPLETE ==="
    fi
fi

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
echo ""
if [[ "${KEEP}" == "true" ]]; then
    echo "Work dir preserved at: ${WORK_DIR}"
    echo "To clean up: rm -rf ${WORK_DIR}"
else
    rm -rf "${WORK_DIR}"
    echo "Cleaned up temp directory."
fi
