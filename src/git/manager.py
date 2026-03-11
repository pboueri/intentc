"""Git operations manager for intentc - wraps git CLI commands."""

from __future__ import annotations

import subprocess
from typing import Protocol

from pydantic import BaseModel, Field

# Commit prefix constants for intentc-managed commits.
INTENT_PREFIX = "intent:"
GENERATED_PREFIX = "generated:"
REFINE_PREFIX = "refine:"


class GitStatus(BaseModel):
    """Parsed output of git status."""

    branch: str = ""
    clean: bool = True
    modified_files: list[str] = Field(default_factory=list)
    untracked_files: list[str] = Field(default_factory=list)
    staged_files: list[str] = Field(default_factory=list)


class GitManager(Protocol):
    """Protocol defining the git operations interface."""

    def initialize(self, project_root: str) -> None: ...
    def is_git_repo(self) -> bool: ...
    def add(self, files: list[str]) -> None: ...
    def commit(self, message: str) -> None: ...
    def get_status(self) -> GitStatus: ...
    def get_current_branch(self) -> str: ...
    def get_commit_hash(self) -> str: ...
    def get_diff(self, paths: list[str] | None = None, include_untracked: bool = False) -> str: ...
    def get_diff_stat(self, paths: list[str] | None = None, include_untracked: bool = False) -> str: ...


class GitCLIManager:
    """Concrete git manager that shells out to the git CLI."""

    def __init__(self) -> None:
        self._project_root: str = ""

    def _run(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
        """Run a git command in the project root."""
        result = subprocess.run(
            ["git"] + args,
            cwd=self._project_root or None,
            capture_output=True,
            text=True,
        )
        if check and result.returncode != 0:
            stderr = result.stderr.strip()
            raise RuntimeError(f"git {' '.join(args)} failed: {stderr}")
        return result

    def initialize(self, project_root: str) -> None:
        """Initialize the manager with a project root directory.

        Verifies git is installed and the directory is a git repository.
        """
        self._project_root = project_root

        # Verify git is installed.
        try:
            subprocess.run(
                ["git", "--version"],
                capture_output=True,
                text=True,
                check=True,
            )
        except FileNotFoundError:
            raise RuntimeError("git is not installed or not in PATH")

        # Verify the project root is a git repo.
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=project_root,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError("not a git repository. Run 'git init' first.")

    def is_git_repo(self) -> bool:
        """Check whether the project root is inside a git repository."""
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=self._project_root or None,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def add(self, files: list[str]) -> None:
        """Stage files for commit."""
        self._run(["add"] + files)

    def commit(self, message: str) -> None:
        """Create a commit with the given message."""
        self._run(["commit", "-m", message])

    def get_status(self) -> GitStatus:
        """Parse ``git status --porcelain`` into a GitStatus model."""
        result = self._run(["status", "--porcelain"], check=False)

        modified: list[str] = []
        untracked: list[str] = []
        staged: list[str] = []

        for line in result.stdout.splitlines():
            if len(line) < 4:
                continue
            index_status = line[0]
            worktree_status = line[1]
            filepath = line[3:]

            # Staged changes (index column).
            if index_status in ("M", "A"):
                staged.append(filepath)

            # Unstaged modifications (worktree column).
            if worktree_status == "M":
                modified.append(filepath)

            # Untracked files.
            if index_status == "?" and worktree_status == "?":
                untracked.append(filepath)

        branch = self.get_current_branch()
        clean = len(modified) == 0 and len(untracked) == 0 and len(staged) == 0

        return GitStatus(
            branch=branch,
            clean=clean,
            modified_files=modified,
            untracked_files=untracked,
            staged_files=staged,
        )

    def get_current_branch(self) -> str:
        """Return the name of the current branch."""
        result = self._run(["branch", "--show-current"])
        return result.stdout.strip()

    def get_commit_hash(self) -> str:
        """Return the full SHA of HEAD."""
        result = self._run(["rev-parse", "HEAD"])
        return result.stdout.strip()

    def get_diff(self, paths: list[str] | None = None, include_untracked: bool = False) -> str:
        """Get unified diff of working directory changes.

        If include_untracked is True, untracked files in paths are staged
        with --intent-to-add first so they appear in the diff.
        """
        if include_untracked and paths:
            self._run(["add", "--intent-to-add"] + paths, check=False)
        args = ["diff"]
        if paths:
            args.append("--")
            args.extend(paths)
        result = self._run(args, check=False)
        return result.stdout

    def get_diff_stat(self, paths: list[str] | None = None, include_untracked: bool = False) -> str:
        """Get diff stat summary (e.g. '3 files changed, +10 -2')."""
        if include_untracked and paths:
            self._run(["add", "--intent-to-add"] + paths, check=False)
        args = ["diff", "--stat"]
        if paths:
            args.append("--")
            args.extend(paths)
        result = self._run(args, check=False)
        return result.stdout.strip()


def new_git_manager() -> GitCLIManager:
    """Factory function returning a new GitCLIManager instance."""
    return GitCLIManager()
