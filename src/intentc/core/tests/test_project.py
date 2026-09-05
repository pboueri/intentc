"""Tests for project loading, the feature DAG, lint, and writing."""

from __future__ import annotations

from pathlib import Path

import pytest

from intentc.core import (
    FeatureNode,
    IntentFile,
    ParseErrors,
    Project,
    ProjectIntent,
    ProjectIssue,
    blank_project,
    check_project,
    load_project,
    write_project,
)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _ic(name: str, deps: list[str] | None = None, body: str = "Body text for the feature.") -> str:
    dep_line = f"depends_on: [{', '.join(deps)}]\n" if deps else ""
    return f"---\nname: {name}\n{dep_line}---\n\n# {name}\n\n{body}\n"


def _icv(target: str, name: str = "check") -> str:
    return (
        f"target: {target}\nvalidations:\n  - name: {name}\n    args:\n"
        "      rubric: verify the feature does what the intent describes in detail\n"
    )


@pytest.fixture()
def chain(tmp_path: Path) -> Path:
    """models -> store -> api, plus an implementation."""
    intent = tmp_path / "intent"
    _write(intent / "project.ic", "---\nname: demo\n---\n\n# Demo\n")
    _write(intent / "implementations" / "default.ic", "---\nname: default\n---\n\nPython.\n")
    _write(intent / "models" / "models.ic", _ic("models"))
    _write(intent / "models" / "validations.icv", _icv("models"))
    _write(intent / "store" / "store.ic", _ic("store", ["models"]))
    _write(intent / "store" / "validations.icv", _icv("store"))
    _write(intent / "api" / "api.ic", _ic("api", ["store"]))
    _write(intent / "api" / "validations.icv", _icv("api"))
    return intent


# ---------------------------------------------------------------------------
# Loading and DAG
# ---------------------------------------------------------------------------


def test_load_project_chain(chain: Path) -> None:
    project = load_project(chain)
    assert project.project_intent.name == "demo"
    assert set(project.features) == {"models", "store", "api"}
    assert project.intent_dir == chain
    assert project.implementations["default"].body == "Python."
    assert project.features["store"].validations[0].target == "store"


def test_dag_traversal(chain: Path) -> None:
    project = load_project(chain)
    assert project.parents("api") == ["store"]
    assert project.ancestors("api") == {"store", "models"}
    assert project.children("models") == ["store"]
    assert project.descendants("models") == {"store", "api"}
    assert project.topological_order() == ["models", "store", "api"]


def test_unknown_feature_raises_key_error_with_hint(chain: Path) -> None:
    project = load_project(chain)
    with pytest.raises(KeyError) as excinfo:
        project.parents("stor")
    assert "Did you mean 'store'" in str(excinfo.value)
    assert "Available: api, models, store" in str(excinfo.value)


def test_topological_order_is_stable(tmp_path: Path) -> None:
    intent = tmp_path / "intent"
    _write(intent / "project.ic", "---\nname: p\n---\n")
    for name in ["zeta", "alpha", "mid"]:
        _write(intent / name / f"{name}.ic", _ic(name))
    _write(intent / "leaf" / "leaf.ic", _ic("leaf", ["zeta", "alpha"]))
    assert load_project(intent).topological_order() == ["alpha", "mid", "zeta", "leaf"]


def test_cycle_is_parse_error(tmp_path: Path) -> None:
    intent = tmp_path / "intent"
    _write(intent / "project.ic", "---\nname: p\n---\n")
    _write(intent / "a" / "a.ic", _ic("a", ["b"]))
    _write(intent / "b" / "b.ic", _ic("b", ["a"]))
    with pytest.raises(ParseErrors) as excinfo:
        load_project(intent)
    assert "cycle" in str(excinfo.value).lower()


def test_topological_order_raises_on_cycle_in_memory() -> None:
    project = Project(
        project_intent=ProjectIntent(name="p"),
        features={
            "a": FeatureNode(path="a", intents=[IntentFile(name="a", depends_on=["b"])]),
            "b": FeatureNode(path="b", intents=[IntentFile(name="b", depends_on=["a"])]),
        },
    )
    with pytest.raises(ValueError):
        project.topological_order()


def test_unknown_dependency_is_parse_error(tmp_path: Path) -> None:
    intent = tmp_path / "intent"
    _write(intent / "project.ic", "---\nname: p\n---\n")
    _write(intent / "models" / "models.ic", _ic("models"))
    _write(intent / "api" / "api.ic", _ic("api", ["model"]))
    with pytest.raises(ParseErrors) as excinfo:
        load_project(intent)
    msg = str(excinfo.value)
    assert "unknown dependency 'model'" in msg
    assert "did you mean 'models'" in msg
    assert excinfo.value.errors[0].field == "depends_on"


def test_self_dependency_is_parse_error(tmp_path: Path) -> None:
    intent = tmp_path / "intent"
    _write(intent / "project.ic", "---\nname: p\n---\n")
    _write(intent / "a" / "a.ic", _ic("a", ["a"]))
    with pytest.raises(ParseErrors) as excinfo:
        load_project(intent)
    assert "depends on itself" in str(excinfo.value)


def test_errors_accumulate_across_files(tmp_path: Path) -> None:
    intent = tmp_path / "intent"
    _write(intent / "project.ic", "---\nname: p\n---\n")
    _write(intent / "a" / "a.ic", "---\ntags: [x]\n---\n")  # missing name
    _write(intent / "b" / "b.ic", _ic("b", ["nope"]))
    _write(intent / "b" / "v.icv", "target: b\nvalidations: nope\n")
    with pytest.raises(ParseErrors) as excinfo:
        load_project(intent)
    assert len(excinfo.value.errors) == 3


def test_missing_project_ic(tmp_path: Path) -> None:
    (tmp_path / "intent").mkdir()
    with pytest.raises(ParseErrors) as excinfo:
        load_project(tmp_path / "intent")
    assert "project.ic not found" in str(excinfo.value)


def test_wildcard_expansion(tmp_path: Path) -> None:
    intent = tmp_path / "intent"
    _write(intent / "project.ic", "---\nname: p\n---\n")
    _write(intent / "core" / "a" / "a.ic", _ic("a"))
    _write(intent / "core" / "b" / "b.ic", _ic("b"))
    _write(intent / "cli" / "cli.ic", _ic("cli", ["core/*"]))
    project = load_project(intent)
    assert project.features["cli"].intents[0].depends_on == ["core/a", "core/b"]
    assert project.parents("cli") == ["core/a", "core/b"]


def test_wildcard_with_no_match_is_error(tmp_path: Path) -> None:
    intent = tmp_path / "intent"
    _write(intent / "project.ic", "---\nname: p\n---\n")
    _write(intent / "cli" / "cli.ic", _ic("cli", ["core/*"]))
    with pytest.raises(ParseErrors) as excinfo:
        load_project(intent)
    assert "matched no features" in str(excinfo.value)


def test_hidden_and_special_dirs_are_skipped(tmp_path: Path) -> None:
    intent = tmp_path / "intent"
    _write(intent / "project.ic", "---\nname: p\n---\n")
    _write(intent / "a" / "a.ic", _ic("a"))
    _write(intent / ".drafts" / "x.ic", "not even valid")
    _write(intent / "_scratch" / "y.ic", "not valid")
    _write(intent / "assertions" / "all.icv", _icv("project"))
    project = load_project(intent)
    assert set(project.features) == {"a"}
    assert len(project.assertions) == 1


def test_legacy_implementation_ic(tmp_path: Path) -> None:
    intent = tmp_path / "intent"
    _write(intent / "project.ic", "---\nname: p\n---\n")
    _write(intent / "implementation.ic", "---\nname: implementation\n---\n\nLegacy.\n")
    _write(intent / "a" / "a.ic", _ic("a"))
    project = load_project(intent)
    assert project.resolve_implementation().body == "Legacy."


def test_resolve_implementation(chain: Path) -> None:
    project = load_project(chain)
    assert project.resolve_implementation().name == "default"
    assert project.resolve_implementation("default").name == "default"
    with pytest.raises(KeyError):
        project.resolve_implementation("rust")
    project.implementations["rust"] = project.implementations["default"].model_copy(update={"name": "rust"})
    assert project.resolve_implementation().name == "default"
    del project.implementations["default"]
    project.implementations["go"] = project.implementations["rust"]
    with pytest.raises(ValueError):
        project.resolve_implementation()
    assert Project(project_intent=ProjectIntent(name="x")).resolve_implementation() is None


def test_buildable_after(chain: Path) -> None:
    project = load_project(chain)
    assert project.buildable_after(set()) == ["models"]
    assert project.buildable_after({"models"}) == ["store"]
    assert project.buildable_after({"models", "store", "api"}) == []


def test_source_files(chain: Path) -> None:
    project = load_project(chain)
    files = project.source_files("store")
    assert [f.name for f in files] == ["store.ic", "validations.icv"]


# ---------------------------------------------------------------------------
# check_project
# ---------------------------------------------------------------------------


def test_check_project_clean(chain: Path) -> None:
    assert check_project(load_project(chain)) == []


def test_check_project_issues(tmp_path: Path) -> None:
    intent = tmp_path / "intent"
    _write(intent / "project.ic", "---\nname: p\n---\n")
    _write(intent / "models" / "models.ic", _ic("models"))
    _write(intent / "models" / "validations.icv", _icv("models"))
    _write(intent / "store" / "storage.ic", "---\nname: storage\ndepends_on: [models]\n---\n")  # name mismatch, empty body
    _write(intent / "store" / "validations.icv", _icv("models"))  # target mismatch
    _write(intent / "api" / "api.ic", _ic("api", ["store"]))  # no validations
    _write(intent / "orphan" / "validations.icv", _icv("orphan"))  # icv with no ic
    _write(intent / "vague" / "vague.ic", _ic("vague"))
    _write(intent / "vague" / "v.icv", "target: vague\nvalidations:\n  - name: v\n    args:\n      rubric: works\n")
    _write(intent / "refs" / "refs.ic", _ic("refs", body="See design/mock.png for the layout."))
    _write(intent / "refs" / "v.icv", _icv("refs"))
    _write(intent / "badtarget" / "badtarget.ic", _ic("badtarget"))
    _write(intent / "badtarget" / "v.icv", _icv("nothing"))

    issues = check_project(load_project(intent))
    messages = [f"{i.level}|{i.feature}|{i.message}" for i in issues]

    def has(level: str, feature: str, fragment: str) -> bool:
        return any(m.startswith(f"{level}|{feature}|") and fragment in m for m in messages)

    assert has("error", "orphan", "no intent to validate")
    assert has("error", "badtarget", "not a feature")
    assert has("warning", "store", "differs from the directory 'store'")
    assert has("warning", "store", "empty intent body")
    assert has("warning", "store", "differs from its directory")
    assert has("warning", "api", "no validations")
    assert has("warning", "vague", "very short rubric")
    assert has("warning", "refs", "design/mock.png")
    assert has("warning", "", "no implementation found")
    assert all(isinstance(i, ProjectIssue) for i in issues)
    assert "error:" in str(next(i for i in issues if i.level == "error"))


# ---------------------------------------------------------------------------
# write_project / blank_project
# ---------------------------------------------------------------------------


def test_write_project_roundtrip(chain: Path, tmp_path: Path) -> None:
    project = load_project(chain)
    dest = write_project(project, tmp_path / "copy")
    back = load_project(dest)
    assert set(back.features) == set(project.features)
    assert back.features["api"].intents[0].depends_on == ["store"]
    assert (dest / "implementations" / "default.ic").exists()
    assert (dest / "store" / "validations.icv").exists()


def test_write_project_copies_supporting_files(tmp_path: Path) -> None:
    intent = tmp_path / "intent"
    _write(intent / "project.ic", "---\nname: p\n---\n")
    _write(intent / "ui" / "ui.ic", _ic("ui", body="Match design/mock.png exactly."))
    _write(intent / "ui" / "design" / "mock.png", "png-bytes")
    dest = write_project(load_project(intent), tmp_path / "copy")
    assert (dest / "ui" / "design" / "mock.png").read_text() == "png-bytes"


def test_blank_project_roundtrip(tmp_path: Path) -> None:
    project = blank_project("myproj")
    dest = write_project(project, tmp_path / "intent")
    back = load_project(dest)
    assert back.project_intent.name == "myproj"
    assert set(back.features) == {"starter"}
    assert back.features["starter"].validations[0].validations[0].type == "file_exists"
    assert back.resolve_implementation().name == "default"
    assert [i for i in check_project(back) if i.level == "error"] == []
