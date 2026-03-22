"""Tests for project loading, writing, and DAG traversal."""

from __future__ import annotations

import pytest
from pathlib import Path

from intentc.core.types import (
    IntentFile,
    Implementation,
    ParseErrors,
    ProjectIntent,
    ValidationFile,
    Validation,
)
from intentc.core.project import (
    FeatureNode,
    Project,
    blank_project,
    load_project,
    write_project,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_ic(path: Path, name: str, **kwargs: object) -> None:
    """Write a minimal .ic file."""
    lines = [f"name: {name}"]
    if "depends_on" in kwargs:
        deps = kwargs["depends_on"]
        lines.append(f"depends_on: {deps}")
    body = kwargs.get("body", "")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n" + "\n".join(lines) + "\n---\n" + str(body) + "\n")


def _write_icv(path: Path, target: str) -> None:
    """Write a minimal .icv file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntarget: {target}\nvalidations:\n  - name: check\n---\n"
    )


def _make_project(tmp_path: Path) -> Path:
    """Create a sample project structure and return the intent dir."""
    intent = tmp_path / "intent"
    _write_ic(intent / "project.ic", "testproject")
    _write_ic(intent / "implementations" / "default.ic", "default")
    _write_icv(intent / "assertions" / "global.icv", "project")
    _write_ic(intent / "core" / "specs" / "specs.ic", "core/specs")
    _write_icv(intent / "core" / "specs" / "specs.icv", "core/specs")
    _write_ic(
        intent / "ui" / "buttons" / "buttons.ic",
        "ui/buttons",
        depends_on=["core/specs"],
    )
    return intent


# ---------------------------------------------------------------------------
# FeatureNode
# ---------------------------------------------------------------------------


class TestFeatureNode:
    def test_depends_on_empty(self):
        node = FeatureNode(path="a")
        assert node.depends_on == []

    def test_depends_on_deduplicated(self):
        node = FeatureNode(
            path="a",
            intents=[
                IntentFile(name="a1", depends_on=["x", "y"]),
                IntentFile(name="a2", depends_on=["y", "z"]),
            ],
        )
        assert node.depends_on == ["x", "y", "z"]

    def test_depends_on_order_preserved(self):
        node = FeatureNode(
            path="a",
            intents=[
                IntentFile(name="a1", depends_on=["c", "a", "b"]),
            ],
        )
        assert node.depends_on == ["c", "a", "b"]


# ---------------------------------------------------------------------------
# Project — resolve_implementation
# ---------------------------------------------------------------------------


class TestResolveImplementation:
    def test_no_implementations(self):
        p = Project(project_intent=ProjectIntent(name="p"))
        assert p.resolve_implementation() is None

    def test_single_implementation(self):
        impl = Implementation(name="python")
        p = Project(
            project_intent=ProjectIntent(name="p"),
            implementations={"python": impl},
        )
        assert p.resolve_implementation() is impl

    def test_default_chosen_when_ambiguous(self):
        default = Implementation(name="default")
        other = Implementation(name="rust")
        p = Project(
            project_intent=ProjectIntent(name="p"),
            implementations={"default": default, "rust": other},
        )
        assert p.resolve_implementation() is default

    def test_explicit_name(self):
        impl = Implementation(name="rust")
        p = Project(
            project_intent=ProjectIntent(name="p"),
            implementations={"rust": impl},
        )
        assert p.resolve_implementation("rust") is impl

    def test_explicit_name_not_found(self):
        p = Project(
            project_intent=ProjectIntent(name="p"),
            implementations={"rust": Implementation(name="rust")},
        )
        with pytest.raises(KeyError):
            p.resolve_implementation("go")

    def test_ambiguous_no_default(self):
        p = Project(
            project_intent=ProjectIntent(name="p"),
            implementations={
                "rust": Implementation(name="rust"),
                "go": Implementation(name="go"),
            },
        )
        with pytest.raises(ValueError, match="Ambiguous"):
            p.resolve_implementation()


# ---------------------------------------------------------------------------
# Project — DAG traversal
# ---------------------------------------------------------------------------


def _dag_project() -> Project:
    """Create a project with a diamond DAG: a -> b,c -> d."""
    return Project(
        project_intent=ProjectIntent(name="dag"),
        features={
            "a": FeatureNode(path="a", intents=[IntentFile(name="a")]),
            "b": FeatureNode(path="b", intents=[IntentFile(name="b", depends_on=["a"])]),
            "c": FeatureNode(path="c", intents=[IntentFile(name="c", depends_on=["a"])]),
            "d": FeatureNode(path="d", intents=[IntentFile(name="d", depends_on=["b", "c"])]),
        },
    )


class TestDAGTraversal:
    def test_parents(self):
        p = _dag_project()
        assert p.parents("d") == ["b", "c"]
        assert p.parents("a") == []

    def test_ancestors(self):
        p = _dag_project()
        assert p.ancestors("d") == {"a", "b", "c"}
        assert p.ancestors("a") == set()

    def test_children(self):
        p = _dag_project()
        assert sorted(p.children("a")) == ["b", "c"]
        assert p.children("d") == []

    def test_descendants(self):
        p = _dag_project()
        assert p.descendants("a") == {"b", "c", "d"}
        assert p.descendants("d") == set()

    def test_topological_order(self):
        p = _dag_project()
        order = p.topological_order()
        assert order.index("a") < order.index("b")
        assert order.index("a") < order.index("c")
        assert order.index("b") < order.index("d")
        assert order.index("c") < order.index("d")

    def test_topological_order_cycle(self):
        p = Project(
            project_intent=ProjectIntent(name="cycle"),
            features={
                "x": FeatureNode(path="x", intents=[IntentFile(name="x", depends_on=["y"])]),
                "y": FeatureNode(path="y", intents=[IntentFile(name="y", depends_on=["x"])]),
            },
        )
        with pytest.raises(ValueError, match="Cycle"):
            p.topological_order()

    def test_require_feature_raises(self):
        p = _dag_project()
        with pytest.raises(KeyError):
            p.parents("nonexistent")
        with pytest.raises(KeyError):
            p.ancestors("nonexistent")
        with pytest.raises(KeyError):
            p.children("nonexistent")
        with pytest.raises(KeyError):
            p.descendants("nonexistent")


# ---------------------------------------------------------------------------
# load_project
# ---------------------------------------------------------------------------


class TestLoadProject:
    def test_load_full_project(self, tmp_path: Path):
        intent = _make_project(tmp_path)
        project = load_project(intent)
        assert project.project_intent.name == "testproject"
        assert "default" in project.implementations
        assert len(project.assertions) == 1
        assert "core/specs" in project.features
        assert "ui/buttons" in project.features
        assert project.intent_dir == intent

    def test_load_feature_intents_and_validations(self, tmp_path: Path):
        intent = _make_project(tmp_path)
        project = load_project(intent)
        node = project.features["core/specs"]
        assert len(node.intents) == 1
        assert len(node.validations) == 1
        assert node.intents[0].name == "core/specs"

    def test_load_dependencies(self, tmp_path: Path):
        intent = _make_project(tmp_path)
        project = load_project(intent)
        assert project.features["ui/buttons"].depends_on == ["core/specs"]

    def test_load_missing_project_ic(self, tmp_path: Path):
        intent = tmp_path / "intent"
        intent.mkdir()
        with pytest.raises(ParseErrors) as exc_info:
            load_project(intent)
        assert any("project.ic not found" in str(e) for e in exc_info.value.errors)

    def test_load_malformed_intent(self, tmp_path: Path):
        intent = tmp_path / "intent"
        _write_ic(intent / "project.ic", "p")
        feat = intent / "bad" / "feat" / "feat.ic"
        feat.parent.mkdir(parents=True)
        feat.write_text("no front matter here")
        with pytest.raises(ParseErrors):
            load_project(intent)

    def test_wildcard_expansion(self, tmp_path: Path):
        intent = tmp_path / "intent"
        _write_ic(intent / "project.ic", "p")
        _write_ic(intent / "core" / "a" / "a.ic", "core/a")
        _write_ic(intent / "core" / "b" / "b.ic", "core/b")
        _write_ic(intent / "top" / "x" / "x.ic", "top/x", depends_on=["core/*"])
        project = load_project(intent)
        deps = project.features["top/x"].depends_on
        assert "core/a" in deps
        assert "core/b" in deps

    def test_wildcard_no_match_error(self, tmp_path: Path):
        intent = tmp_path / "intent"
        _write_ic(intent / "project.ic", "p")
        _write_ic(intent / "feat" / "x" / "x.ic", "feat/x", depends_on=["nothing/*"])
        with pytest.raises(ParseErrors) as exc_info:
            load_project(intent)
        assert any("matched no features" in str(e) for e in exc_info.value.errors)


# ---------------------------------------------------------------------------
# write_project
# ---------------------------------------------------------------------------


class TestWriteProject:
    def test_write_and_reload(self, tmp_path: Path):
        intent = _make_project(tmp_path)
        project = load_project(intent)

        dest = tmp_path / "output"
        write_project(project, dest)

        reloaded = load_project(dest)
        assert reloaded.project_intent.name == project.project_intent.name
        assert set(reloaded.implementations.keys()) == set(project.implementations.keys())
        assert set(reloaded.features.keys()) == set(project.features.keys())

    def test_write_creates_directories(self, tmp_path: Path):
        project = blank_project("test")
        dest = tmp_path / "deep" / "nested"
        write_project(project, dest)
        assert (dest / "project.ic").exists()
        assert (dest / "implementations" / "default.ic").exists()


# ---------------------------------------------------------------------------
# blank_project
# ---------------------------------------------------------------------------


class TestBlankProject:
    def test_blank_has_required_parts(self):
        project = blank_project("myapp")
        assert project.project_intent.name == "myapp"
        assert "default" in project.implementations
        assert "starter" in project.features
        assert len(project.features["starter"].intents) == 1

    def test_blank_write_and_load(self, tmp_path: Path):
        project = blank_project("roundtrip")
        dest = tmp_path / "intent"
        write_project(project, dest)
        reloaded = load_project(dest)
        assert reloaded.project_intent.name == "roundtrip"
        assert "starter" in reloaded.features
