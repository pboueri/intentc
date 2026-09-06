"""Backend-agnostic storage interface: the abstract `StorageBackend`, the record
types it reads and writes (`BuildStep`, `BuildResult`), and the `GenerationStatus`
and `TargetStatus` enums.

`TargetStatus`, `BuildStep`, and `BuildResult` are defined here (rather than in
`intentc.build.state`) because storage is upstream of state in the dependency
DAG and the backend interface must reference them. The state module re-exports
them unchanged.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field


class GenerationStatus(str, Enum):
    """Status of a single `build()` invocation."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TargetStatus(str, Enum):
    """Current status of a target."""

    PENDING = "pending"
    BUILT = "built"
    FAILED = "failed"
    OUTDATED = "outdated"


class BuildStep(BaseModel):
    """One phase of a build (resolve_deps, build, validate, checkpoint)."""

    phase: str
    status: str
    duration_secs: float = 0.0
    summary: str = ""


class RefinementSession(BaseModel):
    """An interactive refinement session: its journal, snapshot, and bake outcome."""

    session_id: str
    target: str
    output_dir: str
    status: str  # recording | baking | baked | failed | abandoned
    base_commit: str
    snapshot_id: Optional[str] = None
    seed_prompt: str = ""
    journal: str = ""
    bake_attempts: int = 0
    bake_generation_id: Optional[str] = None
    bake_response_json: Optional[str] = None
    started_at: str = ""
    ended_at: Optional[str] = None


class BuildResult(BaseModel):
    """The outcome of building one target."""

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


class StorageBackend(ABC):
    """Backend-agnostic persistence interface for build state, results, logs,
    and agent responses. Scoped to a single output directory."""

    def __init__(self, base_dir: Path, output_dir: str) -> None:
        self.base_dir = Path(base_dir)
        self.output_dir = output_dir

    # -- Generation methods -------------------------------------------------

    @abstractmethod
    def create_generation(
        self,
        generation_id: str,
        output_dir: str,
        profile_name: Optional[str] = None,
        options: Optional[dict[str, Any]] = None,
    ) -> None: ...

    @abstractmethod
    def complete_generation(self, generation_id: str, status: GenerationStatus) -> None: ...

    @abstractmethod
    def log_generation_event(self, generation_id: str, message: str) -> None: ...

    @abstractmethod
    def get_generation(self, generation_id: str) -> Optional[dict[str, Any]]: ...

    # -- Intent/validation file version methods ------------------------------

    @abstractmethod
    def record_intent_version(self, name: str, source_path: str, content_hash: str) -> int: ...

    @abstractmethod
    def record_validation_version(self, target: str, source_path: str, content_hash: str) -> int: ...

    # -- Build result methods -------------------------------------------------

    @abstractmethod
    def save_build_result(
        self,
        target: str,
        result: BuildResult,
        intent_version_id: Optional[int] = None,
        git_diff: Optional[str] = None,
        files_created: Optional[list[str]] = None,
        files_modified: Optional[list[str]] = None,
    ) -> int: ...

    @abstractmethod
    def get_build_result(self, target: str) -> Optional[BuildResult]: ...

    @abstractmethod
    def get_build_history(self, target: str, limit: int = 50) -> list[BuildResult]: ...

    @abstractmethod
    def get_build_diff(self, target: str) -> Optional[str]: ...

    @abstractmethod
    def get_validation_results(
        self, target: str, build_result_id: Optional[int] = None
    ) -> list[dict[str, Any]]: ...

    # -- Build step methods -----------------------------------------------------

    @abstractmethod
    def save_build_step(self, build_result_id: int, step: BuildStep, log: str, step_order: int) -> None: ...

    # -- Validation result methods ------------------------------------------

    @abstractmethod
    def save_validation_result(
        self,
        build_result_id: Optional[int],
        generation_id: str,
        target: str,
        validation_file_version_id: Optional[int],
        name: str,
        type: str,
        severity: str,
        status: str,
        reason: str,
        duration_secs: Optional[float],
    ) -> int: ...

    # -- Agent response methods -----------------------------------------------

    @abstractmethod
    def save_agent_response(
        self,
        build_result_id: Optional[int],
        validation_result_id: Optional[int],
        response_type: str,
        response_json: dict[str, Any],
    ) -> None: ...

    # -- Refinement session methods -------------------------------------------

    @abstractmethod
    def create_refinement_session(self, session: RefinementSession) -> None: ...

    @abstractmethod
    def update_refinement_session(self, session_id: str, **fields: Any) -> None: ...

    @abstractmethod
    def get_refinement_session(self, session_id: str) -> Optional[RefinementSession]: ...

    @abstractmethod
    def get_open_refinement_session(self, target: Optional[str] = None) -> Optional[RefinementSession]: ...

    @abstractmethod
    def list_refinement_sessions(self, target: str, limit: int = 10) -> list[RefinementSession]: ...

    # -- Target state methods -------------------------------------------------

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

    # -- Lifecycle --------------------------------------------------------------

    @abstractmethod
    def close(self) -> None: ...

    def __enter__(self) -> "StorageBackend":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()
