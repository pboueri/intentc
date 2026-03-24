"""Parse and write .ic and .icv files."""

from __future__ import annotations

import re
from pathlib import Path

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

# Matches references like `some/path.png` or `../../design_system/*` in markdown.
_FILE_REF_RE = re.compile(
    r"(?<![(\[`])"           # not preceded by ( [ or `
    r"(?:"
    r"\.{1,2}/"              # relative path starting with ./ or ../
    r"|"
    r"[a-zA-Z0-9_][a-zA-Z0-9_\-]*/)"  # or a directory prefix like dir/
    r"[a-zA-Z0-9_\-./\*]+"  # rest of the path
)


def extract_file_references(text: str) -> list[str]:
    """Extract file references from markdown body text."""
    return _FILE_REF_RE.findall(text)


def _split_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """Split a .ic file into YAML frontmatter dict and body string.

    Frontmatter is delimited by ``---`` lines.
    """
    text = text.strip()
    if not text.startswith("---"):
        return {}, text

    # Find the closing ---
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text

    yaml_block = text[3:end].strip()
    body = text[end + 4:].strip()

    meta = yaml.safe_load(yaml_block)
    if not isinstance(meta, dict):
        meta = {}
    return meta, body


def parse_intent_file(
    path: Path,
    as_project: bool = False,
    as_implementation: bool = False,
) -> IntentFile | ProjectIntent | Implementation:
    """Parse a .ic file and return the appropriate model."""
    path = Path(path)
    errors: list[ParseError] = []

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ParseErrors([ParseError(path, str(exc))]) from exc

    meta, body = _split_frontmatter(raw)

    name = meta.get("name")
    if not name:
        errors.append(ParseError(path, "missing required field", field="name"))
    if errors:
        raise ParseErrors(errors)

    file_refs = extract_file_references(body)

    common = dict(
        name=name,
        tags=meta.get("tags", []),
        authors=meta.get("authors", []),
        body=body,
        file_references=file_refs,
        source_path=path,
    )

    if as_project:
        return ProjectIntent(**common)

    depends_on = meta.get("depends_on", [])
    common["depends_on"] = depends_on

    if as_implementation:
        return Implementation(**common)

    return IntentFile(**common)


def parse_validation_file(path: Path) -> ValidationFile:
    """Parse a .icv validation file (pure YAML)."""
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ParseErrors([ParseError(path, str(exc))]) from exc

    data = yaml.safe_load(raw)

    # Empty file is valid
    if data is None:
        return ValidationFile(source_path=path)

    if not isinstance(data, dict):
        raise ParseErrors([ParseError(path, "expected a YAML mapping at top level")])

    validations: list[Validation] = []
    for v in data.get("validations", []):
        vtype = v.get("type", "agent_validation")
        try:
            vtype_enum = ValidationType(vtype)
        except ValueError:
            vtype_enum = ValidationType.AGENT_VALIDATION

        severity = v.get("severity", "error")
        try:
            sev_enum = Severity(severity)
        except ValueError:
            sev_enum = Severity.ERROR

        validations.append(
            Validation(
                name=v.get("name", ""),
                type=vtype_enum,
                severity=sev_enum,
                args=v.get("args", {}),
            )
        )

    return ValidationFile(
        target=data.get("target", ""),
        agent_profile=data.get("agent_profile"),
        validations=validations,
        source_path=path,
    )


def _intent_to_frontmatter(intent: IntentFile | ProjectIntent | Implementation) -> str:
    """Serialize an intent object back to .ic file content."""
    meta: dict[str, object] = {"name": intent.name}

    if hasattr(intent, "depends_on") and intent.depends_on:
        meta["depends_on"] = intent.depends_on
    if intent.tags:
        meta["tags"] = intent.tags
    if intent.authors:
        meta["authors"] = intent.authors

    yaml_str = yaml.dump(meta, default_flow_style=False, sort_keys=False).strip()
    parts = ["---", yaml_str, "---"]
    if intent.body:
        parts.append("")
        parts.append(intent.body)
    return "\n".join(parts) + "\n"


def write_intent_file(
    intent: IntentFile | ProjectIntent | Implementation,
    path: Path | None = None,
) -> Path:
    """Write an intent object to a .ic file. Returns the path written to."""
    out = Path(path) if path is not None else intent.source_path
    if out is None:
        raise ValueError("No path provided and source_path is not set")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_intent_to_frontmatter(intent), encoding="utf-8")
    return out


def write_validation_file(
    vf: ValidationFile,
    path: Path | None = None,
) -> Path:
    """Write a ValidationFile to a .icv file. Returns the path written to."""
    out = Path(path) if path is not None else vf.source_path
    if out is None:
        raise ValueError("No path provided and source_path is not set")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    data: dict[str, object] = {}
    if vf.target:
        data["target"] = vf.target
    if vf.agent_profile is not None:
        data["agent_profile"] = vf.agent_profile
    if vf.validations:
        data["validations"] = [
            {
                "name": v.name,
                "type": v.type.value,
                "severity": v.severity.value,
                "args": dict(v.args) if v.args else {},
            }
            for v in vf.validations
        ]

    out.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False), encoding="utf-8")
    return out
