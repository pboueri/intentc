from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from intentc.build.storage import SQLiteBackend, StorageBackend

if TYPE_CHECKING:
    from intentc.core.project import Project


class TargetStatus(str, Enum):
    PENDING = "pending"
    BUILT = "built"
    FAILED = "failed"
    OUTDATED = "outdated"


class BuildStep(BaseModel):
    phase: str
    status: str
    duration: timedelta = Field(default_factory=lambda: timedelta(0))
    summary: str = ""


class BuildResult(BaseModel):
    target: str
    generation_id: str
    status: TargetStatus
    steps: list[BuildStep] = Field(default_factory=list)
    commit_id: str = ""
    total_duration: timedelta = Field(default_factory=lambda: timedelta(0))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def _build_result_from_row(row: dict) -> BuildResult:
    steps = []
    for s in row.get("steps", []):
        steps.append(BuildStep(
            phase=s.get("phase", ""),
            status=s.get("status", ""),
            duration=timedelta(seconds=s.get("duration_secs", 0.0)),
            summary=s.get("summary", ""),
        ))
    ts_raw = row.get("timestamp")
    if isinstance(ts_raw, str):
        timestamp = datetime.fromisoformat(ts_raw)
    else:
        timestamp = datetime.now(timezone.utc)
    status_str = row.get("status", "pending")
    return BuildResult(
        target=row.get("target", ""),
        generation_id=row.get("generation_id", ""),
        status=TargetStatus(status_str),
        steps=steps,
        commit_id=row.get("commit_id", ""),
        total_duration=timedelta(seconds=row.get("total_duration_secs", 0.0)),
        timestamp=timestamp,
    )


class StateManager:
    def __init__(
        self,
        base_dir: Path,
        output_dir: str,
        backend: StorageBackend | None = None,
    ) -> None:
        self._base_dir = Path(base_dir)
        self._output_dir = output_dir
        self._backend = backend or SQLiteBackend(self._base_dir, output_dir)

        self._response_base = (
            self._base_dir / ".intentc" / "state" / output_dir / "responses"
        )
        self._build_response_dir = self._response_base / "build"
        self._val_response_dir = self._response_base / "val"
        self._build_response_dir.mkdir(parents=True, exist_ok=True)
        self._val_response_dir.mkdir(parents=True, exist_ok=True)

    @property
    def build_response_dir(self) -> Path:
        return self._build_response_dir

    @property
    def val_response_dir(self) -> Path:
        return self._val_response_dir

    def get_status(self, target: str) -> TargetStatus:
        raw = self._backend.get_status(target)
        return TargetStatus(raw)

    def get_build_result(self, target: str) -> BuildResult | None:
        row = self._backend.get_build_result(target)
        if row is None:
            return None
        return _build_result_from_row(row)

    def save_build_result(self, target: str, result: BuildResult) -> None:
        self._backend.save_build_result(target, result)

    def set_status(self, target: str, status: TargetStatus) -> None:
        self._backend.set_status(target, status.value)

    def mark_dependents_outdated(self, target: str, project: Project) -> None:
        for desc in project.descendants(target):
            self._backend.set_status(desc, TargetStatus.OUTDATED.value)

    def reset(self, target: str) -> None:
        self._backend.reset(target)

    def reset_all(self) -> None:
        self._backend.reset_all()

    def list_targets(self) -> list[tuple[str, TargetStatus]]:
        raw = self._backend.list_targets()
        return [(t, TargetStatus(s)) for t, s in raw]


class VersionControl(ABC):
    @abstractmethod
    def checkpoint(self, message: str) -> str: ...

    @abstractmethod
    def diff(self, from_id: str, to_id: str) -> str: ...

    @abstractmethod
    def restore(self, commit_id: str) -> None: ...

    @abstractmethod
    def log(self, target: str | None = None) -> list[str]: ...


class GitVersionControl(VersionControl):
    def __init__(self, repo_dir: Path) -> None:
        self._repo_dir = Path(repo_dir)

    def checkpoint(self, message: str) -> str:
        subprocess.run(
            ["git", "add", "-A"],
            cwd=self._repo_dir,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", message, "--allow-empty"],
            cwd=self._repo_dir,
            check=True,
            capture_output=True,
        )
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self._repo_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def diff(self, from_id: str, to_id: str) -> str:
        result = subprocess.run(
            ["git", "diff", from_id, to_id],
            cwd=self._repo_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout

    def restore(self, commit_id: str) -> None:
        subprocess.run(
            ["git", "checkout", commit_id, "--", "."],
            cwd=self._repo_dir,
            check=True,
            capture_output=True,
        )

    def log(self, target: str | None = None) -> list[str]:
        cmd = ["git", "log", "--format=%H"]
        if target:
            cmd.extend(["--grep", target])
        result = subprocess.run(
            cmd,
            cwd=self._repo_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        lines = result.stdout.strip().split("\n")
        return [line for line in lines if line]
