#!/usr/bin/env bash
# bootstrap.sh — Rebuild src/ from intent/ in an isolated git worktree.
#
# This is the production self-compilation loop: install the current intentc,
# rebuild all of src/ from intent files in a worktree with no git history
# (so the agent builds purely from intent), then choose to accept (replay
# the build commits onto your branch) or abort (delete everything).
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
PATCH_DIR="${WORKTREE_DIR}/.patches"

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------
if [[ -d "${WORKTREE_DIR}" ]]; then
    echo "A bootstrap worktree already exists at ${WORKTREE_DIR}."
    echo "Run './bootstrap.sh' after removing it, or clean up with:"
    echo "  git worktree remove --force ${WORKTREE_DIR} && git branch -D ${WORKTREE_BRANCH}"
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
# 3. Delete src/, strip git history, set up hermetic environment
# ---------------------------------------------------------------------------
echo "--- Removing src/ in worktree ---"
rm -rf "${WORKTREE_DIR}/src/"

echo "--- Stripping git history (orphan branch) ---"
# Record the base commit so we know where to replay patches onto later.
# Then create an orphan branch so the agent has zero history to read from.
ORPHAN_BRANCH="_bootstrap_orphan_${TIMESTAMP}"
(cd "${WORKTREE_DIR}" && \
    git checkout --orphan "${ORPHAN_BRANCH}" --quiet && \
    git add -A && \
    git commit -m "bootstrap: clean slate (no history)" --quiet)

# Add a CLAUDE.md that reinforces no-history behavior
cat > "${WORKTREE_DIR}/CLAUDE.md" << 'HEREDOC'
# Bootstrap Build Rules

You are building this project from intent files. Follow these rules strictly:

- Do NOT use git log, git blame, git show, or any git history commands
- Do NOT reference or reconstruct code from previous commits
- Build everything from scratch using ONLY the intent files in intent/
- The intent files are the sole source of truth
HEREDOC
(cd "${WORKTREE_DIR}" && git add CLAUDE.md && git commit -m "bootstrap: add CLAUDE.md" --quiet)

# Record the root commit — build commits will be everything after this.
ORPHAN_BASE="$(git -C "${WORKTREE_DIR}" rev-parse HEAD)"
echo "Orphan base: ${ORPHAN_BASE}"
echo ""

# ---------------------------------------------------------------------------
# 4. Build from intent/
# ---------------------------------------------------------------------------
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
    echo "To clean up: git worktree remove --force ${WORKTREE_DIR} && git branch -D ${WORKTREE_BRANCH}"
    exit 1
fi

echo ""
echo "Build complete."

# ---------------------------------------------------------------------------
# 5. Compare (optional)
# ---------------------------------------------------------------------------
if [[ "${SKIP_COMPARE}" == "false" ]]; then
    echo ""
    echo "--- Comparing current src/ with rebuilt src/ ---"
    (cd "${REPO_ROOT}" && "${INTENTC}" compare src/ "${WORKTREE_DIR}/src/") || true
fi

# ---------------------------------------------------------------------------
# 6. Export build commits as patches
# ---------------------------------------------------------------------------
echo ""
echo "--- Exporting build commits as patches ---"
mkdir -p "${PATCH_DIR}"
PATCH_COUNT=$(git -C "${WORKTREE_DIR}" format-patch "${ORPHAN_BASE}..HEAD" -o "${PATCH_DIR}" | wc -l | tr -d ' ')
echo "${PATCH_COUNT} patch(es) exported to ${PATCH_DIR}"

# Show what will be applied
echo ""
echo "--- Build commits ---"
git -C "${WORKTREE_DIR}" log --oneline "${ORPHAN_BASE}..HEAD"

# ---------------------------------------------------------------------------
# 7. Prompt
# ---------------------------------------------------------------------------
echo ""
echo "What would you like to do?"
echo "  [a] Accept — replay ${PATCH_COUNT} build commit(s) onto ${CURRENT_BRANCH}"
echo "  [d] Abort  — delete the worktree, no changes to ${CURRENT_BRANCH}"
echo "  [i] Inspect — keep the worktree open, decide later"
echo ""
read -rp "Choice [a/d/i]: " choice

case "${choice}" in
    a|A)
        echo ""
        echo "--- Removing existing generated src/ before applying patches ---"
        if git -C "${REPO_ROOT}" ls-files --error-unmatch src/intentc/ >/dev/null 2>&1; then
            git -C "${REPO_ROOT}" rm -r --quiet src/intentc/
            git -C "${REPO_ROOT}" commit -m "remove generated src before re-applying build patches"
        fi
        echo ""
        echo "--- Applying patches to ${CURRENT_BRANCH} ---"
        git -C "${REPO_ROOT}" am "${PATCH_DIR}"/*.patch
        echo ""
        echo "--- Cleaning up worktree ---"
        git -C "${REPO_ROOT}" worktree remove --force "${WORKTREE_DIR}"
        # The orphan branch isn't linked to our real branch, just force-delete it
        git -C "${REPO_ROOT}" branch -D "${WORKTREE_BRANCH}" 2>/dev/null || true
        git -C "${REPO_ROOT}" branch -D "${ORPHAN_BRANCH}" 2>/dev/null || true
        echo ""
        echo "Done. ${PATCH_COUNT} build commit(s) applied to ${CURRENT_BRANCH}."
        ;;
    d|D)
        cleanup
        echo "Aborted. No changes to ${CURRENT_BRANCH}."
        ;;
    i|I)
        echo ""
        echo "Worktree preserved at: ${WORKTREE_DIR}"
        echo "Patches at: ${PATCH_DIR}"
        echo ""
        echo "To inspect:       cd ${WORKTREE_DIR}"
        echo "To accept later:  git am ${PATCH_DIR}/*.patch"
        echo "To clean up:      git worktree remove --force ${WORKTREE_DIR} && git branch -D ${WORKTREE_BRANCH}"
        ;;
    *)
        echo "Unknown choice. Worktree preserved for safety."
        echo "To clean up: git worktree remove --force ${WORKTREE_DIR} && git branch -D ${WORKTREE_BRANCH}"
        ;;
esac
