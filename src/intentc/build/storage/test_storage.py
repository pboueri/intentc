"""Tests for the storage package."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from intentc.build.state import BuildResult, BuildStep, TargetStatus
from intentc.build.storage.backend import GenerationStatus, StorageBackend
from intentc.build.storage.sqlite import SQLiteBackend


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_backend(tmp_path: Path) -> SQLiteBackend:
    return SQLiteBackend(base_dir=tmp_path, output_dir="test_output")


def _sample_result(generation_id: str = "gen-1", target: str = "feat/a") -> BuildResult:
    return BuildResult(
        generation_id=generation_id,
        target=target,
        status=TargetStatus.BUILT,
        commit_id="abc123",
        total_duration_secs=1.5,
        timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
        steps=[
            BuildStep(phase="resolve_deps", status="success", duration_secs=0.1, summary="deps resolved"),
            BuildStep(phase="build", status="success", duration_secs=1.0, summary="built ok"),
            BuildStep(phase="validate", status="success", duration_secs=0.3, summary="validated"),
        ],
    )


# ---------------------------------------------------------------------------
# Interface tests
# ---------------------------------------------------------------------------


class TestStorageBackendInterface:
    """Verify the ABC shape — SQLiteBackend must be a concrete StorageBackend."""

    def test_is_subclass(self):
        assert issubclass(SQLiteBackend, StorageBackend)

    def test_instantiates(self, tmp_path: Path):
        backend = _make_backend(tmp_path)
        assert isinstance(backend, StorageBackend)
        backend.close()

    def test_generation_status_values(self):
        assert GenerationStatus.RUNNING.value == "running"
        assert GenerationStatus.COMPLETED.value == "completed"
        assert GenerationStatus.FAILED.value == "failed"

    def test_no_sqlite_types_in_abc(self):
        """ABC methods should not reference sqlite3 types in annotations."""
        import inspect
        import sqlite3

        sqlite_types = {sqlite3.Connection, sqlite3.Cursor, sqlite3.Row}
        for name, method in inspect.getmembers(StorageBackend, predicate=inspect.isfunction):
            if name.startswith("_"):
                continue
            hints = method.__annotations__
            for hint in hints.values():
                assert hint not in sqlite_types, (
                    f"StorageBackend.{name} leaks sqlite3 type: {hint}"
                )


# ---------------------------------------------------------------------------
# Schema creation tests
# ---------------------------------------------------------------------------


class TestSchemaCreation:
    """SQLiteBackend creates DB and all tables on construction."""

    def test_database_file_created(self, tmp_path: Path):
        backend = _make_backend(tmp_path)
        db_path = tmp_path / ".intentc" / "state" / "test_output" / "intentc.db"
        assert db_path.exists()
        backend.close()

    def test_all_tables_exist(self, tmp_path: Path):
        backend = _make_backend(tmp_path)
        rows = backend._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        tables = {r[0] for r in rows}
        expected = {
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
        assert expected.issubset(tables)
        backend.close()

    def test_wal_mode(self, tmp_path: Path):
        backend = _make_backend(tmp_path)
        mode = backend._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
        backend.close()

    def test_foreign_keys_enabled(self, tmp_path: Path):
        backend = _make_backend(tmp_path)
        fk = backend._conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1
        backend.close()


# ---------------------------------------------------------------------------
# Full roundtrip tests
# ---------------------------------------------------------------------------


class TestStorageRoundtrip:
    """End-to-end: create gen -> record versions -> save results -> read back."""

    def test_full_roundtrip(self, tmp_path: Path):
        backend = _make_backend(tmp_path)

        # 1. Create a generation
        backend.create_generation("gen-1", "test_output", "default", {"dry_run": False})

        # 2. Record an intent file version
        intent_v_id = backend.record_intent_version("feat/a", "features/a.ic", "sha256-aaa")

        # 3. Save a build result with steps
        result = _sample_result()
        br_id = backend.save_build_result(
            target="feat/a",
            result=result,
            intent_version_id=intent_v_id,
            git_diff="diff --git a/foo",
            files_created=["foo.py"],
            files_modified=["bar.py"],
        )
        assert isinstance(br_id, int)

        # 4. Save an agent response
        backend.save_agent_response(
            build_result_id=br_id,
            validation_result_id=None,
            response_type="build",
            response_json={"status": "success", "summary": "built ok"},
        )

        # 5. Save validation results
        val_v_id = backend.record_validation_version("feat/a", "features/a.icv", "sha256-bbb")
        vr_id = backend.save_validation_result(
            build_result_id=br_id,
            generation_id="gen-1",
            target="feat/a",
            validation_file_version_id=val_v_id,
            name="schema-check",
            type="file_check",
            severity="error",
            status="pass",
            reason="schema valid",
            duration_secs=0.05,
        )
        assert isinstance(vr_id, int)

        # 6. Log generation events
        backend.log_generation_event("gen-1", "Build plan: [feat/a]")
        backend.log_generation_event("gen-1", "Build complete")

        # 7. Complete the generation
        backend.complete_generation("gen-1", GenerationStatus.COMPLETED)

        # 8. Read back and verify
        gen = backend.get_generation("gen-1")
        assert gen is not None
        assert gen["status"] == "completed"
        assert gen["profile_name"] == "default"
        assert gen["options"]["dry_run"] is False
        assert len(gen["logs"]) == 2
        assert gen["logs"][0]["message"] == "Build plan: [feat/a]"

        loaded = backend.get_build_result("feat/a")
        assert loaded is not None
        assert loaded.generation_id == "gen-1"
        assert loaded.status == TargetStatus.BUILT
        assert loaded.commit_id == "abc123"
        assert len(loaded.steps) == 3
        assert loaded.steps[0].phase == "resolve_deps"
        assert loaded.steps[1].phase == "build"

        history = backend.get_build_history("feat/a")
        assert len(history) >= 1

        backend.close()

    def test_intent_version_idempotent(self, tmp_path: Path):
        backend = _make_backend(tmp_path)
        id1 = backend.record_intent_version("feat/a", "a.ic", "hash1")
        id2 = backend.record_intent_version("feat/a", "a.ic", "hash1")
        assert id1 == id2
        id3 = backend.record_intent_version("feat/a", "a.ic", "hash2")
        assert id3 != id1
        backend.close()

    def test_validation_version_idempotent(self, tmp_path: Path):
        backend = _make_backend(tmp_path)
        id1 = backend.record_validation_version("feat/a", "a.icv", "hash1")
        id2 = backend.record_validation_version("feat/a", "a.icv", "hash1")
        assert id1 == id2
        backend.close()

    def test_save_build_step_with_log(self, tmp_path: Path):
        backend = _make_backend(tmp_path)
        backend.create_generation("gen-1", "test_output", None, None)
        result = _sample_result()
        br_id = backend.save_build_result("feat/a", result, None, None, None, None)

        # Save an additional step with log text
        step = BuildStep(phase="checkpoint", status="success", duration_secs=0.2, summary="committed")
        backend.save_build_step(br_id, step, "git commit abc123\n", step_order=3)

        # Verify the log is stored
        rows = backend._conn.execute(
            "SELECT log FROM build_steps WHERE build_result_id = ? AND phase = 'checkpoint'",
            (br_id,),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "git commit abc123\n"
        backend.close()


# ---------------------------------------------------------------------------
# Target state tests
# ---------------------------------------------------------------------------


class TestTargetState:
    """Target state CRUD operations."""

    def test_unknown_target_returns_pending(self, tmp_path: Path):
        backend = _make_backend(tmp_path)
        assert backend.get_status("nonexistent") == TargetStatus.PENDING
        backend.close()

    def test_set_and_get_status(self, tmp_path: Path):
        backend = _make_backend(tmp_path)
        backend.set_status("feat/a", TargetStatus.BUILT)
        assert backend.get_status("feat/a") == TargetStatus.BUILT

        backend.set_status("feat/a", TargetStatus.OUTDATED)
        assert backend.get_status("feat/a") == TargetStatus.OUTDATED
        backend.close()

    def test_list_targets(self, tmp_path: Path):
        backend = _make_backend(tmp_path)
        backend.set_status("feat/a", TargetStatus.BUILT)
        backend.set_status("feat/b", TargetStatus.FAILED)
        backend.set_status("feat/c", TargetStatus.PENDING)

        targets = backend.list_targets()
        assert len(targets) == 3
        target_dict = dict(targets)
        assert target_dict["feat/a"] == TargetStatus.BUILT
        assert target_dict["feat/b"] == TargetStatus.FAILED
        assert target_dict["feat/c"] == TargetStatus.PENDING
        backend.close()

    def test_reset_single(self, tmp_path: Path):
        backend = _make_backend(tmp_path)
        backend.set_status("feat/a", TargetStatus.BUILT)
        backend.set_status("feat/b", TargetStatus.BUILT)

        backend.reset("feat/a")
        assert backend.get_status("feat/a") == TargetStatus.PENDING
        assert backend.get_status("feat/b") == TargetStatus.BUILT
        backend.close()

    def test_reset_all(self, tmp_path: Path):
        backend = _make_backend(tmp_path)
        backend.set_status("feat/a", TargetStatus.BUILT)
        backend.set_status("feat/b", TargetStatus.FAILED)

        backend.reset_all()
        assert backend.get_status("feat/a") == TargetStatus.PENDING
        assert backend.get_status("feat/b") == TargetStatus.PENDING
        assert backend.list_targets() == []
        backend.close()


# ---------------------------------------------------------------------------
# Migration tests
# ---------------------------------------------------------------------------


class TestMigrationFromFlatFiles:
    """state.json migration on construction."""

    def _write_state_json(self, tmp_path: Path, data: dict) -> Path:
        state_dir = tmp_path / ".intentc" / "state" / "test_output"
        state_dir.mkdir(parents=True, exist_ok=True)
        state_file = state_dir / "state.json"
        state_file.write_text(json.dumps(data))
        return state_dir

    def test_migrates_state_json(self, tmp_path: Path):
        self._write_state_json(tmp_path, {
            "feat/a": {"status": "built"},
            "feat/b": {"status": "failed"},
        })

        backend = _make_backend(tmp_path)
        assert backend.get_status("feat/a") == TargetStatus.BUILT
        assert backend.get_status("feat/b") == TargetStatus.FAILED
        backend.close()

    def test_renames_to_migrated(self, tmp_path: Path):
        state_dir = self._write_state_json(tmp_path, {"feat/a": {"status": "built"}})

        backend = _make_backend(tmp_path)
        assert not (state_dir / "state.json").exists()
        assert (state_dir / "state.json.migrated").exists()
        backend.close()

    def test_migration_idempotent(self, tmp_path: Path):
        self._write_state_json(tmp_path, {"feat/a": {"status": "built"}})

        backend1 = _make_backend(tmp_path)
        backend1.close()

        # Second construction should not fail or duplicate data
        backend2 = _make_backend(tmp_path)
        assert backend2.get_status("feat/a") == TargetStatus.BUILT
        targets = backend2.list_targets()
        assert len(targets) == 1
        backend2.close()

    def test_migrates_build_log(self, tmp_path: Path):
        state_dir = self._write_state_json(tmp_path, {"feat/a": {"status": "built"}})
        log_file = state_dir / "build-log.jsonl"
        log_file.write_text(
            json.dumps({
                "target": "feat/a",
                "generation_id": "gen-old",
                "status": "built",
                "duration_secs": 2.0,
                "timestamp": "2025-01-01T00:00:00",
                "steps": [
                    {"phase": "build", "status": "success", "duration_secs": 2.0, "summary": "done"},
                ],
            })
            + "\n"
        )

        backend = _make_backend(tmp_path)
        history = backend.get_build_history("feat/a")
        assert len(history) >= 1
        backend.close()

    def test_context_manager(self, tmp_path: Path):
        with SQLiteBackend(base_dir=tmp_path, output_dir="test_output") as backend:
            backend.set_status("feat/a", TargetStatus.BUILT)
            assert backend.get_status("feat/a") == TargetStatus.BUILT
