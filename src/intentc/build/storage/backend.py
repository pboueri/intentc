"""StorageBackend abstract interface and GenerationStatus enum."""

from __future__ import annotations

import abc
import enum
from pathlib import Path
from typing import Any

from intentc.build.state import BuildResult, BuildStep, TargetStatus


class GenerationStatus(str, enum.Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class StorageBackend(abc.ABC):
    """Abstract interface for persistent build storage.

    Scoped to a single output directory set at construction.
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
        profile_name: str | None,
        options: dict[str, Any] | None,
    ) -> None: ...

    @abc.abstractmethod
    def complete_generation(
        self, generation_id: str, status: GenerationStatus
    ) -> None: ...

    @abc.abstractmethod
    def log_generation_event(self, generation_id: str, message: str) -> None: ...

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
        intent_version_id: int | None,
        git_diff: str | None,
        files_created: list[str] | None,
        files_modified: list[str] | None,
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
        reason: str,
        duration_secs: float | None,
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
