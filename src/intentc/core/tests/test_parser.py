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
    ValidationType,
    content_hash,
    extract_file_references,
    parse_intent_file,
    parse_validation_file,
    write_intent_file,
    write_validation_file,
)


# ---------------------------------------------------------------------------
# extract_file_references
# ---------------------------------------------------------------------------


def test_extract_file_references_plain_and_backtick_and_markdown_link():
    body = (
        "See the image ./ui_design.png next to this feature.\n"
        "Also reference the shared design system like `../../design_system/*`.\n"
        "Full details in [core/project](core/project/project.ic).\n"
    )
    refs = extract_file_references(body)
    assert "./ui_design.png" in refs
    assert "../../design_system/*" in refs
    assert "core/project/project.ic" in refs


def test_extract_file_references_ignores_urls_and_plain_words():
    body = (
        "Visit https://example.com/file.png for docs. This is a normal sentence, e.g. one that\n"
        "mentions project.ic, args.rubric, Severity.ERROR and 0.0 — none of which are paths.\n"
        "Link text is ignored: [core/project](../project/project.ic)."
    )
    refs = extract_file_references(body)
    assert refs == ["../project/project.ic"]


def test_extract_file_references_no_duplicates():
    body = "See `./foo.txt` and ./foo.txt again, plus assets/foo.txt."
    refs = extract_file_references(body)
    assert refs == ["./foo.txt", "assets/foo.txt"]


# ---------------------------------------------------------------------------
# parse_intent_file
# ---------------------------------------------------------------------------


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_parse_intent_file_basic(tmp_path):
    path = _write(
        tmp_path / "feature.ic",
        """---
name: core/feature
depends_on:
  - core/other
tags: [alpha, beta]
authors: [jane]
---
Body text referencing `./design.png`.
""",
    )
    intent = parse_intent_file(path)
    assert isinstance(intent, IntentFile)
    assert intent.name == "core/feature"
    assert intent.depends_on == ["core/other"]
    assert intent.tags == ["alpha", "beta"]
    assert intent.authors == ["jane"]
    assert "Body text" in intent.body
    assert "./design.png" in intent.file_references
    assert intent.source_path == path


def test_parse_intent_file_as_project(tmp_path):
    path = _write(
        tmp_path / "project.ic",
        """---
name: myproject
tags: [core]
---
Project body.
""",
    )
    project = parse_intent_file(path, as_project=True)
    assert isinstance(project, ProjectIntent)
    assert project.name == "myproject"
    assert not hasattr(project, "depends_on")


def test_parse_intent_file_project_with_depends_on_is_error(tmp_path):
    path = _write(
        tmp_path / "project.ic",
        """---
name: myproject
depends_on: [core/foo]
---
Body.
""",
    )
    with pytest.raises(ParseErrors) as exc_info:
        parse_intent_file(path, as_project=True)
    assert any(e.field == "depends_on" for e in exc_info.value.errors)


def test_parse_intent_file_as_implementation(tmp_path):
    path = _write(
        tmp_path / "default.ic",
        """---
name: python
depends_on: []
---
Implementation body.
""",
    )
    impl = parse_intent_file(path, as_implementation=True)
    assert isinstance(impl, Implementation)
    assert impl.name == "python"


def test_parse_intent_file_missing_name_is_error(tmp_path):
    path = _write(tmp_path / "bad.ic", "---\ntags: [x]\n---\nBody\n")
    with pytest.raises(ParseErrors) as exc_info:
        parse_intent_file(path)
    errors = exc_info.value.errors
    assert len(errors) == 1
    assert errors[0].field == "name"
    assert str(path) in str(errors[0])


def test_parse_intent_file_bad_field_types_accumulate(tmp_path):
    path = _write(
        tmp_path / "bad.ic",
        """---
name: core/feature
depends_on: "not-a-list"
tags: 5
authors: [1, 2]
---
Body
""",
    )
    with pytest.raises(ParseErrors) as exc_info:
        parse_intent_file(path)
    fields = {e.field for e in exc_info.value.errors}
    assert fields == {"depends_on", "tags", "authors"}


def test_parse_intent_file_missing_frontmatter_is_error(tmp_path):
    path = _write(tmp_path / "bad.ic", "No frontmatter here.\n")
    with pytest.raises(ParseErrors):
        parse_intent_file(path)


def test_parse_intent_file_malformed_yaml_includes_yaml_error(tmp_path):
    path = _write(tmp_path / "bad.ic", "---\nname: [unterminated\n---\nBody\n")
    with pytest.raises(ParseErrors) as exc_info:
        parse_intent_file(path)
    assert len(exc_info.value.errors) == 1
    message = str(exc_info.value.errors[0])
    assert str(path) in message


def test_parse_intent_file_non_mapping_frontmatter_is_error(tmp_path):
    path = _write(tmp_path / "bad.ic", "---\n- one\n- two\n---\nBody\n")
    with pytest.raises(ParseErrors):
        parse_intent_file(path)


# ---------------------------------------------------------------------------
# write_intent_file / round trip
# ---------------------------------------------------------------------------


def test_write_and_reparse_intent_file_round_trip(tmp_path):
    path = tmp_path / "roundtrip.ic"
    intent = IntentFile(
        name="core/roundtrip",
        depends_on=["core/other"],
        tags=["a"],
        authors=["me"],
        body="Some body text.\n",
    )
    written_path = write_intent_file(intent, path)
    assert written_path == path

    reparsed = parse_intent_file(path)
    assert reparsed.name == intent.name
    assert reparsed.depends_on == intent.depends_on
    assert reparsed.tags == intent.tags
    assert reparsed.authors == intent.authors
    assert reparsed.body == intent.body


def test_write_intent_file_uses_source_path_when_no_path_given(tmp_path):
    path = tmp_path / "feature.ic"
    intent = IntentFile(name="core/feature", body="body", source_path=path)
    written_path = write_intent_file(intent)
    assert written_path == path
    assert path.exists()


def test_write_intent_file_no_path_raises():
    intent = IntentFile(name="core/feature", body="body")
    with pytest.raises(ValueError):
        write_intent_file(intent)


def test_write_intent_file_project_omits_depends_on(tmp_path):
    path = tmp_path / "project.ic"
    project = ProjectIntent(name="myproject", body="body")
    write_intent_file(project, path)
    text = path.read_text(encoding="utf-8")
    assert "depends_on" not in text


# ---------------------------------------------------------------------------
# parse_validation_file
# ---------------------------------------------------------------------------


def test_parse_validation_file_basic(tmp_path):
    path = _write(
        tmp_path / "feature.icv",
        """
target: core/feature
version: 2
agent_profile: careful
validations:
  - name: files-exist
    type: file_exists
    args:
      paths: ["foo.py"]
  - name: cmd-passes
    type: command_validation
    severity: warning
    args:
      command: "true"
  - name: rubric-check
    args:
      rubric: "Everything looks good"
""",
    )
    vf = parse_validation_file(path)
    assert isinstance(vf, ValidationFile)
    assert vf.target == "core/feature"
    assert vf.version == 2
    assert vf.agent_profile == "careful"
    assert len(vf.validations) == 3
    assert vf.validations[0].type == ValidationType.FILE_EXISTS.value
    assert vf.validations[1].severity == Severity.WARNING
    assert vf.validations[2].type == ValidationType.AGENT_VALIDATION.value


def test_parse_validation_file_empty_file_is_valid(tmp_path):
    path = _write(tmp_path / "empty.icv", "")
    vf = parse_validation_file(path)
    assert vf.target == ""
    assert vf.validations == []


def test_parse_validation_file_duplicate_name_is_actionable_error(tmp_path):
    path = _write(
        tmp_path / "dup.icv",
        """
target: core/feature
validations:
  - name: same-name
    args:
      rubric: "first"
  - name: same-name
    args:
      rubric: "second"
""",
    )
    with pytest.raises(ParseErrors) as exc_info:
        parse_validation_file(path)
    errors = exc_info.value.errors
    assert any("duplicate" in str(e).lower() for e in errors)
    dup_error = next(e for e in errors if "duplicate" in str(e).lower())
    assert str(path) in str(dup_error)
    assert "validations[1]" in dup_error.field


def test_parse_validation_file_unknown_severity_is_actionable_error(tmp_path):
    path = _write(
        tmp_path / "bad_severity.icv",
        """
target: core/feature
validations:
  - name: check
    severity: catastrophic
    args:
      rubric: "check something"
""",
    )
    with pytest.raises(ParseErrors) as exc_info:
        parse_validation_file(path)
    errors = exc_info.value.errors
    assert len(errors) == 1
    assert "severity" in errors[0].field
    assert str(path) in str(errors[0])
    assert "validations[0]" in errors[0].field


def test_parse_validation_file_agent_validation_without_rubric_is_actionable_error(tmp_path):
    path = _write(
        tmp_path / "no_rubric.icv",
        """
target: core/feature
validations:
  - name: check
    type: agent_validation
""",
    )
    with pytest.raises(ParseErrors) as exc_info:
        parse_validation_file(path)
    errors = exc_info.value.errors
    assert len(errors) == 1
    assert "rubric" in errors[0].field
    assert str(path) in str(errors[0])
    assert "validations[0]" in errors[0].field


def test_parse_validation_file_command_validation_without_command_is_error(tmp_path):
    path = _write(
        tmp_path / "no_command.icv",
        """
target: core/feature
validations:
  - name: check
    type: command_validation
""",
    )
    with pytest.raises(ParseErrors) as exc_info:
        parse_validation_file(path)
    assert any("command" in e.field for e in exc_info.value.errors)


def test_parse_validation_file_file_exists_without_paths_is_error(tmp_path):
    path = _write(
        tmp_path / "no_paths.icv",
        """
target: core/feature
validations:
  - name: check
    type: file_exists
""",
    )
    with pytest.raises(ParseErrors) as exc_info:
        parse_validation_file(path)
    assert any("paths" in e.field for e in exc_info.value.errors)


def test_parse_validation_file_missing_name_is_error(tmp_path):
    path = _write(
        tmp_path / "no_name.icv",
        """
target: core/feature
validations:
  - args:
      rubric: "check"
""",
    )
    with pytest.raises(ParseErrors) as exc_info:
        parse_validation_file(path)
    assert any("name" in e.field for e in exc_info.value.errors)


def test_parse_validation_file_unknown_type_is_accepted(tmp_path):
    path = _write(
        tmp_path / "custom.icv",
        """
target: core/feature
validations:
  - name: custom-check
    type: my_custom_runner
    args:
      whatever: true
""",
    )
    vf = parse_validation_file(path)
    assert vf.validations[0].type == "my_custom_runner"


def test_parse_validation_file_errors_accumulate_not_first_only(tmp_path):
    path = _write(
        tmp_path / "many_errors.icv",
        """
target: core/feature
validations:
  - name: same
    args:
      rubric: "a"
  - name: same
    severity: bogus
    args:
      rubric: "b"
""",
    )
    with pytest.raises(ParseErrors) as exc_info:
        parse_validation_file(path)
    assert len(exc_info.value.errors) >= 2


def test_parse_validation_file_validations_not_a_list_is_error(tmp_path):
    path = _write(tmp_path / "bad.icv", "target: core/feature\nvalidations: nope\n")
    with pytest.raises(ParseErrors):
        parse_validation_file(path)


def test_parse_validation_file_args_not_a_mapping_is_error(tmp_path):
    path = _write(
        tmp_path / "bad_args.icv",
        """
target: core/feature
validations:
  - name: check
    type: agent_validation
    args: "not-a-mapping"
""",
    )
    with pytest.raises(ParseErrors) as exc_info:
        parse_validation_file(path)
    assert any("args" in e.field for e in exc_info.value.errors)


# ---------------------------------------------------------------------------
# write_validation_file / round trip
# ---------------------------------------------------------------------------


def test_write_and_reparse_validation_file_round_trip(tmp_path):
    path = tmp_path / "roundtrip.icv"
    vf = ValidationFile(
        target="core/feature",
        version=1,
        agent_profile="default",
        validations=[
            Validation(
                name="files-exist",
                type=ValidationType.FILE_EXISTS.value,
                severity=Severity.ERROR,
                args={"paths": ["foo.py"]},
            )
        ],
    )
    written_path = write_validation_file(vf, path)
    assert written_path == path

    reparsed = parse_validation_file(path)
    assert reparsed.target == vf.target
    assert reparsed.agent_profile == vf.agent_profile
    assert len(reparsed.validations) == 1
    assert reparsed.validations[0].name == "files-exist"
    assert reparsed.validations[0].args == {"paths": ["foo.py"]}


def test_write_validation_file_no_path_raises():
    vf = ValidationFile(target="core/feature")
    with pytest.raises(ValueError):
        write_validation_file(vf)


# ---------------------------------------------------------------------------
# content_hash
# ---------------------------------------------------------------------------


def test_content_hash_is_stable_and_order_independent(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("hello", encoding="utf-8")
    b.write_text("world", encoding="utf-8")

    hash1 = content_hash([a, b])
    hash2 = content_hash([b, a])
    assert hash1 == hash2


def test_content_hash_changes_when_content_changes(tmp_path):
    a = tmp_path / "a.txt"
    a.write_text("hello", encoding="utf-8")
    hash1 = content_hash([a])
    a.write_text("goodbye", encoding="utf-8")
    hash2 = content_hash([a])
    assert hash1 != hash2


def test_content_hash_missing_file_contributes_name_only(tmp_path):
    missing = tmp_path / "missing.txt"
    present = tmp_path / "present.txt"
    present.write_text("missing", encoding="utf-8")

    hash_missing = content_hash([missing])
    hash_present = content_hash([present])
    assert hash_missing != hash_present
