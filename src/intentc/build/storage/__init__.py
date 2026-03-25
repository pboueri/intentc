"""Storage sub-package: persistent build state backed by pluggable databases."""

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
