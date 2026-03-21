"""End-to-end integration tests for the intentc build pipeline.

Exercises the full build pipeline in an isolated temporary directory
with a real project loaded from disk, a real StateManager, and a
MockAgent + MockVersionControl. No network calls, no real agents.
"""

from __future__ import annotations

import subprocess
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from intentc.build.agents import (
    AgentError,
    AgentProfile,
    BuildResponse,
    MockAgent,
)
from intentc.build.builder.builder import Builder, BuildOptions
from intentc.build.state import StateManager, TargetStatus, VersionControl
from intentc.core.project import load_project


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockVersionControl(VersionControl):
    """In-memory version control for testing."""

    def __init__(self) -> None:
        self.checkpoints: list[tuple[str, str]] = []
        self.restores: list[str] = []

    def checkpoint(self, message: str) -> str:
        cid = f"commit-{len(self.checkpoints)}"
        self.checkpoints.append((message, cid))
        return cid

    def diff(self, from_id: str, to_id: str) -> str:
        return f"diff {from_id}..{to_id}"

    def restore(self, commit_id: str) -> None:
        self.restores.append(commit_id)

    def log(self, target: str | None = None) -> list[str]:
        return [cid for _, cid in self.checkpoints]


def _init_git_repo(path: Path) -> None:
    """Initialize a git repo with a dummy user and initial commit."""
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=path, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "initial"],
        cwd=path, check=True, capture_output=True,
    )


def _create_project_on_disk(root: Path) -> None:
    """Write a minimal intentc project with a 3-feature dependency chain.

    models -> store -> api, where store has a validation.
    """
    intent = root / "intent"
    intent.mkdir()

    (intent / "project.ic").write_text(
        "---\nname: test-e2e\n---\n# E2E Test Project\n"
    )
    (intent / "implementation.ic").write_text(
        "---\nname: impl\n---\n# Implementation\nPython 3.11, output to src/\n"
    )

    # models — no deps
    models = intent / "models"
    models.mkdir()
    (models / "models.ic").write_text(
        "---\nname: models\nversion: 1\n---\n# Models\nData models.\n"
    )

    # store — depends on models, has a validation
    store = intent / "store"
    store.mkdir()
    (store / "store.ic").write_text(
        "---\nname: store\nversion: 1\ndepends_on: [models]\n---\n# Store\nPersistence layer.\n"
    )
    (store / "validations.icv").write_text(
        "target: store\nversion: 1\nvalidations:\n"
        "  - name: store-check\n"
        "    type: agent_validation\n"
        "    severity: error\n"
        "    args:\n"
        "      rubric: Check store module\n"
    )

    # api — depends on store
    api = intent / "api"
    api.mkdir()
    (api / "api.ic").write_text(
        "---\nname: api\nversion: 1\ndepends_on: [store]\n---\n# API\nREST endpoints.\n"
    )

    # config
    intentc_dir = root / ".intentc"
    intentc_dir.mkdir()
    (intentc_dir / "config.yaml").write_text(
        "default_output_dir: src\ndefault_profile:\n  name: cli\n  provider: cli\n"
    )


def _make_e2e_builder(
    tmp_path: Path,
    agent: MockAgent | None = None,
) -> tuple[Builder, MockAgent, MockVersionControl, StateManager]:
    """Load the on-disk project and wire up a Builder with mocks."""
    project = load_project(tmp_path / "intent")
    mock_agent = agent or MockAgent()
    mock_vc = MockVersionControl()
    profile = AgentProfile(name="mock", provider="cli")
    output_dir = tmp_path / "src"
    output_dir.mkdir(exist_ok=True)
    sm = StateManager(tmp_path, "src")

    builder = Builder(
        project=project,
        state_manager=sm,
        version_control=mock_vc,
        agent_profile=profile,
    )
    builder._create_agent = lambda p: mock_agent  # type: ignore[attr-defined]
    return builder, mock_agent, mock_vc, sm


# ---------------------------------------------------------------------------
# End-to-end tests
# ---------------------------------------------------------------------------


class TestEndToEnd:
    """Full pipeline integration tests using on-disk project + mocked agent."""

    @pytest.fixture(autouse=True)
    def setup_project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tmp_path = tmp_path
        _init_git_repo(tmp_path)
        _create_project_on_disk(tmp_path)
        # Patch create_from_profile in the validations module so that
        # ValidationSuite (created during the build's validate step) uses
        # a MockAgent instead of trying to spawn a real agent.
        self._val_mock_agent = MockAgent()
        import intentc.build.validations as val_mod
        monkeypatch.setattr(val_mod, "create_from_profile", lambda p: self._val_mock_agent)

    def test_full_build_pipeline(self) -> None:
        builder, agent, vc, sm = _make_e2e_builder(self.tmp_path)
        opts = BuildOptions(output_dir="src")

        results, err = builder.build(opts)

        assert err is None
        assert len(results) == 3
        assert all(r.status == TargetStatus.BUILT for r in results)

        # Topological order: models before store before api
        targets = [r.target for r in results]
        assert targets.index("models") < targets.index("store")
        assert targets.index("store") < targets.index("api")

        # Steps recorded for each result
        for r in results:
            phases = [s.phase for s in r.steps]
            assert "resolve_deps" in phases
            assert "build" in phases
            assert "checkpoint" in phases

        # store has validations so it should have a validate step
        store_result = [r for r in results if r.target == "store"][0]
        store_phases = [s.phase for s in store_result.steps]
        assert "validate" in store_phases

        # Shared generation ID (valid UUID)
        gen_ids = {r.generation_id for r in results}
        assert len(gen_ids) == 1
        uuid.UUID(gen_ids.pop())

        # Agent was called 3 times
        assert len(agent.build_calls) == 3

        # State persisted
        for t in ["models", "store", "api"]:
            assert sm.get_status(t) == TargetStatus.BUILT

    def test_idempotent_rebuild(self) -> None:
        builder, agent, vc, sm = _make_e2e_builder(self.tmp_path)
        opts = BuildOptions(output_dir="src")

        builder.build(opts)
        agent.build_calls.clear()

        results, err = builder.build(opts)

        assert err is None
        assert len(results) == 0
        assert len(agent.build_calls) == 0

    def test_force_rebuild(self) -> None:
        builder, agent, vc, sm = _make_e2e_builder(self.tmp_path)
        opts = BuildOptions(output_dir="src")

        builder.build(opts)
        agent.build_calls.clear()

        force_opts = BuildOptions(output_dir="src", force=True)
        results, err = builder.build(force_opts)

        assert err is None
        assert len(results) == 3
        assert all(r.status == TargetStatus.BUILT for r in results)
        assert len(agent.build_calls) == 3

    def test_targeted_build_with_ancestors(self) -> None:
        builder, agent, vc, sm = _make_e2e_builder(self.tmp_path)
        opts = BuildOptions(target="api", output_dir="src")

        results, err = builder.build(opts)

        assert err is None
        targets = [r.target for r in results]
        assert targets == ["models", "store", "api"]

    def test_partial_build_then_continue(self) -> None:
        builder, agent, vc, sm = _make_e2e_builder(self.tmp_path)

        # Build only models
        results1, err1 = builder.build(BuildOptions(target="models", output_dir="src"))
        assert err1 is None
        assert len(results1) == 1
        assert results1[0].target == "models"

        agent.build_calls.clear()

        # Build all — only store and api should be built
        results2, err2 = builder.build(BuildOptions(output_dir="src"))
        assert err2 is None
        built = [r.target for r in results2]
        assert "models" not in built
        assert "store" in built
        assert "api" in built
        assert len(results2) == 2

    def test_build_failure_stops_dag(self) -> None:
        call_count = 0

        def fail_on_store(ctx):
            nonlocal call_count
            call_count += 1
            if ctx.intent.name == "store":
                raise AgentError("store exploded")
            return BuildResponse(status="success", summary="ok")

        agent = MockAgent()
        agent.build = MagicMock(side_effect=fail_on_store)

        builder, _, vc, sm = _make_e2e_builder(self.tmp_path, agent=agent)
        opts = BuildOptions(output_dir="src")

        results, err = builder.build(opts)

        assert err is not None
        built_targets = [r.target for r in results]
        assert "models" in built_targets
        assert "store" in built_targets
        assert "api" not in built_targets

        models_result = [r for r in results if r.target == "models"][0]
        assert models_result.status == TargetStatus.BUILT

        store_result = [r for r in results if r.target == "store"][0]
        assert store_result.status == TargetStatus.FAILED

        assert sm.get_status("store") == TargetStatus.FAILED
