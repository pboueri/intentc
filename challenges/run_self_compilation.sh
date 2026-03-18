#!/usr/bin/env bash
# challenges/run_self_compilation.sh
#
# Runs the self-compilation challenge in a hermetic temporary directory with no
# git history.  The build agent sees only intent files — never src/, never prior
# commits — so there is nothing to cheat from.
#
# Usage:
#   ./challenges/run_self_compilation.sh [options]
#
# Options:
#   --keep          Keep the temp directory after the run (for inspection)
#   --skip-compare  Build only; do not run `intentc compare`
#   --target <t>    Build a single target instead of the full DAG
#   --force         Passed through to `intentc build` (rebuild even if built)
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
        --keep)        KEEP=true; shift ;;
        --skip-compare) SKIP_COMPARE=true; shift ;;
        --target)      TARGET="$2"; shift 2 ;;
        --no-force)    FORCE=""; shift ;;
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
WORK_DIR="/tmp/intentc-selfcompile-${TIMESTAMP}"

echo "=== Self-Compilation Challenge ==="
echo "Repo:     ${REPO_ROOT}"
echo "Work dir: ${WORK_DIR}"
echo ""

# ---------------------------------------------------------------------------
# 1. Create hermetic work directory
#    Copy only what the build needs: intent files and config.
#    Explicitly exclude src/, .git/, and .intentc/state/ so the agent has
#    zero access to the existing implementation or its history.
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

# Assertions (shared validation fixtures, if any)
if [[ -d "${REPO_ROOT}/intent/assertions" ]]; then
    # already copied with intent/, but call it out for clarity
    true
fi

# GitVersionControl requires a git repo in the output dir. Initialise a
# throwaway repo at the work-dir root so commits have nowhere useful to look.
git -C "${WORK_DIR}" init --quiet
git -C "${WORK_DIR}" commit --allow-empty -m "empty root" --quiet

echo "Copied intent/ into ${WORK_DIR}"
echo "No src/, no .git history, no .intentc/state from the real repo."
echo ""

# ---------------------------------------------------------------------------
# 2. Run the build
# ---------------------------------------------------------------------------
echo "--- Building ---"
BUILD_CMD=(intentc build --output-dir src_generated/)
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
echo "Build complete. Generated code at: ${WORK_DIR}/src_generated/"

# ---------------------------------------------------------------------------
# 3. Compare (optional)
# ---------------------------------------------------------------------------
if [[ "${SKIP_COMPARE}" == "false" ]]; then
    echo ""
    echo "--- Comparing ---"
    echo "Running: intentc compare ${REPO_ROOT}/src/ ${WORK_DIR}/src_generated/"
    echo ""

    (cd "${REPO_ROOT}" && intentc compare "${REPO_ROOT}/src/" "${WORK_DIR}/src_generated/")
    COMPARE_EXIT=$?

    if [[ ${COMPARE_EXIT} -eq 0 ]]; then
        echo ""
        echo "=== EQUIVALENT ==="
    else
        echo ""
        echo "=== DIVERGENT (exit ${COMPARE_EXIT}) ==="
    fi
fi

# ---------------------------------------------------------------------------
# 4. Cleanup
# ---------------------------------------------------------------------------
echo ""
if [[ "${KEEP}" == "true" ]]; then
    echo "Work dir preserved at: ${WORK_DIR}"
    echo "To clean up: rm -rf ${WORK_DIR}"
else
    rm -rf "${WORK_DIR}"
    echo "Cleaned up ${WORK_DIR}"
fi
