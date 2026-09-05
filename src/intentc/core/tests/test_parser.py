"""Tests for parsing and writing .ic / .icv files."""

from __future__ import annotations

from pathlib import Path

import pytest

from intentc.core import (
    Implementation,
    IntentFile,
    ParseErrors,
    ProjectIntent,
    Severity,
    Validation,
    ValidationFile,
    content_hash,
    extract_file_references,
    parse_intent_file,
    parse_validation_file,
    write_intent_file,
    write_validation_file,
)


# ---------------------------------------------------------------------------
# .ic parsing
# ---------------------------------------------------------------------------


def test_parse_intent_file(tmp_path: Path) -> None:
    p = tmp_path / "feature.ic"
    p.write_text(
        "---\nname: feature\ndepends_on: [core/models]\ntags: [a]\nauthors: [me]\n---\n\n# Feature\n\nBody text.\n"
    )
    intent = parse_intent_file(p)
    assert isinstance(intent, IntentFile)
    assert intent.name == "feature"
    assert intent.depends_on == ["core/models"]
    assert intent.tags == ["a"]
    assert intent.authors == ["me"]
    assert intent.body == "# Feature\n\nBody text."
    assert intent.source_path == p


def test_parse_intent_file_as_project_and_implementation(tmp_path: Path) -> None:
    p = tmp_path / "project.ic"
    p.write_text("---\nname: proj\n---\n\nAbout.\n")
    project = parse_intent_file(p, as_project=True)
    assert isinstance(project, ProjectIntent)
    assert project.body == "About."

    i = tmp_path / "default.ic"
    i.write_text("---\nname: default\n---\n\nPython.\n")
    impl = parse_intent_file(i, as_implementation=True)
    assert isinstance(impl, Implementation)
    assert impl.name == "default"


def test_parse_intent_missing_name_is_error(tmp_path: Path) -> None:
    p = tmp_path / "x.ic"
    p.write_text("---\ntags: [a]\n---\nbody")
    with pytest.raises(ParseErrors) as excinfo:
        parse_intent_file(p)
    assert excinfo.value.errors[0].field == "name"
    assert str(p) in str(excinfo.value)


def test_parse_intent_bad_list_types_accumulate(tmp_path: Path) -> None:
    p = tmp_path / "x.ic"
    p.write_text("---\nname: x\ndepends_on: 3\ntags: {a: b}\n---\n")
    with pytest.raises(ParseErrors) as excinfo:
        parse_intent_file(p)
    fields = {e.field for e in excinfo.value.errors}
    assert fields == {"depends_on", "tags"}


def test_parse_project_with_depends_on_is_error(tmp_path: Path) -> None:
    p = tmp_path / "project.ic"
    p.write_text("---\nname: p\ndepends_on: [x]\n---\n")
    with pytest.raises(ParseErrors) as excinfo:
        parse_intent_file(p, as_project=True)
    assert excinfo.value.errors[0].field == "depends_on"


def test_parse_intent_invalid_yaml(tmp_path: Path) -> None:
    p = tmp_path / "x.ic"
    p.write_text("---\nname: [unclosed\n---\nbody")
    with pytest.raises(ParseErrors) as excinfo:
        parse_intent_file(p)
    assert "invalid YAML" in str(excinfo.value)


def test_parse_intent_unclosed_frontmatter(tmp_path: Path) -> None:
    p = tmp_path / "x.ic"
    p.write_text("---\nname: x\nbody")
    with pytest.raises(ParseErrors) as excinfo:
        parse_intent_file(p)
    assert "never closed" in str(excinfo.value)


def test_parse_intent_without_frontmatter_is_missing_name(tmp_path: Path) -> None:
    p = tmp_path / "x.ic"
    p.write_text("# Just markdown\n")
    with pytest.raises(ParseErrors):
        parse_intent_file(p)


def test_file_references_extracted(tmp_path: Path) -> None:
    p = tmp_path / "x.ic"
    p.write_text(
        "---\nname: x\n---\nSee ui_design/mock.png and ../../design_system/* plus https://example.com/a.png "
        "and `code/ident.py` and prompts/build.prompt.\n"
    )
    intent = parse_intent_file(p)
    assert "ui_design/mock.png" in intent.file_references
    assert "../../design_system/*" in intent.file_references
    assert "prompts/build.prompt" in intent.file_references
    assert not any(r.startswith("https") or "example.com" in r for r in intent.file_references)


def test_extract_file_references_dedupes_and_ignores_words() -> None:
    refs = extract_file_references("a/b.md then a/b.md and just words and/or here")
    assert refs == ["a/b.md"]


# ---------------------------------------------------------------------------
# .icv parsing
# ---------------------------------------------------------------------------


def test_parse_validation_file(tmp_path: Path) -> None:
    p = tmp_path / "v.icv"
    p.write_text(
        "target: models\nversion: 2\nagent_profile: fast\nvalidations:\n"
        "  - name: a\n    type: agent_validation\n    severity: warning\n    args:\n      rubric: check it thoroughly\n"
        "  - name: b\n    type: command_validation\n    args:\n      command: exit 0\n"
        "  - name: c\n    type: file_exists\n    args:\n      paths: [x.py]\n"
        "  - name: d\n    type: custom_thing\n"
    )
    vf = parse_validation_file(p)
    assert vf.target == "models"
    assert vf.version == 2
    assert vf.agent_profile == "fast"
    assert [v.name for v in vf.validations] == ["a", "b", "c", "d"]
    assert vf.validations[0].severity is Severity.WARNING
    assert vf.validations[1].type == "command_validation"
    assert vf.validations[3].type == "custom_thing"
    assert vf.source_path == p


def test_parse_empty_validation_file(tmp_path: Path) -> None:
    p = tmp_path / "empty.icv"
    p.write_text("")
    vf = parse_validation_file(p)
    assert vf.target == ""
    assert vf.validations == []


def test_parse_validation_defaults(tmp_path: Path) -> None:
    p = tmp_path / "v.icv"
    p.write_text("target: t\nvalidations:\n  - name: only\n    args:\n      rubric: something long enough\n")
    vf = parse_validation_file(p)
    assert vf.validations[0].type == "agent_validation"
    assert vf.validations[0].severity is Severity.ERROR


def test_parse_validation_errors_accumulate(tmp_path: Path) -> None:
    p = tmp_path / "bad.icv"
    p.write_text(
        "target: t\nvalidations:\n"
        "  - name: dup\n    args:\n      rubric: r\n"
        "  - name: dup\n    args:\n      rubric: r\n"
        "  - name: sev\n    severity: fatal\n    args:\n      rubric: r\n"
        "  - name: norubric\n    type: agent_validation\n"
        "  - name: nocmd\n    type: command_validation\n"
        "  - name: nopaths\n    type: file_exists\n    args:\n      paths: []\n"
        "  - type: agent_validation\n    args:\n      rubric: r\n"
        "  - just a string\n"
    )
    with pytest.raises(ParseErrors) as excinfo:
        parse_validation_file(p)
    msgs = [str(e) for e in excinfo.value.errors]
    assert any("duplicate validation name 'dup'" in m for m in msgs)
    assert any("unknown severity 'fatal'" in m and "validations[2].severity" in m for m in msgs)
    assert any("requires args.rubric" in m and "validations[3]" in m for m in msgs)
    assert any("requires args.command" in m for m in msgs)
    assert any("requires args.paths" in m for m in msgs)
    assert any("missing a 'name'" in m and "validations[6]" in m for m in msgs)
    assert any("must be a mapping" in m and "validations[7]" in m for m in msgs)
    assert all(str(p) in m for m in msgs)


def test_parse_validation_not_a_list(tmp_path: Path) -> None:
    p = tmp_path / "bad.icv"
    p.write_text("target: t\nvalidations: nope\n")
    with pytest.raises(ParseErrors) as excinfo:
        parse_validation_file(p)
    assert excinfo.value.errors[0].field == "validations"


def test_parse_validation_invalid_yaml(tmp_path: Path) -> None:
    p = tmp_path / "bad.icv"
    p.write_text("target: [\n")
    with pytest.raises(ParseErrors) as excinfo:
        parse_validation_file(p)
    assert "invalid YAML" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Writing and round trips
# ---------------------------------------------------------------------------


def test_intent_roundtrip(tmp_path: Path) -> None:
    intent = IntentFile(name="f", depends_on=["a", "b"], tags=["t"], authors=["me"], body="# Hi\n\nBody")
    out = write_intent_file(intent, tmp_path / "sub" / "f.ic")
    assert out.exists()
    back = parse_intent_file(out)
    assert back.name == "f"
    assert back.depends_on == ["a", "b"]
    assert back.tags == ["t"]
    assert back.authors == ["me"]
    assert back.body == "# Hi\n\nBody"


def test_project_roundtrip_has_no_depends_on(tmp_path: Path) -> None:
    out = write_intent_file(ProjectIntent(name="p", body="B"), tmp_path / "project.ic")
    assert "depends_on" not in out.read_text()
    assert parse_intent_file(out, as_project=True).body == "B"


def test_write_intent_uses_source_path(tmp_path: Path) -> None:
    intent = IntentFile(name="f", source_path=tmp_path / "f.ic")
    assert write_intent_file(intent) == tmp_path / "f.ic"
    with pytest.raises(ValueError):
        write_intent_file(IntentFile(name="nopath"))


def test_validation_roundtrip(tmp_path: Path) -> None:
    vf = ValidationFile(
        target="feat",
        version=1,
        agent_profile="fast",
        validations=[
            Validation(name="a", type="agent_validation", severity=Severity.WARNING, args={"rubric": "long rubric text"}),
            Validation(name="b", type="file_exists", args={"paths": ["x/*.py"]}),
        ],
    )
    out = write_validation_file(vf, tmp_path / "v.icv")
    text = out.read_text()
    assert not text.startswith("---")
    back = parse_validation_file(out)
    assert back.target == "feat"
    assert back.agent_profile == "fast"
    assert back.validations[0].severity is Severity.WARNING
    assert back.validations[1].args == {"paths": ["x/*.py"]}


def test_content_hash_is_stable_and_content_sensitive(tmp_path: Path) -> None:
    a = tmp_path / "a.ic"
    b = tmp_path / "b.icv"
    a.write_text("one")
    b.write_text("two")
    h1 = content_hash([a, b])
    assert h1 == content_hash([b, a])
    b.write_text("three")
    assert content_hash([a, b]) != h1
    assert content_hash([tmp_path / "missing"]) == content_hash([tmp_path / "missing"])
    assert len(h1) == 64
