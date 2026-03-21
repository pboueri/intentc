"""SQLite implementation of StorageBackend."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from intentc.build.storage.backend import GenerationStatus, StorageBackend


_SCHEMA = """
CREATE TABLE IF NOT EXISTS intent_file_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    source_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(name, content_hash)
);

CREATE TABLE IF NOT EXISTS validation_file_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target TEXT NOT NULL,
    source_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(target, source_path, content_hash)
);

CREATE TABLE IF NOT EXISTS generations (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    output_dir TEXT NOT NULL,
    profile_name TEXT,
    options_json TEXT,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS generation_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id TEXT NOT NULL REFERENCES generations(id),
    message TEXT NOT NULL,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS build_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id TEXT NOT NULL REFERENCES generations(id),
    target TEXT NOT NULL,
    intent_file_version_id INTEGER REFERENCES intent_file_versions(id),
    status TEXT NOT NULL,
    commit_id TEXT,
    total_duration_secs REAL NOT NULL,
    timestamp TEXT NOT NULL,
    git_diff TEXT,
    files_created_json TEXT,
    files_modified_json TEXT
);

CREATE TABLE IF NOT EXISTS build_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    build_result_id INTEGER NOT NULL REFERENCES build_results(id),
    phase TEXT NOT NULL,
    status TEXT NOT NULL,
    duration_secs REAL NOT NULL,
    summary TEXT NOT NULL,
    log TEXT,
    step_order INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS validation_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    build_result_id INTEGER REFERENCES build_results(id),
    generation_id TEXT NOT NULL REFERENCES generations(id),
    target TEXT NOT NULL,
    validation_file_version_id INTEGER REFERENCES validation_file_versions(id),
    validation_name TEXT NOT NULL,
    validation_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    duration_secs REAL,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    build_result_id INTEGER REFERENCES build_results(id),
    validation_result_id INTEGER REFERENCES validation_results(id),
    response_type TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS target_state (
    target TEXT NOT NULL,
    output_dir TEXT NOT NULL,
    status TEXT NOT NULL,
    last_build_result_id INTEGER REFERENCES build_results(id),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (target, output_dir)
);
"""


class SQLiteBackend(StorageBackend):
    """SQLite-backed storage using Python's built-in sqlite3 module."""

    def __init__(self, base_dir: Path, output_dir: str) -> None:
        self._output_dir = output_dir
        self._dir = base_dir / ".intentc" / "state" / output_dir
        self._db_path = self._dir / "intentc.db"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._migrate_flat_files()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SQLiteBackend:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    # -- Migration ---------------------------------------------------------

    def _migrate_flat_files(self) -> None:
        state_json = self._dir / "state.json"
        migrated_marker = self._dir / "state.json.migrated"
        if not state_json.exists() or migrated_marker.exists():
            return

        try:
            data = json.loads(state_json.read_text())
        except (json.JSONDecodeError, OSError):
            return

        for name, entry_data in data.get("targets", {}).items():
            status = entry_data.get("status", "pending")
            self._conn.execute(
                "INSERT OR REPLACE INTO target_state (target, output_dir, status, last_build_result_id, updated_at) "
                "VALUES (?, ?, ?, NULL, ?)",
                (name, self._output_dir, status, datetime.now().isoformat()),
            )
            br = entry_data.get("build_result")
            if br:
                # Create a dummy generation for migrated data
                gen_id = f"migrated-{name}"
                self._conn.execute(
                    "INSERT OR IGNORE INTO generations (id, created_at, output_dir, profile_name, options_json, status) "
                    "VALUES (?, ?, ?, NULL, NULL, ?)",
                    (gen_id, datetime.now().isoformat(), self._output_dir, "completed"),
                )
                cursor = self._conn.execute(
                    "INSERT INTO build_results (generation_id, target, status, commit_id, total_duration_secs, timestamp) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        gen_id,
                        br.get("target", name),
                        br.get("status", "built"),
                        br.get("commit_id", ""),
                        br.get("total_duration", 0),
                        br.get("timestamp", datetime.now().isoformat()),
                    ),
                )
                build_result_id = cursor.lastrowid
                for i, step in enumerate(br.get("steps", [])):
                    self._conn.execute(
                        "INSERT INTO build_steps (build_result_id, phase, status, duration_secs, summary, step_order) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            build_result_id,
                            step.get("phase", ""),
                            step.get("status", ""),
                            step.get("duration", 0),
                            step.get("summary", ""),
                            i,
                        ),
                    )
                self._conn.execute(
                    "UPDATE target_state SET last_build_result_id = ? WHERE target = ? AND output_dir = ?",
                    (build_result_id, name, self._output_dir),
                )

        # Migrate build log
        log_path = self._dir / "build-log.jsonl"
        if log_path.exists():
            try:
                for line in log_path.read_text().strip().splitlines():
                    entry = json.loads(line)
                    target = entry.get("target", "")
                    gen_id = f"migrated-log-{target}-{entry.get('generation_id', '')}"
                    self._conn.execute(
                        "INSERT OR IGNORE INTO generations (id, created_at, output_dir, profile_name, options_json, status) "
                        "VALUES (?, ?, ?, NULL, NULL, ?)",
                        (gen_id, datetime.now().isoformat(), self._output_dir, "completed"),
                    )
                    cursor = self._conn.execute(
                        "INSERT INTO build_results (generation_id, target, status, commit_id, total_duration_secs, timestamp) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            gen_id,
                            target,
                            entry.get("status", "built"),
                            entry.get("commit_id", ""),
                            entry.get("total_duration", 0),
                            entry.get("timestamp", datetime.now().isoformat()),
                        ),
                    )
                    br_id = cursor.lastrowid
                    for i, step in enumerate(entry.get("steps", [])):
                        self._conn.execute(
                            "INSERT INTO build_steps (build_result_id, phase, status, duration_secs, summary, step_order) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (br_id, step.get("phase", ""), step.get("status", ""), step.get("duration", 0), step.get("summary", ""), i),
                        )
            except (json.JSONDecodeError, OSError):
                pass

        self._conn.commit()
        state_json.rename(migrated_marker)

    # -- Generation methods ------------------------------------------------

    def create_generation(
        self,
        generation_id: str,
        output_dir: str,
        profile_name: str | None,
        options: dict | None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO generations (id, created_at, output_dir, profile_name, options_json, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                generation_id,
                datetime.now().isoformat(),
                output_dir,
                profile_name,
                json.dumps(options) if options else None,
                GenerationStatus.RUNNING.value,
            ),
        )
        self._conn.commit()

    def complete_generation(self, generation_id: str, status: GenerationStatus) -> None:
        self._conn.execute(
            "UPDATE generations SET status = ? WHERE id = ?",
            (status.value, generation_id),
        )
        self._conn.commit()

    def log_generation_event(self, generation_id: str, message: str) -> None:
        self._conn.execute(
            "INSERT INTO generation_logs (generation_id, message, timestamp) VALUES (?, ?, ?)",
            (generation_id, message, datetime.now().isoformat()),
        )
        self._conn.commit()

    def get_generation(self, generation_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT id, created_at, output_dir, profile_name, options_json, status FROM generations WHERE id = ?",
            (generation_id,),
        ).fetchone()
        if row is None:
            return None
        logs = self._conn.execute(
            "SELECT message, timestamp FROM generation_logs WHERE generation_id = ? ORDER BY id",
            (generation_id,),
        ).fetchall()
        return {
            "id": row[0],
            "created_at": row[1],
            "output_dir": row[2],
            "profile_name": row[3],
            "options_json": json.loads(row[4]) if row[4] else None,
            "status": row[5],
            "logs": [{"message": l[0], "timestamp": l[1]} for l in logs],
        }

    # -- Intent/Validation file version methods ----------------------------

    def record_intent_version(self, name: str, source_path: str, content_hash: str) -> int:
        row = self._conn.execute(
            "SELECT id FROM intent_file_versions WHERE name = ? AND content_hash = ?",
            (name, content_hash),
        ).fetchone()
        if row:
            return row[0]
        cursor = self._conn.execute(
            "INSERT INTO intent_file_versions (name, source_path, content_hash, created_at) VALUES (?, ?, ?, ?)",
            (name, source_path, content_hash, datetime.now().isoformat()),
        )
        self._conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    def record_validation_version(self, target: str, source_path: str, content_hash: str) -> int:
        row = self._conn.execute(
            "SELECT id FROM validation_file_versions WHERE target = ? AND source_path = ? AND content_hash = ?",
            (target, source_path, content_hash),
        ).fetchone()
        if row:
            return row[0]
        cursor = self._conn.execute(
            "INSERT INTO validation_file_versions (target, source_path, content_hash, created_at) VALUES (?, ?, ?, ?)",
            (target, source_path, content_hash, datetime.now().isoformat()),
        )
        self._conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    # -- Build result methods ----------------------------------------------

    def save_build_result(
        self,
        target: str,
        result_dict: dict,
        generation_id: str,
        intent_version_id: int | None = None,
        git_diff: str | None = None,
        files_created: list[str] | None = None,
        files_modified: list[str] | None = None,
    ) -> int:
        cursor = self._conn.execute(
            "INSERT INTO build_results "
            "(generation_id, target, intent_file_version_id, status, commit_id, total_duration_secs, timestamp, git_diff, files_created_json, files_modified_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                generation_id,
                target,
                intent_version_id,
                result_dict.get("status", "pending"),
                result_dict.get("commit_id", ""),
                result_dict.get("total_duration", 0),
                result_dict.get("timestamp", datetime.now().isoformat()),
                git_diff,
                json.dumps(files_created) if files_created else None,
                json.dumps(files_modified) if files_modified else None,
            ),
        )
        build_result_id = cursor.lastrowid

        # Save steps
        for i, step in enumerate(result_dict.get("steps", [])):
            self._conn.execute(
                "INSERT INTO build_steps (build_result_id, phase, status, duration_secs, summary, log, step_order) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    build_result_id,
                    step.get("phase", ""),
                    step.get("status", ""),
                    step.get("duration", 0),
                    step.get("summary", ""),
                    step.get("log"),
                    i,
                ),
            )

        # Update target state
        self._conn.execute(
            "INSERT OR REPLACE INTO target_state (target, output_dir, status, last_build_result_id, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                target,
                self._output_dir,
                result_dict.get("status", "pending"),
                build_result_id,
                datetime.now().isoformat(),
            ),
        )
        self._conn.commit()
        return build_result_id  # type: ignore[return-value]

    def get_build_result(self, target: str) -> dict | None:
        row = self._conn.execute(
            "SELECT br.id, br.generation_id, br.target, br.status, br.commit_id, "
            "br.total_duration_secs, br.timestamp, br.git_diff, br.files_created_json, br.files_modified_json "
            "FROM build_results br "
            "JOIN target_state ts ON ts.last_build_result_id = br.id "
            "WHERE ts.target = ? AND ts.output_dir = ?",
            (target, self._output_dir),
        ).fetchone()
        if row is None:
            return None
        return self._build_result_row_to_dict(row)

    def get_build_history(self, target: str, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, generation_id, target, status, commit_id, "
            "total_duration_secs, timestamp, git_diff, files_created_json, files_modified_json "
            "FROM build_results WHERE target = ? ORDER BY id DESC LIMIT ?",
            (target, limit),
        ).fetchall()
        return [self._build_result_row_to_dict(r) for r in rows]

    def _build_result_row_to_dict(self, row: tuple) -> dict:
        build_result_id = row[0]
        steps = self._conn.execute(
            "SELECT phase, status, duration_secs, summary, log FROM build_steps "
            "WHERE build_result_id = ? ORDER BY step_order",
            (build_result_id,),
        ).fetchall()
        return {
            "target": row[2],
            "generation_id": row[1],
            "status": row[3],
            "commit_id": row[4] or "",
            "total_duration": row[5],
            "timestamp": row[6],
            "git_diff": row[7],
            "files_created": json.loads(row[8]) if row[8] else None,
            "files_modified": json.loads(row[9]) if row[9] else None,
            "steps": [
                {
                    "phase": s[0],
                    "status": s[1],
                    "duration": s[2],
                    "summary": s[3],
                    "log": s[4],
                }
                for s in steps
            ],
        }

    # -- Build step methods ------------------------------------------------

    def save_build_step(
        self, build_result_id: int, step_dict: dict, log: str | None, step_order: int
    ) -> None:
        self._conn.execute(
            "INSERT INTO build_steps (build_result_id, phase, status, duration_secs, summary, log, step_order) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                build_result_id,
                step_dict.get("phase", ""),
                step_dict.get("status", ""),
                step_dict.get("duration", 0),
                step_dict.get("summary", ""),
                log,
                step_order,
            ),
        )
        self._conn.commit()

    # -- Validation result methods -----------------------------------------

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
        duration_secs: float | None,
    ) -> int:
        cursor = self._conn.execute(
            "INSERT INTO validation_results "
            "(build_result_id, generation_id, target, validation_file_version_id, "
            "validation_name, validation_type, severity, status, reason, duration_secs, timestamp) "
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
                datetime.now().isoformat(),
            ),
        )
        self._conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    # -- Agent response methods --------------------------------------------

    def save_agent_response(
        self,
        build_result_id: int | None,
        validation_result_id: int | None,
        response_type: str,
        response_json: dict,
    ) -> None:
        self._conn.execute(
            "INSERT INTO agent_responses (build_result_id, validation_result_id, response_type, response_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                build_result_id,
                validation_result_id,
                response_type,
                json.dumps(response_json),
                datetime.now().isoformat(),
            ),
        )
        self._conn.commit()

    # -- Target state methods ----------------------------------------------

    def get_status(self, target: str) -> str:
        row = self._conn.execute(
            "SELECT status FROM target_state WHERE target = ? AND output_dir = ?",
            (target, self._output_dir),
        ).fetchone()
        return row[0] if row else "pending"

    def set_status(self, target: str, status: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO target_state (target, output_dir, status, last_build_result_id, updated_at) "
            "VALUES (?, ?, ?, "
            "(SELECT last_build_result_id FROM target_state WHERE target = ? AND output_dir = ?), ?)",
            (target, self._output_dir, status, target, self._output_dir, datetime.now().isoformat()),
        )
        self._conn.commit()

    def list_targets(self) -> list[tuple[str, str]]:
        rows = self._conn.execute(
            "SELECT target, status FROM target_state WHERE output_dir = ? ORDER BY target",
            (self._output_dir,),
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def reset(self, target: str) -> None:
        self._conn.execute(
            "DELETE FROM target_state WHERE target = ? AND output_dir = ?",
            (target, self._output_dir),
        )
        self._conn.commit()

    def reset_all(self) -> None:
        self._conn.execute(
            "DELETE FROM target_state WHERE output_dir = ?",
            (self._output_dir,),
        )
        self._conn.commit()
