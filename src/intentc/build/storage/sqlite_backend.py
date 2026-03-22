from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from intentc.build.storage.backend import GenerationStatus, StorageBackend

_SCHEMA = """
CREATE TABLE IF NOT EXISTS intent_file_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    source_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    UNIQUE(name, content_hash)
);

CREATE TABLE IF NOT EXISTS validation_file_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target TEXT NOT NULL,
    source_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    UNIQUE(target, source_path, content_hash)
);

CREATE TABLE IF NOT EXISTS generations (
    generation_id TEXT PRIMARY KEY,
    output_dir TEXT NOT NULL,
    profile_name TEXT,
    options_json TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    started_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS generation_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id TEXT NOT NULL REFERENCES generations(generation_id),
    message TEXT NOT NULL,
    logged_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS build_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target TEXT NOT NULL,
    generation_id TEXT,
    intent_version_id INTEGER REFERENCES intent_file_versions(id),
    status TEXT NOT NULL,
    commit_id TEXT NOT NULL DEFAULT '',
    total_duration_secs REAL NOT NULL DEFAULT 0.0,
    timestamp TEXT NOT NULL,
    git_diff TEXT,
    files_created TEXT,
    files_modified TEXT
);

CREATE TABLE IF NOT EXISTS build_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    build_result_id INTEGER NOT NULL REFERENCES build_results(id),
    step_order INTEGER NOT NULL,
    phase TEXT NOT NULL,
    status TEXT NOT NULL,
    duration_secs REAL NOT NULL DEFAULT 0.0,
    summary TEXT NOT NULL DEFAULT '',
    log TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS validation_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    build_result_id INTEGER REFERENCES build_results(id),
    generation_id TEXT NOT NULL REFERENCES generations(generation_id),
    target TEXT NOT NULL,
    validation_file_version_id INTEGER REFERENCES validation_file_versions(id),
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    duration_secs REAL,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    build_result_id INTEGER REFERENCES build_results(id),
    validation_result_id INTEGER REFERENCES validation_results(id),
    response_type TEXT NOT NULL,
    response_json TEXT NOT NULL,
    stored_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS target_state (
    target TEXT NOT NULL,
    output_dir TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    last_build_result_id INTEGER REFERENCES build_results(id),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (target, output_dir)
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteBackend(StorageBackend):
    """Concrete ``StorageBackend`` using Python's built-in ``sqlite3``."""

    def __init__(self, base_dir: Path, output_dir: str) -> None:
        super().__init__(base_dir, output_dir)
        db_dir = base_dir / ".intentc" / "state" / output_dir
        db_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = db_dir / "intentc.db"
        self._conn = sqlite3.connect(
            str(self._db_path), check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._migrate_flat_files(db_dir)

    # -- context manager -----------------------------------------------------

    def __enter__(self) -> SQLiteBackend:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    # -- Generation methods --------------------------------------------------

    def create_generation(
        self,
        generation_id: str,
        output_dir: str,
        profile_name: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO generations (generation_id, output_dir, profile_name, options_json, status, started_at) "
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
            "UPDATE generations SET status = ?, completed_at = ? WHERE generation_id = ?",
            (status.value, _now_iso(), generation_id),
        )
        self._conn.commit()

    def log_generation_event(self, generation_id: str, message: str) -> None:
        self._conn.execute(
            "INSERT INTO generation_logs (generation_id, message, logged_at) VALUES (?, ?, ?)",
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
            "SELECT message, logged_at FROM generation_logs WHERE generation_id = ? ORDER BY id",
            (generation_id,),
        ).fetchall()
        result["logs"] = [dict(log) for log in logs]
        return result

    # -- Intent / validation file version methods ----------------------------

    def record_intent_version(
        self, name: str, source_path: str, content_hash: str
    ) -> int:
        cur = self._conn.execute(
            "SELECT id FROM intent_file_versions WHERE name = ? AND content_hash = ?",
            (name, content_hash),
        )
        row = cur.fetchone()
        if row:
            return row["id"]
        cur = self._conn.execute(
            "INSERT INTO intent_file_versions (name, source_path, content_hash, recorded_at) "
            "VALUES (?, ?, ?, ?)",
            (name, source_path, content_hash, _now_iso()),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def record_validation_version(
        self, target: str, source_path: str, content_hash: str
    ) -> int:
        cur = self._conn.execute(
            "SELECT id FROM validation_file_versions WHERE target = ? AND source_path = ? AND content_hash = ?",
            (target, source_path, content_hash),
        )
        row = cur.fetchone()
        if row:
            return row["id"]
        cur = self._conn.execute(
            "INSERT INTO validation_file_versions (target, source_path, content_hash, recorded_at) "
            "VALUES (?, ?, ?, ?)",
            (target, source_path, content_hash, _now_iso()),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    # -- Build result methods ------------------------------------------------

    def save_build_result(
        self,
        target: str,
        result: Any,
        intent_version_id: int | None = None,
        git_diff: str | None = None,
        files_created: list[str] | None = None,
        files_modified: list[str] | None = None,
    ) -> int:
        # Extract fields from result object (duck-typed: expects .generation_id,
        # .status, .commit_id, .total_duration, .timestamp, .steps attributes).
        generation_id = getattr(result, "generation_id", "")
        status = getattr(result, "status", "")
        if hasattr(status, "value"):
            status = status.value
        commit_id = getattr(result, "commit_id", "")
        total_duration = getattr(result, "total_duration", 0.0)
        if hasattr(total_duration, "total_seconds"):
            total_duration = total_duration.total_seconds()
        timestamp = getattr(result, "timestamp", None)
        if timestamp is not None and hasattr(timestamp, "isoformat"):
            timestamp = timestamp.isoformat()
        elif timestamp is None:
            timestamp = _now_iso()

        cur = self._conn.execute(
            "INSERT INTO build_results "
            "(target, generation_id, intent_version_id, status, commit_id, "
            "total_duration_secs, timestamp, git_diff, files_created, files_modified) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                target,
                generation_id,
                intent_version_id,
                str(status),
                commit_id,
                float(total_duration),
                str(timestamp),
                git_diff,
                json.dumps(files_created) if files_created else None,
                json.dumps(files_modified) if files_modified else None,
            ),
        )
        build_result_id: int = cur.lastrowid  # type: ignore[assignment]

        # Save steps
        steps = getattr(result, "steps", [])
        for i, step in enumerate(steps):
            phase = getattr(step, "phase", "")
            step_status = getattr(step, "status", "")
            duration = getattr(step, "duration", 0.0)
            if hasattr(duration, "total_seconds"):
                duration = duration.total_seconds()
            summary = getattr(step, "summary", "")
            self._conn.execute(
                "INSERT INTO build_steps "
                "(build_result_id, step_order, phase, status, duration_secs, summary, log) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (build_result_id, i, phase, step_status, float(duration), summary, ""),
            )

        # Update target_state
        self._conn.execute(
            "INSERT INTO target_state (target, output_dir, status, last_build_result_id, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(target, output_dir) DO UPDATE SET "
            "status = excluded.status, last_build_result_id = excluded.last_build_result_id, "
            "updated_at = excluded.updated_at",
            (target, self.output_dir, str(status), build_result_id, _now_iso()),
        )
        self._conn.commit()
        return build_result_id

    def get_build_result(self, target: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT br.* FROM build_results br "
            "JOIN target_state ts ON ts.last_build_result_id = br.id "
            "WHERE ts.target = ? AND ts.output_dir = ?",
            (target, self.output_dir),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_build_result(row)

    def get_build_history(self, target: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM build_results WHERE target = ? ORDER BY id DESC LIMIT ?",
            (target, limit),
        ).fetchall()
        return [self._row_to_build_result(r) for r in rows]

    def _row_to_build_result(self, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        build_id = result["id"]
        steps = self._conn.execute(
            "SELECT * FROM build_steps WHERE build_result_id = ? ORDER BY step_order",
            (build_id,),
        ).fetchall()
        result["steps"] = [dict(s) for s in steps]
        if result.get("files_created"):
            result["files_created"] = json.loads(result["files_created"])
        if result.get("files_modified"):
            result["files_modified"] = json.loads(result["files_modified"])
        return result

    # -- Build step methods --------------------------------------------------

    def save_build_step(
        self,
        build_result_id: int,
        step: Any,
        log: str,
        step_order: int,
    ) -> None:
        phase = getattr(step, "phase", "")
        status = getattr(step, "status", "")
        duration = getattr(step, "duration", 0.0)
        if hasattr(duration, "total_seconds"):
            duration = duration.total_seconds()
        summary = getattr(step, "summary", "")
        self._conn.execute(
            "INSERT INTO build_steps "
            "(build_result_id, step_order, phase, status, duration_secs, summary, log) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (build_result_id, step_order, phase, status, float(duration), summary, log),
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
        reason: str,
        duration_secs: float | None = None,
    ) -> int:
        cur = self._conn.execute(
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
        return cur.lastrowid  # type: ignore[return-value]

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

    def get_status(self, target: str) -> str:
        row = self._conn.execute(
            "SELECT status FROM target_state WHERE target = ? AND output_dir = ?",
            (target, self.output_dir),
        ).fetchone()
        if row is None:
            return "pending"
        return row["status"]

    def set_status(self, target: str, status: str) -> None:
        self._conn.execute(
            "INSERT INTO target_state (target, output_dir, status, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(target, output_dir) DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at",
            (target, self.output_dir, status, _now_iso()),
        )
        self._conn.commit()

    def list_targets(self) -> list[tuple[str, str]]:
        rows = self._conn.execute(
            "SELECT target, status FROM target_state WHERE output_dir = ? ORDER BY target",
            (self.output_dir,),
        ).fetchall()
        return [(row["target"], row["status"]) for row in rows]

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

    # -- Migration -----------------------------------------------------------

    def _migrate_flat_files(self, db_dir: Path) -> None:
        """Migrate state.json and build-log.jsonl if they exist."""
        state_file = db_dir / "state.json"
        migrated_marker = db_dir / "state.json.migrated"

        if migrated_marker.exists() or not state_file.exists():
            return

        with open(state_file) as f:
            state_data = json.load(f)

        # Migrate target statuses
        targets = state_data if isinstance(state_data, dict) else {}
        for target_name, target_info in targets.items():
            if isinstance(target_info, dict):
                status = target_info.get("status", "pending")
            elif isinstance(target_info, str):
                status = target_info
            else:
                continue
            self._conn.execute(
                "INSERT OR IGNORE INTO target_state (target, output_dir, status, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (target_name, self.output_dir, status, _now_iso()),
            )

        # Migrate build-log.jsonl if present
        log_file = db_dir / "build-log.jsonl"
        if log_file.exists():
            with open(log_file) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    target_name = entry.get("target", "")
                    if not target_name:
                        continue
                    entry_status = entry.get("status", "built")
                    generation_id = entry.get("generation_id", "")
                    commit_id = entry.get("commit_id", "")
                    total_duration = entry.get("total_duration_secs", 0.0)
                    timestamp = entry.get("timestamp", _now_iso())
                    cur = self._conn.execute(
                        "INSERT INTO build_results "
                        "(target, generation_id, status, commit_id, total_duration_secs, timestamp) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (target_name, generation_id, entry_status, commit_id, total_duration, timestamp),
                    )
                    build_result_id = cur.lastrowid
                    for i, step in enumerate(entry.get("steps", [])):
                        self._conn.execute(
                            "INSERT INTO build_steps "
                            "(build_result_id, step_order, phase, status, duration_secs, summary, log) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (
                                build_result_id,
                                i,
                                step.get("phase", ""),
                                step.get("status", ""),
                                step.get("duration_secs", 0.0),
                                step.get("summary", ""),
                                step.get("log", ""),
                            ),
                        )

        self._conn.commit()
        state_file.rename(migrated_marker)
