"""Parsing and writing of `.ic` and `.icv` files."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Optional, Union

import yaml

from intentc.core.models import (
    Artifact,
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

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?\n)---\s*\n?(.*)\Z", re.DOTALL)

_MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_BACKTICK_RE = re.compile(r"`([^`]+)`")
# Bare words are candidates only when they contain a path separator (or start with ./ or ../).
_BARE_FILE_RE = re.compile(
    r"(?<![\w/.\-\[`])"
    r"((?:\.{1,2}/)+[\w.\-/*]+|[\w.\-]+(?:/[\w.\-*]+)+)"
    r"(?![\w])"
)
_URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")
_TRAILING_PUNCT = ".,;:)"


def extract_file_references(body: str) -> list[str]:
    """Extract local file references (paths/globs) mentioned in an intent body."""
    refs: list[str] = []
    seen: set[str] = set()

    def add(candidate: str) -> None:
        candidate = candidate.strip().rstrip(_TRAILING_PUNCT)
        if not candidate or candidate in seen:
            return
        if _URL_RE.match(candidate):
            return
        if not _looks_like_file_reference(candidate):
            return
        seen.add(candidate)
        refs.append(candidate)

    for match in _MD_LINK_RE.finditer(body):
        add(match.group(1))
    for match in _BACKTICK_RE.finditer(body):
        add(match.group(1))
    for match in _BARE_FILE_RE.finditer(body):
        add(match.group(1))

    return refs


def _looks_like_file_reference(candidate: str) -> bool:
    """A path with a separator whose last segment has an extension or is a glob."""
    if any(ch.isspace() for ch in candidate):
        return False
    if "/" not in candidate and not candidate.startswith(("./", "../")):
        return False
    last = candidate.rstrip("/").rsplit("/", 1)[-1]
    if last.endswith("*"):
        return True
    name, dot, ext = last.rpartition(".")
    return bool(dot) and bool(name) and ext.isalnum() and 1 <= len(ext) <= 6


def _split_frontmatter(path: Path, text: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise ParseErrors(
            [ParseError(path, None, "missing YAML frontmatter delimited by '---' blocks")]
        )
    raw_yaml, body = match.group(1), match.group(2)
    try:
        data = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as exc:
        raise ParseErrors(
            [ParseError(path, None, f"invalid YAML frontmatter: {exc}")]
        ) from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ParseErrors(
            [ParseError(path, None, "frontmatter must be a YAML mapping")]
        )
    return data, body


def _validate_str_list(
    data: dict[str, Any], field: str, path: Path, errors: list[ParseError]
) -> list[str]:
    value = data.get(field)
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        errors.append(ParseError(path, field, "must be a list of strings"))
        return []
    return value


def _parse_artifacts(
    data: dict[str, Any], path: Path, errors: list[ParseError]
) -> list[Artifact]:
    raw = data.get("artifacts")
    if raw is None:
        return []
    if not isinstance(raw, list):
        errors.append(ParseError(path, "artifacts", "must be a list"))
        return []

    artifacts: list[Artifact] = []
    for idx, entry in enumerate(raw):
        prefix = f"artifacts[{idx}]"
        if isinstance(entry, str):
            artifacts.append(Artifact(path=entry))
            continue
        if not isinstance(entry, dict):
            errors.append(ParseError(path, prefix, "must be a string or a mapping"))
            continue

        entry_path = entry.get("path")
        if not entry_path or not isinstance(entry_path, str):
            errors.append(ParseError(path, f"{prefix}.path", "missing or empty required field"))
            continue

        kind = entry.get("kind", "reference")
        if not isinstance(kind, str):
            errors.append(ParseError(path, f"{prefix}.kind", "must be a string"))
            continue

        note = entry.get("note", "")
        if not isinstance(note, str):
            errors.append(ParseError(path, f"{prefix}.note", "must be a string"))
            continue

        artifacts.append(Artifact(path=entry_path, kind=kind, note=note))

    return artifacts


def _dedup_artifacts_by_path(artifacts: list[Artifact]) -> list[Artifact]:
    seen: set[str] = set()
    result: list[Artifact] = []
    for artifact in artifacts:
        if artifact.path in seen:
            continue
        seen.add(artifact.path)
        result.append(artifact)
    return result


def parse_intent_file(
    path: Union[str, Path],
    as_project: bool = False,
    as_implementation: bool = False,
) -> Union[IntentFile, ProjectIntent, Implementation]:
    """Parse a `.ic` file into an IntentFile, ProjectIntent, or Implementation."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    data, body = _split_frontmatter(path, text)

    errors: list[ParseError] = []

    name = data.get("name")
    if not name or not isinstance(name, str):
        errors.append(ParseError(path, "name", "missing or empty required field"))

    if as_project and "depends_on" in data:
        errors.append(ParseError(path, "depends_on", "project.ic cannot declare depends_on"))

    depends_on: list[str] = []
    if not as_project:
        depends_on = _validate_str_list(data, "depends_on", path, errors)

    tags = _validate_str_list(data, "tags", path, errors)
    authors = _validate_str_list(data, "authors", path, errors)
    declared_artifacts = _parse_artifacts(data, path, errors)

    if errors:
        raise ParseErrors(errors)

    file_references = extract_file_references(body)
    inline_artifacts = [Artifact(path=ref) for ref in file_references]
    artifacts = _dedup_artifacts_by_path(declared_artifacts + inline_artifacts)

    common: dict[str, Any] = dict(
        name=name,
        tags=tags,
        authors=authors,
        body=body,
        file_references=file_references,
        artifacts=artifacts,
        source_path=path,
    )

    if as_project:
        return ProjectIntent(**common)
    if as_implementation:
        return Implementation(depends_on=depends_on, **common)
    return IntentFile(depends_on=depends_on, **common)


def parse_validation_file(path: Union[str, Path]) -> ValidationFile:
    """Parse a pure-YAML `.icv` file into a ValidationFile."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ParseErrors([ParseError(path, None, f"invalid YAML: {exc}")]) from exc

    if data is None:
        return ValidationFile(target="", version=1, agent_profile=None, validations=[], source_path=path)

    if not isinstance(data, dict):
        raise ParseErrors([ParseError(path, None, "top-level content must be a YAML mapping")])

    errors: list[ParseError] = []

    target = data.get("target", "") or ""
    version = data.get("version", 1)
    if not isinstance(version, int):
        version = 1
    agent_profile = data.get("agent_profile")

    raw_validations = data.get("validations")
    if raw_validations is None:
        raw_validations = []
    elif not isinstance(raw_validations, list):
        errors.append(ParseError(path, "validations", "must be a list"))
        raw_validations = []

    validations: list[Validation] = []
    seen_names: set[str] = set()

    for idx, entry in enumerate(raw_validations):
        prefix = f"validations[{idx}]"

        if not isinstance(entry, dict):
            errors.append(ParseError(path, prefix, "must be a mapping"))
            continue

        name = entry.get("name")
        name_ok = bool(name) and isinstance(name, str)
        if not name_ok:
            errors.append(ParseError(path, f"{prefix}.name", "missing or empty required field"))
        elif name in seen_names:
            errors.append(ParseError(path, f"{prefix}.name", f"duplicate validation name '{name}'"))
        else:
            seen_names.add(name)

        vtype = entry.get("type") or ValidationType.AGENT_VALIDATION.value

        severity_raw = entry.get("severity", Severity.ERROR.value)
        try:
            severity: Optional[Severity] = Severity(severity_raw)
        except ValueError:
            errors.append(ParseError(path, f"{prefix}.severity", f"unknown severity '{severity_raw}'"))
            severity = None

        args = entry.get("args", {})
        if args is None:
            args = {}
        if not isinstance(args, dict):
            errors.append(ParseError(path, f"{prefix}.args", "must be a mapping"))
            args = {}

        if vtype == ValidationType.AGENT_VALIDATION.value:
            if not args.get("rubric"):
                errors.append(
                    ParseError(path, f"{prefix}.args.rubric", "agent_validation requires args.rubric")
                )
        elif vtype == ValidationType.COMMAND_VALIDATION.value:
            if not args.get("command"):
                errors.append(
                    ParseError(path, f"{prefix}.args.command", "command_validation requires args.command")
                )
        elif vtype == ValidationType.FILE_EXISTS.value:
            paths = args.get("paths")
            if not paths or not isinstance(paths, list):
                errors.append(
                    ParseError(
                        path, f"{prefix}.args.paths", "file_exists requires a non-empty args.paths list"
                    )
                )

        if not name_ok or severity is None:
            continue

        validations.append(Validation(name=name, type=vtype, severity=severity, args=args))

    if errors:
        raise ParseErrors(errors)

    return ValidationFile(
        target=target,
        version=version,
        agent_profile=agent_profile,
        validations=validations,
        source_path=path,
    )


def _artifact_to_dict(artifact: Artifact) -> dict[str, Any]:
    data: dict[str, Any] = {"path": artifact.path}
    if artifact.kind != "reference":
        data["kind"] = artifact.kind
    if artifact.note:
        data["note"] = artifact.note
    return data


def write_intent_file(
    intent: Union[IntentFile, ProjectIntent, Implementation],
    path: Optional[Union[str, Path]] = None,
) -> Path:
    """Write an intent object back to disk as a `.ic` file, returning the path written."""
    target_path = Path(path) if path is not None else intent.source_path
    if target_path is None:
        raise ValueError("path must be provided when intent.source_path is None")

    data: dict[str, Any] = {"name": intent.name}
    if isinstance(intent, (IntentFile, Implementation)):
        data["depends_on"] = intent.depends_on
    data["tags"] = intent.tags
    data["authors"] = intent.authors

    declared_artifacts = [a for a in intent.artifacts if a.path not in intent.file_references]
    if declared_artifacts:
        data["artifacts"] = [_artifact_to_dict(a) for a in declared_artifacts]

    frontmatter = yaml.safe_dump(data, sort_keys=False)
    text = f"---\n{frontmatter}---\n{intent.body}"

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(text, encoding="utf-8")
    return target_path


def write_validation_file(vf: ValidationFile, path: Optional[Union[str, Path]] = None) -> Path:
    """Write a ValidationFile back to disk as pure-YAML `.icv`, returning the path written."""
    target_path = Path(path) if path is not None else vf.source_path
    if target_path is None:
        raise ValueError("path must be provided when vf.source_path is None")

    data: dict[str, Any] = {"target": vf.target, "version": vf.version}
    if vf.agent_profile is not None:
        data["agent_profile"] = vf.agent_profile
    data["validations"] = [
        {
            "name": v.name,
            "type": v.type,
            "severity": v.severity.value if isinstance(v.severity, Severity) else v.severity,
            "args": v.args,
        }
        for v in vf.validations
    ]

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return target_path


def content_hash(paths: list[Union[str, Path]]) -> str:
    """Stable SHA-256 hex digest over a sorted list of files (name + bytes; name only if missing)."""
    digest = hashlib.sha256()
    for raw_path in sorted(paths, key=str):
        p = Path(raw_path)
        digest.update(p.name.encode("utf-8"))
        try:
            digest.update(p.read_bytes())
        except FileNotFoundError:
            pass
    return digest.hexdigest()
