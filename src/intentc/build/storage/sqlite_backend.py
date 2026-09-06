"""SQLite implementation of `StorageBackend`."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from intentc.build.storage.backend import (
    BuildResult,
    BuildStep,
    GenerationStatus,
    RefinementSession,
    StorageBackend,
    TargetStatus,
)

_ADDED_BUILD_RESULT_COLUMNS: list[tuple[str, str]] = [
    ("source_hash", "TEXT NOT NULL DEFAULT ''"),
    ("attempts", "INTEGER NOT NULL DEFAULT 1"),
]


def _now_iso() -> str:
    return datetime.now().isoformat()


def _enum_value(value: Any) -> str:
    return value.value if isinstance(value, (TargetStatus, GenerationStatus)) else str(value)


class SQLiteBackend(StorageBackend):
    """Concrete `StorageBackend` backed by SQLite (stdlib `sqlite3`, no external deps).

    One connection per instance, shared across threads and guarded by a
    re-entrant lock so concurrent callers (e.g. a validation thread pool)
    never trip SQLite's "cannot start a transaction within a transaction".
    """

    def __init__(self, base_dir: Path, output_dir: str) -> None:
        super().__init__(base_dir, output_dir)
        self._lock = threading.RLock()
        self.db_path = self.base_dir / ".intentc" / "state" / output_dir / "intentc.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()
        self._upgrade_schema()
        self._migrate_from_flat_files()

    # -- Schema ---------------------------------------------------------------

    def _create_tables(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
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

                CREATE TABLE IF NOT EXISTS refinement_sessions (
                    session_id          TEXT PRIMARY KEY,
                    target              TEXT NOT NULL,
                    output_dir          TEXT NOT NULL,
                    status              TEXT NOT NULL,
                    base_commit         TEXT NOT NULL,
                    snapshot_id         TEXT,
                    seed_prompt         TEXT NOT NULL DEFAULT '',
                    journal             TEXT NOT NULL DEFAULT '',
                    bake_attempts       INTEGER NOT NULL DEFAULT 0,
                    bake_generation_id  TEXT REFERENCES generations(generation_id),
                    bake_response_json  TEXT,
                    started_at          TEXT NOT NULL,
                    ended_at            TEXT
                );
                """
            )
            self._conn.commit()

    def _upgrade_schema(self) -> None:
        with self._lock:
            existing = {
                row["name"] for row in self._conn.execute("PRAGMA table_info(build_results)").fetchall()
            }
            for column, ddl in _ADDED_BUILD_RESULT_COLUMNS:
                if column not in existing:
                    self._conn.execute(f"ALTER TABLE build_results ADD COLUMN {column} {ddl}")
            self._conn.commit()

    # -- Migration --------------------------------------------------------------

    def _migrate_from_flat_files(self) -> None:
        state_path = self.db_path.parent / "state.json"
        migrated_marker = self.db_path.parent / "state.json.migrated"
        if migrated_marker.exists() or not state_path.exists():
            return

        with self._lock:
            try:
                state_data = json.loads(state_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                state_data = {}
            targets_data = state_data.get("targets", {})

            last_build_result_id_by_target: dict[str, int] = {}
            build_log_path = self.db_path.parent / "build-log.jsonl"
            if build_log_path.exists():
                for line in build_log_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    target = entry.get("target")
                    if not target:
                        continue
                    result = BuildResult.model_validate(entry)
                    build_result_id = self._insert_build_result(target, result, None, None, None, None)
                    last_build_result_id_by_target[target] = build_result_id

            now = _now_iso()
            for target, info in targets_data.items():
                status_value = _enum_value(info.get("status", TargetStatus.PENDING))
                self._conn.execute(
                    """
                    INSERT INTO target_state (target, output_dir, status, last_build_result_id, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(target, output_dir) DO UPDATE SET
                        status=excluded.status,
                        last_build_result_id=excluded.last_build_result_id,
                        updated_at=excluded.updated_at
                    """,
                    (target, self.output_dir, status_value, last_build_result_id_by_target.get(target), now),
                )
            self._conn.commit()

        state_path.rename(migrated_marker)

    # -- Generation methods -------------------------------------------------

    def create_generation(
        self,
        generation_id: str,
        output_dir: str,
        profile_name: Optional[str] = None,
        options: Optional[dict[str, Any]] = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO generations (generation_id, output_dir, profile_name, options_json, status, started_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    generation_id,
                    output_dir,
                    profile_name,
                    json.dumps(options) if options is not None else None,
                    GenerationStatus.RUNNING.value,
                    _now_iso(),
                ),
            )
            self._conn.commit()

    def complete_generation(self, generation_id: str, status: GenerationStatus) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE generations SET status=?, completed_at=? WHERE generation_id=?",
                (_enum_value(status), _now_iso(), generation_id),
            )
            self._conn.commit()

    def log_generation_event(self, generation_id: str, message: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO generation_logs (generation_id, message, logged_at) VALUES (?, ?, ?)",
                (generation_id, message, _now_iso()),
            )
            self._conn.commit()

    def get_generation(self, generation_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM generations WHERE generation_id=?", (generation_id,)
            ).fetchone()
            if row is None:
                return None
            logs = self._conn.execute(
                "SELECT message, logged_at FROM generation_logs WHERE generation_id=? ORDER BY id",
                (generation_id,),
            ).fetchall()
            result = dict(row)
            options_json = result.pop("options_json", None)
            result["options"] = json.loads(options_json) if options_json else None
            result["logs"] = [dict(log_row) for log_row in logs]
            return result

    # -- Intent/validation file version methods ------------------------------

    def record_intent_version(self, name: str, source_path: str, content_hash: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM intent_file_versions WHERE name=? AND content_hash=?",
                (name, content_hash),
            ).fetchone()
            if row is not None:
                return row["id"]
            cursor = self._conn.execute(
                """
                INSERT INTO intent_file_versions (name, source_path, content_hash, recorded_at)
                VALUES (?, ?, ?, ?)
                """,
                (name, str(source_path), content_hash, _now_iso()),
            )
            self._conn.commit()
            return cursor.lastrowid

    def record_validation_version(self, target: str, source_path: str, content_hash: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM validation_file_versions WHERE target=? AND source_path=? AND content_hash=?",
                (target, str(source_path), content_hash),
            ).fetchone()
            if row is not None:
                return row["id"]
            cursor = self._conn.execute(
                """
                INSERT INTO validation_file_versions (target, source_path, content_hash, recorded_at)
                VALUES (?, ?, ?, ?)
                """,
                (target, str(source_path), content_hash, _now_iso()),
            )
            self._conn.commit()
            return cursor.lastrowid

    # -- Build result methods -------------------------------------------------

    def _insert_build_result(
        self,
        target: str,
        result: BuildResult,
        intent_version_id: Optional[int],
        git_diff: Optional[str],
        files_created: Optional[list[str]],
        files_modified: Optional[list[str]],
    ) -> int:
        resolved_created = files_created if files_created is not None else result.files_created
        resolved_modified = files_modified if files_modified is not None else result.files_modified
        cursor = self._conn.execute(
            """
            INSERT INTO build_results (
                target, generation_id, intent_version_id, status, commit_id,
                total_duration_secs, timestamp, git_diff, files_created, files_modified,
                source_hash, attempts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                target,
                result.generation_id or None,
                intent_version_id,
                _enum_value(result.status),
                result.commit_id,
                result.total_duration_secs,
                result.timestamp or _now_iso(),
                git_diff,
                json.dumps(resolved_created),
                json.dumps(resolved_modified),
                result.source_hash,
                result.attempts,
            ),
        )
        build_result_id = cursor.lastrowid
        self._conn.commit()
        for order, step in enumerate(result.steps):
            self.save_build_step(build_result_id, step, log="", step_order=order)
        return build_result_id

    def save_build_result(
        self,
        target: str,
        result: BuildResult,
        intent_version_id: Optional[int] = None,
        git_diff: Optional[str] = None,
        files_created: Optional[list[str]] = None,
        files_modified: Optional[list[str]] = None,
    ) -> int:
        with self._lock:
            build_result_id = self._insert_build_result(
                target, result, intent_version_id, git_diff, files_created, files_modified
            )
            self._conn.execute(
                """
                INSERT INTO target_state (target, output_dir, status, last_build_result_id, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(target, output_dir) DO UPDATE SET
                    status=excluded.status,
                    last_build_result_id=excluded.last_build_result_id,
                    updated_at=excluded.updated_at
                """,
                (target, self.output_dir, _enum_value(result.status), build_result_id, _now_iso()),
            )
            self._conn.commit()
            return build_result_id

    def _load_build_result(self, build_result_id: int) -> BuildResult:
        row = self._conn.execute("SELECT * FROM build_results WHERE id=?", (build_result_id,)).fetchone()
        step_rows = self._conn.execute(
            "SELECT phase, status, duration_secs, summary FROM build_steps "
            "WHERE build_result_id=? ORDER BY step_order",
            (build_result_id,),
        ).fetchall()
        steps = [
            BuildStep(
                phase=step_row["phase"],
                status=step_row["status"],
                duration_secs=step_row["duration_secs"],
                summary=step_row["summary"],
            )
            for step_row in step_rows
        ]
        return BuildResult(
            target=row["target"],
            generation_id=row["generation_id"] or "",
            status=row["status"],
            steps=steps,
            commit_id=row["commit_id"],
            total_duration_secs=row["total_duration_secs"],
            timestamp=row["timestamp"],
            source_hash=row["source_hash"] or "",
            files_created=json.loads(row["files_created"]) if row["files_created"] else [],
            files_modified=json.loads(row["files_modified"]) if row["files_modified"] else [],
            attempts=row["attempts"] if row["attempts"] is not None else 1,
        )

    def get_build_result(self, target: str) -> Optional[BuildResult]:
        with self._lock:
            row = self._conn.execute(
                "SELECT last_build_result_id FROM target_state WHERE target=? AND output_dir=?",
                (target, self.output_dir),
            ).fetchone()
            if row is None or row["last_build_result_id"] is None:
                return None
            return self._load_build_result(row["last_build_result_id"])

    def get_build_history(self, target: str, limit: int = 50) -> list[BuildResult]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM build_results WHERE target=? ORDER BY id DESC LIMIT ?",
                (target, limit),
            ).fetchall()
            return [self._load_build_result(row["id"]) for row in rows]

    def get_build_diff(self, target: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT br.git_diff AS git_diff
                FROM target_state ts
                JOIN build_results br ON br.id = ts.last_build_result_id
                WHERE ts.target=? AND ts.output_dir=?
                """,
                (target, self.output_dir),
            ).fetchone()
            return row["git_diff"] if row is not None else None

    def get_validation_results(
        self, target: str, build_result_id: Optional[int] = None
    ) -> list[dict[str, Any]]:
        with self._lock:
            if build_result_id is None:
                state_row = self._conn.execute(
                    "SELECT last_build_result_id FROM target_state WHERE target=? AND output_dir=?",
                    (target, self.output_dir),
                ).fetchone()
                last_build_result_id = state_row["last_build_result_id"] if state_row is not None else None
                if last_build_result_id is None:
                    rows = self._conn.execute(
                        """
                        SELECT name, type, severity, status, reason, timestamp FROM validation_results
                        WHERE target=? ORDER BY id DESC
                        """,
                        (target,),
                    ).fetchall()
                    return [dict(row) for row in rows]
                build_row = self._conn.execute(
                    "SELECT generation_id FROM build_results WHERE id=?", (last_build_result_id,)
                ).fetchone()
                generation_id = build_row["generation_id"] if build_row is not None else None
                rows = self._conn.execute(
                    """
                    SELECT name, type, severity, status, reason, timestamp FROM validation_results
                    WHERE target=? AND (build_result_id=? OR generation_id=?)
                    ORDER BY id DESC
                    """,
                    (target, last_build_result_id, generation_id),
                ).fetchall()
                return [dict(row) for row in rows]

            rows = self._conn.execute(
                """
                SELECT name, type, severity, status, reason, timestamp FROM validation_results
                WHERE target=? AND build_result_id=?
                ORDER BY id DESC
                """,
                (target, build_result_id),
            ).fetchall()
            return [dict(row) for row in rows]

    # -- Build step methods -----------------------------------------------------

    def save_build_step(self, build_result_id: int, step: BuildStep, log: str, step_order: int) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO build_steps (build_result_id, step_order, phase, status, duration_secs, summary, log)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (build_result_id, step_order, step.phase, step.status, step.duration_secs, step.summary, log),
            )
            self._conn.commit()

    # -- Validation result methods ------------------------------------------

    def save_validation_result(
        self,
        build_result_id: Optional[int],
        generation_id: str,
        target: str,
        validation_file_version_id: Optional[int],
        name: str,
        type: str,
        severity: str,
        status: str,
        reason: str,
        duration_secs: Optional[float],
    ) -> int:
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO validation_results (
                    build_result_id, generation_id, target, validation_file_version_id,
                    name, type, severity, status, reason, duration_secs, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
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
            return cursor.lastrowid

    # -- Agent response methods -----------------------------------------------

    def save_agent_response(
        self,
        build_result_id: Optional[int],
        validation_result_id: Optional[int],
        response_type: str,
        response_json: dict[str, Any],
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO agent_responses (
                    build_result_id, validation_result_id, response_type, response_json, stored_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (build_result_id, validation_result_id, response_type, json.dumps(response_json), _now_iso()),
            )
            self._conn.commit()

    # -- Refinement session methods -------------------------------------------

    _REFINEMENT_SESSION_FIELDS = {
        "target",
        "output_dir",
        "status",
        "base_commit",
        "snapshot_id",
        "seed_prompt",
        "journal",
        "bake_attempts",
        "bake_generation_id",
        "bake_response_json",
        "started_at",
        "ended_at",
    }

    def create_refinement_session(self, session: RefinementSession) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO refinement_sessions (
                    session_id, target, output_dir, status, base_commit, snapshot_id,
                    seed_prompt, journal, bake_attempts, bake_generation_id,
                    bake_response_json, started_at, ended_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.session_id,
                    session.target,
                    session.output_dir,
                    session.status,
                    session.base_commit,
                    session.snapshot_id,
                    session.seed_prompt,
                    session.journal,
                    session.bake_attempts,
                    session.bake_generation_id,
                    session.bake_response_json,
                    session.started_at,
                    session.ended_at,
                ),
            )
            self._conn.commit()

    def update_refinement_session(self, session_id: str, **fields: Any) -> None:
        unknown = set(fields) - self._REFINEMENT_SESSION_FIELDS
        if unknown:
            raise ValueError(f"Unknown refinement_session field(s): {', '.join(sorted(unknown))}")
        if not fields:
            return
        with self._lock:
            assignments = ", ".join(f"{key}=?" for key in fields)
            values = list(fields.values()) + [session_id]
            self._conn.execute(
                f"UPDATE refinement_sessions SET {assignments} WHERE session_id=?", values
            )
            self._conn.commit()

    @staticmethod
    def _row_to_refinement_session(row: sqlite3.Row) -> RefinementSession:
        return RefinementSession(
            session_id=row["session_id"],
            target=row["target"],
            output_dir=row["output_dir"],
            status=row["status"],
            base_commit=row["base_commit"],
            snapshot_id=row["snapshot_id"],
            seed_prompt=row["seed_prompt"] or "",
            journal=row["journal"] or "",
            bake_attempts=row["bake_attempts"] if row["bake_attempts"] is not None else 0,
            bake_generation_id=row["bake_generation_id"],
            bake_response_json=row["bake_response_json"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
        )

    def get_refinement_session(self, session_id: str) -> Optional[RefinementSession]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM refinement_sessions WHERE session_id=?", (session_id,)
            ).fetchone()
            return self._row_to_refinement_session(row) if row is not None else None

    def get_open_refinement_session(self, target: Optional[str] = None) -> Optional[RefinementSession]:
        with self._lock:
            if target is not None:
                row = self._conn.execute(
                    """
                    SELECT * FROM refinement_sessions
                    WHERE output_dir=? AND target=? AND status IN ('recording', 'baking')
                    ORDER BY started_at DESC LIMIT 1
                    """,
                    (self.output_dir, target),
                ).fetchone()
            else:
                row = self._conn.execute(
                    """
                    SELECT * FROM refinement_sessions
                    WHERE output_dir=? AND status IN ('recording', 'baking')
                    ORDER BY started_at DESC LIMIT 1
                    """,
                    (self.output_dir,),
                ).fetchone()
            return self._row_to_refinement_session(row) if row is not None else None

    def list_refinement_sessions(self, target: str, limit: int = 10) -> list[RefinementSession]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM refinement_sessions WHERE output_dir=? AND target=?
                ORDER BY started_at DESC LIMIT ?
                """,
                (self.output_dir, target, limit),
            ).fetchall()
            return [self._row_to_refinement_session(row) for row in rows]

    # -- Target state methods -------------------------------------------------

    def get_status(self, target: str) -> TargetStatus:
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM target_state WHERE target=? AND output_dir=?",
                (target, self.output_dir),
            ).fetchone()
            if row is None:
                return TargetStatus.PENDING
            return TargetStatus(row["status"])

    def set_status(self, target: str, status: TargetStatus) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO target_state (target, output_dir, status, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(target, output_dir) DO UPDATE SET
                    status=excluded.status,
                    updated_at=excluded.updated_at
                """,
                (target, self.output_dir, _enum_value(status), _now_iso()),
            )
            self._conn.commit()

    def list_targets(self) -> list[tuple[str, TargetStatus]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT target, status FROM target_state WHERE output_dir=? ORDER BY target",
                (self.output_dir,),
            ).fetchall()
            return [(row["target"], TargetStatus(row["status"])) for row in rows]

    def reset(self, target: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM target_state WHERE target=? AND output_dir=?",
                (target, self.output_dir),
            )
            self._conn.commit()

    def reset_all(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM target_state WHERE output_dir=?", (self.output_dir,))
            self._conn.commit()

    # -- Lifecycle --------------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            self._conn.close()
