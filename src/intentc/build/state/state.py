"""Per-target build state, backed by a `StorageBackend`, and version control for
checkpointing generated output.

`TargetStatus`, `BuildStep`, and `BuildResult` are defined in
`intentc.build.storage` (storage is upstream of state) and re-exported here
unchanged.
"""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

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

    @abstractmethod
    def snapshot(self, message: str, ref_name: str) -> str: ...

    @abstractmethod
    def materialize(self, commit_id: str, dest_dir: "str | Path") -> None: ...


class GitVersionControl(VersionControl):
    """`VersionControl` backed by git. Shells out via `subprocess` with argument
    lists (never a shell string)."""

    def __init__(self, repo_dir: "str | Path", output_dir: Optional[str] = None) -> None:
        self.repo_dir = Path(repo_dir)
        self.output_dir = output_dir

    def _run(self, args: list[str], env: Optional[dict[str, str]] = None) -> str:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
                env=env,
            )
        except OSError as exc:
            raise RuntimeError(f"failed to run git {' '.join(args)}: {exc}") from exc
        if result.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout

    def _resolve_head(self) -> Optional[str]:
        try:
            return self._run(["rev-parse", "HEAD"]).strip()
        except RuntimeError:
            return None

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

    def snapshot(self, message: str, ref_name: str) -> str:
        """Commit the current working tree to a side ref without moving HEAD
        or the branch. Stages into a temporary index so the user's real index
        is never disturbed."""
        pathspec = self._pathspec()
        parent = self._resolve_head()
        with tempfile.TemporaryDirectory() as tmp_dir:
            index_path = Path(tmp_dir) / "index"
            env = dict(os.environ)
            env["GIT_INDEX_FILE"] = str(index_path)
            if parent is not None:
                self._run(["read-tree", parent], env=env)
            self._run(["add", "-A", "--", pathspec], env=env)
            tree = self._run(["write-tree"], env=env).strip()
        commit_args = ["commit-tree", tree, "-m", message]
        if parent is not None:
            commit_args += ["-p", parent]
        commit_id = self._run(commit_args).strip()
        self._run(["update-ref", ref_name, commit_id])
        return commit_id

    def materialize(self, commit_id: str, dest_dir: "str | Path") -> None:
        """Extract `commit_id`'s output directory into `dest_dir` (as
        `dest_dir/<output_dir>/...`) via `git archive | tar -x`."""
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        pathspec = self._pathspec()
        try:
            archive_proc = subprocess.Popen(
                ["git", "archive", commit_id, "--", pathspec],
                cwd=self.repo_dir,
                stdout=subprocess.PIPE,
            )
            extract_proc = subprocess.Popen(
                ["tar", "-x", "-C", str(dest)],
                stdin=archive_proc.stdout,
            )
            if archive_proc.stdout is not None:
                archive_proc.stdout.close()
            extract_proc.communicate()
            archive_proc.wait()
        except OSError as exc:
            raise RuntimeError(f"failed to materialize commit {commit_id}: {exc}") from exc
        if archive_proc.returncode != 0 or extract_proc.returncode != 0:
            raise RuntimeError(f"failed to materialize commit {commit_id} into {dest_dir}")
