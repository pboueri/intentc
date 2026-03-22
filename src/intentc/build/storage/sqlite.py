"""SQLite implementation of StorageBackend."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from intentc.build.state import BuildResult, BuildStep, TargetStatus
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
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

        # Migrate flat files if present
        self._migrate_flat_files(db_dir)

    # -- context manager -----------------------------------------------------

    def __enter__(self) -> SQLiteBackend:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    # -- Migration -----------------------------------------------------------

    def _migrate_flat_files(self, state_dir: Path) -> None:
        state_json = state_dir / "state.json"
        migrated_marker = state_dir / "state.json.migrated"
        if not state_json.exists() or migrated_marker.exists():
            return

        try:
            data = json.loads(state_json.read_text())
        except (json.JSONDecodeError, OSError):
            return

        # Migrate target state entries
        if isinstance(data, dict):
            for target, info in data.items():
                if isinstance(info, dict):
                    status_str = info.get("status", "pending")
                else:
                    status_str = str(info) if info else "pending"
                try:
                    status = TargetStatus(status_str)
                except ValueError:
                    status = TargetStatus.PENDING
                self._conn.execute(
                    """INSERT OR REPLACE INTO target_state
                       (target, output_dir, status, last_build_result_id, updated_at)
                       VALUES (?, ?, ?, NULL, ?)""",
                    (target, self.output_dir, status.value, _now_iso()),
                )

        # Migrate build log if present
        log_file = state_dir / "build-log.jsonl"
        if log_file.exists():
            try:
                for line in log_file.read_text().splitlines():
                    if not line.strip():
                        continue
                    entry = json.loads(line)
                    target = entry.get("target", "unknown")
                    gen_id = entry.get("generation_id", "migrated")
                    status_str = entry.get("status", "built")

                    # Ensure generation exists
                    existing = self._conn.execute(
                        "SELECT id FROM generations WHERE id = ?", (gen_id,)
                    ).fetchone()
                    if not existing:
                        self._conn.execute(
                            """INSERT INTO generations
                               (id, created_at, output_dir, status)
                               VALUES (?, ?, ?, ?)""",
                            (gen_id, _now_iso(), self.output_dir, "completed"),
                        )

                    self._conn.execute(
                        """INSERT INTO build_results
                           (generation_id, target, status, total_duration_secs, timestamp)
                           VALUES (?, ?, ?, ?, ?)""",
                        (
                            gen_id,
                            target,
                            status_str,
                            entry.get("duration_secs", 0.0),
                            entry.get("timestamp", _now_iso()),
                        ),
                    )

                    # Migrate steps if present
                    for i, step in enumerate(entry.get("steps", [])):
                        br_id = self._conn.execute(
                            "SELECT last_insert_rowid()"
                        ).fetchone()[0]
                        self._conn.execute(
                            """INSERT INTO build_steps
                               (build_result_id, phase, status, duration_secs, summary, step_order)
                               VALUES (?, ?, ?, ?, ?, ?)""",
                            (
                                br_id,
                                step.get("phase", "unknown"),
                                step.get("status", "success"),
                                step.get("duration_secs", 0.0),
                                step.get("summary", ""),
                                i,
                            ),
                        )
            except (json.JSONDecodeError, OSError):
                pass

        self._conn.commit()
        state_json.rename(migrated_marker)

    # -- Generation methods --------------------------------------------------

    def create_generation(
        self,
        generation_id: str,
        output_dir: str,
        profile_name: str | None,
        options: dict[str, Any] | None,
    ) -> None:
        self._conn.execute(
            """INSERT INTO generations (id, created_at, output_dir, profile_name, options_json, status)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                generation_id,
                _now_iso(),
                output_dir,
                profile_name,
                json.dumps(options) if options else None,
                GenerationStatus.RUNNING.value,
            ),
        )
        self._conn.commit()

    def complete_generation(
        self, generation_id: str, status: GenerationStatus
    ) -> None:
        self._conn.execute(
            "UPDATE generations SET status = ? WHERE id = ?",
            (status.value, generation_id),
        )
        self._conn.commit()

    def log_generation_event(self, generation_id: str, message: str) -> None:
        self._conn.execute(
            """INSERT INTO generation_logs (generation_id, message, timestamp)
               VALUES (?, ?, ?)""",
            (generation_id, message, _now_iso()),
        )
        self._conn.commit()

    def get_generation(self, generation_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM generations WHERE id = ?", (generation_id,)
        ).fetchone()
        if row is None:
            return None
        result: dict[str, Any] = dict(row)
        if result.get("options_json"):
            result["options"] = json.loads(result["options_json"])
        logs = self._conn.execute(
            "SELECT message, timestamp FROM generation_logs WHERE generation_id = ? ORDER BY id",
            (generation_id,),
        ).fetchall()
        result["logs"] = [dict(l) for l in logs]
        return result

    # -- Intent / validation file version methods ----------------------------

    def record_intent_version(
        self, name: str, source_path: str, content_hash: str
    ) -> int:
        existing = self._conn.execute(
            "SELECT id FROM intent_file_versions WHERE name = ? AND content_hash = ?",
            (name, content_hash),
        ).fetchone()
        if existing:
            return existing[0]
        cur = self._conn.execute(
            """INSERT INTO intent_file_versions (name, source_path, content_hash, created_at)
               VALUES (?, ?, ?, ?)""",
            (name, source_path, content_hash, _now_iso()),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def record_validation_version(
        self, target: str, source_path: str, content_hash: str
    ) -> int:
        existing = self._conn.execute(
            "SELECT id FROM validation_file_versions WHERE target = ? AND source_path = ? AND content_hash = ?",
            (target, source_path, content_hash),
        ).fetchone()
        if existing:
            return existing[0]
        cur = self._conn.execute(
            """INSERT INTO validation_file_versions (target, source_path, content_hash, created_at)
               VALUES (?, ?, ?, ?)""",
            (target, source_path, content_hash, _now_iso()),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    # -- Build result methods ------------------------------------------------

    def save_build_result(
        self,
        target: str,
        result: BuildResult,
        intent_version_id: int | None,
        git_diff: str | None,
        files_created: list[str] | None,
        files_modified: list[str] | None,
    ) -> int:
        cur = self._conn.execute(
            """INSERT INTO build_results
               (generation_id, target, intent_file_version_id, status,
                commit_id, total_duration_secs, timestamp,
                git_diff, files_created_json, files_modified_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                result.generation_id,
                target,
                intent_version_id,
                result.status.value,
                result.commit_id,
                result.total_duration_secs,
                result.timestamp.isoformat(),
                git_diff,
                json.dumps(files_created) if files_created else None,
                json.dumps(files_modified) if files_modified else None,
            ),
        )
        build_result_id: int = cur.lastrowid  # type: ignore[assignment]

        # Save steps
        for i, step in enumerate(result.steps):
            self.save_build_step(build_result_id, step, "", i)

        # Update target state
        self._conn.execute(
            """INSERT OR REPLACE INTO target_state
               (target, output_dir, status, last_build_result_id, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                target,
                self.output_dir,
                result.status.value,
                build_result_id,
                _now_iso(),
            ),
        )
        self._conn.commit()
        return build_result_id

    def get_build_result(self, target: str) -> BuildResult | None:
        # Get latest via target_state
        state_row = self._conn.execute(
            "SELECT last_build_result_id FROM target_state WHERE target = ? AND output_dir = ?",
            (target, self.output_dir),
        ).fetchone()
        if not state_row or state_row[0] is None:
            return None
        return self._load_build_result(state_row[0])

    def get_build_history(
        self, target: str, limit: int = 50
    ) -> list[BuildResult]:
        rows = self._conn.execute(
            "SELECT id FROM build_results WHERE target = ? ORDER BY id DESC LIMIT ?",
            (target, limit),
        ).fetchall()
        return [self._load_build_result(r[0]) for r in rows]

    def _load_build_result(self, build_result_id: int) -> BuildResult:
        row = self._conn.execute(
            "SELECT * FROM build_results WHERE id = ?", (build_result_id,)
        ).fetchone()
        steps_rows = self._conn.execute(
            "SELECT * FROM build_steps WHERE build_result_id = ? ORDER BY step_order",
            (build_result_id,),
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
            generation_id=row["generation_id"],
            target=row["target"],
            status=TargetStatus(row["status"]),
            commit_id=row["commit_id"],
            total_duration_secs=row["total_duration_secs"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
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
            """INSERT INTO build_steps
               (build_result_id, phase, status, duration_secs, summary, log, step_order)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                build_result_id,
                step.phase,
                step.status,
                step.duration_secs,
                step.summary,
                log or None,
                step_order,
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
        reason: str,
        duration_secs: float | None,
    ) -> int:
        cur = self._conn.execute(
            """INSERT INTO validation_results
               (build_result_id, generation_id, target,
                validation_file_version_id, validation_name, validation_type,
                severity, status, reason, duration_secs, timestamp)
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
            """INSERT INTO agent_responses
               (build_result_id, validation_result_id, response_type, response_json, created_at)
               VALUES (?, ?, ?, ?, ?)""",
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
            "SELECT status FROM target_state WHERE target = ? AND output_dir = ?",
            (target, self.output_dir),
        ).fetchone()
        if row is None:
            return TargetStatus.PENDING
        return TargetStatus(row[0])

    def set_status(self, target: str, status: TargetStatus) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO target_state
               (target, output_dir, status, last_build_result_id, updated_at)
               VALUES (?, ?, ?,
                       (SELECT last_build_result_id FROM target_state WHERE target = ? AND output_dir = ?),
                       ?)""",
            (target, self.output_dir, status.value, target, self.output_dir, _now_iso()),
        )
        self._conn.commit()

    def list_targets(self) -> list[tuple[str, TargetStatus]]:
        rows = self._conn.execute(
            "SELECT target, status FROM target_state WHERE output_dir = ? ORDER BY target",
            (self.output_dir,),
        ).fetchall()
        return [(r[0], TargetStatus(r[1])) for r in rows]

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
