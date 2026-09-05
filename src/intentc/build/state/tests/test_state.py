"""Tests for StateManager and GitVersionControl (real SQLiteBackend, real git)."""

from __future__ import annotations

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
    response_file_name,
)
from intentc.build.storage import SQLiteBackend
from intentc.core import FeatureNode, IntentFile, Project, ProjectIntent


def _project() -> Project:
    return Project(
        project_intent=ProjectIntent(name="p"),
        features={
            "models": FeatureNode(path="models", intents=[IntentFile(name="models")]),
            "store": FeatureNode(path="store", intents=[IntentFile(name="store", depends_on=["models"])]),
            "api": FeatureNode(path="api", intents=[IntentFile(name="api", depends_on=["store"])]),
            "other": FeatureNode(path="other", intents=[IntentFile(name="other")]),
        },
    )


def _result(target: str, **overrides) -> BuildResult:
    fields = dict(
        target=target,
        generation_id="gen-1",
        status=TargetStatus.BUILT,
        steps=[BuildStep(phase="build", status="success", duration_secs=1.25, summary="ok")],
        commit_id="deadbeef",
        total_duration_secs=1.25,
        timestamp="2026-02-02T12:00:00",
        source_hash="abc",
        attempts=1,
    )
    fields.update(overrides)
    return BuildResult(**fields)


def test_state_roundtrip_through_new_manager(tmp_path: Path) -> None:
    sm = StateManager(tmp_path, "src")
    assert isinstance(sm.backend, SQLiteBackend)
    assert sm.get_status("models") is TargetStatus.PENDING
    assert sm.get_build_result("models") is None

    sm.save_build_result("models", _result("models"))
    sm.backend.close()

    sm2 = StateManager(tmp_path, "src")
    loaded = sm2.get_build_result("models")
    assert sm2.get_status("models") is TargetStatus.BUILT
    assert loaded is not None
    assert loaded.status is TargetStatus.BUILT
    assert loaded.steps[0].phase == "build" and loaded.steps[0].duration_secs == 1.25
    assert loaded.commit_id == "deadbeef"
    assert loaded.timestamp == "2026-02-02T12:00:00"
    assert loaded.source_hash == "abc"
    assert loaded.total_duration_secs == 1.25
    sm2.backend.close()


def test_build_history_append_only(tmp_path: Path) -> None:
    sm = StateManager(tmp_path, "src")
    sm.save_build_result("models", _result("models", generation_id="g1", status="failed"))
    sm.save_build_result("models", _result("models", generation_id="g2"))
    history = sm.get_build_history("models")
    assert [r.generation_id for r in history] == ["g2", "g1"]
    assert sm.get_status("models") is TargetStatus.BUILT


def test_mark_dependents_outdated_walks_dag(tmp_path: Path) -> None:
    sm = StateManager(tmp_path, "src")
    for t in ["models", "store", "api", "other"]:
        sm.save_build_result(t, _result(t))
    sm.set_status("api", TargetStatus.FAILED)
    changed = sm.mark_dependents_outdated("models", _project())
    assert changed == ["store"]  # api was failed, left alone
    assert sm.get_status("store") is TargetStatus.OUTDATED
    assert sm.get_status("api") is TargetStatus.FAILED
    assert sm.get_status("other") is TargetStatus.BUILT
    assert sm.get_status("models") is TargetStatus.BUILT


def test_reset_and_reset_all(tmp_path: Path) -> None:
    sm = StateManager(tmp_path, "src")
    sm.save_build_result("models", _result("models"))
    sm.save_build_result("store", _result("store"))
    sm.reset("models")
    assert sm.get_status("models") is TargetStatus.PENDING
    assert sm.get_status("store") is TargetStatus.BUILT
    assert sm.list_targets() == [("store", TargetStatus.BUILT)]
    sm.reset_all()
    assert sm.list_targets() == []


def test_response_dirs(tmp_path: Path) -> None:
    sm = StateManager(tmp_path, "src")
    assert sm.build_response_dir == tmp_path / ".intentc" / "state" / "src" / "responses" / "build"
    assert sm.val_response_dir == tmp_path / ".intentc" / "state" / "src" / "responses" / "val"
    assert sm.build_response_dir.is_dir() and sm.val_response_dir.is_dir()
    name = response_file_name("build/agents")
    assert name.startswith("build_agents-") and name.endswith(".json") and len(name) == len("build_agents-") + 8 + 5


def test_custom_backend_is_used(tmp_path: Path) -> None:
    backend = SQLiteBackend(tmp_path, "custom")
    sm = StateManager(tmp_path, "src", backend=backend)
    assert sm.backend is backend


# ---------------------------------------------------------------------------
# GitVersionControl
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "T")
    return tmp_path


def test_git_version_control_roundtrip(repo: Path) -> None:
    vc = GitVersionControl(repo)
    assert isinstance(vc, VersionControl)
    assert not vc.has_changes()
    (repo / "a.py").write_text("print(1)\n")
    assert vc.has_changes()

    first = vc.checkpoint("build models [gen:1]")
    assert len(first) == 40
    assert not vc.has_changes()
    assert "+print(1)" in vc.diff(f"{first}~1", first)  # root commit diffs against empty tree

    (repo / "a.py").write_text("print(2)\n")
    second = vc.checkpoint("build store [gen:1]")
    assert "-print(1)" in vc.diff(first, second)
    assert vc.log() == [second, first]
    assert vc.log("store") == [second]

    vc.restore(first)
    assert (repo / "a.py").read_text() == "print(1)\n"
    assert vc.has_changes()  # restored files are left uncommitted

    third = vc.checkpoint("empty is fine")
    assert third != second

    # Restoring to before the root commit removes files that did not exist then.
    vc.restore(f"{first}~1")
    assert not (repo / "a.py").exists()


def test_git_restore_is_scoped_to_output_dir(repo: Path) -> None:
    vc = GitVersionControl(repo, output_dir="src")
    (repo / "src").mkdir()
    (repo / "src" / "gen.py").write_text("gen\n")
    (repo / "notes.md").write_text("v1\n")
    first = vc.checkpoint("build a")
    (repo / "src" / "gen.py").write_text("gen2\n")
    (repo / "src" / "extra.py").write_text("untracked\n")
    (repo / "notes.md").write_text("v2 (user edit, must survive)\n")
    vc.restore(first)
    assert (repo / "src" / "gen.py").read_text() == "gen\n"
    assert not (repo / "src" / "extra.py").exists()
    assert (repo / "notes.md").read_text() == "v2 (user edit, must survive)\n"
    vc.restore(f"{first}~1")
    assert not (repo / "src" / "gen.py").exists()


def test_git_errors_are_runtime_errors(tmp_path: Path) -> None:
    vc = GitVersionControl(tmp_path / "not-a-repo")
    with pytest.raises(RuntimeError, match="git"):
        vc.checkpoint("x")
