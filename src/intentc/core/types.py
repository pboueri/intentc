"""Core data structures for intentc specification files."""

from __future__ import annotations

import enum
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, model_validator


class ValidationType(str, enum.Enum):
    """Built-in validation types. Extensible via registration."""

    AGENT_VALIDATION = "agent_validation"
    LLM_JUDGE = "llm_judge"
    FILE_CHECK = "file_check"
    FOLDER_CHECK = "folder_check"
    COMMAND_CHECK = "command_check"


class Severity(str, enum.Enum):
    """What happens when a validation fails."""

    ERROR = "error"
    WARNING = "warning"


class Validation(BaseModel):
    """A single validation entry within a ValidationFile."""

    model_config = {"extra": "ignore"}

    type: ValidationType
    name: str
    severity: Severity = Severity.ERROR
    args: dict[str, Any] = {}


class ValidationFile(BaseModel):
    """Parsed .icv file — validates a feature after it is built."""

    model_config = {"extra": "ignore"}

    target: str
    agent_profile: str | None = None
    validations: list[Validation] = []
    source_path: Path | None = None


# Regex for local file references in markdown bodies.
# Matches: ![alt](path), [text](path) pointing to local paths (not URLs or anchors).
_MARKDOWN_LINK_RE = re.compile(
    r"!?\[[^\]]*\]\((?!https?://|#)([^)]+)\)"
)
# Matches bare relative paths like ../foo/bar.txt or ./file.png.
# Must start with ./ or ../ to avoid false positives on domain names.
_BARE_PATH_RE = re.compile(
    r"(?<!\w)(\.\./[^\s)\"'>]+|\.\/[^\s)\"'>]+)"
)

# Spans covered by markdown links — used to avoid double-counting
_MARKDOWN_FULL_RE = re.compile(r"!?\[[^\]]*\]\([^)]+\)")


def extract_file_references(body: str) -> list[str]:
    """Extract local file references from markdown body text.

    Finds markdown links/images pointing to local paths and bare relative
    paths with file extensions. Excludes URLs and anchor-only links.
    """
    refs: list[str] = []
    seen: set[str] = set()

    # Collect spans covered by markdown links to avoid double-matching
    link_spans: list[tuple[int, int]] = []
    for m in _MARKDOWN_FULL_RE.finditer(body):
        link_spans.append((m.start(), m.end()))

    for match in _MARKDOWN_LINK_RE.finditer(body):
        path = match.group(1).strip()
        if path not in seen:
            refs.append(path)
            seen.add(path)

    for match in _BARE_PATH_RE.finditer(body):
        # Skip if this match falls inside a markdown link
        pos = match.start()
        if any(s <= pos < e for s, e in link_spans):
            continue
        path = match.group(1).strip()
        if path not in seen:
            refs.append(path)
            seen.add(path)

    return refs


class IntentFile(BaseModel):
    """Parsed .ic file — a feature intent with frontmatter and markdown body."""

    model_config = {"extra": "ignore"}

    name: str
    depends_on: list[str] = []
    tags: list[str] = []
    authors: list[str] = []
    body: str = ""
    file_references: list[str] = []
    source_path: Path | None = None


class ProjectIntent(BaseModel):
    """Special singleton at intent/project.ic. No depends_on allowed."""

    model_config = {"extra": "ignore"}

    name: str
    tags: list[str] = []
    authors: list[str] = []
    body: str = ""
    file_references: list[str] = []
    source_path: Path | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_depends_on(cls, values: dict[str, Any]) -> dict[str, Any]:
        if values.get("depends_on"):
            raise ValueError("project.ic cannot have depends_on")
        values.pop("depends_on", None)
        return values


class Implementation(BaseModel):
    """Special file at intent/implementation.ic — language, libs, conventions."""

    model_config = {"extra": "ignore"}

    name: str
    tags: list[str] = []
    authors: list[str] = []
    body: str = ""
    file_references: list[str] = []
    source_path: Path | None = None
