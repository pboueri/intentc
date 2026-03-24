"""Tests for core data models and parser."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

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
from intentc.core.parser import (
    extract_file_references,
    parse_intent_file,
    parse_validation_file,
    write_intent_file,
    write_validation_file,
)


# ── Model tests ──────────────────────────────────────────────────────────


class TestModels:
    def test_intent_file_defaults(self):
        f = IntentFile(name="test")
        assert f.name == "test"
        assert f.depends_on == []
        assert f.tags == []
        assert f.authors == []
        assert f.body == ""
        assert f.file_references == []
        assert f.source_path is None

    def test_project_intent_has_no_depends_on(self):
        p = ProjectIntent(name="proj")
        assert not hasattr(p, "depends_on") or "depends_on" not in ProjectIntent.model_fields

    def test_implementation_defaults(self):
        impl = Implementation(name="default")
        assert impl.depends_on == []
        assert impl.body == ""

    def test_validation_type_values(self):
        assert ValidationType.AGENT_VALIDATION.value == "agent_validation"
        assert ValidationType.LLM_JUDGE.value == "llm_judge"
        assert ValidationType.FILE_CHECK.value == "file_check"
        assert ValidationType.FOLDER_CHECK.value == "folder_check"
        assert ValidationType.COMMAND_CHECK.value == "command_check"

    def test_severity_values(self):
        assert Severity.ERROR.value == "error"
        assert Severity.WARNING.value == "warning"

    def test_validation_defaults(self):
        v = Validation(name="check")
        assert v.type == ValidationType.AGENT_VALIDATION
        assert v.severity == Severity.ERROR
        assert v.args == {}

    def test_validation_file_defaults(self):
        vf = ValidationFile()
        assert vf.target == ""
        assert vf.validations == []
        assert vf.source_path is None

    def test_parse_error_str_without_field(self):
        e = ParseError(path=Path("foo.ic"), message="bad stuff")
        assert str(e) == "foo.ic: bad stuff"

    def test_parse_error_str_with_field(self):
        e = ParseError(path=Path("foo.ic"), field="name", message="missing")
        assert str(e) == "foo.ic [name]: missing"

    def test_parse_errors_message(self):
        errs = ParseErrors([
            ParseError(path=Path("a.ic"), message="err1"),
            ParseError(path=Path("b.ic"), field="x", message="err2"),
        ])
        assert "2 parse error(s)" in str(errs)
        assert "a.ic: err1" in str(errs)
        assert "b.ic [x]: err2" in str(errs)


# ── File reference extraction ────────────────────────────────────────────


class TestFileReferences:
    def test_extract_simple_refs(self):
        body = "See ui_design.png for the layout and ../../design_system/* for tokens."
        refs = extract_file_references(body)
        assert "ui_design.png" in refs
        assert "../../design_system/*" in refs

    def test_extract_relative_path(self):
        refs = extract_file_references("Check ./local/file.txt here")
        assert "./local/file.txt" in refs

    def test_no_refs(self):
        assert extract_file_references("Just plain text with no paths.") == []


# ── .ic parsing ──────────────────────────────────────────────────────────


class TestParseIntentFile:
    def _write(self, tmp: Path, content: str) -> Path:
        p = tmp / "test.ic"
        p.write_text(content, encoding="utf-8")
        return p

    def test_basic_parse(self, tmp_path):
        p = self._write(tmp_path, "---\nname: my-feature\ndepends_on:\n  - core/a\ntags: [cli]\n---\nHello body\n")
        result = parse_intent_file(p)
        assert isinstance(result, IntentFile)
        assert result.name == "my-feature"
        assert result.depends_on == ["core/a"]
        assert result.tags == ["cli"]
        assert "Hello body" in result.body
        assert result.source_path == p

    def test_parse_as_project(self, tmp_path):
        p = self._write(tmp_path, "---\nname: proj\ntags: [meta]\n---\nProject body\n")
        result = parse_intent_file(p, as_project=True)
        assert isinstance(result, ProjectIntent)
        assert result.name == "proj"

    def test_parse_as_implementation(self, tmp_path):
        p = self._write(tmp_path, "---\nname: default\ntags: [impl]\n---\nImpl body\n")
        result = parse_intent_file(p, as_implementation=True)
        assert isinstance(result, Implementation)

    def test_missing_name_raises(self, tmp_path):
        p = self._write(tmp_path, "---\ntags: [x]\n---\nbody\n")
        with pytest.raises(ParseErrors) as exc_info:
            parse_intent_file(p)
        assert any(e.field == "name" for e in exc_info.value.errors)

    def test_missing_frontmatter_raises(self, tmp_path):
        p = self._write(tmp_path, "No frontmatter here\n")
        with pytest.raises(ParseErrors):
            parse_intent_file(p)

    def test_file_references_extracted(self, tmp_path):
        p = self._write(tmp_path, "---\nname: feat\n---\nSee design.png and ../shared/*\n")
        result = parse_intent_file(p)
        assert "design.png" in result.file_references
        assert "../shared/*" in result.file_references


# ── .icv parsing ─────────────────────────────────────────────────────────


class TestParseValidationFile:
    def _write(self, tmp: Path, content: str) -> Path:
        p = tmp / "test.icv"
        p.write_text(content, encoding="utf-8")
        return p

    def test_basic_parse(self, tmp_path):
        content = (
            "target: core/specs\n"
            "validations:\n"
            "  - name: types-exist\n"
            "    type: file_check\n"
            "    severity: error\n"
            "    args:\n"
            "      path: models.py\n"
        )
        p = self._write(tmp_path, content)
        vf = parse_validation_file(p)
        assert vf.target == "core/specs"
        assert len(vf.validations) == 1
        v = vf.validations[0]
        assert v.name == "types-exist"
        assert v.type == ValidationType.FILE_CHECK
        assert v.severity == Severity.ERROR
        assert v.args == {"path": "models.py"}

    def test_empty_file(self, tmp_path):
        p = self._write(tmp_path, "")
        vf = parse_validation_file(p)
        assert vf.target == ""
        assert vf.validations == []

    def test_defaults(self, tmp_path):
        content = "target: feat\nvalidations:\n  - name: check\n"
        p = self._write(tmp_path, content)
        vf = parse_validation_file(p)
        v = vf.validations[0]
        assert v.type == ValidationType.AGENT_VALIDATION
        assert v.severity == Severity.ERROR

    def test_agent_profile(self, tmp_path):
        content = "target: feat\nagent_profile: custom\nvalidations: []\n"
        p = self._write(tmp_path, content)
        vf = parse_validation_file(p)
        assert vf.agent_profile == "custom"

    def test_warning_severity(self, tmp_path):
        content = "target: feat\nvalidations:\n  - name: soft\n    severity: warning\n"
        p = self._write(tmp_path, content)
        vf = parse_validation_file(p)
        assert vf.validations[0].severity == Severity.WARNING


# ── Write + round-trip ───────────────────────────────────────────────────


class TestWriteIntentFile:
    def test_write_and_roundtrip(self, tmp_path):
        original = IntentFile(
            name="roundtrip",
            depends_on=["core/a"],
            tags=["test"],
            authors=["dev"],
            body="# My Feature\n\nSome description.\n",
        )
        out = write_intent_file(original, tmp_path / "roundtrip.ic")
        assert out.exists()

        parsed = parse_intent_file(out)
        assert parsed.name == original.name
        assert parsed.depends_on == original.depends_on
        assert parsed.tags == original.tags
        assert parsed.authors == original.authors
        assert "My Feature" in parsed.body

    def test_write_uses_source_path(self, tmp_path):
        intent = IntentFile(name="auto", source_path=tmp_path / "auto.ic")
        result = write_intent_file(intent)
        assert result == tmp_path / "auto.ic"
        assert result.exists()

    def test_write_no_path_raises(self):
        intent = IntentFile(name="nope")
        with pytest.raises(ValueError):
            write_intent_file(intent)

    def test_write_project_intent(self, tmp_path):
        proj = ProjectIntent(name="proj", tags=["meta"], body="Project body\n")
        out = write_intent_file(proj, tmp_path / "project.ic")
        parsed = parse_intent_file(out, as_project=True)
        assert parsed.name == "proj"
        assert parsed.tags == ["meta"]

    def test_write_implementation(self, tmp_path):
        impl = Implementation(name="go", depends_on=["core"], tags=["lang"], body="Go impl\n")
        out = write_intent_file(impl, tmp_path / "go.ic")
        parsed = parse_intent_file(out, as_implementation=True)
        assert parsed.name == "go"
        assert parsed.depends_on == ["core"]


class TestWriteValidationFile:
    def test_write_and_roundtrip(self, tmp_path):
        original = ValidationFile(
            target="core/specs",
            validations=[
                Validation(name="check1", type=ValidationType.FILE_CHECK, args={"path": "foo.py"}),
                Validation(name="check2", severity=Severity.WARNING),
            ],
        )
        out = write_validation_file(original, tmp_path / "test.icv")
        assert out.exists()

        parsed = parse_validation_file(out)
        assert parsed.target == "core/specs"
        assert len(parsed.validations) == 2
        assert parsed.validations[0].name == "check1"
        assert parsed.validations[0].type == ValidationType.FILE_CHECK
        assert parsed.validations[1].severity == Severity.WARNING

    def test_write_empty_validation_file(self, tmp_path):
        vf = ValidationFile()
        out = write_validation_file(vf, tmp_path / "empty.icv")
        parsed = parse_validation_file(out)
        assert parsed.target == ""
        assert parsed.validations == []

    def test_write_no_path_raises(self):
        vf = ValidationFile(target="x")
        with pytest.raises(ValueError):
            write_validation_file(vf)
