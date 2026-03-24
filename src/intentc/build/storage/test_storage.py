"""Tests for the storage backend."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from intentc.build.storage import (
    BuildResult,
    BuildStep,
    GenerationStatus,
    SQLiteBackend,
    StorageBackend,
    TargetStatus,
)


@pytest.fixture()
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture()
def backend(tmp_dir: Path) -> SQLiteBackend:
    b = SQLiteBackend(base_dir=tmp_dir, output_dir="out")
    yield b
    b.close()


# --- Schema creation ---


class TestSchemaCreation:
    def test_creates_database_file(self, tmp_dir: Path):
        b = SQLiteBackend(base_dir=tmp_dir, output_dir="out")
        db_path = tmp_dir / ".intentc" / "state" / "out" / "intentc.db"
        assert db_path.exists()
        b.close()

    def test_creates_all_tables(self, backend: SQLiteBackend):
        rows = backend._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = sorted(
            r[0] for r in rows if not r[0].startswith("sqlite_")
        )
        expected = sorted([
            "agent_responses",
            "build_results",
            "build_steps",
            "generation_logs",
            "generations",
            "intent_file_versions",
            "target_state",
            "validation_file_versions",
            "validation_results",
        ])
        assert table_names == expected

    def test_wal_mode_enabled(self, backend: SQLiteBackend):
        mode = backend._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"

    def test_foreign_keys_enabled(self, backend: SQLiteBackend):
        fk = backend._conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1

    def test_is_storage_backend(self, backend: SQLiteBackend):
        assert isinstance(backend, StorageBackend)


# --- Full roundtrip ---


class TestStorageRoundtrip:
    def test_full_roundtrip(self, backend: SQLiteBackend):
        # 1. Create a generation
        backend.create_generation(
            generation_id="gen-001",
            output_dir="out",
            profile_name="default",
            options={"retries": 3},
        )

        # 2. Record an intent file version
        iv_id = backend.record_intent_version(
            name="feature/auth",
            source_path="intent/feature/auth.ic",
            content_hash="abc123",
        )
        assert isinstance(iv_id, int)

        # 3. Save a build result with steps
        result = BuildResult(
            target="feature/auth",
            generation_id="gen-001",
            status="success",
            commit_id="deadbeef",
            total_duration_secs=12.5,
            timestamp="2026-01-01T00:00:00Z",
            steps=[
                BuildStep(phase="resolve_deps", status="success", duration_secs=1.0, summary="deps ok"),
                BuildStep(phase="build", status="success", duration_secs=10.0, summary="built"),
                BuildStep(phase="validate", status="success", duration_secs=1.5, summary="valid"),
            ],
        )
        br_id = backend.save_build_result(
            target="feature/auth",
            result=result,
            intent_version_id=iv_id,
            git_diff="diff --git a/auth.py",
            files_created=["auth.py"],
            files_modified=["__init__.py"],
        )
        assert isinstance(br_id, int)

        # 4. Save an agent response
        backend.save_agent_response(
            build_result_id=br_id,
            validation_result_id=None,
            response_type="build",
            response_json={"status": "success", "summary": "built auth"},
        )

        # 5. Save validation results
        vv_id = backend.record_validation_version(
            target="feature/auth",
            source_path="intent/feature/auth.icv",
            content_hash="val456",
        )
        vr_id = backend.save_validation_result(
            build_result_id=br_id,
            generation_id="gen-001",
            target="feature/auth",
            validation_file_version_id=vv_id,
            name="auth-check",
            type="command_check",
            severity="error",
            status="pass",
            reason="all checks passed",
            duration_secs=2.0,
        )
        assert isinstance(vr_id, int)

        # 6. Log generation events
        backend.log_generation_event("gen-001", "Starting build")
        backend.log_generation_event("gen-001", "Build complete")

        # 7. Complete the generation
        backend.complete_generation("gen-001", GenerationStatus.COMPLETED)

        # 8. Read back and verify
        gen = backend.get_generation("gen-001")
        assert gen is not None
        assert gen["status"] == "completed"
        assert gen["profile_name"] == "default"
        assert gen["options"] == {"retries": 3}
        assert len(gen["logs"]) == 2
        assert gen["logs"][0]["message"] == "Starting build"
        assert gen["completed_at"] is not None

        loaded = backend.get_build_result("feature/auth")
        assert loaded is not None
        assert loaded.target == "feature/auth"
        assert loaded.status == "success"
        assert loaded.commit_id == "deadbeef"
        assert loaded.total_duration_secs == 12.5
        assert loaded.git_diff == "diff --git a/auth.py"
        assert loaded.files_created == ["auth.py"]
        assert loaded.files_modified == ["__init__.py"]
        assert len(loaded.steps) == 3
        assert loaded.steps[0].phase == "resolve_deps"
        assert loaded.steps[1].summary == "built"

    def test_idempotent_intent_version(self, backend: SQLiteBackend):
        id1 = backend.record_intent_version("f", "p", "hash1")
        id2 = backend.record_intent_version("f", "p", "hash1")
        assert id1 == id2

    def test_idempotent_validation_version(self, backend: SQLiteBackend):
        id1 = backend.record_validation_version("t", "p", "hash1")
        id2 = backend.record_validation_version("t", "p", "hash1")
        assert id1 == id2

    def test_build_history(self, backend: SQLiteBackend):
        backend.create_generation("gen-h", "out")
        for i in range(3):
            r = BuildResult(
                target="feat",
                generation_id="gen-h",
                status="success",
                timestamp=f"2026-01-0{i+1}T00:00:00Z",
            )
            backend.save_build_result("feat", r)
        history = backend.get_build_history("feat", limit=10)
        assert len(history) == 3
        # newest first
        assert history[0].timestamp == "2026-01-03T00:00:00Z"

    def test_get_build_result_returns_none_for_unknown(self, backend: SQLiteBackend):
        assert backend.get_build_result("nonexistent") is None

    def test_get_generation_returns_none_for_unknown(self, backend: SQLiteBackend):
        assert backend.get_generation("nonexistent") is None

    def test_save_build_step_standalone(self, backend: SQLiteBackend):
        backend.create_generation("gen-s", "out")
        r = BuildResult(
            target="feat",
            generation_id="gen-s",
            status="success",
            timestamp="2026-01-01T00:00:00Z",
        )
        br_id = backend.save_build_result("feat", r)
        step = BuildStep(phase="checkpoint", status="success", duration_secs=0.5, summary="saved")
        backend.save_build_step(br_id, step, log="checkpoint log output", step_order=10)
        rows = backend._conn.execute(
            "SELECT log FROM build_steps WHERE build_result_id = ? AND step_order = 10",
            (br_id,),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "checkpoint log output"

    def test_save_agent_response_for_validation(self, backend: SQLiteBackend):
        backend.create_generation("gen-v", "out")
        vr_id = backend.save_validation_result(
            build_result_id=None,
            generation_id="gen-v",
            target="feat",
            validation_file_version_id=None,
            name="check",
            type="command_check",
            severity="error",
            status="pass",
        )
        backend.save_agent_response(
            build_result_id=None,
            validation_result_id=vr_id,
            response_type="validation",
            response_json={"passed": True},
        )
        row = backend._conn.execute(
            "SELECT response_json FROM agent_responses WHERE validation_result_id = ?",
            (vr_id,),
        ).fetchone()
        assert json.loads(row[0]) == {"passed": True}


# --- Target state management ---


class TestTargetState:
    def test_pending_for_unknown(self, backend: SQLiteBackend):
        assert backend.get_status("unknown") == TargetStatus.PENDING

    def test_set_and_get(self, backend: SQLiteBackend):
        backend.set_status("feat/a", TargetStatus.BUILT)
        assert backend.get_status("feat/a") == TargetStatus.BUILT

    def test_set_overwrites(self, backend: SQLiteBackend):
        backend.set_status("feat/a", TargetStatus.BUILDING)
        backend.set_status("feat/a", TargetStatus.FAILED)
        assert backend.get_status("feat/a") == TargetStatus.FAILED

    def test_list_targets(self, backend: SQLiteBackend):
        backend.set_status("feat/a", TargetStatus.BUILT)
        backend.set_status("feat/b", TargetStatus.FAILED)
        targets = backend.list_targets()
        assert len(targets) == 2
        names = {t[0] for t in targets}
        assert names == {"feat/a", "feat/b"}

    def test_reset_single(self, backend: SQLiteBackend):
        backend.set_status("feat/a", TargetStatus.BUILT)
        backend.set_status("feat/b", TargetStatus.BUILT)
        backend.reset("feat/a")
        assert backend.get_status("feat/a") == TargetStatus.PENDING
        assert backend.get_status("feat/b") == TargetStatus.BUILT

    def test_reset_all(self, backend: SQLiteBackend):
        backend.set_status("feat/a", TargetStatus.BUILT)
        backend.set_status("feat/b", TargetStatus.FAILED)
        backend.reset_all()
        assert backend.list_targets() == []
        assert backend.get_status("feat/a") == TargetStatus.PENDING


# --- Migration from flat files ---


class TestMigration:
    def test_migrates_state_json(self, tmp_dir: Path):
        state_dir = tmp_dir / ".intentc" / "state" / "out"
        state_dir.mkdir(parents=True)
        state_file = state_dir / "state.json"
        state_file.write_text(json.dumps({
            "feat/a": {"status": "built"},
            "feat/b": {"status": "failed"},
        }))

        b = SQLiteBackend(base_dir=tmp_dir, output_dir="out")
        # state.json renamed
        assert not state_file.exists()
        assert (state_dir / "state.json.migrated").exists()
        # data migrated
        assert b.get_status("feat/a") == TargetStatus.BUILT
        assert b.get_status("feat/b") == TargetStatus.FAILED
        b.close()

    def test_migration_idempotent(self, tmp_dir: Path):
        state_dir = tmp_dir / ".intentc" / "state" / "out"
        state_dir.mkdir(parents=True)
        state_file = state_dir / "state.json"
        state_file.write_text(json.dumps({"feat/a": {"status": "built"}}))

        b1 = SQLiteBackend(base_dir=tmp_dir, output_dir="out")
        b1.close()
        # Opening again should not fail (marker exists, no state.json)
        b2 = SQLiteBackend(base_dir=tmp_dir, output_dir="out")
        assert b2.get_status("feat/a") == TargetStatus.BUILT
        b2.close()

    def test_migrates_build_log(self, tmp_dir: Path):
        state_dir = tmp_dir / ".intentc" / "state" / "out"
        state_dir.mkdir(parents=True)
        state_file = state_dir / "state.json"
        state_file.write_text(json.dumps({"feat/a": "built"}))
        log_file = state_dir / "build-log.jsonl"
        log_file.write_text(
            json.dumps({
                "target": "feat/a",
                "status": "success",
                "timestamp": "2026-01-01T00:00:00Z",
                "steps": [
                    {"phase": "build", "status": "success", "duration_secs": 5.0, "summary": "ok"},
                ],
            }) + "\n"
        )

        b = SQLiteBackend(base_dir=tmp_dir, output_dir="out")
        rows = b._conn.execute(
            "SELECT * FROM build_results WHERE target = 'feat/a'"
        ).fetchall()
        assert len(rows) >= 1
        step_rows = b._conn.execute("SELECT * FROM build_steps").fetchall()
        assert len(step_rows) >= 1
        b.close()

    def test_no_migration_without_state_json(self, tmp_dir: Path):
        """If no state.json exists, no migration occurs and no errors."""
        b = SQLiteBackend(base_dir=tmp_dir, output_dir="out")
        assert b.list_targets() == []
        b.close()


# --- Interface checks ---


class TestInterface:
    def test_generation_status_values(self):
        assert GenerationStatus.RUNNING.value == "running"
        assert GenerationStatus.COMPLETED.value == "completed"
        assert GenerationStatus.FAILED.value == "failed"

    def test_abc_methods_exist(self):
        """Verify all expected methods are on the ABC."""
        expected = [
            "create_generation", "complete_generation",
            "log_generation_event", "get_generation",
            "record_intent_version", "record_validation_version",
            "save_build_result", "get_build_result", "get_build_history",
            "save_build_step",
            "save_validation_result",
            "save_agent_response",
            "get_status", "set_status", "list_targets",
            "reset", "reset_all",
        ]
        for name in expected:
            assert hasattr(StorageBackend, name), f"Missing method: {name}"

    def test_no_sqlite_types_in_abc(self):
        """ABC should not import sqlite3."""
        import inspect
        import intentc.build.storage.backend as mod
        source = inspect.getsource(mod)
        assert "sqlite3" not in source

    def test_context_manager(self, tmp_dir: Path):
        with SQLiteBackend(base_dir=tmp_dir, output_dir="out") as b:
            b.set_status("x", TargetStatus.BUILT)
            assert b.get_status("x") == TargetStatus.BUILT
