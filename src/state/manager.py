"""State management for intentc - persists build state as JSON files."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import warnings
from typing import Any

from core.types import BuildResult, TargetStatus

logger = logging.getLogger(__name__)

_STATUS_VERSION = 1


class _StatusFile:
    """In-memory representation of status.json."""

    def __init__(self, version: int = _STATUS_VERSION, targets: dict[str, str] | None = None):
        self.version = version
        self.targets: dict[str, str] = targets or {}

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version, "targets": dict(self.targets)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> _StatusFile:
        version = data.get("version", _STATUS_VERSION)
        if version != _STATUS_VERSION:
            warnings.warn(
                f"status.json has unknown schema version {version}, attempting best-effort parse",
                stacklevel=2,
            )
        targets = data.get("targets", {})
        if not isinstance(targets, dict):
            targets = {}
        return cls(version=version, targets={str(k): str(v) for k, v in targets.items()})


def _atomic_write(path: str, data: str) -> None:
    """Write data to a file atomically using write-to-temp-then-rename."""
    dir_name = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(data)
        os.replace(tmp_path, path)
    except BaseException:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _build_result_to_dict(result: BuildResult) -> dict[str, Any]:
    """Serialize a BuildResult to a JSON-compatible dict with version."""
    data = json.loads(result.model_dump_json())
    data["version"] = 1
    return data


def _build_result_from_dict(data: dict[str, Any]) -> BuildResult:
    """Deserialize a BuildResult from a dict, tolerating unknown fields."""
    version = data.get("version", 1)
    if version != 1:
        warnings.warn(
            f"Build result has unknown schema version {version}, attempting best-effort parse",
            stacklevel=2,
        )
    return BuildResult.model_validate(data)


def _output_dir_key(output_dir: str) -> str:
    """Derive a filesystem-safe key from an output directory path.

    Uses the directory name relative to the project root when possible,
    otherwise sanitizes the absolute path.
    """
    # Normalise to an absolute path so we get a deterministic key
    abs_path = os.path.abspath(output_dir)
    # Use the basename if it is a simple relative-looking name
    basename = os.path.basename(abs_path)
    if basename:
        return basename
    # Fallback: replace separators
    return abs_path.replace(os.sep, "_").strip("_")


class FileStateManager:
    """Manages build state on disk under .intentc/state/.

    State is **scoped by output directory** — each output directory gets
    its own ``status.json`` and ``builds/`` tree.  Call ``set_output_dir``
    before any status or build-result operations.
    """

    def __init__(self, project_root: str) -> None:
        self.project_root = project_root
        self.state_dir = os.path.join(project_root, ".intentc", "state")
        self._scoped_dir: str | None = None  # set by set_output_dir

    # -- Directory helpers --

    def _ensure_scoped(self) -> str:
        """Return the scoped state directory, raising if not set."""
        if self._scoped_dir is None:
            raise RuntimeError(
                "set_output_dir must be called before any state operations"
            )
        return self._scoped_dir

    @property
    def _status_path(self) -> str:
        return os.path.join(self._ensure_scoped(), "status.json")

    @property
    def _builds_dir(self) -> str:
        return os.path.join(self._ensure_scoped(), "builds")

    def _target_dir(self, target_name: str) -> str:
        return os.path.join(self._builds_dir, target_name)

    def _latest_path(self, target_name: str) -> str:
        return os.path.join(self._target_dir(target_name), "latest.json")

    def _generation_path(self, target_name: str, generation_id: str) -> str:
        return os.path.join(self._target_dir(target_name), f"{generation_id}.json")

    # -- Public API --

    def initialize(self) -> None:
        """Create the top-level state directory."""
        os.makedirs(self.state_dir, exist_ok=True)

    def set_output_dir(self, output_dir: str) -> None:
        """Set the active output directory, scoping all subsequent operations.

        Creates ``.intentc/state/{output-dir-key}/`` if it does not exist.
        """
        key = _output_dir_key(output_dir)
        self._scoped_dir = os.path.join(self.state_dir, key)
        os.makedirs(self._scoped_dir, exist_ok=True)
        os.makedirs(self._builds_dir, exist_ok=True)

    def save_build_result(self, result: BuildResult) -> None:
        """Persist a build result and update latest + status."""
        target_dir = self._target_dir(result.target)
        os.makedirs(target_dir, exist_ok=True)

        data = _build_result_to_dict(result)
        json_str = json.dumps(data, indent=2)

        # Write generation-specific file
        gen_path = self._generation_path(result.target, result.generation_id)
        _atomic_write(gen_path, json_str)

        # Write latest.json
        latest_path = self._latest_path(result.target)
        _atomic_write(latest_path, json_str)

        # Update status
        status = TargetStatus.BUILT if result.success else TargetStatus.FAILED
        self.update_target_status(result.target, status)

    def get_latest_build_result(self, target_name: str) -> BuildResult:
        """Read the latest build result for a target.

        Raises FileNotFoundError if no builds exist.
        """
        path = self._latest_path(target_name)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"No builds found for target '{target_name}'")
        with open(path) as f:
            data = json.load(f)
        return _build_result_from_dict(data)

    def get_build_result(self, target_name: str, generation_id: str) -> BuildResult:
        """Read a specific build result by generation ID.

        Raises FileNotFoundError if the generation does not exist.
        """
        path = self._generation_path(target_name, generation_id)
        if not os.path.isfile(path):
            raise FileNotFoundError(
                f"Build result not found for target '{target_name}', "
                f"generation '{generation_id}'"
            )
        with open(path) as f:
            data = json.load(f)
        return _build_result_from_dict(data)

    def get_target_status(self, target_name: str) -> TargetStatus:
        """Get the status of a target. Returns PENDING if unknown."""
        status_file = self._read_status_file()
        raw = status_file.targets.get(target_name)
        if raw is None:
            return TargetStatus.PENDING
        try:
            return TargetStatus(raw)
        except ValueError:
            logger.warning("Unknown target status '%s' for '%s', treating as pending", raw, target_name)
            return TargetStatus.PENDING

    def update_target_status(self, target_name: str, status: TargetStatus) -> None:
        """Update a target's status in status.json atomically."""
        status_file = self._read_status_file()
        status_file.targets[target_name] = status.value
        json_str = json.dumps(status_file.to_dict(), indent=2)
        _atomic_write(self._status_path, json_str)

    def list_build_results(self, target_name: str) -> list[BuildResult]:
        """List all build results for a target, sorted by generation ID."""
        target_dir = self._target_dir(target_name)
        if not os.path.isdir(target_dir):
            return []

        results: list[BuildResult] = []
        for filename in sorted(os.listdir(target_dir)):
            if filename == "latest.json":
                continue
            if not filename.endswith(".json"):
                continue
            path = os.path.join(target_dir, filename)
            try:
                with open(path) as f:
                    data = json.load(f)
                results.append(_build_result_from_dict(data))
            except (json.JSONDecodeError, Exception) as exc:
                logger.warning("Skipping unreadable build result %s: %s", path, exc)
        return results

    def reset_target(self, target_name: str) -> None:
        """Reset a target's status to pending and remove its build results."""
        import shutil

        # Set status to pending
        status_file = self._read_status_file()
        status_file.targets[target_name] = TargetStatus.PENDING.value
        json_str = json.dumps(status_file.to_dict(), indent=2)
        _atomic_write(self._status_path, json_str)

        # Remove the target's build result directory
        target_dir = self._target_dir(target_name)
        if os.path.isdir(target_dir):
            shutil.rmtree(target_dir)

    def reset_all(self) -> None:
        """Reset all targets for the current output directory.

        Removes the entire scoped state directory and recreates it empty.
        """
        import shutil

        scoped = self._ensure_scoped()
        if os.path.isdir(scoped):
            shutil.rmtree(scoped)
        os.makedirs(scoped, exist_ok=True)
        os.makedirs(self._builds_dir, exist_ok=True)

    # -- Internal helpers --

    def _read_status_file(self) -> _StatusFile:
        """Read status.json, returning an empty status if missing."""
        if not os.path.isfile(self._status_path):
            return _StatusFile()
        try:
            with open(self._status_path) as f:
                data = json.load(f)
            return _StatusFile.from_dict(data)
        except (json.JSONDecodeError, Exception) as exc:
            logger.warning("Failed to read status.json, starting fresh: %s", exc)
            return _StatusFile()


def new_state_manager(project_root: str) -> FileStateManager:
    """Create a new FileStateManager for the given project root."""
    return FileStateManager(project_root)
