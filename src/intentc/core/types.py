"""Core data models for intentc."""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ValidationType(str, enum.Enum):
    AGENT_VALIDATION = "agent_validation"


class Severity(str, enum.Enum):
    ERROR = "error"
    WARNING = "warning"


# ---------------------------------------------------------------------------
# Intent files
# ---------------------------------------------------------------------------


class IntentFile(BaseModel):
    model_config = {"extra": "ignore"}

    name: str
    depends_on: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    body: str = ""
    file_references: list[str] = Field(default_factory=list)
    source_path: Path | None = None


class ProjectIntent(BaseModel):
    model_config = {"extra": "ignore"}

    name: str
    tags: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    body: str = ""
    file_references: list[str] = Field(default_factory=list)
    source_path: Path | None = None


class Implementation(BaseModel):
    model_config = {"extra": "ignore"}

    name: str
    depends_on: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    body: str = ""
    file_references: list[str] = Field(default_factory=list)
    source_path: Path | None = None


# ---------------------------------------------------------------------------
# Validation files
# ---------------------------------------------------------------------------


class Validation(BaseModel):
    model_config = {"extra": "ignore"}

    name: str
    type: str = ValidationType.AGENT_VALIDATION.value
    severity: Severity = Severity.ERROR
    args: dict[str, Any] = Field(default_factory=dict)


class ValidationFile(BaseModel):
    model_config = {"extra": "ignore"}

    target: str
    agent_profile: str | None = None
    validations: list[Validation] = Field(default_factory=list)
    source_path: Path | None = None


# ---------------------------------------------------------------------------
# Parse errors
# ---------------------------------------------------------------------------

_FILE_REF_RE = re.compile(
    r"(?<![(\[/a-zA-Z0-9])"
    r"(?:\.\.?/)?(?:[a-zA-Z0-9_\-]+/)+"
    r"[a-zA-Z0-9_\-]+(?:\.[a-zA-Z0-9_]+)+"
    r"|"
    r"(?:\.\.?/)(?:[a-zA-Z0-9_\-]+/)*"
    r"[a-zA-Z0-9_\-]+(?:\.\*|\.[a-zA-Z0-9_]+)*"
    r"|"
    r"[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_]+"
)


@dataclass
class ParseError:
    path: Path
    field: str | None
    message: str

    def __str__(self) -> str:
        if self.field is None:
            return f"{self.path}: {self.message}"
        return f"{self.path} [{self.field}]: {self.message}"


class ParseErrors(Exception):
    def __init__(self, errors: list[ParseError]) -> None:
        self.errors = errors
        lines = "\n".join(str(e) for e in errors)
        super().__init__(f"{len(errors)} parse error(s):\n{lines}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def extract_file_references(body: str) -> list[str]:
    """Extract file references from a markdown body string."""
    refs: list[str] = []
    for line in body.splitlines():
        for match in _FILE_REF_RE.finditer(line):
            ref = match.group(0)
            if ref in ("e.g", "i.e", "etc."):
                continue
            refs.append(ref)
    return refs
