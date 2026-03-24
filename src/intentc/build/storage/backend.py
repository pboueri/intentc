"""Storage backend interface and shared types."""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import BaseModel, Field


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


class BuildStep(BaseModel):
    phase: str
    status: str
    duration_secs: float = 0.0
    summary: str = ""


class BuildResult(BaseModel):
    target: str
    generation_id: str = ""
    status: str = ""
    commit_id: str = ""
    total_duration_secs: float = 0.0
    timestamp: str = ""
    git_diff: str | None = None
    files_created: list[str] = Field(default_factory=list)
    files_modified: list[str] = Field(default_factory=list)
    steps: list[BuildStep] = Field(default_factory=list)


class StorageBackend(ABC):
    """Abstract interface for all build state persistence."""

    def __init__(self, base_dir: Path, output_dir: str) -> None:
        self.base_dir = base_dir
        self.output_dir = output_dir

    # --- Generation methods ---

    @abstractmethod
    def create_generation(
        self,
        generation_id: str,
        output_dir: str,
        profile_name: str | None = None,
        options: dict | None = None,
    ) -> None: ...

    @abstractmethod
    def complete_generation(
        self, generation_id: str, status: GenerationStatus
    ) -> None: ...

    @abstractmethod
    def log_generation_event(self, generation_id: str, message: str) -> None: ...

    @abstractmethod
    def get_generation(self, generation_id: str) -> dict | None: ...

    # --- Intent / validation file version methods ---

    @abstractmethod
    def record_intent_version(
        self, name: str, source_path: str, content_hash: str
    ) -> int: ...

    @abstractmethod
    def record_validation_version(
        self, target: str, source_path: str, content_hash: str
    ) -> int: ...

    # --- Build result methods ---

    @abstractmethod
    def save_build_result(
        self,
        target: str,
        result: BuildResult,
        intent_version_id: int | None = None,
        git_diff: str | None = None,
        files_created: list[str] | None = None,
        files_modified: list[str] | None = None,
    ) -> int: ...

    @abstractmethod
    def get_build_result(self, target: str) -> BuildResult | None: ...

    @abstractmethod
    def get_build_history(
        self, target: str, limit: int = 50
    ) -> list[BuildResult]: ...

    # --- Build step methods ---

    @abstractmethod
    def save_build_step(
        self,
        build_result_id: int,
        step: BuildStep,
        log: str,
        step_order: int,
    ) -> None: ...

    # --- Validation result methods ---

    @abstractmethod
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

    # --- Agent response methods ---

    @abstractmethod
    def save_agent_response(
        self,
        build_result_id: int | None,
        validation_result_id: int | None,
        response_type: str,
        response_json: dict,
    ) -> None: ...

    # --- Target state methods ---

    @abstractmethod
    def get_status(self, target: str) -> TargetStatus: ...

    @abstractmethod
    def set_status(self, target: str, status: TargetStatus) -> None: ...

    @abstractmethod
    def list_targets(self) -> list[tuple[str, TargetStatus]]: ...

    @abstractmethod
    def reset(self, target: str) -> None: ...

    @abstractmethod
    def reset_all(self) -> None: ...
