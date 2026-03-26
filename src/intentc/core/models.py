"""Core data models for intentc specification files."""

from __future__ import annotations

import enum
from pathlib import Path

from pydantic import BaseModel, Field


class ValidationType(str, enum.Enum):
    AGENT_VALIDATION = "agent_validation"


class Severity(str, enum.Enum):
    ERROR = "error"
    WARNING = "warning"


class IntentFile(BaseModel):
    name: str
    depends_on: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    body: str = ""
    file_references: list[str] = Field(default_factory=list)
    source_path: Path | None = None


class ProjectIntent(BaseModel):
    name: str
    tags: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    body: str = ""
    file_references: list[str] = Field(default_factory=list)
    source_path: Path | None = None


class Implementation(BaseModel):
    name: str
    depends_on: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    body: str = ""
    file_references: list[str] = Field(default_factory=list)
    source_path: Path | None = None


class Validation(BaseModel):
    name: str
    type: ValidationType = ValidationType.AGENT_VALIDATION
    severity: Severity = Severity.ERROR
    args: dict[str, object] = Field(default_factory=dict)


class ValidationFile(BaseModel):
    target: str = ""
    agent_profile: str | None = None
    validations: list[Validation] = Field(default_factory=list)
    source_path: Path | None = None


class ParseError:
    """A single parse error with location context."""

    def __init__(self, path: Path, message: str, field: str | None = None) -> None:
        self.path = path
        self.field = field
        self.message = message

    def __str__(self) -> str:
        if self.field:
            return f"{self.path} [{self.field}]: {self.message}"
        return f"{self.path}: {self.message}"

    def __repr__(self) -> str:
        return f"ParseError({self!s})"


class ParseErrors(Exception):
    """Exception holding multiple ParseError instances."""

    def __init__(self, errors: list[ParseError]) -> None:
        self.errors = errors
        lines = "\n".join(str(e) for e in errors)
        super().__init__(f"{len(errors)} parse error(s):\n{lines}")
