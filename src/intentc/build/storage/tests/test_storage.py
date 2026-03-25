"""Tests for the storage module: SQLiteBackend, schema creation, roundtrips, migration."""

from __future__ import annotations

import json
import sqlite3
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
    be = SQLiteBackend(base_dir=tmp_dir, output_dir="src")
    yield be
    be.close()


# ---------------------------------------------------------------------------
# 1. Interface / ABC checks
# ---------------------------------------------------------------------------


class TestStorageInterface:
    def test_storage_backend_is_abstract(self):
        """StorageBackend ABC cannot be instantiated directly."""
        with pytest.raises(TypeError):
            StorageBackend(Path("/tmp"), "out")  # type: ignore[abstract]

    def test_generation_status_enum(self):
        assert GenerationStatus.RUNNING.value == "running"
        assert GenerationStatus.COMPLETED.value == "completed"
        assert GenerationStatus.FAILED.value == "failed"

    def test_sqlite_backend_is_concrete(self, backend: SQLiteBackend):
        assert isinstance(backend, StorageBackend)

    def test_no_sqlite_types_in_abc(self):
        """No sqlite3-specific types should appear in the ABC's annotations."""
        import inspect

        for name, method in inspect.getmembers(StorageBackend, predicate=inspect.isfunction):
            hints = method.__annotations__
            for hint_val in hints.values():
                hint_str = str(hint_val)
                assert "sqlite3" not in hint_str.lower(), (
                    f"StorageBackend.{name} leaks sqlite type: {hint_str}"
                )


# ---------------------------------------------------------------------------
# 2. Schema creation
# ---------------------------------------------------------------------------

EXPECTED_TABLES = {
    "intent_file_versions",
    "validation_file_versions",
    "generations",
    "generation_logs",
    "build_results",
    "build_steps",
    "validation_results",
    "agent_responses",
    "target_state",
}


class TestSchemaCreation:
    def test_all_tables_created(self, backend: SQLiteBackend):
        conn = sqlite3.connect(str(backend._db_path))
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {r[0] for r in rows}
        conn.close()
        assert EXPECTED_TABLES.issubset(table_names), (
            f"Missing tables: {EXPECTED_TABLES - table_names}"
        )

    def test_wal_mode_enabled(self, backend: SQLiteBackend):
        row = backend._conn.execute("PRAGMA journal_mode").fetchone()
        assert row[0] == "wal"

    def test_foreign_keys_enabled(self, backend: SQLiteBackend):
        row = backend._conn.execute("PRAGMA foreign_keys").fetchone()
        assert row[0] == 1

    def test_database_file_exists(self, backend: SQLiteBackend):
        assert backend._db_path.exists()


# ---------------------------------------------------------------------------
# 3. Full roundtrip
# ---------------------------------------------------------------------------


class TestStorageRoundtrip:
    def test_full_roundtrip(self, backend: SQLiteBackend):
        # 1. Create a generation
        gen_id = "gen-001"
        backend.create_generation(
            gen_id, "src", profile_name="default", options={"retry": True}
        )

        # 2. Record an intent file version
        iv_id = backend.record_intent_version(
            "build/storage", "intent/build/storage/storage.ic", "abc123"
        )
        assert isinstance(iv_id, int) and iv_id > 0

        # Idempotent: same hash returns same id
        iv_id2 = backend.record_intent_version(
            "build/storage", "intent/build/storage/storage.ic", "abc123"
        )
        assert iv_id2 == iv_id

        # 3. Save a build result with steps
        step1 = BuildStep(phase="resolve_deps", status="success", duration_secs=0.5, summary="resolved")
        step2 = BuildStep(phase="build", status="success", duration_secs=2.0, summary="built")
        result = BuildResult(
            target="build/storage",
            generation_id=gen_id,
            status="success",
            commit_id="deadbeef",
            total_duration_secs=2.5,
            timestamp="2026-01-01T00:00:00Z",
            steps=[step1, step2],
        )
        br_id = backend.save_build_result(
            "build/storage",
            result,
            intent_version_id=iv_id,
            git_diff="diff --git ...",
            files_created=["storage.py"],
            files_modified=["__init__.py"],
        )
        assert isinstance(br_id, int) and br_id > 0

        # 4. Save an agent response
        backend.save_agent_response(
            build_result_id=br_id,
            validation_result_id=None,
            response_type="build",
            response_json={"status": "success", "summary": "done"},
        )

        # 5. Save a validation result
        vv_id = backend.record_validation_version(
            "build/storage", "intent/build/storage/validations.icv", "val_hash_1"
        )
        vr_id = backend.save_validation_result(
            build_result_id=br_id,
            generation_id=gen_id,
            target="build/storage",
            validation_file_version_id=vv_id,
            name="storage-roundtrip",
            type="agent_validation",
            severity="error",
            status="pass",
            reason="All checks passed",
            duration_secs=1.0,
        )
        assert isinstance(vr_id, int) and vr_id > 0

        # 6. Log generation events
        backend.log_generation_event(gen_id, "Build started")
        backend.log_generation_event(gen_id, "Build completed")

        # 7. Complete the generation
        backend.complete_generation(gen_id, GenerationStatus.COMPLETED)

        # 8. Read back and verify
        gen = backend.get_generation(gen_id)
        assert gen is not None
        assert gen["status"] == "completed"
        assert gen["profile_name"] == "default"
        assert gen["options"] == {"retry": True}
        assert gen["completed_at"] is not None
        assert len(gen["logs"]) == 2
        assert gen["logs"][0]["message"] == "Build started"

        loaded = backend.get_build_result("build/storage")
        assert loaded is not None
        assert loaded.target == "build/storage"
        assert loaded.status == "success"
        assert loaded.commit_id == "deadbeef"
        assert loaded.total_duration_secs == 2.5
        assert len(loaded.steps) == 2
        assert loaded.steps[0].phase == "resolve_deps"
        assert loaded.steps[1].phase == "build"

        history = backend.get_build_history("build/storage")
        assert len(history) == 1

    def test_save_build_step_individually(self, backend: SQLiteBackend):
        """save_build_step can add steps after the initial save."""
        gen_id = "gen-step"
        backend.create_generation(gen_id, "src")
        result = BuildResult(
            target="feat/a", generation_id=gen_id, status="success",
            timestamp="2026-01-01T00:00:00Z",
        )
        br_id = backend.save_build_result("feat/a", result)

        step = BuildStep(phase="validate", status="pass", duration_secs=0.1, summary="ok")
        backend.save_build_step(br_id, step, log="validation output here", step_order=0)

        # Verify step was saved with log
        row = backend._conn.execute(
            "SELECT log FROM build_steps WHERE build_result_id = ? AND step_order = 0",
            (br_id,),
        ).fetchone()
        assert row is not None
        assert row[0] == "validation output here"

    def test_agent_response_for_validation(self, backend: SQLiteBackend):
        """Agent responses can link to validation results."""
        gen_id = "gen-val"
        backend.create_generation(gen_id, "src")
        vr_id = backend.save_validation_result(
            build_result_id=None,
            generation_id=gen_id,
            target="feat/a",
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
            response_json={"name": "check", "status": "pass", "reason": "ok"},
        )
        row = backend._conn.execute(
            "SELECT response_json FROM agent_responses WHERE validation_result_id = ?",
            (vr_id,),
        ).fetchone()
        assert row is not None
        data = json.loads(row[0])
        assert data["status"] == "pass"


# ---------------------------------------------------------------------------
# 4. Target state management
# ---------------------------------------------------------------------------


class TestTargetState:
    def test_unknown_target_returns_pending(self, backend: SQLiteBackend):
        assert backend.get_status("nonexistent") == TargetStatus.PENDING

    def test_set_and_get_roundtrip(self, backend: SQLiteBackend):
        backend.set_status("feat/a", TargetStatus.BUILT)
        assert backend.get_status("feat/a") == TargetStatus.BUILT

        backend.set_status("feat/a", TargetStatus.OUTDATED)
        assert backend.get_status("feat/a") == TargetStatus.OUTDATED

    def test_list_targets(self, backend: SQLiteBackend):
        backend.set_status("feat/a", TargetStatus.BUILT)
        backend.set_status("feat/b", TargetStatus.FAILED)
        targets = backend.list_targets()
        targets_dict = dict(targets)
        assert targets_dict["feat/a"] == TargetStatus.BUILT
        assert targets_dict["feat/b"] == TargetStatus.FAILED

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
        assert backend.get_status("feat/a") == TargetStatus.PENDING
        assert backend.get_status("feat/b") == TargetStatus.PENDING
        assert backend.list_targets() == []


# ---------------------------------------------------------------------------
# 5. Migration from flat files
# ---------------------------------------------------------------------------


class TestMigration:
    def test_migrate_state_json(self, tmp_dir: Path):
        """When state.json exists, it is migrated and renamed."""
        db_dir = tmp_dir / ".intentc" / "state" / "src"
        db_dir.mkdir(parents=True)
        state = {
            "targets": {
                "feat/a": "built",
                "feat/b": {"status": "failed"},
            }
        }
        (db_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")

        be = SQLiteBackend(base_dir=tmp_dir, output_dir="src")
        try:
            assert (db_dir / "state.json.migrated").exists()
            assert not (db_dir / "state.json").exists()
            assert be.get_status("feat/a") == TargetStatus.BUILT
            assert be.get_status("feat/b") == TargetStatus.FAILED
        finally:
            be.close()

    def test_migration_idempotent(self, tmp_dir: Path):
        """If .migrated marker exists, migration is skipped."""
        db_dir = tmp_dir / ".intentc" / "state" / "src"
        db_dir.mkdir(parents=True)
        # Create the migrated marker with some dummy content
        (db_dir / "state.json.migrated").write_text("{}", encoding="utf-8")

        be = SQLiteBackend(base_dir=tmp_dir, output_dir="src")
        try:
            # No targets should be present since migration was skipped
            assert be.list_targets() == []
        finally:
            be.close()

    def test_migrate_build_log(self, tmp_dir: Path):
        """build-log.jsonl entries are migrated into build_results and build_steps."""
        db_dir = tmp_dir / ".intentc" / "state" / "src"
        db_dir.mkdir(parents=True)

        state = {"targets": {}}
        (db_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")

        log_entry = {
            "target": "feat/x",
            "status": "success",
            "total_duration_secs": 3.5,
            "timestamp": "2026-01-15T10:00:00Z",
            "steps": [
                {"phase": "build", "status": "success", "duration_secs": 3.0, "summary": "built", "log": "output"},
            ],
        }
        (db_dir / "build-log.jsonl").write_text(
            json.dumps(log_entry) + "\n", encoding="utf-8"
        )

        be = SQLiteBackend(base_dir=tmp_dir, output_dir="src")
        try:
            # Verify build result was migrated
            rows = be._conn.execute(
                "SELECT target, status FROM build_results"
            ).fetchall()
            assert len(rows) == 1
            assert rows[0][0] == "feat/x"
            assert rows[0][1] == "success"

            # Verify build step was migrated
            step_rows = be._conn.execute(
                "SELECT phase, summary FROM build_steps"
            ).fetchall()
            assert len(step_rows) == 1
            assert step_rows[0][0] == "build"
        finally:
            be.close()

    def test_context_manager(self, tmp_dir: Path):
        """SQLiteBackend works as a context manager."""
        with SQLiteBackend(base_dir=tmp_dir, output_dir="src") as be:
            be.set_status("feat/a", TargetStatus.BUILT)
            assert be.get_status("feat/a") == TargetStatus.BUILT
