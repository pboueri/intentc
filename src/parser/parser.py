"""Parser implementation for .ic and .icv intent files.

Reads YAML frontmatter, parses into core types, provides schema validation
and target registry discovery.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from core.types import (
    Intent,
    SchemaViolation,
    Target,
    Validation,
    ValidationFile,
    ValidationType,
)

# Known frontmatter fields for each file type.
_KNOWN_INTENT_FIELDS = {"name", "version", "depends_on", "tags", "profile"}
_KNOWN_VALIDATION_FILE_FIELDS = {"target", "version", "judge_profile", "validations"}
_KNOWN_VALIDATION_ENTRY_FIELDS = {
    "name",
    "type",
    "hidden",
    "path",
    "contains",
    "children",
    "command",
    "working_dir",
    "exit_code",
    "stdout_contains",
    "stderr_contains",
    "rubric",
    "severity",
    "context_files",
}

# Supported schema versions.
_SUPPORTED_VERSIONS = {1}

# Fields that belong to the Validation model itself (not parameters).
_VALIDATION_CORE_FIELDS = {"name", "type", "hidden"}


def expand_dependency_globs(
    deps: list[str], known_targets: set[str]
) -> list[str]:
    """Expand wildcard patterns in dependency lists.

    Supports:
      - "module/*" -> all targets whose parent module is "module"
        (direct children only, not grandchildren)
      - Literal names are passed through unchanged.

    Returns a deduplicated list preserving order of first occurrence.
    """
    result: list[str] = []
    seen: set[str] = set()

    for dep in deps:
        if dep.endswith("/*"):
            prefix = dep[:-2]  # e.g., "core" from "core/*"
            for name in sorted(known_targets):
                # Direct child: starts with prefix/ and has no further /
                if name.startswith(prefix + "/"):
                    remainder = name[len(prefix) + 1 :]
                    if "/" not in remainder and name not in seen:
                        seen.add(name)
                        result.append(name)
        else:
            if dep not in seen:
                seen.add(dep)
                result.append(dep)

    return result


def _split_frontmatter(text: str, file_path: str) -> tuple[str, str]:
    """Split a file's text into YAML frontmatter and markdown body.

    The file must start with a line that is exactly '---'. The frontmatter
    ends at the next '---' line. Everything after the closing delimiter is
    the body.

    Returns:
        (frontmatter_yaml, body)

    Raises:
        ValueError: If no valid frontmatter delimiters are found.
    """
    lines = text.split("\n")

    # First line must be '---'
    if not lines or lines[0].strip() != "---":
        raise ValueError(
            f"parser: {file_path}: missing frontmatter (file must start with '---')"
        )

    # Find the closing '---'
    closing_index = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            closing_index = i
            break

    if closing_index is None:
        raise ValueError(
            f"parser: {file_path}: missing closing '---' delimiter for frontmatter"
        )

    frontmatter = "\n".join(lines[1:closing_index])
    body = "\n".join(lines[closing_index + 1 :])

    # Strip a single leading newline from body if present (common formatting).
    if body.startswith("\n"):
        body = body[1:]

    return frontmatter, body


def ParseIntentFile(file_path: str) -> Intent:
    """Parse a .ic intent file into an Intent object.

    Args:
        file_path: Path to the .ic file.

    Returns:
        Parsed Intent with content and file_path set.

    Raises:
        ValueError: If the file cannot be parsed or required fields are missing.
        FileNotFoundError: If the file does not exist.
    """
    abs_path = os.path.abspath(file_path)
    rel_path = file_path  # Keep original for error messages.

    with open(abs_path, "r", encoding="utf-8") as f:
        text = f.read()

    frontmatter_yaml, body = _split_frontmatter(text, rel_path)

    try:
        data = yaml.safe_load(frontmatter_yaml)
    except yaml.YAMLError as e:
        raise ValueError(f"parser: {rel_path}: invalid YAML in frontmatter: {e}")

    if data is None:
        data = {}

    if not isinstance(data, dict):
        raise ValueError(
            f"parser: {rel_path}: frontmatter must be a YAML mapping, got {type(data).__name__}"
        )

    # Validate required fields.
    if "name" not in data or not data["name"]:
        raise ValueError(
            f"parser: {rel_path}: missing required field 'name' in frontmatter"
        )

    if "version" not in data:
        raise ValueError(
            f"parser: {rel_path}: missing required field 'version' in frontmatter"
        )

    intent = Intent(
        name=str(data.get("name", "")),
        version=int(data.get("version", 1)),
        depends_on=data.get("depends_on", []) or [],
        tags=data.get("tags", []) or [],
        profile=str(data.get("profile", "") or ""),
        content=body,
        file_path=abs_path,
    )

    return intent


def ParseValidationFile(file_path: str) -> ValidationFile:
    """Parse a .icv validation file into a ValidationFile object.

    Args:
        file_path: Path to the .icv file.

    Returns:
        Parsed ValidationFile with file_path set.

    Raises:
        ValueError: If the file cannot be parsed or required fields are missing.
        FileNotFoundError: If the file does not exist.
    """
    abs_path = os.path.abspath(file_path)
    rel_path = file_path

    with open(abs_path, "r", encoding="utf-8") as f:
        text = f.read()

    frontmatter_yaml, _ = _split_frontmatter(text, rel_path)

    try:
        data = yaml.safe_load(frontmatter_yaml)
    except yaml.YAMLError as e:
        raise ValueError(f"parser: {rel_path}: invalid YAML in frontmatter: {e}")

    if data is None:
        data = {}

    if not isinstance(data, dict):
        raise ValueError(
            f"parser: {rel_path}: frontmatter must be a YAML mapping, got {type(data).__name__}"
        )

    # Validate required fields.
    if "target" not in data or not data["target"]:
        raise ValueError(
            f"parser: {rel_path}: missing required field 'target' in frontmatter"
        )

    if "version" not in data:
        raise ValueError(
            f"parser: {rel_path}: missing required field 'version' in frontmatter"
        )

    raw_validations = data.get("validations", [])
    if not isinstance(raw_validations, list):
        raise ValueError(
            f"parser: {rel_path}: 'validations' must be a list"
        )

    # Valid type values for lookup.
    valid_types = {vt.value for vt in ValidationType}

    validations: list[Validation] = []
    for i, raw_v in enumerate(raw_validations):
        if not isinstance(raw_v, dict):
            raise ValueError(
                f"parser: {rel_path}: validation[{i}]: must be a mapping"
            )

        v_name = raw_v.get("name")
        if not v_name:
            raise ValueError(
                f"parser: {rel_path}: validation[{i}]: missing required field 'name'"
            )

        v_type_str = raw_v.get("type")
        if not v_type_str:
            raise ValueError(
                f"parser: {rel_path}: validation[{i}]: missing required field 'type'"
            )

        if v_type_str not in valid_types:
            raise ValueError(
                f"parser: {rel_path}: validation[{i}]: unknown type '{v_type_str}'"
            )

        v_type = ValidationType(v_type_str)
        v_hidden = bool(raw_v.get("hidden", False))

        # Collect type-specific fields into parameters.
        parameters: dict[str, Any] = {}
        for key, value in raw_v.items():
            if key not in _VALIDATION_CORE_FIELDS:
                parameters[key] = value

        validations.append(
            Validation(
                name=str(v_name),
                type=v_type,
                hidden=v_hidden,
                parameters=parameters,
            )
        )

    vf = ValidationFile(
        target=str(data.get("target", "")),
        version=int(data.get("version", 1)),
        judge_profile=str(data.get("judge_profile", "") or ""),
        validations=validations,
        file_path=abs_path,
    )

    return vf


class TargetRegistry:
    """Discovers and caches all targets in a project's intent/ directory.

    Usage:
        registry = TargetRegistry(project_root="/path/to/project")
        registry.load_targets()
        target = registry.get_target("auth")
    """

    def __init__(self, project_root: str) -> None:
        self._project_root = os.path.abspath(project_root)
        self._intent_dir = os.path.join(self._project_root, "intent")
        self._targets: dict[str, Target] = {}
        self._project_intent: Intent | None = None

    @property
    def project_root(self) -> str:
        return self._project_root

    def load_targets(self) -> None:
        """Walk the intent/ directory, parse all .ic and .icv files, build target map.

        Recursively discovers targets at any nesting depth. The target name is
        derived from the relative path from intent/ to the directory containing
        the .ic file, using '/' as separator:
          - intent/core/core.ic -> name "core"
          - intent/core/parser/parser.ic -> name "core/parser"

        Directories that contain no .ic file are pure module containers and
        are skipped.

        Raises:
            FileNotFoundError: If intent/ directory does not exist.
            ValueError: If any spec file has parse errors.
        """
        if not os.path.isdir(self._intent_dir):
            raise FileNotFoundError(
                f"parser: intent directory not found: {self._intent_dir}"
            )

        self._targets.clear()
        self._project_intent = None

        project_ic_path = os.path.join(self._intent_dir, "project.ic")
        if os.path.isfile(project_ic_path):
            self._project_intent = ParseIntentFile(project_ic_path)

        for dirpath, dirnames, filenames in os.walk(self._intent_dir):
            if os.path.abspath(dirpath) == os.path.abspath(self._intent_dir):
                continue

            ic_files = sorted(f for f in filenames if f.endswith(".ic"))
            if not ic_files:
                continue

            rel_path = os.path.relpath(dirpath, self._intent_dir)
            target_name = rel_path.replace(os.sep, "/")

            ic_path = os.path.join(dirpath, ic_files[0])
            intent = ParseIntentFile(ic_path)

            icv_files = sorted(f for f in filenames if f.endswith(".icv"))
            validation_files: list[ValidationFile] = []
            for icv_file in icv_files:
                icv_path = os.path.join(dirpath, icv_file)
                vf = ParseValidationFile(icv_path)
                validation_files.append(vf)

            target = Target(
                name=target_name,
                intent=intent,
                validations=validation_files,
            )
            self._targets[target_name] = target

    def get_target(self, name: str) -> Target:
        """Look up a target by name.

        Raises:
            KeyError: If the target is not found.
        """
        if name not in self._targets:
            raise KeyError(f"parser: target not found: '{name}'")
        return self._targets[name]

    def get_all_targets(self) -> list[Target]:
        """Return all discovered targets (sorted by name)."""
        return [self._targets[k] for k in sorted(self._targets)]

    def get_project_intent(self) -> Intent:
        """Return the project-level intent (intent/project.ic).

        Raises:
            FileNotFoundError: If project.ic was not found.
        """
        if self._project_intent is None:
            raise FileNotFoundError(
                "parser: project intent not found (expected intent/project.ic)"
            )
        return self._project_intent


# ---------------------------------------------------------------------------
# Schema Validation
# ---------------------------------------------------------------------------


def validate_intent_schema(intent: Intent) -> list[SchemaViolation]:
    """Validate a parsed Intent against the .ic schema rules.

    Returns a list of SchemaViolation objects (empty if valid).
    """
    violations: list[SchemaViolation] = []
    fp = intent.file_path

    # name required and non-empty.
    if not intent.name:
        violations.append(
            SchemaViolation(
                file_path=fp,
                field="name",
                message=f"parser: {fp}: missing required field 'name' in frontmatter",
                severity="error",
            )
        )

    # version required, positive integer, supported.
    if intent.version <= 0:
        violations.append(
            SchemaViolation(
                file_path=fp,
                field="version",
                message=f"parser: {fp}: 'version' must be a positive integer, got {intent.version}",
                severity="error",
            )
        )
    elif intent.version not in _SUPPORTED_VERSIONS:
        violations.append(
            SchemaViolation(
                file_path=fp,
                field="version",
                message=f"parser: {fp}: unsupported version {intent.version} (supported: {sorted(_SUPPORTED_VERSIONS)})",
                severity="error",
            )
        )

    # depends_on entries non-empty strings.
    for i, dep in enumerate(intent.depends_on):
        if not dep or not dep.strip():
            violations.append(
                SchemaViolation(
                    file_path=fp,
                    field=f"depends_on[{i}]",
                    message=f"parser: {fp}: depends_on[{i}] must be a non-empty string",
                    severity="error",
                )
            )

    # tags entries non-empty strings.
    for i, tag in enumerate(intent.tags):
        if not tag or not tag.strip():
            violations.append(
                SchemaViolation(
                    file_path=fp,
                    field=f"tags[{i}]",
                    message=f"parser: {fp}: tags[{i}] must be a non-empty string",
                    severity="error",
                )
            )

    # profile if present non-empty.
    # Note: intent.profile defaults to "" which means "not set", so we only
    # flag it if it was explicitly set to a whitespace-only value.  Since the
    # parser already coerces to str, an all-whitespace profile is the only bad
    # case we can catch here.  A truly empty string means "not present".
    # We treat whitespace-only as invalid when the field is non-empty.
    if intent.profile and not intent.profile.strip():
        violations.append(
            SchemaViolation(
                file_path=fp,
                field="profile",
                message=f"parser: {fp}: 'profile' must be a non-empty string if present",
                severity="error",
            )
        )

    return violations


def _check_unknown_fields(
    data: dict[str, Any],
    known: set[str],
    file_path: str,
) -> list[SchemaViolation]:
    """Warn on unknown fields in a frontmatter mapping."""
    violations: list[SchemaViolation] = []
    for key in data:
        if key not in known:
            violations.append(
                SchemaViolation(
                    file_path=file_path,
                    field=key,
                    message=f"parser: {file_path}: unknown field '{key}' in frontmatter",
                    severity="warning",
                )
            )
    return violations


def validate_project_intent(intent: Intent) -> list[SchemaViolation]:
    """Validate the project-level intent (intent/project.ic).

    Includes all checks from validate_intent_schema plus project-specific rules.
    """
    violations = validate_intent_schema(intent)
    fp = intent.file_path

    # project intent must not have depends_on.
    if intent.depends_on:
        violations.append(
            SchemaViolation(
                file_path=fp,
                field="depends_on",
                message=f"parser: {fp}: project intent must not have 'depends_on'",
                severity="error",
            )
        )

    return violations


def validate_validation_schema(vf: ValidationFile) -> list[SchemaViolation]:
    """Validate a parsed ValidationFile against the .icv schema rules.

    Returns a list of SchemaViolation objects (empty if valid).
    """
    violations: list[SchemaViolation] = []
    fp = vf.file_path

    # target required and non-empty.
    if not vf.target:
        violations.append(
            SchemaViolation(
                file_path=fp,
                field="target",
                message=f"parser: {fp}: missing required field 'target' in frontmatter",
                severity="error",
            )
        )

    # version required, positive integer.
    if vf.version <= 0:
        violations.append(
            SchemaViolation(
                file_path=fp,
                field="version",
                message=f"parser: {fp}: 'version' must be a positive integer, got {vf.version}",
                severity="error",
            )
        )

    # judge_profile if present non-empty.
    if vf.judge_profile and not vf.judge_profile.strip():
        violations.append(
            SchemaViolation(
                file_path=fp,
                field="judge_profile",
                message=f"parser: {fp}: 'judge_profile' must be a non-empty string if present",
                severity="error",
            )
        )

    # validations list required and non-empty.
    if not vf.validations:
        violations.append(
            SchemaViolation(
                file_path=fp,
                field="validations",
                message=f"parser: {fp}: 'validations' list must be non-empty",
                severity="error",
            )
        )

    # Validate each validation entry.
    valid_types = {vt.value for vt in ValidationType}
    seen_names: set[str] = set()

    for i, v in enumerate(vf.validations):
        prefix = f"validations[{i}]"

        # name required and non-empty.
        if not v.name:
            violations.append(
                SchemaViolation(
                    file_path=fp,
                    field=f"{prefix}.name",
                    message=f"parser: {fp}: validation[{i}]: missing required field 'name'",
                    severity="error",
                )
            )

        # type required and valid.
        if v.type.value not in valid_types:
            violations.append(
                SchemaViolation(
                    file_path=fp,
                    field=f"{prefix}.type",
                    message=f"parser: {fp}: validation[{i}]: unknown type '{v.type.value}'",
                    severity="error",
                )
            )

        # Type-specific required fields.
        params = v.parameters

        if v.type == ValidationType.FILE_CHECK:
            if "path" not in params or not params["path"]:
                violations.append(
                    SchemaViolation(
                        file_path=fp,
                        field=f"{prefix}.path",
                        message=f"parser: {fp}: validation[{i}]: file_check requires 'path'",
                        severity="error",
                    )
                )

        elif v.type == ValidationType.FOLDER_CHECK:
            if "path" not in params or not params["path"]:
                violations.append(
                    SchemaViolation(
                        file_path=fp,
                        field=f"{prefix}.path",
                        message=f"parser: {fp}: validation[{i}]: folder_check requires 'path'",
                        severity="error",
                    )
                )

        elif v.type == ValidationType.COMMAND_CHECK:
            if "command" not in params or not params["command"]:
                violations.append(
                    SchemaViolation(
                        file_path=fp,
                        field=f"{prefix}.command",
                        message=f"parser: {fp}: validation[{i}]: command_check requires 'command'",
                        severity="error",
                    )
                )

        elif v.type == ValidationType.LLM_JUDGE:
            if "rubric" not in params or not params["rubric"]:
                violations.append(
                    SchemaViolation(
                        file_path=fp,
                        field=f"{prefix}.rubric",
                        message=f"parser: {fp}: validation[{i}]: llm_judge requires 'rubric'",
                        severity="error",
                    )
                )

        # severity if present must be "error" or "warning".
        if "severity" in params:
            sev = params["severity"]
            if sev not in ("error", "warning"):
                violations.append(
                    SchemaViolation(
                        file_path=fp,
                        field=f"{prefix}.severity",
                        message=f"parser: {fp}: validation[{i}]: severity must be 'error' or 'warning', got '{sev}'",
                        severity="error",
                    )
                )

        # Duplicate validation names.
        if v.name:
            if v.name in seen_names:
                violations.append(
                    SchemaViolation(
                        file_path=fp,
                        field=f"{prefix}.name",
                        message=f"parser: {fp}: validation[{i}]: duplicate validation name '{v.name}'",
                        severity="error",
                    )
                )
            seen_names.add(v.name)

    return violations


def validate_all_specs(project_root: str) -> list[SchemaViolation]:
    """Walk the intent/ directory and validate all spec files.

    Performs both per-file schema validation and cross-file consistency checks.
    Recursively discovers features at any nesting depth under intent/.

    Returns an aggregate list of all violations found.
    """
    abs_root = os.path.abspath(project_root)
    intent_dir = os.path.join(abs_root, "intent")

    violations: list[SchemaViolation] = []

    if not os.path.isdir(intent_dir):
        violations.append(
            SchemaViolation(
                file_path=intent_dir,
                field="",
                message=f"parser: intent directory not found: {intent_dir}",
                severity="error",
            )
        )
        return violations

    # Track feature names for duplicate detection.
    feature_names: set[str] = set()
    # Track all discovered feature paths for depends_on validation.
    feature_dirs: set[str] = set()
    # Collect intents for cross-file validation.
    feature_intents: dict[str, Intent] = {}  # feature_path -> Intent
    feature_validation_files: dict[str, list[ValidationFile]] = {}  # feature_path -> [VF]

    # --- Validate project.ic ---
    project_ic_path = os.path.join(intent_dir, "project.ic")
    if os.path.isfile(project_ic_path):
        try:
            project_intent = ParseIntentFile(project_ic_path)
            violations.extend(validate_project_intent(project_intent))
        except (ValueError, FileNotFoundError) as e:
            violations.append(
                SchemaViolation(
                    file_path=project_ic_path,
                    field="",
                    message=str(e),
                    severity="error",
                )
            )

    # --- Recursively walk feature subdirectories ---
    for dirpath, dirnames, filenames in os.walk(intent_dir):
        if os.path.abspath(dirpath) == os.path.abspath(intent_dir):
            continue

        # Find .ic and .icv files in this directory.
        ic_files = sorted(f for f in filenames if f.endswith(".ic"))
        icv_files = sorted(f for f in filenames if f.endswith(".icv"))

        # Derive feature path from relative path.
        rel_path = os.path.relpath(dirpath, intent_dir)
        feature_path = rel_path.replace(os.sep, "/")

        # Register all walked directories (that contain .ic files) as feature dirs.
        if not ic_files:
            # Directory exists but has no .ic file -- not a feature, skip.
            continue

        feature_dirs.add(feature_path)

        # Parse the .ic file.
        ic_path = os.path.join(dirpath, ic_files[0])
        try:
            intent = ParseIntentFile(ic_path)
            intent_violations = validate_intent_schema(intent)
            violations.extend(intent_violations)

            feature_intents[feature_path] = intent

            # Cross-file: name must match directory path.
            if intent.name != feature_path:
                violations.append(
                    SchemaViolation(
                        file_path=ic_path,
                        field="name",
                        message=(
                            f"parser: {ic_path}: name '{intent.name}' does not match "
                            f"directory path '{feature_path}'"
                        ),
                        severity="error",
                    )
                )

            # Duplicate feature name detection.
            if intent.name in feature_names:
                violations.append(
                    SchemaViolation(
                        file_path=ic_path,
                        field="name",
                        message=f"parser: {ic_path}: duplicate feature name '{intent.name}'",
                        severity="error",
                    )
                )
            feature_names.add(intent.name)

        except (ValueError, FileNotFoundError) as e:
            violations.append(
                SchemaViolation(
                    file_path=ic_path,
                    field="",
                    message=str(e),
                    severity="error",
                )
            )

        # Parse .icv files.
        vf_list: list[ValidationFile] = []
        for icv_file in icv_files:
            icv_path = os.path.join(dirpath, icv_file)
            try:
                vf = ParseValidationFile(icv_path)
                vf_violations = validate_validation_schema(vf)
                violations.extend(vf_violations)
                vf_list.append(vf)

                # Cross-file: .icv target must match directory path.
                if vf.target != feature_path:
                    violations.append(
                        SchemaViolation(
                            file_path=icv_path,
                            field="target",
                            message=(
                                f"parser: {icv_path}: target '{vf.target}' does not match "
                                f"directory path '{feature_path}'"
                            ),
                            severity="error",
                        )
                    )

            except (ValueError, FileNotFoundError) as e:
                violations.append(
                    SchemaViolation(
                        file_path=icv_path,
                        field="",
                        message=str(e),
                        severity="error",
                    )
                )

        feature_validation_files[feature_path] = vf_list

    # --- Cross-file: depends_on references must exist ---
    for feature_path, intent in feature_intents.items():
        expanded_deps = expand_dependency_globs(intent.depends_on, feature_dirs)
        for dep in expanded_deps:
            if dep not in feature_dirs:
                violations.append(
                    SchemaViolation(
                        file_path=intent.file_path,
                        field="depends_on",
                        message=(
                            f"parser: {intent.file_path}: depends_on references "
                            f"unknown feature '{dep}'"
                        ),
                        severity="error",
                    )
                )

    return violations
