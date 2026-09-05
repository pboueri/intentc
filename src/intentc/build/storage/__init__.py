"""Persistent, backend-agnostic storage for build state, results, logs, and
agent responses."""

from intentc.build.storage.backend import (
    BuildResult,
    BuildStep,
    GenerationStatus,
    StorageBackend,
    TargetStatus,
)
from intentc.build.storage.sqlite_backend import SQLiteBackend

__all__ = [
    "BuildResult",
    "BuildStep",
    "GenerationStatus",
    "SQLiteBackend",
    "StorageBackend",
    "TargetStatus",
]
