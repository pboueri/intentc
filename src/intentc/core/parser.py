"""Parse and write .ic (intent) and .icv (validation) files."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

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

# A relative path token in a markdown body: ./x, ../x, or dir/file.ext (with optional
# trailing glob). Deliberately conservative — only tokens with a directory separator.
_FILE_REF_RE = re.compile(
    r"(?<![\w.`/\[])"
    r"((?:\.{1,2}/)+[\w\-./*]+|[\w\-]+/[\w\-./*]+)"
)
_URL_LIKE = re.compile(r"^[a-z]+://", re.IGNORECASE)


def extract_file_references(text: str) -> list[str]:
    """Extract local file references (relative paths) from a markdown body.

    Returns deduplicated references in order of first appearance. Bare words,
    URLs and code identifiers are ignored; only tokens with a path separator
    that end in a file extension or a glob are considered references.
    """
    refs: list[str] = []
    seen: set[str] = set()
    for match in _FILE_REF_RE.finditer(text):
        token = match.group(1).rstrip(".,;:)")
        if _URL_LIKE.match(token):
            continue
        last = token.rsplit("/", 1)[-1]
        if not ("." in last or "*" in last) or last.startswith("."):
            continue
        if token not in seen:
            seen.add(token)
            refs.append(token)
    return refs


def content_hash(paths: list[Path]) -> str:
    """Stable SHA-256 over a list of files (sorted). Missing files add their name only."""
    digest = hashlib.sha256()
    for path in sorted(Path(p) for p in paths):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError:
            pass
        digest.update(b"\0")
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Intent files
# ---------------------------------------------------------------------------


def _split_frontmatter(text: str, path: Path) -> tuple[dict[str, Any], str]:
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        return {}, text.strip()
    rest = stripped[3:]
    end = re.search(r"^---[ \t]*$", rest, flags=re.MULTILINE)
    if end is None:
        raise ParseErrors([ParseError(path, "frontmatter opened with '---' but never closed")])
    yaml_block = rest[: end.start()]
    body = rest[end.end():].strip()
    try:
        meta = yaml.safe_load(yaml_block)
    except yaml.YAMLError as exc:
        raise ParseErrors([ParseError(path, f"invalid YAML frontmatter: {exc}")]) from exc
    if meta is None:
        meta = {}
    if not isinstance(meta, dict):
        raise ParseErrors([ParseError(path, "frontmatter must be a YAML mapping")])
    return meta, body


def _string_list(meta: dict[str, Any], key: str, path: Path, errors: list[ParseError]) -> list[str]:
    value = meta.get(key)
    if value is None:
        return []
    if isinstance(value, str):
        # A single bare string is tolerated and promoted to a one-element list.
        return [value]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        errors.append(ParseError(path, f"'{key}' must be a list of strings", field=key))
        return []
    return list(value)


def parse_intent_file(
    path: Path,
    as_project: bool = False,
    as_implementation: bool = False,
) -> IntentFile | ProjectIntent | Implementation:
    """Parse a ``.ic`` file. Raises ``ParseErrors`` with every problem found."""
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ParseErrors([ParseError(path, f"cannot read file: {exc}")]) from exc

    meta, body = _split_frontmatter(raw, path)
    errors: list[ParseError] = []

    name = meta.get("name")
    if name is None or (isinstance(name, str) and not name.strip()):
        errors.append(ParseError(path, "missing required field 'name' in frontmatter", field="name"))
        name = ""
    elif not isinstance(name, str):
        name = str(name)

    tags = _string_list(meta, "tags", path, errors)
    authors = _string_list(meta, "authors", path, errors)
    depends_on = _string_list(meta, "depends_on", path, errors)

    if as_project and "depends_on" in meta:
        errors.append(
            ParseError(path, "project.ic cannot declare 'depends_on' — remove the field", field="depends_on")
        )

    if errors:
        raise ParseErrors(errors)

    common: dict[str, Any] = dict(
        name=name,
        tags=tags,
        authors=authors,
        body=body,
        file_references=extract_file_references(body),
        source_path=path,
    )
    if as_project:
        return ProjectIntent(**common)
    common["depends_on"] = depends_on
    if as_implementation:
        return Implementation(**common)
    return IntentFile(**common)


# ---------------------------------------------------------------------------
# Validation files
# ---------------------------------------------------------------------------

_REQUIRED_ARGS: dict[str, tuple[str, str]] = {
    ValidationType.AGENT_VALIDATION.value: ("rubric", "a natural-language description of what to verify"),
    ValidationType.COMMAND_VALIDATION.value: ("command", "the shell command that must exit 0"),
    ValidationType.FILE_EXISTS.value: ("paths", "a non-empty list of paths or globs"),
}


def _parse_validation_entry(
    index: int, entry: Any, path: Path, errors: list[ParseError]
) -> Validation | None:
    prefix = f"validations[{index}]"
    if not isinstance(entry, dict):
        errors.append(ParseError(path, "validation entry must be a mapping", field=prefix))
        return None

    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append(ParseError(path, "validation entry is missing a 'name'", field=f"{prefix}.name"))
        name = ""

    vtype = entry.get("type", ValidationType.AGENT_VALIDATION.value)
    if not isinstance(vtype, str) or not vtype:
        errors.append(ParseError(path, "'type' must be a non-empty string", field=f"{prefix}.type"))
        vtype = ValidationType.AGENT_VALIDATION.value

    raw_sev = entry.get("severity", Severity.ERROR.value)
    try:
        severity = Severity(raw_sev)
    except ValueError:
        errors.append(
            ParseError(
                path,
                f"unknown severity '{raw_sev}' — use 'error' or 'warning'",
                field=f"{prefix}.severity",
            )
        )
        severity = Severity.ERROR

    args = entry.get("args", {})
    if args is None:
        args = {}
    if not isinstance(args, dict):
        errors.append(ParseError(path, "'args' must be a mapping", field=f"{prefix}.args"))
        args = {}

    required = _REQUIRED_ARGS.get(vtype)
    if required is not None:
        key, description = required
        value = args.get(key)
        missing = value is None or (isinstance(value, (str, list)) and len(value) == 0)
        if missing:
            errors.append(
                ParseError(
                    path,
                    f"'{vtype}' requires args.{key} ({description})",
                    field=f"{prefix}.args.{key}",
                )
            )
        elif key == "paths" and not isinstance(value, list):
            errors.append(
                ParseError(path, "args.paths must be a list of strings", field=f"{prefix}.args.paths")
            )

    return Validation(name=name, type=vtype, severity=severity, args=dict(args))


def parse_validation_file(path: Path) -> ValidationFile:
    """Parse a ``.icv`` file (pure YAML). An empty file is a valid, empty ValidationFile."""
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ParseErrors([ParseError(path, f"cannot read file: {exc}")]) from exc

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ParseErrors([ParseError(path, f"invalid YAML: {exc}")]) from exc

    if data is None:
        return ValidationFile(source_path=path)
    if not isinstance(data, dict):
        raise ParseErrors([ParseError(path, "expected a YAML mapping at the top level")])

    errors: list[ParseError] = []
    target = data.get("target", "") or ""
    if not isinstance(target, str):
        errors.append(ParseError(path, "'target' must be a string", field="target"))
        target = str(target)

    version = data.get("version", 1)
    if not isinstance(version, int):
        errors.append(ParseError(path, "'version' must be an integer", field="version"))
        version = 1

    agent_profile = data.get("agent_profile")
    if agent_profile is not None and not isinstance(agent_profile, str):
        errors.append(ParseError(path, "'agent_profile' must be a string", field="agent_profile"))
        agent_profile = None

    validations: list[Validation] = []
    raw_validations = data.get("validations", [])
    if raw_validations is None:
        raw_validations = []
    if not isinstance(raw_validations, list):
        errors.append(ParseError(path, "'validations' must be a list", field="validations"))
        raw_validations = []

    seen_names: set[str] = set()
    for index, entry in enumerate(raw_validations):
        validation = _parse_validation_entry(index, entry, path, errors)
        if validation is None:
            continue
        if validation.name:
            if validation.name in seen_names:
                errors.append(
                    ParseError(
                        path,
                        f"duplicate validation name '{validation.name}' — names must be unique within a file",
                        field=f"validations[{index}].name",
                    )
                )
            seen_names.add(validation.name)
        validations.append(validation)

    if errors:
        raise ParseErrors(errors)

    return ValidationFile(
        target=target,
        version=version,
        agent_profile=agent_profile,
        validations=validations,
        source_path=path,
    )


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


def _frontmatter(intent: IntentFile | ProjectIntent | Implementation) -> str:
    meta: dict[str, Any] = {"name": intent.name}
    depends_on = getattr(intent, "depends_on", None)
    if depends_on:
        meta["depends_on"] = list(depends_on)
    if intent.tags:
        meta["tags"] = list(intent.tags)
    if intent.authors:
        meta["authors"] = list(intent.authors)
    yaml_text = yaml.safe_dump(meta, default_flow_style=False, sort_keys=False).strip()
    parts = ["---", yaml_text, "---"]
    if intent.body:
        parts.extend(["", intent.body])
    return "\n".join(parts) + "\n"


def write_intent_file(
    intent: IntentFile | ProjectIntent | Implementation,
    path: Path | None = None,
) -> Path:
    """Write an intent to ``path`` (default: its ``source_path``). Returns the path."""
    out = Path(path) if path is not None else intent.source_path
    if out is None:
        raise ValueError("write_intent_file: no path given and the intent has no source_path")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_frontmatter(intent), encoding="utf-8")
    return out


def write_validation_file(vf: ValidationFile, path: Path | None = None) -> Path:
    """Write a ValidationFile as pure YAML to ``path`` (default: ``source_path``)."""
    out = Path(path) if path is not None else vf.source_path
    if out is None:
        raise ValueError("write_validation_file: no path given and the file has no source_path")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    data: dict[str, Any] = {}
    if vf.target:
        data["target"] = vf.target
    data["version"] = vf.version
    if vf.agent_profile is not None:
        data["agent_profile"] = vf.agent_profile
    data["validations"] = [
        {
            "name": v.name,
            "type": v.type,
            "severity": v.severity.value,
            "args": dict(v.args),
        }
        for v in vf.validations
    ]
    out.write_text(yaml.safe_dump(data, default_flow_style=False, sort_keys=False), encoding="utf-8")
    return out
