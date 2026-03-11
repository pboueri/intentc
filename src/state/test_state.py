"""Tests for the state management package."""

import json
import os
from datetime import datetime, timezone

from core.types import BuildResult, TargetStatus
from state.manager import FileStateManager, _output_dir_key, new_state_manager


# Default output dir used by most tests
_OUTPUT_DIR = "/tmp/build"


def _make_build_result(
    target: str = "auth",
    generation_id: str = "gen-1234567890",
    success: bool = True,
) -> BuildResult:
    return BuildResult(
        target=target,
        generation_id=generation_id,
        success=success,
        error="" if success else "build failed",
        generated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        files=["src/auth.py"],
        output_dir=_OUTPUT_DIR,
    )


def _setup(tmp_path, output_dir=_OUTPUT_DIR) -> FileStateManager:
    """Create, initialize, and scope a state manager for tests."""
    mgr = FileStateManager(str(tmp_path))
    mgr.initialize()
    mgr.set_output_dir(output_dir)
    return mgr


def _scoped_state_dir(tmp_path, output_dir=_OUTPUT_DIR) -> str:
    """Return the expected scoped state dir for a given output dir."""
    key = _output_dir_key(output_dir)
    return os.path.join(tmp_path, ".intentc", "state", key)


def test_initialize_creates_top_level_state_dir(tmp_path):
    mgr = FileStateManager(str(tmp_path))
    mgr.initialize()

    assert os.path.isdir(os.path.join(tmp_path, ".intentc", "state"))


def test_set_output_dir_creates_scoped_structure(tmp_path):
    mgr = _setup(tmp_path)
    scoped = _scoped_state_dir(tmp_path)

    assert os.path.isdir(scoped)
    assert os.path.isdir(os.path.join(scoped, "builds"))


def test_initialize_is_idempotent(tmp_path):
    mgr = FileStateManager(str(tmp_path))
    mgr.initialize()
    mgr.initialize()
    mgr.set_output_dir(_OUTPUT_DIR)
    mgr.set_output_dir(_OUTPUT_DIR)

    scoped = _scoped_state_dir(tmp_path)
    assert os.path.isdir(os.path.join(scoped, "builds"))


def test_operations_fail_without_set_output_dir(tmp_path):
    mgr = FileStateManager(str(tmp_path))
    mgr.initialize()

    try:
        mgr.get_target_status("auth")
        assert False, "Expected RuntimeError"
    except RuntimeError as e:
        assert "set_output_dir" in str(e)


def test_save_and_get_latest_roundtrip(tmp_path):
    mgr = _setup(tmp_path)

    result = _make_build_result()
    mgr.save_build_result(result)

    latest = mgr.get_latest_build_result("auth")
    assert latest.target == "auth"
    assert latest.generation_id == "gen-1234567890"
    assert latest.success is True
    assert latest.error == ""
    assert latest.files == ["src/auth.py"]
    assert latest.output_dir == _OUTPUT_DIR


def test_save_creates_generation_file(tmp_path):
    mgr = _setup(tmp_path)

    result = _make_build_result()
    mgr.save_build_result(result)

    scoped = _scoped_state_dir(tmp_path)
    gen_path = os.path.join(scoped, "builds", "auth", "gen-1234567890.json")
    assert os.path.isfile(gen_path)

    with open(gen_path) as f:
        data = json.load(f)
    assert data["target"] == "auth"
    assert data["version"] == 1


def test_get_build_result_by_generation_id(tmp_path):
    mgr = _setup(tmp_path)

    result = _make_build_result()
    mgr.save_build_result(result)

    fetched = mgr.get_build_result("auth", "gen-1234567890")
    assert fetched.target == "auth"
    assert fetched.generation_id == "gen-1234567890"


def test_get_latest_raises_when_no_builds(tmp_path):
    mgr = _setup(tmp_path)

    try:
        mgr.get_latest_build_result("nonexistent")
        assert False, "Expected FileNotFoundError"
    except FileNotFoundError:
        pass


def test_get_build_result_raises_for_missing_generation(tmp_path):
    mgr = _setup(tmp_path)

    try:
        mgr.get_build_result("auth", "gen-missing")
        assert False, "Expected FileNotFoundError"
    except FileNotFoundError:
        pass


def test_get_target_status_returns_pending_for_unknown(tmp_path):
    mgr = _setup(tmp_path)

    status = mgr.get_target_status("unknown-target")
    assert status == TargetStatus.PENDING


def test_update_target_status_persists(tmp_path):
    mgr = _setup(tmp_path)

    mgr.update_target_status("auth", TargetStatus.BUILDING)
    assert mgr.get_target_status("auth") == TargetStatus.BUILDING

    mgr.update_target_status("auth", TargetStatus.BUILT)
    assert mgr.get_target_status("auth") == TargetStatus.BUILT


def test_save_build_result_updates_status(tmp_path):
    mgr = _setup(tmp_path)

    success_result = _make_build_result(success=True)
    mgr.save_build_result(success_result)
    assert mgr.get_target_status("auth") == TargetStatus.BUILT

    fail_result = _make_build_result(generation_id="gen-9999999999", success=False)
    mgr.save_build_result(fail_result)
    assert mgr.get_target_status("auth") == TargetStatus.FAILED


def test_list_build_results(tmp_path):
    mgr = _setup(tmp_path)

    r1 = _make_build_result(generation_id="gen-0000000001")
    r2 = _make_build_result(generation_id="gen-0000000002")
    mgr.save_build_result(r1)
    mgr.save_build_result(r2)

    results = mgr.list_build_results("auth")
    assert len(results) == 2
    assert results[0].generation_id == "gen-0000000001"
    assert results[1].generation_id == "gen-0000000002"


def test_list_build_results_empty_for_unknown_target(tmp_path):
    mgr = _setup(tmp_path)

    results = mgr.list_build_results("nonexistent")
    assert results == []


def test_missing_status_json_handled_gracefully(tmp_path):
    mgr = _setup(tmp_path)

    # No status.json exists yet
    status = mgr.get_target_status("auth")
    assert status == TargetStatus.PENDING


def test_corrupted_status_json_handled_gracefully(tmp_path):
    mgr = _setup(tmp_path)

    # Write garbage to the scoped status.json
    scoped = _scoped_state_dir(tmp_path)
    status_path = os.path.join(scoped, "status.json")
    with open(status_path, "w") as f:
        f.write("not valid json{{{")

    status = mgr.get_target_status("auth")
    assert status == TargetStatus.PENDING


def test_forward_compatibility_extra_fields_in_build_result(tmp_path):
    mgr = _setup(tmp_path)

    # Write a build result with extra fields directly
    scoped = _scoped_state_dir(tmp_path)
    target_dir = os.path.join(scoped, "builds", "auth")
    os.makedirs(target_dir, exist_ok=True)

    data = {
        "version": 1,
        "target": "auth",
        "generation_id": "gen-1234567890",
        "success": True,
        "error": "",
        "generated_at": "2024-01-01T00:00:00+00:00",
        "files": ["src/auth.py"],
        "output_dir": _OUTPUT_DIR,
        "future_field": "some-value",
        "another_new_field": 42,
    }

    latest_path = os.path.join(target_dir, "latest.json")
    with open(latest_path, "w") as f:
        json.dump(data, f)

    gen_path = os.path.join(target_dir, "gen-1234567890.json")
    with open(gen_path, "w") as f:
        json.dump(data, f)

    # Should parse without error, ignoring extra fields
    result = mgr.get_latest_build_result("auth")
    assert result.target == "auth"
    assert result.success is True

    result2 = mgr.get_build_result("auth", "gen-1234567890")
    assert result2.generation_id == "gen-1234567890"


def test_forward_compatibility_extra_fields_in_status(tmp_path):
    mgr = _setup(tmp_path)

    # Write status.json with extra fields
    scoped = _scoped_state_dir(tmp_path)
    status_path = os.path.join(scoped, "status.json")
    data = {
        "version": 1,
        "targets": {"auth": "built"},
        "unknown_key": "should be tolerated",
    }
    with open(status_path, "w") as f:
        json.dump(data, f)

    status = mgr.get_target_status("auth")
    assert status == TargetStatus.BUILT


def test_future_schema_version_warns(tmp_path):
    mgr = _setup(tmp_path)

    # Write status.json with a future version
    scoped = _scoped_state_dir(tmp_path)
    status_path = os.path.join(scoped, "status.json")
    data = {"version": 99, "targets": {"auth": "built"}}
    with open(status_path, "w") as f:
        json.dump(data, f)

    import warnings

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        status = mgr.get_target_status("auth")
        assert status == TargetStatus.BUILT
        assert len(w) == 1
        assert "unknown schema version" in str(w[0].message).lower()


def test_status_json_schema(tmp_path):
    mgr = _setup(tmp_path)

    mgr.update_target_status("auth", TargetStatus.BUILT)
    mgr.update_target_status("core", TargetStatus.PENDING)

    scoped = _scoped_state_dir(tmp_path)
    status_path = os.path.join(scoped, "status.json")
    with open(status_path) as f:
        data = json.load(f)

    assert data["version"] == 1
    assert isinstance(data["targets"], dict)
    assert data["targets"]["auth"] == "built"
    assert data["targets"]["core"] == "pending"


def test_build_result_json_schema(tmp_path):
    mgr = _setup(tmp_path)

    result = _make_build_result()
    mgr.save_build_result(result)

    scoped = _scoped_state_dir(tmp_path)
    gen_path = os.path.join(scoped, "builds", "auth", "gen-1234567890.json")
    with open(gen_path) as f:
        data = json.load(f)

    assert data["version"] == 1
    assert data["target"] == "auth"
    assert data["generation_id"] == "gen-1234567890"
    assert data["success"] is True
    assert data["error"] == ""
    assert data["files"] == ["src/auth.py"]
    assert data["output_dir"] == _OUTPUT_DIR


def test_latest_is_updated_on_each_save(tmp_path):
    mgr = _setup(tmp_path)

    r1 = _make_build_result(generation_id="gen-0000000001")
    mgr.save_build_result(r1)
    assert mgr.get_latest_build_result("auth").generation_id == "gen-0000000001"

    r2 = _make_build_result(generation_id="gen-0000000002")
    mgr.save_build_result(r2)
    assert mgr.get_latest_build_result("auth").generation_id == "gen-0000000002"


def test_multiple_targets_independent(tmp_path):
    mgr = _setup(tmp_path)

    auth_result = _make_build_result(target="auth", generation_id="gen-0000000001")
    core_result = _make_build_result(target="core", generation_id="gen-0000000002")

    mgr.save_build_result(auth_result)
    mgr.save_build_result(core_result)

    assert mgr.get_latest_build_result("auth").target == "auth"
    assert mgr.get_latest_build_result("core").target == "core"
    assert mgr.get_target_status("auth") == TargetStatus.BUILT
    assert mgr.get_target_status("core") == TargetStatus.BUILT

    auth_results = mgr.list_build_results("auth")
    core_results = mgr.list_build_results("core")
    assert len(auth_results) == 1
    assert len(core_results) == 1


def test_new_state_manager_factory(tmp_path):
    mgr = new_state_manager(str(tmp_path))
    assert isinstance(mgr, FileStateManager)
    assert mgr.project_root == str(tmp_path)


# -- New tests for output-dir scoping, reset_target, and reset_all --


def test_different_output_dirs_are_independent(tmp_path):
    mgr = FileStateManager(str(tmp_path))
    mgr.initialize()

    # Write to output dir "src"
    mgr.set_output_dir("/tmp/src")
    mgr.update_target_status("auth", TargetStatus.BUILT)
    assert mgr.get_target_status("auth") == TargetStatus.BUILT

    # Switch to output dir "src2" — should be independent
    mgr.set_output_dir("/tmp/src2")
    assert mgr.get_target_status("auth") == TargetStatus.PENDING

    # Original still intact
    mgr.set_output_dir("/tmp/src")
    assert mgr.get_target_status("auth") == TargetStatus.BUILT


def test_reset_target_resets_status_and_removes_builds(tmp_path):
    mgr = _setup(tmp_path)

    result = _make_build_result()
    mgr.save_build_result(result)
    assert mgr.get_target_status("auth") == TargetStatus.BUILT
    assert mgr.list_build_results("auth") != []

    mgr.reset_target("auth")

    assert mgr.get_target_status("auth") == TargetStatus.PENDING
    assert mgr.list_build_results("auth") == []

    try:
        mgr.get_latest_build_result("auth")
        assert False, "Expected FileNotFoundError"
    except FileNotFoundError:
        pass


def test_reset_target_leaves_other_targets(tmp_path):
    mgr = _setup(tmp_path)

    mgr.save_build_result(_make_build_result(target="auth", generation_id="gen-1"))
    mgr.save_build_result(_make_build_result(target="core", generation_id="gen-2"))

    mgr.reset_target("auth")

    assert mgr.get_target_status("auth") == TargetStatus.PENDING
    assert mgr.get_target_status("core") == TargetStatus.BUILT


def test_reset_all_clears_everything(tmp_path):
    mgr = _setup(tmp_path)

    mgr.save_build_result(_make_build_result(target="auth", generation_id="gen-1"))
    mgr.save_build_result(_make_build_result(target="core", generation_id="gen-2"))

    mgr.reset_all()

    assert mgr.get_target_status("auth") == TargetStatus.PENDING
    assert mgr.get_target_status("core") == TargetStatus.PENDING
    assert mgr.list_build_results("auth") == []
    assert mgr.list_build_results("core") == []


def test_reset_all_only_affects_current_output_dir(tmp_path):
    mgr = FileStateManager(str(tmp_path))
    mgr.initialize()

    # Build into src
    mgr.set_output_dir("/tmp/src")
    mgr.update_target_status("auth", TargetStatus.BUILT)

    # Build into src2
    mgr.set_output_dir("/tmp/src2")
    mgr.update_target_status("auth", TargetStatus.BUILT)

    # Reset only src2
    mgr.reset_all()
    assert mgr.get_target_status("auth") == TargetStatus.PENDING

    # src should be unaffected
    mgr.set_output_dir("/tmp/src")
    assert mgr.get_target_status("auth") == TargetStatus.BUILT


def test_output_dir_key_uses_basename():
    assert _output_dir_key("/home/user/project/src") == "src"
    assert _output_dir_key("/tmp/build") == "build"
    assert _output_dir_key("relative/path/output") == "output"


class TestPathBasedTargetNames:
    """Test that target names containing '/' work correctly."""

    def test_save_and_load_nested_target(self, tmp_path) -> None:
        sm = FileStateManager(str(tmp_path))
        sm.initialize()
        sm.set_output_dir(str(tmp_path / "output"))

        result = BuildResult(
            target="core/parser",
            generation_id="gen-123",
            success=True,
            files=["src/parser.py"],
            output_dir=str(tmp_path / "output"),
        )
        sm.save_build_result(result)
        loaded = sm.get_latest_build_result("core/parser")
        assert loaded.target == "core/parser"
        assert loaded.success is True

    def test_status_with_slashes(self, tmp_path) -> None:
        sm = FileStateManager(str(tmp_path))
        sm.initialize()
        sm.set_output_dir(str(tmp_path / "output"))

        sm.update_target_status("core/parser", TargetStatus.BUILT)
        assert sm.get_target_status("core/parser") == TargetStatus.BUILT

    def test_reset_nested_target(self, tmp_path) -> None:
        sm = FileStateManager(str(tmp_path))
        sm.initialize()
        sm.set_output_dir(str(tmp_path / "output"))

        sm.update_target_status("build/state", TargetStatus.BUILT)
        sm.reset_target("build/state")
        assert sm.get_target_status("build/state") == TargetStatus.PENDING

    def test_list_builds_nested_target(self, tmp_path) -> None:
        sm = FileStateManager(str(tmp_path))
        sm.initialize()
        sm.set_output_dir(str(tmp_path / "output"))

        result = BuildResult(
            target="validation/file_check",
            generation_id="gen-456",
            success=True,
            files=[],
            output_dir=str(tmp_path / "output"),
        )
        sm.save_build_result(result)
        builds = sm.list_build_results("validation/file_check")
        assert len(builds) == 1
        assert builds[0].target == "validation/file_check"
