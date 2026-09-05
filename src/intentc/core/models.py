"""Core data models for intentc specification files (.ic / .icv)."""

from __future__ import annotations

import enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ValidationType(str, enum.Enum):
    """Catalogue of the built-in validation types.

    ``Validation.type`` is stored as a plain string so that custom runner types
    can be used without touching the core models; this enum only names the
    built-ins.
    """

    AGENT_VALIDATION = "agent_validation"
    COMMAND_VALIDATION = "command_validation"
    FILE_EXISTS = "file_exists"


class Severity(str, enum.Enum):
    ERROR = "error"
    WARNING = "warning"


class IntentFile(BaseModel):
    """A feature intent parsed from a ``.ic`` file."""

    name: str
    depends_on: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    body: str = ""
    file_references: list[str] = Field(default_factory=list)
    source_path: Path | None = None


class ProjectIntent(BaseModel):
    """The singleton ``intent/project.ic``. Has no ``depends_on``."""

    name: str
    tags: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    body: str = ""
    file_references: list[str] = Field(default_factory=list)
    source_path: Path | None = None


class Implementation(BaseModel):
    """An implementation spec from ``intent/implementations/*.ic``."""

    name: str
    depends_on: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    body: str = ""
    file_references: list[str] = Field(default_factory=list)
    source_path: Path | None = None


class Validation(BaseModel):
    """A single validation entry inside a ``.icv`` file."""

    model_config = ConfigDict(use_enum_values=False)

    name: str
    type: str = ValidationType.AGENT_VALIDATION.value
    severity: Severity = Severity.ERROR
    args: dict[str, Any] = Field(default_factory=dict)


class ValidationFile(BaseModel):
    """A parsed ``.icv`` file."""

    target: str = ""
    version: int = 1
    agent_profile: str | None = None
    validations: list[Validation] = Field(default_factory=list)
    source_path: Path | None = None


class ParseError:
    """A single parse error with location context (a value, not an exception)."""

    def __init__(self, path: Path | None, message: str, field: str | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self.field = field
        self.message = message

    def __str__(self) -> str:
        location = str(self.path) if self.path is not None else "<unknown>"
        if self.field:
            return f"{location} [{self.field}]: {self.message}"
        return f"{location}: {self.message}"

    def __repr__(self) -> str:
        return f"ParseError({self!s})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, ParseError) and str(self) == str(other)


class ParseErrors(Exception):
    """Raised with every parse error accumulated for a file or project."""

    def __init__(self, errors: list[ParseError]) -> None:
        self.errors = list(errors)
        lines = "\n".join(str(e) for e in self.errors)
        super().__init__(f"{len(self.errors)} parse error(s):\n{lines}")
