"""File I/O for .ic and .icv files."""

from __future__ import annotations

from pathlib import Path

import yaml

from intentc.core.types import (
    Implementation,
    IntentFile,
    ParseError,
    ParseErrors,
    ProjectIntent,
    Validation,
    ValidationFile,
    extract_file_references,
)


def _split_front_matter(text: str) -> tuple[dict | None, str]:
    """Split YAML front matter from body. Returns (metadata_dict, body)."""
    text = text.lstrip("\ufeff")  # strip BOM if present
    if not text.startswith("---"):
        return None, text

    # Find the closing ---
    end = text.find("\n---", 3)
    if end == -1:
        return None, text

    yaml_block = text[3:end].strip()
    body = text[end + 4:]  # skip past \n---
    if body.startswith("\n"):
        body = body[1:]

    meta = yaml.safe_load(yaml_block)
    if not isinstance(meta, dict):
        return None, text
    return meta, body


def _build_front_matter(meta: dict) -> str:
    """Serialize metadata dict to YAML front matter string."""
    yaml_str = yaml.dump(meta, default_flow_style=False, sort_keys=False).rstrip("\n")
    return f"---\n{yaml_str}\n---\n"


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------


def parse_intent_file(
    path: Path,
    *,
    as_project: bool = False,
    as_implementation: bool = False,
) -> IntentFile | ProjectIntent | Implementation:
    """Parse a .ic file into an IntentFile, ProjectIntent, or Implementation."""
    path = Path(path)
    errors: list[ParseError] = []

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ParseErrors([ParseError(path=path, field=None, message="File not found")])

    meta, body = _split_front_matter(text)
    if meta is None:
        errors.append(ParseError(path=path, field=None, message="Missing or invalid YAML front matter"))
        raise ParseErrors(errors)

    if "name" not in meta:
        errors.append(ParseError(path=path, field="name", message="Required field 'name' is missing"))

    if as_project and "depends_on" in meta:
        errors.append(
            ParseError(path=path, field="depends_on", message="ProjectIntent cannot have 'depends_on'")
        )

    if errors:
        raise ParseErrors(errors)

    file_refs = extract_file_references(body)

    common = dict(
        name=meta["name"],
        tags=meta.get("tags", []),
        authors=meta.get("authors", []),
        body=body,
        file_references=file_refs,
        source_path=path,
    )

    if as_project:
        return ProjectIntent(**common)
    elif as_implementation:
        return Implementation(depends_on=meta.get("depends_on", []), **common)
    else:
        return IntentFile(depends_on=meta.get("depends_on", []), **common)


def parse_validation_file(path: Path) -> ValidationFile:
    """Parse a .icv file into a ValidationFile."""
    path = Path(path)
    errors: list[ParseError] = []

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ParseErrors([ParseError(path=path, field=None, message="File not found")])

    # Empty or whitespace-only .icv files are valid (no validations)
    if not text.strip():
        return ValidationFile(target="", validations=[], source_path=path)

    meta, _body = _split_front_matter(text)
    if meta is None:
        # .icv files may be plain YAML without front matter delimiters
        try:
            meta = yaml.safe_load(text)
        except yaml.YAMLError:
            meta = None
        if not isinstance(meta, dict):
            errors.append(ParseError(path=path, field=None, message="Missing or invalid YAML front matter"))
            raise ParseErrors(errors)

    if "target" not in meta:
        errors.append(ParseError(path=path, field="target", message="Required field 'target' is missing"))

    if "validations" not in meta or not isinstance(meta.get("validations"), list):
        errors.append(
            ParseError(path=path, field="validations", message="Required field 'validations' must be a list")
        )

    if errors:
        raise ParseErrors(errors)

    validations = []
    for i, v in enumerate(meta["validations"]):
        if not isinstance(v, dict):
            errors.append(
                ParseError(path=path, field=f"validations[{i}]", message="Validation entry must be a mapping")
            )
            continue
        if "name" not in v:
            errors.append(
                ParseError(path=path, field=f"validations[{i}].name", message="Required field 'name' is missing")
            )
            continue
        validations.append(
            Validation(
                name=v["name"],
                type=v.get("type", "agent_validation"),
                severity=v.get("severity", "error"),
                args=v.get("args", {}),
            )
        )

    if errors:
        raise ParseErrors(errors)

    return ValidationFile(
        target=meta["target"],
        agent_profile=meta.get("agent_profile"),
        validations=validations,
        source_path=path,
    )


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def write_intent_file(
    intent: IntentFile | ProjectIntent | Implementation,
    path: Path | None = None,
) -> Path:
    """Write an intent object to disk as a .ic file. Returns the path written."""
    path = Path(path) if path is not None else intent.source_path
    if path is None:
        raise ValueError("No path provided and source_path is not set")

    meta: dict = {"name": intent.name}

    if hasattr(intent, "depends_on") and intent.depends_on:
        meta["depends_on"] = intent.depends_on

    if intent.tags:
        meta["tags"] = intent.tags
    if intent.authors:
        meta["authors"] = intent.authors

    content = _build_front_matter(meta) + intent.body
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def write_validation_file(
    vf: ValidationFile,
    path: Path | None = None,
) -> Path:
    """Write a ValidationFile to disk as a .icv file. Returns the path written."""
    path = Path(path) if path is not None else vf.source_path
    if path is None:
        raise ValueError("No path provided and source_path is not set")

    meta: dict = {"target": vf.target}
    if vf.agent_profile is not None:
        meta["agent_profile"] = vf.agent_profile

    validations_out = []
    for v in vf.validations:
        entry: dict = {"name": v.name}
        if v.type != "agent_validation":
            entry["type"] = v.type
        if v.severity != "error":
            entry["severity"] = v.severity.value if hasattr(v.severity, "value") else v.severity
        if v.args:
            entry["args"] = v.args
        validations_out.append(entry)

    meta["validations"] = validations_out

    content = _build_front_matter(meta)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
