"""Tests for intentc.core.project — Project, FeatureNode, load/write/blank."""

from __future__ import annotations

from pathlib import Path

import pytest

from intentc.core.models import (
    Implementation,
    IntentFile,
    ParseErrors,
    ProjectIntent,
    ValidationFile,
)
from intentc.core.project import (
    FeatureNode,
    Project,
    blank_project,
    load_project,
    write_project,
)


# ---------------------------------------------------------------------------
# FeatureNode
# ---------------------------------------------------------------------------


class TestFeatureNode:
    def test_depends_on_empty(self):
        node = FeatureNode(path="core/foo")
        assert node.depends_on == []

    def test_depends_on_combined(self):
        node = FeatureNode(
            path="build/runner",
            intents=[
                IntentFile(name="a", depends_on=["core/foo", "core/bar"]),
                IntentFile(name="b", depends_on=["core/bar", "core/baz"]),
            ],
        )
        assert node.depends_on == ["core/foo", "core/bar", "core/baz"]

    def test_depends_on_order_preserved(self):
        node = FeatureNode(
            path="x",
            intents=[
                IntentFile(name="i1", depends_on=["z", "a", "m"]),
            ],
        )
        assert node.depends_on == ["z", "a", "m"]


# ---------------------------------------------------------------------------
# Project.resolve_implementation
# ---------------------------------------------------------------------------


class TestResolveImplementation:
    def test_no_implementations(self):
        proj = Project(project_intent=ProjectIntent(name="p"))
        assert proj.resolve_implementation() is None

    def test_single_implementation(self):
        impl = Implementation(name="only")
        proj = Project(
            project_intent=ProjectIntent(name="p"),
            implementations={"only": impl},
        )
        assert proj.resolve_implementation() is impl

    def test_default_chosen_when_multiple(self):
        default = Implementation(name="default")
        other = Implementation(name="other")
        proj = Project(
            project_intent=ProjectIntent(name="p"),
            implementations={"default": default, "other": other},
        )
        assert proj.resolve_implementation() is default

    def test_ambiguous_raises(self):
        proj = Project(
            project_intent=ProjectIntent(name="p"),
            implementations={
                "a": Implementation(name="a"),
                "b": Implementation(name="b"),
            },
        )
        with pytest.raises(ValueError, match="Ambiguous"):
            proj.resolve_implementation()

    def test_named_lookup(self):
        impl = Implementation(name="rust")
        proj = Project(
            project_intent=ProjectIntent(name="p"),
            implementations={"rust": impl},
        )
        assert proj.resolve_implementation("rust") is impl

    def test_named_not_found(self):
        proj = Project(
            project_intent=ProjectIntent(name="p"),
            implementations={"py": Implementation(name="py")},
        )
        with pytest.raises(KeyError, match="not found"):
            proj.resolve_implementation("go")


# ---------------------------------------------------------------------------
# DAG Traversal
# ---------------------------------------------------------------------------


def _dag_project() -> Project:
    """Build a diamond-shaped DAG: d -> b,c -> a."""
    return Project(
        project_intent=ProjectIntent(name="dag"),
        features={
            "a": FeatureNode(
                path="a", intents=[IntentFile(name="a")]
            ),
            "b": FeatureNode(
                path="b", intents=[IntentFile(name="b", depends_on=["a"])]
            ),
            "c": FeatureNode(
                path="c", intents=[IntentFile(name="c", depends_on=["a"])]
            ),
            "d": FeatureNode(
                path="d", intents=[IntentFile(name="d", depends_on=["b", "c"])]
            ),
        },
    )


class TestDAGTraversal:
    def test_require_feature_missing(self):
        proj = _dag_project()
        with pytest.raises(KeyError, match="not found"):
            proj.parents("nope")

    def test_parents(self):
        proj = _dag_project()
        assert proj.parents("d") == ["b", "c"]
        assert proj.parents("a") == []

    def test_ancestors(self):
        proj = _dag_project()
        assert proj.ancestors("d") == {"a", "b", "c"}
        assert proj.ancestors("a") == set()

    def test_children(self):
        proj = _dag_project()
        assert sorted(proj.children("a")) == ["b", "c"]
        assert proj.children("d") == []

    def test_descendants(self):
        proj = _dag_project()
        assert proj.descendants("a") == {"b", "c", "d"}
        assert proj.descendants("d") == set()

    def test_topological_order(self):
        proj = _dag_project()
        order = proj.topological_order()
        assert order.index("a") < order.index("b")
        assert order.index("a") < order.index("c")
        assert order.index("b") < order.index("d")
        assert order.index("c") < order.index("d")

    def test_topological_order_cycle(self):
        proj = Project(
            project_intent=ProjectIntent(name="cyc"),
            features={
                "x": FeatureNode(
                    path="x", intents=[IntentFile(name="x", depends_on=["y"])]
                ),
                "y": FeatureNode(
                    path="y", intents=[IntentFile(name="y", depends_on=["x"])]
                ),
            },
        )
        with pytest.raises(ValueError, match="cycle"):
            proj.topological_order()


# ---------------------------------------------------------------------------
# load_project
# ---------------------------------------------------------------------------


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestLoadProject:
    def test_missing_project_ic(self, tmp_path: Path):
        intent_dir = tmp_path / "intent"
        intent_dir.mkdir()
        with pytest.raises(ParseErrors, match="not found"):
            load_project(intent_dir)

    def test_minimal_project(self, tmp_path: Path):
        intent_dir = tmp_path / "intent"
        _write_file(intent_dir / "project.ic", "---\nname: test\n---\nHello")
        proj = load_project(intent_dir)
        assert proj.project_intent.name == "test"
        assert proj.intent_dir == intent_dir
        assert proj.features == {}
        assert proj.implementations == {}

    def test_loads_implementations(self, tmp_path: Path):
        intent_dir = tmp_path / "intent"
        _write_file(intent_dir / "project.ic", "---\nname: p\n---\n")
        _write_file(
            intent_dir / "implementations" / "default.ic",
            "---\nname: default\n---\nPython",
        )
        proj = load_project(intent_dir)
        assert "default" in proj.implementations
        assert proj.implementations["default"].name == "default"

    def test_loads_assertions(self, tmp_path: Path):
        intent_dir = tmp_path / "intent"
        _write_file(intent_dir / "project.ic", "---\nname: p\n---\n")
        _write_file(
            intent_dir / "assertions" / "smoke.icv",
            "target: all\n",
        )
        proj = load_project(intent_dir)
        assert len(proj.assertions) == 1

    def test_loads_features(self, tmp_path: Path):
        intent_dir = tmp_path / "intent"
        _write_file(intent_dir / "project.ic", "---\nname: p\n---\n")
        _write_file(
            intent_dir / "core" / "models" / "models.ic",
            "---\nname: models\n---\nModels feature.",
        )
        _write_file(
            intent_dir / "core" / "models" / "tests.icv",
            "target: core/models\n",
        )
        proj = load_project(intent_dir)
        assert "core/models" in proj.features
        node = proj.features["core/models"]
        assert len(node.intents) == 1
        assert len(node.validations) == 1

    def test_wildcard_expansion(self, tmp_path: Path):
        intent_dir = tmp_path / "intent"
        _write_file(intent_dir / "project.ic", "---\nname: p\n---\n")
        _write_file(
            intent_dir / "core" / "a" / "a.ic",
            "---\nname: a\n---\n",
        )
        _write_file(
            intent_dir / "core" / "b" / "b.ic",
            "---\nname: b\n---\n",
        )
        _write_file(
            intent_dir / "top" / "all" / "all.ic",
            "---\nname: all\ndepends_on:\n  - core/*\n---\n",
        )
        proj = load_project(intent_dir)
        deps = proj.features["top/all"].depends_on
        assert sorted(deps) == ["core/a", "core/b"]

    def test_wildcard_no_match_errors(self, tmp_path: Path):
        intent_dir = tmp_path / "intent"
        _write_file(intent_dir / "project.ic", "---\nname: p\n---\n")
        _write_file(
            intent_dir / "feat" / "x" / "x.ic",
            "---\nname: x\ndepends_on:\n  - missing/*\n---\n",
        )
        with pytest.raises(ParseErrors, match="matched no features"):
            load_project(intent_dir)

    def test_accumulates_errors(self, tmp_path: Path):
        intent_dir = tmp_path / "intent"
        _write_file(intent_dir / "project.ic", "---\nname: p\n---\n")
        # Two bad intent files (missing name)
        _write_file(intent_dir / "a" / "x" / "bad1.ic", "---\n---\n")
        _write_file(intent_dir / "b" / "y" / "bad2.ic", "---\n---\n")
        with pytest.raises(ParseErrors) as exc_info:
            load_project(intent_dir)
        assert len(exc_info.value.errors) >= 2

    def test_nested_features(self, tmp_path: Path):
        intent_dir = tmp_path / "intent"
        _write_file(intent_dir / "project.ic", "---\nname: p\n---\n")
        _write_file(
            intent_dir / "a" / "b" / "c" / "deep.ic",
            "---\nname: deep\n---\n",
        )
        proj = load_project(intent_dir)
        assert "a/b/c" in proj.features


# ---------------------------------------------------------------------------
# write_project
# ---------------------------------------------------------------------------


class TestWriteProject:
    def test_round_trip(self, tmp_path: Path):
        proj = blank_project("roundtrip")
        dest = tmp_path / "output"
        result = write_project(proj, dest)
        assert result == dest
        assert (dest / "project.ic").exists()
        assert (dest / "implementations" / "default.ic").exists()
        assert (dest / "starter").is_dir()

    def test_round_trip_reload(self, tmp_path: Path):
        proj = blank_project("rt")
        dest = tmp_path / "output"
        write_project(proj, dest)
        loaded = load_project(dest)
        assert loaded.project_intent.name == "rt"
        assert "starter" in loaded.features


# ---------------------------------------------------------------------------
# blank_project
# ---------------------------------------------------------------------------


class TestBlankProject:
    def test_has_project_intent(self):
        proj = blank_project("my-app")
        assert proj.project_intent.name == "my-app"

    def test_has_default_implementation(self):
        proj = blank_project("my-app")
        assert "default" in proj.implementations

    def test_has_starter_feature(self):
        proj = blank_project("my-app")
        assert "starter" in proj.features
        node = proj.features["starter"]
        assert len(node.intents) == 1
        assert node.intents[0].name == "starter"

    def test_write_and_load(self, tmp_path: Path):
        proj = blank_project("new")
        dest = tmp_path / "new_project"
        write_project(proj, dest)
        loaded = load_project(dest)
        assert loaded.project_intent.name == "new"
        assert "default" in loaded.implementations
        assert "starter" in loaded.features
