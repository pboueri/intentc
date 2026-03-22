from __future__ import annotations

import pytest
from pathlib import Path

from intentc.core.types import (
    IntentFile,
    ProjectIntent,
    Implementation,
    ValidationFile,
    Validation,
    ValidationType,
    Severity,
    ParseError,
    ParseErrors,
    extract_file_references,
)
from intentc.core.parser import (
    parse_intent_file,
    parse_validation_file,
    write_intent_file,
    write_validation_file,
)


class TestTypes:
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
        assert not hasattr(p, "depends_on") or "depends_on" not in p.model_fields

    def test_implementation_defaults(self):
        impl = Implementation(name="default")
        assert impl.name == "default"
        assert impl.body == ""

    def test_validation_type_values(self):
        assert ValidationType.AGENT_VALIDATION == "agent_validation"
        assert ValidationType.FILE_CHECK == "file_check"
        assert ValidationType.COMMAND_CHECK == "command_check"

    def test_severity_values(self):
        assert Severity.ERROR == "error"
        assert Severity.WARNING == "warning"

    def test_validation_defaults(self):
        v = Validation(name="check")
        assert v.type == "agent_validation"
        assert v.severity == Severity.ERROR
        assert v.args == {}

    def test_validation_file_defaults(self):
        vf = ValidationFile(target="core/specs")
        assert vf.agent_profile is None
        assert vf.validations == []


class TestParseError:
    def test_str_without_field(self):
        e = ParseError(path=Path("foo.ic"), field=None, message="bad file")
        assert str(e) == "foo.ic: bad file"

    def test_str_with_field(self):
        e = ParseError(path=Path("foo.ic"), field="name", message="missing")
        assert str(e) == "foo.ic [name]: missing"

    def test_parse_errors_message(self):
        errs = ParseErrors([
            ParseError(path=Path("a.ic"), field=None, message="err1"),
            ParseError(path=Path("b.ic"), field="x", message="err2"),
        ])
        assert "2 parse error(s)" in str(errs)
        assert "a.ic: err1" in str(errs)
        assert "b.ic [x]: err2" in str(errs)


class TestExtractFileReferences:
    def test_simple_file_ref(self):
        refs = extract_file_references("see ui_design.png for details")
        assert "ui_design.png" in refs

    def test_relative_path_ref(self):
        refs = extract_file_references("use ../../design_system/* for styling")
        assert "../../design_system/*" in refs

    def test_no_duplicates(self):
        refs = extract_file_references("see file.png and file.png again")
        assert refs.count("file.png") == 1


class TestParseIntentFile:
    def test_parse_basic(self, tmp_path):
        p = tmp_path / "test.ic"
        p.write_text("---\nname: myfeature\ndepends_on: [core]\ntags: [ui]\n---\n\nSome body text\n")
        result = parse_intent_file(p)
        assert isinstance(result, IntentFile)
        assert result.name == "myfeature"
        assert result.depends_on == ["core"]
        assert result.tags == ["ui"]
        assert "Some body text" in result.body
        assert result.source_path == p

    def test_parse_as_project(self, tmp_path):
        p = tmp_path / "project.ic"
        p.write_text("---\nname: myproject\ntags: [meta]\n---\n\nProject description\n")
        result = parse_intent_file(p, as_project=True)
        assert isinstance(result, ProjectIntent)
        assert result.name == "myproject"

    def test_parse_as_implementation(self, tmp_path):
        p = tmp_path / "impl.ic"
        p.write_text("---\nname: python\n---\n\nPython impl\n")
        result = parse_intent_file(p, as_implementation=True)
        assert isinstance(result, Implementation)
        assert result.name == "python"

    def test_parse_missing_name(self, tmp_path):
        p = tmp_path / "bad.ic"
        p.write_text("---\ntags: [x]\n---\nbody\n")
        with pytest.raises(ParseErrors) as exc_info:
            parse_intent_file(p)
        assert any("name" in str(e) for e in exc_info.value.errors)

    def test_parse_project_with_depends_on_errors(self, tmp_path):
        p = tmp_path / "proj.ic"
        p.write_text("---\nname: proj\ndepends_on: [x]\n---\nbody\n")
        with pytest.raises(ParseErrors) as exc_info:
            parse_intent_file(p, as_project=True)
        assert any("depends_on" in str(e) for e in exc_info.value.errors)

    def test_parse_file_not_found(self, tmp_path):
        with pytest.raises(ParseErrors):
            parse_intent_file(tmp_path / "nope.ic")

    def test_parse_extracts_file_references(self, tmp_path):
        p = tmp_path / "ref.ic"
        p.write_text("---\nname: refs\n---\n\nSee ui_design.png for details\n")
        result = parse_intent_file(p)
        assert "ui_design.png" in result.file_references


class TestParseValidationFile:
    def test_parse_basic(self, tmp_path):
        p = tmp_path / "val.icv"
        p.write_text(
            "target: core/specs\n"
            "validations:\n"
            "  - name: check1\n"
            "    type: file_check\n"
            "    severity: warning\n"
            "    args:\n"
            "      path: foo.py\n"
        )
        vf = parse_validation_file(p)
        assert vf.target == "core/specs"
        assert len(vf.validations) == 1
        assert vf.validations[0].name == "check1"
        assert vf.validations[0].type == "file_check"
        assert vf.validations[0].severity == Severity.WARNING
        assert vf.validations[0].args == {"path": "foo.py"}

    def test_parse_defaults(self, tmp_path):
        p = tmp_path / "val.icv"
        p.write_text("target: feat\nvalidations:\n  - name: v1\n")
        vf = parse_validation_file(p)
        assert vf.validations[0].type == "agent_validation"
        assert vf.validations[0].severity == Severity.ERROR

    def test_parse_missing_target(self, tmp_path):
        p = tmp_path / "val.icv"
        p.write_text("validations: []\n")
        with pytest.raises(ParseErrors):
            parse_validation_file(p)


class TestWriteIntentFile:
    def test_write_and_roundtrip(self, tmp_path):
        original = IntentFile(
            name="feature",
            depends_on=["core"],
            tags=["ui"],
            authors=["alice"],
            body="Feature description here",
        )
        out = tmp_path / "feature.ic"
        write_intent_file(original, out)

        assert out.exists()
        parsed = parse_intent_file(out)
        assert parsed.name == "feature"
        assert parsed.depends_on == ["core"]
        assert parsed.tags == ["ui"]
        assert parsed.authors == ["alice"]
        assert "Feature description here" in parsed.body

    def test_write_uses_source_path(self, tmp_path):
        f = IntentFile(name="x", source_path=tmp_path / "x.ic")
        result = write_intent_file(f)
        assert result == tmp_path / "x.ic"
        assert result.exists()

    def test_write_no_path_raises(self):
        f = IntentFile(name="x")
        with pytest.raises(ValueError):
            write_intent_file(f)

    def test_write_project_intent(self, tmp_path):
        pi = ProjectIntent(name="proj", body="Project body")
        out = tmp_path / "project.ic"
        write_intent_file(pi, out)
        parsed = parse_intent_file(out, as_project=True)
        assert parsed.name == "proj"
        assert "Project body" in parsed.body

    def test_write_implementation(self, tmp_path):
        impl = Implementation(name="go", tags=["lang"], body="Go impl")
        out = tmp_path / "go.ic"
        write_intent_file(impl, out)
        parsed = parse_intent_file(out, as_implementation=True)
        assert parsed.name == "go"
        assert parsed.tags == ["lang"]


class TestWriteValidationFile:
    def test_write_and_roundtrip(self, tmp_path):
        original = ValidationFile(
            target="core/specs",
            validations=[
                Validation(name="check1", type="file_check", severity=Severity.WARNING, args={"path": "f.py"}),
                Validation(name="check2", args={"rubric": "verify it works"}),
            ],
        )
        out = tmp_path / "val.icv"
        write_validation_file(original, out)

        assert out.exists()
        parsed = parse_validation_file(out)
        assert parsed.target == "core/specs"
        assert len(parsed.validations) == 2
        assert parsed.validations[0].name == "check1"
        assert parsed.validations[0].type == "file_check"
        assert parsed.validations[0].severity == Severity.WARNING
        assert parsed.validations[1].name == "check2"
        assert parsed.validations[1].type == "agent_validation"

    def test_write_no_path_raises(self):
        vf = ValidationFile(target="x")
        with pytest.raises(ValueError):
            write_validation_file(vf)

    def test_write_with_agent_profile(self, tmp_path):
        vf = ValidationFile(target="feat", agent_profile="custom", validations=[])
        out = tmp_path / "v.icv"
        write_validation_file(vf, out)
        parsed = parse_validation_file(out)
        assert parsed.agent_profile == "custom"
