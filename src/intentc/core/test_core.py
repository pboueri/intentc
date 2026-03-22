"""Tests for core types and parser."""

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


# ---------------------------------------------------------------------------
# Type construction tests
# ---------------------------------------------------------------------------


class TestTypes:
    def test_intent_file_defaults(self):
        f = IntentFile(name="feat")
        assert f.name == "feat"
        assert f.depends_on == []
        assert f.tags == []
        assert f.authors == []
        assert f.body == ""
        assert f.file_references == []
        assert f.source_path is None

    def test_intent_file_full(self):
        f = IntentFile(
            name="core/specs",
            depends_on=["core/*"],
            tags=["foundation"],
            authors=["alice"],
            body="Some body",
            file_references=["design.png"],
            source_path=Path("/tmp/test.ic"),
        )
        assert f.depends_on == ["core/*"]
        assert f.source_path == Path("/tmp/test.ic")

    def test_project_intent_no_depends_on(self):
        p = ProjectIntent(name="project")
        assert not hasattr(p, "depends_on") or "depends_on" not in p.model_fields

    def test_implementation(self):
        impl = Implementation(name="python", depends_on=["core"])
        assert impl.depends_on == ["core"]

    def test_validation_file(self):
        vf = ValidationFile(
            target="core/specs",
            validations=[
                Validation(name="check1", type=ValidationType.FILE_CHECK, args={"path": "foo.py"}),
                Validation(name="check2", severity=Severity.WARNING),
            ],
        )
        assert len(vf.validations) == 2
        assert vf.validations[0].type == ValidationType.FILE_CHECK
        assert vf.validations[1].severity == Severity.WARNING
        assert vf.validations[1].type == ValidationType.AGENT_VALIDATION

    def test_validation_type_values(self):
        assert ValidationType.AGENT_VALIDATION.value == "agent_validation"
        assert ValidationType.LLM_JUDGE.value == "llm_judge"
        assert ValidationType.FILE_CHECK.value == "file_check"
        assert ValidationType.FOLDER_CHECK.value == "folder_check"
        assert ValidationType.COMMAND_CHECK.value == "command_check"

    def test_severity_values(self):
        assert Severity.ERROR.value == "error"
        assert Severity.WARNING.value == "warning"

    def test_extra_fields_ignored(self):
        f = IntentFile(name="feat", unknown_field="ignored")
        assert not hasattr(f, "unknown_field")


class TestParseError:
    def test_str_no_field(self):
        e = ParseError(path=Path("foo.ic"), field=None, message="bad file")
        assert str(e) == "foo.ic: bad file"

    def test_str_with_field(self):
        e = ParseError(path=Path("foo.ic"), field="name", message="missing")
        assert str(e) == "foo.ic [name]: missing"

    def test_parse_errors_exception(self):
        errs = ParseErrors([
            ParseError(path=Path("a.ic"), field=None, message="err1"),
            ParseError(path=Path("b.ic"), field="x", message="err2"),
        ])
        assert "2 parse error(s)" in str(errs)
        assert "a.ic: err1" in str(errs)
        assert "b.ic [x]: err2" in str(errs)


class TestExtractFileReferences:
    def test_simple_filename(self):
        refs = extract_file_references("see ui_design.png for details")
        assert "ui_design.png" in refs

    def test_relative_path(self):
        refs = extract_file_references("refer to ../../design_system/tokens.yaml")
        assert any("design_system/tokens.yaml" in r for r in refs)

    def test_no_refs_in_plain_text(self):
        refs = extract_file_references("This is just plain text with no files")
        assert refs == []


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------

IC_CONTENT = """\
---
name: core/specs
depends_on:
  - foundation
tags:
  - core
authors:
  - alice
---
# Feature spec

Refer to design.png for the mockup.
"""

ICV_CONTENT = """\
---
target: core/specs
agent_profile: default
validations:
  - name: types-exist
    type: file_check
    severity: error
    args:
      path: types.py
  - name: quality
    type: agent_validation
    severity: warning
    args:
      rubric: Code is clean
---
"""


class TestParseIntentFile:
    def test_parse_basic(self, tmp_path: Path):
        p = tmp_path / "test.ic"
        p.write_text(IC_CONTENT)
        result = parse_intent_file(p)
        assert isinstance(result, IntentFile)
        assert result.name == "core/specs"
        assert result.depends_on == ["foundation"]
        assert result.tags == ["core"]
        assert result.authors == ["alice"]
        assert "# Feature spec" in result.body
        assert result.source_path == p

    def test_parse_as_project(self, tmp_path: Path):
        p = tmp_path / "project.ic"
        p.write_text("---\nname: myproject\ntags: [proj]\n---\nProject body\n")
        result = parse_intent_file(p, as_project=True)
        assert isinstance(result, ProjectIntent)
        assert result.name == "myproject"

    def test_parse_as_project_rejects_depends_on(self, tmp_path: Path):
        p = tmp_path / "project.ic"
        p.write_text("---\nname: myproject\ndepends_on: [x]\n---\nbody\n")
        with pytest.raises(ParseErrors) as exc_info:
            parse_intent_file(p, as_project=True)
        assert any("depends_on" in str(e) for e in exc_info.value.errors)

    def test_parse_as_implementation(self, tmp_path: Path):
        p = tmp_path / "impl.ic"
        p.write_text("---\nname: python\ndepends_on: [core]\n---\nPython impl\n")
        result = parse_intent_file(p, as_implementation=True)
        assert isinstance(result, Implementation)
        assert result.depends_on == ["core"]

    def test_parse_missing_name(self, tmp_path: Path):
        p = tmp_path / "bad.ic"
        p.write_text("---\ntags: [x]\n---\nbody\n")
        with pytest.raises(ParseErrors) as exc_info:
            parse_intent_file(p)
        assert any("name" in str(e) for e in exc_info.value.errors)

    def test_parse_missing_file(self, tmp_path: Path):
        with pytest.raises(ParseErrors):
            parse_intent_file(tmp_path / "nonexistent.ic")

    def test_parse_no_front_matter(self, tmp_path: Path):
        p = tmp_path / "no_fm.ic"
        p.write_text("just text, no front matter")
        with pytest.raises(ParseErrors):
            parse_intent_file(p)

    def test_file_references_extracted(self, tmp_path: Path):
        p = tmp_path / "refs.ic"
        p.write_text("---\nname: feat\n---\nSee design.png and ../shared/tokens.yaml\n")
        result = parse_intent_file(p)
        assert "design.png" in result.file_references


class TestParseValidationFile:
    def test_parse_basic(self, tmp_path: Path):
        p = tmp_path / "test.icv"
        p.write_text(ICV_CONTENT)
        result = parse_validation_file(p)
        assert isinstance(result, ValidationFile)
        assert result.target == "core/specs"
        assert result.agent_profile == "default"
        assert len(result.validations) == 2
        assert result.validations[0].name == "types-exist"
        assert result.validations[0].type == ValidationType.FILE_CHECK
        assert result.validations[0].severity == Severity.ERROR
        assert result.validations[0].args == {"path": "types.py"}
        assert result.validations[1].severity == Severity.WARNING

    def test_parse_missing_target(self, tmp_path: Path):
        p = tmp_path / "bad.icv"
        p.write_text("---\nvalidations:\n  - name: x\n---\n")
        with pytest.raises(ParseErrors):
            parse_validation_file(p)

    def test_parse_missing_validations(self, tmp_path: Path):
        p = tmp_path / "bad.icv"
        p.write_text("---\ntarget: feat\n---\n")
        with pytest.raises(ParseErrors):
            parse_validation_file(p)


# ---------------------------------------------------------------------------
# Writer tests
# ---------------------------------------------------------------------------


class TestWriteIntentFile:
    def test_write_and_read_back(self, tmp_path: Path):
        original = IntentFile(
            name="core/specs",
            depends_on=["foundation"],
            tags=["core"],
            authors=["alice"],
            body="# My feature\n\nDetails here.\n",
        )
        out = tmp_path / "feature.ic"
        result_path = write_intent_file(original, out)
        assert result_path == out
        assert out.exists()

        roundtripped = parse_intent_file(out)
        assert roundtripped.name == original.name
        assert roundtripped.depends_on == original.depends_on
        assert roundtripped.tags == original.tags
        assert roundtripped.authors == original.authors
        assert roundtripped.body.strip() == original.body.strip()

    def test_write_project_intent(self, tmp_path: Path):
        proj = ProjectIntent(name="myproject", body="Project body\n")
        out = tmp_path / "project.ic"
        write_intent_file(proj, out)
        roundtripped = parse_intent_file(out, as_project=True)
        assert isinstance(roundtripped, ProjectIntent)
        assert roundtripped.name == "myproject"

    def test_write_uses_source_path(self, tmp_path: Path):
        out = tmp_path / "auto.ic"
        intent = IntentFile(name="auto", source_path=out)
        write_intent_file(intent)
        assert out.exists()

    def test_write_no_path_raises(self):
        intent = IntentFile(name="no-path")
        with pytest.raises(ValueError):
            write_intent_file(intent)

    def test_write_creates_parent_dirs(self, tmp_path: Path):
        out = tmp_path / "deep" / "nested" / "feature.ic"
        intent = IntentFile(name="deep")
        write_intent_file(intent, out)
        assert out.exists()


class TestWriteValidationFile:
    def test_write_and_read_back(self, tmp_path: Path):
        original = ValidationFile(
            target="core/specs",
            agent_profile="default",
            validations=[
                Validation(
                    name="types-exist",
                    type=ValidationType.FILE_CHECK,
                    severity=Severity.ERROR,
                    args={"path": "types.py"},
                ),
                Validation(
                    name="quality",
                    type=ValidationType.AGENT_VALIDATION,
                    severity=Severity.WARNING,
                    args={"rubric": "Code is clean"},
                ),
            ],
        )
        out = tmp_path / "test.icv"
        write_validation_file(original, out)
        assert out.exists()

        roundtripped = parse_validation_file(out)
        assert roundtripped.target == original.target
        assert roundtripped.agent_profile == original.agent_profile
        assert len(roundtripped.validations) == 2
        assert roundtripped.validations[0].name == "types-exist"
        assert roundtripped.validations[0].type == ValidationType.FILE_CHECK
        assert roundtripped.validations[1].severity == Severity.WARNING

    def test_write_no_path_raises(self):
        vf = ValidationFile(target="x", validations=[])
        with pytest.raises(ValueError):
            write_validation_file(vf)


# ---------------------------------------------------------------------------
# Round-trip tests
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_intent_file_roundtrip(self, tmp_path: Path):
        """Write -> parse -> write -> parse yields identical data."""
        original = IntentFile(
            name="roundtrip/test",
            depends_on=["a", "b"],
            tags=["t1", "t2"],
            authors=["bob"],
            body="Body with design.png reference\n",
        )
        p1 = tmp_path / "v1.ic"
        write_intent_file(original, p1)
        loaded = parse_intent_file(p1)

        p2 = tmp_path / "v2.ic"
        write_intent_file(loaded, p2)
        reloaded = parse_intent_file(p2)

        assert loaded.name == reloaded.name
        assert loaded.depends_on == reloaded.depends_on
        assert loaded.tags == reloaded.tags
        assert loaded.authors == reloaded.authors
        assert loaded.body == reloaded.body

    def test_validation_file_roundtrip(self, tmp_path: Path):
        original = ValidationFile(
            target="feat",
            validations=[
                Validation(name="v1", type=ValidationType.COMMAND_CHECK, args={"cmd": "echo ok"}),
            ],
        )
        p1 = tmp_path / "v1.icv"
        write_validation_file(original, p1)
        loaded = parse_validation_file(p1)

        p2 = tmp_path / "v2.icv"
        write_validation_file(loaded, p2)
        reloaded = parse_validation_file(p2)

        assert loaded.target == reloaded.target
        assert len(loaded.validations) == len(reloaded.validations)
        assert loaded.validations[0].name == reloaded.validations[0].name
        assert loaded.validations[0].type == reloaded.validations[0].type
