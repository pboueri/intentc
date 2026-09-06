"""Tests for intentc.build.state."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from intentc.build.state import (
    BuildResult,
    BuildStep,
    GitVersionControl,
    StateManager,
    TargetStatus,
    VersionControl,
)
from intentc.build.state.state import response_file_name
from intentc.build.storage import SQLiteBackend
from intentc.core import FeatureNode, IntentFile, Project, ProjectIntent


def make_manager(tmp_path: Path, output_dir: str = "src") -> StateManager:
    return StateManager(base_dir=tmp_path, output_dir=output_dir)


def make_result(target: str, **overrides) -> BuildResult:
    defaults = dict(
        target=target,
        generation_id="gen-1",
        status=TargetStatus.BUILT,
        steps=[
            BuildStep(phase="resolve_deps", status="success", duration_secs=0.1, summary="resolved"),
            BuildStep(phase="build", status="success", duration_secs=3.5, summary="built module"),
        ],
        commit_id="deadbeef",
        total_duration_secs=3.6,
        timestamp="2026-09-05T10:00:00",
        source_hash="hash-abc",
        files_created=["a.py"],
        files_modified=["__init__.py"],
        attempts=1,
    )
    defaults.update(overrides)
    return BuildResult(**defaults)


def make_project(edges: dict[str, list[str]]) -> Project:
    """Build a minimal Project whose features depend_on as described by `edges`
    (feature -> list of features it depends on)."""
    features: dict[str, FeatureNode] = {}
    for name, deps in edges.items():
        intent = IntentFile(name=name.rsplit("/", 1)[-1], depends_on=list(deps), body="body")
        features[name] = FeatureNode(path=name, intents=[intent])
    return Project(
        project_intent=ProjectIntent(name="proj", body="body"),
        implementations={},
        assertions=[],
        features=features,
    )


# ---------------------------------------------------------------------------
# Construction / defaults
# ---------------------------------------------------------------------------


def test_default_status_is_pending_for_unknown_target(tmp_path):
    manager = make_manager(tmp_path)
    try:
        assert manager.get_status("never/built") == TargetStatus.PENDING
        assert manager.get_build_result("never/built") is None
    finally:
        manager.backend.close()


def test_constructs_own_sqlite_backend_when_none_given(tmp_path):
    manager = make_manager(tmp_path)
    try:
        assert isinstance(manager.backend, SQLiteBackend)
        db_path = tmp_path / ".intentc" / "state" / "src" / "intentc.db"
        assert db_path.is_file()
    finally:
        manager.backend.close()


def test_response_dirs_are_created_lazily_under_intentc_state(tmp_path):
    manager = make_manager(tmp_path)
    try:
        build_dir = manager.build_response_dir
        val_dir = manager.val_response_dir
        assert build_dir == tmp_path / ".intentc" / "state" / "src" / "responses" / "build"
        assert val_dir == tmp_path / ".intentc" / "state" / "src" / "responses" / "val"
        assert build_dir.is_dir()
        assert val_dir.is_dir()
    finally:
        manager.backend.close()


def test_response_file_name_replaces_slashes_and_has_json_extension():
    name = response_file_name("build/state")
    assert name.startswith("build_state-")
    assert name.endswith(".json")
    assert "/" not in name

    # Unique across calls.
    assert response_file_name("build/state") != response_file_name("build/state")


# ---------------------------------------------------------------------------
# Full roundtrip through a real SQLiteBackend
# ---------------------------------------------------------------------------


def test_full_state_roundtrip_through_real_backend(tmp_path):
    manager_a = make_manager(tmp_path)
    result = make_result("build/state")
    try:
        build_result_id = manager_a.save_build_result(
            "build/state", result, git_diff="diff --git a/a.py b/a.py\n+content"
        )
        assert isinstance(build_result_id, int)
        assert manager_a.get_status("build/state") == TargetStatus.BUILT
    finally:
        manager_a.backend.close()

    # A brand new StateManager, same database path.
    manager_b = make_manager(tmp_path)
    try:
        assert manager_b.get_status("build/state") == TargetStatus.BUILT

        fetched = manager_b.get_build_result("build/state")
        assert fetched is not None
        assert fetched.target == "build/state"
        assert fetched.status == TargetStatus.BUILT
        assert fetched.commit_id == "deadbeef"
        assert fetched.timestamp == "2026-09-05T10:00:00"
        assert fetched.source_hash == "hash-abc"
        assert fetched.total_duration_secs == 3.6
        assert fetched.attempts == 1
        assert fetched.files_created == ["a.py"]
        assert fetched.files_modified == ["__init__.py"]
        assert [step.phase for step in fetched.steps] == ["resolve_deps", "build"]
        assert fetched.steps[1].summary == "built module"

        # Missing target still returns defaults, not an error.
        assert manager_b.get_status("never/built") == TargetStatus.PENDING
        assert manager_b.get_build_result("never/built") is None
    finally:
        manager_b.backend.close()


# ---------------------------------------------------------------------------
# Build history is append-only
# ---------------------------------------------------------------------------


def test_build_history_is_append_only_and_target_state_points_to_latest(tmp_path):
    manager = make_manager(tmp_path)
    try:
        id_1 = manager.save_build_result("build/state", make_result("build/state", commit_id="sha-1"))
        id_2 = manager.save_build_result("build/state", make_result("build/state", commit_id="sha-2"))
        id_3 = manager.save_build_result(
            "build/state", make_result("build/state", commit_id="sha-3", status=TargetStatus.FAILED)
        )

        assert len({id_1, id_2, id_3}) == 3

        history = manager.get_build_history("build/state")
        assert [entry.commit_id for entry in history] == ["sha-3", "sha-2", "sha-1"]

        # Previous entries are never overwritten or deleted.
        assert len(history) == 3

        # target_state always points to the latest result.
        latest = manager.get_build_result("build/state")
        assert latest is not None
        assert latest.commit_id == "sha-3"
        assert manager.get_status("build/state") == TargetStatus.FAILED

        limited = manager.get_build_history("build/state", limit=2)
        assert [entry.commit_id for entry in limited] == ["sha-3", "sha-2"]
    finally:
        manager.backend.close()


# ---------------------------------------------------------------------------
# DAG-aware operations
# ---------------------------------------------------------------------------


def test_mark_dependents_outdated_walks_the_dag(tmp_path):
    # a <- b <- c  (c depends on b, b depends on a)
    project = make_project({"a": [], "b": ["a"], "c": ["b"], "unrelated": []})
    manager = make_manager(tmp_path)
    try:
        manager.set_status("a", TargetStatus.BUILT)
        manager.set_status("b", TargetStatus.BUILT)
        manager.set_status("c", TargetStatus.BUILT)
        manager.set_status("unrelated", TargetStatus.BUILT)

        manager.mark_dependents_outdated("a", project)

        assert manager.get_status("a") == TargetStatus.BUILT
        assert manager.get_status("b") == TargetStatus.OUTDATED
        assert manager.get_status("c") == TargetStatus.OUTDATED
        assert manager.get_status("unrelated") == TargetStatus.BUILT
    finally:
        manager.backend.close()


def test_reset_clears_single_target_without_affecting_others(tmp_path):
    manager = make_manager(tmp_path)
    try:
        manager.save_build_result("build/state", make_result("build/state"))
        manager.save_build_result("build/storage", make_result("build/storage"))

        manager.reset("build/state")

        assert manager.get_status("build/state") == TargetStatus.PENDING
        assert manager.get_build_result("build/state") is None
        assert manager.get_status("build/storage") == TargetStatus.BUILT
        assert manager.get_build_result("build/storage") is not None
    finally:
        manager.backend.close()


def test_reset_all_clears_every_target_for_this_output_dir(tmp_path):
    manager = make_manager(tmp_path)
    try:
        manager.set_status("build/state", TargetStatus.BUILT)
        manager.set_status("build/storage", TargetStatus.BUILT)

        manager.reset_all()

        assert manager.list_targets() == []
        assert manager.get_status("build/state") == TargetStatus.PENDING
    finally:
        manager.backend.close()


def test_list_targets_returns_all_tracked_targets(tmp_path):
    manager = make_manager(tmp_path)
    try:
        manager.set_status("build/state", TargetStatus.BUILT)
        manager.set_status("build/storage", TargetStatus.PENDING)

        assert dict(manager.list_targets()) == {
            "build/state": TargetStatus.BUILT,
            "build/storage": TargetStatus.PENDING,
        }
    finally:
        manager.backend.close()


# ---------------------------------------------------------------------------
# Response file lifecycle
# ---------------------------------------------------------------------------


def test_response_file_is_stored_in_db_and_deleted_from_disk(tmp_path):
    manager = make_manager(tmp_path)
    try:
        build_result_id = manager.save_build_result("build/state", make_result("build/state"))

        response_path = manager.build_response_dir / response_file_name("build/state")
        response_payload = {"status": "success", "summary": "did the thing"}
        response_path.write_text(json.dumps(response_payload), encoding="utf-8")

        # Caller: read the response file, persist it, then delete it.
        stored = json.loads(response_path.read_text(encoding="utf-8"))
        manager.backend.save_agent_response(
            build_result_id=build_result_id,
            validation_result_id=None,
            response_type="build",
            response_json=stored,
        )
        response_path.unlink()

        assert not response_path.exists()
        assert list(manager.build_response_dir.iterdir()) == []

        raw = manager.backend._conn.execute(
            "SELECT response_type, response_json FROM agent_responses WHERE build_result_id=?",
            (build_result_id,),
        ).fetchone()
        assert raw["response_type"] == "build"
        assert json.loads(raw["response_json"]) == response_payload
    finally:
        manager.backend.close()


# ---------------------------------------------------------------------------
# VersionControl / GitVersionControl
# ---------------------------------------------------------------------------


def _init_repo(repo_dir: Path) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo_dir, check=True)


def test_git_version_control_is_a_version_control():
    assert VersionControl.__abstractmethods__
    with pytest.raises(TypeError):
        VersionControl()


def test_checkpoint_creates_a_commit_even_with_no_changes(tmp_path):
    _init_repo(tmp_path)
    vc = GitVersionControl(tmp_path)

    sha = vc.checkpoint("empty checkpoint for build/state")

    assert isinstance(sha, str)
    assert len(sha) == 40
    assert not vc.has_changes()


def test_checkpoint_and_diff_roundtrip(tmp_path):
    _init_repo(tmp_path)
    vc = GitVersionControl(tmp_path)

    first_sha = vc.checkpoint("initial checkpoint")

    (tmp_path / "generated.py").write_text("value = 1\n", encoding="utf-8")
    assert vc.has_changes()
    second_sha = vc.checkpoint("build/state: add generated.py")
    assert not vc.has_changes()

    diff = vc.diff(first_sha, second_sha)
    assert "generated.py" in diff
    assert "value = 1" in diff


def test_diff_on_root_commit_returns_diff_against_empty_tree(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "seed.py").write_text("x = 1\n", encoding="utf-8")
    vc = GitVersionControl(tmp_path)
    root_sha = vc.checkpoint("root checkpoint with a file")

    diff = vc.diff(f"{root_sha}~1", root_sha)

    assert "seed.py" in diff
    assert "x = 1" in diff


def test_restore_reverts_output_dir_and_removes_new_files(tmp_path):
    _init_repo(tmp_path)
    output_dir = "out"
    (tmp_path / output_dir).mkdir()
    (tmp_path / output_dir / "keep.py").write_text("original = True\n", encoding="utf-8")
    vc = GitVersionControl(tmp_path, output_dir=output_dir)
    original_sha = vc.checkpoint("original state")

    (tmp_path / output_dir / "keep.py").write_text("original = False\n", encoding="utf-8")
    (tmp_path / output_dir / "new_file.py").write_text("new = True\n", encoding="utf-8")
    vc.checkpoint("mutated state")

    vc.restore(original_sha)

    assert (tmp_path / output_dir / "keep.py").read_text(encoding="utf-8") == "original = True\n"
    assert not (tmp_path / output_dir / "new_file.py").exists()


def test_has_changes_is_scoped_to_output_dir(tmp_path):
    _init_repo(tmp_path)
    output_dir = "out"
    (tmp_path / output_dir).mkdir()
    (tmp_path / "outside.py").write_text("a = 1\n", encoding="utf-8")
    vc = GitVersionControl(tmp_path, output_dir=output_dir)
    vc.checkpoint("initial")

    # A change outside the scoped output dir should not count.
    (tmp_path / "outside.py").write_text("a = 2\n", encoding="utf-8")
    assert not vc.has_changes()

    (tmp_path / output_dir / "inside.py").write_text("b = 1\n", encoding="utf-8")
    assert vc.has_changes()


def test_log_filters_by_target_via_grep(tmp_path):
    _init_repo(tmp_path)
    vc = GitVersionControl(tmp_path)
    sha_a = vc.checkpoint("build/state: first pass")
    (tmp_path / "f.py").write_text("v = 1\n", encoding="utf-8")
    sha_b = vc.checkpoint("build/storage: unrelated change")

    all_commits = vc.log()
    assert sha_a in all_commits
    assert sha_b in all_commits

    filtered = vc.log(target="build/state")
    assert sha_a in filtered
    assert sha_b not in filtered


def test_git_errors_are_raised_as_runtime_error_not_raw_subprocess_error(tmp_path):
    _init_repo(tmp_path)
    vc = GitVersionControl(tmp_path)

    with pytest.raises(RuntimeError):
        vc.restore("not-a-real-commit-sha")
