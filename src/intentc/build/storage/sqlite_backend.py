"""SQLite implementation of StorageBackend (stdlib sqlite3, no extra dependencies)."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from intentc.build.storage.backend import (
    BuildResult,
    BuildStep,
    GenerationStatus,
    StorageBackend,
    TargetStatus,
)

_SCHEMA = """
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
    files_modified     TEXT,
    source_hash        TEXT NOT NULL DEFAULT '',
    attempts           INTEGER NOT NULL DEFAULT 1
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

# Columns added after the initial schema; applied with ALTER TABLE when missing.
_ADDED_COLUMNS: list[tuple[str, str, str]] = [
    ("build_results", "source_hash", "TEXT NOT NULL DEFAULT ''"),
    ("build_results", "attempts", "INTEGER NOT NULL DEFAULT 1"),
]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _locked(method):
    """Run a backend method under the instance lock."""

    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    wrapper.__name__ = method.__name__
    wrapper.__doc__ = method.__doc__
    return wrapper


class SQLiteBackend(StorageBackend):
    """Persist build state in ``.intentc/state/{output_dir}/intentc.db``."""

    def __init__(self, base_dir: Path, output_dir: str) -> None:
        super().__init__(base_dir, output_dir)
        self.db_dir = self.base_dir / ".intentc" / "state" / output_dir.strip("/")
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.db_dir / "intentc.db"
        # One connection per backend, shared across threads (validations run in a thread
        # pool), so every statement runs under a re-entrant lock.
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._upgrade_schema()
        self._migrate_flat_files()

    # -- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SQLiteBackend:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _upgrade_schema(self) -> None:
        for table, column, decl in _ADDED_COLUMNS:
            existing = {row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        self._conn.commit()

    # -- migration from flat files ------------------------------------------

    def _migrate_flat_files(self) -> None:
        state_json = self.db_dir / "state.json"
        marker = self.db_dir / "state.json.migrated"
        if marker.exists() or not state_json.exists():
            return
        try:
            data = json.loads(state_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        now = _now()
        targets = data.get("targets", data) if isinstance(data, dict) else {}
        for target, info in targets.items():
            status_str = info if isinstance(info, str) else (info or {}).get("status", "pending")
            try:
                status = TargetStatus(status_str)
            except ValueError:
                status = TargetStatus.PENDING
            self._conn.execute(
                "INSERT OR REPLACE INTO target_state (target, output_dir, status, updated_at) VALUES (?, ?, ?, ?)",
                (target, self.output_dir, status.value, now),
            )
        build_log = self.db_dir / "build-log.jsonl"
        if build_log.exists():
            for line in build_log.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cur = self._conn.execute(
                    "INSERT INTO build_results (target, generation_id, status, commit_id, total_duration_secs, timestamp) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        entry.get("target", "unknown"),
                        entry.get("generation_id"),
                        entry.get("status", "built"),
                        entry.get("commit_id", ""),
                        float(entry.get("total_duration_secs", 0.0) or 0.0),
                        entry.get("timestamp", now),
                    ),
                )
                for i, step in enumerate(entry.get("steps", [])):
                    self._conn.execute(
                        "INSERT INTO build_steps (build_result_id, step_order, phase, status, duration_secs, summary, log) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            cur.lastrowid,
                            i,
                            step.get("phase", ""),
                            step.get("status", ""),
                            float(step.get("duration_secs", 0.0) or 0.0),
                            step.get("summary", ""),
                            step.get("log", ""),
                        ),
                    )
        self._conn.commit()
        state_json.rename(marker)

    # -- generations ---------------------------------------------------------

    @_locked
    def create_generation(
        self,
        generation_id: str,
        output_dir: str,
        profile_name: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO generations (generation_id, output_dir, profile_name, options_json, status, started_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                generation_id,
                output_dir,
                profile_name,
                json.dumps(options) if options is not None else None,
                GenerationStatus.RUNNING.value,
                _now(),
            ),
        )
        self._conn.commit()

    @_locked
    def complete_generation(self, generation_id: str, status: GenerationStatus) -> None:
        self._conn.execute(
            "UPDATE generations SET status = ?, completed_at = ? WHERE generation_id = ?",
            (GenerationStatus(status).value, _now(), generation_id),
        )
        self._conn.commit()

    @_locked
    def log_generation_event(self, generation_id: str, message: str) -> None:
        self._conn.execute(
            "INSERT INTO generation_logs (generation_id, message, logged_at) VALUES (?, ?, ?)",
            (generation_id, message, _now()),
        )
        self._conn.commit()

    @_locked
    def get_generation(self, generation_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM generations WHERE generation_id = ?", (generation_id,)
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["options"] = json.loads(result["options_json"]) if result.get("options_json") else None
        result["logs"] = [
            dict(r)
            for r in self._conn.execute(
                "SELECT message, logged_at FROM generation_logs WHERE generation_id = ? ORDER BY id",
                (generation_id,),
            )
        ]
        return result

    # -- file versions -------------------------------------------------------

    @_locked
    def record_intent_version(self, name: str, source_path: str, content_hash: str) -> int:
        row = self._conn.execute(
            "SELECT id FROM intent_file_versions WHERE name = ? AND content_hash = ?",
            (name, content_hash),
        ).fetchone()
        if row is not None:
            return int(row["id"])
        cur = self._conn.execute(
            "INSERT INTO intent_file_versions (name, source_path, content_hash, recorded_at) VALUES (?, ?, ?, ?)",
            (name, source_path, content_hash, _now()),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    @_locked
    def record_validation_version(self, target: str, source_path: str, content_hash: str) -> int:
        row = self._conn.execute(
            "SELECT id FROM validation_file_versions WHERE target = ? AND source_path = ? AND content_hash = ?",
            (target, source_path, content_hash),
        ).fetchone()
        if row is not None:
            return int(row["id"])
        cur = self._conn.execute(
            "INSERT INTO validation_file_versions (target, source_path, content_hash, recorded_at) VALUES (?, ?, ?, ?)",
            (target, source_path, content_hash, _now()),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    # -- build results -------------------------------------------------------

    @_locked
    def save_build_result(
        self,
        target: str,
        result: BuildResult,
        intent_version_id: int | None = None,
        git_diff: str | None = None,
        files_created: list[str] | None = None,
        files_modified: list[str] | None = None,
    ) -> int:
        created = files_created if files_created is not None else result.files_created
        modified = files_modified if files_modified is not None else result.files_modified
        cur = self._conn.execute(
            "INSERT INTO build_results (target, generation_id, intent_version_id, status, commit_id, "
            "total_duration_secs, timestamp, git_diff, files_created, files_modified, source_hash, attempts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                target,
                result.generation_id or None,
                intent_version_id,
                result.status.value,
                result.commit_id,
                result.total_duration_secs,
                result.timestamp or _now(),
                git_diff,
                json.dumps(created),
                json.dumps(modified),
                result.source_hash,
                result.attempts,
            ),
        )
        build_result_id = int(cur.lastrowid)
        for order, step in enumerate(result.steps):
            self._conn.execute(
                "INSERT INTO build_steps (build_result_id, step_order, phase, status, duration_secs, summary, log) "
                "VALUES (?, ?, ?, ?, ?, ?, '')",
                (build_result_id, order, step.phase, step.status, step.duration_secs, step.summary),
            )
        self._conn.execute(
            "INSERT OR REPLACE INTO target_state (target, output_dir, status, last_build_result_id, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (target, self.output_dir, result.status.value, build_result_id, _now()),
        )
        self._conn.commit()
        return build_result_id

    def _load_result(self, row: sqlite3.Row) -> BuildResult:
        steps = [
            BuildStep(
                phase=s["phase"],
                status=s["status"],
                duration_secs=float(s["duration_secs"] or 0.0),
                summary=s["summary"] or "",
            )
            for s in self._conn.execute(
                "SELECT * FROM build_steps WHERE build_result_id = ? ORDER BY step_order", (row["id"],)
            )
        ]
        return BuildResult(
            target=row["target"],
            generation_id=row["generation_id"] or "",
            status=TargetStatus(row["status"]),
            steps=steps,
            commit_id=row["commit_id"] or "",
            total_duration_secs=float(row["total_duration_secs"] or 0.0),
            timestamp=row["timestamp"] or "",
            source_hash=row["source_hash"] or "",
            files_created=json.loads(row["files_created"]) if row["files_created"] else [],
            files_modified=json.loads(row["files_modified"]) if row["files_modified"] else [],
            attempts=int(row["attempts"] or 1),
        )

    def _latest_result_row(self, target: str) -> sqlite3.Row | None:
        state = self._conn.execute(
            "SELECT last_build_result_id FROM target_state WHERE target = ? AND output_dir = ?",
            (target, self.output_dir),
        ).fetchone()
        if state is None or state["last_build_result_id"] is None:
            return None
        return self._conn.execute(
            "SELECT * FROM build_results WHERE id = ?", (state["last_build_result_id"],)
        ).fetchone()

    @_locked
    def get_build_result(self, target: str) -> BuildResult | None:
        row = self._latest_result_row(target)
        return self._load_result(row) if row is not None else None

    @_locked
    def get_build_history(self, target: str, limit: int = 50) -> list[BuildResult]:
        rows = self._conn.execute(
            "SELECT * FROM build_results WHERE target = ? ORDER BY id DESC LIMIT ?", (target, limit)
        ).fetchall()
        return [self._load_result(r) for r in rows]

    @_locked
    def get_build_diff(self, target: str) -> str | None:
        row = self._latest_result_row(target)
        return row["git_diff"] if row is not None else None

    @_locked
    def get_validation_results(
        self, target: str, build_result_id: int | None = None
    ) -> list[dict[str, Any]]:
        generation_id = None
        if build_result_id is None:
            row = self._latest_result_row(target)
            if row is None:
                return []
            build_result_id = int(row["id"])
            generation_id = row["generation_id"]
        rows = self._conn.execute(
            "SELECT name, type, severity, status, reason, duration_secs, timestamp FROM validation_results "
            "WHERE target = ? AND (build_result_id = ? OR (generation_id = ? AND ? IS NOT NULL)) ORDER BY id DESC",
            (target, build_result_id, generation_id, generation_id),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- build steps ---------------------------------------------------------

    @_locked
    def save_build_step(self, build_result_id: int, step: BuildStep, log: str, step_order: int) -> None:
        self._conn.execute(
            "INSERT INTO build_steps (build_result_id, step_order, phase, status, duration_secs, summary, log) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (build_result_id, step_order, step.phase, step.status, step.duration_secs, step.summary, log),
        )
        self._conn.commit()

    # -- validation results --------------------------------------------------

    @_locked
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
        cur = self._conn.execute(
            "INSERT INTO validation_results (build_result_id, generation_id, target, validation_file_version_id, "
            "name, type, severity, status, reason, duration_secs, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                _now(),
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    # -- agent responses -----------------------------------------------------

    @_locked
    def save_agent_response(
        self,
        build_result_id: int | None,
        validation_result_id: int | None,
        response_type: str,
        response_json: dict[str, Any],
    ) -> None:
        self._conn.execute(
            "INSERT INTO agent_responses (build_result_id, validation_result_id, response_type, response_json, stored_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (build_result_id, validation_result_id, response_type, json.dumps(response_json), _now()),
        )
        self._conn.commit()

    @_locked
    def get_agent_responses(self, build_result_id: int) -> list[dict[str, Any]]:
        """Stored raw responses for a build result (build and validation responses)."""
        rows = self._conn.execute(
            "SELECT response_type, response_json, stored_at FROM agent_responses WHERE build_result_id = ? "
            "OR validation_result_id IN (SELECT id FROM validation_results WHERE build_result_id = ?) ORDER BY id",
            (build_result_id, build_result_id),
        ).fetchall()
        return [
            {"response_type": r["response_type"], "response": json.loads(r["response_json"]), "stored_at": r["stored_at"]}
            for r in rows
        ]

    # -- target state --------------------------------------------------------

    @_locked
    def get_status(self, target: str) -> TargetStatus:
        row = self._conn.execute(
            "SELECT status FROM target_state WHERE target = ? AND output_dir = ?", (target, self.output_dir)
        ).fetchone()
        if row is None:
            return TargetStatus.PENDING
        try:
            return TargetStatus(row["status"])
        except ValueError:
            return TargetStatus.PENDING

    @_locked
    def set_status(self, target: str, status: TargetStatus) -> None:
        status = TargetStatus(status)
        self._conn.execute(
            "INSERT INTO target_state (target, output_dir, status, last_build_result_id, updated_at) "
            "VALUES (?, ?, ?, NULL, ?) "
            "ON CONFLICT(target, output_dir) DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at",
            (target, self.output_dir, status.value, _now()),
        )
        self._conn.commit()

    @_locked
    def list_targets(self) -> list[tuple[str, TargetStatus]]:
        rows = self._conn.execute(
            "SELECT target, status FROM target_state WHERE output_dir = ? ORDER BY target", (self.output_dir,)
        ).fetchall()
        return [(r["target"], TargetStatus(r["status"])) for r in rows]

    @_locked
    def reset(self, target: str) -> None:
        self._conn.execute(
            "DELETE FROM target_state WHERE target = ? AND output_dir = ?", (target, self.output_dir)
        )
        self._conn.commit()

    @_locked
    def reset_all(self) -> None:
        self._conn.execute("DELETE FROM target_state WHERE output_dir = ?", (self.output_dir,))
        self._conn.commit()
