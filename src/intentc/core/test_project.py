"""Tests for intentc.core.project."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from intentc.core.project import (
    FeatureNode,
    Project,
    blank_project,
    load_project,
    write_project,
)
from intentc.core.types import (
    Implementation,
    IntentFile,
    ParseErrors,
    ProjectIntent,
    ValidationFile,
    Validation,
    ValidationType,
    Severity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _minimal_project(tmp: Path) -> Path:
    """Create a minimal valid intent/ directory and return its path."""
    intent = tmp / "intent"
    _write(
        intent / "project.ic",
        "---\nname: test-project\n---\n# Test\n",
    )
    _write(
        intent / "implementations" / "default.ic",
        "---\nname: default\n---\n# Default impl\n",
    )
    _write(
        intent / "core" / "specs" / "specs.ic",
        "---\nname: specs\n---\n# Specs feature\n",
    )
    return intent


# ---------------------------------------------------------------------------
# FeatureNode
# ---------------------------------------------------------------------------


class TestFeatureNode:
    def test_depends_on_deduplicates(self):
        node = FeatureNode(
            path="feat",
            intents=[
                IntentFile(name="a", depends_on=["x", "y"]),
                IntentFile(name="b", depends_on=["y", "z"]),
            ],
        )
        assert node.depends_on == ["x", "y", "z"]

    def test_depends_on_empty(self):
        node = FeatureNode(path="feat")
        assert node.depends_on == []


# ---------------------------------------------------------------------------
# Project.resolve_implementation
# ---------------------------------------------------------------------------


class TestResolveImplementation:
    def test_no_implementations(self):
        p = Project(project_intent=ProjectIntent(name="p"))
        assert p.resolve_implementation() is None

    def test_single_implementation(self):
        impl = Implementation(name="only")
        p = Project(project_intent=ProjectIntent(name="p"), implementations={"only": impl})
        assert p.resolve_implementation() is impl

    def test_default_chosen_when_multiple(self):
        default = Implementation(name="default")
        other = Implementation(name="other")
        p = Project(
            project_intent=ProjectIntent(name="p"),
            implementations={"default": default, "other": other},
        )
        assert p.resolve_implementation() is default

    def test_explicit_name(self):
        impl = Implementation(name="rust")
        p = Project(project_intent=ProjectIntent(name="p"), implementations={"rust": impl})
        assert p.resolve_implementation("rust") is impl

    def test_explicit_name_not_found(self):
        p = Project(
            project_intent=ProjectIntent(name="p"),
            implementations={"a": Implementation(name="a")},
        )
        with pytest.raises(KeyError, match="nope"):
            p.resolve_implementation("nope")

    def test_ambiguous(self):
        p = Project(
            project_intent=ProjectIntent(name="p"),
            implementations={"a": Implementation(name="a"), "b": Implementation(name="b")},
        )
        with pytest.raises(ValueError, match="Ambiguous"):
            p.resolve_implementation()


# ---------------------------------------------------------------------------
# DAG traversal
# ---------------------------------------------------------------------------


def _dag_project() -> Project:
    """A -> B -> C  (A depends on B, B depends on C)."""
    return Project(
        project_intent=ProjectIntent(name="p"),
        features={
            "a": FeatureNode(path="a", intents=[IntentFile(name="a", depends_on=["b"])]),
            "b": FeatureNode(path="b", intents=[IntentFile(name="b", depends_on=["c"])]),
            "c": FeatureNode(path="c", intents=[IntentFile(name="c")]),
        },
    )


class TestDAG:
    def test_parents(self):
        p = _dag_project()
        assert p.parents("a") == ["b"]
        assert p.parents("c") == []

    def test_ancestors(self):
        p = _dag_project()
        assert p.ancestors("a") == {"b", "c"}
        assert p.ancestors("c") == set()

    def test_children(self):
        p = _dag_project()
        assert p.children("c") == ["b"]
        assert p.children("a") == []

    def test_descendants(self):
        p = _dag_project()
        assert p.descendants("c") == {"a", "b"}
        assert p.descendants("a") == set()

    def test_topological_order(self):
        p = _dag_project()
        order = p.topological_order()
        assert order.index("c") < order.index("b") < order.index("a")

    def test_topological_cycle(self):
        p = Project(
            project_intent=ProjectIntent(name="p"),
            features={
                "x": FeatureNode(path="x", intents=[IntentFile(name="x", depends_on=["y"])]),
                "y": FeatureNode(path="y", intents=[IntentFile(name="y", depends_on=["x"])]),
            },
        )
        with pytest.raises(ValueError, match="cycle"):
            p.topological_order()

    def test_require_feature_raises(self):
        p = _dag_project()
        with pytest.raises(KeyError, match="nonexistent"):
            p.parents("nonexistent")


# ---------------------------------------------------------------------------
# load_project
# ---------------------------------------------------------------------------


class TestLoadProject:
    def test_minimal(self):
        with tempfile.TemporaryDirectory() as tmp:
            intent = _minimal_project(Path(tmp))
            proj = load_project(intent)
            assert proj.project_intent.name == "test-project"
            assert "default" in proj.implementations
            assert "core/specs" in proj.features
            assert proj.intent_dir == intent

    def test_missing_project_ic(self):
        with tempfile.TemporaryDirectory() as tmp:
            intent = Path(tmp) / "intent"
            intent.mkdir()
            with pytest.raises(ParseErrors, match="project.ic not found"):
                load_project(intent)

    def test_assertions_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            intent = _minimal_project(Path(tmp))
            _write(
                intent / "assertions" / "smoke.icv",
                "---\ntarget: core/specs\nvalidations:\n  - name: check\n    type: file_check\n    args:\n      path: x.py\n---\n",
            )
            proj = load_project(intent)
            assert len(proj.assertions) == 1
            assert proj.assertions[0].target == "core/specs"

    def test_feature_validations_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            intent = _minimal_project(Path(tmp))
            _write(
                intent / "core" / "specs" / "check.icv",
                "---\ntarget: core/specs\nvalidations:\n  - name: exists\n    type: file_check\n    args:\n      path: y.py\n---\n",
            )
            proj = load_project(intent)
            assert len(proj.features["core/specs"].validations) == 1

    def test_nested_features(self):
        with tempfile.TemporaryDirectory() as tmp:
            intent = _minimal_project(Path(tmp))
            _write(
                intent / "core" / "deep" / "nested" / "f.ic",
                "---\nname: nested\n---\n# Deep\n",
            )
            proj = load_project(intent)
            assert "core/deep/nested" in proj.features

    def test_wildcard_expansion(self):
        with tempfile.TemporaryDirectory() as tmp:
            intent = Path(tmp) / "intent"
            _write(intent / "project.ic", "---\nname: wc\n---\n")
            _write(intent / "core" / "a" / "a.ic", "---\nname: a\n---\n")
            _write(intent / "core" / "b" / "b.ic", "---\nname: b\n---\n")
            _write(
                intent / "top" / "all" / "all.ic",
                "---\nname: all\ndepends_on:\n  - 'core/*'\n---\n",
            )
            proj = load_project(intent)
            node = proj.features["top/all"]
            assert set(node.depends_on) == {"core/a", "core/b"}

    def test_wildcard_no_match_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            intent = Path(tmp) / "intent"
            _write(intent / "project.ic", "---\nname: wc\n---\n")
            _write(
                intent / "feat" / "x" / "x.ic",
                "---\nname: x\ndepends_on:\n  - 'nonexistent/*'\n---\n",
            )
            with pytest.raises(ParseErrors, match="matched no features"):
                load_project(intent)

    def test_accumulates_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            intent = Path(tmp) / "intent"
            _write(intent / "project.ic", "---\nname: p\n---\n")
            # Two bad .ic files (missing name)
            _write(intent / "a" / "f1" / "bad1.ic", "---\ntags: [x]\n---\n")
            _write(intent / "a" / "f2" / "bad2.ic", "---\ntags: [y]\n---\n")
            with pytest.raises(ParseErrors) as exc_info:
                load_project(intent)
            assert len(exc_info.value.errors) == 2


# ---------------------------------------------------------------------------
# write_project / round-trip
# ---------------------------------------------------------------------------


class TestWriteProject:
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            intent = _minimal_project(Path(tmp))
            proj = load_project(intent)

            dest = Path(tmp) / "output"
            write_project(proj, dest)

            proj2 = load_project(dest)
            assert proj2.project_intent.name == proj.project_intent.name
            assert set(proj2.implementations) == set(proj.implementations)
            assert set(proj2.features) == set(proj.features)

    def test_write_creates_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = blank_project("new")
            dest = Path(tmp) / "deep" / "nested" / "out"
            result = write_project(proj, dest)
            assert result == dest
            assert (dest / "project.ic").exists()
            assert (dest / "implementations" / "default.ic").exists()


# ---------------------------------------------------------------------------
# blank_project
# ---------------------------------------------------------------------------


class TestBlankProject:
    def test_structure(self):
        proj = blank_project("my-app")
        assert proj.project_intent.name == "my-app"
        assert "default" in proj.implementations
        assert "starter" in proj.features
        assert len(proj.features["starter"].intents) == 1

    def test_write_and_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = blank_project("roundtrip")
            dest = Path(tmp) / "out"
            write_project(proj, dest)
            proj2 = load_project(dest)
            assert proj2.project_intent.name == "roundtrip"
            assert "starter" in proj2.features
