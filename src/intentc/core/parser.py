"""Parse and write .ic and .icv files from/to disk."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from intentc.core.types import (
    Implementation,
    IntentFile,
    ProjectIntent,
    ValidationFile,
    extract_file_references,
)


@dataclass
class ParseError:
    """A single parse error with location context."""

    path: Path
    field: str | None
    message: str

    def __str__(self) -> str:
        loc = str(self.path)
        if self.field:
            loc += f" [{self.field}]"
        return f"{loc}: {self.message}"


class ParseErrors(Exception):
    """Accumulated parse errors raised together."""

    def __init__(self, errors: list[ParseError]) -> None:
        self.errors = errors
        messages = "\n".join(str(e) for e in errors)
        super().__init__(f"{len(errors)} parse error(s):\n{messages}")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split YAML frontmatter from markdown body.

    Returns (metadata_dict, body_text). Raises ValueError when
    the frontmatter delimiters are missing or YAML is malformed.
    """
    text = text.strip()
    if not text.startswith("---"):
        raise ValueError("Missing opening --- frontmatter delimiter")

    # Find the closing ---
    end = text.find("---", 3)
    if end == -1:
        raise ValueError("Missing closing --- frontmatter delimiter")

    yaml_block = text[3:end].strip()
    body = text[end + 3:].strip()

    meta = yaml.safe_load(yaml_block)
    if meta is None:
        meta = {}
    if not isinstance(meta, dict):
        raise ValueError(f"Frontmatter must be a YAML mapping, got {type(meta).__name__}")

    return meta, body


def _build_frontmatter(meta: dict[str, Any]) -> str:
    """Render a metadata dict back to YAML frontmatter string."""
    # Filter out None values for cleaner output
    filtered = {k: v for k, v in meta.items() if v is not None and v != [] and v != ""}
    yaml_str = yaml.dump(filtered, default_flow_style=False, sort_keys=False).strip()
    return f"---\n{yaml_str}\n---"


# ---------------------------------------------------------------------------
# .ic parsing
# ---------------------------------------------------------------------------

def parse_intent_file(
    path: Path,
    *,
    as_project: bool = False,
    as_implementation: bool = False,
) -> IntentFile | ProjectIntent | Implementation:
    """Parse a .ic file from disk.

    Args:
        path: Path to the .ic file.
        as_project: If True, parse as ProjectIntent (rejects depends_on).
        as_implementation: If True, parse as Implementation.

    Returns:
        The parsed model instance.

    Raises:
        ParseErrors: On any parse failures (accumulated).
    """
    errors: list[ParseError] = []
    path = Path(path)

    if not path.exists():
        raise ParseErrors([ParseError(path=path, field=None, message="File not found")])

    raw = path.read_text(encoding="utf-8")

    try:
        meta, body = _split_frontmatter(raw)
    except ValueError as exc:
        raise ParseErrors([ParseError(path=path, field=None, message=str(exc))])

    # Validate required field
    if "name" not in meta:
        errors.append(ParseError(path=path, field="name", message="Required field 'name' is missing"))

    if errors:
        raise ParseErrors(errors)

    file_refs = extract_file_references(body)

    data = {
        **meta,
        "body": body,
        "file_references": file_refs,
        "source_path": path,
    }

    try:
        if as_project:
            return ProjectIntent(**data)
        elif as_implementation:
            return Implementation(**data)
        else:
            return IntentFile(**data)
    except Exception as exc:
        raise ParseErrors([ParseError(path=path, field=None, message=str(exc))])


# ---------------------------------------------------------------------------
# .icv parsing
# ---------------------------------------------------------------------------

def parse_validation_file(path: Path) -> ValidationFile:
    """Parse a .icv validation file from disk.

    Raises:
        ParseErrors: On any parse failures (accumulated).
    """
    errors: list[ParseError] = []
    path = Path(path)

    if not path.exists():
        raise ParseErrors([ParseError(path=path, field=None, message="File not found")])

    raw = path.read_text(encoding="utf-8")

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ParseErrors([ParseError(path=path, field=None, message=f"Invalid YAML: {exc}")])

    if data is None:
        data = {}

    if not isinstance(data, dict):
        raise ParseErrors([ParseError(path=path, field=None, message=f"Expected a YAML mapping, got {type(data).__name__}")])

    if "target" not in data:
        errors.append(ParseError(path=path, field="target", message="Required field 'target' is missing"))

    if errors:
        raise ParseErrors(errors)

    data["source_path"] = path

    try:
        return ValidationFile(**data)
    except Exception as exc:
        raise ParseErrors([ParseError(path=path, field=None, message=str(exc))])


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def write_intent_file(
    intent: IntentFile | ProjectIntent | Implementation,
    path: Path | None = None,
) -> Path:
    """Write an intent file back to disk.

    Args:
        intent: The intent model to write.
        path: Destination path. Falls back to intent.source_path.

    Returns:
        The path written to.

    Raises:
        ValueError: If no path is provided and source_path is None.
    """
    dest = path or intent.source_path
    if dest is None:
        raise ValueError("No destination path: provide path argument or set source_path")
    dest = Path(dest)

    meta: dict[str, Any] = {"name": intent.name}
    if intent.tags:
        meta["tags"] = intent.tags
    if intent.authors:
        meta["authors"] = intent.authors
    if isinstance(intent, IntentFile) and intent.depends_on:
        meta["depends_on"] = intent.depends_on

    header = _build_frontmatter(meta)
    content = f"{header}\n\n{intent.body}\n" if intent.body else f"{header}\n"

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")
    return dest


def write_validation_file(
    vf: ValidationFile,
    path: Path | None = None,
) -> Path:
    """Write a validation file back to disk.

    Args:
        vf: The validation file model to write.
        path: Destination path. Falls back to vf.source_path.

    Returns:
        The path written to.

    Raises:
        ValueError: If no path is provided and source_path is None.
    """
    dest = path or vf.source_path
    if dest is None:
        raise ValueError("No destination path: provide path argument or set source_path")
    dest = Path(dest)

    data: dict[str, Any] = {"target": vf.target}
    if vf.agent_profile is not None:
        data["agent_profile"] = vf.agent_profile
    validation_entries = []
    for v in vf.validations:
        entry: dict = {
            "name": v.name,
            "type": v.type.value,
            "severity": v.severity.value,
            "args": v.args,
        }
        if v.agent_profile is not None:
            ap = v.agent_profile
            entry["agent_profile"] = {
                k: val for k, val in {
                    "provider": ap.provider,
                    "model_id": ap.model_id,
                    "timeout": ap.timeout,
                }.items()
                if val is not None
            }
        validation_entries.append(entry)
    data["validations"] = validation_entries

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False), encoding="utf-8")
    return dest
