"""Tests for core data models."""

from pathlib import Path

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


def test_intent_file_defaults():
    f = IntentFile(name="test")
    assert f.name == "test"
    assert f.depends_on == []
    assert f.tags == []
    assert f.authors == []
    assert f.body == ""
    assert f.file_references == []
    assert f.source_path is None


def test_project_intent_has_no_depends_on():
    p = ProjectIntent(name="project")
    assert not hasattr(p, "depends_on") or "depends_on" not in p.model_fields


def test_implementation_has_depends_on():
    impl = Implementation(name="default", depends_on=["core/*"])
    assert impl.depends_on == ["core/*"]


def test_validation_type_values():
    assert ValidationType.AGENT_VALIDATION.value == "agent_validation"


def test_severity_values():
    assert Severity.ERROR.value == "error"
    assert Severity.WARNING.value == "warning"


def test_validation_defaults():
    v = Validation(name="check")
    assert v.type == ValidationType.AGENT_VALIDATION
    assert v.severity == Severity.ERROR
    assert v.args == {}


def test_validation_file_defaults():
    vf = ValidationFile()
    assert vf.target == ""
    assert vf.agent_profile is None
    assert vf.validations == []
    assert vf.source_path is None


def test_parse_error_str_without_field():
    e = ParseError(Path("foo.ic"), "bad file")
    assert str(e) == "foo.ic: bad file"


def test_parse_error_str_with_field():
    e = ParseError(Path("foo.ic"), "missing", field="name")
    assert str(e) == "foo.ic [name]: missing"


def test_parse_errors_message():
    errors = [
        ParseError(Path("a.ic"), "err1"),
        ParseError(Path("b.ic"), "err2", field="x"),
    ]
    exc = ParseErrors(errors)
    assert "2 parse error(s)" in str(exc)
    assert "a.ic: err1" in str(exc)
    assert "b.ic [x]: err2" in str(exc)
