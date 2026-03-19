"""Tests for core specification types and file I/O."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from intentc.core.types import (
    Implementation,
    IntentFile,
    ProjectIntent,
    Severity,
    Validation,
    ValidationFile,
    ValidationType,
    extract_file_references,
)
from intentc.core.parser import (
    ParseError,
    ParseErrors,
    parse_intent_file,
    parse_validation_file,
    write_intent_file,
    write_validation_file,
)


# ---------------------------------------------------------------------------
# Type construction
# ---------------------------------------------------------------------------


class TestValidation:
    def test_defaults(self):
        v = Validation(type=ValidationType.LLM_JUDGE, name="check-it")
        assert v.severity == Severity.ERROR
        assert v.args == {}

    def test_with_args(self):
        v = Validation(
            type=ValidationType.COMMAND_CHECK,
            name="lint",
            severity=Severity.WARNING,
            args={"command": "ruff check"},
        )
        assert v.args["command"] == "ruff check"
        assert v.severity == Severity.WARNING

    def test_extra_fields_ignored(self):
        v = Validation(type=ValidationType.FILE_CHECK, name="f", future_field="hello")
        assert not hasattr(v, "future_field")


class TestValidationFile:
    def test_minimal(self):
        vf = ValidationFile(target="core/specs")
        assert vf.validations == []
        assert vf.agent_profile is None

    def test_full(self):
        vf = ValidationFile(
            target="core/specs",
            agent_profile="default",
            validations=[
                Validation(type=ValidationType.LLM_JUDGE, name="t1", args={"rubric": "check stuff"}),
            ],
        )
        assert len(vf.validations) == 1
        assert vf.validations[0].args["rubric"] == "check stuff"


class TestIntentFile:
    def test_minimal(self):
        i = IntentFile(name="feature-a")
        assert i.depends_on == []
        assert i.body == ""
        assert i.file_references == []

    def test_full(self):
        i = IntentFile(
            name="feature-a",
            depends_on=["core/specs"],
            tags=["foundation"],
            authors=["alice"],
            body="# My Feature\n\nSee [design](./design.png)",
            file_references=["./design.png"],
        )
        assert i.depends_on == ["core/specs"]
        assert i.tags == ["foundation"]


class TestProjectIntent:
    def test_rejects_depends_on(self):
        with pytest.raises(ValueError, match="depends_on"):
            ProjectIntent(name="myproj", depends_on=["something"])

    def test_accepts_without_depends_on(self):
        p = ProjectIntent(name="myproj", tags=["meta"])
        assert p.name == "myproj"


class TestImplementation:
    def test_basic(self):
        impl = Implementation(name="impl", body="Python 3.11+")
        assert impl.name == "impl"


# ---------------------------------------------------------------------------
# File reference extraction
# ---------------------------------------------------------------------------


class TestExtractFileReferences:
    def test_markdown_image(self):
        refs = extract_file_references("![ui](ui_design.png)")
        assert "ui_design.png" in refs

    def test_markdown_link(self):
        refs = extract_file_references("See [guide](../docs/guide.md)")
        assert "../docs/guide.md" in refs

    def test_ignores_urls(self):
        refs = extract_file_references("[link](https://example.com/foo.png)")
        assert refs == []

    def test_ignores_anchors(self):
        refs = extract_file_references("[link](#section)")
        assert refs == []

    def test_relative_path(self):
        refs = extract_file_references("Check ../../design_system/tokens.yaml for details")
        assert "../../design_system/tokens.yaml" in refs

    def test_deduplication(self):
        refs = extract_file_references("![a](icon.png) and ![b](icon.png)")
        assert refs.count("icon.png") == 1


# ---------------------------------------------------------------------------
# File I/O — .ic files
# ---------------------------------------------------------------------------


class TestParseIntentFile:
    def test_roundtrip(self, tmp_path: Path):
        ic = tmp_path / "feature.ic"
        ic.write_text(textwrap.dedent("""\
            ---
            name: my-feature
            depends_on:
              - core/specs
            tags:
              - foundation
            ---

            # My Feature

            This feature does things. See [mockup](./mockup.png).
        """))

        intent = parse_intent_file(ic)
        assert isinstance(intent, IntentFile)
        assert intent.name == "my-feature"
        assert intent.depends_on == ["core/specs"]
        assert intent.tags == ["foundation"]
        assert "# My Feature" in intent.body
        assert "./mockup.png" in intent.file_references
        assert intent.source_path == ic

    def test_write_and_reparse(self, tmp_path: Path):
        original = IntentFile(
            name="roundtrip",
            depends_on=["a", "b"],
            tags=["test"],
            body="# Hello\n\nWorld",
            source_path=tmp_path / "roundtrip.ic",
        )
        write_intent_file(original)
        reparsed = parse_intent_file(original.source_path)
        assert reparsed.name == original.name
        assert reparsed.depends_on == original.depends_on
        assert reparsed.tags == original.tags
        assert "# Hello" in reparsed.body

    def test_missing_file(self, tmp_path: Path):
        with pytest.raises(ParseErrors) as exc_info:
            parse_intent_file(tmp_path / "nope.ic")
        assert "File not found" in str(exc_info.value)

    def test_missing_frontmatter(self, tmp_path: Path):
        ic = tmp_path / "bad.ic"
        ic.write_text("no frontmatter here")
        with pytest.raises(ParseErrors) as exc_info:
            parse_intent_file(ic)
        assert "frontmatter" in str(exc_info.value).lower()

    def test_missing_name(self, tmp_path: Path):
        ic = tmp_path / "noname.ic"
        ic.write_text("---\ntags: [a]\n---\nbody")
        with pytest.raises(ParseErrors) as exc_info:
            parse_intent_file(ic)
        assert "name" in str(exc_info.value)

    def test_parse_as_project(self, tmp_path: Path):
        ic = tmp_path / "project.ic"
        ic.write_text("---\nname: myproj\ntags: [meta]\n---\n# The Project")
        proj = parse_intent_file(ic, as_project=True)
        assert isinstance(proj, ProjectIntent)
        assert proj.name == "myproj"

    def test_parse_as_project_rejects_depends_on(self, tmp_path: Path):
        ic = tmp_path / "project.ic"
        ic.write_text("---\nname: myproj\ndepends_on: [something]\n---\nbody")
        with pytest.raises(ParseErrors):
            parse_intent_file(ic, as_project=True)

    def test_parse_as_implementation(self, tmp_path: Path):
        ic = tmp_path / "implementation.ic"
        ic.write_text("---\nname: impl\ntags: [meta]\n---\n# Stack\nPython 3.11+")
        impl = parse_intent_file(ic, as_implementation=True)
        assert isinstance(impl, Implementation)

    def test_empty_frontmatter_body(self, tmp_path: Path):
        ic = tmp_path / "empty.ic"
        ic.write_text("---\nname: empty\n---\n")
        intent = parse_intent_file(ic)
        assert intent.name == "empty"
        assert intent.body == ""


# ---------------------------------------------------------------------------
# File I/O — .icv files
# ---------------------------------------------------------------------------


class TestParseValidationFile:
    def test_roundtrip(self, tmp_path: Path):
        icv = tmp_path / "validations.icv"
        icv.write_text(textwrap.dedent("""\
            target: core
            validations:
              - name: types-exist
                type: llm_judge
                severity: error
                args:
                  rubric: |
                    Check that types exist
              - name: has-init
                type: file_check
                severity: warning
                args:
                  path: __init__.py
        """))

        vf = parse_validation_file(icv)
        assert vf.target == "core"
        assert len(vf.validations) == 2
        assert vf.validations[0].type == ValidationType.LLM_JUDGE
        assert vf.validations[0].severity == Severity.ERROR
        assert "Check that types exist" in vf.validations[0].args["rubric"]
        assert vf.validations[1].type == ValidationType.FILE_CHECK
        assert vf.validations[1].severity == Severity.WARNING

    def test_write_and_reparse(self, tmp_path: Path):
        original = ValidationFile(
            target="feature/a",
            agent_profile="default",
            validations=[
                Validation(type=ValidationType.COMMAND_CHECK, name="lint", args={"command": "ruff check"}),
            ],
            source_path=tmp_path / "vals.icv",
        )
        write_validation_file(original)
        reparsed = parse_validation_file(original.source_path)
        assert reparsed.target == "feature/a"
        assert reparsed.agent_profile == "default"
        assert len(reparsed.validations) == 1
        assert reparsed.validations[0].name == "lint"

    def test_missing_file(self, tmp_path: Path):
        with pytest.raises(ParseErrors) as exc_info:
            parse_validation_file(tmp_path / "nope.icv")
        assert "File not found" in str(exc_info.value)

    def test_missing_target(self, tmp_path: Path):
        icv = tmp_path / "bad.icv"
        icv.write_text("validations: []")
        with pytest.raises(ParseErrors) as exc_info:
            parse_validation_file(icv)
        assert "target" in str(exc_info.value)

    def test_invalid_yaml(self, tmp_path: Path):
        icv = tmp_path / "bad.icv"
        icv.write_text(": : : not yaml [[[")
        with pytest.raises(ParseErrors) as exc_info:
            parse_validation_file(icv)
        assert "YAML" in str(exc_info.value)

    def test_empty_file(self, tmp_path: Path):
        icv = tmp_path / "empty.icv"
        icv.write_text("")
        with pytest.raises(ParseErrors) as exc_info:
            parse_validation_file(icv)
        assert "target" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------


class TestWriteIntentFile:
    def test_creates_parent_dirs(self, tmp_path: Path):
        intent = IntentFile(name="nested", body="content")
        dest = tmp_path / "a" / "b" / "feature.ic"
        write_intent_file(intent, dest)
        assert dest.exists()

    def test_raises_without_path(self):
        intent = IntentFile(name="orphan", body="no path")
        with pytest.raises(ValueError, match="destination"):
            write_intent_file(intent)


class TestWriteValidationFile:
    def test_creates_parent_dirs(self, tmp_path: Path):
        vf = ValidationFile(target="core")
        dest = tmp_path / "deep" / "validations.icv"
        write_validation_file(vf, dest)
        assert dest.exists()

    def test_raises_without_path(self):
        vf = ValidationFile(target="core")
        with pytest.raises(ValueError, match="destination"):
            write_validation_file(vf)


# ---------------------------------------------------------------------------
# Parse the actual intent files in this repo
# ---------------------------------------------------------------------------


class TestParseRealFiles:
    """Parse the actual .ic and .icv files shipped with this repo."""

    INTENT_ROOT = Path(__file__).resolve().parents[3] / "intent"

    def test_parse_project_ic(self):
        path = self.INTENT_ROOT / "project.ic"
        if not path.exists():
            pytest.skip("project.ic not found")
        proj = parse_intent_file(path, as_project=True)
        assert isinstance(proj, ProjectIntent)
        assert proj.name == "intentc"

    def test_parse_implementation_ic(self):
        path = self.INTENT_ROOT / "implementations" / "default.ic"
        if not path.exists():
            pytest.skip("implementations/default.ic not found")
        impl = parse_intent_file(path, as_implementation=True)
        assert isinstance(impl, Implementation)
        assert impl.name == "implementation"

    def test_parse_specifications_ic(self):
        path = self.INTENT_ROOT / "core" / "specifications" / "specifications.ic"
        if not path.exists():
            pytest.skip("specifications.ic not found")
        intent = parse_intent_file(path)
        assert intent.name == "core"

    def test_parse_specifications_icv(self):
        path = self.INTENT_ROOT / "core" / "specifications" / "validations.icv"
        if not path.exists():
            pytest.skip("validations.icv not found")
        vf = parse_validation_file(path)
        assert vf.target == "core/specifications"
        assert len(vf.validations) == 2
