"""SQLite implementation of the StorageBackend interface."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from intentc.build.storage.backend import (
    BuildResult,
    BuildStep,
    GenerationStatus,
    StorageBackend,
    TargetStatus,
)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS intent_file_versions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    source_path   TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    recorded_at   TEXT NOT NULL,
    UNIQUE(name, content_hash)
);

CREATE TABLE IF NOT EXISTS validation_file_versions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    target        TEXT NOT NULL,
    source_path   TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    recorded_at   TEXT NOT NULL,
    UNIQUE(target, source_path, content_hash)
);

CREATE TABLE IF NOT EXISTS generations (
    generation_id  TEXT PRIMARY KEY,
    output_dir     TEXT NOT NULL,
    profile_name   TEXT,
    options_json   TEXT,
    status         TEXT NOT NULL DEFAULT 'running',
    started_at     TEXT NOT NULL,
    completed_at   TEXT
);

CREATE TABLE IF NOT EXISTS generation_logs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id  TEXT NOT NULL REFERENCES generations(generation_id),
    message        TEXT NOT NULL,
    logged_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS build_results (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    target             TEXT NOT NULL,
    generation_id      TEXT,
    intent_version_id  INTEGER REFERENCES intent_file_versions(id),
    status             TEXT NOT NULL,
    commit_id          TEXT NOT NULL DEFAULT '',
    total_duration_secs REAL NOT NULL DEFAULT 0.0,
    timestamp          TEXT NOT NULL,
    git_diff           TEXT,
    files_created      TEXT,
    files_modified     TEXT
);

CREATE TABLE IF NOT EXISTS build_steps (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    build_result_id  INTEGER NOT NULL REFERENCES build_results(id),
    step_order       INTEGER NOT NULL,
    phase            TEXT NOT NULL,
    status           TEXT NOT NULL,
    duration_secs    REAL NOT NULL DEFAULT 0.0,
    summary          TEXT NOT NULL DEFAULT '',
    log              TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS validation_results (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    build_result_id             INTEGER REFERENCES build_results(id),
    generation_id               TEXT NOT NULL REFERENCES generations(generation_id),
    target                      TEXT NOT NULL,
    validation_file_version_id  INTEGER REFERENCES validation_file_versions(id),
    name                        TEXT NOT NULL,
    type                        TEXT NOT NULL,
    severity                    TEXT NOT NULL,
    status                      TEXT NOT NULL,
    reason                      TEXT NOT NULL DEFAULT '',
    duration_secs               REAL,
    timestamp                   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_responses (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    build_result_id       INTEGER REFERENCES build_results(id),
    validation_result_id  INTEGER REFERENCES validation_results(id),
    response_type         TEXT NOT NULL,
    response_json         TEXT NOT NULL,
    stored_at             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS target_state (
    target               TEXT NOT NULL,
    output_dir           TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'pending',
    last_build_result_id INTEGER REFERENCES build_results(id),
    updated_at           TEXT NOT NULL,
    PRIMARY KEY (target, output_dir)
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteBackend(StorageBackend):
    """SQLite-backed storage for intentc build state."""

    def __init__(self, base_dir: Path, output_dir: str) -> None:
        super().__init__(base_dir, output_dir)
        db_dir = base_dir / ".intentc" / "state" / output_dir
        db_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = db_dir / "intentc.db"

        self._conn = sqlite3.connect(
            str(self._db_path), check_same_thread=False
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA_SQL)

        # Migrate from flat-file state if present
        self._migrate_flat_files(db_dir)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SQLiteBackend:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    # -- Migration -----------------------------------------------------------

    def _migrate_flat_files(self, db_dir: Path) -> None:
        state_json = db_dir / "state.json"
        migrated_marker = db_dir / "state.json.migrated"

        if migrated_marker.exists() or not state_json.exists():
            return

        try:
            data = json.loads(state_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return

        now = _now_iso()

        # Migrate target states
        targets = data.get("targets", {})
        for target, info in targets.items():
            if isinstance(info, str):
                status_str = info
            elif isinstance(info, dict):
                status_str = info.get("status", "pending")
            else:
                continue
            try:
                status = TargetStatus(status_str)
            except ValueError:
                status = TargetStatus.PENDING
            self._conn.execute(
                "INSERT OR REPLACE INTO target_state (target, output_dir, status, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (target, self.output_dir, status.value, now),
            )

        # Migrate build log entries if present
        build_log = db_dir / "build-log.jsonl"
        if build_log.exists():
            try:
                for line in build_log.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    target = entry.get("target", "unknown")
                    timestamp = entry.get("timestamp", now)
                    status = entry.get("status", "success")
                    duration = entry.get("total_duration_secs", 0.0)
                    self._conn.execute(
                        "INSERT INTO build_results "
                        "(target, status, total_duration_secs, timestamp) "
                        "VALUES (?, ?, ?, ?)",
                        (target, status, duration, timestamp),
                    )
                    br_id = self._conn.execute(
                        "SELECT last_insert_rowid()"
                    ).fetchone()[0]

                    # Migrate steps if present
                    for i, step_data in enumerate(entry.get("steps", [])):
                        self._conn.execute(
                            "INSERT INTO build_steps "
                            "(build_result_id, step_order, phase, status, "
                            "duration_secs, summary, log) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (
                                br_id,
                                i,
                                step_data.get("phase", ""),
                                step_data.get("status", ""),
                                step_data.get("duration_secs", 0.0),
                                step_data.get("summary", ""),
                                step_data.get("log", ""),
                            ),
                        )
            except OSError:
                pass

        self._conn.commit()
        state_json.rename(migrated_marker)

    # -- Generation methods --------------------------------------------------

    def create_generation(
        self,
        generation_id: str,
        output_dir: str,
        profile_name: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO generations "
            "(generation_id, output_dir, profile_name, options_json, status, started_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                generation_id,
                output_dir,
                profile_name,
                json.dumps(options) if options else None,
                GenerationStatus.RUNNING.value,
                _now_iso(),
            ),
        )
        self._conn.commit()

    def complete_generation(
        self, generation_id: str, status: GenerationStatus
    ) -> None:
        self._conn.execute(
            "UPDATE generations SET status = ?, completed_at = ? "
            "WHERE generation_id = ?",
            (status.value, _now_iso(), generation_id),
        )
        self._conn.commit()

    def log_generation_event(
        self, generation_id: str, message: str
    ) -> None:
        self._conn.execute(
            "INSERT INTO generation_logs (generation_id, message, logged_at) "
            "VALUES (?, ?, ?)",
            (generation_id, message, _now_iso()),
        )
        self._conn.commit()

    def get_generation(self, generation_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM generations WHERE generation_id = ?",
            (generation_id,),
        ).fetchone()
        if row is None:
            return None
        result: dict[str, Any] = dict(row)
        if result.get("options_json"):
            result["options"] = json.loads(result["options_json"])
        else:
            result["options"] = None

        logs = self._conn.execute(
            "SELECT message, logged_at FROM generation_logs "
            "WHERE generation_id = ? ORDER BY id",
            (generation_id,),
        ).fetchall()
        result["logs"] = [dict(l) for l in logs]
        return result

    # -- Intent / validation file version methods ----------------------------

    def record_intent_version(
        self, name: str, source_path: str, content_hash: str
    ) -> int:
        row = self._conn.execute(
            "SELECT id FROM intent_file_versions "
            "WHERE name = ? AND content_hash = ?",
            (name, content_hash),
        ).fetchone()
        if row:
            return row[0]
        self._conn.execute(
            "INSERT INTO intent_file_versions "
            "(name, source_path, content_hash, recorded_at) "
            "VALUES (?, ?, ?, ?)",
            (name, source_path, content_hash, _now_iso()),
        )
        self._conn.commit()
        return self._conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def record_validation_version(
        self, target: str, source_path: str, content_hash: str
    ) -> int:
        row = self._conn.execute(
            "SELECT id FROM validation_file_versions "
            "WHERE target = ? AND source_path = ? AND content_hash = ?",
            (target, source_path, content_hash),
        ).fetchone()
        if row:
            return row[0]
        self._conn.execute(
            "INSERT INTO validation_file_versions "
            "(target, source_path, content_hash, recorded_at) "
            "VALUES (?, ?, ?, ?)",
            (target, source_path, content_hash, _now_iso()),
        )
        self._conn.commit()
        return self._conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # -- Build result methods ------------------------------------------------

    def save_build_result(
        self,
        target: str,
        result: BuildResult,
        intent_version_id: int | None = None,
        git_diff: str | None = None,
        files_created: list[str] | None = None,
        files_modified: list[str] | None = None,
    ) -> int:
        self._conn.execute(
            "INSERT INTO build_results "
            "(target, generation_id, intent_version_id, status, commit_id, "
            "total_duration_secs, timestamp, git_diff, files_created, files_modified) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                target,
                result.generation_id,
                intent_version_id,
                result.status,
                result.commit_id,
                result.total_duration_secs,
                result.timestamp or _now_iso(),
                git_diff,
                json.dumps(files_created) if files_created else None,
                json.dumps(files_modified) if files_modified else None,
            ),
        )
        br_id: int = self._conn.execute(
            "SELECT last_insert_rowid()"
        ).fetchone()[0]

        # Insert steps
        for i, step in enumerate(result.steps):
            self._conn.execute(
                "INSERT INTO build_steps "
                "(build_result_id, step_order, phase, status, duration_secs, summary, log) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (br_id, i, step.phase, step.status, step.duration_secs, step.summary, ""),
            )

        # Update target state
        self._conn.execute(
            "INSERT OR REPLACE INTO target_state "
            "(target, output_dir, status, last_build_result_id, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (target, self.output_dir, result.status, br_id, _now_iso()),
        )
        self._conn.commit()
        return br_id

    def get_build_result(self, target: str) -> BuildResult | None:
        state_row = self._conn.execute(
            "SELECT last_build_result_id FROM target_state "
            "WHERE target = ? AND output_dir = ?",
            (target, self.output_dir),
        ).fetchone()
        if state_row is None or state_row[0] is None:
            return None
        return self._load_build_result(state_row[0])

    def get_build_history(
        self, target: str, limit: int = 50
    ) -> list[BuildResult]:
        rows = self._conn.execute(
            "SELECT id FROM build_results WHERE target = ? "
            "ORDER BY id DESC LIMIT ?",
            (target, limit),
        ).fetchall()
        return [self._load_build_result(r[0]) for r in rows]

    def _load_build_result(self, br_id: int) -> BuildResult:
        row = self._conn.execute(
            "SELECT * FROM build_results WHERE id = ?", (br_id,)
        ).fetchone()
        steps_rows = self._conn.execute(
            "SELECT * FROM build_steps WHERE build_result_id = ? "
            "ORDER BY step_order",
            (br_id,),
        ).fetchall()
        steps = [
            BuildStep(
                phase=s["phase"],
                status=s["status"],
                duration_secs=s["duration_secs"],
                summary=s["summary"],
            )
            for s in steps_rows
        ]
        return BuildResult(
            target=row["target"],
            generation_id=row["generation_id"],
            status=row["status"],
            commit_id=row["commit_id"],
            total_duration_secs=row["total_duration_secs"],
            timestamp=row["timestamp"],
            steps=steps,
        )

    # -- Build step methods --------------------------------------------------

    def save_build_step(
        self,
        build_result_id: int,
        step: BuildStep,
        log: str,
        step_order: int,
    ) -> None:
        self._conn.execute(
            "INSERT INTO build_steps "
            "(build_result_id, step_order, phase, status, duration_secs, summary, log) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                build_result_id,
                step_order,
                step.phase,
                step.status,
                step.duration_secs,
                step.summary,
                log,
            ),
        )
        self._conn.commit()

    # -- Validation result methods -------------------------------------------

    def save_validation_result(
        self,
        build_result_id: int | None,
        generation_id: str,
        target: str,
        validation_file_version_id: int | None,
        name: str,
        type: str,
        severity: str,
        status: str,
        reason: str = "",
        duration_secs: float | None = None,
    ) -> int:
        self._conn.execute(
            "INSERT INTO validation_results "
            "(build_result_id, generation_id, target, validation_file_version_id, "
            "name, type, severity, status, reason, duration_secs, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                build_result_id,
                generation_id,
                target,
                validation_file_version_id,
                name,
                type,
                severity,
                status,
                reason,
                duration_secs,
                _now_iso(),
            ),
        )
        self._conn.commit()
        return self._conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # -- Agent response methods ----------------------------------------------

    def save_agent_response(
        self,
        build_result_id: int | None,
        validation_result_id: int | None,
        response_type: str,
        response_json: dict[str, Any],
    ) -> None:
        self._conn.execute(
            "INSERT INTO agent_responses "
            "(build_result_id, validation_result_id, response_type, response_json, stored_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                build_result_id,
                validation_result_id,
                response_type,
                json.dumps(response_json),
                _now_iso(),
            ),
        )
        self._conn.commit()

    # -- Target state methods ------------------------------------------------

    def get_status(self, target: str) -> TargetStatus:
        row = self._conn.execute(
            "SELECT status FROM target_state "
            "WHERE target = ? AND output_dir = ?",
            (target, self.output_dir),
        ).fetchone()
        if row is None:
            return TargetStatus.PENDING
        try:
            return TargetStatus(row[0])
        except ValueError:
            return TargetStatus.PENDING

    def set_status(self, target: str, status: TargetStatus) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO target_state "
            "(target, output_dir, status, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (target, self.output_dir, status.value, _now_iso()),
        )
        self._conn.commit()

    def list_targets(self) -> list[tuple[str, TargetStatus]]:
        rows = self._conn.execute(
            "SELECT target, status FROM target_state WHERE output_dir = ? "
            "ORDER BY target",
            (self.output_dir,),
        ).fetchall()
        result: list[tuple[str, TargetStatus]] = []
        for r in rows:
            try:
                s = TargetStatus(r[1])
            except ValueError:
                s = TargetStatus.PENDING
            result.append((r[0], s))
        return result

    def reset(self, target: str) -> None:
        self._conn.execute(
            "DELETE FROM target_state WHERE target = ? AND output_dir = ?",
            (target, self.output_dir),
        )
        self._conn.commit()

    def reset_all(self) -> None:
        self._conn.execute(
            "DELETE FROM target_state WHERE output_dir = ?",
            (self.output_dir,),
        )
        self._conn.commit()
