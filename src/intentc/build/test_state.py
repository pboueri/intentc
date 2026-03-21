"""Tests for build state management."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from intentc.build.state import (
    BuildResult,
    BuildStep,
    GitVersionControl,
    StateManager,
    TargetStatus,
    VersionControl,
)
from intentc.build.storage import SQLiteBackend
from intentc.core.project import FeatureNode, Project
from intentc.core.types import IntentFile, ProjectIntent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(
    target: str = "feat/a",
    *,
    generation_id: str = "gen-1",
    status: TargetStatus = TargetStatus.BUILT,
    commit_id: str = "abc123",
) -> BuildResult:
    return BuildResult(
        target=target,
        generation_id=generation_id,
        status=status,
        steps=[
            BuildStep(
                phase="resolve_deps",
                status="success",
                duration=timedelta(seconds=0.5),
                summary="Resolved 2 dependencies",
            ),
            BuildStep(
                phase="build",
                status="success",
                duration=timedelta(seconds=3.2),
                summary="Agent generated 4 files",
            ),
        ],
        commit_id=commit_id,
        total_duration=timedelta(seconds=3.7),
        timestamp=datetime(2026, 3, 16, 12, 0, 0),
    )


def _diamond_project() -> Project:
    """A -> B,C -> D diamond graph."""
    return Project(
        project_intent=ProjectIntent(name="test"),
        features={
            "a": FeatureNode(path="a", intents=[IntentFile(name="a")]),
            "b": FeatureNode(path="b", intents=[IntentFile(name="b", depends_on=["a"])]),
            "c": FeatureNode(path="c", intents=[IntentFile(name="c", depends_on=["a"])]),
            "d": FeatureNode(path="d", intents=[IntentFile(name="d", depends_on=["b", "c"])]),
        },
    )


def _make_sm(tmp_path: Path, output_dir: str = "out") -> StateManager:
    """Create a StateManager with an explicit SQLiteBackend for testing."""
    backend = SQLiteBackend(tmp_path, output_dir)
    # Create a dummy generation for test results
    backend.create_generation("gen-1", output_dir, None, None)
    return StateManager(tmp_path, output_dir, backend=backend)


# ---------------------------------------------------------------------------
# TargetStatus
# ---------------------------------------------------------------------------


class TestTargetStatus:
    def test_values(self):
        assert TargetStatus.PENDING == "pending"
        assert TargetStatus.BUILT == "built"
        assert TargetStatus.FAILED == "failed"
        assert TargetStatus.OUTDATED == "outdated"


# ---------------------------------------------------------------------------
# BuildStep / BuildResult
# ---------------------------------------------------------------------------


class TestBuildStep:
    def test_construction(self):
        step = BuildStep(
            phase="build",
            status="success",
            duration=timedelta(seconds=1.5),
            summary="Done",
        )
        assert step.phase == "build"
        assert step.status == "success"
        assert step.duration == timedelta(seconds=1.5)
        assert step.summary == "Done"

    def test_extra_fields_ignored(self):
        step = BuildStep(
            phase="build",
            status="success",
            duration=timedelta(seconds=1),
            summary="ok",
            unknown_field="ignored",  # type: ignore[call-arg]
        )
        assert not hasattr(step, "unknown_field")


class TestBuildResult:
    def test_all_fields(self):
        result = _make_result()
        assert result.target == "feat/a"
        assert result.generation_id == "gen-1"
        assert result.status == TargetStatus.BUILT
        assert len(result.steps) == 2
        assert result.commit_id == "abc123"
        assert result.total_duration == timedelta(seconds=3.7)
        assert result.timestamp == datetime(2026, 3, 16, 12, 0, 0)

    def test_defaults(self):
        result = BuildResult(
            target="x",
            generation_id="g",
            status=TargetStatus.PENDING,
            timestamp=datetime.now(),
        )
        assert result.steps == []
        assert result.commit_id == ""
        assert result.total_duration == timedelta()


# ---------------------------------------------------------------------------
# StateManager — basic operations
# ---------------------------------------------------------------------------


class TestStateManager:
    def test_unknown_target_is_pending(self, tmp_path: Path):
        sm = StateManager(tmp_path, "out")
        assert sm.get_status("nonexistent") == TargetStatus.PENDING

    def test_unknown_target_result_is_none(self, tmp_path: Path):
        sm = StateManager(tmp_path, "out")
        assert sm.get_build_result("nonexistent") is None

    def test_save_and_get(self, tmp_path: Path):
        sm = _make_sm(tmp_path)
        result = _make_result()
        sm.save_build_result("feat/a", result)

        assert sm.get_status("feat/a") == TargetStatus.BUILT
        loaded = sm.get_build_result("feat/a")
        assert loaded is not None
        assert loaded.target == "feat/a"
        assert loaded.commit_id == "abc123"

    def test_set_status(self, tmp_path: Path):
        sm = StateManager(tmp_path, "out")
        sm.set_status("feat/a", TargetStatus.OUTDATED)
        assert sm.get_status("feat/a") == TargetStatus.OUTDATED

    def test_list_targets(self, tmp_path: Path):
        sm = _make_sm(tmp_path)
        sm.save_build_result("b", _make_result("b"))
        sm.save_build_result("a", _make_result("a"))
        targets = sm.list_targets()
        assert targets == [("a", TargetStatus.BUILT), ("b", TargetStatus.BUILT)]

    def test_reset_target(self, tmp_path: Path):
        sm = _make_sm(tmp_path)
        sm.save_build_result("feat/a", _make_result())
        sm.save_build_result("feat/b", _make_result("feat/b"))
        sm.reset("feat/a")

        assert sm.get_status("feat/a") == TargetStatus.PENDING
        assert sm.get_build_result("feat/a") is None
        # Other target unaffected
        assert sm.get_status("feat/b") == TargetStatus.BUILT

    def test_reset_all(self, tmp_path: Path):
        sm = _make_sm(tmp_path)
        sm.save_build_result("feat/a", _make_result())
        sm.save_build_result("feat/b", _make_result("feat/b"))
        sm.reset_all()

        assert sm.list_targets() == []
        assert sm.get_status("feat/a") == TargetStatus.PENDING


# ---------------------------------------------------------------------------
# Response directory properties
# ---------------------------------------------------------------------------


class TestResponseDirs:
    def test_build_response_dir(self, tmp_path: Path):
        sm = StateManager(tmp_path, "src")
        expected = tmp_path / ".intentc" / "state" / "src" / "responses" / "build"
        assert sm.build_response_dir == expected

    def test_val_response_dir(self, tmp_path: Path):
        sm = StateManager(tmp_path, "src")
        expected = tmp_path / ".intentc" / "state" / "src" / "responses" / "val"
        assert sm.val_response_dir == expected


# ---------------------------------------------------------------------------
# State roundtrip — save, reload from database, verify
# ---------------------------------------------------------------------------


class TestStateRoundtrip:
    def test_full_roundtrip(self, tmp_path: Path):
        sm = _make_sm(tmp_path)
        original = _make_result()
        sm.save_build_result("feat/a", original)

        # Create a new StateManager from the same database
        sm2 = StateManager(tmp_path, "out")
        assert sm2.get_status("feat/a") == TargetStatus.BUILT

        loaded = sm2.get_build_result("feat/a")
        assert loaded is not None
        assert loaded.target == original.target
        assert loaded.generation_id == original.generation_id
        assert loaded.status == original.status
        assert loaded.commit_id == original.commit_id
        assert loaded.total_duration == original.total_duration
        assert loaded.timestamp == original.timestamp
        assert len(loaded.steps) == len(original.steps)
        for orig_step, loaded_step in zip(original.steps, loaded.steps):
            assert loaded_step.phase == orig_step.phase
            assert loaded_step.status == orig_step.status
            assert loaded_step.duration == orig_step.duration
            assert loaded_step.summary == orig_step.summary

    def test_missing_database_returns_defaults(self, tmp_path: Path):
        sm = StateManager(tmp_path, "out")
        assert sm.get_status("anything") == TargetStatus.PENDING
        assert sm.get_build_result("anything") is None
        assert sm.list_targets() == []

    def test_backend_property_exposed(self, tmp_path: Path):
        backend = SQLiteBackend(tmp_path, "out")
        sm = StateManager(tmp_path, "out", backend=backend)
        assert sm.backend is backend


# ---------------------------------------------------------------------------
# DAG-aware operations
# ---------------------------------------------------------------------------


class TestDAGOperations:
    def test_mark_dependents_outdated(self, tmp_path: Path):
        project = _diamond_project()
        sm = _make_sm(tmp_path)

        # Build all targets
        for name in ["a", "b", "c", "d"]:
            sm.save_build_result(name, _make_result(name))

        # Mark dependents of "a" as outdated
        sm.mark_dependents_outdated("a", project)

        assert sm.get_status("a") == TargetStatus.BUILT  # unchanged
        assert sm.get_status("b") == TargetStatus.OUTDATED
        assert sm.get_status("c") == TargetStatus.OUTDATED
        assert sm.get_status("d") == TargetStatus.OUTDATED

    def test_mark_dependents_partial_graph(self, tmp_path: Path):
        project = _diamond_project()
        sm = _make_sm(tmp_path)

        for name in ["a", "b", "c", "d"]:
            sm.save_build_result(name, _make_result(name))

        # Mark dependents of "b" — only "d" depends on "b"
        sm.mark_dependents_outdated("b", project)

        assert sm.get_status("a") == TargetStatus.BUILT
        assert sm.get_status("b") == TargetStatus.BUILT  # unchanged
        assert sm.get_status("c") == TargetStatus.BUILT
        assert sm.get_status("d") == TargetStatus.OUTDATED

    def test_reset_does_not_affect_others(self, tmp_path: Path):
        sm = _make_sm(tmp_path)
        sm.save_build_result("a", _make_result("a"))
        sm.save_build_result("b", _make_result("b"))

        sm.reset("a")

        assert sm.get_status("a") == TargetStatus.PENDING
        assert sm.get_status("b") == TargetStatus.BUILT
        assert sm.get_build_result("b") is not None


# ---------------------------------------------------------------------------
# Build history — append-only in database
# ---------------------------------------------------------------------------


class TestBuildHistory:
    def test_append_only(self, tmp_path: Path):
        sm = _make_sm(tmp_path)

        r1 = _make_result("feat/a", generation_id="gen-1")
        r2 = _make_result("feat/b", generation_id="gen-1")
        r3 = _make_result("feat/a", generation_id="gen-1")

        sm.save_build_result("feat/a", r1)
        sm.save_build_result("feat/b", r2)
        sm.save_build_result("feat/a", r3)

        # Query build history
        history = sm.backend.get_build_history("feat/a")
        assert len(history) == 2  # two results for feat/a

        # Target state always points to latest
        latest = sm.get_build_result("feat/a")
        assert latest is not None

    def test_history_preserves_entries(self, tmp_path: Path):
        sm = _make_sm(tmp_path)
        for i in range(5):
            sm.save_build_result(f"t{i}", _make_result(f"t{i}", generation_id="gen-1"))

        # All 5 targets tracked
        targets = sm.list_targets()
        assert len(targets) == 5


# ---------------------------------------------------------------------------
# VersionControl interface
# ---------------------------------------------------------------------------


class TestVersionControlInterface:
    def test_is_abstract(self):
        with pytest.raises(TypeError):
            VersionControl()  # type: ignore[abstract]

    def test_git_version_control_is_concrete(self, tmp_path: Path):
        gvc = GitVersionControl(tmp_path)
        assert isinstance(gvc, VersionControl)


class TestGitVersionControl:
    def _init_repo(self, tmp_path: Path) -> Path:
        """Initialize a git repo with an initial commit."""
        import subprocess

        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=tmp_path, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=tmp_path, capture_output=True, check=True,
        )
        (tmp_path / "README.md").write_text("init")
        subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=tmp_path, capture_output=True, check=True,
        )
        return tmp_path

    def test_checkpoint_returns_sha(self, tmp_path: Path):
        repo = self._init_repo(tmp_path)
        gvc = GitVersionControl(repo)

        (repo / "file.txt").write_text("hello")
        sha = gvc.checkpoint("add file")

        assert len(sha) == 40
        assert all(c in "0123456789abcdef" for c in sha)

    def test_diff_shows_changes(self, tmp_path: Path):
        repo = self._init_repo(tmp_path)
        gvc = GitVersionControl(repo)

        sha1 = gvc.checkpoint("empty checkpoint")

        (repo / "file.txt").write_text("content")
        sha2 = gvc.checkpoint("add file")

        diff = gvc.diff(sha1, sha2)
        assert "file.txt" in diff
        assert "content" in diff

    def test_log_returns_shas(self, tmp_path: Path):
        repo = self._init_repo(tmp_path)
        gvc = GitVersionControl(repo)

        gvc.checkpoint("first")
        gvc.checkpoint("second")

        shas = gvc.log()
        # At least initial + 2 checkpoints
        assert len(shas) >= 3
        assert all(len(s) == 40 for s in shas)

    def test_log_filters_by_target(self, tmp_path: Path):
        repo = self._init_repo(tmp_path)
        gvc = GitVersionControl(repo)

        gvc.checkpoint("build feat/a")
        gvc.checkpoint("build feat/b")

        shas = gvc.log(target="feat/a")
        assert len(shas) == 1

    def test_restore(self, tmp_path: Path):
        repo = self._init_repo(tmp_path)
        gvc = GitVersionControl(repo)

        (repo / "file.txt").write_text("v1")
        sha1 = gvc.checkpoint("v1")

        (repo / "file.txt").write_text("v2")
        gvc.checkpoint("v2")

        gvc.restore(sha1)
        assert (repo / "file.txt").read_text() == "v1"
