from __future__ import annotations

from pathlib import Path
from typing import Union

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


def _split_frontmatter(text: str) -> tuple[dict, str]:
    text = text.strip()
    if not text.startswith("---"):
        return {}, text

    # Find the closing ---
    end = text.find("---", 3)
    if end == -1:
        return {}, text

    yaml_block = text[3:end].strip()
    body = text[end + 3:].strip()

    meta = yaml.safe_load(yaml_block) or {}
    return meta, body


def parse_intent_file(
    path: Path,
    as_project: bool = False,
    as_implementation: bool = False,
) -> Union[IntentFile, ProjectIntent, Implementation]:
    path = Path(path)
    errors: list[ParseError] = []

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ParseErrors([ParseError(path=path, field=None, message="File not found")])

    meta, body = _split_frontmatter(text)

    if not isinstance(meta, dict):
        raise ParseErrors([ParseError(path=path, field=None, message="Frontmatter is not a valid YAML mapping")])

    name = meta.get("name")
    if not name:
        errors.append(ParseError(path=path, field="name", message="Missing required field 'name'"))

    if as_project and "depends_on" in meta:
        errors.append(ParseError(path=path, field="depends_on", message="ProjectIntent cannot have 'depends_on'"))

    if errors:
        raise ParseErrors(errors)

    file_references = extract_file_references(body)

    common = dict(
        name=name,
        tags=meta.get("tags", []),
        authors=meta.get("authors", []),
        body=body,
        file_references=file_references,
        source_path=path,
    )

    if as_project:
        return ProjectIntent(**common)
    elif as_implementation:
        return Implementation(**common)
    else:
        return IntentFile(
            depends_on=meta.get("depends_on", []),
            **common,
        )


def parse_validation_file(path: Path) -> ValidationFile:
    path = Path(path)
    errors: list[ParseError] = []

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ParseErrors([ParseError(path=path, field=None, message="File not found")])

    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ParseErrors([ParseError(path=path, field=None, message="File is not a valid YAML mapping")])

    target = data.get("target")
    if not target:
        errors.append(ParseError(path=path, field="target", message="Missing required field 'target'"))

    if errors:
        raise ParseErrors(errors)

    validations = []
    for v in data.get("validations", []):
        validations.append(Validation(
            name=v["name"],
            type=v.get("type", "agent_validation"),
            severity=v.get("severity", "error"),
            args=v.get("args", {}),
        ))

    return ValidationFile(
        target=target,
        agent_profile=data.get("agent_profile"),
        validations=validations,
        source_path=path,
    )


def _intent_to_frontmatter(intent: Union[IntentFile, ProjectIntent, Implementation]) -> dict:
    meta: dict = {"name": intent.name}
    if isinstance(intent, IntentFile) and intent.depends_on:
        meta["depends_on"] = intent.depends_on
    if intent.tags:
        meta["tags"] = intent.tags
    if intent.authors:
        meta["authors"] = intent.authors
    return meta


def write_intent_file(
    intent: Union[IntentFile, ProjectIntent, Implementation],
    path: Path | None = None,
) -> Path:
    path = Path(path) if path is not None else intent.source_path
    if path is None:
        raise ValueError("No path provided and intent has no source_path")

    meta = _intent_to_frontmatter(intent)
    yaml_str = yaml.dump(meta, default_flow_style=True).strip()

    parts = ["---", yaml_str, "---"]
    if intent.body:
        parts.append("")
        parts.append(intent.body)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return path


def write_validation_file(
    vf: ValidationFile,
    path: Path | None = None,
) -> Path:
    path = Path(path) if path is not None else vf.source_path
    if path is None:
        raise ValueError("No path provided and validation file has no source_path")

    data: dict = {"target": vf.target}
    if vf.agent_profile is not None:
        data["agent_profile"] = vf.agent_profile
    data["validations"] = [
        {
            "name": v.name,
            "type": v.type,
            "severity": v.severity.value,
            "args": v.args,
        }
        for v in vf.validations
    ]

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False), encoding="utf-8")
    return path
