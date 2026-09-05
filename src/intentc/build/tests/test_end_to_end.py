"""End-to-end build pipeline test: real project on disk, real git, real SQLite, mocked agent."""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest

from intentc.build.agents import AgentError, AgentProfile, BuildContext, BuildResponse, MockAgent
from intentc.build.builder.builder import Builder, BuildOptions
from intentc.build.state import StateManager, TargetStatus, VersionControl
from intentc.core import load_project


class MockVersionControl(VersionControl):
    """In-memory VersionControl so the test never depends on git commit behaviour."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def checkpoint(self, message: str) -> str:
        self.messages.append(message)
        return uuid.uuid4().hex

    def diff(self, from_id: str, to_id: str) -> str:
        return ""

    def restore(self, commit_id: str) -> None:
        return None

    def log(self, target: str | None = None) -> list[str]:
        return []

    def has_changes(self) -> bool:
        return False


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "commit", "--allow-empty", "-m", "init", "-q")

    intent = tmp_path / "intent"
    _write(intent / "project.ic", "---\nname: e2e\n---\n\n# E2E project\n")
    _write(intent / "implementation.ic", "---\nname: implementation\n---\n\nPython 3.11, output to src/.\n")
    _write(intent / "models" / "models.ic", "---\nname: models\n---\n\nTask model.\n")
    _write(intent / "store" / "store.ic", "---\nname: store\ndepends_on: [models]\n---\n\nJSON store.\n")
    _write(intent / "api" / "api.ic", "---\nname: api\ndepends_on: [store]\n---\n\nHTTP API.\n")
    _write(
        intent / "store" / "validations.icv",
        "target: store\nvalidations:\n  - name: store-works\n    type: agent_validation\n    args:\n      rubric: the store round-trips tasks through the JSON file on disk\n",
    )
    _write(tmp_path / ".intentc" / "config.yaml", "default_profile:\n  name: default\n  provider: cli\n  command: echo\ndefault_output_dir: src\n")
    return tmp_path


def _make_builder(workspace: Path, agent: MockAgent, retries: int = 3) -> tuple[Builder, StateManager]:
    project = load_project(workspace / "intent")
    state = StateManager(workspace, "src")
    builder = Builder(project, state, MockVersionControl(), AgentProfile(name="default", provider="cli", command="echo", retries=retries))
    builder._create_agent = lambda p: agent
    return builder, state


OPTS = BuildOptions(output_dir="src")


class TestEndToEnd:
    def test_full_build_pipeline(self, workspace: Path) -> None:
        agent = MockAgent()
        builder, state = _make_builder(workspace, agent)
        results, error = builder.build(OPTS)
        assert error is None
        assert [r.target for r in results] == ["models", "store", "api"]
        assert all(r.status is TargetStatus.BUILT for r in results)
        for r in results:
            phases = [s.phase for s in r.steps]
            assert "resolve_deps" in phases and "build" in phases and "checkpoint" in phases
        assert "validate" in [s.phase for s in results[1].steps]
        assert "validate" not in [s.phase for s in results[0].steps]
        gen_ids = {r.generation_id for r in results}
        assert len(gen_ids) == 1 and uuid.UUID(next(iter(gen_ids)))
        assert len(agent.build_calls) == 3
        assert all(state.get_status(t) is TargetStatus.BUILT for t in ["models", "store", "api"])
        assert (workspace / "src").is_dir()

    def test_idempotent_rebuild(self, workspace: Path) -> None:
        agent = MockAgent()
        builder, state = _make_builder(workspace, agent)
        builder.build(OPTS)
        results, error = builder.build(OPTS)
        assert results == [] and error is None
        assert len(agent.build_calls) == 3
        assert all(state.get_status(t) is TargetStatus.BUILT for t in ["models", "store", "api"])

    def test_force_rebuild(self, workspace: Path) -> None:
        agent = MockAgent()
        builder, _ = _make_builder(workspace, agent)
        builder.build(OPTS)
        results, error = builder.build(BuildOptions(output_dir="src", force=True))
        assert error is None and len(results) == 3
        assert len(agent.build_calls) == 6
        assert all(r.status is TargetStatus.BUILT for r in results)

    def test_targeted_build_with_ancestors(self, workspace: Path) -> None:
        agent = MockAgent()
        builder, _ = _make_builder(workspace, agent)
        results, error = builder.build(BuildOptions(output_dir="src", target="api"))
        assert error is None
        assert [r.target for r in results] == ["models", "store", "api"]

    def test_partial_build_then_continue(self, workspace: Path) -> None:
        agent = MockAgent()
        builder, _ = _make_builder(workspace, agent)
        first, _ = builder.build(BuildOptions(output_dir="src", target="models"))
        assert [r.target for r in first] == ["models"]
        second, _ = builder.build(OPTS)
        assert [r.target for r in second] == ["store", "api"]
        assert [c.feature_path for c in agent.build_calls] == ["models", "store", "api"]

    def test_build_failure_stops_dag(self, workspace: Path) -> None:
        def crash_on_store(ctx: BuildContext) -> BuildResponse:
            if ctx.feature_path == "store":
                raise AgentError("store agent crashed")
            return BuildResponse(status="success", summary="ok")

        agent = MockAgent(build_side_effect=crash_on_store)
        builder, state = _make_builder(workspace, agent)
        results, error = builder.build(OPTS)
        assert error is not None and "store" in str(error)
        assert [r.target for r in results] == ["models", "store"]
        assert results[0].status is TargetStatus.BUILT and results[1].status is TargetStatus.FAILED
        assert state.get_status("store") is TargetStatus.FAILED
        assert state.get_status("api") is TargetStatus.PENDING

    def test_edited_intent_triggers_rebuild(self, workspace: Path) -> None:
        agent = MockAgent()
        builder, state = _make_builder(workspace, agent)
        builder.build(OPTS)
        assert state.get_status("store") is TargetStatus.BUILT
        store_ic = workspace / "intent" / "store" / "store.ic"
        store_ic.write_text(store_ic.read_text() + "\nNow also supports deletion.\n")
        builder, state = _make_builder(workspace, agent)
        assert builder.refresh_outdated() == ["store", "api"]
        assert state.get_status("store") is TargetStatus.OUTDATED
        results, error = builder.build(OPTS)
        assert error is None
        assert [r.target for r in results] == ["store", "api"]
        assert len(agent.build_calls) == 5

    def test_deterministic_validation_failure_retries_then_fails(self, workspace: Path) -> None:
        _write(
            workspace / "intent" / "store" / "validations.icv",
            "target: store\nvalidations:\n  - name: must-fail\n    type: command_validation\n    args:\n      command: exit 1\n",
        )
        agent = MockAgent()
        builder, state = _make_builder(workspace, agent, retries=2)
        results, error = builder.build(OPTS)
        assert error is not None
        store = results[1]
        assert store.status is TargetStatus.FAILED and store.attempts == 2
        store_calls = [c for c in agent.build_calls if c.feature_path == "store"]
        assert len(store_calls) == 2
        assert store_calls[1].previous_errors and "exited 1" in store_calls[1].previous_errors[0]
        assert not any(c.feature_path == "api" for c in agent.build_calls)
        assert state.get_status("api") is TargetStatus.PENDING
