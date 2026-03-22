from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class ValidationType(str, Enum):
    AGENT_VALIDATION = "agent_validation"
    LLM_JUDGE = "llm_judge"
    FILE_CHECK = "file_check"
    FOLDER_CHECK = "folder_check"
    COMMAND_CHECK = "command_check"


class Severity(str, Enum):
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
    tags: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    body: str = ""
    file_references: list[str] = Field(default_factory=list)
    source_path: Path | None = None


class Validation(BaseModel):
    name: str
    type: str = "agent_validation"
    severity: Severity = Severity.ERROR
    args: dict[str, Any] = Field(default_factory=dict)


class ValidationFile(BaseModel):
    target: str
    agent_profile: str | None = None
    validations: list[Validation] = Field(default_factory=list)
    source_path: Path | None = None


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


# File reference patterns: plain file paths or glob patterns referenced in body text
_FILE_REF_PATTERN = re.compile(
    r'(?<![(\[`])'           # not preceded by (, [, or `
    r'((?:\.\./|\./)+'       # starts with ../ or ./ (one or more)
    r'[A-Za-z0-9_.*/?-]+'   # path characters including glob wildcards
    r'(?:/[A-Za-z0-9_.*/?-]+)*'  # additional path segments
    r')'
    r'|'
    r'([A-Za-z0-9_][A-Za-z0-9_.-]*'  # filename starting with alnum
    r'\.[a-zA-Z]{1,5})'     # with extension
)


def extract_file_references(body: str) -> list[str]:
    refs: list[str] = []
    for match in _FILE_REF_PATTERN.finditer(body):
        ref = match.group(1) or match.group(2)
        if ref and ref not in refs:
            refs.append(ref)
    return refs
