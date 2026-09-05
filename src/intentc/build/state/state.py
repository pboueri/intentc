"""Build state: per-target status/history via a StorageBackend, and version control."""

from __future__ import annotations

import abc
import secrets
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from intentc.build.storage import (
    BuildResult,
    BuildStep,
    SQLiteBackend,
    StorageBackend,
    TargetStatus,
)

if TYPE_CHECKING:  # pragma: no cover
    from intentc.core.project import Project

__all__ = [
    "BuildResult",
    "BuildStep",
    "GitVersionControl",
    "StateManager",
    "TargetStatus",
    "VersionControl",
    "response_file_name",
]


def response_file_name(target: str) -> str:
    """``<target with slashes as underscores>-<8 hex>.json`` — unique per invocation."""
    return f"{target.replace('/', '_')}-{secrets.token_hex(4)}.json"


# ---------------------------------------------------------------------------
# Version control
# ---------------------------------------------------------------------------


class VersionControl(abc.ABC):
    """Checkpoints file changes and produces an ID that can inspect or restore them."""

    @abc.abstractmethod
    def checkpoint(self, message: str) -> str: ...

    @abc.abstractmethod
    def diff(self, from_id: str, to_id: str) -> str: ...

    @abc.abstractmethod
    def restore(self, commit_id: str) -> None: ...

    @abc.abstractmethod
    def log(self, target: str | None = None) -> list[str]: ...

    @abc.abstractmethod
    def has_changes(self) -> bool: ...


_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


class GitVersionControl(VersionControl):
    """VersionControl backed by git commits in ``repo_dir``. Checkpoint IDs are SHAs."""

    def __init__(self, repo_dir: Path, output_dir: str | None = None) -> None:
        self._repo_dir = Path(repo_dir)
        self._output_dir = output_dir or None

    def _git(self, *args: str, check: bool = True) -> str:
        try:
            proc = subprocess.run(
                ["git", *args], cwd=str(self._repo_dir), capture_output=True, text=True
            )
        except OSError as exc:
            raise RuntimeError(f"git is not available: {exc}") from exc
        if check and proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()
            raise RuntimeError(f"git {args[0]} failed in {self._repo_dir}: {detail}")
        return proc.stdout.strip()

    def checkpoint(self, message: str) -> str:
        self._git("add", "-A")
        self._git("commit", "-m", message, "--allow-empty", "--quiet")
        return self._git("rev-parse", "HEAD")

    def _resolve_parent(self, ref: str) -> str:
        """Map ``<sha>~1`` of a root commit to the empty tree instead of failing."""
        if ref.endswith("~1"):
            parents = self._git("rev-list", "--parents", "-n", "1", ref[:-2]).split()
            if len(parents) < 2:
                return _EMPTY_TREE
        return ref

    def diff(self, from_id: str, to_id: str) -> str:
        return self._git("diff", self._resolve_parent(from_id), to_id)

    def restore(self, commit_id: str) -> None:
        """Make the output directory (or the whole tree) match ``commit_id``, removing files it lacks."""
        source = self._resolve_parent(commit_id)
        pathspec = self._output_dir or "."
        if pathspec != "." and not (self._repo_dir / pathspec).exists():
            (self._repo_dir / pathspec).mkdir(parents=True, exist_ok=True)
        self._git("restore", f"--source={source}", "--staged", "--worktree", "--", pathspec)
        # `git restore` leaves untracked files alone; generated files that were never committed
        # are exactly what a clean should remove, so drop untracked files inside the pathspec too.
        self._git("clean", "-fdq", "--", pathspec)

    def log(self, target: str | None = None) -> list[str]:
        args = ["log", "--format=%H"]
        if target:
            args += ["--grep", target]
        output = self._git(*args, check=False)
        return output.splitlines() if output else []

    def has_changes(self) -> bool:
        return bool(self._git("status", "--porcelain", "--untracked-files=all"))


# ---------------------------------------------------------------------------
# State manager
# ---------------------------------------------------------------------------


class StateManager:
    """Per-target build state for one output directory. Persistence is delegated to a backend."""

    def __init__(self, base_dir: Path, output_dir: str, backend: StorageBackend | None = None) -> None:
        self.base_dir = Path(base_dir)
        self.output_dir = output_dir
        self._backend = backend if backend is not None else SQLiteBackend(self.base_dir, output_dir)
        responses = self.base_dir / ".intentc" / "state" / output_dir.strip("/") / "responses"
        self._build_response_dir = responses / "build"
        self._val_response_dir = responses / "val"

    @property
    def backend(self) -> StorageBackend:
        return self._backend

    @property
    def build_response_dir(self) -> Path:
        self._build_response_dir.mkdir(parents=True, exist_ok=True)
        return self._build_response_dir

    @property
    def val_response_dir(self) -> Path:
        self._val_response_dir.mkdir(parents=True, exist_ok=True)
        return self._val_response_dir

    def get_status(self, target: str) -> TargetStatus:
        return self._backend.get_status(target)

    def get_build_result(self, target: str) -> BuildResult | None:
        return self._backend.get_build_result(target)

    def get_build_history(self, target: str, limit: int = 50) -> list[BuildResult]:
        return self._backend.get_build_history(target, limit)

    def save_build_result(self, target: str, result: BuildResult, git_diff: str | None = None) -> int:
        return self._backend.save_build_result(target, result, git_diff=git_diff)

    def set_status(self, target: str, status: TargetStatus) -> None:
        self._backend.set_status(target, status)

    def mark_dependents_outdated(self, target: str, project: "Project") -> list[str]:
        """Set every built descendant of ``target`` to OUTDATED. Returns the targets changed."""
        changed: list[str] = []
        for dependent in sorted(project.descendants(target)):
            if self._backend.get_status(dependent) == TargetStatus.BUILT:
                self._backend.set_status(dependent, TargetStatus.OUTDATED)
                changed.append(dependent)
        return changed

    def reset(self, target: str) -> None:
        self._backend.reset(target)

    def reset_all(self) -> None:
        self._backend.reset_all()

    def list_targets(self) -> list[tuple[str, TargetStatus]]:
        return self._backend.list_targets()
