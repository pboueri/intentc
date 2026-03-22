"""State management types and StateManager for intentc builds."""

from __future__ import annotations

import abc
import enum
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from intentc.core.project import Project


class TargetStatus(str, enum.Enum):
    PENDING = "pending"
    BUILT = "built"
    FAILED = "failed"
    OUTDATED = "outdated"


class BuildStep(BaseModel):
    """A single phase within a build."""

    model_config = {"extra": "ignore"}

    phase: str
    status: str  # "success" or "failure"
    duration_secs: float
    summary: str


class BuildResult(BaseModel):
    """Result of building a single target."""

    model_config = {"extra": "ignore"}

    generation_id: str
    target: str
    status: TargetStatus
    commit_id: str = ""
    total_duration_secs: float = 0.0
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    steps: list[BuildStep] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# VersionControl
# ---------------------------------------------------------------------------


class VersionControl(abc.ABC):
    """Abstract interface for checkpointing file changes."""

    @abc.abstractmethod
    def checkpoint(self, message: str) -> str:
        """Snapshot current changes, return a unique commit/checkpoint ID."""
        ...

    @abc.abstractmethod
    def diff(self, from_id: str, to_id: str) -> str:
        """Return the diff between two checkpoints."""
        ...

    @abc.abstractmethod
    def restore(self, commit_id: str) -> None:
        """Restore the output directory to the state at a given checkpoint."""
        ...

    @abc.abstractmethod
    def log(self, target: str | None = None) -> list[str]:
        """List checkpoints, optionally filtered by target."""
        ...


class GitVersionControl(VersionControl):
    """Concrete VersionControl backed by git."""

    def __init__(self, repo_dir: Path) -> None:
        self._repo_dir = Path(repo_dir)

    def _run(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=str(self._repo_dir),
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


# ---------------------------------------------------------------------------
# StateManager
# ---------------------------------------------------------------------------


class StateManager:
    """Manages per-target state for a given output directory.

    Delegates all persistence to a StorageBackend.
    """

    def __init__(
        self,
        base_dir: Path,
        output_dir: str,
        backend: "StorageBackend | None" = None,
    ) -> None:
        from intentc.build.storage import SQLiteBackend

        self._base_dir = Path(base_dir)
        self._output_dir = output_dir

        if backend is not None:
            self._backend = backend
        else:
            self._backend = SQLiteBackend(self._base_dir, output_dir)

        # Ensure response directories exist
        self.build_response_dir.mkdir(parents=True, exist_ok=True)
        self.val_response_dir.mkdir(parents=True, exist_ok=True)

    @property
    def build_response_dir(self) -> Path:
        return self._base_dir / ".intentc" / "state" / self._output_dir / "responses" / "build"

    @property
    def val_response_dir(self) -> Path:
        return self._base_dir / ".intentc" / "state" / self._output_dir / "responses" / "val"

    def get_status(self, target: str) -> TargetStatus:
        return self._backend.get_status(target)

    def get_build_result(self, target: str) -> BuildResult | None:
        return self._backend.get_build_result(target)

    def save_build_result(self, target: str, result: BuildResult) -> None:
        self._backend.save_build_result(
            target=target,
            result=result,
            intent_version_id=None,
            git_diff=None,
            files_created=None,
            files_modified=None,
        )

    def set_status(self, target: str, status: TargetStatus) -> None:
        self._backend.set_status(target, status)

    def mark_dependents_outdated(self, target: str, project: Project) -> None:
        for desc in project.descendants(target):
            current = self._backend.get_status(desc)
            if current != TargetStatus.PENDING:
                self._backend.set_status(desc, TargetStatus.OUTDATED)

    def reset(self, target: str) -> None:
        self._backend.reset(target)

    def reset_all(self) -> None:
        self._backend.reset_all()

    def list_targets(self) -> list[tuple[str, TargetStatus]]:
        return self._backend.list_targets()


# Re-export StorageBackend type for convenience (avoids circular at runtime)
if TYPE_CHECKING:
    from intentc.build.storage.backend import StorageBackend
