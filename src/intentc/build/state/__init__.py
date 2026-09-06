"""Per-target build state and history, backed by a `StorageBackend`; git-backed
checkpointing of generated output."""

from intentc.build.state.state import GitVersionControl, StateManager, VersionControl
from intentc.build.storage import BuildResult, BuildStep, TargetStatus

__all__ = [
    "BuildResult",
    "BuildStep",
    "GitVersionControl",
    "StateManager",
    "TargetStatus",
    "VersionControl",
]
