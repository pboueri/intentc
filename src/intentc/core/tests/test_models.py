"""Tests for the core data models."""

from __future__ import annotations

from pathlib import Path

from intentc.core import (
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


def test_intent_file_defaults() -> None:
    intent = IntentFile(name="x")
    assert intent.depends_on == []
    assert intent.tags == []
    assert intent.authors == []
    assert intent.body == ""
    assert intent.file_references == []
    assert intent.source_path is None


def test_project_intent_has_no_depends_on() -> None:
    assert not hasattr(ProjectIntent(name="p"), "depends_on")


def test_implementation_has_body() -> None:
    impl = Implementation(name="default", body="# Impl")
    assert impl.body == "# Impl"


def test_validation_type_catalogue() -> None:
    assert ValidationType.AGENT_VALIDATION.value == "agent_validation"
    assert ValidationType.COMMAND_VALIDATION.value == "command_validation"
    assert ValidationType.FILE_EXISTS.value == "file_exists"


def test_validation_type_is_plain_string() -> None:
    v = Validation(name="v", type="my_custom_type")
    assert v.type == "my_custom_type"
    assert Validation(name="d").type == "agent_validation"
    assert Validation(name="d").severity is Severity.ERROR


def test_validation_file_defaults() -> None:
    vf = ValidationFile()
    assert vf.target == ""
    assert vf.version == 1
    assert vf.agent_profile is None
    assert vf.validations == []


def test_parse_error_str_with_and_without_field() -> None:
    assert str(ParseError(Path("a.ic"), "boom")) == "a.ic: boom"
    assert str(ParseError(Path("a.ic"), "boom", field="name")) == "a.ic [name]: boom"


def test_parse_errors_message_lists_each_error() -> None:
    exc = ParseErrors([ParseError(Path("a.ic"), "one"), ParseError(Path("b.icv"), "two")])
    assert str(exc).startswith("2 parse error(s):\n")
    assert "a.ic: one" in str(exc)
    assert "b.icv: two" in str(exc)
    assert len(exc.errors) == 2
