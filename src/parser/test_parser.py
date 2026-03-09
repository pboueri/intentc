"""Comprehensive tests for the parser package."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from core.types import (
    Intent,
    SchemaViolation,
    Validation,
    ValidationFile,
    ValidationType,
)
from parser.parser import (
    ParseIntentFile,
    ParseValidationFile,
    TargetRegistry,
    validate_all_specs,
    validate_intent_schema,
    validate_project_intent,
    validate_validation_schema,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_file(path: str, content: str) -> str:
    """Write content to a file, creating parent directories as needed."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def _make_ic(
    tmpdir: str,
    feature: str,
    name: str | None = None,
    version: int = 1,
    depends_on: list[str] | None = None,
    tags: list[str] | None = None,
    profile: str = "",
    body: str = "# Description\n",
) -> str:
    """Create a .ic file in a feature directory."""
    name = name or feature
    deps = depends_on or []
    tag_list = tags or []

    lines = ["---"]
    lines.append(f"name: {name}")
    lines.append(f"version: {version}")
    if deps:
        lines.append(f"depends_on: [{', '.join(deps)}]")
    if tag_list:
        lines.append(f"tags: [{', '.join(tag_list)}]")
    if profile:
        lines.append(f"profile: {profile}")
    lines.append("---")
    lines.append("")
    lines.append(body)

    content = "\n".join(lines)
    path = os.path.join(tmpdir, "intent", feature, f"{feature}.ic")
    return _write_file(path, content)


def _make_project_ic(
    tmpdir: str,
    name: str = "testproject",
    version: int = 1,
    depends_on: list[str] | None = None,
    tags: list[str] | None = None,
    body: str = "# Project\n",
) -> str:
    """Create a project.ic file."""
    lines = ["---"]
    lines.append(f"name: {name}")
    lines.append(f"version: {version}")
    if depends_on:
        lines.append(f"depends_on: [{', '.join(depends_on)}]")
    if tags:
        lines.append(f"tags: [{', '.join(tags)}]")
    lines.append("---")
    lines.append("")
    lines.append(body)

    content = "\n".join(lines)
    path = os.path.join(tmpdir, "intent", "project.ic")
    return _write_file(path, content)


def _make_icv(
    tmpdir: str,
    feature: str,
    target: str | None = None,
    filename: str = "validations.icv",
    version: int = 1,
    judge_profile: str = "",
    validations: list[dict] | None = None,
    body: str = "# Validations\n",
) -> str:
    """Create a .icv file in a feature directory."""
    target = target or feature
    if validations is None:
        validations = [
            {"name": "check-exists", "type": "file_check", "path": "src/main.py"}
        ]

    lines = ["---"]
    lines.append(f"target: {target}")
    lines.append(f"version: {version}")
    if judge_profile:
        lines.append(f"judge_profile: {judge_profile}")
    lines.append("validations:")
    for v in validations:
        lines.append(f"  - name: {v['name']}")
        lines.append(f"    type: {v['type']}")
        for k, val in v.items():
            if k in ("name", "type"):
                continue
            if isinstance(val, list):
                lines.append(f"    {k}: [{', '.join(str(x) for x in val)}]")
            elif isinstance(val, str) and "\n" in val:
                lines.append(f"    {k}: |")
                for line in val.split("\n"):
                    lines.append(f"      {line}")
            else:
                lines.append(f"    {k}: {val}")
    lines.append("---")
    lines.append("")
    lines.append(body)

    content = "\n".join(lines)
    path = os.path.join(tmpdir, "intent", feature, filename)
    return _write_file(path, content)


# ===========================================================================
# ParseIntentFile tests
# ===========================================================================


class TestParseIntentFile:
    """Tests for ParseIntentFile."""

    def test_basic_parsing(self, tmp_path: Path) -> None:
        """Parse a well-formed .ic file."""
        content = """\
---
name: auth
version: 1
depends_on: [core, utils]
tags: [security, foundation]
profile: fast
---

# Authentication

Implement user auth.
"""
        ic_path = str(tmp_path / "auth.ic")
        _write_file(ic_path, content)

        intent = ParseIntentFile(ic_path)
        assert intent.name == "auth"
        assert intent.version == 1
        assert intent.depends_on == ["core", "utils"]
        assert intent.tags == ["security", "foundation"]
        assert intent.profile == "fast"
        assert "# Authentication" in intent.content
        assert "Implement user auth." in intent.content
        assert intent.file_path == os.path.abspath(ic_path)

    def test_minimal_fields(self, tmp_path: Path) -> None:
        """Parse .ic with only required fields."""
        content = """\
---
name: minimal
version: 1
---

# Minimal feature
"""
        ic_path = str(tmp_path / "minimal.ic")
        _write_file(ic_path, content)

        intent = ParseIntentFile(ic_path)
        assert intent.name == "minimal"
        assert intent.version == 1
        assert intent.depends_on == []
        assert intent.tags == []
        assert intent.profile == ""

    def test_empty_body_is_valid(self, tmp_path: Path) -> None:
        """A .ic file with only frontmatter and no body is valid."""
        content = """\
---
name: empty-body
version: 1
---
"""
        ic_path = str(tmp_path / "empty.ic")
        _write_file(ic_path, content)

        intent = ParseIntentFile(ic_path)
        assert intent.name == "empty-body"
        assert intent.content.strip() == ""

    def test_missing_frontmatter(self, tmp_path: Path) -> None:
        """File with no --- delimiters should raise."""
        content = "Just plain text, no frontmatter."
        ic_path = str(tmp_path / "bad.ic")
        _write_file(ic_path, content)

        with pytest.raises(ValueError, match="missing frontmatter"):
            ParseIntentFile(ic_path)

    def test_missing_closing_delimiter(self, tmp_path: Path) -> None:
        """File with only opening --- should raise."""
        content = """\
---
name: broken
version: 1
"""
        ic_path = str(tmp_path / "broken.ic")
        _write_file(ic_path, content)

        with pytest.raises(ValueError, match="missing closing '---'"):
            ParseIntentFile(ic_path)

    def test_missing_name(self, tmp_path: Path) -> None:
        """Missing required field 'name' should raise."""
        content = """\
---
version: 1
---

Body text.
"""
        ic_path = str(tmp_path / "no_name.ic")
        _write_file(ic_path, content)

        with pytest.raises(ValueError, match="missing required field 'name'"):
            ParseIntentFile(ic_path)

    def test_missing_version(self, tmp_path: Path) -> None:
        """Missing required field 'version' should raise."""
        content = """\
---
name: test
---

Body text.
"""
        ic_path = str(tmp_path / "no_version.ic")
        _write_file(ic_path, content)

        with pytest.raises(ValueError, match="missing required field 'version'"):
            ParseIntentFile(ic_path)

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        """Invalid YAML in frontmatter should raise."""
        content = """\
---
name: [unterminated
version: 1
---

Body text.
"""
        ic_path = str(tmp_path / "invalid.ic")
        _write_file(ic_path, content)

        with pytest.raises(ValueError, match="invalid YAML"):
            ParseIntentFile(ic_path)

    def test_file_not_found(self) -> None:
        """Non-existent file should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            ParseIntentFile("/nonexistent/path/test.ic")

    def test_absolute_file_path(self, tmp_path: Path) -> None:
        """file_path on the result should be absolute."""
        content = """\
---
name: test
version: 1
---

Body.
"""
        ic_path = str(tmp_path / "test.ic")
        _write_file(ic_path, content)

        intent = ParseIntentFile(ic_path)
        assert os.path.isabs(intent.file_path)

    def test_empty_name_field(self, tmp_path: Path) -> None:
        """Name set to empty string should raise."""
        content = """\
---
name: ""
version: 1
---

Body.
"""
        ic_path = str(tmp_path / "empty_name.ic")
        _write_file(ic_path, content)

        with pytest.raises(ValueError, match="missing required field 'name'"):
            ParseIntentFile(ic_path)


# ===========================================================================
# ParseValidationFile tests
# ===========================================================================


class TestParseValidationFile:
    """Tests for ParseValidationFile."""

    def test_basic_parsing(self, tmp_path: Path) -> None:
        """Parse a well-formed .icv file."""
        content = """\
---
target: auth
version: 1
judge_profile: review
validations:
  - name: file-exists
    type: file_check
    path: src/auth.py
    contains: [def login, def logout]
  - name: tests-pass
    type: command_check
    command: pytest tests/
    exit_code: 0
---

# Auth Validations
"""
        icv_path = str(tmp_path / "validations.icv")
        _write_file(icv_path, content)

        vf = ParseValidationFile(icv_path)
        assert vf.target == "auth"
        assert vf.version == 1
        assert vf.judge_profile == "review"
        assert len(vf.validations) == 2

        v0 = vf.validations[0]
        assert v0.name == "file-exists"
        assert v0.type == ValidationType.FILE_CHECK
        assert v0.parameters["path"] == "src/auth.py"
        assert v0.parameters["contains"] == ["def login", "def logout"]

        v1 = vf.validations[1]
        assert v1.name == "tests-pass"
        assert v1.type == ValidationType.COMMAND_CHECK
        assert v1.parameters["command"] == "pytest tests/"
        assert v1.parameters["exit_code"] == 0

    def test_folder_check(self, tmp_path: Path) -> None:
        """Parse folder_check validation."""
        content = """\
---
target: project
version: 1
validations:
  - name: src-dir
    type: folder_check
    path: src
    children: [main.py, utils.py]
---
"""
        icv_path = str(tmp_path / "validations.icv")
        _write_file(icv_path, content)

        vf = ParseValidationFile(icv_path)
        v = vf.validations[0]
        assert v.type == ValidationType.FOLDER_CHECK
        assert v.parameters["path"] == "src"
        assert v.parameters["children"] == ["main.py", "utils.py"]

    def test_llm_judge(self, tmp_path: Path) -> None:
        """Parse llm_judge validation."""
        content = """\
---
target: auth
version: 1
validations:
  - name: code-quality
    type: llm_judge
    rubric: Check code quality and patterns.
    severity: warning
    context_files: ["src/auth/**"]
---
"""
        icv_path = str(tmp_path / "validations.icv")
        _write_file(icv_path, content)

        vf = ParseValidationFile(icv_path)
        v = vf.validations[0]
        assert v.type == ValidationType.LLM_JUDGE
        assert v.parameters["rubric"] == "Check code quality and patterns."
        assert v.parameters["severity"] == "warning"
        assert v.parameters["context_files"] == ["src/auth/**"]

    def test_hidden_validation(self, tmp_path: Path) -> None:
        """Parse hidden validation flag."""
        content = """\
---
target: auth
version: 1
validations:
  - name: secret-check
    type: file_check
    path: src/auth.py
    hidden: true
---
"""
        icv_path = str(tmp_path / "validations.icv")
        _write_file(icv_path, content)

        vf = ParseValidationFile(icv_path)
        assert vf.validations[0].hidden is True

    def test_missing_target(self, tmp_path: Path) -> None:
        """Missing target field should raise."""
        content = """\
---
version: 1
validations:
  - name: check
    type: file_check
    path: src/main.py
---
"""
        icv_path = str(tmp_path / "validations.icv")
        _write_file(icv_path, content)

        with pytest.raises(ValueError, match="missing required field 'target'"):
            ParseValidationFile(icv_path)

    def test_missing_validation_name(self, tmp_path: Path) -> None:
        """Validation without a name should raise."""
        content = """\
---
target: auth
version: 1
validations:
  - type: file_check
    path: src/main.py
---
"""
        icv_path = str(tmp_path / "validations.icv")
        _write_file(icv_path, content)

        with pytest.raises(ValueError, match="missing required field 'name'"):
            ParseValidationFile(icv_path)

    def test_missing_validation_type(self, tmp_path: Path) -> None:
        """Validation without a type should raise."""
        content = """\
---
target: auth
version: 1
validations:
  - name: check
    path: src/main.py
---
"""
        icv_path = str(tmp_path / "validations.icv")
        _write_file(icv_path, content)

        with pytest.raises(ValueError, match="missing required field 'type'"):
            ParseValidationFile(icv_path)

    def test_unknown_validation_type(self, tmp_path: Path) -> None:
        """Unknown validation type should raise."""
        content = """\
---
target: auth
version: 1
validations:
  - name: check
    type: regex_check
    pattern: ".*"
---
"""
        icv_path = str(tmp_path / "validations.icv")
        _write_file(icv_path, content)

        with pytest.raises(ValueError, match="unknown type 'regex_check'"):
            ParseValidationFile(icv_path)

    def test_missing_frontmatter(self, tmp_path: Path) -> None:
        """No frontmatter delimiters should raise."""
        content = "Just plain text."
        icv_path = str(tmp_path / "validations.icv")
        _write_file(icv_path, content)

        with pytest.raises(ValueError, match="missing frontmatter"):
            ParseValidationFile(icv_path)

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        """Invalid YAML in frontmatter should raise."""
        content = """\
---
target: [broken
version: 1
---
"""
        icv_path = str(tmp_path / "bad.icv")
        _write_file(icv_path, content)

        with pytest.raises(ValueError, match="invalid YAML"):
            ParseValidationFile(icv_path)

    def test_absolute_file_path(self, tmp_path: Path) -> None:
        """file_path on result should be absolute."""
        content = """\
---
target: auth
version: 1
validations:
  - name: check
    type: file_check
    path: src/auth.py
---
"""
        icv_path = str(tmp_path / "validations.icv")
        _write_file(icv_path, content)

        vf = ParseValidationFile(icv_path)
        assert os.path.isabs(vf.file_path)

    def test_missing_version(self, tmp_path: Path) -> None:
        """Missing required field 'version' should raise."""
        content = """\
---
target: auth
validations:
  - name: check
    type: file_check
    path: src/auth.py
---
"""
        icv_path = str(tmp_path / "no_version.icv")
        _write_file(icv_path, content)

        with pytest.raises(ValueError, match="missing required field 'version'"):
            ParseValidationFile(icv_path)


# ===========================================================================
# TargetRegistry tests
# ===========================================================================


class TestTargetRegistry:
    """Tests for TargetRegistry."""

    def test_discover_features(self, tmp_path: Path) -> None:
        """Discover features from intent/ directory."""
        root = str(tmp_path)
        _make_project_ic(root)
        _make_ic(root, "auth", depends_on=["core"])
        _make_ic(root, "core")
        _make_icv(root, "auth")
        _make_icv(root, "core")

        registry = TargetRegistry(root)
        registry.load_targets()

        targets = registry.get_all_targets()
        assert len(targets) == 2

        names = [t.name for t in targets]
        assert "auth" in names
        assert "core" in names

    def test_get_target(self, tmp_path: Path) -> None:
        """Get a specific target by name."""
        root = str(tmp_path)
        _make_ic(root, "auth")
        _make_icv(root, "auth")

        registry = TargetRegistry(root)
        registry.load_targets()

        target = registry.get_target("auth")
        assert target.name == "auth"
        assert target.intent.name == "auth"
        assert len(target.validations) == 1

    def test_get_target_not_found(self, tmp_path: Path) -> None:
        """Get a non-existent target should raise KeyError."""
        root = str(tmp_path)
        os.makedirs(os.path.join(root, "intent"))

        registry = TargetRegistry(root)
        registry.load_targets()

        with pytest.raises(KeyError, match="target not found"):
            registry.get_target("nonexistent")

    def test_get_project_intent(self, tmp_path: Path) -> None:
        """Get the project intent from project.ic."""
        root = str(tmp_path)
        _make_project_ic(root, name="myproject")

        registry = TargetRegistry(root)
        registry.load_targets()

        project = registry.get_project_intent()
        assert project.name == "myproject"

    def test_get_project_intent_missing(self, tmp_path: Path) -> None:
        """Missing project.ic should raise FileNotFoundError."""
        root = str(tmp_path)
        os.makedirs(os.path.join(root, "intent"))

        registry = TargetRegistry(root)
        registry.load_targets()

        with pytest.raises(FileNotFoundError, match="project intent not found"):
            registry.get_project_intent()

    def test_missing_intent_directory(self, tmp_path: Path) -> None:
        """No intent/ directory should raise FileNotFoundError."""
        root = str(tmp_path)

        registry = TargetRegistry(root)
        with pytest.raises(FileNotFoundError, match="intent directory not found"):
            registry.load_targets()

    def test_multiple_icv_files(self, tmp_path: Path) -> None:
        """A feature can have multiple .icv files."""
        root = str(tmp_path)
        _make_ic(root, "auth")
        _make_icv(root, "auth", filename="structure.icv", validations=[
            {"name": "dir-check", "type": "folder_check", "path": "src/auth"},
        ])
        _make_icv(root, "auth", filename="tests.icv", validations=[
            {"name": "test-check", "type": "command_check", "command": "pytest"},
        ])

        registry = TargetRegistry(root)
        registry.load_targets()

        target = registry.get_target("auth")
        assert len(target.validations) == 2

    def test_directory_without_ic_file_ignored(self, tmp_path: Path) -> None:
        """Subdirectory without a .ic file is not a feature."""
        root = str(tmp_path)
        _make_ic(root, "auth")
        # Create a directory with only an .icv file (no .ic).
        os.makedirs(os.path.join(root, "intent", "orphan"))
        _write_file(
            os.path.join(root, "intent", "orphan", "validations.icv"),
            "---\ntarget: orphan\nversion: 1\nvalidations:\n  - name: c\n    type: file_check\n    path: x\n---\n",
        )

        registry = TargetRegistry(root)
        registry.load_targets()

        targets = registry.get_all_targets()
        assert len(targets) == 1
        assert targets[0].name == "auth"

    def test_sorted_targets(self, tmp_path: Path) -> None:
        """get_all_targets returns targets sorted by name."""
        root = str(tmp_path)
        _make_ic(root, "zebra")
        _make_ic(root, "alpha")
        _make_ic(root, "middle")

        registry = TargetRegistry(root)
        registry.load_targets()

        names = [t.name for t in registry.get_all_targets()]
        assert names == ["alpha", "middle", "zebra"]


# ===========================================================================
# validate_intent_schema tests
# ===========================================================================


class TestValidateIntentSchema:
    """Tests for validate_intent_schema."""

    def test_valid_intent(self) -> None:
        """Valid intent produces no violations."""
        intent = Intent(name="auth", version=1, depends_on=["core"], tags=["sec"])
        violations = validate_intent_schema(intent)
        assert violations == []

    def test_missing_name(self) -> None:
        """Empty name produces a violation."""
        intent = Intent(name="", version=1)
        violations = validate_intent_schema(intent)
        assert len(violations) == 1
        assert violations[0].field == "name"
        assert "missing required field 'name'" in violations[0].message

    def test_zero_version(self) -> None:
        """Version 0 produces a violation."""
        intent = Intent(name="test", version=0)
        violations = validate_intent_schema(intent)
        assert any(v.field == "version" for v in violations)

    def test_negative_version(self) -> None:
        """Negative version produces a violation."""
        intent = Intent(name="test", version=-1)
        violations = validate_intent_schema(intent)
        assert any(v.field == "version" for v in violations)

    def test_unsupported_version(self) -> None:
        """Unsupported version produces a violation."""
        intent = Intent(name="test", version=99)
        violations = validate_intent_schema(intent)
        assert any("unsupported version" in v.message for v in violations)

    def test_empty_depends_on_entry(self) -> None:
        """Empty string in depends_on produces a violation."""
        intent = Intent(name="test", version=1, depends_on=["core", ""])
        violations = validate_intent_schema(intent)
        assert any("depends_on[1]" in v.field for v in violations)

    def test_empty_tags_entry(self) -> None:
        """Empty string in tags produces a violation."""
        intent = Intent(name="test", version=1, tags=["valid", ""])
        violations = validate_intent_schema(intent)
        assert any("tags[1]" in v.field for v in violations)

    def test_whitespace_profile(self) -> None:
        """Whitespace-only profile produces a violation."""
        intent = Intent(name="test", version=1, profile="   ")
        violations = validate_intent_schema(intent)
        assert any(v.field == "profile" for v in violations)

    def test_valid_profile(self) -> None:
        """Non-empty profile is fine."""
        intent = Intent(name="test", version=1, profile="fast")
        violations = validate_intent_schema(intent)
        assert violations == []

    def test_multiple_violations(self) -> None:
        """Multiple issues produce multiple violations."""
        intent = Intent(name="", version=0, depends_on=[""], tags=[""])
        violations = validate_intent_schema(intent)
        assert len(violations) >= 4  # name, version, depends_on[0], tags[0]


# ===========================================================================
# validate_project_intent tests
# ===========================================================================


class TestValidateProjectIntent:
    """Tests for validate_project_intent."""

    def test_valid_project_intent(self) -> None:
        """Valid project intent produces no violations."""
        intent = Intent(name="myproject", version=1, tags=["meta"])
        violations = validate_project_intent(intent)
        assert violations == []

    def test_depends_on_rejected(self) -> None:
        """Project intent with depends_on produces a violation."""
        intent = Intent(name="myproject", version=1, depends_on=["core"])
        violations = validate_project_intent(intent)
        assert any(
            "project intent must not have 'depends_on'" in v.message
            for v in violations
        )

    def test_inherits_base_checks(self) -> None:
        """Project intent also runs base intent checks."""
        intent = Intent(name="", version=1)
        violations = validate_project_intent(intent)
        assert any(v.field == "name" for v in violations)


# ===========================================================================
# validate_validation_schema tests
# ===========================================================================


class TestValidateValidationSchema:
    """Tests for validate_validation_schema."""

    def test_valid_file_check(self) -> None:
        """Valid file_check validation produces no violations."""
        vf = ValidationFile(
            target="auth",
            version=1,
            validations=[
                Validation(
                    name="check",
                    type=ValidationType.FILE_CHECK,
                    parameters={"path": "src/auth.py"},
                )
            ],
        )
        violations = validate_validation_schema(vf)
        assert violations == []

    def test_valid_folder_check(self) -> None:
        """Valid folder_check validation produces no violations."""
        vf = ValidationFile(
            target="auth",
            version=1,
            validations=[
                Validation(
                    name="check",
                    type=ValidationType.FOLDER_CHECK,
                    parameters={"path": "src/auth"},
                )
            ],
        )
        violations = validate_validation_schema(vf)
        assert violations == []

    def test_valid_command_check(self) -> None:
        """Valid command_check validation produces no violations."""
        vf = ValidationFile(
            target="auth",
            version=1,
            validations=[
                Validation(
                    name="check",
                    type=ValidationType.COMMAND_CHECK,
                    parameters={"command": "pytest"},
                )
            ],
        )
        violations = validate_validation_schema(vf)
        assert violations == []

    def test_valid_llm_judge(self) -> None:
        """Valid llm_judge validation produces no violations."""
        vf = ValidationFile(
            target="auth",
            version=1,
            validations=[
                Validation(
                    name="check",
                    type=ValidationType.LLM_JUDGE,
                    parameters={"rubric": "Check quality."},
                )
            ],
        )
        violations = validate_validation_schema(vf)
        assert violations == []

    def test_missing_target(self) -> None:
        """Missing target produces a violation."""
        vf = ValidationFile(
            target="",
            version=1,
            validations=[
                Validation(
                    name="check",
                    type=ValidationType.FILE_CHECK,
                    parameters={"path": "x"},
                )
            ],
        )
        violations = validate_validation_schema(vf)
        assert any(v.field == "target" for v in violations)

    def test_zero_version(self) -> None:
        """Zero version produces a violation."""
        vf = ValidationFile(
            target="auth",
            version=0,
            validations=[
                Validation(
                    name="check",
                    type=ValidationType.FILE_CHECK,
                    parameters={"path": "x"},
                )
            ],
        )
        violations = validate_validation_schema(vf)
        assert any(v.field == "version" for v in violations)

    def test_empty_validations_list(self) -> None:
        """Empty validations list produces a violation."""
        vf = ValidationFile(target="auth", version=1, validations=[])
        violations = validate_validation_schema(vf)
        assert any(v.field == "validations" for v in violations)

    def test_file_check_missing_path(self) -> None:
        """file_check without path produces a violation."""
        vf = ValidationFile(
            target="auth",
            version=1,
            validations=[
                Validation(
                    name="check",
                    type=ValidationType.FILE_CHECK,
                    parameters={},
                )
            ],
        )
        violations = validate_validation_schema(vf)
        assert any("file_check requires 'path'" in v.message for v in violations)

    def test_folder_check_missing_path(self) -> None:
        """folder_check without path produces a violation."""
        vf = ValidationFile(
            target="auth",
            version=1,
            validations=[
                Validation(
                    name="check",
                    type=ValidationType.FOLDER_CHECK,
                    parameters={},
                )
            ],
        )
        violations = validate_validation_schema(vf)
        assert any("folder_check requires 'path'" in v.message for v in violations)

    def test_command_check_missing_command(self) -> None:
        """command_check without command produces a violation."""
        vf = ValidationFile(
            target="auth",
            version=1,
            validations=[
                Validation(
                    name="check",
                    type=ValidationType.COMMAND_CHECK,
                    parameters={},
                )
            ],
        )
        violations = validate_validation_schema(vf)
        assert any("command_check requires 'command'" in v.message for v in violations)

    def test_llm_judge_missing_rubric(self) -> None:
        """llm_judge without rubric produces a violation."""
        vf = ValidationFile(
            target="auth",
            version=1,
            validations=[
                Validation(
                    name="check",
                    type=ValidationType.LLM_JUDGE,
                    parameters={},
                )
            ],
        )
        violations = validate_validation_schema(vf)
        assert any("llm_judge requires 'rubric'" in v.message for v in violations)

    def test_invalid_severity(self) -> None:
        """Invalid severity produces a violation."""
        vf = ValidationFile(
            target="auth",
            version=1,
            validations=[
                Validation(
                    name="check",
                    type=ValidationType.FILE_CHECK,
                    parameters={"path": "x", "severity": "critical"},
                )
            ],
        )
        violations = validate_validation_schema(vf)
        assert any("severity must be" in v.message for v in violations)

    def test_valid_severity_values(self) -> None:
        """Valid severity values produce no violations."""
        for sev in ("error", "warning"):
            vf = ValidationFile(
                target="auth",
                version=1,
                validations=[
                    Validation(
                        name="check",
                        type=ValidationType.FILE_CHECK,
                        parameters={"path": "x", "severity": sev},
                    )
                ],
            )
            violations = validate_validation_schema(vf)
            assert not any("severity" in v.message for v in violations)

    def test_duplicate_validation_names(self) -> None:
        """Duplicate validation names produce a violation."""
        vf = ValidationFile(
            target="auth",
            version=1,
            validations=[
                Validation(
                    name="check",
                    type=ValidationType.FILE_CHECK,
                    parameters={"path": "src/a.py"},
                ),
                Validation(
                    name="check",
                    type=ValidationType.FILE_CHECK,
                    parameters={"path": "src/b.py"},
                ),
            ],
        )
        violations = validate_validation_schema(vf)
        assert any("duplicate validation name 'check'" in v.message for v in violations)

    def test_whitespace_judge_profile(self) -> None:
        """Whitespace-only judge_profile produces a violation."""
        vf = ValidationFile(
            target="auth",
            version=1,
            judge_profile="   ",
            validations=[
                Validation(
                    name="check",
                    type=ValidationType.FILE_CHECK,
                    parameters={"path": "x"},
                )
            ],
        )
        violations = validate_validation_schema(vf)
        assert any(v.field == "judge_profile" for v in violations)

    def test_missing_validation_name(self) -> None:
        """Validation with empty name produces a violation."""
        vf = ValidationFile(
            target="auth",
            version=1,
            validations=[
                Validation(
                    name="",
                    type=ValidationType.FILE_CHECK,
                    parameters={"path": "x"},
                )
            ],
        )
        violations = validate_validation_schema(vf)
        assert any("missing required field 'name'" in v.message for v in violations)


# ===========================================================================
# validate_all_specs tests
# ===========================================================================


class TestValidateAllSpecs:
    """Tests for validate_all_specs."""

    def test_valid_project(self, tmp_path: Path) -> None:
        """A well-formed project produces no violations."""
        root = str(tmp_path)
        _make_project_ic(root)
        _make_ic(root, "auth", depends_on=["core"])
        _make_ic(root, "core")
        _make_icv(root, "auth")
        _make_icv(root, "core")

        violations = validate_all_specs(root)
        assert violations == []

    def test_missing_intent_directory(self, tmp_path: Path) -> None:
        """No intent/ directory produces a violation."""
        root = str(tmp_path)
        violations = validate_all_specs(root)
        assert len(violations) == 1
        assert "intent directory not found" in violations[0].message

    def test_name_directory_mismatch(self, tmp_path: Path) -> None:
        """Feature name not matching directory name produces a violation."""
        root = str(tmp_path)
        # Create auth directory but set name to "authentication"
        _make_ic(root, "auth", name="authentication")

        violations = validate_all_specs(root)
        assert any(
            "does not match directory name" in v.message for v in violations
        )

    def test_icv_target_directory_mismatch(self, tmp_path: Path) -> None:
        """icv target not matching directory name produces a violation."""
        root = str(tmp_path)
        _make_ic(root, "auth")
        _make_icv(root, "auth", target="wrong-target")

        violations = validate_all_specs(root)
        assert any(
            "target 'wrong-target' does not match directory name 'auth'" in v.message
            for v in violations
        )

    def test_depends_on_unknown_feature(self, tmp_path: Path) -> None:
        """depends_on referencing non-existent feature produces a violation."""
        root = str(tmp_path)
        _make_ic(root, "auth", depends_on=["nonexistent"])

        violations = validate_all_specs(root)
        assert any(
            "depends_on references unknown feature 'nonexistent'" in v.message
            for v in violations
        )

    def test_depends_on_valid_reference(self, tmp_path: Path) -> None:
        """depends_on referencing an existing feature is fine."""
        root = str(tmp_path)
        _make_ic(root, "auth", depends_on=["core"])
        _make_ic(root, "core")

        violations = validate_all_specs(root)
        assert not any("depends_on references unknown" in v.message for v in violations)

    def test_project_ic_with_depends_on(self, tmp_path: Path) -> None:
        """project.ic with depends_on produces a violation."""
        root = str(tmp_path)
        _make_project_ic(root, depends_on=["core"])

        violations = validate_all_specs(root)
        assert any(
            "project intent must not have 'depends_on'" in v.message
            for v in violations
        )

    def test_duplicate_feature_names(self, tmp_path: Path) -> None:
        """Two directories with same feature name produces a violation.

        This requires crafting two directories whose .ic files have the same name.
        Since directory names differ, one will get a name/directory mismatch too.
        """
        root = str(tmp_path)
        _make_ic(root, "auth")
        # Create a second directory 'auth2' but name the feature 'auth'.
        _make_ic(root, "auth2", name="auth")

        violations = validate_all_specs(root)
        # Should flag duplicate name and/or name-directory mismatch.
        messages = [v.message for v in violations]
        assert any("duplicate feature name 'auth'" in m for m in messages) or any(
            "does not match directory name" in m for m in messages
        )

    def test_aggregates_schema_violations(self, tmp_path: Path) -> None:
        """validate_all_specs aggregates violations from multiple files."""
        root = str(tmp_path)
        _make_project_ic(root)
        # Create a feature with an empty validations list in the .icv
        _make_ic(root, "auth")
        _make_icv(root, "auth", validations=[])

        # The icv will produce "validations list must be non-empty"
        # but parsing will fail before that since we need at least one
        # validation for the YAML to parse. So let's write directly.
        icv_path = os.path.join(root, "intent", "auth", "validations.icv")
        _write_file(icv_path, """\
---
target: auth
version: 1
validations: []
---

# Empty validations
""")

        violations = validate_all_specs(root)
        # Should have validation schema violation for empty validations list.
        assert any("non-empty" in v.message for v in violations)

    def test_parse_error_in_ic_file(self, tmp_path: Path) -> None:
        """Parse error in a .ic file is captured as a violation."""
        root = str(tmp_path)
        os.makedirs(os.path.join(root, "intent", "broken"))
        _write_file(
            os.path.join(root, "intent", "broken", "broken.ic"),
            "No frontmatter here.",
        )

        violations = validate_all_specs(root)
        assert any("missing frontmatter" in v.message for v in violations)

    def test_parse_error_in_icv_file(self, tmp_path: Path) -> None:
        """Parse error in a .icv file is captured as a violation."""
        root = str(tmp_path)
        _make_ic(root, "auth")
        os.makedirs(os.path.join(root, "intent", "auth"), exist_ok=True)
        _write_file(
            os.path.join(root, "intent", "auth", "bad.icv"),
            "No frontmatter here.",
        )

        violations = validate_all_specs(root)
        assert any("missing frontmatter" in v.message for v in violations)


# ===========================================================================
# Integration / edge-case tests
# ===========================================================================


class TestEdgeCases:
    """Edge case and integration tests."""

    def test_ic_multiline_body(self, tmp_path: Path) -> None:
        """Multiline markdown body is preserved."""
        content = """\
---
name: docs
version: 1
---

# Documentation

## Section 1

Paragraph one.

## Section 2

Paragraph two.
"""
        ic_path = str(tmp_path / "docs.ic")
        _write_file(ic_path, content)

        intent = ParseIntentFile(ic_path)
        assert "# Documentation" in intent.content
        assert "## Section 1" in intent.content
        assert "## Section 2" in intent.content
        assert "Paragraph one." in intent.content
        assert "Paragraph two." in intent.content

    def test_ic_no_optional_fields(self, tmp_path: Path) -> None:
        """All optional fields default correctly."""
        content = """\
---
name: bare
version: 1
---
"""
        ic_path = str(tmp_path / "bare.ic")
        _write_file(ic_path, content)

        intent = ParseIntentFile(ic_path)
        assert intent.depends_on == []
        assert intent.tags == []
        assert intent.profile == ""

    def test_icv_empty_body(self, tmp_path: Path) -> None:
        """icv file with empty body after frontmatter is valid."""
        content = """\
---
target: auth
version: 1
validations:
  - name: check
    type: file_check
    path: src/auth.py
---
"""
        icv_path = str(tmp_path / "validations.icv")
        _write_file(icv_path, content)

        vf = ParseValidationFile(icv_path)
        assert vf.target == "auth"
        assert len(vf.validations) == 1

    def test_registry_with_real_project_structure(self, tmp_path: Path) -> None:
        """Test registry with a realistic multi-feature project."""
        root = str(tmp_path)
        _make_project_ic(root, name="myapp", tags=["meta"])
        _make_ic(root, "core", tags=["foundation"])
        _make_ic(root, "auth", depends_on=["core"], tags=["security"])
        _make_ic(root, "api", depends_on=["core", "auth"], tags=["web"])

        _make_icv(root, "core", validations=[
            {"name": "core-exists", "type": "folder_check", "path": "src/core"},
        ])
        _make_icv(root, "auth", validations=[
            {"name": "auth-file", "type": "file_check", "path": "src/auth.py"},
            {"name": "auth-tests", "type": "command_check", "command": "pytest tests/auth/"},
        ])
        _make_icv(root, "api", validations=[
            {"name": "api-quality", "type": "llm_judge", "rubric": "Check API quality."},
        ])

        registry = TargetRegistry(root)
        registry.load_targets()

        assert len(registry.get_all_targets()) == 3

        project = registry.get_project_intent()
        assert project.name == "myapp"

        auth = registry.get_target("auth")
        assert auth.intent.depends_on == ["core"]
        assert len(auth.validations) == 1
        assert len(auth.validations[0].validations) == 2

        api = registry.get_target("api")
        assert api.intent.depends_on == ["core", "auth"]

    def test_validate_all_specs_with_real_project(self, tmp_path: Path) -> None:
        """validate_all_specs on a clean project produces zero violations."""
        root = str(tmp_path)
        _make_project_ic(root, name="myapp")
        _make_ic(root, "core")
        _make_ic(root, "auth", depends_on=["core"])
        _make_icv(root, "core", validations=[
            {"name": "exists", "type": "folder_check", "path": "src/core"},
        ])
        _make_icv(root, "auth", validations=[
            {"name": "exists", "type": "file_check", "path": "src/auth.py"},
        ])

        violations = validate_all_specs(root)
        assert violations == []

    def test_error_message_format(self, tmp_path: Path) -> None:
        """Error messages follow the specified format."""
        content = """\
---
version: 1
---

Body.
"""
        ic_path = str(tmp_path / "bad.ic")
        _write_file(ic_path, content)

        with pytest.raises(ValueError) as exc_info:
            ParseIntentFile(ic_path)

        msg = str(exc_info.value)
        assert msg.startswith("parser:")
        assert "missing required field 'name'" in msg

    def test_validation_parameters_include_all_extra_fields(self, tmp_path: Path) -> None:
        """All non-core fields from a validation entry go into parameters."""
        content = """\
---
target: auth
version: 1
validations:
  - name: cmd-check
    type: command_check
    command: make test
    working_dir: /tmp
    exit_code: 0
    stdout_contains: [PASS]
    stderr_contains: []
---
"""
        icv_path = str(tmp_path / "validations.icv")
        _write_file(icv_path, content)

        vf = ParseValidationFile(icv_path)
        v = vf.validations[0]
        assert v.parameters["command"] == "make test"
        assert v.parameters["working_dir"] == "/tmp"
        assert v.parameters["exit_code"] == 0
        assert v.parameters["stdout_contains"] == ["PASS"]
        assert v.parameters["stderr_contains"] == []
