"""Tests for the Builder pipeline (mock agent, mock version control, real SQLite state)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from intentc.build.agents import AgentError, AgentProfile, BuildContext, BuildResponse, MockAgent, ValidationResponse
from intentc.build.builder.builder import Builder, BuildOptions
from intentc.build.state import StateManager, TargetStatus, VersionControl
from intentc.core import load_project

PROFILE = AgentProfile(name="default", provider="cli", command="unused", retries=3)


class MockVersionControl(VersionControl):
    def __init__(self) -> None:
        self.checkpoints: list[str] = []
        self.restored: list[str] = []
        self.fail = False

    def checkpoint(self, message: str) -> str:
        if self.fail:
            raise RuntimeError("git broke")
        sha = uuid.uuid4().hex
        self.checkpoints.append(f"{sha}:{message}")
        return sha

    def diff(self, from_id: str, to_id: str) -> str:
        return f"diff {from_id}..{to_id}"

    def restore(self, commit_id: str) -> None:
        self.restored.append(commit_id)

    def log(self, target: str | None = None) -> list[str]:
        return [c.split(":")[0] for c in self.checkpoints if not target or target in c]

    def has_changes(self) -> bool:
        return False


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    intent = tmp_path / "intent"
    _write(intent / "project.ic", "---\nname: demo\n---\n\n# Demo\n")
    _write(intent / "implementations" / "default.ic", "---\nname: default\n---\n\nPython.\n")
    _write(intent / "models" / "models.ic", "---\nname: models\n---\n\nModels.\n")
    _write(intent / "store" / "store.ic", "---\nname: store\ndepends_on: [models]\n---\n\nStore.\n")
    _write(
        intent / "store" / "validations.icv",
        "target: store\nvalidations:\n  - name: store-ok\n    args:\n      rubric: the store must persist tasks correctly to disk\n",
    )
    _write(intent / "api" / "api.ic", "---\nname: api\ndepends_on: [store]\n---\n\nAPI.\n")
    (tmp_path / "src").mkdir()
    return tmp_path


class Harness:
    def __init__(self, workspace: Path, agent: MockAgent | None = None, profile: AgentProfile = PROFILE) -> None:
        self.root = workspace
        self.project = load_project(workspace / "intent")
        self.state = StateManager(workspace, "src")
        self.vc = MockVersionControl()
        self.agent = agent or MockAgent()
        self.logs: list[str] = []
        self.builder = Builder(self.project, self.state, self.vc, profile, log=self.logs.append, create_agent=lambda p: self.agent)

    def build(self, **kwargs) -> tuple:
        opts = BuildOptions(output_dir="src", **kwargs)
        return self.builder.build(opts)

    def reload(self) -> None:
        self.project = load_project(self.root / "intent")
        self.builder = Builder(self.project, self.state, self.vc, PROFILE, log=self.logs.append, create_agent=lambda p: self.agent)


def test_full_pipeline_order_and_steps(workspace: Path) -> None:
    h = Harness(workspace)
    results, error = h.build()
    assert error is None
    assert [r.target for r in results] == ["models", "store", "api"]
    assert all(r.status is TargetStatus.BUILT for r in results)
    assert [s.phase for s in results[0].steps] == ["resolve_deps", "build", "checkpoint"]
    assert [s.phase for s in results[1].steps] == ["resolve_deps", "build", "validate", "checkpoint"]
    gen_ids = {r.generation_id for r in results}
    assert len(gen_ids) == 1 and uuid.UUID(gen_ids.pop())
    assert all(r.commit_id and r.source_hash and r.attempts == 1 for r in results)
    assert len(h.agent.build_calls) == 3
    assert h.agent.build_calls[1].dependency_names == ["models"]
    assert h.agent.build_calls[1].feature_path == "store"
    assert h.agent.build_calls[1].implementation.body == "Python."
    assert len(h.agent.validate_calls) == 1
    assert all(h.state.get_status(t) is TargetStatus.BUILT for t in ["models", "store", "api"])
    assert len(h.vc.checkpoints) == 3 and "build models [gen:" in h.vc.checkpoints[0]
    assert h.state.backend.get_build_diff("models").startswith("diff ")
    gen = h.state.backend.get_generation(results[0].generation_id)
    assert gen["status"] == "completed" and gen["profile_name"] == "default"
    assert not list(h.state.build_response_dir.glob("*.json"))
    assert h.builder.next_targets() == []


def test_build_records_agent_response_and_manifest(workspace: Path) -> None:
    h = Harness(workspace, MockAgent(build_response=BuildResponse(status="success", summary="made", files_created=["a.py"], files_modified=["b.py"])))
    h.build(target="models")
    result = h.state.get_build_result("models")
    assert result.files_created == ["a.py"] and result.files_modified == ["b.py"]
    rows = h.state.backend._conn.execute("SELECT response_type, build_result_id FROM agent_responses").fetchall()
    assert rows and rows[0][0] == "build" and rows[0][1] is not None


def test_sandbox_paths_are_absolute_and_scoped(workspace: Path) -> None:
    seen: list[AgentProfile] = []
    h = Harness(workspace)
    agent = h.agent

    def factory(profile: AgentProfile) -> MockAgent:
        seen.append(profile)
        return agent

    h.builder._create_agent = factory
    h.build(target="store")
    profile = seen[-1]
    assert all(Path(p).is_absolute() for p in profile.sandbox_write_paths + profile.sandbox_read_paths)
    assert str((workspace / "src").resolve()) in profile.sandbox_write_paths
    reads = profile.sandbox_read_paths
    assert str((workspace / "intent" / "store" / "store.ic").resolve()) in reads
    assert str((workspace / "intent" / "models" / "models.ic").resolve()) in reads
    assert str((workspace / "intent" / "project.ic").resolve()) in reads
    assert str((workspace / "intent" / "implementations").resolve()) in reads
    assert not any(p.endswith("api.ic") for p in reads)


def test_idempotent_and_force(workspace: Path) -> None:
    h = Harness(workspace)
    h.build()
    results, error = h.build()
    assert results == [] and error is None and len(h.agent.build_calls) == 3
    assert any("Nothing to build" in l for l in h.logs)
    results, _ = h.build(force=True)
    assert len(results) == 3 and len(h.agent.build_calls) == 6


def test_dry_run_has_no_side_effects(workspace: Path) -> None:
    h = Harness(workspace)
    results, error = h.build(dry_run=True)
    assert error is None
    assert [(r.target, r.status) for r in results] == [("models", TargetStatus.PENDING), ("store", TargetStatus.PENDING), ("api", TargetStatus.PENDING)]
    assert h.agent.build_calls == [] and h.state.list_targets() == []
    assert h.state.backend._conn.execute("SELECT COUNT(*) FROM generations").fetchone()[0] == 0


def test_targeted_build_includes_ancestors_only(workspace: Path) -> None:
    h = Harness(workspace)
    results, _ = h.build(target="store")
    assert [r.target for r in results] == ["models", "store"]
    assert h.state.get_status("api") is TargetStatus.PENDING
    assert h.builder.next_targets() == ["api"]


def test_unknown_target_raises_key_error(workspace: Path) -> None:
    with pytest.raises(KeyError, match="Feature 'nope' not found"):
        Harness(workspace).build(target="nope")


def test_agent_error_retries_then_fails_and_stops_dag(workspace: Path) -> None:
    calls = {"n": 0}

    def failing(ctx: BuildContext) -> BuildResponse:
        if ctx.feature_path == "store":
            calls["n"] += 1
            raise AgentError(f"crash {calls['n']}")
        return BuildResponse(status="success", summary="ok")

    h = Harness(workspace, MockAgent(build_side_effect=failing))
    results, error = h.build()
    assert isinstance(error, RuntimeError) and "Build failed for target 'store'" in str(error) and "crash 3" in str(error)
    assert [r.target for r in results] == ["models", "store"]
    assert results[1].status is TargetStatus.FAILED and results[1].attempts == 3
    assert calls["n"] == 3
    store_calls = [c for c in h.agent.build_calls if c.feature_path == "store"]
    assert store_calls[0].previous_errors == []
    assert store_calls[2].previous_errors == ["Agent error: crash 1", "Agent error: crash 2"]
    assert h.state.get_status("store") is TargetStatus.FAILED
    assert h.state.get_status("api") is TargetStatus.PENDING
    assert h.state.backend.get_generation(results[0].generation_id)["status"] == "failed"
    assert len(h.vc.checkpoints) == 1  # only models was checkpointed


def test_agent_reported_failure_is_retried(workspace: Path) -> None:
    attempts = {"n": 0}

    def flaky(ctx: BuildContext) -> BuildResponse:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return BuildResponse(status="failure", summary="could not write file")
        return BuildResponse(status="success", summary="second time lucky")

    h = Harness(workspace, MockAgent(build_side_effect=flaky))
    results, error = h.build(target="models")
    assert error is None and results[0].status is TargetStatus.BUILT and results[0].attempts == 2
    assert h.agent.build_calls[1].previous_errors == ["could not write file"]


def test_validation_failure_retries_from_build_then_fails(workspace: Path) -> None:
    agent = MockAgent(validate_side_effect=lambda ctx, v: ValidationResponse(name=v.name, status="fail", reason="tasks are not persisted"))
    h = Harness(workspace, agent, profile=PROFILE.model_copy(update={"retries": 2}))
    results, error = h.build()
    assert error is not None and "tasks are not persisted" in str(error)
    store = results[1]
    assert store.status is TargetStatus.FAILED and store.attempts == 2
    assert [s.phase for s in store.steps] == ["resolve_deps", "build", "validate"]
    assert store.steps[-1].status == "failure"
    store_builds = [c for c in agent.build_calls if c.feature_path == "store"]
    assert len(store_builds) == 2 and "store-ok: tasks are not persisted" in store_builds[1].previous_errors[0]
    assert len(agent.validate_calls) == 2
    assert not any("store" in c for c in h.vc.checkpoints)
    vals = h.state.backend.get_validation_results("store")  # linked through the generation
    assert len(vals) == 2 and all(v["status"] == "fail" for v in vals)


def test_checkpoint_failure_marks_target_failed(workspace: Path) -> None:
    h = Harness(workspace)
    h.vc.fail = True
    results, error = h.build(target="models")
    assert error is not None and "git broke" in str(error)
    assert results[0].status is TargetStatus.FAILED and results[0].steps[-1].phase == "checkpoint"


def test_profile_override(workspace: Path) -> None:
    h = Harness(workspace)
    h.build(target="models", profile_override="fast")
    gen = h.state.backend.get_generation(h.state.get_build_result("models").generation_id)
    assert gen["profile_name"] == "fast"


def test_implementation_selection_and_unknown(workspace: Path) -> None:
    h = Harness(workspace)
    with pytest.raises(KeyError):
        h.build(target="models", implementation="rust")
    h.build(target="models", implementation="default")
    assert h.agent.build_calls[0].implementation.name == "default"


# ---------------------------------------------------------------------------
# Staleness
# ---------------------------------------------------------------------------


def test_edited_intent_is_detected_and_rebuilt(workspace: Path) -> None:
    h = Harness(workspace)
    h.build()
    assert h.builder.detect_outdated() == []
    (workspace / "intent" / "store" / "store.ic").write_text("---\nname: store\ndepends_on: [models]\n---\n\nStore v2.\n")
    h.reload()
    assert h.builder.detect_outdated() == ["store"]
    changed = h.builder.refresh_outdated()
    assert changed == ["store", "api"]
    assert h.state.get_status("store") is TargetStatus.OUTDATED and h.state.get_status("api") is TargetStatus.OUTDATED
    assert h.state.get_status("models") is TargetStatus.BUILT
    results, _ = h.build()
    assert [r.target for r in results] == ["store", "api"]


def test_build_auto_refreshes_stale_targets(workspace: Path) -> None:
    h = Harness(workspace)
    h.build()
    (workspace / "intent" / "models" / "models.ic").write_text("---\nname: models\n---\n\nModels v2.\n")
    h.reload()
    results, _ = h.build()
    assert [r.target for r in results] == ["models", "store", "api"]
    assert any("Marked 'models' outdated" in l for l in h.logs)


def test_mtime_fallback_when_no_hash(workspace: Path) -> None:
    import os
    import time

    h = Harness(workspace)
    h.build(target="models")
    result = h.state.get_build_result("models")
    h.state.backend._conn.execute("UPDATE build_results SET source_hash = ''")
    h.state.backend._conn.commit()
    assert h.builder.detect_outdated() == []
    future = time.time() + 3600
    os.utime(workspace / "intent" / "models" / "models.ic", (future, future))
    assert h.builder.detect_outdated() == ["models"]
    assert result.timestamp


# ---------------------------------------------------------------------------
# Clean / validate
# ---------------------------------------------------------------------------


def test_clean_reverts_resets_and_marks_dependents(workspace: Path) -> None:
    h = Harness(workspace)
    h.build()
    commit = h.state.get_build_result("models").commit_id
    h.builder.clean("models", "src")
    assert h.vc.restored == [f"{commit}~1"]
    assert h.state.get_status("models") is TargetStatus.PENDING
    assert h.state.get_status("store") is TargetStatus.OUTDATED and h.state.get_status("api") is TargetStatus.OUTDATED
    assert len(h.vc.checkpoints) == 3  # no new checkpoint created by clean
    h.builder.clean("models", "src")  # nothing to clean — no error
    h.builder.clean_all("src")
    assert h.state.list_targets() == []
    with pytest.raises(KeyError):
        h.builder.clean("nope", "src")


def test_validate_delegates_without_state_changes(workspace: Path) -> None:
    h = Harness(workspace)
    Builder.validate  # noqa: B018
    result = h.builder.validate("store", "src")
    assert result.target == "store" and result.passed
    results = h.builder.validate(None, "src")
    assert [r.target for r in results] == ["models", "store", "api"]
    assert h.state.list_targets() == []
