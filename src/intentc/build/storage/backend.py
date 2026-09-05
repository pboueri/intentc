"""StorageBackend interface plus the record types shared by state and storage."""

from __future__ import annotations

import abc
import enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class GenerationStatus(str, enum.Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TargetStatus(str, enum.Enum):
    PENDING = "pending"
    BUILT = "built"
    FAILED = "failed"
    OUTDATED = "outdated"


class BuildStep(BaseModel):
    """One phase within a build: what ran, whether it worked, how long it took."""

    phase: str
    status: str  # "success" or "failure"
    duration_secs: float = 0.0
    summary: str = ""


class BuildResult(BaseModel):
    """The outcome of building a single target."""

    model_config = ConfigDict(validate_assignment=True)

    target: str
    generation_id: str = ""
    status: TargetStatus = TargetStatus.BUILT
    steps: list[BuildStep] = Field(default_factory=list)
    commit_id: str = ""
    total_duration_secs: float = 0.0
    timestamp: str = ""
    source_hash: str = ""
    files_created: list[str] = Field(default_factory=list)
    files_modified: list[str] = Field(default_factory=list)
    attempts: int = 1

    @field_validator("status", mode="before")
    @classmethod
    def _coerce_status(cls, value: Any) -> Any:
        if isinstance(value, str) and not isinstance(value, TargetStatus):
            return TargetStatus(value)
        return value


class StorageBackend(abc.ABC):
    """Abstract persistence for build state. Scoped to one output directory."""

    def __init__(self, base_dir: Path, output_dir: str) -> None:
        self.base_dir = Path(base_dir)
        self.output_dir = output_dir

    # -- generations ---------------------------------------------------------

    @abc.abstractmethod
    def create_generation(
        self,
        generation_id: str,
        output_dir: str,
        profile_name: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> None: ...

    @abc.abstractmethod
    def complete_generation(self, generation_id: str, status: GenerationStatus) -> None: ...

    @abc.abstractmethod
    def log_generation_event(self, generation_id: str, message: str) -> None: ...

    @abc.abstractmethod
    def get_generation(self, generation_id: str) -> dict[str, Any] | None: ...

    # -- file versions -------------------------------------------------------

    @abc.abstractmethod
    def record_intent_version(self, name: str, source_path: str, content_hash: str) -> int: ...

    @abc.abstractmethod
    def record_validation_version(self, target: str, source_path: str, content_hash: str) -> int: ...

    # -- build results -------------------------------------------------------

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
    def get_build_history(self, target: str, limit: int = 50) -> list[BuildResult]: ...

    @abc.abstractmethod
    def get_build_diff(self, target: str) -> str | None: ...

    @abc.abstractmethod
    def get_validation_results(
        self, target: str, build_result_id: int | None = None
    ) -> list[dict[str, Any]]: ...

    # -- build steps ---------------------------------------------------------

    @abc.abstractmethod
    def save_build_step(self, build_result_id: int, step: BuildStep, log: str, step_order: int) -> None: ...

    # -- validation results --------------------------------------------------

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

    # -- agent responses -----------------------------------------------------

    @abc.abstractmethod
    def save_agent_response(
        self,
        build_result_id: int | None,
        validation_result_id: int | None,
        response_type: str,
        response_json: dict[str, Any],
    ) -> None: ...

    # -- target state --------------------------------------------------------

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

    def close(self) -> None:  # pragma: no cover - default no-op
        """Release resources. Backends with connections override this."""
