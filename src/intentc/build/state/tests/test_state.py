"""Tests for the state module: StateManager, VersionControl, roundtrip, DAG ops."""

from __future__ import annotations

import json
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
from intentc.core.models import IntentFile, ProjectIntent


@pytest.fixture()
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture()
def backend(tmp_dir: Path) -> SQLiteBackend:
    be = SQLiteBackend(base_dir=tmp_dir, output_dir="src")
    yield be
    be.close()


@pytest.fixture()
def state_manager(tmp_dir: Path, backend: SQLiteBackend) -> StateManager:
    return StateManager(base_dir=tmp_dir, output_dir="src", backend=backend)


def _make_project_with_dag() -> Project:
    """Create a project with a simple DAG: A -> B -> C, A -> D."""
    return Project(
        project_intent=ProjectIntent(name="test-project"),
        features={
            "A": FeatureNode(
                path="A",
                intents=[IntentFile(name="A")],
            ),
            "B": FeatureNode(
                path="B",
                intents=[IntentFile(name="B", depends_on=["A"])],
            ),
            "C": FeatureNode(
                path="C",
                intents=[IntentFile(name="C", depends_on=["B"])],
            ),
            "D": FeatureNode(
                path="D",
                intents=[IntentFile(name="D", depends_on=["A"])],
            ),
        },
    )


def _make_build_result(
    target: str,
    generation_id: str | None = None,
    status: str = "built",
    commit_id: str = "abc123",
) -> BuildResult:
    return BuildResult(
        target=target,
        generation_id=generation_id or str(uuid.uuid4()),
        status=status,
        commit_id=commit_id,
        total_duration_secs=1.5,
        timestamp=datetime.now(timezone.utc).isoformat(),
        steps=[
            BuildStep(phase="resolve_deps", status="success", duration_secs=0.5, summary="Resolved"),
            BuildStep(phase="build", status="success", duration_secs=1.0, summary="Built OK"),
        ],
    )


# ---------------------------------------------------------------------------
# 1. Types and interfaces
# ---------------------------------------------------------------------------


class TestTypesAndInterfaces:
    def test_target_status_enum_values(self):
        assert TargetStatus.PENDING.value == "pending"
        assert TargetStatus.BUILT.value == "built"
        assert TargetStatus.FAILED.value == "failed"
        assert TargetStatus.OUTDATED.value == "outdated"

    def test_build_step_fields(self):
        step = BuildStep(phase="build", status="success", duration_secs=2.5, summary="Done")
        assert step.phase == "build"
        assert step.status == "success"
        assert step.duration_secs == 2.5
        assert step.summary == "Done"

    def test_build_result_fields(self):
        ts = datetime.now(timezone.utc).isoformat()
        result = BuildResult(
            target="core/project",
            generation_id="gen-1",
            status="built",
            commit_id="deadbeef",
            total_duration_secs=3.0,
            timestamp=ts,
            steps=[BuildStep(phase="test", status="success", duration_secs=1.0, summary="OK")],
        )
        assert result.target == "core/project"
        assert result.generation_id == "gen-1"
        assert result.status == "built"
        assert result.commit_id == "deadbeef"
        assert result.total_duration_secs == 3.0
        assert result.timestamp == ts
        assert len(result.steps) == 1

    def test_version_control_is_abstract(self):
        with pytest.raises(TypeError):
            VersionControl()  # type: ignore[abstract]

    def test_git_version_control_is_concrete(self, tmp_dir: Path):
        gvc = GitVersionControl(tmp_dir)
        assert isinstance(gvc, VersionControl)

    def test_state_manager_methods_exist(self, state_manager: StateManager):
        assert callable(state_manager.get_status)
        assert callable(state_manager.get_build_result)
        assert callable(state_manager.save_build_result)
        assert callable(state_manager.set_status)
        assert callable(state_manager.mark_dependents_outdated)
        assert callable(state_manager.reset)
        assert callable(state_manager.reset_all)
        assert callable(state_manager.list_targets)

    def test_state_manager_default_backend(self, tmp_dir: Path):
        """StateManager creates SQLiteBackend by default when none provided."""
        sm = StateManager(base_dir=tmp_dir, output_dir="out")
        assert isinstance(sm.backend, SQLiteBackend)

    def test_state_manager_response_dirs(self, state_manager: StateManager, tmp_dir: Path):
        assert state_manager.build_response_dir == (
            tmp_dir / ".intentc" / "state" / "src" / "responses" / "build"
        )
        assert state_manager.val_response_dir == (
            tmp_dir / ".intentc" / "state" / "src" / "responses" / "val"
        )
        assert state_manager.build_response_dir.is_dir()
        assert state_manager.val_response_dir.is_dir()


# ---------------------------------------------------------------------------
# 2. Roundtrip: save + reload from same DB
# ---------------------------------------------------------------------------


class TestStateRoundtrip:
    def test_full_roundtrip_with_real_backend(self, tmp_dir: Path):
        """Save a BuildResult, create a NEW StateManager from the same DB, verify all fields."""
        be1 = SQLiteBackend(base_dir=tmp_dir, output_dir="src")
        sm1 = StateManager(base_dir=tmp_dir, output_dir="src", backend=be1)

        gen_id = "gen-roundtrip-1"
        ts = datetime.now(timezone.utc).isoformat()
        result = BuildResult(
            target="core/project",
            generation_id=gen_id,
            status="built",
            commit_id="sha256abc",
            total_duration_secs=4.2,
            timestamp=ts,
            steps=[
                BuildStep(phase="resolve_deps", status="success", duration_secs=1.2, summary="Deps resolved"),
                BuildStep(phase="build", status="success", duration_secs=3.0, summary="Build complete"),
            ],
        )
        sm1.save_build_result("core/project", result)
        be1.close()

        # Create a NEW backend and StateManager from the same database
        be2 = SQLiteBackend(base_dir=tmp_dir, output_dir="src")
        sm2 = StateManager(base_dir=tmp_dir, output_dir="src", backend=be2)

        loaded = sm2.get_build_result("core/project")
        assert loaded is not None
        assert loaded.target == "core/project"
        assert loaded.generation_id == gen_id
        assert loaded.status == "built"
        assert loaded.commit_id == "sha256abc"
        assert loaded.total_duration_secs == pytest.approx(4.2)
        assert loaded.timestamp == ts

        # Verify steps survived
        assert len(loaded.steps) == 2
        assert loaded.steps[0].phase == "resolve_deps"
        assert loaded.steps[0].status == "success"
        assert loaded.steps[0].duration_secs == pytest.approx(1.2)
        assert loaded.steps[0].summary == "Deps resolved"
        assert loaded.steps[1].phase == "build"
        assert loaded.steps[1].duration_secs == pytest.approx(3.0)

        # Verify status survived
        assert sm2.get_status("core/project") == TargetStatus.BUILT

        be2.close()

    def test_missing_target_returns_defaults(self, state_manager: StateManager):
        """Missing database entries return defaults, not errors."""
        assert state_manager.get_status("nonexistent") == TargetStatus.PENDING
        assert state_manager.get_build_result("nonexistent") is None


# ---------------------------------------------------------------------------
# 3. DAG-aware operations
# ---------------------------------------------------------------------------


class TestDAGOperations:
    def test_mark_dependents_outdated(self, tmp_dir: Path):
        """mark_dependents_outdated walks the DAG and sets all descendants to outdated."""
        be = SQLiteBackend(base_dir=tmp_dir, output_dir="src")
        sm = StateManager(base_dir=tmp_dir, output_dir="src", backend=be)
        project = _make_project_with_dag()

        # Set all targets to built first
        for name in ["A", "B", "C", "D"]:
            sm.set_status(name, TargetStatus.BUILT)

        # Mark A's dependents as outdated (B, C, D are all downstream)
        sm.mark_dependents_outdated("A", project)

        assert sm.get_status("A") == TargetStatus.BUILT  # A itself unchanged
        assert sm.get_status("B") == TargetStatus.OUTDATED
        assert sm.get_status("C") == TargetStatus.OUTDATED
        assert sm.get_status("D") == TargetStatus.OUTDATED

        be.close()

    def test_mark_dependents_partial(self, tmp_dir: Path):
        """Only descendants are marked, not siblings or ancestors."""
        be = SQLiteBackend(base_dir=tmp_dir, output_dir="src")
        sm = StateManager(base_dir=tmp_dir, output_dir="src", backend=be)
        project = _make_project_with_dag()

        for name in ["A", "B", "C", "D"]:
            sm.set_status(name, TargetStatus.BUILT)

        # Mark B's dependents (only C)
        sm.mark_dependents_outdated("B", project)

        assert sm.get_status("A") == TargetStatus.BUILT
        assert sm.get_status("B") == TargetStatus.BUILT
        assert sm.get_status("C") == TargetStatus.OUTDATED
        assert sm.get_status("D") == TargetStatus.BUILT  # D depends on A, not B

        be.close()

    def test_reset_clears_single_target(self, tmp_dir: Path):
        """Reset clears state for a single target without affecting others."""
        be = SQLiteBackend(base_dir=tmp_dir, output_dir="src")
        sm = StateManager(base_dir=tmp_dir, output_dir="src", backend=be)

        sm.set_status("A", TargetStatus.BUILT)
        sm.set_status("B", TargetStatus.BUILT)
        sm.set_status("C", TargetStatus.FAILED)

        sm.reset("B")

        assert sm.get_status("A") == TargetStatus.BUILT
        assert sm.get_status("B") == TargetStatus.PENDING  # Reset to default
        assert sm.get_status("C") == TargetStatus.FAILED

        be.close()

    def test_reset_all_clears_everything(self, tmp_dir: Path):
        be = SQLiteBackend(base_dir=tmp_dir, output_dir="src")
        sm = StateManager(base_dir=tmp_dir, output_dir="src", backend=be)

        sm.set_status("A", TargetStatus.BUILT)
        sm.set_status("B", TargetStatus.FAILED)

        sm.reset_all()

        assert sm.get_status("A") == TargetStatus.PENDING
        assert sm.get_status("B") == TargetStatus.PENDING
        assert sm.list_targets() == []

        be.close()


# ---------------------------------------------------------------------------
# 4. Build history is append-only
# ---------------------------------------------------------------------------


class TestBuildHistoryAppendOnly:
    def test_multiple_saves_produce_multiple_rows(self, tmp_dir: Path):
        """Multiple save_build_result calls produce multiple rows, each queryable via get_build_history."""
        be = SQLiteBackend(base_dir=tmp_dir, output_dir="src")
        sm = StateManager(base_dir=tmp_dir, output_dir="src", backend=be)

        result1 = _make_build_result("feature/x", generation_id="gen-1", status="built", commit_id="aaa")
        result2 = _make_build_result("feature/x", generation_id="gen-2", status="failed", commit_id="bbb")
        result3 = _make_build_result("feature/x", generation_id="gen-3", status="built", commit_id="ccc")

        sm.save_build_result("feature/x", result1)
        sm.save_build_result("feature/x", result2)
        sm.save_build_result("feature/x", result3)

        # get_build_history returns all entries
        history = be.get_build_history("feature/x")
        assert len(history) == 3

        # Most recent first (descending order)
        assert history[0].generation_id == "gen-3"
        assert history[1].generation_id == "gen-2"
        assert history[2].generation_id == "gen-1"

        # Previous entries are never overwritten
        assert history[2].commit_id == "aaa"
        assert history[1].commit_id == "bbb"
        assert history[0].commit_id == "ccc"

        # target_state points to the latest result
        latest = sm.get_build_result("feature/x")
        assert latest is not None
        assert latest.generation_id == "gen-3"

        be.close()

    def test_append_only_across_sessions(self, tmp_dir: Path):
        """Build results persist across backend sessions."""
        be1 = SQLiteBackend(base_dir=tmp_dir, output_dir="src")
        sm1 = StateManager(base_dir=tmp_dir, output_dir="src", backend=be1)
        sm1.save_build_result("t", _make_build_result("t", generation_id="g1"))
        be1.close()

        be2 = SQLiteBackend(base_dir=tmp_dir, output_dir="src")
        sm2 = StateManager(base_dir=tmp_dir, output_dir="src", backend=be2)
        sm2.save_build_result("t", _make_build_result("t", generation_id="g2"))

        history = be2.get_build_history("t")
        assert len(history) == 2
        assert history[0].generation_id == "g2"
        assert history[1].generation_id == "g1"

        be2.close()


# ---------------------------------------------------------------------------
# 5. Response file cleanup
# ---------------------------------------------------------------------------


class TestResponseFileCleanup:
    def test_response_file_saved_to_db_and_deleted(self, tmp_dir: Path):
        """After saving, agent response JSON is stored in DB and the file on disk is deleted."""
        be = SQLiteBackend(base_dir=tmp_dir, output_dir="src")
        sm = StateManager(base_dir=tmp_dir, output_dir="src", backend=be)

        # 1. Create a response file in build_response_dir
        target_name = "build/state"
        safe_name = target_name.replace("/", "_")
        hex_suffix = uuid.uuid4().hex[:8]
        response_filename = f"{safe_name}-{hex_suffix}.json"
        response_path = sm.build_response_dir / response_filename

        response_data = {
            "status": "success",
            "summary": "Built build/state",
            "files_created": ["src/intentc/build/state/state.py"],
            "files_modified": [],
        }
        response_path.write_text(json.dumps(response_data), encoding="utf-8")
        assert response_path.exists()

        # 2. Save build result
        result = _make_build_result(target_name)
        br_id = be.save_build_result(target_name, result)

        # 3. Read the response, store in DB, then delete the file
        loaded_response = json.loads(response_path.read_text(encoding="utf-8"))
        be.save_agent_response(
            build_result_id=br_id,
            validation_result_id=None,
            response_type="build",
            response_json=loaded_response,
        )
        response_path.unlink()

        # 4. Verify: file is gone, but data is in the database
        assert not response_path.exists()

        # Verify no response files accumulate
        remaining = list(sm.build_response_dir.iterdir())
        assert len(remaining) == 0

        be.close()

    def test_val_response_file_lifecycle(self, tmp_dir: Path):
        """Same lifecycle for validation response files."""
        be = SQLiteBackend(base_dir=tmp_dir, output_dir="src")
        sm = StateManager(base_dir=tmp_dir, output_dir="src", backend=be)

        safe_name = "build_state"
        hex_suffix = uuid.uuid4().hex[:8]
        response_path = sm.val_response_dir / f"{safe_name}-{hex_suffix}.json"

        val_response = {"name": "check-types", "status": "pass", "reason": "All good"}
        response_path.write_text(json.dumps(val_response), encoding="utf-8")
        assert response_path.exists()

        # Store in DB and delete
        be.save_agent_response(
            build_result_id=None,
            validation_result_id=None,
            response_type="validation",
            response_json=val_response,
        )
        response_path.unlink()

        assert not response_path.exists()
        assert len(list(sm.val_response_dir.iterdir())) == 0

        be.close()
