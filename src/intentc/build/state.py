"""Build state types for intentc."""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TargetStatus(str, enum.Enum):
    PENDING = "pending"
    BUILT = "built"
    FAILED = "failed"
    OUTDATED = "outdated"


# ---------------------------------------------------------------------------
# Build step
# ---------------------------------------------------------------------------


class BuildStep(BaseModel):
    """A single phase within a build."""

    model_config = {"extra": "ignore"}

    phase: str
    status: str  # "success" or "failure"
    duration_secs: float
    summary: str


# ---------------------------------------------------------------------------
# Build result
# ---------------------------------------------------------------------------


class BuildResult(BaseModel):
    """Result of building a single target."""

    model_config = {"extra": "ignore"}

    target: str
    status: TargetStatus
    steps: list[BuildStep] = Field(default_factory=list)
    commit_id: str | None = None
    total_duration_secs: float = 0.0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    generation_id: str | None = None
