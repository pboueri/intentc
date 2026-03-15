"""Tests for the project DAG loader, writer, and traversal."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from intentc.core.parser import ParseErrors
from intentc.core.project import (
    FeatureNode,
    Project,
    blank_project,
    load_project,
    write_project,
)
from intentc.core.types import IntentFile, ProjectIntent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_ic(path: Path, name: str, depends_on: list[str] | None = None, body: str = "") -> None:
    """Write a minimal .ic file."""
    meta = f"name: {name}"
    if depends_on:
        deps = ", ".join(depends_on)
        meta += f"\ndepends_on: [{deps}]"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{meta}\n---\n\n{body}\n")


def _write_icv(path: Path, target: str) -> None:
    """Write a minimal .icv file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"target: {target}\nvalidations: []\n")


def _make_project(tmp_path: Path, features: dict[str, list[str]] | None = None) -> Path:
    """Create a synthetic project directory and return the intent/ path.

    features maps feature_path -> list of depends_on paths.
    """
    intent = tmp_path / "intent"
    _write_ic(intent / "project.ic", "test-project")

    if features is None:
        features = {"alpha": [], "beta": ["alpha"]}

    for fp, deps in features.items():
        _write_ic(intent / fp / f"{fp.split('/')[-1]}.ic", fp.split("/")[-1], deps)

    return intent


# ---------------------------------------------------------------------------
# FeatureNode
# ---------------------------------------------------------------------------


class TestFeatureNode:
    def test_depends_on_combines_intents(self):
        node = FeatureNode(
            path="mod/feat",
            intents=[
                IntentFile(name="a", depends_on=["x", "y"]),
                IntentFile(name="b", depends_on=["y", "z"]),
            ],
        )
        assert node.depends_on == ["x", "y", "z"]

    def test_depends_on_empty(self):
        node = FeatureNode(path="mod/feat")
        assert node.depends_on == []


# ---------------------------------------------------------------------------
# DAG traversal
# ---------------------------------------------------------------------------


class TestProjectDAG:
    def _diamond(self) -> Project:
        """Build A -> B,C -> D diamond graph."""
        return Project(
            project_intent=ProjectIntent(name="test"),
            features={
                "a": FeatureNode(path="a", intents=[IntentFile(name="a")]),
                "b": FeatureNode(path="b", intents=[IntentFile(name="b", depends_on=["a"])]),
                "c": FeatureNode(path="c", intents=[IntentFile(name="c", depends_on=["a"])]),
                "d": FeatureNode(path="d", intents=[IntentFile(name="d", depends_on=["b", "c"])]),
            },
        )

    def test_parents(self):
        p = self._diamond()
        assert p.parents("d") == ["b", "c"]
        assert p.parents("a") == []

    def test_ancestors(self):
        p = self._diamond()
        assert p.ancestors("d") == {"a", "b", "c"}
        assert p.ancestors("b") == {"a"}
        assert p.ancestors("a") == set()

    def test_children(self):
        p = self._diamond()
        assert sorted(p.children("a")) == ["b", "c"]
        assert p.children("d") == []

    def test_descendants(self):
        p = self._diamond()
        assert p.descendants("a") == {"b", "c", "d"}
        assert p.descendants("b") == {"d"}
        assert p.descendants("d") == set()

    def test_topological_order(self):
        p = self._diamond()
        order = p.topological_order()
        assert order.index("a") < order.index("b")
        assert order.index("a") < order.index("c")
        assert order.index("b") < order.index("d")
        assert order.index("c") < order.index("d")

    def test_cycle_detection(self):
        p = Project(
            project_intent=ProjectIntent(name="test"),
            features={
                "x": FeatureNode(path="x", intents=[IntentFile(name="x", depends_on=["y"])]),
                "y": FeatureNode(path="y", intents=[IntentFile(name="y", depends_on=["x"])]),
            },
        )
        with pytest.raises(ValueError, match="cycle"):
            p.topological_order()

    def test_unknown_feature_raises(self):
        p = self._diamond()
        with pytest.raises(KeyError, match="nope"):
            p.parents("nope")


# ---------------------------------------------------------------------------
# load_project
# ---------------------------------------------------------------------------


class TestLoadProject:
    def test_loads_simple_project(self, tmp_path: Path):
        intent = _make_project(tmp_path)
        project = load_project(intent)

        assert project.project_intent.name == "test-project"
        assert "alpha" in project.features
        assert "beta" in project.features
        assert project.parents("beta") == ["alpha"]

    def test_loads_with_implementation(self, tmp_path: Path):
        intent = _make_project(tmp_path)
        _write_ic(intent / "implementation.ic", "impl")
        project = load_project(intent)

        assert project.implementation is not None
        assert project.implementation.name == "impl"

    def test_loads_assertions(self, tmp_path: Path):
        intent = _make_project(tmp_path)
        _write_icv(intent / "assertions" / "check.icv", "alpha")
        project = load_project(intent)

        assert len(project.assertions) == 1
        assert project.assertions[0].target == "alpha"

    def test_loads_feature_validations(self, tmp_path: Path):
        intent = _make_project(tmp_path)
        _write_icv(intent / "alpha" / "validations.icv", "alpha")
        project = load_project(intent)

        assert len(project.features["alpha"].validations) == 1

    def test_skips_empty_icv(self, tmp_path: Path):
        intent = _make_project(tmp_path)
        (intent / "alpha" / "validations.icv").write_text("")
        project = load_project(intent)

        assert len(project.features["alpha"].validations) == 0

    def test_missing_project_ic(self, tmp_path: Path):
        intent = tmp_path / "intent"
        intent.mkdir()
        _write_ic(intent / "feat" / "feat.ic", "feat")

        with pytest.raises(ParseErrors) as exc_info:
            load_project(intent)
        assert "project.ic" in str(exc_info.value)

    def test_missing_intent_dir(self, tmp_path: Path):
        with pytest.raises(ParseErrors) as exc_info:
            load_project(tmp_path / "nonexistent")
        assert "not found" in str(exc_info.value)

    def test_bad_dependency_reference(self, tmp_path: Path):
        intent = _make_project(tmp_path, {"alpha": ["does_not_exist"]})

        with pytest.raises(ParseErrors) as exc_info:
            load_project(intent)
        assert "does_not_exist" in str(exc_info.value)

    def test_cycle_in_loaded_project(self, tmp_path: Path):
        intent = _make_project(tmp_path, {"a": ["b"], "b": ["a"]})

        with pytest.raises(ParseErrors) as exc_info:
            load_project(intent)
        assert "cycle" in str(exc_info.value).lower()

    def test_accumulates_multiple_errors(self, tmp_path: Path):
        intent = tmp_path / "intent"
        _write_ic(intent / "project.ic", "test-project")
        # Two malformed intent files
        (intent / "bad1").mkdir(parents=True)
        (intent / "bad1" / "bad1.ic").write_text("no frontmatter")
        (intent / "bad2").mkdir(parents=True)
        (intent / "bad2" / "bad2.ic").write_text("also no frontmatter")

        with pytest.raises(ParseErrors) as exc_info:
            load_project(intent)
        assert len(exc_info.value.errors) >= 2

    def test_nested_features(self, tmp_path: Path):
        intent = _make_project(tmp_path, {"mod/sub/feat": []})
        project = load_project(intent)

        assert "mod/sub/feat" in project.features


# ---------------------------------------------------------------------------
# write_project
# ---------------------------------------------------------------------------


class TestWriteProject:
    def test_roundtrip(self, tmp_path: Path):
        intent = _make_project(tmp_path, {"alpha": [], "beta": ["alpha"]})
        _write_icv(intent / "alpha" / "validations.icv", "alpha")
        _write_ic(intent / "implementation.ic", "impl")

        original = load_project(intent)
        dest = tmp_path / "copy"
        write_project(original, dest)
        reloaded = load_project(dest)

        assert reloaded.project_intent.name == original.project_intent.name
        assert set(reloaded.features) == set(original.features)
        assert reloaded.implementation is not None
        assert reloaded.parents("beta") == ["alpha"]

    def test_copies_file_references(self, tmp_path: Path):
        intent = _make_project(tmp_path, {"feat": []})
        # Create a referenced file
        ref_file = intent / "feat" / "design.png"
        ref_file.write_bytes(b"PNG_DATA")
        # Write intent body that references it
        _write_ic(
            intent / "feat" / "feat.ic",
            "feat",
            body="See the [design](design.png) for details.",
        )

        original = load_project(intent)
        dest = tmp_path / "copy"
        write_project(original, dest)

        assert (dest / "feat" / "design.png").exists()
        assert (dest / "feat" / "design.png").read_bytes() == b"PNG_DATA"

    def test_creates_parent_dirs(self, tmp_path: Path):
        project = blank_project("test")
        dest = tmp_path / "a" / "b" / "c"
        write_project(project, dest)
        assert (dest / "project.ic").exists()


# ---------------------------------------------------------------------------
# blank_project
# ---------------------------------------------------------------------------


class TestBlankProject:
    def test_has_project_intent(self):
        p = blank_project("my-app")
        assert p.project_intent.name == "my-app"

    def test_has_starter_feature(self):
        p = blank_project("my-app")
        assert "starter" in p.features
        assert len(p.features["starter"].intents) == 1

    def test_no_cycles(self):
        p = blank_project("my-app")
        order = p.topological_order()
        assert order == ["starter"]

    def test_write_and_reload(self, tmp_path: Path):
        p = blank_project("test-init")
        dest = tmp_path / "intent"
        write_project(p, dest)

        reloaded = load_project(dest)
        assert reloaded.project_intent.name == "test-init"
        assert "starter" in reloaded.features


# ---------------------------------------------------------------------------
# Parse the actual repo
# ---------------------------------------------------------------------------


class TestLoadRealProject:
    INTENT_ROOT = Path(__file__).resolve().parents[3] / "intent"

    def test_load_real_project(self):
        if not self.INTENT_ROOT.exists():
            pytest.skip("intent/ directory not found")
        project = load_project(self.INTENT_ROOT)

        assert project.project_intent.name == "intentc"
        assert "core/specifications" in project.features
        assert "core/project" in project.features
        assert project.parents("core/project") == ["core/specifications"]
        order = project.topological_order()
        assert order.index("core/specifications") < order.index("core/project")
