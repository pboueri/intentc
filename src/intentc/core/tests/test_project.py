from pathlib import Path

import pytest

from intentc.core import (
    ParseErrors,
    blank_project,
    check_project,
    load_project,
    write_project,
)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_project_ic(intent_dir: Path, name: str = "demo") -> None:
    _write(
        intent_dir / "project.ic",
        f"""---
name: {name}
---
A demo project.
""",
    )


def _write_default_impl(intent_dir: Path) -> None:
    _write(
        intent_dir / "implementations" / "default.ic",
        """---
name: default
---
Python 3.11, uv, pydantic.
""",
    )


def _write_feature(
    intent_dir: Path,
    feature_path: str,
    depends_on: list[str] | None = None,
    name: str | None = None,
    body: str = "Do the thing.",
    with_validation: bool = True,
) -> None:
    deps_yaml = ""
    if depends_on:
        deps_yaml = "depends_on:\n" + "\n".join(f"  - {d}" for d in depends_on) + "\n"
    intent_name = name if name is not None else feature_path.rsplit("/", 1)[-1]
    _write(
        intent_dir / feature_path / f"{feature_path.rsplit('/', 1)[-1]}.ic",
        f"""---
name: {intent_name}
{deps_yaml}---
{body}
""",
    )
    if with_validation:
        _write(
            intent_dir / feature_path / "validation.icv",
            f"""target: {feature_path.rsplit('/', 1)[-1]}
version: 1
validations:
  - name: {feature_path.rsplit('/', 1)[-1]}-exists
    type: file_exists
    severity: error
    args:
      paths: ["out.txt"]
""",
        )


def _three_feature_chain(intent_dir: Path) -> None:
    _write_project_ic(intent_dir)
    _write_default_impl(intent_dir)
    _write_feature(intent_dir, "a")
    _write_feature(intent_dir, "b", depends_on=["a"])
    _write_feature(intent_dir, "c", depends_on=["b"])


# ---------------------------------------------------------------------------
# DAG traversal
# ---------------------------------------------------------------------------


def test_dag_traversal_on_three_feature_chain(tmp_path):
    _three_feature_chain(tmp_path)
    project = load_project(tmp_path)

    assert project.parents("c") == ["b"]
    assert project.parents("a") == []
    assert project.ancestors("c") == {"a", "b"}
    assert project.ancestors("a") == set()
    assert project.children("a") == ["b"]
    assert project.descendants("a") == {"b", "c"}
    assert project.descendants("c") == set()

    order = project.topological_order()
    assert order.index("a") < order.index("b") < order.index("c")


def test_topological_order_raises_on_cycle_at_load_time(tmp_path):
    _write_project_ic(tmp_path)
    _write_default_impl(tmp_path)
    _write_feature(tmp_path, "a", depends_on=["b"])
    _write_feature(tmp_path, "b", depends_on=["a"])

    with pytest.raises(ParseErrors) as exc_info:
        load_project(tmp_path)
    assert any("cycle" in str(e).lower() for e in exc_info.value.errors)


def test_buildable_after_returns_only_features_with_built_dependencies(tmp_path):
    _three_feature_chain(tmp_path)
    project = load_project(tmp_path)

    assert project.buildable_after(set()) == ["a"]
    assert project.buildable_after({"a"}) == ["b"]
    assert project.buildable_after({"a", "b"}) == ["c"]
    assert project.buildable_after({"a", "b", "c"}) == []


def test_require_feature_raises_key_error_for_all_dag_methods(tmp_path):
    _three_feature_chain(tmp_path)
    project = load_project(tmp_path)

    with pytest.raises(KeyError):
        project.parents("nope")
    with pytest.raises(KeyError):
        project.ancestors("nope")
    with pytest.raises(KeyError):
        project.children("nope")
    with pytest.raises(KeyError):
        project.descendants("nope")


# ---------------------------------------------------------------------------
# load_project error accumulation
# ---------------------------------------------------------------------------


def test_load_project_raises_parse_errors_for_unknown_dependency(tmp_path):
    _write_project_ic(tmp_path)
    _write_default_impl(tmp_path)
    _write_feature(tmp_path, "a", depends_on=["does-not-exist"])

    with pytest.raises(ParseErrors) as exc_info:
        load_project(tmp_path)

    messages = [str(e) for e in exc_info.value.errors]
    assert any("unknown dependency 'does-not-exist'" in m for m in messages)
    assert any("available: a" in m for m in messages)


def test_load_project_raises_parse_errors_for_wildcard_matching_nothing(tmp_path):
    _write_project_ic(tmp_path)
    _write_default_impl(tmp_path)
    _write_feature(tmp_path, "a", depends_on=["nomatch/*"])

    with pytest.raises(ParseErrors) as exc_info:
        load_project(tmp_path)

    assert any("matches no features" in str(e) for e in exc_info.value.errors)


def test_load_project_accumulates_multiple_errors_at_once(tmp_path):
    _write_project_ic(tmp_path)
    _write_default_impl(tmp_path)
    _write_feature(tmp_path, "a", depends_on=["missing-one"])
    _write_feature(tmp_path, "b", depends_on=["missing-two"])

    with pytest.raises(ParseErrors) as exc_info:
        load_project(tmp_path)

    assert len(exc_info.value.errors) >= 2


def test_load_project_raises_for_self_dependency(tmp_path):
    _write_project_ic(tmp_path)
    _write_default_impl(tmp_path)
    _write_feature(tmp_path, "a", depends_on=["a"])

    with pytest.raises(ParseErrors) as exc_info:
        load_project(tmp_path)
    assert any("cannot depend on itself" in str(e) for e in exc_info.value.errors)


def test_wildcard_dependency_expands_in_place(tmp_path):
    _write_project_ic(tmp_path)
    _write_default_impl(tmp_path)
    _write_feature(tmp_path, "core/one")
    _write_feature(tmp_path, "core/two")
    _write_feature(tmp_path, "downstream", depends_on=["core/*"])

    project = load_project(tmp_path)
    assert set(project.parents("downstream")) == {"core/one", "core/two"}


def test_load_project_missing_project_ic_raises_parse_errors(tmp_path):
    _write_default_impl(tmp_path)
    _write_feature(tmp_path, "a")

    with pytest.raises(ParseErrors) as exc_info:
        load_project(tmp_path)
    assert any("project.ic" in str(e) for e in exc_info.value.errors)


# ---------------------------------------------------------------------------
# check_project
# ---------------------------------------------------------------------------


def test_check_project_empty_for_well_formed_project(tmp_path):
    _three_feature_chain(tmp_path)
    project = load_project(tmp_path)
    assert check_project(project) == []


def test_check_project_flags_icv_target_mismatch(tmp_path):
    _write_project_ic(tmp_path)
    _write_default_impl(tmp_path)
    _write_feature(tmp_path, "store", with_validation=False)
    _write(
        tmp_path / "store" / "validation.icv",
        """target: models
version: 1
validations:
  - name: models-exists
    type: file_exists
    severity: error
    args:
      paths: ["out.txt"]
""",
    )
    project = load_project(tmp_path)
    issues = check_project(project)
    assert any(
        issue.level == "warning" and "differs from the directory" in issue.message for issue in issues
    )


def test_check_project_flags_assertion_target_that_does_not_exist(tmp_path):
    _three_feature_chain(tmp_path)
    _write(
        tmp_path / "assertions" / "top_level.icv",
        """target: no-such-feature
version: 1
validations:
  - name: top-level-check
    type: file_exists
    severity: error
    args:
      paths: ["out.txt"]
""",
    )
    project = load_project(tmp_path)
    issues = check_project(project)
    assert any(
        issue.level == "error" and "does not name an existing feature" in issue.message for issue in issues
    )


def test_check_project_flags_feature_with_no_validations(tmp_path):
    _write_project_ic(tmp_path)
    _write_default_impl(tmp_path)
    _write_feature(tmp_path, "a", with_validation=False)
    project = load_project(tmp_path)
    issues = check_project(project)
    assert any(issue.level == "warning" and "no validations" in issue.message for issue in issues)


def test_check_project_flags_intent_name_mismatch(tmp_path):
    _write_project_ic(tmp_path)
    _write_default_impl(tmp_path)
    _write_feature(tmp_path, "a", name="totally-different")
    project = load_project(tmp_path)
    issues = check_project(project)
    assert any(
        issue.level == "warning" and "differs from its directory" in issue.message for issue in issues
    )


def test_check_project_flags_icv_without_ic(tmp_path):
    _three_feature_chain(tmp_path)
    _write(
        tmp_path / "orphan" / "validation.icv",
        """target: orphan
version: 1
validations:
  - name: orphan-exists
    type: file_exists
    severity: error
    args:
      paths: ["out.txt"]
""",
    )
    project = load_project(tmp_path)
    issues = check_project(project)
    assert any(issue.level == "error" and "no accompanying .ic file" in issue.message for issue in issues)


def test_check_project_never_invokes_agent(tmp_path, monkeypatch):
    _three_feature_chain(tmp_path)
    project = load_project(tmp_path)

    def _boom(*args, **kwargs):
        raise AssertionError("check_project must not invoke an agent")

    import subprocess

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    check_project(project)


# ---------------------------------------------------------------------------
# blank_project / write_project roundtrip
# ---------------------------------------------------------------------------


def test_blank_project_roundtrip(tmp_path):
    project = blank_project("x")
    dest = write_project(project, tmp_path / "intent")
    assert dest == tmp_path / "intent"

    loaded = load_project(dest)
    assert list(loaded.features.keys()) == ["starter"]
    assert len(loaded.features["starter"].validations) == 1
    assert len(loaded.features["starter"].validations[0].validations) == 1
    assert loaded.resolve_implementation() is not None
    assert loaded.resolve_implementation().name == "default"
    assert loaded.resolve_implementation("default").name == "default"

    assert check_project(loaded) == []


def test_write_project_copies_referenced_supporting_files(tmp_path):
    _write_project_ic(tmp_path)
    _write_default_impl(tmp_path)
    _write(
        tmp_path / "a" / "a.ic",
        """---
name: a
---
See `design.png` for details.
""",
    )
    _write(tmp_path / "a" / "design.png", "fake-png-bytes")
    _write(
        tmp_path / "a" / "validation.icv",
        """target: a
version: 1
validations:
  - name: a-exists
    type: file_exists
    severity: error
    args:
      paths: ["out.txt"]
""",
    )
    project = load_project(tmp_path)

    dest = tmp_path.parent / "written_out" / "intent"
    write_project(project, dest)

    assert (dest / "a" / "a.ic").exists()
    assert (dest / "a" / "design.png").exists()
    assert (dest / "a" / "design.png").read_text() == "fake-png-bytes"


def test_resolve_implementation_raises_key_error_and_value_error(tmp_path):
    project = blank_project("x")
    with pytest.raises(KeyError):
        project.resolve_implementation("nonexistent")

    del project.implementations["default"]
    project.implementations["one"] = _impl("one")
    project.implementations["two"] = _impl("two")
    with pytest.raises(ValueError):
        project.resolve_implementation()


def _impl(name: str):
    from intentc.core import Implementation

    return Implementation(name=name, body="body")
