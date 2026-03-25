"""Storage backend interface and shared types for intentc build state."""

from __future__ import annotations

import abc
import enum
from pathlib import Path
from typing import Any


class GenerationStatus(str, enum.Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TargetStatus(str, enum.Enum):
    PENDING = "pending"
    BUILDING = "building"
    BUILT = "built"
    FAILED = "failed"
    OUTDATED = "outdated"


class BuildStep:
    """A single phase within a build result."""

    def __init__(
        self,
        phase: str,
        status: str,
        duration_secs: float = 0.0,
        summary: str = "",
    ) -> None:
        self.phase = phase
        self.status = status
        self.duration_secs = duration_secs
        self.summary = summary


class BuildResult:
    """Result of building a single target."""

    def __init__(
        self,
        target: str,
        generation_id: str | None = None,
        status: str = "success",
        commit_id: str = "",
        total_duration_secs: float = 0.0,
        timestamp: str = "",
        steps: list[BuildStep] | None = None,
    ) -> None:
        self.target = target
        self.generation_id = generation_id
        self.status = status
        self.commit_id = commit_id
        self.total_duration_secs = total_duration_secs
        self.timestamp = timestamp
        self.steps: list[BuildStep] = steps or []


class StorageBackend(abc.ABC):
    """Abstract interface for persisting build state.

    All methods follow snake_case naming. The backend is scoped to a single
    output directory (set at construction).
    """

    def __init__(self, base_dir: Path, output_dir: str) -> None:
        self.base_dir = base_dir
        self.output_dir = output_dir

    # -- Generation methods --------------------------------------------------

    @abc.abstractmethod
    def create_generation(
        self,
        generation_id: str,
        output_dir: str,
        profile_name: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> None: ...

    @abc.abstractmethod
    def complete_generation(
        self, generation_id: str, status: GenerationStatus
    ) -> None: ...

    @abc.abstractmethod
    def log_generation_event(
        self, generation_id: str, message: str
    ) -> None: ...

    @abc.abstractmethod
    def get_generation(self, generation_id: str) -> dict[str, Any] | None: ...

    # -- Intent / validation file version methods ----------------------------

    @abc.abstractmethod
    def record_intent_version(
        self, name: str, source_path: str, content_hash: str
    ) -> int: ...

    @abc.abstractmethod
    def record_validation_version(
        self, target: str, source_path: str, content_hash: str
    ) -> int: ...

    # -- Build result methods ------------------------------------------------

    @abc.abstractmethod
    def save_build_result(
        self,
        target: str,
        result: BuildResult,
        intent_version_id: int | None = None,
        git_diff: str | None = None,
        files_created: list[str] | None = None,
        files_modified: list[str] | None = None,
    ) -> int: ...

    @abc.abstractmethod
    def get_build_result(self, target: str) -> BuildResult | None: ...

    @abc.abstractmethod
    def get_build_history(
        self, target: str, limit: int = 50
    ) -> list[BuildResult]: ...

    # -- Build step methods --------------------------------------------------

    @abc.abstractmethod
    def save_build_step(
        self,
        build_result_id: int,
        step: BuildStep,
        log: str,
        step_order: int,
    ) -> None: ...

    # -- Validation result methods -------------------------------------------

    @abc.abstractmethod
    def save_validation_result(
        self,
        build_result_id: int | None,
        generation_id: str,
        target: str,
        validation_file_version_id: int | None,
        name: str,
        type: str,
        severity: str,
        status: str,
        reason: str = "",
        duration_secs: float | None = None,
    ) -> int: ...

    # -- Agent response methods ----------------------------------------------

    @abc.abstractmethod
    def save_agent_response(
        self,
        build_result_id: int | None,
        validation_result_id: int | None,
        response_type: str,
        response_json: dict[str, Any],
    ) -> None: ...

    # -- Target state methods ------------------------------------------------

    @abc.abstractmethod
    def get_status(self, target: str) -> TargetStatus: ...

    @abc.abstractmethod
    def set_status(self, target: str, status: TargetStatus) -> None: ...

    @abc.abstractmethod
    def list_targets(self) -> list[tuple[str, TargetStatus]]: ...

    @abc.abstractmethod
    def reset(self, target: str) -> None: ...

    @abc.abstractmethod
    def reset_all(self) -> None: ...
