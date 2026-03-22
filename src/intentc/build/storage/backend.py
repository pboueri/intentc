"""StorageBackend abstract interface and GenerationStatus enum."""

from __future__ import annotations

import abc
import enum
from pathlib import Path
from typing import Any

from intentc.build.state import BuildResult, BuildStep, TargetStatus


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class GenerationStatus(str, enum.Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------


class StorageBackend(abc.ABC):
    """Backend-agnostic persistence interface for build state.

    All methods use Python-native types. No backend-specific types leak
    through this interface.
    """

    def __init__(self, base_dir: Path, output_dir: str) -> None:
        self.base_dir = base_dir
        self.output_dir = output_dir

    # -- Generation ----------------------------------------------------------

    @abc.abstractmethod
    def create_generation(
        self,
        generation_id: str,
        output_dir: str,
        profile_name: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> None:
        """Insert a new generation row with status ``running``."""

    @abc.abstractmethod
    def complete_generation(
        self, generation_id: str, status: GenerationStatus
    ) -> None:
        """Update generation status to ``completed`` or ``failed``."""

    @abc.abstractmethod
    def log_generation_event(self, generation_id: str, message: str) -> None:
        """Append a log entry to the generation."""

    @abc.abstractmethod
    def get_generation(self, generation_id: str) -> dict[str, Any] | None:
        """Retrieve generation metadata and logs."""

    # -- Intent / validation file versions -----------------------------------

    @abc.abstractmethod
    def record_intent_version(
        self, name: str, source_path: str, content_hash: str
    ) -> int:
        """Insert if new hash, return the version ID. Idempotent."""

    @abc.abstractmethod
    def record_validation_version(
        self, target: str, source_path: str, content_hash: str
    ) -> int:
        """Insert if new hash, return the version ID. Idempotent."""

    # -- Build results -------------------------------------------------------

    @abc.abstractmethod
    def save_build_result(
        self,
        target: str,
        result: BuildResult,
        intent_version_id: int | None = None,
        git_diff: str | None = None,
        files_created: list[str] | None = None,
        files_modified: list[str] | None = None,
    ) -> int:
        """Insert build result and its steps. Returns the build_result ID."""

    @abc.abstractmethod
    def get_build_result(self, target: str) -> BuildResult | None:
        """Get the latest build result for a target."""

    @abc.abstractmethod
    def get_build_history(
        self, target: str, limit: int = 50
    ) -> list[BuildResult]:
        """All build results for a target, newest first."""

    # -- Build steps ---------------------------------------------------------

    @abc.abstractmethod
    def save_build_step(
        self,
        build_result_id: int,
        step: BuildStep,
        log: str,
        step_order: int,
    ) -> None:
        """Insert a build step with its log output."""

    # -- Validation results --------------------------------------------------

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
        duration_secs: float | None = None,
    ) -> int:
        """Insert a validation result and return the ID."""

    # -- Agent responses -----------------------------------------------------

    @abc.abstractmethod
    def save_agent_response(
        self,
        build_result_id: int | None,
        validation_result_id: int | None,
        response_type: str,
        response_json: dict[str, Any],
    ) -> None:
        """Store raw agent response JSON."""

    # -- Target state --------------------------------------------------------

    @abc.abstractmethod
    def get_status(self, target: str) -> TargetStatus:
        """Current status; ``pending`` if unknown."""

    @abc.abstractmethod
    def set_status(self, target: str, status: TargetStatus) -> None:
        """Update current status."""

    @abc.abstractmethod
    def list_targets(self) -> list[tuple[str, TargetStatus]]:
        """All tracked targets."""

    @abc.abstractmethod
    def reset(self, target: str) -> None:
        """Remove target state entry."""

    @abc.abstractmethod
    def reset_all(self) -> None:
        """Remove all target state entries for this output directory."""
