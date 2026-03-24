#!/usr/bin/env bash
# bootstrap.sh — Rebuild src/ from intent/ in an isolated git worktree.
#
# This is the production self-compilation loop: install the current intentc,
# rebuild all of src/ from intent files in a worktree, then choose to accept
# (merge) or abort (delete the worktree).
#
# Usage:
#   ./bootstrap.sh [options]
#
# Options:
#   --target <t>    Build a single target instead of the full DAG
#   --no-force      Respect existing build state (default is --force)
#   --skip-compare  Skip the comparison step
#   -h, --help      Show this help message
#
set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
TARGET=""
FORCE="--force"
SKIP_COMPARE=false

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --target)       TARGET="$2"; shift 2 ;;
        --no-force)     FORCE=""; shift ;;
        --skip-compare) SKIP_COMPARE=true; shift ;;
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
REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
CURRENT_BRANCH="$(git -C "${REPO_ROOT}" rev-parse --abbrev-ref HEAD)"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
WORKTREE_BRANCH="bootstrap/${TIMESTAMP}"
WORKTREE_DIR="${REPO_ROOT}/.worktrees/bootstrap-${TIMESTAMP}"

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------
if [[ -d "${WORKTREE_DIR}" ]]; then
    echo "A bootstrap worktree already exists at ${WORKTREE_DIR}."
    echo "Run './bootstrap.sh' after removing it, or clean up with:"
    echo "  git worktree remove .worktrees/bootstrap"
    exit 1
fi

# Check for uncommitted changes — the worktree is based on HEAD, so
# uncommitted intent/ changes would be missed.
if ! git -C "${REPO_ROOT}" diff --quiet -- intent/ || \
   ! git -C "${REPO_ROOT}" diff --cached --quiet -- intent/; then
    echo "Warning: you have uncommitted changes in intent/."
    echo "The worktree will be based on HEAD, so those changes won't be included."
    read -rp "Continue anyway? [y/N] " answer
    [[ "${answer}" =~ ^[Yy]$ ]] || exit 0
fi

echo "=== Bootstrap: Self-Compilation ==="
echo "Branch:    ${CURRENT_BRANCH}"
echo "Worktree:  ${WORKTREE_DIR}"
echo "Bootstrap: ${WORKTREE_BRANCH}"
echo ""

# ---------------------------------------------------------------------------
# 1. Create worktree on a new branch
# ---------------------------------------------------------------------------
echo "--- Creating worktree ---"
mkdir -p "${REPO_ROOT}/.worktrees"
git -C "${REPO_ROOT}" worktree add "${WORKTREE_DIR}" -b "${WORKTREE_BRANCH}"
echo ""

# Cleanup function — removes worktree on unexpected exit
cleanup() {
    if [[ -d "${WORKTREE_DIR}" ]]; then
        echo ""
        echo "Cleaning up worktree..."
        git -C "${REPO_ROOT}" worktree remove --force "${WORKTREE_DIR}" 2>/dev/null || true
        git -C "${REPO_ROOT}" branch -D "${WORKTREE_BRANCH}" 2>/dev/null || true
    fi
}

# ---------------------------------------------------------------------------
# 2. Install intentc into a local venv (avoids clobbering the global install)
# ---------------------------------------------------------------------------
BOOTSTRAP_VENV="${WORKTREE_DIR}/.bootstrap-venv"
INTENTC="${BOOTSTRAP_VENV}/bin/intentc"

echo "--- Installing intentc from current source ---"
echo "Venv: ${BOOTSTRAP_VENV}"
uv venv "${BOOTSTRAP_VENV}"
(cd "${WORKTREE_DIR}" && uv pip install --python "${BOOTSTRAP_VENV}/bin/python" .)
echo ""

# ---------------------------------------------------------------------------
# 3. Delete src/ in worktree and rebuild
# ---------------------------------------------------------------------------
echo "--- Removing src/ in worktree ---"
rm -rf "${WORKTREE_DIR}/src/"
echo ""

echo "--- Building from intent/ ---"
BUILD_CMD=("${INTENTC}" build)
[[ -n "${FORCE}" ]] && BUILD_CMD+=(${FORCE})
[[ -n "${TARGET}" ]] && BUILD_CMD+=("${TARGET}")

echo "Running: ${BUILD_CMD[*]}"
echo ""

if ! (cd "${WORKTREE_DIR}" && "${BUILD_CMD[@]}"); then
    echo ""
    echo "Build FAILED."
    echo "Worktree preserved at: ${WORKTREE_DIR}"
    echo ""
    echo "To inspect:  cd ${WORKTREE_DIR}"
    echo "To clean up: git worktree remove --force .worktrees/bootstrap && git branch -D ${WORKTREE_BRANCH}"
    exit 1
fi

echo ""
echo "Build complete."

# ---------------------------------------------------------------------------
# 4. Compare (optional)
# ---------------------------------------------------------------------------
if [[ "${SKIP_COMPARE}" == "false" ]]; then
    echo ""
    echo "--- Comparing current src/ with rebuilt src/ ---"
    (cd "${REPO_ROOT}" && "${INTENTC}" compare src/ "${WORKTREE_DIR}/src/") || true
fi

# ---------------------------------------------------------------------------
# 5. Show diff stats and prompt
# ---------------------------------------------------------------------------
echo ""
echo "--- Diff summary (worktree vs current branch) ---"
git -C "${WORKTREE_DIR}" diff "${CURRENT_BRANCH}" --stat -- src/ || true

echo ""
echo "What would you like to do?"
echo "  [a] Accept — merge bootstrap commits into ${CURRENT_BRANCH}"
echo "  [d] Abort  — delete the worktree, no changes to ${CURRENT_BRANCH}"
echo "  [i] Inspect — keep the worktree open, decide later"
echo ""
read -rp "Choice [a/d/i]: " choice

case "${choice}" in
    a|A)
        echo ""
        echo "--- Merging ${WORKTREE_BRANCH} into ${CURRENT_BRANCH} ---"
        git -C "${REPO_ROOT}" merge "${WORKTREE_BRANCH}" --no-edit
        echo ""
        echo "--- Cleaning up worktree ---"
        git -C "${REPO_ROOT}" worktree remove --force "${WORKTREE_DIR}"
        git -C "${REPO_ROOT}" branch -d "${WORKTREE_BRANCH}"
        echo ""
        echo "Done. Bootstrap commits merged into ${CURRENT_BRANCH}."
        ;;
    d|D)
        cleanup
        echo "Aborted. No changes to ${CURRENT_BRANCH}."
        ;;
    i|I)
        echo ""
        echo "Worktree preserved at: ${WORKTREE_DIR}"
        echo "Branch: ${WORKTREE_BRANCH}"
        echo ""
        echo "To inspect:      cd ${WORKTREE_DIR}"
        echo "To accept later: git merge ${WORKTREE_BRANCH}"
        echo "To clean up:     git worktree remove --force .worktrees/bootstrap && git branch -D ${WORKTREE_BRANCH}"
        ;;
    *)
        echo "Unknown choice. Worktree preserved for safety."
        echo "To clean up: git worktree remove --force .worktrees/bootstrap && git branch -D ${WORKTREE_BRANCH}"
        ;;
esac
