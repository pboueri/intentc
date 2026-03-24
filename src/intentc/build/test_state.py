"""Tests for build state management."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from intentc.build.storage.backend import BuildResult, BuildStep, TargetStatus
from intentc.build.storage.sqlite_backend import SQLiteBackend
from intentc.build.state import GitVersionControl, StateManager, VersionControl
from intentc.core.project import FeatureNode, Project
from intentc.core.models import IntentFile, ProjectIntent


def _make_result(
    target: str = "core/foo",
    generation_id: str = "gen-1",
    status: str = "success",
    commit_id: str = "abc123",
    total_duration_secs: float = 1.5,
    timestamp: str | None = None,
    steps: list[BuildStep] | None = None,
) -> BuildResult:
    return BuildResult(
        target=target,
        generation_id=generation_id,
        status=status,
        commit_id=commit_id,
        total_duration_secs=total_duration_secs,
        timestamp=timestamp or datetime.now(timezone.utc).isoformat(),
        steps=steps or [],
    )


def _make_project() -> Project:
    """Create a small project DAG: A -> B -> C (C depends on B depends on A)."""
    return Project(
        project_intent=ProjectIntent(name="test", body="test project"),
        features={
            "core/a": FeatureNode(path="core/a"),
            "core/b": FeatureNode(
                path="core/b",
                intents=[IntentFile(name="b", depends_on=["core/a"], body="")],
            ),
            "core/c": FeatureNode(
                path="core/c",
                intents=[IntentFile(name="c", depends_on=["core/b"], body="")],
            ),
        },
    )


class TestTargetStatus:
    def test_enum_values(self):
        assert TargetStatus.PENDING.value == "pending"
        assert TargetStatus.BUILT.value == "built"
        assert TargetStatus.FAILED.value == "failed"
        assert TargetStatus.OUTDATED.value == "outdated"

    def test_string_valued(self):
        assert isinstance(TargetStatus.PENDING, str)
        assert TargetStatus.PENDING == "pending"


class TestBuildStepAndResult:
    def test_build_step_fields(self):
        step = BuildStep(phase="resolve_deps", status="success", duration_secs=0.5, summary="resolved 3 deps")
        assert step.phase == "resolve_deps"
        assert step.status == "success"
        assert step.duration_secs == 0.5
        assert step.summary == "resolved 3 deps"

    def test_build_result_defaults(self):
        result = BuildResult(target="core/foo")
        assert result.target == "core/foo"
        assert result.generation_id == ""
        assert result.status == ""
        assert result.commit_id == ""
        assert result.total_duration_secs == 0.0
        assert result.steps == []

    def test_build_result_with_steps(self):
        steps = [
            BuildStep(phase="build", status="success", duration_secs=1.0, summary="built"),
            BuildStep(phase="validate", status="failure", duration_secs=0.5, summary="failed check"),
        ]
        result = _make_result(steps=steps)
        assert len(result.steps) == 2
        assert result.steps[0].phase == "build"
        assert result.steps[1].status == "failure"


class TestStateManagerRoundtrip:
    """Roundtrip tests using a real SQLiteBackend."""

    def test_save_and_retrieve_build_result(self, tmp_path: Path):
        """Full roundtrip: save via one StateManager, read via a fresh one."""
        steps = [
            BuildStep(phase="resolve_deps", status="success", duration_secs=0.3, summary="deps resolved"),
            BuildStep(phase="build", status="success", duration_secs=1.2, summary="code generated"),
        ]
        ts = datetime.now(timezone.utc).isoformat()
        result = _make_result(
            target="build/state",
            generation_id="gen-abc",
            status="success",
            commit_id="sha-deadbeef",
            total_duration_secs=1.5,
            timestamp=ts,
            steps=steps,
        )

        # Save via first StateManager
        sm1 = StateManager(tmp_path, "src", SQLiteBackend(tmp_path, "src"))
        sm1.save_build_result("build/state", result)

        # Read via a completely new StateManager + new backend instance
        sm2 = StateManager(tmp_path, "src", SQLiteBackend(tmp_path, "src"))
        loaded = sm2.get_build_result("build/state")

        assert loaded is not None
        assert loaded.target == "build/state"
        assert loaded.generation_id == "gen-abc"
        assert loaded.status == "success"
        assert loaded.commit_id == "sha-deadbeef"
        assert loaded.total_duration_secs == 1.5
        assert loaded.timestamp == ts
        assert len(loaded.steps) == 2
        assert loaded.steps[0].phase == "resolve_deps"
        assert loaded.steps[0].status == "success"
        assert loaded.steps[0].duration_secs == pytest.approx(0.3)
        assert loaded.steps[0].summary == "deps resolved"
        assert loaded.steps[1].phase == "build"
        assert loaded.steps[1].duration_secs == pytest.approx(1.2)

    def test_status_roundtrip(self, tmp_path: Path):
        sm = StateManager(tmp_path, "src", SQLiteBackend(tmp_path, "src"))

        # Unknown target returns PENDING
        assert sm.get_status("unknown/target") == TargetStatus.PENDING

        # After saving a successful result, status is BUILT
        result = _make_result(target="core/foo", status="success")
        sm.save_build_result("core/foo", result)
        assert sm.get_status("core/foo") == TargetStatus.BUILT

        # After saving a failed result, status is FAILED
        result2 = _make_result(target="core/foo", status="failed")
        sm.save_build_result("core/foo", result2)
        assert sm.get_status("core/foo") == TargetStatus.FAILED

    def test_missing_target_returns_none(self, tmp_path: Path):
        sm = StateManager(tmp_path, "src", SQLiteBackend(tmp_path, "src"))
        assert sm.get_build_result("nonexistent") is None

    def test_set_status(self, tmp_path: Path):
        sm = StateManager(tmp_path, "src", SQLiteBackend(tmp_path, "src"))
        sm.set_status("core/foo", TargetStatus.OUTDATED)
        assert sm.get_status("core/foo") == TargetStatus.OUTDATED

    def test_list_targets(self, tmp_path: Path):
        sm = StateManager(tmp_path, "src", SQLiteBackend(tmp_path, "src"))
        sm.set_status("core/a", TargetStatus.BUILT)
        sm.set_status("core/b", TargetStatus.FAILED)
        targets = sm.list_targets()
        assert ("core/a", TargetStatus.BUILT) in targets
        assert ("core/b", TargetStatus.FAILED) in targets


class TestDAGAwareOperations:
    """Tests for mark_dependents_outdated and reset using real SQLiteBackend."""

    def test_mark_dependents_outdated(self, tmp_path: Path):
        project = _make_project()
        sm = StateManager(tmp_path, "src", SQLiteBackend(tmp_path, "src"))

        # Set all to BUILT
        for feat in ["core/a", "core/b", "core/c"]:
            sm.set_status(feat, TargetStatus.BUILT)

        # Mark dependents of A as outdated
        sm.mark_dependents_outdated("core/a", project)

        # A stays BUILT, B and C are OUTDATED
        assert sm.get_status("core/a") == TargetStatus.BUILT
        assert sm.get_status("core/b") == TargetStatus.OUTDATED
        assert sm.get_status("core/c") == TargetStatus.OUTDATED

    def test_mark_dependents_leaf_node(self, tmp_path: Path):
        project = _make_project()
        sm = StateManager(tmp_path, "src", SQLiteBackend(tmp_path, "src"))

        for feat in ["core/a", "core/b", "core/c"]:
            sm.set_status(feat, TargetStatus.BUILT)

        # C is a leaf — no dependents
        sm.mark_dependents_outdated("core/c", project)
        assert sm.get_status("core/a") == TargetStatus.BUILT
        assert sm.get_status("core/b") == TargetStatus.BUILT
        assert sm.get_status("core/c") == TargetStatus.BUILT

    def test_reset_single_target(self, tmp_path: Path):
        sm = StateManager(tmp_path, "src", SQLiteBackend(tmp_path, "src"))
        sm.set_status("core/a", TargetStatus.BUILT)
        sm.set_status("core/b", TargetStatus.BUILT)

        sm.reset("core/a")
        assert sm.get_status("core/a") == TargetStatus.PENDING
        assert sm.get_status("core/b") == TargetStatus.BUILT

    def test_reset_all(self, tmp_path: Path):
        sm = StateManager(tmp_path, "src", SQLiteBackend(tmp_path, "src"))
        sm.set_status("core/a", TargetStatus.BUILT)
        sm.set_status("core/b", TargetStatus.FAILED)

        sm.reset_all()
        assert sm.get_status("core/a") == TargetStatus.PENDING
        assert sm.get_status("core/b") == TargetStatus.PENDING


class TestBuildHistoryAppendOnly:
    """Verify build results are append-only."""

    def test_multiple_results_produce_multiple_rows(self, tmp_path: Path):
        backend = SQLiteBackend(tmp_path, "src")
        sm = StateManager(tmp_path, "src", backend)

        r1 = _make_result(target="core/foo", generation_id="gen-1", status="success", commit_id="sha1")
        r2 = _make_result(target="core/foo", generation_id="gen-2", status="failed", commit_id="sha2")
        r3 = _make_result(target="core/foo", generation_id="gen-3", status="success", commit_id="sha3")

        sm.save_build_result("core/foo", r1)
        sm.save_build_result("core/foo", r2)
        sm.save_build_result("core/foo", r3)

        # All three rows exist in history
        history = backend.get_build_history("core/foo")
        assert len(history) == 3

        # Most recent first
        assert history[0].generation_id == "gen-3"
        assert history[1].generation_id == "gen-2"
        assert history[2].generation_id == "gen-1"

        # Each has correct status
        assert history[0].status == "success"
        assert history[1].status == "failed"
        assert history[2].status == "success"

    def test_latest_result_is_returned_by_get_build_result(self, tmp_path: Path):
        backend = SQLiteBackend(tmp_path, "src")
        sm = StateManager(tmp_path, "src", backend)

        r1 = _make_result(target="core/foo", generation_id="gen-1", status="success")
        r2 = _make_result(target="core/foo", generation_id="gen-2", status="failed")

        sm.save_build_result("core/foo", r1)
        sm.save_build_result("core/foo", r2)

        latest = sm.get_build_result("core/foo")
        assert latest is not None
        assert latest.generation_id == "gen-2"
        assert latest.status == "failed"


class TestResponseFileCleanup:
    """Verify response files are stored in DB and deleted from disk."""

    def test_response_file_lifecycle(self, tmp_path: Path):
        backend = SQLiteBackend(tmp_path, "src")
        sm = StateManager(tmp_path, "src", backend)

        # Create a response file in the build response dir
        response_dir = sm.build_response_dir
        response_data = {"status": "success", "summary": "built successfully", "files_created": ["foo.py"]}
        hex_id = uuid.uuid4().hex[:8]
        response_file = response_dir / f"core_foo-{hex_id}.json"
        response_file.write_text(json.dumps(response_data))
        assert response_file.exists()

        # Save a build result
        result = _make_result(target="core/foo")
        br_id = sm.save_build_result("core/foo", result)

        # Read the response file, store in DB, then delete
        stored_data = json.loads(response_file.read_text())
        backend.save_agent_response(
            build_result_id=br_id,
            validation_result_id=None,
            response_type="build",
            response_json=stored_data,
        )
        response_file.unlink()

        # File is gone from disk
        assert not response_file.exists()

        # But stored in the database — verify via raw query
        row = backend._conn.execute(
            "SELECT response_json FROM agent_responses WHERE build_result_id = ?",
            (br_id,),
        ).fetchone()
        assert row is not None
        assert json.loads(row[0]) == response_data

    def test_val_response_dir_exists(self, tmp_path: Path):
        sm = StateManager(tmp_path, "src", SQLiteBackend(tmp_path, "src"))
        val_dir = sm.val_response_dir
        assert val_dir.exists()
        assert "responses/val" in str(val_dir)

    def test_build_response_dir_path(self, tmp_path: Path):
        sm = StateManager(tmp_path, "src", SQLiteBackend(tmp_path, "src"))
        build_dir = sm.build_response_dir
        assert build_dir.exists()
        assert "responses/build" in str(build_dir)


class TestVersionControlInterface:
    def test_abstract_methods(self):
        """VersionControl cannot be instantiated directly."""
        with pytest.raises(TypeError):
            VersionControl()  # type: ignore[abstract]


class TestGitVersionControl:
    """Basic tests for GitVersionControl using a real git repo."""

    def _init_repo(self, path: Path) -> None:
        import subprocess
        subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=path, capture_output=True, check=True)

    def test_checkpoint_and_log(self, tmp_path: Path):
        self._init_repo(tmp_path)
        (tmp_path / "file.txt").write_text("hello")
        vc = GitVersionControl(tmp_path)

        sha = vc.checkpoint("initial commit")
        assert len(sha) == 40

        commits = vc.log()
        assert sha in commits

    def test_checkpoint_and_diff(self, tmp_path: Path):
        self._init_repo(tmp_path)
        (tmp_path / "file.txt").write_text("v1")
        vc = GitVersionControl(tmp_path)

        sha1 = vc.checkpoint("version 1")
        (tmp_path / "file.txt").write_text("v2")
        sha2 = vc.checkpoint("version 2")

        diff_output = vc.diff(sha1, sha2)
        assert "v1" in diff_output or "v2" in diff_output

    def test_log_filtered_by_target(self, tmp_path: Path):
        self._init_repo(tmp_path)
        vc = GitVersionControl(tmp_path)

        (tmp_path / "a.txt").write_text("a")
        vc.checkpoint("build core/a")

        (tmp_path / "b.txt").write_text("b")
        vc.checkpoint("build core/b")

        a_commits = vc.log("core/a")
        b_commits = vc.log("core/b")
        assert len(a_commits) == 1
        assert len(b_commits) == 1
        assert a_commits[0] != b_commits[0]
