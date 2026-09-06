"""Per-target build state, backed by a `StorageBackend`, and version control for
checkpointing generated output.

`TargetStatus`, `BuildStep`, and `BuildResult` are defined in
`intentc.build.storage` (storage is upstream of state) and re-exported here
unchanged.
"""

from __future__ import annotations

import re
import secrets
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from intentc.build.storage import (
    BuildResult,
    BuildStep,
    SQLiteBackend,
    StorageBackend,
    TargetStatus,
)

if TYPE_CHECKING:
    from intentc.core import Project

_SLASH_RE = re.compile(r"[\\/]")
_EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def response_file_name(target: str) -> str:
    """UUID-based response file name for a target: slashes -> underscores, plus
    an 8-char random hex suffix."""
    safe_target = _SLASH_RE.sub("_", target)
    return f"{safe_target}-{secrets.token_hex(4)}.json"


# ---------------------------------------------------------------------------
# StateManager
# ---------------------------------------------------------------------------


class StateManager:
    """Manages per-target build state for a given output directory. Delegates
    all persistence to a `StorageBackend`."""

    def __init__(
        self,
        base_dir: "str | Path",
        output_dir: str,
        backend: Optional[StorageBackend] = None,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.output_dir = output_dir
        self.backend = backend if backend is not None else SQLiteBackend(self.base_dir, output_dir)

    @property
    def build_response_dir(self) -> Path:
        path = self.base_dir / ".intentc" / "state" / self.output_dir / "responses" / "build"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def val_response_dir(self) -> Path:
        path = self.base_dir / ".intentc" / "state" / self.output_dir / "responses" / "val"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_status(self, target: str) -> TargetStatus:
        return self.backend.get_status(target)

    def get_build_result(self, target: str) -> Optional[BuildResult]:
        return self.backend.get_build_result(target)

    def get_build_history(self, target: str, limit: int = 50) -> list[BuildResult]:
        return self.backend.get_build_history(target, limit=limit)

    def save_build_result(
        self, target: str, result: BuildResult, git_diff: Optional[str] = None
    ) -> int:
        return self.backend.save_build_result(target, result, git_diff=git_diff)

    def set_status(self, target: str, status: TargetStatus) -> None:
        self.backend.set_status(target, status)

    def mark_dependents_outdated(self, target: str, project: "Project") -> None:
        for descendant in project.descendants(target):
            self.backend.set_status(descendant, TargetStatus.OUTDATED)

    def reset(self, target: str) -> None:
        self.backend.reset(target)

    def reset_all(self) -> None:
        self.backend.reset_all()

    def list_targets(self) -> list[tuple[str, TargetStatus]]:
        return self.backend.list_targets()


# ---------------------------------------------------------------------------
# VersionControl
# ---------------------------------------------------------------------------


class VersionControl(ABC):
    """Abstract interface for checkpointing file changes."""

    @abstractmethod
    def checkpoint(self, message: str) -> str: ...

    @abstractmethod
    def diff(self, from_id: str, to_id: str) -> str: ...

    @abstractmethod
    def restore(self, commit_id: str) -> None: ...

    @abstractmethod
    def log(self, target: Optional[str] = None) -> list[str]: ...

    @abstractmethod
    def has_changes(self) -> bool: ...


class GitVersionControl(VersionControl):
    """`VersionControl` backed by git. Shells out via `subprocess` with argument
    lists (never a shell string)."""

    def __init__(self, repo_dir: "str | Path", output_dir: Optional[str] = None) -> None:
        self.repo_dir = Path(repo_dir)
        self.output_dir = output_dir

    def _run(self, args: list[str]) -> str:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            raise RuntimeError(f"failed to run git {' '.join(args)}: {exc}") from exc
        if result.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout

    def _resolves(self, ref: str) -> bool:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", ref],
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def _pathspec(self) -> str:
        return self.output_dir if self.output_dir else "."

    def checkpoint(self, message: str) -> str:
        self._run(["add", "-A"])
        self._run(["commit", "--allow-empty", "-m", message])
        return self._run(["rev-parse", "HEAD"]).strip()

    def diff(self, from_id: str, to_id: str) -> str:
        # A root commit has no parent, so "<sha>~1" doesn't resolve; diff
        # against the empty tree instead of raising.
        resolved_from_id = from_id if self._resolves(from_id) else _EMPTY_TREE_SHA
        return self._run(["diff", f"{resolved_from_id}..{to_id}"])

    def restore(self, commit_id: str) -> None:
        pathspec = self._pathspec()
        self._run(["restore", f"--source={commit_id}", "--staged", "--worktree", "--", pathspec])
        self._run(["clean", "-fdq", "--", pathspec])

    def log(self, target: Optional[str] = None) -> list[str]:
        args = ["log", "--format=%H"]
        if target:
            args += ["--grep", target]
        try:
            output = self._run(args)
        except RuntimeError:
            return []
        return [line for line in output.splitlines() if line]

    def has_changes(self) -> bool:
        status = self._run(["status", "--porcelain", "--", self._pathspec()])
        return bool(status.strip())
