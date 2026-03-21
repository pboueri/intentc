"""StorageBackend ABC and GenerationStatus enum."""

from __future__ import annotations

import abc
import enum
from pathlib import Path


class GenerationStatus(str, enum.Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class StorageBackend(abc.ABC):
    """Abstract interface for persistent storage of build state, results, logs, and agent responses."""

    @abc.abstractmethod
    def __init__(self, base_dir: Path, output_dir: str) -> None: ...

    # -- Generation methods ------------------------------------------------

    @abc.abstractmethod
    def create_generation(
        self,
        generation_id: str,
        output_dir: str,
        profile_name: str | None,
        options: dict | None,
    ) -> None: ...

    @abc.abstractmethod
    def complete_generation(self, generation_id: str, status: GenerationStatus) -> None: ...

    @abc.abstractmethod
    def log_generation_event(self, generation_id: str, message: str) -> None: ...

    @abc.abstractmethod
    def get_generation(self, generation_id: str) -> dict | None: ...

    # -- Intent/Validation file version methods ----------------------------

    @abc.abstractmethod
    def record_intent_version(self, name: str, source_path: str, content_hash: str) -> int: ...

    @abc.abstractmethod
    def record_validation_version(self, target: str, source_path: str, content_hash: str) -> int: ...

    # -- Build result methods ----------------------------------------------

    @abc.abstractmethod
    def save_build_result(
        self,
        target: str,
        result_dict: dict,
        generation_id: str,
        intent_version_id: int | None,
        git_diff: str | None,
        files_created: list[str] | None,
        files_modified: list[str] | None,
    ) -> int: ...

    @abc.abstractmethod
    def get_build_result(self, target: str) -> dict | None: ...

    @abc.abstractmethod
    def get_build_history(self, target: str, limit: int = 50) -> list[dict]: ...

    # -- Build step methods ------------------------------------------------

    @abc.abstractmethod
    def save_build_step(
        self, build_result_id: int, step_dict: dict, log: str | None, step_order: int
    ) -> None: ...

    # -- Validation result methods -----------------------------------------

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

    # -- Agent response methods --------------------------------------------

    @abc.abstractmethod
    def save_agent_response(
        self,
        build_result_id: int | None,
        validation_result_id: int | None,
        response_type: str,
        response_json: dict,
    ) -> None: ...

    # -- Target state methods ----------------------------------------------

    @abc.abstractmethod
    def get_status(self, target: str) -> str: ...

    @abc.abstractmethod
    def set_status(self, target: str, status: str) -> None: ...

    @abc.abstractmethod
    def list_targets(self) -> list[tuple[str, str]]: ...

    @abc.abstractmethod
    def reset(self, target: str) -> None: ...

    @abc.abstractmethod
    def reset_all(self) -> None: ...
