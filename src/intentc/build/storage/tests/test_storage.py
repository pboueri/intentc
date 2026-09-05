"""Tests for the StorageBackend interface and SQLiteBackend."""

from __future__ import annotations

import json
import sqlite3
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

TABLES = {
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


@pytest.fixture()
def backend(tmp_path: Path):
    b = SQLiteBackend(tmp_path, "src")
    yield b
    b.close()


def _result(target: str = "models", **overrides) -> BuildResult:
    fields = dict(
        target=target,
        generation_id="gen-1",
        status=TargetStatus.BUILT,
        steps=[
            BuildStep(phase="resolve_deps", status="success", duration_secs=0.1, summary="deps"),
            BuildStep(phase="build", status="success", duration_secs=2.5, summary="built it"),
        ],
        commit_id="abc123",
        total_duration_secs=2.6,
        timestamp="2026-01-01T10:00:00",
        source_hash="h1",
        files_created=["a.py"],
        files_modified=["b.py"],
        attempts=2,
    )
    fields.update(overrides)
    return BuildResult(**fields)


def test_is_storage_backend(backend: SQLiteBackend) -> None:
    assert isinstance(backend, StorageBackend)
    assert backend.db_path == backend.base_dir / ".intentc" / "state" / "src" / "intentc.db"
    assert backend.db_path.exists()


def test_schema_created_with_wal_and_foreign_keys(backend: SQLiteBackend) -> None:
    conn = sqlite3.connect(str(backend.db_path))
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert TABLES <= names
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert backend._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()


def test_generation_status_enum() -> None:
    assert {s.value for s in GenerationStatus} == {"running", "completed", "failed"}


def test_full_roundtrip(backend: SQLiteBackend) -> None:
    backend.create_generation("gen-1", "src", "default", {"force": True})
    iv = backend.record_intent_version("models", "intent/models/models.ic", "hash-a")
    assert backend.record_intent_version("models", "intent/models/models.ic", "hash-a") == iv
    vv = backend.record_validation_version("models", "intent/models/v.icv", "hash-b")

    br_id = backend.save_build_result("models", _result(), intent_version_id=iv, git_diff="diff text")
    backend.save_build_step(br_id, BuildStep(phase="validate", status="success", duration_secs=1.0, summary="ok"), "log", 2)
    backend.save_agent_response(br_id, None, "build", {"status": "success"})
    vr_id = backend.save_validation_result(br_id, "gen-1", "models", vv, "v1", "agent_validation", "error", "pass", "fine", 0.5)
    backend.save_agent_response(None, vr_id, "validation", {"status": "pass"})
    backend.log_generation_event("gen-1", "built models")
    backend.complete_generation("gen-1", GenerationStatus.COMPLETED)

    loaded = backend.get_build_result("models")
    assert loaded is not None
    assert loaded.target == "models"
    assert loaded.generation_id == "gen-1"
    assert loaded.status is TargetStatus.BUILT
    assert [s.phase for s in loaded.steps] == ["resolve_deps", "build", "validate"]
    assert loaded.steps[1].duration_secs == 2.5
    assert loaded.commit_id == "abc123"
    assert loaded.timestamp == "2026-01-01T10:00:00"
    assert loaded.source_hash == "h1"
    assert loaded.files_created == ["a.py"]
    assert loaded.files_modified == ["b.py"]
    assert loaded.attempts == 2
    assert backend.get_build_diff("models") == "diff text"

    gen = backend.get_generation("gen-1")
    assert gen is not None
    assert gen["status"] == "completed"
    assert gen["options"] == {"force": True}
    assert gen["profile_name"] == "default"
    assert [l["message"] for l in gen["logs"]] == ["built models"]
    assert gen["completed_at"] is not None

    vals = backend.get_validation_results("models")
    assert len(vals) == 1
    assert vals[0]["name"] == "v1" and vals[0]["status"] == "pass"

    responses = backend.get_agent_responses(br_id)
    assert [r["response_type"] for r in responses] == ["build", "validation"]
    assert backend.get_generation("nope") is None


def test_survives_reopen(tmp_path: Path) -> None:
    b1 = SQLiteBackend(tmp_path, "src")
    b1.save_build_result("x", _result("x"))
    b1.close()
    b2 = SQLiteBackend(tmp_path, "src")
    assert b2.get_status("x") is TargetStatus.BUILT
    assert b2.get_build_result("x").commit_id == "abc123"
    b2.close()


def test_build_history_is_append_only(backend: SQLiteBackend) -> None:
    backend.save_build_result("m", _result("m", generation_id="g1", status="failed"))
    backend.save_build_result("m", _result("m", generation_id="g2"))
    history = backend.get_build_history("m")
    assert [r.generation_id for r in history] == ["g2", "g1"]
    assert history[1].status is TargetStatus.FAILED
    assert backend.get_build_result("m").generation_id == "g2"
    assert backend.get_status("m") is TargetStatus.BUILT
    assert len(backend.get_build_history("m", limit=1)) == 1


def test_target_state_operations(backend: SQLiteBackend) -> None:
    assert backend.get_status("unknown") is TargetStatus.PENDING
    backend.set_status("a", TargetStatus.OUTDATED)
    backend.set_status("b", TargetStatus.FAILED)
    assert backend.get_status("a") is TargetStatus.OUTDATED
    assert backend.list_targets() == [("a", TargetStatus.OUTDATED), ("b", TargetStatus.FAILED)]
    backend.reset("a")
    assert backend.get_status("a") is TargetStatus.PENDING
    assert backend.list_targets() == [("b", TargetStatus.FAILED)]
    backend.reset_all()
    assert backend.list_targets() == []


def test_set_status_keeps_last_build_result(backend: SQLiteBackend) -> None:
    backend.save_build_result("a", _result("a"))
    backend.set_status("a", TargetStatus.OUTDATED)
    assert backend.get_status("a") is TargetStatus.OUTDATED
    assert backend.get_build_result("a") is not None


def test_output_dirs_are_isolated(tmp_path: Path) -> None:
    a = SQLiteBackend(tmp_path, "src")
    b = SQLiteBackend(tmp_path, "src_go")
    a.set_status("x", TargetStatus.BUILT)
    assert b.get_status("x") is TargetStatus.PENDING
    assert a.db_path != b.db_path
    a.close()
    b.close()


def test_schema_upgrade_adds_missing_columns(tmp_path: Path) -> None:
    db_dir = tmp_path / ".intentc" / "state" / "src"
    db_dir.mkdir(parents=True)
    conn = sqlite3.connect(str(db_dir / "intentc.db"))
    conn.executescript(
        "CREATE TABLE build_results (id INTEGER PRIMARY KEY AUTOINCREMENT, target TEXT NOT NULL, generation_id TEXT, "
        "intent_version_id INTEGER, status TEXT NOT NULL, commit_id TEXT NOT NULL DEFAULT '', "
        "total_duration_secs REAL NOT NULL DEFAULT 0.0, timestamp TEXT NOT NULL, git_diff TEXT, files_created TEXT, files_modified TEXT);"
        "INSERT INTO build_results (target, status, timestamp) VALUES ('old', 'built', '2025-01-01T00:00:00');"
    )
    conn.commit()
    conn.close()
    backend = SQLiteBackend(tmp_path, "src")
    cols = {r["name"] for r in backend._conn.execute("PRAGMA table_info(build_results)")}
    assert {"source_hash", "attempts"} <= cols
    assert backend.get_build_history("old")[0].attempts == 1
    backend.close()


def test_migration_from_flat_files(tmp_path: Path) -> None:
    db_dir = tmp_path / ".intentc" / "state" / "src"
    db_dir.mkdir(parents=True)
    (db_dir / "state.json").write_text(json.dumps({"targets": {"a": {"status": "built"}, "b": "failed"}}))
    (db_dir / "build-log.jsonl").write_text(
        json.dumps({"target": "a", "status": "built", "total_duration_secs": 1.5, "timestamp": "t", "steps": [{"phase": "build", "status": "success"}]}) + "\n"
    )
    backend = SQLiteBackend(tmp_path, "src")
    assert backend.get_status("a") is TargetStatus.BUILT
    assert backend.get_status("b") is TargetStatus.FAILED
    assert (db_dir / "state.json.migrated").exists()
    assert not (db_dir / "state.json").exists()
    history = backend.get_build_history("a")
    assert len(history) == 1 and history[0].steps[0].phase == "build"
    backend.close()
    # Idempotent: reopening does not re-migrate.
    backend2 = SQLiteBackend(tmp_path, "src")
    assert len(backend2.get_build_history("a")) == 1
    backend2.close()


def test_build_result_status_coercion() -> None:
    assert BuildResult(target="x", status="failed").status is TargetStatus.FAILED
    with pytest.raises(ValueError):
        BuildResult(target="x", status="nonsense")


def test_context_manager(tmp_path: Path) -> None:
    with SQLiteBackend(tmp_path, "src") as backend:
        backend.set_status("x", TargetStatus.BUILT)
    with pytest.raises(sqlite3.ProgrammingError):
        backend.get_status("x")


def test_concurrent_writes_from_threads(backend: SQLiteBackend) -> None:
    from concurrent.futures import ThreadPoolExecutor

    backend.create_generation("g", "src")

    def write(i: int) -> int:
        return backend.save_validation_result(None, "g", "t", None, f"v{i}", "agent_validation", "error", "pass", "ok", 0.1)

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(write, range(40)))
    assert len(set(ids)) == 40
