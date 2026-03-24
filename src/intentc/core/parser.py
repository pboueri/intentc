"""File I/O for .ic and .icv files."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Union

import yaml

from intentc.core.models import (
    Implementation,
    IntentFile,
    ParseError,
    ParseErrors,
    ProjectIntent,
    Severity,
    Validation,
    ValidationFile,
    ValidationType,
)

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?\n)---\s*\n?(.*)", re.DOTALL)
_FILE_REF_RE = re.compile(r"(?<!\[)(?<!\()(?:\.\.?/)?(?:[\w.*-]+/)*[\w.*-]+\.\w+|(?:\.\.?/)?(?:[\w.*-]+/)+\*")


def extract_file_references(body: str) -> list[str]:
    """Extract file references from markdown body text.

    Looks for path-like patterns: relative paths with extensions or glob patterns.
    """
    refs: list[str] = []
    for match in _FILE_REF_RE.finditer(body):
        candidate = match.group()
        # Filter out things that are clearly not file references
        if candidate.startswith("http") or candidate.startswith("ftp"):
            continue
        refs.append(candidate)
    return refs


def _parse_frontmatter(text: str, path: Path) -> tuple[dict, str]:
    """Split a .ic file into YAML frontmatter dict and markdown body."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ParseErrors([ParseError(path=path, message="Missing YAML frontmatter delimited by ---")])
    raw_yaml, body = m.group(1), m.group(2)
    try:
        meta = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as exc:
        raise ParseErrors([ParseError(path=path, message=f"Invalid YAML frontmatter: {exc}")])
    if not isinstance(meta, dict):
        raise ParseErrors([ParseError(path=path, message="Frontmatter must be a YAML mapping")])
    return meta, body


def parse_intent_file(
    path: Path,
    as_project: bool = False,
    as_implementation: bool = False,
) -> Union[IntentFile, ProjectIntent, Implementation]:
    """Parse a .ic file and return the appropriate model."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(text, path)

    errors: list[ParseError] = []
    name = meta.get("name")
    if not name:
        errors.append(ParseError(path=path, field="name", message="'name' is required"))
    if errors:
        raise ParseErrors(errors)

    depends_on = meta.get("depends_on", [])
    if not isinstance(depends_on, list):
        depends_on = [depends_on]

    tags = meta.get("tags", [])
    if not isinstance(tags, list):
        tags = [tags]

    authors = meta.get("authors", [])
    if not isinstance(authors, list):
        authors = [authors]

    file_references = extract_file_references(body)

    if as_project:
        return ProjectIntent(
            name=name,
            tags=tags,
            authors=authors,
            body=body,
            file_references=file_references,
            source_path=path,
        )
    elif as_implementation:
        return Implementation(
            name=name,
            depends_on=depends_on,
            tags=tags,
            authors=authors,
            body=body,
            file_references=file_references,
            source_path=path,
        )
    else:
        return IntentFile(
            name=name,
            depends_on=depends_on,
            tags=tags,
            authors=authors,
            body=body,
            file_references=file_references,
            source_path=path,
        )


def parse_validation_file(path: Path) -> ValidationFile:
    """Parse a .icv file (pure YAML, no frontmatter)."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ParseErrors([ParseError(path=path, message=f"Invalid YAML: {exc}")])

    if data is None:
        return ValidationFile(source_path=path)

    if not isinstance(data, dict):
        raise ParseErrors([ParseError(path=path, message="Validation file must be a YAML mapping")])

    target = data.get("target", "")
    agent_profile = data.get("agent_profile")

    validations: list[Validation] = []
    for entry in data.get("validations", []):
        if not isinstance(entry, dict):
            raise ParseErrors([ParseError(path=path, message="Each validation must be a mapping")])
        v_name = entry.get("name", "")
        v_type_str = entry.get("type", "agent_validation")
        try:
            v_type = ValidationType(v_type_str)
        except ValueError:
            raise ParseErrors([ParseError(
                path=path,
                field="type",
                message=f"Unknown validation type: {v_type_str}",
            )])
        severity_str = entry.get("severity", "error")
        try:
            severity = Severity(severity_str)
        except ValueError:
            raise ParseErrors([ParseError(
                path=path,
                field="severity",
                message=f"Unknown severity: {severity_str}",
            )])
        args = entry.get("args", {})
        validations.append(Validation(name=v_name, type=v_type, severity=severity, args=args))

    return ValidationFile(
        target=target,
        agent_profile=agent_profile,
        validations=validations,
        source_path=path,
    )


def _build_frontmatter(meta: dict) -> str:
    """Serialize a metadata dict into YAML frontmatter."""
    yaml_str = yaml.dump(meta, default_flow_style=False, sort_keys=False, allow_unicode=True)
    return f"---\n{yaml_str}---\n"


def write_intent_file(
    intent: Union[IntentFile, ProjectIntent, Implementation],
    path: Path | None = None,
) -> Path:
    """Write an intent object back to a .ic file."""
    target = Path(path) if path is not None else intent.source_path
    if target is None:
        raise ValueError("No path provided and source_path is not set")

    meta: dict = {"name": intent.name}
    if isinstance(intent, IntentFile) or isinstance(intent, Implementation):
        if intent.depends_on:
            meta["depends_on"] = intent.depends_on
    if intent.tags:
        meta["tags"] = intent.tags
    if intent.authors:
        meta["authors"] = intent.authors

    content = _build_frontmatter(meta)
    if intent.body:
        content += "\n" + intent.body

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def write_validation_file(
    vf: ValidationFile,
    path: Path | None = None,
) -> Path:
    """Write a ValidationFile back to a .icv file."""
    target = Path(path) if path is not None else vf.source_path
    if target is None:
        raise ValueError("No path provided and source_path is not set")

    data: dict = {}
    if vf.target:
        data["target"] = vf.target
    if vf.agent_profile:
        data["agent_profile"] = vf.agent_profile
    if vf.validations:
        data["validations"] = []
        for v in vf.validations:
            entry: dict = {"name": v.name}
            if v.type != ValidationType.AGENT_VALIDATION:
                entry["type"] = v.type.value
            if v.severity != Severity.ERROR:
                entry["severity"] = v.severity.value
            if v.args:
                entry["args"] = v.args
            data["validations"].append(entry)

    yaml_str = yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True) if data else ""

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml_str, encoding="utf-8")
    return target
