"""Tests for parsing, writing, and round-tripping .ic and .icv files."""

import pytest
from pathlib import Path

from intentc.core.models import (
    Implementation,
    IntentFile,
    ParseErrors,
    ProjectIntent,
    Severity,
    Validation,
    ValidationFile,
    ValidationType,
)
from intentc.core.parser import (
    extract_file_references,
    parse_intent_file,
    parse_validation_file,
    write_intent_file,
    write_validation_file,
)


# --- extract_file_references ---

def test_extract_file_references_relative():
    refs = extract_file_references("See ./diagram.png for details")
    assert "./diagram.png" in refs


def test_extract_file_references_parent():
    refs = extract_file_references("Use ../../design_system/* as guide")
    assert "../../design_system/*" in refs


def test_extract_file_references_dir_prefix():
    refs = extract_file_references("Look at assets/logo.png")
    assert "assets/logo.png" in refs


def test_extract_file_references_empty():
    assert extract_file_references("No references here") == []


# --- parse_intent_file ---

def test_parse_intent_file_basic(tmp_path: Path):
    ic = tmp_path / "feature.ic"
    ic.write_text(
        "---\n"
        "name: my-feature\n"
        "depends_on:\n"
        "  - core/base\n"
        "tags:\n"
        "  - backend\n"
        "authors:\n"
        "  - alice\n"
        "---\n"
        "\n"
        "This feature does things with ./schema.png\n"
    )
    result = parse_intent_file(ic)
    assert isinstance(result, IntentFile)
    assert result.name == "my-feature"
    assert result.depends_on == ["core/base"]
    assert result.tags == ["backend"]
    assert result.authors == ["alice"]
    assert "This feature does things" in result.body
    assert "./schema.png" in result.file_references
    assert result.source_path == ic


def test_parse_intent_file_missing_name(tmp_path: Path):
    ic = tmp_path / "bad.ic"
    ic.write_text("---\ntags: [x]\n---\nBody\n")
    with pytest.raises(ParseErrors) as exc_info:
        parse_intent_file(ic)
    assert "name" in str(exc_info.value)


def test_parse_intent_file_as_project(tmp_path: Path):
    ic = tmp_path / "project.ic"
    ic.write_text("---\nname: myproject\n---\nProject body\n")
    result = parse_intent_file(ic, as_project=True)
    assert isinstance(result, ProjectIntent)
    assert result.name == "myproject"


def test_parse_intent_file_as_implementation(tmp_path: Path):
    ic = tmp_path / "default.ic"
    ic.write_text("---\nname: python\ndepends_on:\n  - core/*\n---\nImpl body\n")
    result = parse_intent_file(ic, as_implementation=True)
    assert isinstance(result, Implementation)
    assert result.depends_on == ["core/*"]


def test_parse_intent_file_not_found(tmp_path: Path):
    with pytest.raises(ParseErrors):
        parse_intent_file(tmp_path / "missing.ic")


# --- parse_validation_file ---

def test_parse_validation_file_basic(tmp_path: Path):
    icv = tmp_path / "check.icv"
    icv.write_text(
        "target: core/feature\n"
        "validations:\n"
        "  - name: has-files\n"
        "    type: agent_validation\n"
        "    severity: error\n"
        "    args:\n"
        "      rubric: Check that src/main.py exists\n"
    )
    result = parse_validation_file(icv)
    assert isinstance(result, ValidationFile)
    assert result.target == "core/feature"
    assert len(result.validations) == 1
    v = result.validations[0]
    assert v.name == "has-files"
    assert v.type == ValidationType.AGENT_VALIDATION
    assert v.severity == Severity.ERROR
    assert v.args["rubric"] == "Check that src/main.py exists"


def test_parse_validation_file_empty(tmp_path: Path):
    icv = tmp_path / "empty.icv"
    icv.write_text("")
    result = parse_validation_file(icv)
    assert result.target == ""
    assert result.validations == []


def test_parse_validation_file_defaults(tmp_path: Path):
    icv = tmp_path / "minimal.icv"
    icv.write_text(
        "target: feat\n"
        "validations:\n"
        "  - name: check1\n"
    )
    result = parse_validation_file(icv)
    v = result.validations[0]
    assert v.type == ValidationType.AGENT_VALIDATION
    assert v.severity == Severity.ERROR


def test_parse_validation_file_with_agent_profile(tmp_path: Path):
    icv = tmp_path / "profiled.icv"
    icv.write_text(
        "target: feat\n"
        "agent_profile: gpt4\n"
        "validations: []\n"
    )
    result = parse_validation_file(icv)
    assert result.agent_profile == "gpt4"


# --- write_intent_file ---

def test_write_intent_file(tmp_path: Path):
    intent = IntentFile(
        name="test-feature",
        depends_on=["core/base"],
        tags=["api"],
        authors=["bob"],
        body="Feature body text",
    )
    out = write_intent_file(intent, tmp_path / "out.ic")
    assert out.exists()
    content = out.read_text()
    assert "name: test-feature" in content
    assert "Feature body text" in content


def test_write_intent_file_uses_source_path(tmp_path: Path):
    p = tmp_path / "auto.ic"
    intent = IntentFile(name="auto", source_path=p)
    out = write_intent_file(intent)
    assert out == p
    assert p.exists()


def test_write_intent_file_no_path():
    intent = IntentFile(name="orphan")
    with pytest.raises(ValueError):
        write_intent_file(intent)


# --- write_validation_file ---

def test_write_validation_file(tmp_path: Path):
    vf = ValidationFile(
        target="core/feat",
        validations=[
            Validation(
                name="check",
                type=ValidationType.AGENT_VALIDATION,
                severity=Severity.WARNING,
                args={"rubric": "echo ok"},
            )
        ],
    )
    out = write_validation_file(vf, tmp_path / "out.icv")
    assert out.exists()
    content = out.read_text()
    assert "target: core/feat" in content
    assert "agent_validation" in content
    assert "warning" in content


# --- round-trip tests ---

def test_round_trip_intent_file(tmp_path: Path):
    original = IntentFile(
        name="round-trip",
        depends_on=["dep/a", "dep/b"],
        tags=["test"],
        authors=["carol"],
        body="Body with ./ref.png reference",
    )
    path = write_intent_file(original, tmp_path / "rt.ic")
    loaded = parse_intent_file(path)
    assert loaded.name == original.name
    assert loaded.depends_on == original.depends_on
    assert loaded.tags == original.tags
    assert loaded.authors == original.authors
    assert loaded.body == original.body
    assert "./ref.png" in loaded.file_references


def test_round_trip_project_intent(tmp_path: Path):
    original = ProjectIntent(name="proj", body="Project desc")
    path = write_intent_file(original, tmp_path / "project.ic")
    loaded = parse_intent_file(path, as_project=True)
    assert isinstance(loaded, ProjectIntent)
    assert loaded.name == original.name
    assert loaded.body == original.body


def test_round_trip_validation_file(tmp_path: Path):
    original = ValidationFile(
        target="core/spec",
        agent_profile="claude",
        validations=[
            Validation(name="v1", type=ValidationType.AGENT_VALIDATION, args={"rubric": "check it"}),
            Validation(name="v2", severity=Severity.WARNING),
        ],
    )
    path = write_validation_file(original, tmp_path / "rt.icv")
    loaded = parse_validation_file(path)
    assert loaded.target == original.target
    assert loaded.agent_profile == original.agent_profile
    assert len(loaded.validations) == 2
    assert loaded.validations[0].name == "v1"
    assert loaded.validations[0].type == ValidationType.AGENT_VALIDATION
    assert loaded.validations[1].severity == Severity.WARNING
