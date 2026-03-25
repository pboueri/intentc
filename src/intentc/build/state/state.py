"""State management for intentc builds: StateManager and VersionControl."""

from __future__ import annotations

import abc
import subprocess
from pathlib import Path

from intentc.build.storage.backend import BuildResult, StorageBackend, TargetStatus
from intentc.build.storage.sqlite_backend import SQLiteBackend


class VersionControl(abc.ABC):
    """Abstract interface for checkpointing file changes."""

    @abc.abstractmethod
    def checkpoint(self, message: str) -> str:
        """Snapshot current changes, return a unique commit/checkpoint ID."""

    @abc.abstractmethod
    def diff(self, from_id: str, to_id: str) -> str:
        """Return the diff between two checkpoints."""

    @abc.abstractmethod
    def restore(self, commit_id: str) -> None:
        """Restore the output directory to the state at a given checkpoint."""

    @abc.abstractmethod
    def log(self, target: str | None = None) -> list[str]:
        """List checkpoint IDs, optionally filtered by target."""


class GitVersionControl(VersionControl):
    """Concrete VersionControl backed by git."""

    def __init__(self, repo_dir: Path) -> None:
        self._repo_dir = repo_dir

    def _run(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=str(self._repo_dir),
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    def checkpoint(self, message: str) -> str:
        self._run("add", "-A")
        self._run("commit", "-m", message, "--allow-empty")
        return self._run("rev-parse", "HEAD")

    def diff(self, from_id: str, to_id: str) -> str:
        return self._run("diff", from_id, to_id)

    def restore(self, commit_id: str) -> None:
        self._run("checkout", commit_id, "--", ".")

    def log(self, target: str | None = None) -> list[str]:
        if target:
            output = self._run("log", "--format=%H", "--grep", target)
        else:
            output = self._run("log", "--format=%H")
        if not output:
            return []
        return output.splitlines()


class StateManager:
    """Manages per-target state for a given output directory.

    Delegates all persistence to a StorageBackend.
    """

    def __init__(
        self,
        base_dir: Path,
        output_dir: str,
        backend: StorageBackend | None = None,
    ) -> None:
        self._base_dir = base_dir
        self._output_dir = output_dir
        self._backend = backend or SQLiteBackend(base_dir, output_dir)

        # Response file directories
        resp_base = base_dir / ".intentc" / "state" / output_dir / "responses"
        self._build_response_dir = resp_base / "build"
        self._val_response_dir = resp_base / "val"
        self._build_response_dir.mkdir(parents=True, exist_ok=True)
        self._val_response_dir.mkdir(parents=True, exist_ok=True)

    @property
    def build_response_dir(self) -> Path:
        return self._build_response_dir

    @property
    def val_response_dir(self) -> Path:
        return self._val_response_dir

    @property
    def backend(self) -> StorageBackend:
        return self._backend

    def get_status(self, target: str) -> TargetStatus:
        return self._backend.get_status(target)

    def get_build_result(self, target: str) -> BuildResult | None:
        return self._backend.get_build_result(target)

    def save_build_result(self, target: str, result: BuildResult) -> None:
        self._backend.save_build_result(target, result)

    def set_status(self, target: str, status: TargetStatus) -> None:
        self._backend.set_status(target, status)

    def mark_dependents_outdated(self, target: str, project: object) -> None:
        """Walk the DAG and set all descendants to outdated.

        Args:
            target: The feature path whose dependents should be marked.
            project: A Project instance with a descendants() method.
        """
        desc = project.descendants(target)  # type: ignore[union-attr]
        for dep in desc:
            self._backend.set_status(dep, TargetStatus.OUTDATED)

    def reset(self, target: str) -> None:
        self._backend.reset(target)

    def reset_all(self) -> None:
        self._backend.reset_all()

    def list_targets(self) -> list[tuple[str, TargetStatus]]:
        return self._backend.list_targets()
