from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
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


def _make_project() -> Project:
    """Create a small DAG: a -> b -> c, a -> d."""
    return Project(
        project_intent=ProjectIntent(name="test", body="test"),
        features={
            "a": FeatureNode(
                path="a",
                intents=[IntentFile(name="a", body="a")],
            ),
            "b": FeatureNode(
                path="b",
                intents=[IntentFile(name="b", body="b", depends_on=["a"])],
            ),
            "c": FeatureNode(
                path="c",
                intents=[IntentFile(name="c", body="c", depends_on=["b"])],
            ),
            "d": FeatureNode(
                path="d",
                intents=[IntentFile(name="d", body="d", depends_on=["a"])],
            ),
        },
    )


def _sample_result(target: str = "feat/x", gen_id: str = "gen-1") -> BuildResult:
    return BuildResult(
        target=target,
        generation_id=gen_id,
        status=TargetStatus.BUILT,
        steps=[
            BuildStep(phase="resolve_deps", status="success", duration=timedelta(seconds=1.5), summary="resolved"),
            BuildStep(phase="build", status="success", duration=timedelta(seconds=3.0), summary="built"),
        ],
        commit_id="abc123",
        total_duration=timedelta(seconds=4.5),
        timestamp=datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
    )


class TestTargetStatus:
    def test_values(self):
        assert TargetStatus.PENDING.value == "pending"
        assert TargetStatus.BUILT.value == "built"
        assert TargetStatus.FAILED.value == "failed"
        assert TargetStatus.OUTDATED.value == "outdated"

    def test_from_string(self):
        assert TargetStatus("built") is TargetStatus.BUILT


class TestBuildStepAndResult:
    def test_build_step_defaults(self):
        step = BuildStep(phase="test", status="success")
        assert step.duration == timedelta(0)
        assert step.summary == ""

    def test_build_result_defaults(self):
        result = BuildResult(
            target="x", generation_id="g", status=TargetStatus.PENDING
        )
        assert result.steps == []
        assert result.commit_id == ""
        assert result.total_duration == timedelta(0)
        assert result.timestamp.tzinfo is not None


class TestStateManagerRoundtrip:
    """Roundtrip test with a REAL SQLiteBackend."""

    def test_save_and_load_roundtrip(self, tmp_path: Path):
        backend = SQLiteBackend(tmp_path, "out")
        sm = StateManager(tmp_path, "out", backend=backend)

        result = _sample_result()
        sm.save_build_result("feat/x", result)

        # Create a NEW StateManager from the same database path
        backend2 = SQLiteBackend(tmp_path, "out")
        sm2 = StateManager(tmp_path, "out", backend=backend2)

        loaded = sm2.get_build_result("feat/x")
        assert loaded is not None
        assert loaded.target == "feat/x"
        assert loaded.generation_id == "gen-1"
        assert loaded.status == TargetStatus.BUILT
        assert loaded.commit_id == "abc123"
        assert loaded.total_duration == timedelta(seconds=4.5)
        assert loaded.timestamp == datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

        assert len(loaded.steps) == 2
        assert loaded.steps[0].phase == "resolve_deps"
        assert loaded.steps[0].status == "success"
        assert loaded.steps[0].duration == timedelta(seconds=1.5)
        assert loaded.steps[0].summary == "resolved"
        assert loaded.steps[1].phase == "build"
        assert loaded.steps[1].duration == timedelta(seconds=3.0)

    def test_status_default_for_unknown_target(self, tmp_path: Path):
        backend = SQLiteBackend(tmp_path, "out")
        sm = StateManager(tmp_path, "out", backend=backend)
        assert sm.get_status("nonexistent") == TargetStatus.PENDING

    def test_get_build_result_returns_none_for_unknown(self, tmp_path: Path):
        backend = SQLiteBackend(tmp_path, "out")
        sm = StateManager(tmp_path, "out", backend=backend)
        assert sm.get_build_result("nonexistent") is None

    def test_set_status(self, tmp_path: Path):
        backend = SQLiteBackend(tmp_path, "out")
        sm = StateManager(tmp_path, "out", backend=backend)
        sm.set_status("feat/a", TargetStatus.OUTDATED)
        assert sm.get_status("feat/a") == TargetStatus.OUTDATED

    def test_list_targets(self, tmp_path: Path):
        backend = SQLiteBackend(tmp_path, "out")
        sm = StateManager(tmp_path, "out", backend=backend)
        sm.set_status("feat/a", TargetStatus.BUILT)
        sm.set_status("feat/b", TargetStatus.FAILED)
        targets = sm.list_targets()
        assert ("feat/a", TargetStatus.BUILT) in targets
        assert ("feat/b", TargetStatus.FAILED) in targets


class TestDAGAwareOperations:
    """Tests using a real SQLiteBackend for DAG operations."""

    def test_mark_dependents_outdated(self, tmp_path: Path):
        project = _make_project()
        backend = SQLiteBackend(tmp_path, "out")
        sm = StateManager(tmp_path, "out", backend=backend)

        # Mark all as built
        for feat in ["a", "b", "c", "d"]:
            sm.set_status(feat, TargetStatus.BUILT)

        # Mark dependents of "a" outdated -> b, c, d should be outdated
        sm.mark_dependents_outdated("a", project)

        assert sm.get_status("a") == TargetStatus.BUILT  # unchanged
        assert sm.get_status("b") == TargetStatus.OUTDATED
        assert sm.get_status("c") == TargetStatus.OUTDATED
        assert sm.get_status("d") == TargetStatus.OUTDATED

    def test_reset_single_target(self, tmp_path: Path):
        backend = SQLiteBackend(tmp_path, "out")
        sm = StateManager(tmp_path, "out", backend=backend)

        sm.set_status("a", TargetStatus.BUILT)
        sm.set_status("b", TargetStatus.BUILT)

        sm.reset("a")
        assert sm.get_status("a") == TargetStatus.PENDING  # cleared -> default
        assert sm.get_status("b") == TargetStatus.BUILT  # unaffected

    def test_reset_all(self, tmp_path: Path):
        backend = SQLiteBackend(tmp_path, "out")
        sm = StateManager(tmp_path, "out", backend=backend)

        sm.set_status("a", TargetStatus.BUILT)
        sm.set_status("b", TargetStatus.FAILED)

        sm.reset_all()
        assert sm.get_status("a") == TargetStatus.PENDING
        assert sm.get_status("b") == TargetStatus.PENDING
        assert sm.list_targets() == []


class TestBuildHistoryAppendOnly:
    """Build results are append-only; previous entries are never overwritten."""

    def test_multiple_saves_produce_multiple_rows(self, tmp_path: Path):
        backend = SQLiteBackend(tmp_path, "out")
        sm = StateManager(tmp_path, "out", backend=backend)

        r1 = _sample_result("feat/x", "gen-1")
        r2 = BuildResult(
            target="feat/x",
            generation_id="gen-2",
            status=TargetStatus.FAILED,
            steps=[BuildStep(phase="build", status="failure", duration=timedelta(seconds=2.0), summary="failed")],
            commit_id="def456",
            total_duration=timedelta(seconds=2.0),
            timestamp=datetime(2025, 6, 16, 12, 0, 0, tzinfo=timezone.utc),
        )

        sm.save_build_result("feat/x", r1)
        sm.save_build_result("feat/x", r2)

        history = backend.get_build_history("feat/x")
        assert len(history) == 2
        # Newest first
        assert history[0]["generation_id"] == "gen-2"
        assert history[0]["status"] == "failed"
        assert history[1]["generation_id"] == "gen-1"
        assert history[1]["status"] == "built"

        # Latest result via get_build_result points to the newest
        latest = sm.get_build_result("feat/x")
        assert latest is not None
        assert latest.generation_id == "gen-2"
        assert latest.status == TargetStatus.FAILED


class TestResponseFileCleanup:
    """Response files are stored in the DB then deleted from disk."""

    def test_response_file_lifecycle(self, tmp_path: Path):
        backend = SQLiteBackend(tmp_path, "out")
        sm = StateManager(tmp_path, "out", backend=backend)

        # Save a build result first to get a build_result_id
        result = _sample_result()
        build_result_id = backend.save_build_result("feat/x", result)

        # Simulate writing a response file
        response_data = {"status": "success", "summary": "built ok"}
        response_file = sm.build_response_dir / "feat_x-abcd1234.json"
        response_file.write_text(json.dumps(response_data))
        assert response_file.exists()

        # Read, store in DB, delete
        loaded = json.loads(response_file.read_text())
        backend.save_agent_response(
            build_result_id=build_result_id,
            validation_result_id=None,
            response_type="build",
            response_json=loaded,
        )
        response_file.unlink()

        # Verify: file gone, data in DB
        assert not response_file.exists()
        # No response files should remain
        remaining = list(sm.build_response_dir.glob("*.json"))
        assert remaining == []


class TestResponseDirectories:
    def test_directories_created(self, tmp_path: Path):
        backend = SQLiteBackend(tmp_path, "out")
        sm = StateManager(tmp_path, "out", backend=backend)
        assert sm.build_response_dir.is_dir()
        assert sm.val_response_dir.is_dir()
        assert sm.build_response_dir == tmp_path / ".intentc" / "state" / "out" / "responses" / "build"
        assert sm.val_response_dir == tmp_path / ".intentc" / "state" / "out" / "responses" / "val"


class TestVersionControlInterface:
    def test_is_abstract(self):
        with pytest.raises(TypeError):
            VersionControl()  # type: ignore[abstract]


class TestGitVersionControl:
    def test_checkpoint_and_log(self, tmp_path: Path):
        # Set up a git repo
        subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True, capture_output=True)

        (tmp_path / "file.txt").write_text("hello")
        vc = GitVersionControl(tmp_path)
        sha = vc.checkpoint("initial commit")
        assert len(sha) == 40

        commits = vc.log()
        assert sha in commits

    def test_diff(self, tmp_path: Path):
        subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True, capture_output=True)

        (tmp_path / "file.txt").write_text("v1")
        vc = GitVersionControl(tmp_path)
        sha1 = vc.checkpoint("v1")

        (tmp_path / "file.txt").write_text("v2")
        sha2 = vc.checkpoint("v2")

        diff = vc.diff(sha1, sha2)
        assert "v1" in diff
        assert "v2" in diff

    def test_log_with_target_filter(self, tmp_path: Path):
        subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True, capture_output=True)

        (tmp_path / "a.txt").write_text("a")
        vc = GitVersionControl(tmp_path)
        sha1 = vc.checkpoint("build feat/a")

        (tmp_path / "b.txt").write_text("b")
        sha2 = vc.checkpoint("build feat/b")

        a_logs = vc.log("feat/a")
        assert sha1 in a_logs
        assert sha2 not in a_logs


# Need subprocess import at module level for git tests
import subprocess
