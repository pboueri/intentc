from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from intentc.build.storage.backend import GenerationStatus, StorageBackend
from intentc.build.storage.sqlite_backend import SQLiteBackend


# ---------------------------------------------------------------------------
# Lightweight stand-ins for BuildResult / BuildStep (state module not yet built)
# ---------------------------------------------------------------------------

@dataclass
class _Step:
    phase: str
    status: str
    duration: timedelta
    summary: str


@dataclass
class _Result:
    target: str
    generation_id: str
    status: str
    steps: list[_Step]
    commit_id: str
    total_duration: timedelta
    timestamp: datetime


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_base(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture()
def backend(tmp_base: Path) -> SQLiteBackend:
    return SQLiteBackend(tmp_base, "src")


# ---------------------------------------------------------------------------
# storage-backend-interface: verify ABC and enum
# ---------------------------------------------------------------------------

class TestStorageBackendInterface:
    def test_generation_status_enum(self) -> None:
        assert GenerationStatus.RUNNING.value == "running"
        assert GenerationStatus.COMPLETED.value == "completed"
        assert GenerationStatus.FAILED.value == "failed"

    def test_storage_backend_is_abstract(self) -> None:
        assert StorageBackend.__abstractmethods__  # has abstract methods

    def test_sqlite_backend_is_concrete(self, backend: SQLiteBackend) -> None:
        assert isinstance(backend, StorageBackend)

    def test_abc_methods_present(self) -> None:
        expected = {
            "create_generation",
            "complete_generation",
            "log_generation_event",
            "get_generation",
            "record_intent_version",
            "record_validation_version",
            "save_build_result",
            "get_build_result",
            "get_build_history",
            "save_build_step",
            "save_validation_result",
            "save_agent_response",
            "get_status",
            "set_status",
            "list_targets",
            "reset",
            "reset_all",
        }
        assert expected.issubset(StorageBackend.__abstractmethods__)

    def test_no_sqlite_types_in_abc(self) -> None:
        """No sqlite3-specific types leak through the ABC annotations."""
        import inspect
        import sqlite3

        sqlite_types = {sqlite3.Connection, sqlite3.Cursor, sqlite3.Row}
        for name in StorageBackend.__abstractmethods__:
            sig = inspect.signature(getattr(StorageBackend, name))
            for param in sig.parameters.values():
                ann = param.annotation
                if ann is not inspect.Parameter.empty:
                    assert ann not in sqlite_types, f"{name} leaks sqlite type via {param.name}"
            ret = sig.return_annotation
            if ret is not inspect.Signature.empty:
                assert ret not in sqlite_types, f"{name} leaks sqlite return type"


# ---------------------------------------------------------------------------
# sqlite-schema-creation: tables, WAL, foreign keys
# ---------------------------------------------------------------------------

class TestSQLiteSchemaCreation:
    def test_database_created(self, backend: SQLiteBackend) -> None:
        assert backend._db_path.exists()

    def test_all_tables_exist(self, backend: SQLiteBackend) -> None:
        expected_tables = {
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
        rows = backend._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        actual = {row["name"] for row in rows}
        assert expected_tables.issubset(actual)

    def test_wal_mode(self, backend: SQLiteBackend) -> None:
        mode = backend._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"

    def test_foreign_keys_enabled(self, backend: SQLiteBackend) -> None:
        fk = backend._conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1


# ---------------------------------------------------------------------------
# storage-roundtrip: full lifecycle
# ---------------------------------------------------------------------------

class TestStorageRoundtrip:
    def test_full_roundtrip(self, backend: SQLiteBackend) -> None:
        gen_id = "gen-001"
        target = "feature/auth"

        # 1. Create a generation
        backend.create_generation(gen_id, "src", profile_name="default", options={"retries": 3})

        # 2. Record an intent file version
        intent_vid = backend.record_intent_version("auth", "intent/auth.ic", "sha256:abc123")

        # 3. Save a build result with steps
        now = datetime.now(timezone.utc)
        result = _Result(
            target=target,
            generation_id=gen_id,
            status="built",
            steps=[
                _Step(phase="resolve_deps", status="success", duration=timedelta(seconds=1.5), summary="Deps resolved"),
                _Step(phase="build", status="success", duration=timedelta(seconds=10.2), summary="Built ok"),
            ],
            commit_id="abc123def",
            total_duration=timedelta(seconds=11.7),
            timestamp=now,
        )
        br_id = backend.save_build_result(
            target,
            result,
            intent_version_id=intent_vid,
            git_diff="diff --git ...",
            files_created=["src/auth.py"],
            files_modified=["src/config.py"],
        )
        assert isinstance(br_id, int)

        # 4. Save an agent response
        backend.save_agent_response(
            build_result_id=br_id,
            validation_result_id=None,
            response_type="build",
            response_json={"status": "success", "summary": "Auth module built"},
        )

        # 5. Save validation results
        val_vid = backend.record_validation_version(target, "intent/auth.icv", "sha256:val456")
        vr_id = backend.save_validation_result(
            build_result_id=br_id,
            generation_id=gen_id,
            target=target,
            validation_file_version_id=val_vid,
            name="auth-file-check",
            type="file_check",
            severity="error",
            status="pass",
            reason="File exists",
            duration_secs=0.01,
        )
        assert isinstance(vr_id, int)

        # 6. Log generation events
        backend.log_generation_event(gen_id, "Build plan: [auth]")
        backend.log_generation_event(gen_id, "Build complete")

        # 7. Complete the generation
        backend.complete_generation(gen_id, GenerationStatus.COMPLETED)

        # 8. Read back all data
        gen = backend.get_generation(gen_id)
        assert gen is not None
        assert gen["status"] == "completed"
        assert gen["profile_name"] == "default"
        assert gen["options"] == {"retries": 3}
        assert len(gen["logs"]) == 2
        assert gen["logs"][0]["message"] == "Build plan: [auth]"

        br = backend.get_build_result(target)
        assert br is not None
        assert br["target"] == target
        assert br["status"] == "built"
        assert br["commit_id"] == "abc123def"
        assert br["git_diff"] == "diff --git ..."
        assert br["files_created"] == ["src/auth.py"]
        assert br["files_modified"] == ["src/config.py"]
        assert len(br["steps"]) == 2
        assert br["steps"][0]["phase"] == "resolve_deps"
        assert br["steps"][1]["phase"] == "build"

        history = backend.get_build_history(target)
        assert len(history) == 1

    def test_intent_version_idempotent(self, backend: SQLiteBackend) -> None:
        id1 = backend.record_intent_version("a", "a.ic", "hash1")
        id2 = backend.record_intent_version("a", "a.ic", "hash1")
        assert id1 == id2

    def test_validation_version_idempotent(self, backend: SQLiteBackend) -> None:
        id1 = backend.record_validation_version("t", "t.icv", "hash1")
        id2 = backend.record_validation_version("t", "t.icv", "hash1")
        assert id1 == id2


# ---------------------------------------------------------------------------
# target-state-management
# ---------------------------------------------------------------------------

class TestTargetState:
    def test_unknown_target_returns_pending(self, backend: SQLiteBackend) -> None:
        assert backend.get_status("nonexistent") == "pending"

    def test_set_and_get_roundtrip(self, backend: SQLiteBackend) -> None:
        backend.set_status("feature/a", "built")
        assert backend.get_status("feature/a") == "built"
        backend.set_status("feature/a", "outdated")
        assert backend.get_status("feature/a") == "outdated"

    def test_list_targets(self, backend: SQLiteBackend) -> None:
        backend.set_status("feature/a", "built")
        backend.set_status("feature/b", "failed")
        targets = backend.list_targets()
        assert ("feature/a", "built") in targets
        assert ("feature/b", "failed") in targets

    def test_reset_single(self, backend: SQLiteBackend) -> None:
        backend.set_status("feature/a", "built")
        backend.set_status("feature/b", "built")
        backend.reset("feature/a")
        assert backend.get_status("feature/a") == "pending"
        assert backend.get_status("feature/b") == "built"

    def test_reset_all(self, backend: SQLiteBackend) -> None:
        backend.set_status("feature/a", "built")
        backend.set_status("feature/b", "built")
        backend.reset_all()
        assert backend.get_status("feature/a") == "pending"
        assert backend.get_status("feature/b") == "pending"
        assert backend.list_targets() == []


# ---------------------------------------------------------------------------
# migration-from-flat-files
# ---------------------------------------------------------------------------

class TestMigration:
    def test_migrates_state_json(self, tmp_base: Path) -> None:
        db_dir = tmp_base / ".intentc" / "state" / "src"
        db_dir.mkdir(parents=True)
        state_file = db_dir / "state.json"
        state_file.write_text(json.dumps({
            "feature/auth": {"status": "built"},
            "feature/api": {"status": "failed"},
        }))

        backend = SQLiteBackend(tmp_base, "src")
        assert backend.get_status("feature/auth") == "built"
        assert backend.get_status("feature/api") == "failed"
        assert not state_file.exists()
        assert (db_dir / "state.json.migrated").exists()
        backend.close()

    def test_migrates_build_log(self, tmp_base: Path) -> None:
        db_dir = tmp_base / ".intentc" / "state" / "src"
        db_dir.mkdir(parents=True)
        state_file = db_dir / "state.json"
        state_file.write_text(json.dumps({"feature/x": {"status": "built"}}))
        log_file = db_dir / "build-log.jsonl"
        log_file.write_text(
            json.dumps({
                "target": "feature/x",
                "status": "built",
                "generation_id": "gen-1",
                "commit_id": "abc",
                "total_duration_secs": 5.0,
                "timestamp": "2025-01-01T00:00:00Z",
                "steps": [
                    {"phase": "build", "status": "success", "duration_secs": 5.0, "summary": "ok"},
                ],
            })
            + "\n"
        )

        backend = SQLiteBackend(tmp_base, "src")
        history = backend.get_build_history("feature/x")
        assert len(history) == 1
        assert history[0]["commit_id"] == "abc"
        assert len(history[0]["steps"]) == 1
        backend.close()

    def test_migration_idempotent(self, tmp_base: Path) -> None:
        db_dir = tmp_base / ".intentc" / "state" / "src"
        db_dir.mkdir(parents=True)
        state_file = db_dir / "state.json"
        state_file.write_text(json.dumps({"feature/a": {"status": "built"}}))

        b1 = SQLiteBackend(tmp_base, "src")
        b1.close()
        # Second construction — marker exists, migration skipped
        b2 = SQLiteBackend(tmp_base, "src")
        assert b2.get_status("feature/a") == "built"
        assert len(b2.list_targets()) == 1
        b2.close()

    def test_no_state_file_no_migration(self, tmp_base: Path) -> None:
        """No crash when there is no state.json."""
        backend = SQLiteBackend(tmp_base, "src")
        assert backend.list_targets() == []
        backend.close()
