"""Tests for the storage module."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from intentc.build.storage.backend import GenerationStatus, StorageBackend
from intentc.build.storage.sqlite import SQLiteBackend


# ---------------------------------------------------------------------------
# StorageBackend interface
# ---------------------------------------------------------------------------


class TestStorageBackendInterface:
    def test_is_abstract(self):
        with pytest.raises(TypeError):
            StorageBackend(Path("."), "out")  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# SQLiteBackend — schema creation
# ---------------------------------------------------------------------------


class TestSQLiteSchemaCreation:
    def test_creates_database_on_construction(self, tmp_path: Path):
        backend = SQLiteBackend(tmp_path, "out")
        db_path = tmp_path / ".intentc" / "state" / "out" / "intentc.db"
        assert db_path.exists()
        backend.close()

    def test_creates_all_tables(self, tmp_path: Path):
        backend = SQLiteBackend(tmp_path, "out")
        tables = backend._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = sorted(t[0] for t in tables if t[0] != "sqlite_sequence")
        expected = sorted([
            "intent_file_versions",
            "validation_file_versions",
            "generations",
            "generation_logs",
            "build_results",
            "build_steps",
            "validation_results",
            "agent_responses",
            "target_state",
        ])
        assert table_names == expected
        backend.close()

    def test_wal_mode_enabled(self, tmp_path: Path):
        backend = SQLiteBackend(tmp_path, "out")
        mode = backend._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
        backend.close()

    def test_foreign_keys_enabled(self, tmp_path: Path):
        backend = SQLiteBackend(tmp_path, "out")
        fk = backend._conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1
        backend.close()


# ---------------------------------------------------------------------------
# Full roundtrip
# ---------------------------------------------------------------------------


class TestStorageRoundtrip:
    def test_full_roundtrip(self, tmp_path: Path):
        backend = SQLiteBackend(tmp_path, "out")

        # 1. Create a generation
        backend.create_generation("gen-1", "out", "default", {"force": True})

        # 2. Record an intent file version
        v_id = backend.record_intent_version("feat/a", "intent/feat/a/a.ic", "sha256abc")

        # 3. Save a build result with steps
        result_dict = {
            "target": "feat/a",
            "generation_id": "gen-1",
            "status": "built",
            "commit_id": "abc123",
            "total_duration": 3.7,
            "timestamp": "2026-03-16T12:00:00",
            "steps": [
                {"phase": "resolve_deps", "status": "success", "duration": 0.5, "summary": "Resolved 2 deps"},
                {"phase": "build", "status": "success", "duration": 3.2, "summary": "Built", "log": "agent output here"},
            ],
        }
        br_id = backend.save_build_result(
            "feat/a", result_dict, "gen-1",
            intent_version_id=v_id,
            git_diff="diff --git ...",
            files_created=["src/feat/a.py"],
            files_modified=["src/utils.py"],
        )

        # 4. Save an agent response
        backend.save_agent_response(br_id, None, "build", {"status": "success", "summary": "ok"})

        # 5. Save validation results
        vr_id = backend.save_validation_result(
            br_id, "gen-1", "feat/a", None, "check-1", "agent_validation", "error", "pass", "All good", 1.5
        )

        # 6. Log generation events
        backend.log_generation_event("gen-1", "Starting build")
        backend.log_generation_event("gen-1", "Building feat/a")

        # 7. Complete the generation
        backend.complete_generation("gen-1", GenerationStatus.COMPLETED)

        # -- Read back and verify --

        gen = backend.get_generation("gen-1")
        assert gen is not None
        assert gen["status"] == "completed"
        assert gen["profile_name"] == "default"
        assert gen["options_json"] == {"force": True}
        assert len(gen["logs"]) == 2
        assert gen["logs"][0]["message"] == "Starting build"

        loaded = backend.get_build_result("feat/a")
        assert loaded is not None
        assert loaded["target"] == "feat/a"
        assert loaded["status"] == "built"
        assert loaded["commit_id"] == "abc123"
        assert loaded["total_duration"] == 3.7
        assert loaded["timestamp"] == "2026-03-16T12:00:00"
        assert loaded["git_diff"] == "diff --git ..."
        assert loaded["files_created"] == ["src/feat/a.py"]
        assert loaded["files_modified"] == ["src/utils.py"]
        assert len(loaded["steps"]) == 2
        assert loaded["steps"][0]["phase"] == "resolve_deps"
        assert loaded["steps"][1]["log"] == "agent output here"

        backend.close()

    def test_get_nonexistent_generation(self, tmp_path: Path):
        backend = SQLiteBackend(tmp_path, "out")
        assert backend.get_generation("nonexistent") is None
        backend.close()


# ---------------------------------------------------------------------------
# Target state management
# ---------------------------------------------------------------------------


class TestTargetState:
    def test_unknown_target_returns_pending(self, tmp_path: Path):
        backend = SQLiteBackend(tmp_path, "out")
        assert backend.get_status("unknown") == "pending"
        backend.close()

    def test_set_and_get_status(self, tmp_path: Path):
        backend = SQLiteBackend(tmp_path, "out")
        backend.set_status("feat/a", "built")
        assert backend.get_status("feat/a") == "built"
        backend.close()

    def test_list_targets(self, tmp_path: Path):
        backend = SQLiteBackend(tmp_path, "out")
        backend.set_status("b", "built")
        backend.set_status("a", "pending")
        targets = backend.list_targets()
        assert targets == [("a", "pending"), ("b", "built")]
        backend.close()

    def test_reset_single_target(self, tmp_path: Path):
        backend = SQLiteBackend(tmp_path, "out")
        backend.set_status("a", "built")
        backend.set_status("b", "built")
        backend.reset("a")
        assert backend.get_status("a") == "pending"
        assert backend.get_status("b") == "built"
        backend.close()

    def test_reset_all(self, tmp_path: Path):
        backend = SQLiteBackend(tmp_path, "out")
        backend.set_status("a", "built")
        backend.set_status("b", "built")
        backend.reset_all()
        assert backend.list_targets() == []
        assert backend.get_status("a") == "pending"
        backend.close()


# ---------------------------------------------------------------------------
# Build history (append-only)
# ---------------------------------------------------------------------------


class TestBuildHistory:
    def test_append_only_history(self, tmp_path: Path):
        backend = SQLiteBackend(tmp_path, "out")
        backend.create_generation("gen-1", "out", None, None)

        for i in range(3):
            backend.save_build_result(
                "feat/a",
                {"status": "built", "total_duration": float(i), "timestamp": f"2026-03-{16+i}T00:00:00", "steps": []},
                "gen-1",
            )

        history = backend.get_build_history("feat/a")
        assert len(history) == 3
        # Newest first
        assert history[0]["total_duration"] == 2.0
        assert history[2]["total_duration"] == 0.0

        # target_state points to latest
        latest = backend.get_build_result("feat/a")
        assert latest is not None
        assert latest["total_duration"] == 2.0
        backend.close()


# ---------------------------------------------------------------------------
# Intent/validation file versions
# ---------------------------------------------------------------------------


class TestFileVersions:
    def test_intent_version_idempotent(self, tmp_path: Path):
        backend = SQLiteBackend(tmp_path, "out")
        id1 = backend.record_intent_version("feat/a", "a.ic", "hash1")
        id2 = backend.record_intent_version("feat/a", "a.ic", "hash1")
        assert id1 == id2
        id3 = backend.record_intent_version("feat/a", "a.ic", "hash2")
        assert id3 != id1
        backend.close()

    def test_validation_version_idempotent(self, tmp_path: Path):
        backend = SQLiteBackend(tmp_path, "out")
        id1 = backend.record_validation_version("feat/a", "a.icv", "hash1")
        id2 = backend.record_validation_version("feat/a", "a.icv", "hash1")
        assert id1 == id2
        backend.close()


# ---------------------------------------------------------------------------
# Migration from flat files
# ---------------------------------------------------------------------------


class TestMigration:
    def test_migrates_state_json(self, tmp_path: Path):
        state_dir = tmp_path / ".intentc" / "state" / "out"
        state_dir.mkdir(parents=True)
        state_data = {
            "version": 1,
            "targets": {
                "feat/a": {
                    "status": "built",
                    "build_result": {
                        "target": "feat/a",
                        "generation_id": "g1",
                        "status": "built",
                        "steps": [{"phase": "build", "status": "success", "duration": 1.0, "summary": "ok"}],
                        "commit_id": "sha1",
                        "total_duration": 1.0,
                        "timestamp": "2026-01-01T00:00:00",
                    },
                },
            },
        }
        (state_dir / "state.json").write_text(json.dumps(state_data))

        backend = SQLiteBackend(tmp_path, "out")

        assert backend.get_status("feat/a") == "built"
        result = backend.get_build_result("feat/a")
        assert result is not None
        assert result["commit_id"] == "sha1"

        # state.json renamed
        assert not (state_dir / "state.json").exists()
        assert (state_dir / "state.json.migrated").exists()
        backend.close()

    def test_migration_idempotent(self, tmp_path: Path):
        state_dir = tmp_path / ".intentc" / "state" / "out"
        state_dir.mkdir(parents=True)
        state_data = {"version": 1, "targets": {"a": {"status": "built", "build_result": None}}}
        (state_dir / "state.json").write_text(json.dumps(state_data))

        backend1 = SQLiteBackend(tmp_path, "out")
        backend1.close()

        # Second construction — migrated marker exists, should skip
        backend2 = SQLiteBackend(tmp_path, "out")
        assert backend2.get_status("a") == "built"
        backend2.close()

    def test_context_manager(self, tmp_path: Path):
        with SQLiteBackend(tmp_path, "out") as backend:
            backend.set_status("a", "built")
            assert backend.get_status("a") == "built"
