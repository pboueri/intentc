"""Tests for the storage module."""

from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path

import pytest

from intentc.build.state import BuildResult, BuildStep, TargetStatus
from intentc.build.storage.backend import GenerationStatus, StorageBackend
from intentc.build.storage.sqlite import SQLiteBackend


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_backend(tmp_path: Path, output_dir: str = "src") -> SQLiteBackend:
    return SQLiteBackend(base_dir=tmp_path, output_dir=output_dir)


def _gen_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Backend interface tests
# ---------------------------------------------------------------------------


class TestStorageBackendInterface:
    """Verify the ABC defines all required methods and the enum exists."""

    def test_generation_status_enum(self):
        assert GenerationStatus.RUNNING.value == "running"
        assert GenerationStatus.COMPLETED.value == "completed"
        assert GenerationStatus.FAILED.value == "failed"

    def test_storage_backend_is_abstract(self):
        assert StorageBackend.__abstractmethods__
        # Should not be instantiable directly
        with pytest.raises(TypeError):
            StorageBackend(Path("/tmp"), "out")  # type: ignore[abstract]

    def test_sqlite_backend_is_concrete(self, tmp_path: Path):
        backend = _make_backend(tmp_path)
        assert isinstance(backend, StorageBackend)
        backend.close()

    def test_no_sqlite_types_in_abc(self):
        """No SQLite-specific types leak through the ABC."""
        import inspect
        import sqlite3

        for name, method in inspect.getmembers(StorageBackend, predicate=inspect.isfunction):
            sig = inspect.signature(method)
            for param in sig.parameters.values():
                annotation = param.annotation
                if annotation is inspect.Parameter.empty:
                    continue
                assert annotation is not sqlite3.Connection
                assert annotation is not sqlite3.Row


# ---------------------------------------------------------------------------
# Schema creation tests
# ---------------------------------------------------------------------------


EXPECTED_TABLES = [
    "intent_file_versions",
    "validation_file_versions",
    "generations",
    "generation_logs",
    "build_results",
    "build_steps",
    "validation_results",
    "agent_responses",
    "target_state",
]


class TestSchemaCreation:
    """Verify SQLiteBackend creates all tables on construction."""

    def test_all_tables_created(self, tmp_path: Path):
        backend = _make_backend(tmp_path)
        tables = backend._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = sorted(r["name"] for r in tables)
        for expected in EXPECTED_TABLES:
            assert expected in table_names, f"Missing table: {expected}"
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

    def test_db_file_location(self, tmp_path: Path):
        backend = _make_backend(tmp_path, output_dir="myout")
        expected = tmp_path / ".intentc" / "state" / "myout" / "intentc.db"
        assert expected.exists()
        backend.close()


# ---------------------------------------------------------------------------
# Full roundtrip tests
# ---------------------------------------------------------------------------


class TestStorageRoundtrip:
    """Verify data survives a full create → save → read roundtrip."""

    def test_full_roundtrip(self, tmp_path: Path):
        backend = _make_backend(tmp_path)
        gen_id = _gen_id()

        # 1. Create a generation
        backend.create_generation(gen_id, "src", profile_name="claude", options={"fast": True})
        gen = backend.get_generation(gen_id)
        assert gen is not None
        assert gen["status"] == "running"
        assert gen["profile_name"] == "claude"
        assert gen["options_json"] == {"fast": True}

        # 2. Record an intent file version
        iv_id = backend.record_intent_version("core/types", "intent/core/types.ic", "abc123")
        assert isinstance(iv_id, int)

        # Idempotent
        iv_id2 = backend.record_intent_version("core/types", "intent/core/types.ic", "abc123")
        assert iv_id == iv_id2

        # 3. Save a build result with steps
        steps = [
            BuildStep(phase="resolve_deps", status="success", duration_secs=0.1, summary="Deps resolved"),
            BuildStep(phase="build", status="success", duration_secs=5.2, summary="Built target"),
        ]
        result = BuildResult(
            target="core/types",
            status=TargetStatus.BUILT,
            steps=steps,
            commit_id="deadbeef",
            total_duration_secs=5.3,
            generation_id=gen_id,
        )
        br_id = backend.save_build_result(
            "core/types",
            result,
            intent_version_id=iv_id,
            git_diff="diff --git a/foo\n+bar",
            files_created=["types.py"],
            files_modified=["__init__.py"],
        )
        assert isinstance(br_id, int)

        # Save an additional build step with log text
        extra_step = BuildStep(phase="validate", status="success", duration_secs=1.0, summary="Validated")
        backend.save_build_step(br_id, extra_step, log="Running validation...\nAll passed.", step_order=2)

        # 4. Save an agent response
        backend.save_agent_response(
            build_result_id=br_id,
            validation_result_id=None,
            response_type="build",
            response_json={"status": "success", "summary": "done"},
        )

        # 5. Save validation results
        vv_id = backend.record_validation_version("core/types", "intent/core/types.icv", "def456")
        vr_id = backend.save_validation_result(
            build_result_id=br_id,
            generation_id=gen_id,
            target="core/types",
            validation_file_version_id=vv_id,
            name="types-exist",
            type="agent_validation",
            severity="error",
            status="pass",
            reason="All types present",
            duration_secs=0.5,
        )
        assert isinstance(vr_id, int)

        # Save agent response for validation
        backend.save_agent_response(
            build_result_id=None,
            validation_result_id=vr_id,
            response_type="validation",
            response_json={"status": "pass"},
        )

        # 6. Log generation events
        backend.log_generation_event(gen_id, "Starting build plan")
        backend.log_generation_event(gen_id, "Build complete")

        # 7. Complete the generation
        backend.complete_generation(gen_id, GenerationStatus.COMPLETED)

        # 8. Read back and verify
        gen = backend.get_generation(gen_id)
        assert gen is not None
        assert gen["status"] == "completed"
        assert len(gen["logs"]) == 2
        assert gen["logs"][0]["message"] == "Starting build plan"

        # Read back build result
        br = backend.get_build_result("core/types")
        assert br is not None
        assert br.target == "core/types"
        assert br.status == TargetStatus.BUILT
        assert br.commit_id == "deadbeef"
        assert len(br.steps) >= 2  # original 2 steps from save_build_result

        # Build history
        history = backend.get_build_history("core/types")
        assert len(history) == 1
        assert history[0].target == "core/types"

        # Target state was set
        assert backend.get_status("core/types") == TargetStatus.BUILT

        backend.close()

    def test_validation_version_idempotent(self, tmp_path: Path):
        backend = _make_backend(tmp_path)
        id1 = backend.record_validation_version("t", "p.icv", "hash1")
        id2 = backend.record_validation_version("t", "p.icv", "hash1")
        assert id1 == id2
        # Different hash = new version
        id3 = backend.record_validation_version("t", "p.icv", "hash2")
        assert id3 != id1
        backend.close()


# ---------------------------------------------------------------------------
# Target state management tests
# ---------------------------------------------------------------------------


class TestTargetState:
    """Verify target state CRUD operations."""

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

    def test_reset_single_target(self, tmp_path: Path):
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

    def test_output_dir_isolation(self, tmp_path: Path):
        b1 = _make_backend(tmp_path, "out1")
        b2 = _make_backend(tmp_path, "out2")

        b1.set_status("feat/a", TargetStatus.BUILT)
        b2.set_status("feat/a", TargetStatus.FAILED)

        assert b1.get_status("feat/a") == TargetStatus.BUILT
        assert b2.get_status("feat/a") == TargetStatus.FAILED

        b1.close()
        b2.close()


# ---------------------------------------------------------------------------
# Migration tests
# ---------------------------------------------------------------------------


class TestMigrationFromFlatFiles:
    """Verify migration from state.json to SQLite."""

    def test_migrates_state_json(self, tmp_path: Path):
        state_dir = tmp_path / ".intentc" / "state" / "src"
        state_dir.mkdir(parents=True)

        state_data = {
            "core/types": "built",
            "core/parser": "failed",
            "build/agents": "pending",
        }
        (state_dir / "state.json").write_text(json.dumps(state_data))

        backend = SQLiteBackend(base_dir=tmp_path, output_dir="src")

        # state.json renamed
        assert not (state_dir / "state.json").exists()
        assert (state_dir / "state.json.migrated").exists()

        # Targets migrated
        assert backend.get_status("core/types") == TargetStatus.BUILT
        assert backend.get_status("core/parser") == TargetStatus.FAILED
        assert backend.get_status("build/agents") == TargetStatus.PENDING

        backend.close()

    def test_migration_idempotent(self, tmp_path: Path):
        state_dir = tmp_path / ".intentc" / "state" / "src"
        state_dir.mkdir(parents=True)

        state_data = {"core/types": "built"}
        (state_dir / "state.json").write_text(json.dumps(state_data))

        # First construction migrates
        b1 = SQLiteBackend(base_dir=tmp_path, output_dir="src")
        b1.close()

        # Second construction skips migration (marker exists)
        b2 = SQLiteBackend(base_dir=tmp_path, output_dir="src")
        assert b2.get_status("core/types") == TargetStatus.BUILT

        # Only one generation was created (from migration)
        count = b2._conn.execute("SELECT COUNT(*) FROM generations").fetchone()[0]
        assert count == 1
        b2.close()

    def test_migration_with_dict_state(self, tmp_path: Path):
        """state.json with dict values (status nested in object)."""
        state_dir = tmp_path / ".intentc" / "state" / "src"
        state_dir.mkdir(parents=True)

        state_data = {
            "core/types": {"status": "built", "commit": "abc"},
            "build/agents": {"status": "failed"},
        }
        (state_dir / "state.json").write_text(json.dumps(state_data))

        backend = SQLiteBackend(base_dir=tmp_path, output_dir="src")
        assert backend.get_status("core/types") == TargetStatus.BUILT
        assert backend.get_status("build/agents") == TargetStatus.FAILED
        backend.close()

    def test_migration_with_build_log(self, tmp_path: Path):
        state_dir = tmp_path / ".intentc" / "state" / "src"
        state_dir.mkdir(parents=True)

        (state_dir / "state.json").write_text(json.dumps({"a": "built"}))
        log_lines = [
            json.dumps({"event": "start", "message": "Starting build"}),
            json.dumps({"event": "done", "message": "Build complete"}),
        ]
        (state_dir / "build-log.jsonl").write_text("\n".join(log_lines))

        backend = SQLiteBackend(base_dir=tmp_path, output_dir="src")

        # Verify logs migrated
        gens = backend._conn.execute("SELECT id FROM generations").fetchall()
        gen_id = gens[0]["id"]
        gen = backend.get_generation(gen_id)
        assert gen is not None
        assert len(gen["logs"]) == 2
        backend.close()

    def test_no_state_json_no_migration(self, tmp_path: Path):
        """No crash when state.json doesn't exist."""
        backend = _make_backend(tmp_path)
        assert backend.list_targets() == []
        backend.close()


# ---------------------------------------------------------------------------
# Context manager tests
# ---------------------------------------------------------------------------


class TestContextManager:
    def test_context_manager(self, tmp_path: Path):
        with _make_backend(tmp_path) as backend:
            backend.set_status("a", TargetStatus.BUILT)
            assert backend.get_status("a") == TargetStatus.BUILT
