"""Tests for intentc.build.storage."""

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


def make_backend(tmp_path: Path, output_dir: str = "src") -> SQLiteBackend:
    return SQLiteBackend(base_dir=tmp_path, output_dir=output_dir)


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------


def test_storage_backend_is_abstract_and_backend_agnostic():
    assert StorageBackend.__abstractmethods__
    with pytest.raises(TypeError):
        StorageBackend(base_dir=Path("."), output_dir="src")


def test_sqlite_backend_is_a_storage_backend(tmp_path):
    backend = make_backend(tmp_path)
    try:
        assert isinstance(backend, StorageBackend)
    finally:
        backend.close()


# ---------------------------------------------------------------------------
# Schema creation
# ---------------------------------------------------------------------------


def test_construction_creates_database_and_all_tables(tmp_path):
    backend = make_backend(tmp_path)
    try:
        db_path = tmp_path / ".intentc" / "state" / "src" / "intentc.db"
        assert db_path.is_file()

        raw = sqlite3.connect(str(db_path))
        try:
            rows = raw.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            table_names = {row[0] for row in rows}
            assert EXPECTED_TABLES.issubset(table_names)

            journal_mode = raw.execute("PRAGMA journal_mode").fetchone()[0]
            assert journal_mode.lower() == "wal"

            foreign_keys = backend._conn.execute("PRAGMA foreign_keys").fetchone()[0]
            assert foreign_keys == 1
        finally:
            raw.close()
    finally:
        backend.close()


def test_construction_is_idempotent_on_existing_database(tmp_path):
    backend_a = make_backend(tmp_path)
    backend_a.set_status("feature/a", TargetStatus.BUILT)
    backend_a.close()

    backend_b = make_backend(tmp_path)
    try:
        assert backend_b.get_status("feature/a") == TargetStatus.BUILT
    finally:
        backend_b.close()


# ---------------------------------------------------------------------------
# Schema upgrade
# ---------------------------------------------------------------------------


def test_schema_upgrade_adds_missing_columns_and_preserves_rows(tmp_path):
    db_dir = tmp_path / ".intentc" / "state" / "src"
    db_dir.mkdir(parents=True)
    db_path = db_dir / "intentc.db"

    legacy_conn = sqlite3.connect(str(db_path))
    try:
        legacy_conn.executescript(
            """
            CREATE TABLE build_results (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                target             TEXT NOT NULL,
                generation_id      TEXT,
                intent_version_id  INTEGER,
                status             TEXT NOT NULL,
                commit_id          TEXT NOT NULL DEFAULT '',
                total_duration_secs REAL NOT NULL DEFAULT 0.0,
                timestamp          TEXT NOT NULL,
                git_diff           TEXT,
                files_created      TEXT,
                files_modified     TEXT
            );
            """
        )
        legacy_conn.execute(
            "INSERT INTO build_results (target, status, timestamp) VALUES (?, ?, ?)",
            ("legacy/target", "built", "2020-01-01T00:00:00"),
        )
        legacy_conn.commit()

        columns_before = {row[1] for row in legacy_conn.execute("PRAGMA table_info(build_results)")}
        assert "source_hash" not in columns_before
        assert "attempts" not in columns_before
    finally:
        legacy_conn.close()

    backend = make_backend(tmp_path)
    try:
        columns_after = {row["name"] for row in backend._conn.execute("PRAGMA table_info(build_results)")}
        assert "source_hash" in columns_after
        assert "attempts" in columns_after

        row = backend._conn.execute(
            "SELECT target, status, source_hash, attempts FROM build_results WHERE target=?",
            ("legacy/target",),
        ).fetchone()
        assert row["target"] == "legacy/target"
        assert row["status"] == "built"
        assert row["source_hash"] == ""
        assert row["attempts"] == 1
    finally:
        backend.close()


# ---------------------------------------------------------------------------
# Full roundtrip
# ---------------------------------------------------------------------------


def test_full_storage_roundtrip(tmp_path):
    backend = make_backend(tmp_path)
    try:
        generation_id = "gen-roundtrip-1"
        backend.create_generation(
            generation_id, output_dir="src", profile_name="claude-default", options={"agents": 2}
        )

        intent_version_id = backend.record_intent_version(
            "build/storage", "intent/build/storage/storage.ic", "hash-abc123"
        )
        assert isinstance(intent_version_id, int)
        # Idempotent: same hash returns the same id.
        assert backend.record_intent_version(
            "build/storage", "intent/build/storage/storage.ic", "hash-abc123"
        ) == intent_version_id

        result = BuildResult(
            target="build/storage",
            generation_id=generation_id,
            status=TargetStatus.BUILT,
            steps=[
                BuildStep(phase="resolve_deps", status="success", duration_secs=0.1, summary="resolved"),
                BuildStep(phase="build", status="success", duration_secs=4.2, summary="built module"),
            ],
            commit_id="deadbeef",
            total_duration_secs=4.3,
            timestamp="2026-09-05T10:00:00",
            source_hash="hash-abc123",
            files_created=["a.py", "b.py"],
            files_modified=["__init__.py"],
            attempts=2,
        )
        build_result_id = backend.save_build_result(
            "build/storage",
            result,
            intent_version_id=intent_version_id,
            git_diff="diff --git a/a.py b/a.py\n+content",
        )
        assert isinstance(build_result_id, int)

        # A caller with real agent/command output for a step attaches it via
        # save_build_step directly, alongside the steps already inserted from
        # result.steps.
        backend.save_build_step(
            build_result_id,
            BuildStep(phase="validate", status="success", duration_secs=0.8, summary="ran validations"),
            log="running pytest...\n16 passed in 0.42s\n",
            step_order=2,
        )

        backend.save_agent_response(
            build_result_id=build_result_id,
            validation_result_id=None,
            response_type="build",
            response_json={"status": "success", "summary": "did it"},
        )

        validation_result_id = backend.save_validation_result(
            build_result_id=build_result_id,
            generation_id=generation_id,
            target="build/storage",
            validation_file_version_id=None,
            name="storage-tests-pass",
            type="command_validation",
            severity="error",
            status="pass",
            reason="all tests passed",
            duration_secs=1.5,
        )
        assert isinstance(validation_result_id, int)

        backend.save_agent_response(
            build_result_id=None,
            validation_result_id=validation_result_id,
            response_type="validation",
            response_json={"name": "storage-tests-pass", "status": "pass"},
        )

        backend.log_generation_event(generation_id, "started build for build/storage")
        backend.log_generation_event(generation_id, "finished build for build/storage")
        backend.complete_generation(generation_id, GenerationStatus.COMPLETED)

        # -- Read everything back --

        generation = backend.get_generation(generation_id)
        assert generation is not None
        assert generation["status"] == GenerationStatus.COMPLETED.value
        assert generation["profile_name"] == "claude-default"
        assert generation["options"] == {"agents": 2}
        assert [log["message"] for log in generation["logs"]] == [
            "started build for build/storage",
            "finished build for build/storage",
        ]

        fetched_result = backend.get_build_result("build/storage")
        assert fetched_result is not None
        assert fetched_result.target == "build/storage"
        assert fetched_result.generation_id == generation_id
        assert fetched_result.status == TargetStatus.BUILT
        assert fetched_result.commit_id == "deadbeef"
        assert fetched_result.total_duration_secs == 4.3
        assert fetched_result.timestamp == "2026-09-05T10:00:00"
        assert fetched_result.source_hash == "hash-abc123"
        assert fetched_result.files_created == ["a.py", "b.py"]
        assert fetched_result.files_modified == ["__init__.py"]
        assert fetched_result.attempts == 2
        assert [step.phase for step in fetched_result.steps] == ["resolve_deps", "build", "validate"]
        assert fetched_result.steps[1].summary == "built module"
        assert fetched_result.steps[2].summary == "ran validations"

        # The log text passed to save_build_step is not part of the BuildStep
        # model (see intent/build/storage), so it's verified against the raw
        # table rather than through get_build_result.
        step_logs = backend._conn.execute(
            "SELECT phase, log FROM build_steps WHERE build_result_id=? ORDER BY step_order",
            (build_result_id,),
        ).fetchall()
        assert [row["log"] for row in step_logs[:2]] == ["", ""]
        assert step_logs[2]["phase"] == "validate"
        assert step_logs[2]["log"] == "running pytest...\n16 passed in 0.42s\n"

        history = backend.get_build_history("build/storage")
        assert len(history) == 1
        assert history[0].target == "build/storage"

        diff = backend.get_build_diff("build/storage")
        assert diff == "diff --git a/a.py b/a.py\n+content"

        validations = backend.get_validation_results("build/storage")
        assert len(validations) == 1
        assert validations[0]["name"] == "storage-tests-pass"
        assert validations[0]["status"] == "pass"
        assert validations[0]["reason"] == "all tests passed"

        raw_responses = backend._conn.execute(
            "SELECT response_type, response_json FROM agent_responses ORDER BY id"
        ).fetchall()
        assert len(raw_responses) == 2
        assert json.loads(raw_responses[0]["response_json"])["status"] == "success"
        assert json.loads(raw_responses[1]["response_json"])["name"] == "storage-tests-pass"
    finally:
        backend.close()


def test_get_validation_results_falls_back_to_generation_id(tmp_path):
    backend = make_backend(tmp_path)
    try:
        generation_id = "gen-shared-1"
        backend.create_generation(generation_id, output_dir="src")
        # Validation recorded before the build result row exists (during a build).
        backend.save_validation_result(
            build_result_id=None,
            generation_id=generation_id,
            target="build/storage",
            validation_file_version_id=None,
            name="mid-build-check",
            type="agent_validation",
            severity="error",
            status="pass",
            reason="looks fine",
            duration_secs=None,
        )
        result = BuildResult(target="build/storage", generation_id=generation_id, status=TargetStatus.BUILT)
        backend.save_build_result("build/storage", result)

        validations = backend.get_validation_results("build/storage")
        assert [v["name"] for v in validations] == ["mid-build-check"]
    finally:
        backend.close()


# ---------------------------------------------------------------------------
# Target state management
# ---------------------------------------------------------------------------


def test_get_status_returns_pending_for_unknown_target(tmp_path):
    backend = make_backend(tmp_path)
    try:
        assert backend.get_status("never/built") == TargetStatus.PENDING
    finally:
        backend.close()


def test_set_status_and_get_status_roundtrip(tmp_path):
    backend = make_backend(tmp_path)
    try:
        backend.set_status("build/storage", TargetStatus.BUILT)
        assert backend.get_status("build/storage") == TargetStatus.BUILT

        backend.set_status("build/storage", TargetStatus.OUTDATED)
        assert backend.get_status("build/storage") == TargetStatus.OUTDATED
    finally:
        backend.close()


def test_list_targets_returns_all_tracked_targets(tmp_path):
    backend = make_backend(tmp_path)
    try:
        backend.set_status("build/storage", TargetStatus.BUILT)
        backend.set_status("build/state", TargetStatus.PENDING)
        backend.set_status("core/project", TargetStatus.FAILED)

        targets = dict(backend.list_targets())
        assert targets == {
            "build/storage": TargetStatus.BUILT,
            "build/state": TargetStatus.PENDING,
            "core/project": TargetStatus.FAILED,
        }
    finally:
        backend.close()


def test_reset_clears_single_target_without_affecting_others(tmp_path):
    backend = make_backend(tmp_path)
    try:
        backend.set_status("build/storage", TargetStatus.BUILT)
        backend.set_status("build/state", TargetStatus.BUILT)

        backend.reset("build/storage")

        assert backend.get_status("build/storage") == TargetStatus.PENDING
        assert backend.get_status("build/state") == TargetStatus.BUILT
        assert dict(backend.list_targets()) == {"build/state": TargetStatus.BUILT}
    finally:
        backend.close()


def test_reset_all_clears_every_target(tmp_path):
    backend = make_backend(tmp_path)
    try:
        backend.set_status("build/storage", TargetStatus.BUILT)
        backend.set_status("build/state", TargetStatus.BUILT)

        backend.reset_all()

        assert backend.list_targets() == []
        assert backend.get_status("build/storage") == TargetStatus.PENDING
        assert backend.get_status("build/state") == TargetStatus.PENDING
    finally:
        backend.close()


def test_reset_all_is_scoped_to_its_own_output_dir(tmp_path):
    backend_src = make_backend(tmp_path, output_dir="src")
    backend_go = make_backend(tmp_path, output_dir="src_go")
    try:
        backend_src.set_status("build/storage", TargetStatus.BUILT)
        backend_go.set_status("build/storage", TargetStatus.BUILT)

        backend_go.reset_all()

        assert backend_go.list_targets() == []
        assert backend_src.get_status("build/storage") == TargetStatus.BUILT
    finally:
        backend_src.close()
        backend_go.close()


# ---------------------------------------------------------------------------
# Migration from flat files
# ---------------------------------------------------------------------------


def test_migration_from_flat_files_populates_db_and_renames_state_file(tmp_path):
    db_dir = tmp_path / ".intentc" / "state" / "src"
    db_dir.mkdir(parents=True)

    state_path = db_dir / "state.json"
    state_path.write_text(
        json.dumps({"targets": {"build/storage": {"status": "built"}, "build/state": {"status": "pending"}}}),
        encoding="utf-8",
    )

    build_log_path = db_dir / "build-log.jsonl"
    log_entry = {
        "target": "build/storage",
        "generation_id": "gen-legacy-1",
        "status": "built",
        "steps": [{"phase": "build", "status": "success", "duration_secs": 2.0, "summary": "ok"}],
        "commit_id": "cafef00d",
        "total_duration_secs": 2.0,
        "timestamp": "2026-01-01T00:00:00",
        "source_hash": "legacy-hash",
        "files_created": ["x.py"],
        "files_modified": [],
        "attempts": 1,
    }
    build_log_path.write_text(json.dumps(log_entry) + "\n", encoding="utf-8")

    backend = make_backend(tmp_path)
    try:
        assert not state_path.exists()
        assert (db_dir / "state.json.migrated").is_file()

        assert backend.get_status("build/storage") == TargetStatus.BUILT
        assert backend.get_status("build/state") == TargetStatus.PENDING

        migrated_result = backend.get_build_result("build/storage")
        assert migrated_result is not None
        assert migrated_result.commit_id == "cafef00d"
        assert migrated_result.source_hash == "legacy-hash"
        assert migrated_result.files_created == ["x.py"]
        assert len(migrated_result.steps) == 1
        assert migrated_result.steps[0].phase == "build"
    finally:
        backend.close()


def test_migration_is_idempotent(tmp_path):
    db_dir = tmp_path / ".intentc" / "state" / "src"
    db_dir.mkdir(parents=True)
    state_path = db_dir / "state.json"
    state_path.write_text(
        json.dumps({"targets": {"build/storage": {"status": "built"}}}), encoding="utf-8"
    )

    backend_a = make_backend(tmp_path)
    backend_a.close()
    assert not state_path.exists()

    # A stray state.json reappearing (e.g. from an old backup) must not be
    # re-migrated once the .migrated marker exists.
    state_path.write_text(
        json.dumps({"targets": {"build/storage": {"status": "outdated"}}}), encoding="utf-8"
    )
    backend_b = make_backend(tmp_path)
    try:
        assert backend_b.get_status("build/storage") == TargetStatus.BUILT
        assert state_path.exists()
    finally:
        backend_b.close()


def test_no_migration_when_no_state_json_present(tmp_path):
    backend = make_backend(tmp_path)
    try:
        db_dir = tmp_path / ".intentc" / "state" / "src"
        assert not (db_dir / "state.json.migrated").exists()
        assert backend.list_targets() == []
    finally:
        backend.close()
