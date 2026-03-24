"""Tests for project loading, writing, DAG traversal, and blank project creation."""

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


# ── Helpers ──────────────────────────────────────────────────────────────


def _write_ic(path: Path, name: str, depends_on: list[str] | None = None, body: str = "") -> Path:
    """Write a minimal .ic file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"---\nname: {name}\n"]
    if depends_on:
        lines.append("depends_on:\n")
        for dep in depends_on:
            lines.append(f"  - {dep}\n")
    lines.append("---\n")
    if body:
        lines.append(body + "\n")
    path.write_text("".join(lines), encoding="utf-8")
    return path


def _write_icv(path: Path, target: str = "", validations: list[dict] | None = None) -> Path:
    """Write a minimal .icv file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if target:
        lines.append(f"target: {target}\n")
    if validations:
        lines.append("validations:\n")
        for v in validations:
            lines.append(f"  - name: {v['name']}\n")
            if "type" in v:
                lines.append(f"    type: {v['type']}\n")
    else:
        lines.append("validations: []\n")
    path.write_text("".join(lines), encoding="utf-8")
    return path


def _make_project(tmp_path: Path) -> Path:
    """Create a sample project layout and return the intent_dir."""
    intent_dir = tmp_path / "intent"
    _write_ic(intent_dir / "project.ic", "test-project", body="# Test Project")
    _write_ic(intent_dir / "implementations" / "default.ic", "default", body="# Default impl")
    _write_icv(intent_dir / "assertions" / "smoke.icv", target="project", validations=[{"name": "smoke"}])
    _write_ic(intent_dir / "core" / "models" / "models.ic", "models", body="# Models")
    _write_icv(intent_dir / "core" / "models" / "models.icv", target="core/models", validations=[{"name": "check-models"}])
    _write_ic(intent_dir / "core" / "parser" / "parser.ic", "parser", depends_on=["core/models"], body="# Parser")
    _write_ic(intent_dir / "ui" / "dashboard" / "dashboard.ic", "dashboard", depends_on=["core/models", "core/parser"], body="# Dashboard")
    return intent_dir


# ── FeatureNode ──────────────────────────────────────────────────────────


class TestFeatureNode:
    def test_defaults(self):
        node = FeatureNode(path="core/models")
        assert node.path == "core/models"
        assert node.intents == []
        assert node.validations == []
        assert node.depends_on == []

    def test_depends_on_combines_intents(self):
        i1 = IntentFile(name="a", depends_on=["x", "y"])
        i2 = IntentFile(name="b", depends_on=["y", "z"])
        node = FeatureNode(path="feat", intents=[i1, i2])
        assert node.depends_on == ["x", "y", "z"]

    def test_depends_on_preserves_order(self):
        i1 = IntentFile(name="a", depends_on=["c", "b", "a"])
        node = FeatureNode(path="feat", intents=[i1])
        assert node.depends_on == ["c", "b", "a"]


# ── Project model ────────────────────────────────────────────────────────


class TestProjectModel:
    def test_resolve_implementation_single(self):
        proj = Project(
            project_intent=ProjectIntent(name="p"),
            implementations={"go": Implementation(name="go")},
        )
        assert proj.resolve_implementation().name == "go"

    def test_resolve_implementation_by_name(self):
        proj = Project(
            project_intent=ProjectIntent(name="p"),
            implementations={
                "go": Implementation(name="go"),
                "python": Implementation(name="python"),
            },
        )
        assert proj.resolve_implementation("python").name == "python"

    def test_resolve_implementation_default_preferred(self):
        proj = Project(
            project_intent=ProjectIntent(name="p"),
            implementations={
                "default": Implementation(name="default"),
                "alt": Implementation(name="alt"),
            },
        )
        assert proj.resolve_implementation().name == "default"

    def test_resolve_implementation_ambiguous(self):
        proj = Project(
            project_intent=ProjectIntent(name="p"),
            implementations={
                "go": Implementation(name="go"),
                "python": Implementation(name="python"),
            },
        )
        with pytest.raises(ValueError, match="Ambiguous"):
            proj.resolve_implementation()

    def test_resolve_implementation_missing_name(self):
        proj = Project(
            project_intent=ProjectIntent(name="p"),
            implementations={"go": Implementation(name="go")},
        )
        with pytest.raises(KeyError, match="not found"):
            proj.resolve_implementation("rust")

    def test_resolve_implementation_empty(self):
        proj = Project(project_intent=ProjectIntent(name="p"))
        assert proj.resolve_implementation() is None


# ── DAG traversal ────────────────────────────────────────────────────────


class TestDAGTraversal:
    def _build_project(self) -> Project:
        """A -> B -> C chain, plus D depends on both A and B."""
        return Project(
            project_intent=ProjectIntent(name="p"),
            features={
                "a": FeatureNode(path="a", intents=[IntentFile(name="a")]),
                "b": FeatureNode(path="b", intents=[IntentFile(name="b", depends_on=["a"])]),
                "c": FeatureNode(path="c", intents=[IntentFile(name="c", depends_on=["b"])]),
                "d": FeatureNode(path="d", intents=[IntentFile(name="d", depends_on=["a", "b"])]),
            },
        )

    def test_parents(self):
        proj = self._build_project()
        assert proj.parents("c") == ["b"]
        assert proj.parents("d") == ["a", "b"]
        assert proj.parents("a") == []

    def test_ancestors(self):
        proj = self._build_project()
        assert proj.ancestors("c") == {"a", "b"}
        assert proj.ancestors("d") == {"a", "b"}
        assert proj.ancestors("a") == set()

    def test_children(self):
        proj = self._build_project()
        assert sorted(proj.children("a")) == ["b", "d"]
        assert proj.children("c") == []

    def test_descendants(self):
        proj = self._build_project()
        assert proj.descendants("a") == {"b", "c", "d"}
        assert proj.descendants("c") == set()

    def test_topological_order(self):
        proj = self._build_project()
        order = proj.topological_order()
        assert order.index("a") < order.index("b")
        assert order.index("b") < order.index("c")
        assert order.index("a") < order.index("d")

    def test_topological_order_cycle(self):
        proj = Project(
            project_intent=ProjectIntent(name="p"),
            features={
                "x": FeatureNode(path="x", intents=[IntentFile(name="x", depends_on=["y"])]),
                "y": FeatureNode(path="y", intents=[IntentFile(name="y", depends_on=["x"])]),
            },
        )
        with pytest.raises(ValueError, match="cycle"):
            proj.topological_order()

    def test_require_feature_missing(self):
        proj = Project(project_intent=ProjectIntent(name="p"))
        with pytest.raises(KeyError, match="not found"):
            proj.parents("nonexistent")

    def test_require_feature_missing_children(self):
        proj = Project(project_intent=ProjectIntent(name="p"))
        with pytest.raises(KeyError):
            proj.children("nonexistent")

    def test_require_feature_missing_ancestors(self):
        proj = Project(project_intent=ProjectIntent(name="p"))
        with pytest.raises(KeyError):
            proj.ancestors("nonexistent")

    def test_require_feature_missing_descendants(self):
        proj = Project(project_intent=ProjectIntent(name="p"))
        with pytest.raises(KeyError):
            proj.descendants("nonexistent")


# ── load_project ─────────────────────────────────────────────────────────


class TestLoadProject:
    def test_load_full_project(self, tmp_path):
        intent_dir = _make_project(tmp_path)
        proj = load_project(intent_dir)

        assert proj.project_intent.name == "test-project"
        assert proj.intent_dir == intent_dir
        assert "default" in proj.implementations
        assert len(proj.assertions) == 1
        assert "core/models" in proj.features
        assert "core/parser" in proj.features
        assert "ui/dashboard" in proj.features

    def test_load_features_have_correct_deps(self, tmp_path):
        intent_dir = _make_project(tmp_path)
        proj = load_project(intent_dir)

        assert proj.features["core/parser"].depends_on == ["core/models"]
        assert proj.features["ui/dashboard"].depends_on == ["core/models", "core/parser"]
        assert proj.features["core/models"].depends_on == []

    def test_load_validations_attached(self, tmp_path):
        intent_dir = _make_project(tmp_path)
        proj = load_project(intent_dir)

        assert len(proj.features["core/models"].validations) == 1

    def test_missing_project_ic(self, tmp_path):
        intent_dir = tmp_path / "intent"
        intent_dir.mkdir()
        with pytest.raises(ParseErrors, match="project.ic not found"):
            load_project(intent_dir)

    def test_malformed_intent_file(self, tmp_path):
        intent_dir = tmp_path / "intent"
        _write_ic(intent_dir / "project.ic", "proj")
        # Malformed .ic: missing name
        bad = intent_dir / "feat" / "bad" / "bad.ic"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("---\ntags: [x]\n---\nbody\n", encoding="utf-8")
        with pytest.raises(ParseErrors):
            load_project(intent_dir)

    def test_wildcard_expansion(self, tmp_path):
        intent_dir = tmp_path / "intent"
        _write_ic(intent_dir / "project.ic", "proj")
        _write_ic(intent_dir / "core" / "a" / "a.ic", "a")
        _write_ic(intent_dir / "core" / "b" / "b.ic", "b")
        _write_ic(intent_dir / "app" / "main" / "main.ic", "main", depends_on=["core/*"])

        proj = load_project(intent_dir)
        deps = proj.features["app/main"].depends_on
        assert "core/a" in deps
        assert "core/b" in deps

    def test_wildcard_no_match_errors(self, tmp_path):
        intent_dir = tmp_path / "intent"
        _write_ic(intent_dir / "project.ic", "proj")
        _write_ic(intent_dir / "feat" / "lonely" / "lonely.ic", "lonely", depends_on=["nonexistent/*"])
        with pytest.raises(ParseErrors, match="matched no features"):
            load_project(intent_dir)

    def test_load_empty_project(self, tmp_path):
        intent_dir = tmp_path / "intent"
        _write_ic(intent_dir / "project.ic", "empty-proj")
        proj = load_project(intent_dir)
        assert proj.project_intent.name == "empty-proj"
        assert proj.features == {}
        assert proj.implementations == {}
        assert proj.assertions == []

    def test_accumulates_errors(self, tmp_path):
        """Multiple bad files should accumulate errors and raise them all at once."""
        intent_dir = tmp_path / "intent"
        _write_ic(intent_dir / "project.ic", "proj")

        # Two bad files
        for name in ("bad1", "bad2"):
            bad = intent_dir / "feat" / name / f"{name}.ic"
            bad.parent.mkdir(parents=True, exist_ok=True)
            bad.write_text("---\ntags: [x]\n---\nbody\n", encoding="utf-8")

        with pytest.raises(ParseErrors) as exc_info:
            load_project(intent_dir)
        assert len(exc_info.value.errors) >= 2


# ── write_project ────────────────────────────────────────────────────────


class TestWriteProject:
    def test_write_and_reload(self, tmp_path):
        intent_dir = _make_project(tmp_path)
        proj = load_project(intent_dir)

        dest = tmp_path / "output"
        result = write_project(proj, dest)
        assert result == dest
        assert (dest / "project.ic").exists()
        assert (dest / "implementations" / "default.ic").exists()
        assert (dest / "assertions" / "smoke.icv").exists()
        assert (dest / "core" / "models" / "models.ic").exists()
        assert (dest / "core" / "models" / "models.icv").exists()

        # Reload from written output
        reloaded = load_project(dest)
        assert reloaded.project_intent.name == "test-project"
        assert set(reloaded.features.keys()) == set(proj.features.keys())

    def test_write_blank_project(self, tmp_path):
        proj = blank_project("my-project")
        dest = tmp_path / "blank"
        write_project(proj, dest)

        assert (dest / "project.ic").exists()
        assert (dest / "starter").is_dir()

    def test_write_copies_supporting_files(self, tmp_path):
        intent_dir = _make_project(tmp_path)
        # Add a supporting file
        supporting = intent_dir / "core" / "models" / "schema.png"
        supporting.write_bytes(b"fake-png-data")

        proj = load_project(intent_dir)
        dest = tmp_path / "output"
        write_project(proj, dest)

        assert (dest / "core" / "models" / "schema.png").exists()


# ── blank_project ────────────────────────────────────────────────────────


class TestBlankProject:
    def test_blank_has_required_fields(self):
        proj = blank_project("hello")
        assert proj.project_intent.name == "hello"
        assert "starter" in proj.features
        assert len(proj.features["starter"].intents) == 1
        assert proj.features["starter"].intents[0].name == "starter"

    def test_blank_has_validation(self):
        proj = blank_project("hello")
        assert len(proj.features["starter"].validations) == 1

    def test_blank_roundtrip(self, tmp_path):
        proj = blank_project("roundtrip-test")
        dest = tmp_path / "blank"
        write_project(proj, dest)
        reloaded = load_project(dest)
        assert reloaded.project_intent.name == "roundtrip-test"
        assert "starter" in reloaded.features
