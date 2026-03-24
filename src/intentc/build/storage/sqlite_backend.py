"""SQLite implementation of StorageBackend."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

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
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    target              TEXT NOT NULL,
    generation_id       TEXT,
    intent_version_id   INTEGER REFERENCES intent_file_versions(id),
    status              TEXT NOT NULL,
    commit_id           TEXT NOT NULL DEFAULT '',
    total_duration_secs REAL NOT NULL DEFAULT 0.0,
    timestamp           TEXT NOT NULL,
    git_diff            TEXT,
    files_created       TEXT,
    files_modified      TEXT
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteBackend(StorageBackend):
    """SQLite-backed storage for build state."""

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
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()
        self._migrate_flat_files(db_dir)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SQLiteBackend:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- Migration ---

    def _migrate_flat_files(self, db_dir: Path) -> None:
        state_file = db_dir / "state.json"
        migrated_marker = db_dir / "state.json.migrated"
        if migrated_marker.exists() or not state_file.exists():
            return
        try:
            data = json.loads(state_file.read_text())
        except (json.JSONDecodeError, OSError):
            return

        now = _now()
        # Migrate target states
        targets = data if isinstance(data, dict) else {}
        for target_name, info in targets.items():
            if isinstance(info, dict):
                status = info.get("status", "pending")
            elif isinstance(info, str):
                status = info
            else:
                continue
            self._conn.execute(
                """INSERT OR REPLACE INTO target_state
                   (target, output_dir, status, updated_at)
                   VALUES (?, ?, ?, ?)""",
                (target_name, self.output_dir, status, now),
            )

        # Migrate build log if present
        log_file = db_dir / "build-log.jsonl"
        if log_file.exists():
            try:
                for line in log_file.read_text().splitlines():
                    if not line.strip():
                        continue
                    entry = json.loads(line)
                    target_name = entry.get("target", "")
                    timestamp = entry.get("timestamp", now)
                    status = entry.get("status", "unknown")
                    self._conn.execute(
                        """INSERT INTO build_results
                           (target, status, timestamp)
                           VALUES (?, ?, ?)""",
                        (target_name, status, timestamp),
                    )
                    br_id = self._conn.execute(
                        "SELECT last_insert_rowid()"
                    ).fetchone()[0]
                    # Migrate steps if present
                    for i, step in enumerate(entry.get("steps", [])):
                        self._conn.execute(
                            """INSERT INTO build_steps
                               (build_result_id, step_order, phase, status,
                                duration_secs, summary)
                               VALUES (?, ?, ?, ?, ?, ?)""",
                            (
                                br_id,
                                i,
                                step.get("phase", ""),
                                step.get("status", ""),
                                step.get("duration_secs", 0.0),
                                step.get("summary", ""),
                            ),
                        )
            except (json.JSONDecodeError, OSError):
                pass

        self._conn.commit()
        state_file.rename(migrated_marker)

    # --- Generation methods ---

    def create_generation(
        self,
        generation_id: str,
        output_dir: str,
        profile_name: str | None = None,
        options: dict | None = None,
    ) -> None:
        self._conn.execute(
            """INSERT INTO generations
               (generation_id, output_dir, profile_name, options_json,
                status, started_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                generation_id,
                output_dir,
                profile_name,
                json.dumps(options) if options else None,
                GenerationStatus.RUNNING.value,
                _now(),
            ),
        )
        self._conn.commit()

    def complete_generation(
        self, generation_id: str, status: GenerationStatus
    ) -> None:
        self._conn.execute(
            """UPDATE generations
               SET status = ?, completed_at = ?
               WHERE generation_id = ?""",
            (status.value, _now(), generation_id),
        )
        self._conn.commit()

    def log_generation_event(self, generation_id: str, message: str) -> None:
        self._conn.execute(
            """INSERT INTO generation_logs
               (generation_id, message, logged_at)
               VALUES (?, ?, ?)""",
            (generation_id, message, _now()),
        )
        self._conn.commit()

    def get_generation(self, generation_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM generations WHERE generation_id = ?",
            (generation_id,),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        if result.get("options_json"):
            result["options"] = json.loads(result["options_json"])
        else:
            result["options"] = None
        logs = self._conn.execute(
            """SELECT message, logged_at FROM generation_logs
               WHERE generation_id = ? ORDER BY id""",
            (generation_id,),
        ).fetchall()
        result["logs"] = [dict(lg) for lg in logs]
        return result

    # --- Intent / validation file version methods ---

    def record_intent_version(
        self, name: str, source_path: str, content_hash: str
    ) -> int:
        existing = self._conn.execute(
            """SELECT id FROM intent_file_versions
               WHERE name = ? AND content_hash = ?""",
            (name, content_hash),
        ).fetchone()
        if existing:
            return existing[0]
        self._conn.execute(
            """INSERT INTO intent_file_versions
               (name, source_path, content_hash, recorded_at)
               VALUES (?, ?, ?, ?)""",
            (name, source_path, content_hash, _now()),
        )
        self._conn.commit()
        return self._conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def record_validation_version(
        self, target: str, source_path: str, content_hash: str
    ) -> int:
        existing = self._conn.execute(
            """SELECT id FROM validation_file_versions
               WHERE target = ? AND source_path = ? AND content_hash = ?""",
            (target, source_path, content_hash),
        ).fetchone()
        if existing:
            return existing[0]
        self._conn.execute(
            """INSERT INTO validation_file_versions
               (target, source_path, content_hash, recorded_at)
               VALUES (?, ?, ?, ?)""",
            (target, source_path, content_hash, _now()),
        )
        self._conn.commit()
        return self._conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # --- Build result methods ---

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
            """INSERT INTO build_results
               (target, generation_id, intent_version_id, status, commit_id,
                total_duration_secs, timestamp, git_diff,
                files_created, files_modified)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                target,
                result.generation_id or None,
                intent_version_id,
                result.status,
                result.commit_id,
                result.total_duration_secs,
                result.timestamp or _now(),
                git_diff,
                json.dumps(files_created) if files_created else None,
                json.dumps(files_modified) if files_modified else None,
            ),
        )
        br_id = self._conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        # Save steps
        for i, step in enumerate(result.steps):
            self._conn.execute(
                """INSERT INTO build_steps
                   (build_result_id, step_order, phase, status,
                    duration_secs, summary)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (br_id, i, step.phase, step.status, step.duration_secs, step.summary),
            )
        # Update target state
        self._conn.execute(
            """INSERT OR REPLACE INTO target_state
               (target, output_dir, status, last_build_result_id, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                target,
                self.output_dir,
                "built" if result.status == "success" else "failed",
                br_id,
                _now(),
            ),
        )
        self._conn.commit()
        return br_id

    def get_build_result(self, target: str) -> BuildResult | None:
        state_row = self._conn.execute(
            """SELECT last_build_result_id FROM target_state
               WHERE target = ? AND output_dir = ?""",
            (target, self.output_dir),
        ).fetchone()
        if state_row is None or state_row[0] is None:
            return None
        return self._load_build_result(state_row[0])

    def get_build_history(
        self, target: str, limit: int = 50
    ) -> list[BuildResult]:
        rows = self._conn.execute(
            """SELECT id FROM build_results
               WHERE target = ? ORDER BY id DESC LIMIT ?""",
            (target, limit),
        ).fetchall()
        return [self._load_build_result(r[0]) for r in rows]

    def _load_build_result(self, br_id: int) -> BuildResult:
        row = self._conn.execute(
            "SELECT * FROM build_results WHERE id = ?", (br_id,)
        ).fetchone()
        step_rows = self._conn.execute(
            """SELECT phase, status, duration_secs, summary
               FROM build_steps WHERE build_result_id = ?
               ORDER BY step_order""",
            (br_id,),
        ).fetchall()
        steps = [
            BuildStep(
                phase=s["phase"],
                status=s["status"],
                duration_secs=s["duration_secs"],
                summary=s["summary"],
            )
            for s in step_rows
        ]
        fc = json.loads(row["files_created"]) if row["files_created"] else []
        fm = json.loads(row["files_modified"]) if row["files_modified"] else []
        return BuildResult(
            target=row["target"],
            generation_id=row["generation_id"] or "",
            status=row["status"],
            commit_id=row["commit_id"],
            total_duration_secs=row["total_duration_secs"],
            timestamp=row["timestamp"],
            git_diff=row["git_diff"],
            files_created=fc,
            files_modified=fm,
            steps=steps,
        )

    # --- Build step methods ---

    def save_build_step(
        self,
        build_result_id: int,
        step: BuildStep,
        log: str,
        step_order: int,
    ) -> None:
        self._conn.execute(
            """INSERT INTO build_steps
               (build_result_id, step_order, phase, status,
                duration_secs, summary, log)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
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

    # --- Validation result methods ---

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
            """INSERT INTO validation_results
               (build_result_id, generation_id, target,
                validation_file_version_id, name, type, severity,
                status, reason, duration_secs, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
        return self._conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # --- Agent response methods ---

    def save_agent_response(
        self,
        build_result_id: int | None,
        validation_result_id: int | None,
        response_type: str,
        response_json: dict,
    ) -> None:
        self._conn.execute(
            """INSERT INTO agent_responses
               (build_result_id, validation_result_id, response_type,
                response_json, stored_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                build_result_id,
                validation_result_id,
                response_type,
                json.dumps(response_json),
                _now(),
            ),
        )
        self._conn.commit()

    # --- Target state methods ---

    def get_status(self, target: str) -> TargetStatus:
        row = self._conn.execute(
            """SELECT status FROM target_state
               WHERE target = ? AND output_dir = ?""",
            (target, self.output_dir),
        ).fetchone()
        if row is None:
            return TargetStatus.PENDING
        return TargetStatus(row[0])

    def set_status(self, target: str, status: TargetStatus) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO target_state
               (target, output_dir, status, updated_at)
               VALUES (?, ?, ?, ?)""",
            (target, self.output_dir, status.value, _now()),
        )
        self._conn.commit()

    def list_targets(self) -> list[tuple[str, TargetStatus]]:
        rows = self._conn.execute(
            """SELECT target, status FROM target_state
               WHERE output_dir = ? ORDER BY target""",
            (self.output_dir,),
        ).fetchall()
        return [(r["target"], TargetStatus(r["status"])) for r in rows]

    def reset(self, target: str) -> None:
        self._conn.execute(
            """DELETE FROM target_state
               WHERE target = ? AND output_dir = ?""",
            (target, self.output_dir),
        )
        self._conn.commit()

    def reset_all(self) -> None:
        self._conn.execute(
            "DELETE FROM target_state WHERE output_dir = ?",
            (self.output_dir,),
        )
        self._conn.commit()
