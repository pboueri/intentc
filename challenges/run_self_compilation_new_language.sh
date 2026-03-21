#!/usr/bin/env bash
# challenges/run_self_compilation_new_language.sh
#
# Runs the self-compilation new-language challenge.  Builds both Python and Go
# implementations in hermetic temp directories, then compares:
#   1. Python temp build  ↔  existing src/  (same-language equivalence)
#   2. Go temp build      ↔  Python temp build  (cross-language equivalence)
#
# Usage:
#   ./challenges/run_self_compilation_new_language.sh [options]
#
# Options:
#   --keep          Keep the temp directories after the run (for inspection)
#   --skip-compare  Build only; do not run `intentc compare`
#   --target <t>    Build a single target instead of the full DAG
#   --no-force      Don't pass --force (respect existing build state)
#   --python-only   Only build and compare the Python side
#   --go-only       Only build and compare the Go side
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
PYTHON_ONLY=false
GO_ONLY=false

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --keep)          KEEP=true; shift ;;
        --skip-compare)  SKIP_COMPARE=true; shift ;;
        --target)        TARGET="$2"; shift 2 ;;
        --no-force)      FORCE=""; shift ;;
        --python-only)   PYTHON_ONLY=true; shift ;;
        --go-only)       GO_ONLY=true; shift ;;
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

if [[ "${PYTHON_ONLY}" == "true" && "${GO_ONLY}" == "true" ]]; then
    echo "Error: --python-only and --go-only are mutually exclusive" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Resolve paths
# ---------------------------------------------------------------------------
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
WORK_DIR_PY="/tmp/intentc-newlang-py-${TIMESTAMP}"
WORK_DIR_GO="/tmp/intentc-newlang-go-${TIMESTAMP}"

echo "=== Self-Compilation New Language Challenge ==="
echo "Repo:        ${REPO_ROOT}"
[[ "${GO_ONLY}" == "false" ]] && echo "Python dir:  ${WORK_DIR_PY}"
[[ "${PYTHON_ONLY}" == "false" ]] && echo "Go dir:      ${WORK_DIR_GO}"
echo ""

# ---------------------------------------------------------------------------
# Helper: set up a hermetic work directory
# ---------------------------------------------------------------------------
setup_hermetic_dir() {
    local work_dir="$1"
    mkdir -p "${work_dir}"

    # Intent files (the only source of truth the agent should see)
    cp -R "${REPO_ROOT}/intent" "${work_dir}/intent"

    # Config (agent profile, output dir defaults)
    if [[ -f "${REPO_ROOT}/.intentc/config.yaml" ]]; then
        mkdir -p "${work_dir}/.intentc"
        cp "${REPO_ROOT}/.intentc/config.yaml" "${work_dir}/.intentc/config.yaml"
    fi

    # GitVersionControl requires a git repo. Initialise a throwaway repo.
    git -C "${work_dir}" init --quiet
    git -C "${work_dir}" commit --allow-empty -m "empty root" --quiet

    echo "Copied intent/ into ${work_dir}"
    echo "No src/, no .git history, no .intentc/state from the real repo."
}

# ---------------------------------------------------------------------------
# 1. Set up hermetic work directories
# ---------------------------------------------------------------------------
echo "--- Setting up hermetic environments ---"

if [[ "${GO_ONLY}" == "false" ]]; then
    setup_hermetic_dir "${WORK_DIR_PY}"
fi

if [[ "${PYTHON_ONLY}" == "false" ]]; then
    setup_hermetic_dir "${WORK_DIR_GO}"
fi

echo ""

# ---------------------------------------------------------------------------
# 2. Build Python
# ---------------------------------------------------------------------------
if [[ "${GO_ONLY}" == "false" ]]; then
    echo "--- Building Python ---"
    BUILD_CMD_PY=(intentc build --output-dir src_python/)
    [[ -n "${FORCE}" ]] && BUILD_CMD_PY+=(${FORCE})
    [[ -n "${TARGET}" ]] && BUILD_CMD_PY+=("${TARGET}")

    echo "Running: ${BUILD_CMD_PY[*]}"
    echo ""

    (cd "${WORK_DIR_PY}" && "${BUILD_CMD_PY[@]}")

    BUILD_PY_EXIT=$?
    if [[ ${BUILD_PY_EXIT} -ne 0 ]]; then
        echo ""
        echo "Python build failed (exit ${BUILD_PY_EXIT})."
        echo "Work dir preserved at: ${WORK_DIR_PY}"
        exit ${BUILD_PY_EXIT}
    fi

    echo ""
    echo "Python build complete. Generated code at: ${WORK_DIR_PY}/src_python/"
    echo ""
fi

# ---------------------------------------------------------------------------
# 3. Build Go
# ---------------------------------------------------------------------------
if [[ "${PYTHON_ONLY}" == "false" ]]; then
    echo "--- Building Go ---"
    BUILD_CMD_GO=(intentc build --implementation go --output-dir src_go/)
    [[ -n "${FORCE}" ]] && BUILD_CMD_GO+=(${FORCE})
    [[ -n "${TARGET}" ]] && BUILD_CMD_GO+=("${TARGET}")

    echo "Running: ${BUILD_CMD_GO[*]}"
    echo ""

    (cd "${WORK_DIR_GO}" && "${BUILD_CMD_GO[@]}")

    BUILD_GO_EXIT=$?
    if [[ ${BUILD_GO_EXIT} -ne 0 ]]; then
        echo ""
        echo "Go build failed (exit ${BUILD_GO_EXIT})."
        echo "Work dir preserved at: ${WORK_DIR_GO}"
        exit ${BUILD_GO_EXIT}
    fi

    echo ""
    echo "Go build complete. Generated code at: ${WORK_DIR_GO}/src_go/"
    echo ""
fi

# ---------------------------------------------------------------------------
# 4. Compare Python temp ↔ existing src/ (same-language equivalence)
# ---------------------------------------------------------------------------
COMPARE_PY_EXIT=0
COMPARE_CROSS_EXIT=0

if [[ "${SKIP_COMPARE}" == "false" && "${GO_ONLY}" == "false" ]]; then
    echo "--- Comparison 1: Python temp build ↔ existing src/ ---"
    echo "Running: intentc compare ${REPO_ROOT}/src/ ${WORK_DIR_PY}/src_python/"
    echo ""

    (cd "${REPO_ROOT}" && intentc compare "${REPO_ROOT}/src/" "${WORK_DIR_PY}/src_python/")
    COMPARE_PY_EXIT=$?

    if [[ ${COMPARE_PY_EXIT} -eq 0 ]]; then
        echo ""
        echo "=== Python comparison: EQUIVALENT ==="
    else
        echo ""
        echo "=== Python comparison: DIVERGENT (exit ${COMPARE_PY_EXIT}) ==="
    fi
    echo ""
fi

# ---------------------------------------------------------------------------
# 5. Compare Go temp ↔ Python temp (cross-language equivalence)
# ---------------------------------------------------------------------------
if [[ "${SKIP_COMPARE}" == "false" && "${PYTHON_ONLY}" == "false" && "${GO_ONLY}" == "false" ]]; then
    echo "--- Comparison 2: Go temp build ↔ Python temp build ---"
    echo "Running: intentc compare ${WORK_DIR_PY}/src_python/ ${WORK_DIR_GO}/src_go/"
    echo ""

    (cd "${REPO_ROOT}" && intentc compare "${WORK_DIR_PY}/src_python/" "${WORK_DIR_GO}/src_go/")
    COMPARE_CROSS_EXIT=$?

    if [[ ${COMPARE_CROSS_EXIT} -eq 0 ]]; then
        echo ""
        echo "=== Cross-language comparison: EQUIVALENT ==="
    else
        echo ""
        echo "=== Cross-language comparison: DIVERGENT (exit ${COMPARE_CROSS_EXIT}) ==="
    fi
    echo ""
elif [[ "${SKIP_COMPARE}" == "false" && "${GO_ONLY}" == "true" ]]; then
    echo "--- Comparison: Go temp build ↔ existing src/ ---"
    echo "Running: intentc compare ${REPO_ROOT}/src/ ${WORK_DIR_GO}/src_go/"
    echo ""

    (cd "${REPO_ROOT}" && intentc compare "${REPO_ROOT}/src/" "${WORK_DIR_GO}/src_go/")
    COMPARE_CROSS_EXIT=$?

    if [[ ${COMPARE_CROSS_EXIT} -eq 0 ]]; then
        echo ""
        echo "=== Go vs existing comparison: EQUIVALENT ==="
    else
        echo ""
        echo "=== Go vs existing comparison: DIVERGENT (exit ${COMPARE_CROSS_EXIT}) ==="
    fi
    echo ""
fi

# ---------------------------------------------------------------------------
# 6. Final report
# ---------------------------------------------------------------------------
echo "--- Results ---"
if [[ "${SKIP_COMPARE}" == "false" ]]; then
    if [[ "${GO_ONLY}" == "false" ]]; then
        if [[ ${COMPARE_PY_EXIT} -eq 0 ]]; then
            echo "Python ↔ existing src/:  EQUIVALENT"
        else
            echo "Python ↔ existing src/:  DIVERGENT"
        fi
    fi

    if [[ "${PYTHON_ONLY}" == "false" ]]; then
        if [[ "${GO_ONLY}" == "false" ]]; then
            if [[ ${COMPARE_CROSS_EXIT} -eq 0 ]]; then
                echo "Go ↔ Python:             EQUIVALENT"
            else
                echo "Go ↔ Python:             DIVERGENT"
            fi
        else
            if [[ ${COMPARE_CROSS_EXIT} -eq 0 ]]; then
                echo "Go ↔ existing src/:      EQUIVALENT"
            else
                echo "Go ↔ existing src/:      DIVERGENT"
            fi
        fi
    fi

    if [[ ${COMPARE_PY_EXIT} -eq 0 && ${COMPARE_CROSS_EXIT} -eq 0 ]]; then
        echo ""
        echo "=== CHALLENGE PASSED ==="
    else
        echo ""
        echo "=== CHALLENGE NOT YET COMPLETE ==="
    fi
fi

# ---------------------------------------------------------------------------
# 7. Cleanup
# ---------------------------------------------------------------------------
echo ""
if [[ "${KEEP}" == "true" ]]; then
    [[ "${GO_ONLY}" == "false" ]] && echo "Python work dir preserved at: ${WORK_DIR_PY}"
    [[ "${PYTHON_ONLY}" == "false" ]] && echo "Go work dir preserved at:     ${WORK_DIR_GO}"
    echo "To clean up: rm -rf /tmp/intentc-newlang-*-${TIMESTAMP}"
else
    [[ "${GO_ONLY}" == "false" ]] && rm -rf "${WORK_DIR_PY}"
    [[ "${PYTHON_ONLY}" == "false" ]] && rm -rf "${WORK_DIR_GO}"
    echo "Cleaned up temp directories."
fi
