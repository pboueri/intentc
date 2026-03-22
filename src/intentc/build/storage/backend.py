from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import Any


class GenerationStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class StorageBackend(ABC):
    """Abstract interface for persisting build state.

    All persistence goes through this interface. No code outside the storage
    module should know which database engine is being used.

    The backend is scoped to a single output directory, set at construction.
    """

    def __init__(self, base_dir: Path, output_dir: str) -> None:
        self.base_dir = base_dir
        self.output_dir = output_dir

    # -- Generation methods --------------------------------------------------

    @abstractmethod
    def create_generation(
        self,
        generation_id: str,
        output_dir: str,
        profile_name: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> None:
        """Insert a new generation row with status ``running``."""

    @abstractmethod
    def complete_generation(
        self, generation_id: str, status: GenerationStatus
    ) -> None:
        """Update generation status to ``completed`` or ``failed``."""

    @abstractmethod
    def log_generation_event(self, generation_id: str, message: str) -> None:
        """Append a log entry to the generation."""

    @abstractmethod
    def get_generation(self, generation_id: str) -> dict[str, Any] | None:
        """Retrieve generation metadata and logs."""

    # -- Intent / validation file version methods ----------------------------

    @abstractmethod
    def record_intent_version(
        self, name: str, source_path: str, content_hash: str
    ) -> int:
        """Record a unique version of an intent file by content hash.

        Idempotent: returns existing ID if hash already recorded.
        """

    @abstractmethod
    def record_validation_version(
        self, target: str, source_path: str, content_hash: str
    ) -> int:
        """Record a unique version of a validation file by content hash.

        Idempotent: returns existing ID if hash already recorded.
        """

    # -- Build result methods ------------------------------------------------

    @abstractmethod
    def save_build_result(
        self,
        target: str,
        result: Any,
        intent_version_id: int | None = None,
        git_diff: str | None = None,
        files_created: list[str] | None = None,
        files_modified: list[str] | None = None,
    ) -> int:
        """Insert a build result and its steps. Returns the build_result ID."""

    @abstractmethod
    def get_build_result(self, target: str) -> Any | None:
        """Get the latest build result for a target (via ``target_state``)."""

    @abstractmethod
    def get_build_history(self, target: str, limit: int = 50) -> list[Any]:
        """All build results for a target, newest first."""

    # -- Build step methods --------------------------------------------------

    @abstractmethod
    def save_build_step(
        self,
        build_result_id: int,
        step: Any,
        log: str,
        step_order: int,
    ) -> None:
        """Insert a build step with its log output."""

    # -- Validation result methods -------------------------------------------

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
        reason: str,
        duration_secs: float | None = None,
    ) -> int:
        """Insert a validation result and return its ID."""

    # -- Agent response methods ----------------------------------------------

    @abstractmethod
    def save_agent_response(
        self,
        build_result_id: int | None,
        validation_result_id: int | None,
        response_type: str,
        response_json: dict[str, Any],
    ) -> None:
        """Store a raw agent JSON response for audit and debugging."""

    # -- Target state methods ------------------------------------------------

    @abstractmethod
    def get_status(self, target: str) -> str:
        """Current status string; ``'pending'`` if unknown."""

    @abstractmethod
    def set_status(self, target: str, status: str) -> None:
        """Update the current status for a target."""

    @abstractmethod
    def list_targets(self) -> list[tuple[str, str]]:
        """All tracked targets as ``(target, status)`` pairs."""

    @abstractmethod
    def reset(self, target: str) -> None:
        """Remove the target state entry for a single target."""

    @abstractmethod
    def reset_all(self) -> None:
        """Remove all target state entries for this output directory."""
