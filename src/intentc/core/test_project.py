from __future__ import annotations

import textwrap
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
)


# ---------------------------------------------------------------------------
# FeatureNode
# ---------------------------------------------------------------------------

class TestFeatureNode:
    def test_depends_on_empty(self):
        node = FeatureNode(path="a")
        assert node.depends_on == []

    def test_depends_on_single_intent(self):
        node = FeatureNode(
            path="a",
            intents=[IntentFile(name="x", depends_on=["b", "c"])],
        )
        assert node.depends_on == ["b", "c"]

    def test_depends_on_multiple_intents_deduplicates(self):
        node = FeatureNode(
            path="a",
            intents=[
                IntentFile(name="x", depends_on=["b", "c"]),
                IntentFile(name="y", depends_on=["c", "d"]),
            ],
        )
        assert node.depends_on == ["b", "c", "d"]


# ---------------------------------------------------------------------------
# Project — resolve_implementation
# ---------------------------------------------------------------------------

class TestResolveImplementation:
    def test_no_implementations(self):
        proj = Project(project_intent=ProjectIntent(name="test"))
        assert proj.resolve_implementation() is None

    def test_single_implementation(self):
        impl = Implementation(name="only")
        proj = Project(
            project_intent=ProjectIntent(name="test"),
            implementations={"only": impl},
        )
        assert proj.resolve_implementation() is impl

    def test_default_implementation(self):
        default = Implementation(name="default")
        other = Implementation(name="go")
        proj = Project(
            project_intent=ProjectIntent(name="test"),
            implementations={"default": default, "go": other},
        )
        assert proj.resolve_implementation() is default

    def test_explicit_name(self):
        impl = Implementation(name="go")
        proj = Project(
            project_intent=ProjectIntent(name="test"),
            implementations={"go": impl},
        )
        assert proj.resolve_implementation("go") is impl

    def test_explicit_name_not_found(self):
        proj = Project(
            project_intent=ProjectIntent(name="test"),
            implementations={"go": Implementation(name="go")},
        )
        with pytest.raises(KeyError, match="not found"):
            proj.resolve_implementation("rust")

    def test_ambiguous(self):
        proj = Project(
            project_intent=ProjectIntent(name="test"),
            implementations={
                "go": Implementation(name="go"),
                "rust": Implementation(name="rust"),
            },
        )
        with pytest.raises(ValueError, match="Ambiguous"):
            proj.resolve_implementation()


# ---------------------------------------------------------------------------
# Project — DAG traversal
# ---------------------------------------------------------------------------

def _make_dag_project() -> Project:
    """
    DAG:  a -> b -> d
          a -> c -> d
    """
    return Project(
        project_intent=ProjectIntent(name="test"),
        features={
            "a": FeatureNode(path="a", intents=[IntentFile(name="a")]),
            "b": FeatureNode(path="b", intents=[IntentFile(name="b", depends_on=["a"])]),
            "c": FeatureNode(path="c", intents=[IntentFile(name="c", depends_on=["a"])]),
            "d": FeatureNode(path="d", intents=[IntentFile(name="d", depends_on=["b", "c"])]),
        },
    )


class TestDAGTraversal:
    def test_parents(self):
        proj = _make_dag_project()
        assert proj.parents("d") == ["b", "c"]
        assert proj.parents("a") == []

    def test_parents_not_found(self):
        proj = _make_dag_project()
        with pytest.raises(KeyError):
            proj.parents("z")

    def test_ancestors(self):
        proj = _make_dag_project()
        assert proj.ancestors("d") == {"a", "b", "c"}
        assert proj.ancestors("a") == set()

    def test_children(self):
        proj = _make_dag_project()
        assert sorted(proj.children("a")) == ["b", "c"]
        assert proj.children("d") == []

    def test_descendants(self):
        proj = _make_dag_project()
        assert proj.descendants("a") == {"b", "c", "d"}
        assert proj.descendants("d") == set()

    def test_topological_order(self):
        proj = _make_dag_project()
        order = proj.topological_order()
        assert order.index("a") < order.index("b")
        assert order.index("a") < order.index("c")
        assert order.index("b") < order.index("d")
        assert order.index("c") < order.index("d")

    def test_cycle_detection(self):
        proj = Project(
            project_intent=ProjectIntent(name="test"),
            features={
                "a": FeatureNode(path="a", intents=[IntentFile(name="a", depends_on=["b"])]),
                "b": FeatureNode(path="b", intents=[IntentFile(name="b", depends_on=["a"])]),
            },
        )
        with pytest.raises(ValueError, match="Cycle"):
            proj.topological_order()


# ---------------------------------------------------------------------------
# load_project
# ---------------------------------------------------------------------------

def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")


class TestLoadProject:
    def test_minimal_project(self, tmp_path: Path):
        intent_dir = tmp_path / "intent"
        _write_file(intent_dir / "project.ic", """\
            ---
            name: myproject
            ---
            My project description.
        """)
        _write_file(intent_dir / "implementations" / "default.ic", """\
            ---
            name: default
            ---
            Python implementation.
        """)
        _write_file(intent_dir / "feat" / "feat.ic", """\
            ---
            name: feature-one
            ---
            Feature body.
        """)

        proj = load_project(intent_dir)
        assert proj.project_intent.name == "myproject"
        assert "default" in proj.implementations
        assert "feat" in proj.features
        assert proj.features["feat"].intents[0].name == "feature-one"
        assert proj.intent_dir == intent_dir

    def test_missing_intent_dir(self, tmp_path: Path):
        with pytest.raises(ParseErrors, match="does not exist"):
            load_project(tmp_path / "nonexistent")

    def test_missing_project_ic(self, tmp_path: Path):
        intent_dir = tmp_path / "intent"
        intent_dir.mkdir()
        with pytest.raises(ParseErrors, match="project.ic"):
            load_project(intent_dir)

    def test_nested_features(self, tmp_path: Path):
        intent_dir = tmp_path / "intent"
        _write_file(intent_dir / "project.ic", """\
            ---
            name: nested
            ---
        """)
        _write_file(intent_dir / "core" / "auth" / "auth.ic", """\
            ---
            name: auth
            ---
        """)
        _write_file(intent_dir / "core" / "db" / "db.ic", """\
            ---
            name: db
            ---
        """)

        proj = load_project(intent_dir)
        assert "core/auth" in proj.features
        assert "core/db" in proj.features

    def test_assertions(self, tmp_path: Path):
        intent_dir = tmp_path / "intent"
        _write_file(intent_dir / "project.ic", "---\nname: test\n---\n")
        _write_file(intent_dir / "assertions" / "smoke.icv", """\
            target: all
            validations:
              - name: smoke test
        """)

        proj = load_project(intent_dir)
        assert len(proj.assertions) == 1
        assert proj.assertions[0].target == "all"

    def test_wildcard_expansion(self, tmp_path: Path):
        intent_dir = tmp_path / "intent"
        _write_file(intent_dir / "project.ic", "---\nname: test\n---\n")
        _write_file(intent_dir / "core" / "a" / "a.ic", "---\nname: a\n---\n")
        _write_file(intent_dir / "core" / "b" / "b.ic", "---\nname: b\n---\n")
        _write_file(intent_dir / "app" / "app.ic", """\
            ---
            name: app
            depends_on: ["core/*"]
            ---
        """)

        proj = load_project(intent_dir)
        deps = proj.features["app"].depends_on
        assert "core/a" in deps
        assert "core/b" in deps

    def test_wildcard_no_match(self, tmp_path: Path):
        intent_dir = tmp_path / "intent"
        _write_file(intent_dir / "project.ic", "---\nname: test\n---\n")
        _write_file(intent_dir / "feat" / "feat.ic", """\
            ---
            name: feat
            depends_on: ["nonexistent/*"]
            ---
        """)

        with pytest.raises(ParseErrors, match="matched no features"):
            load_project(intent_dir)

    def test_accumulates_errors(self, tmp_path: Path):
        intent_dir = tmp_path / "intent"
        _write_file(intent_dir / "project.ic", "---\nname: test\n---\n")
        # Two features with missing name
        _write_file(intent_dir / "a" / "a.ic", "---\ntags: [x]\n---\n")
        _write_file(intent_dir / "b" / "b.ic", "---\ntags: [y]\n---\n")

        with pytest.raises(ParseErrors) as exc_info:
            load_project(intent_dir)
        assert len(exc_info.value.errors) == 2

    def test_validations_parsed(self, tmp_path: Path):
        intent_dir = tmp_path / "intent"
        _write_file(intent_dir / "project.ic", "---\nname: test\n---\n")
        _write_file(intent_dir / "feat" / "feat.ic", "---\nname: feat\n---\n")
        _write_file(intent_dir / "feat" / "validations.icv", """\
            target: feat
            validations:
              - name: check output
        """)

        proj = load_project(intent_dir)
        assert len(proj.features["feat"].validations) == 1


# ---------------------------------------------------------------------------
# write_project
# ---------------------------------------------------------------------------

class TestWriteProject:
    def test_roundtrip(self, tmp_path: Path):
        # Load a project, write it, load it again
        src = tmp_path / "src"
        _write_file(src / "project.ic", "---\nname: roundtrip\n---\nBody.\n")
        _write_file(src / "implementations" / "default.ic", "---\nname: default\n---\nImpl.\n")
        _write_file(src / "feat" / "feat.ic", "---\nname: feat\ndepends_on: []\n---\nFeat body.\n")

        proj = load_project(src)
        dest = tmp_path / "dest"
        write_project(proj, dest)

        proj2 = load_project(dest)
        assert proj2.project_intent.name == "roundtrip"
        assert "default" in proj2.implementations
        assert "feat" in proj2.features


# ---------------------------------------------------------------------------
# blank_project
# ---------------------------------------------------------------------------

class TestBlankProject:
    def test_blank_project_structure(self):
        proj = blank_project("my-app")
        assert proj.project_intent.name == "my-app"
        assert "default" in proj.implementations
        assert "starter" in proj.features
        assert proj.features["starter"].intents[0].name == "starter"

    def test_blank_project_write_and_load(self, tmp_path: Path):
        proj = blank_project("test-app")
        dest = tmp_path / "intent"
        write_project(proj, dest)

        loaded = load_project(dest)
        assert loaded.project_intent.name == "test-app"
        assert "starter" in loaded.features
