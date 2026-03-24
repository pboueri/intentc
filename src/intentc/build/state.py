"""Build state management — tracks status and history of each target across builds."""

from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from pathlib import Path

from intentc.build.storage.backend import (
    BuildResult,
    StorageBackend,
    TargetStatus,
)
from intentc.build.storage.sqlite_backend import SQLiteBackend


class VersionControl(ABC):
    """Abstract interface for checkpointing file changes."""

    @abstractmethod
    def checkpoint(self, message: str) -> str:
        """Snapshot current changes, return a unique commit/checkpoint ID."""
        ...

    @abstractmethod
    def diff(self, from_id: str, to_id: str) -> str:
        """Return the diff between two checkpoints."""
        ...

    @abstractmethod
    def restore(self, commit_id: str) -> None:
        """Restore the output directory to the state at a given checkpoint."""
        ...

    @abstractmethod
    def log(self, target: str | None = None) -> list[str]:
        """List checkpoint IDs, optionally filtered by target."""
        ...


class GitVersionControl(VersionControl):
    """Concrete VersionControl backed by git."""

    def __init__(self, repo_dir: Path) -> None:
        self._repo_dir = Path(repo_dir)

    def _run(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self._repo_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git {args[0]} failed: {result.stderr.strip()}")
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
        self._base_dir = Path(base_dir)
        self._output_dir = output_dir
        self._backend = backend or SQLiteBackend(self._base_dir, output_dir)

    @property
    def backend(self) -> StorageBackend:
        return self._backend

    @property
    def build_response_dir(self) -> Path:
        """Staging area for build agent response files."""
        d = self._base_dir / ".intentc" / "state" / self._output_dir / "responses" / "build"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def val_response_dir(self) -> Path:
        """Staging area for validation agent response files."""
        d = self._base_dir / ".intentc" / "state" / self._output_dir / "responses" / "val"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def get_status(self, target: str) -> TargetStatus:
        """Returns current status; PENDING if unknown."""
        return self._backend.get_status(target)

    def get_build_result(self, target: str) -> BuildResult | None:
        """Last build result, None if never built."""
        return self._backend.get_build_result(target)

    def save_build_result(self, target: str, result: BuildResult) -> int:
        """Persist result and update status."""
        return self._backend.save_build_result(target, result)

    def set_status(self, target: str, status: TargetStatus) -> None:
        """Override status (e.g. mark outdated)."""
        self._backend.set_status(target, status)

    def mark_dependents_outdated(self, target: str, project: object) -> None:
        """Walk the DAG and set all descendants to OUTDATED.

        Args:
            target: The feature path whose dependents should be marked.
            project: A Project instance with a descendants() method.
        """
        descendants = project.descendants(target)  # type: ignore[union-attr]
        for dep in descendants:
            self._backend.set_status(dep, TargetStatus.OUTDATED)

    def reset(self, target: str) -> None:
        """Clear all state for a target."""
        self._backend.reset(target)

    def reset_all(self) -> None:
        """Clear all state for the output directory."""
        self._backend.reset_all()

    def list_targets(self) -> list[tuple[str, TargetStatus]]:
        """All tracked targets with their status."""
        return self._backend.list_targets()
