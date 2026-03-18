"""Build state management — tracks status and history of each target across builds."""

from __future__ import annotations

import abc
import enum
import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel

from intentc.core.project import Project


class TargetStatus(str, enum.Enum):
    """Possible states for a build target."""

    PENDING = "pending"
    BUILT = "built"
    FAILED = "failed"
    OUTDATED = "outdated"


class BuildStep(BaseModel):
    """A single phase within a build."""

    model_config = {"extra": "ignore"}

    phase: str
    status: str  # "success" or "failure"
    duration: timedelta
    summary: str


class BuildResult(BaseModel):
    """The outcome of building a single target."""

    model_config = {"extra": "ignore"}

    target: str
    generation_id: str
    status: TargetStatus
    steps: list[BuildStep] = []
    commit_id: str = ""
    total_duration: timedelta = timedelta()
    timestamp: datetime


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _state_dir(base_dir: Path, output_dir: str) -> Path:
    return base_dir / ".intentc" / "state" / output_dir


def _serialize_state(targets: dict[str, _TargetEntry]) -> dict:
    """Convert internal state to JSON-serializable dict."""
    return {
        "version": 1,
        "targets": {
            name: {
                "status": entry.status.value,
                "build_result": _result_to_dict(entry.build_result)
                if entry.build_result
                else None,
            }
            for name, entry in targets.items()
        },
    }


def _result_to_dict(r: BuildResult) -> dict:
    return {
        "target": r.target,
        "generation_id": r.generation_id,
        "status": r.status.value,
        "steps": [
            {
                "phase": s.phase,
                "status": s.status,
                "duration": s.duration.total_seconds(),
                "summary": s.summary,
            }
            for s in r.steps
        ],
        "commit_id": r.commit_id,
        "total_duration": r.total_duration.total_seconds(),
        "timestamp": r.timestamp.isoformat(),
    }


def _result_from_dict(d: dict) -> BuildResult:
    return BuildResult(
        target=d["target"],
        generation_id=d["generation_id"],
        status=TargetStatus(d["status"]),
        steps=[
            BuildStep(
                phase=s["phase"],
                status=s["status"],
                duration=timedelta(seconds=s["duration"]),
                summary=s["summary"],
            )
            for s in d.get("steps", [])
        ],
        commit_id=d.get("commit_id", ""),
        total_duration=timedelta(seconds=d.get("total_duration", 0)),
        timestamp=datetime.fromisoformat(d["timestamp"]),
    )


class _TargetEntry:
    """Internal bookkeeping for a single target."""

    __slots__ = ("status", "build_result")

    def __init__(
        self,
        status: TargetStatus = TargetStatus.PENDING,
        build_result: BuildResult | None = None,
    ):
        self.status = status
        self.build_result = build_result


# ---------------------------------------------------------------------------
# StateManager
# ---------------------------------------------------------------------------


class StateManager:
    """Manages per-target state for a given output directory.

    State is persisted as versioned JSON at
    ``.intentc/state/{output_dir}/state.json``.  An append-only build log
    lives alongside at ``build-log.jsonl``.
    """

    def __init__(self, base_dir: Path, output_dir: str) -> None:
        self._dir = _state_dir(base_dir, output_dir)
        self._state_path = self._dir / "state.json"
        self._log_path = self._dir / "build-log.jsonl"
        self._targets: dict[str, _TargetEntry] = {}
        self._load()

    # -- public API --------------------------------------------------------

    def get_status(self, target: str) -> TargetStatus:
        """Return current status; ``pending`` if unknown."""
        entry = self._targets.get(target)
        return entry.status if entry else TargetStatus.PENDING

    def get_build_result(self, target: str) -> BuildResult | None:
        """Last build result, or ``None`` if never built."""
        entry = self._targets.get(target)
        return entry.build_result if entry else None

    def save_build_result(self, target: str, result: BuildResult) -> None:
        """Persist a build result, update status, and append to the build log."""
        entry = self._targets.get(target)
        if entry is None:
            entry = _TargetEntry()
            self._targets[target] = entry
        entry.status = result.status
        entry.build_result = result
        self._save()
        self._append_log(result)

    def set_status(self, target: str, status: TargetStatus) -> None:
        """Override the status for a target."""
        entry = self._targets.get(target)
        if entry is None:
            entry = _TargetEntry()
            self._targets[target] = entry
        entry.status = status
        self._save()

    def mark_dependents_outdated(self, target: str, project: Project) -> None:
        """Walk the project DAG and set all descendants to ``outdated``."""
        for desc in project.descendants(target):
            self.set_status(desc, TargetStatus.OUTDATED)

    def reset(self, target: str) -> None:
        """Clear all state for a single target."""
        self._targets.pop(target, None)
        self._save()

    def reset_all(self) -> None:
        """Clear all state for this output directory."""
        self._targets.clear()
        self._save()

    def list_targets(self) -> list[tuple[str, TargetStatus]]:
        """All tracked targets and their statuses."""
        return [(name, entry.status) for name, entry in sorted(self._targets.items())]

    @property
    def build_response_dir(self) -> Path:
        """Directory for build response files."""
        return self._dir / "responses" / "build"

    @property
    def val_response_dir(self) -> Path:
        """Directory for validation response files."""
        return self._dir / "responses" / "val"

    # -- persistence -------------------------------------------------------

    def _load(self) -> None:
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text())
        except (json.JSONDecodeError, OSError):
            return
        for name, entry_data in data.get("targets", {}).items():
            br = None
            if entry_data.get("build_result"):
                br = _result_from_dict(entry_data["build_result"])
            self._targets[name] = _TargetEntry(
                status=TargetStatus(entry_data["status"]),
                build_result=br,
            )

    def _save(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(
            json.dumps(_serialize_state(self._targets), indent=2) + "\n"
        )

    def _append_log(self, result: BuildResult) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        with self._log_path.open("a") as f:
            f.write(json.dumps(_result_to_dict(result)) + "\n")


# ---------------------------------------------------------------------------
# VersionControl
# ---------------------------------------------------------------------------


class VersionControl(abc.ABC):
    """Abstract interface for checkpointing file changes."""

    @abc.abstractmethod
    def checkpoint(self, message: str) -> str:
        """Snapshot current changes and return a unique checkpoint ID."""

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
    """Git-backed version control. Uses commits; checkpoint ID is the git SHA."""

    def __init__(self, repo_dir: Path) -> None:
        self._repo_dir = repo_dir

    def checkpoint(self, message: str) -> str:
        self._git("add", "-A")
        self._git("commit", "-m", message, "--allow-empty")
        return self._git("rev-parse", "HEAD").strip()

    def diff(self, from_id: str, to_id: str) -> str:
        return self._git("diff", from_id, to_id)

    def restore(self, commit_id: str) -> None:
        self._git("checkout", commit_id, "--", ".")

    def log(self, target: str | None = None) -> list[str]:
        args = ["log", "--format=%H"]
        if target:
            args.extend(["--grep", target])
        output = self._git(*args)
        return [line for line in output.strip().splitlines() if line]

    def _git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self._repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout
