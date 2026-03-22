from __future__ import annotations

import subprocess
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from intentc.build.agents.mock_agent import MockAgent
from intentc.build.agents.types import (
    AgentError,
    AgentProfile,
    BuildContext,
    BuildResponse,
)
from intentc.build.builder.builder import Builder, BuildOptions
from intentc.build.state import (
    BuildResult,
    StateManager,
    TargetStatus,
    VersionControl,
)
from intentc.build.storage.sqlite_backend import SQLiteBackend
from intentc.core.project import load_project


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockVersionControl(VersionControl):
    def __init__(self) -> None:
        self.checkpoints: list[str] = []
        self.restores: list[str] = []
        self._commit_counter = 0

    def checkpoint(self, message: str) -> str:
        self._commit_counter += 1
        commit_id = f"abc{self._commit_counter:04d}"
        self.checkpoints.append(message)
        return commit_id

    def diff(self, from_id: str, to_id: str) -> str:
        return f"diff {from_id}..{to_id}"

    def restore(self, commit_id: str) -> None:
        self.restores.append(commit_id)

    def log(self, target: str | None = None) -> list[str]:
        return []


def _init_git_repo(tmp_dir: Path) -> None:
    """Initialize a git repo with a dummy user and initial commit."""
    subprocess.run(["git", "init"], cwd=tmp_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmp_dir, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_dir, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "initial"],
        cwd=tmp_dir, check=True, capture_output=True,
    )


def _create_project_on_disk(tmp_dir: Path) -> Path:
    """Create a minimal intentc project on disk and return the intent dir."""
    intent_dir = tmp_dir / "intent"

    # project.ic
    (intent_dir).mkdir(parents=True, exist_ok=True)
    (intent_dir / "project.ic").write_text(
        "---\nname: test-e2e\n---\n\nEnd-to-end test project\n"
    )

    # models feature — no dependencies
    models_dir = intent_dir / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    (models_dir / "models.ic").write_text(
        "---\nname: models\n---\n\nData models\n"
    )

    # store feature — depends on models
    store_dir = intent_dir / "store"
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / "store.ic").write_text(
        "---\nname: store\ndepends_on:\n  - models\n---\n\nStorage layer\n"
    )

    # api feature — depends on store, has validation
    api_dir = intent_dir / "api"
    api_dir.mkdir(parents=True, exist_ok=True)
    (api_dir / "api.ic").write_text(
        "---\nname: api\ndepends_on:\n  - store\n---\n\nAPI layer\n"
    )
    (api_dir / "api.icv").write_text(
        "target: api\n"
        "validations:\n"
        "  - name: api_check\n"
        "    type: agent_validation\n"
        "    severity: error\n"
    )

    # config
    config_dir = tmp_dir / ".intentc"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.yaml").write_text(
        "profiles:\n"
        "  default:\n"
        "    provider: cli\n"
        "    command: echo\n"
    )

    return intent_dir


def _setup_e2e(
    tmp_path: Path,
    mock_agent: MockAgent | None = None,
) -> tuple[Builder, MockAgent, MockVersionControl, StateManager]:
    """Full e2e setup: git init, create project, load, wire builder."""
    _init_git_repo(tmp_path)
    intent_dir = _create_project_on_disk(tmp_path)
    project = load_project(intent_dir)

    agent = mock_agent or MockAgent()
    vc = MockVersionControl()
    profile = AgentProfile(name="test", provider="cli", command="echo", retries=3)
    backend = SQLiteBackend(tmp_path, "out")
    sm = StateManager(tmp_path, "out", backend=backend)

    builder = Builder(
        project=project,
        state_manager=sm,
        version_control=vc,
        agent_profile=profile,
        create_agent=lambda _profile: agent,
    )

    return builder, agent, vc, sm


# ---------------------------------------------------------------------------
# End-to-end tests
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_full_build_pipeline(self, tmp_path: Path) -> None:
        """Build all targets and verify orchestration end-to-end."""
        agent = MockAgent()
        builder, agent, vc, sm = _setup_e2e(tmp_path, mock_agent=agent)
        output_dir = str(tmp_path / "output")

        # Patch create_from_profile in validations so the ValidationSuite
        # also uses our mock agent instead of a real CLIAgent.
        with patch(
            "intentc.build.validations.create_from_profile",
            return_value=agent,
        ):
            results, error = builder.build(BuildOptions(output_dir=output_dir))

        # No error
        assert error is None

        # 3 results, one per feature
        assert len(results) == 3

        # All built
        for r in results:
            assert r.status == TargetStatus.BUILT

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

        # The api feature (with validations) has a validate step
        api_result = next(r for r in results if r.target == "api")
        api_phases = [s.phase for s in api_result.steps]
        assert "validate" in api_phases

        # All results share the same generation_id (a valid UUID)
        gen_id = results[0].generation_id
        uuid.UUID(gen_id)  # raises if not a valid UUID
        for r in results:
            assert r.generation_id == gen_id

        # MockAgent.build_calls has 3 entries
        assert len(agent.build_calls) == 3

        # StateManager reports BUILT for all
        for t in ["models", "store", "api"]:
            assert sm.get_status(t) == TargetStatus.BUILT

    def test_idempotent_rebuild(self, tmp_path: Path) -> None:
        """After a full build, rebuilding produces no results."""
        agent = MockAgent()
        builder, agent, vc, sm = _setup_e2e(tmp_path, mock_agent=agent)
        output_dir = str(tmp_path / "output")
        opts = BuildOptions(output_dir=output_dir)

        with patch(
            "intentc.build.validations.create_from_profile",
            return_value=agent,
        ):
            builder.build(opts)
            calls_after_first = len(agent.build_calls)

            results, error = builder.build(opts)

        assert error is None
        assert len(results) == 0
        assert len(agent.build_calls) == calls_after_first

    def test_force_rebuild(self, tmp_path: Path) -> None:
        """Force rebuild re-builds all targets."""
        agent = MockAgent()
        builder, agent, vc, sm = _setup_e2e(tmp_path, mock_agent=agent)
        output_dir = str(tmp_path / "output")

        with patch(
            "intentc.build.validations.create_from_profile",
            return_value=agent,
        ):
            builder.build(BuildOptions(output_dir=output_dir))
            calls_after_first = len(agent.build_calls)

            results, error = builder.build(
                BuildOptions(output_dir=output_dir, force=True)
            )

        assert error is None
        assert len(results) == 3
        assert len(agent.build_calls) == calls_after_first + 3
        for r in results:
            assert r.status == TargetStatus.BUILT

    def test_targeted_build_with_ancestors(self, tmp_path: Path) -> None:
        """Building a leaf target pulls in all ancestors."""
        agent = MockAgent()
        builder, agent, vc, sm = _setup_e2e(tmp_path, mock_agent=agent)
        output_dir = str(tmp_path / "output")

        with patch(
            "intentc.build.validations.create_from_profile",
            return_value=agent,
        ):
            results, error = builder.build(
                BuildOptions(target="api", output_dir=output_dir)
            )

        assert error is None
        assert len(results) == 3
        targets = [r.target for r in results]
        assert targets == ["models", "store", "api"]

    def test_partial_build_then_continue(self, tmp_path: Path) -> None:
        """Build one target, then build all — only remaining targets are built."""
        agent = MockAgent()
        builder, agent, vc, sm = _setup_e2e(tmp_path, mock_agent=agent)
        output_dir = str(tmp_path / "output")

        with patch(
            "intentc.build.validations.create_from_profile",
            return_value=agent,
        ):
            # Build only models
            results1, error1 = builder.build(
                BuildOptions(target="models", output_dir=output_dir)
            )
            assert error1 is None
            assert len(results1) == 1
            assert results1[0].target == "models"

            # Build all — models should NOT be rebuilt
            results2, error2 = builder.build(
                BuildOptions(output_dir=output_dir)
            )

        assert error2 is None
        assert len(results2) == 2
        targets = [r.target for r in results2]
        assert "models" not in targets
        assert "store" in targets
        assert "api" in targets

    def test_build_failure_stops_dag(self, tmp_path: Path) -> None:
        """When an agent fails on a mid-DAG target, downstream targets are skipped."""
        call_count = 0

        class FailOnStoreAgent:
            def __init__(self) -> None:
                self.build_calls: list[BuildContext] = []
                self.validate_calls: list = []
                self.plan_calls: list = []

            def get_name(self) -> str:
                return "fail-on-store"

            def get_type(self) -> str:
                return "mock"

            def build(self, ctx: BuildContext) -> BuildResponse:
                nonlocal call_count
                call_count += 1
                self.build_calls.append(ctx)
                if ctx.intent.name == "store":
                    raise AgentError("store build failed")
                return BuildResponse(status="success", summary="ok")

            def validate(self, ctx, validation):
                pass

        _init_git_repo(tmp_path)
        intent_dir = _create_project_on_disk(tmp_path)
        project = load_project(intent_dir)

        failing_agent = FailOnStoreAgent()
        vc = MockVersionControl()
        profile = AgentProfile(
            name="test", provider="cli", command="echo", retries=1
        )
        backend = SQLiteBackend(tmp_path, "out")
        sm = StateManager(tmp_path, "out", backend=backend)

        builder = Builder(
            project=project,
            state_manager=sm,
            version_control=vc,
            agent_profile=profile,
            create_agent=lambda _: failing_agent,
        )

        output_dir = str(tmp_path / "output")
        results, error = builder.build(BuildOptions(output_dir=output_dir))

        # models builds successfully
        models_result = next(
            (r for r in results if r.target == "models"), None
        )
        assert models_result is not None
        assert models_result.status == TargetStatus.BUILT

        # store fails
        store_result = next(
            (r for r in results if r.target == "store"), None
        )
        assert store_result is not None
        assert store_result.status == TargetStatus.FAILED

        # api is never attempted
        api_result = next((r for r in results if r.target == "api"), None)
        assert api_result is None

        # Error is returned
        assert error is not None

        # StateManager reports store as FAILED
        assert sm.get_status("store") == TargetStatus.FAILED
