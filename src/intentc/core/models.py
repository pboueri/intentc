"""Core data models for intentc: intent files, implementations, and validation files."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field, PrivateAttr


class ValidationType(str, Enum):
    """Built-in catalogue of validation runner types.

    This is NOT exhaustive: `Validation.type` is stored as a plain string so
    that custom runners can be registered without changing this enum.
    """

    AGENT_VALIDATION = "agent_validation"
    COMMAND_VALIDATION = "command_validation"
    FILE_EXISTS = "file_exists"


class Severity(str, Enum):
    """Severity of a validation failure."""

    ERROR = "error"
    WARNING = "warning"


class Artifact(BaseModel):
    """A file that constrains a build: a schema, mockup, fixture, prompt, etc.

    Declared explicitly in `.ic` frontmatter (kind carries meaning, note explains
    how it constrains the build) or derived from an inline body reference (always
    `kind: reference` with an empty note).
    """

    path: str
    kind: str = "reference"
    note: str = ""
    owner: str = ""
    resolved_paths: list[Path] = Field(default_factory=list)


class IntentFile(BaseModel):
    """An `.ic` file describing a feature's intent."""

    name: str
    depends_on: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    body: str = ""
    file_references: list[str] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    source_path: Optional[Path] = None

    # Paths explicitly declared under `artifacts:` frontmatter (as opposed to inline
    # body references), tracked separately from `artifacts` because that list merges
    # both kinds and dedup collapses a path declared both ways to one entry. Used by
    # `check_project` to warn about inline references that were never declared.
    _declared_artifact_paths: set[str] = PrivateAttr(default_factory=set)


class ProjectIntent(BaseModel):
    """The special singleton `intent/project.ic` file. Cannot depend on features."""

    name: str
    tags: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    body: str = ""
    file_references: list[str] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    source_path: Optional[Path] = None


class Implementation(BaseModel):
    """An `.ic` file under `implementations/` describing a target stack."""

    name: str
    depends_on: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    body: str = ""
    file_references: list[str] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    source_path: Optional[Path] = None


class Validation(BaseModel):
    """A single validation entry within a `.icv` file."""

    name: str
    type: str = ValidationType.AGENT_VALIDATION.value
    severity: Severity = Severity.ERROR
    args: dict[str, Any] = Field(default_factory=dict)


class ValidationFile(BaseModel):
    """An `.icv` file: a set of validations targeting a feature or the project."""

    target: str = ""
    version: int = 1
    agent_profile: Optional[str] = None
    validations: list[Validation] = Field(default_factory=list)
    source_path: Optional[Path] = None


class ParseError:
    """A single parse error with location context. Not raisable on its own."""

    def __init__(self, path: Path, field: Optional[str], message: str) -> None:
        self.path = path
        self.field = field
        self.message = message

    def __str__(self) -> str:
        if self.field is None:
            return f"{self.path}: {self.message}"
        return f"{self.path} [{self.field}]: {self.message}"

    def __repr__(self) -> str:
        return f"ParseError(path={self.path!r}, field={self.field!r}, message={self.message!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ParseError):
            return NotImplemented
        return (self.path, self.field, self.message) == (other.path, other.field, other.message)


class ParseErrors(Exception):
    """Raised with one or more accumulated `ParseError` instances."""

    def __init__(self, errors: list[ParseError]) -> None:
        self.errors = errors
        message = f"{len(errors)} parse error(s):\n" + "\n".join(str(e) for e in errors)
        super().__init__(message)
