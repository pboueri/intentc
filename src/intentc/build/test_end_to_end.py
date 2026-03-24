"""End-to-end integration tests for the full intentc build pipeline.

Exercises the complete build pipeline in an isolated temporary directory with
a clean git repository. The agent is mocked — these tests validate the
orchestration, not the agent output.
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

from unittest.mock import patch

import pytest

from intentc.build.agents.mock_agent import MockAgent
from intentc.build.agents.models import AgentError, AgentProfile, BuildContext, BuildResponse
from intentc.build.builder.builder import Builder, BuildOptions
from intentc.build.state import StateManager, VersionControl
from intentc.build.storage.backend import TargetStatus
from intentc.core.project import load_project


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockVersionControl(VersionControl):
    """In-memory version control for end-to-end tests."""

    def __init__(self) -> None:
        self.commits: list[tuple[str, str]] = []
        self._counter = 0
        self.restores: list[str] = []

    def checkpoint(self, message: str) -> str:
        self._counter += 1
        commit_id = f"e2e{self._counter:04d}"
        self.commits.append((commit_id, message))
        return commit_id

    def diff(self, from_id: str, to_id: str) -> str:
        return f"diff {from_id}..{to_id}"

    def restore(self, commit_id: str) -> None:
        self.restores.append(commit_id)

    def log(self, target: str | None = None) -> list[str]:
        return [cid for cid, msg in self.commits if target is None or target in msg]


def _init_git_repo(tmp_dir: Path) -> None:
    """Initialize a git repo with a dummy user and an initial empty commit."""
    subprocess.run(["git", "init"], cwd=tmp_dir, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.name", "test"],
        cwd=tmp_dir, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_dir, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "initial"],
        cwd=tmp_dir, capture_output=True, check=True,
    )


def _create_project_on_disk(tmp_dir: Path) -> Path:
    """Create an intentc project on disk and return the intent directory path.

    Creates:
      - intent/project.ic
      - intent/models/models.ic  (no deps)
      - intent/store/store.ic    (depends_on: [models])
      - intent/api/api.ic        (depends_on: [store])
      - intent/api/api.icv       (agent_validation entry)
    """
    intent_dir = tmp_dir / "intent"

    # project.ic
    (intent_dir).mkdir(parents=True)
    (intent_dir / "project.ic").write_text(
        "---\nname: e2e-test-project\n---\nAn end-to-end test project.\n"
    )

    # models — no dependencies
    (intent_dir / "models").mkdir()
    (intent_dir / "models" / "models.ic").write_text(
        "---\nname: models\n---\nData models for the project.\n"
    )

    # store — depends on models
    (intent_dir / "store").mkdir()
    (intent_dir / "store" / "store.ic").write_text(
        "---\nname: store\ndepends_on:\n  - models\n---\nStorage layer.\n"
    )

    # api — depends on store
    (intent_dir / "api").mkdir()
    (intent_dir / "api" / "api.ic").write_text(
        "---\nname: api\ndepends_on:\n  - store\n---\nAPI layer.\n"
    )

    # api validation file
    (intent_dir / "api" / "api.icv").write_text(
        "target: api\nvalidations:\n"
        "  - name: api-check\n"
        "    type: agent_validation\n"
        "    severity: error\n"
        "    args:\n"
        "      rubric: Verify API endpoints work.\n"
    )

    return intent_dir


def _create_config(tmp_dir: Path) -> None:
    """Create .intentc/config.yaml with a default cli profile."""
    config_dir = tmp_dir / ".intentc"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.yaml").write_text(
        "profiles:\n"
        "  default:\n"
        "    provider: cli\n"
        "    command: echo ok\n"
    )


def _setup_e2e(tmp_dir: Path) -> tuple[Builder, MockAgent, MockVersionControl, StateManager]:
    """Full E2E setup: git init, create project on disk, load, wire builder.

    The caller MUST use the returned mock_agent within a
    ``patch("intentc.build.validations.create_from_profile", ...)``
    context so that the ValidationSuite also uses the mock agent.
    """
    _init_git_repo(tmp_dir)
    intent_dir = _create_project_on_disk(tmp_dir)
    _create_config(tmp_dir)

    project = load_project(intent_dir)

    state_manager = StateManager(base_dir=tmp_dir, output_dir="src")
    vc = MockVersionControl()
    mock_agent = MockAgent()
    profile = AgentProfile(name="test", provider="cli", command="echo ok", retries=1)

    builder = Builder(
        project=project,
        state_manager=state_manager,
        version_control=vc,
        agent_profile=profile,
        create_agent=lambda _p: mock_agent,
    )

    return builder, mock_agent, vc, state_manager


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


def _patch_validation_factory(mock_agent: MockAgent):
    """Return a context manager that patches create_from_profile in the validations module."""
    return patch(
        "intentc.build.validations.create_from_profile",
        return_value=mock_agent,
    )


class TestEndToEnd:
    """End-to-end tests for the intentc build pipeline."""

    def test_full_build_pipeline(self, tmp_path: Path) -> None:
        builder, mock_agent, vc, state_manager = _setup_e2e(tmp_path)
        output_dir = str(tmp_path / "src")

        with _patch_validation_factory(mock_agent):
            results, err = builder.build(BuildOptions(output_dir=output_dir))

        # No error
        assert err is None, f"Unexpected error: {err}"

        # 3 results, one per feature
        assert len(results) == 3

        # All built
        for r in results:
            assert r.status == "success"

        # Build order respects dependency chain
        targets = [r.target for r in results]
        assert targets.index("models") < targets.index("store")
        assert targets.index("store") < targets.index("api")

        # Each result has resolve_deps, build, and checkpoint steps
        for r in results:
            phases = [s.phase for s in r.steps]
            assert "resolve_deps" in phases
            assert "build" in phases
            assert "checkpoint" in phases

        # The feature with validations (api) also has a validate step
        api_result = next(r for r in results if r.target == "api")
        api_phases = [s.phase for s in api_result.steps]
        assert "validate" in api_phases

        # All results share the same generation_id (a valid UUID)
        gen_ids = {r.generation_id for r in results}
        assert len(gen_ids) == 1
        gen_id = gen_ids.pop()
        uuid.UUID(gen_id)  # raises if not valid UUID

        # MockAgent.calls has 3 build entries
        build_calls = [c for c in mock_agent.calls if c.method == "build"]
        assert len(build_calls) == 3

        # StateManager shows all targets as BUILT
        for target in ("models", "store", "api"):
            assert state_manager.get_status(target) == TargetStatus.BUILT

    def test_idempotent_rebuild(self, tmp_path: Path) -> None:
        builder, mock_agent, vc, state_manager = _setup_e2e(tmp_path)
        output_dir = str(tmp_path / "src")

        with _patch_validation_factory(mock_agent):
            # First build
            builder.build(BuildOptions(output_dir=output_dir))
            first_call_count = len(mock_agent.calls)

            # Second build — nothing to do
            results, err = builder.build(BuildOptions(output_dir=output_dir))

        assert err is None
        assert results == []
        assert len(mock_agent.calls) == first_call_count  # No additional calls

    def test_force_rebuild(self, tmp_path: Path) -> None:
        builder, mock_agent, vc, state_manager = _setup_e2e(tmp_path)
        output_dir = str(tmp_path / "src")

        with _patch_validation_factory(mock_agent):
            # Initial build
            builder.build(BuildOptions(output_dir=output_dir))
            calls_after_first = len([c for c in mock_agent.calls if c.method == "build"])

            # Force rebuild
            results, err = builder.build(BuildOptions(output_dir=output_dir, force=True))

        assert err is None
        assert len(results) == 3
        for r in results:
            assert r.status == "success"

        # Agent called 3 more times for build
        build_calls = [c for c in mock_agent.calls if c.method == "build"]
        assert len(build_calls) == calls_after_first + 3

    def test_targeted_build_with_ancestors(self, tmp_path: Path) -> None:
        builder, mock_agent, vc, state_manager = _setup_e2e(tmp_path)
        output_dir = str(tmp_path / "src")

        with _patch_validation_factory(mock_agent):
            results, err = builder.build(BuildOptions(target="api", output_dir=output_dir))

        assert err is None

        # All 3 targets built (api depends on store depends on models)
        targets = [r.target for r in results]
        assert len(targets) == 3
        assert "models" in targets
        assert "store" in targets
        assert "api" in targets

        # Build order is models -> store -> api
        assert targets.index("models") < targets.index("store")
        assert targets.index("store") < targets.index("api")

    def test_partial_build_then_continue(self, tmp_path: Path) -> None:
        builder, mock_agent, vc, state_manager = _setup_e2e(tmp_path)
        output_dir = str(tmp_path / "src")

        with _patch_validation_factory(mock_agent):
            # Build only models
            results1, err1 = builder.build(BuildOptions(target="models", output_dir=output_dir))
            assert err1 is None
            assert len(results1) == 1
            assert results1[0].target == "models"

            # Build all — models should be skipped
            results2, err2 = builder.build(BuildOptions(output_dir=output_dir))

        assert err2 is None
        assert len(results2) == 2

        targets2 = [r.target for r in results2]
        assert "models" not in targets2
        assert "store" in targets2
        assert "api" in targets2

    def test_build_failure_stops_dag(self, tmp_path: Path) -> None:
        builder, mock_agent, vc, state_manager = _setup_e2e(tmp_path)
        output_dir = str(tmp_path / "src")

        # Make agent fail when building store
        original_build = mock_agent.build

        def _failing_on_store(ctx: BuildContext) -> BuildResponse:
            if ctx.intent.name == "store":
                raise AgentError("store build failed")
            return original_build(ctx)

        mock_agent.build = _failing_on_store

        with _patch_validation_factory(mock_agent):
            results, err = builder.build(BuildOptions(output_dir=output_dir))

        # models builds successfully
        models_results = [r for r in results if r.target == "models"]
        assert len(models_results) == 1
        assert models_results[0].status == "success"

        # store fails
        store_results = [r for r in results if r.target == "store"]
        assert len(store_results) == 1
        assert store_results[0].status == "failed"

        # api is never attempted
        api_results = [r for r in results if r.target == "api"]
        assert len(api_results) == 0

        # Error returned
        assert err is not None

        # StateManager shows store as FAILED
        assert state_manager.get_status("store") == TargetStatus.FAILED
