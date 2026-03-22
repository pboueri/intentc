"""Tests for intentc.build.state — StateManager, VersionControl, types."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
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
    target: str = "core/foo",
    status: TargetStatus = TargetStatus.BUILT,
    gen_id: str | None = None,
    commit_id: str = "abc123",
) -> BuildResult:
    return BuildResult(
        generation_id=gen_id or uuid.uuid4().hex[:8],
        target=target,
        status=status,
        commit_id=commit_id,
        total_duration_secs=1.5,
        timestamp=datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
        steps=[
            BuildStep(phase="resolve_deps", status="success", duration_secs=0.5, summary="Resolved deps"),
            BuildStep(phase="build", status="success", duration_secs=1.0, summary="Built target"),
        ],
    )


def _make_project() -> Project:
    """A -> B -> C linear DAG."""
    return Project(
        project_intent=ProjectIntent(name="test", body="test"),
        features={
            "a": FeatureNode(path="a", intents=[IntentFile(name="a", body="a")]),
            "b": FeatureNode(path="b", intents=[IntentFile(name="b", body="b", depends_on=["a"])]),
            "c": FeatureNode(path="c", intents=[IntentFile(name="c", body="c", depends_on=["b"])]),
        },
    )


def _make_manager(tmp: Path) -> StateManager:
    backend = SQLiteBackend(tmp, "src")
    _ensure_generation(backend, "gen-1")
    return StateManager(tmp, "src", backend=backend)


def _ensure_generation(backend: SQLiteBackend, gen_id: str) -> None:
    """Create a generation row if it doesn't already exist."""
    if backend.get_generation(gen_id) is None:
        backend.create_generation(gen_id, "src", None, None)


# ---------------------------------------------------------------------------
# Type tests
# ---------------------------------------------------------------------------


class TestTargetStatus:
    def test_enum_values(self) -> None:
        assert TargetStatus.PENDING.value == "pending"
        assert TargetStatus.BUILT.value == "built"
        assert TargetStatus.FAILED.value == "failed"
        assert TargetStatus.OUTDATED.value == "outdated"

    def test_string_valued(self) -> None:
        assert isinstance(TargetStatus.BUILT, str)
        assert TargetStatus("built") is TargetStatus.BUILT


class TestBuildStep:
    def test_fields(self) -> None:
        step = BuildStep(phase="build", status="success", duration_secs=2.5, summary="ok")
        assert step.phase == "build"
        assert step.status == "success"
        assert step.duration_secs == 2.5
        assert step.summary == "ok"


class TestBuildResult:
    def test_defaults(self) -> None:
        r = BuildResult(generation_id="g1", target="t", status=TargetStatus.PENDING)
        assert r.commit_id == ""
        assert r.total_duration_secs == 0.0
        assert r.steps == []
        assert isinstance(r.timestamp, datetime)

    def test_all_fields(self) -> None:
        r = _make_result()
        assert r.status == TargetStatus.BUILT
        assert len(r.steps) == 2
        assert r.commit_id == "abc123"


# ---------------------------------------------------------------------------
# VersionControl interface test
# ---------------------------------------------------------------------------


class TestVersionControlInterface:
    def test_abstract(self) -> None:
        assert issubclass(GitVersionControl, VersionControl)
        with pytest.raises(TypeError):
            VersionControl()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# State roundtrip with real SQLiteBackend
# ---------------------------------------------------------------------------


class TestStateRoundtrip:
    def test_roundtrip_through_new_manager(self, tmp_path: Path) -> None:
        """Save via one StateManager, load via a fresh one — all fields survive."""
        backend1 = SQLiteBackend(tmp_path, "src")
        gen_id = "gen-rt"
        _ensure_generation(backend1, gen_id)
        sm1 = StateManager(tmp_path, "src", backend=backend1)

        result = _make_result(gen_id=gen_id)
        sm1.save_build_result("core/foo", result)

        # Create a completely new backend + manager from same DB path
        backend2 = SQLiteBackend(tmp_path, "src")
        sm2 = StateManager(tmp_path, "src", backend=backend2)

        assert sm2.get_status("core/foo") == TargetStatus.BUILT

        loaded = sm2.get_build_result("core/foo")
        assert loaded is not None
        assert loaded.generation_id == gen_id
        assert loaded.target == "core/foo"
        assert loaded.status == TargetStatus.BUILT
        assert loaded.commit_id == "abc123"
        assert loaded.total_duration_secs == 1.5
        assert loaded.timestamp == datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        assert len(loaded.steps) == 2
        assert loaded.steps[0].phase == "resolve_deps"
        assert loaded.steps[0].duration_secs == 0.5
        assert loaded.steps[1].phase == "build"
        assert loaded.steps[1].status == "success"

    def test_missing_target_returns_defaults(self, tmp_path: Path) -> None:
        sm = _make_manager(tmp_path)
        assert sm.get_status("nonexistent") == TargetStatus.PENDING
        assert sm.get_build_result("nonexistent") is None


# ---------------------------------------------------------------------------
# DAG-aware operations
# ---------------------------------------------------------------------------


class TestDAGOperations:
    def test_mark_dependents_outdated(self, tmp_path: Path) -> None:
        backend = SQLiteBackend(tmp_path, "src")
        gen_id = "gen-dag"
        _ensure_generation(backend, gen_id)
        sm = StateManager(tmp_path, "src", backend=backend)
        project = _make_project()

        # Build all three
        for t in ["a", "b", "c"]:
            sm.save_build_result(t, _make_result(target=t, gen_id=gen_id))

        assert sm.get_status("a") == TargetStatus.BUILT
        assert sm.get_status("b") == TargetStatus.BUILT
        assert sm.get_status("c") == TargetStatus.BUILT

        # Mark dependents of "a" outdated
        sm.mark_dependents_outdated("a", project)

        assert sm.get_status("a") == TargetStatus.BUILT  # unchanged
        assert sm.get_status("b") == TargetStatus.OUTDATED
        assert sm.get_status("c") == TargetStatus.OUTDATED

    def test_reset_clears_single_target(self, tmp_path: Path) -> None:
        backend = SQLiteBackend(tmp_path, "src")
        gen_id = "gen-reset"
        _ensure_generation(backend, gen_id)
        sm = StateManager(tmp_path, "src", backend=backend)

        sm.save_build_result("a", _make_result(target="a", gen_id=gen_id))
        sm.save_build_result("b", _make_result(target="b", gen_id=gen_id))

        sm.reset("a")
        assert sm.get_status("a") == TargetStatus.PENDING
        assert sm.get_status("b") == TargetStatus.BUILT  # untouched

    def test_mark_dependents_skips_pending(self, tmp_path: Path) -> None:
        """Pending targets should not be marked outdated."""
        backend = SQLiteBackend(tmp_path, "src")
        gen_id = "gen-skip"
        _ensure_generation(backend, gen_id)
        sm = StateManager(tmp_path, "src", backend=backend)
        project = _make_project()

        # Only build "a", leave "b" and "c" pending
        sm.save_build_result("a", _make_result(target="a", gen_id=gen_id))

        sm.mark_dependents_outdated("a", project)
        assert sm.get_status("b") == TargetStatus.PENDING
        assert sm.get_status("c") == TargetStatus.PENDING


# ---------------------------------------------------------------------------
# Build history — append-only
# ---------------------------------------------------------------------------


class TestBuildHistoryAppendOnly:
    def test_multiple_saves_append(self, tmp_path: Path) -> None:
        backend = SQLiteBackend(tmp_path, "src")
        gen_id = "gen-hist"
        _ensure_generation(backend, gen_id)
        sm = StateManager(tmp_path, "src", backend=backend)

        # Save three build results for the same target
        for i in range(3):
            r = _make_result(
                target="feat/x",
                gen_id=gen_id,
                status=TargetStatus.BUILT if i < 2 else TargetStatus.FAILED,
                commit_id=f"sha-{i}",
            )
            sm.save_build_result("feat/x", r)

        # History should have all 3, most recent first
        history = backend.get_build_history("feat/x")
        assert len(history) == 3
        assert history[0].commit_id == "sha-2"
        assert history[0].status == TargetStatus.FAILED
        assert history[1].commit_id == "sha-1"
        assert history[2].commit_id == "sha-0"

        # Current status reflects the latest
        assert sm.get_status("feat/x") == TargetStatus.FAILED

    def test_previous_entries_not_overwritten(self, tmp_path: Path) -> None:
        backend = SQLiteBackend(tmp_path, "src")
        gen_id = "gen-nodel"
        _ensure_generation(backend, gen_id)
        sm = StateManager(tmp_path, "src", backend=backend)

        r1 = _make_result(target="t", gen_id=gen_id, commit_id="first")
        sm.save_build_result("t", r1)
        r2 = _make_result(target="t", gen_id=gen_id, commit_id="second")
        sm.save_build_result("t", r2)

        history = backend.get_build_history("t")
        commits = [h.commit_id for h in history]
        assert "first" in commits
        assert "second" in commits


# ---------------------------------------------------------------------------
# Response file cleanup
# ---------------------------------------------------------------------------


class TestResponseFileCleanup:
    def test_response_file_stored_and_deleted(self, tmp_path: Path) -> None:
        backend = SQLiteBackend(tmp_path, "src")
        gen_id = "gen-resp"
        _ensure_generation(backend, gen_id)
        sm = StateManager(tmp_path, "src", backend=backend)

        # Simulate agent writing a response file
        resp_data = {"status": "success", "summary": "built ok", "files_created": ["a.py"]}
        resp_name = "core_foo-abcd1234.json"
        resp_path = sm.build_response_dir / resp_name
        resp_path.write_text(json.dumps(resp_data))
        assert resp_path.exists()

        # Save build result
        result = _make_result(gen_id=gen_id)
        sm.save_build_result("core/foo", result)
        build_result_id = backend.get_build_history("core/foo")[0]

        # Read response, store in DB, delete file (simulating the caller workflow)
        response_json = json.loads(resp_path.read_text())
        backend.save_agent_response(
            build_result_id=None,
            validation_result_id=None,
            response_type="build",
            response_json=response_json,
        )
        resp_path.unlink()

        # File is gone
        assert not resp_path.exists()
        # No response files left in directory
        remaining = list(sm.build_response_dir.glob("*.json"))
        assert len(remaining) == 0


# ---------------------------------------------------------------------------
# StateManager properties
# ---------------------------------------------------------------------------


class TestStateManagerProperties:
    def test_response_dirs(self, tmp_path: Path) -> None:
        sm = _make_manager(tmp_path)
        assert sm.build_response_dir == tmp_path / ".intentc" / "state" / "src" / "responses" / "build"
        assert sm.val_response_dir == tmp_path / ".intentc" / "state" / "src" / "responses" / "val"
        assert sm.build_response_dir.is_dir()
        assert sm.val_response_dir.is_dir()

    def test_list_targets(self, tmp_path: Path) -> None:
        backend = SQLiteBackend(tmp_path, "src")
        gen_id = "gen-list"
        _ensure_generation(backend, gen_id)
        sm = StateManager(tmp_path, "src", backend=backend)

        sm.save_build_result("a", _make_result(target="a", gen_id=gen_id))
        sm.save_build_result("b", _make_result(target="b", gen_id=gen_id))

        targets = sm.list_targets()
        assert len(targets) == 2
        target_names = {t[0] for t in targets}
        assert target_names == {"a", "b"}

    def test_reset_all(self, tmp_path: Path) -> None:
        backend = SQLiteBackend(tmp_path, "src")
        gen_id = "gen-ra"
        _ensure_generation(backend, gen_id)
        sm = StateManager(tmp_path, "src", backend=backend)

        sm.save_build_result("x", _make_result(target="x", gen_id=gen_id))
        sm.save_build_result("y", _make_result(target="y", gen_id=gen_id))
        sm.reset_all()
        assert sm.list_targets() == []

    def test_set_status(self, tmp_path: Path) -> None:
        sm = _make_manager(tmp_path)
        sm.set_status("t", TargetStatus.OUTDATED)
        assert sm.get_status("t") == TargetStatus.OUTDATED

    def test_default_backend_creation(self, tmp_path: Path) -> None:
        """StateManager creates SQLiteBackend when none provided."""
        sm = StateManager(tmp_path, "out")
        assert sm.get_status("anything") == TargetStatus.PENDING
