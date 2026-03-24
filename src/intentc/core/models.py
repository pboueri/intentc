"""Core data models for intentc."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class ValidationType(str, enum.Enum):
    AGENT_VALIDATION = "agent_validation"
    LLM_JUDGE = "llm_judge"
    FILE_CHECK = "file_check"
    FOLDER_CHECK = "folder_check"
    COMMAND_CHECK = "command_check"


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
    args: dict[str, Any] = Field(default_factory=dict)


class ValidationFile(BaseModel):
    target: str = ""
    agent_profile: str | None = None
    validations: list[Validation] = Field(default_factory=list)
    source_path: Path | None = None


@dataclass
class ParseError:
    path: Path
    field: str | None = None
    message: str = ""

    def __str__(self) -> str:
        if self.field:
            return f"{self.path} [{self.field}]: {self.message}"
        return f"{self.path}: {self.message}"


class ParseErrors(Exception):
    def __init__(self, errors: list[ParseError]) -> None:
        self.errors = errors
        lines = [str(e) for e in errors]
        msg = f"{len(errors)} parse error(s):\n" + "\n".join(lines)
        super().__init__(msg)
