"""End-to-end integration tests for the intentc build pipeline.

Exercises the full build pipeline in an isolated temporary directory with a
clean git repository. The agent is mocked — this test validates the
orchestration, not the agent output.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import pytest

from intentc.build.agents import (
    AgentError,
    AgentProfile,
    BuildContext,
    BuildResponse,
    MockAgent,
)
from intentc.build.builder.builder import Builder, BuildOptions
from intentc.build.state import (
    BuildResult,
    StateManager,
    TargetStatus,
    VersionControl,
)
from intentc.build.storage import SQLiteBackend
from intentc.core.project import load_project


# ---------------------------------------------------------------------------
# Mock VersionControl
# ---------------------------------------------------------------------------


class MockVersionControl(VersionControl):
    """In-memory version control for tests."""

    def __init__(self) -> None:
        self.checkpoints: list[tuple[str, str]] = []
        self.restores: list[str] = []
        self._counter = 0

    def checkpoint(self, message: str) -> str:
        self._counter += 1
        cid = f"commit-{self._counter:04d}"
        self.checkpoints.append((cid, message))
        return cid

    def diff(self, from_id: str, to_id: str) -> str:
        return f"diff {from_id}..{to_id}"

    def restore(self, commit_id: str) -> None:
        self.restores.append(commit_id)

    def log(self, target: str | None = None) -> list[str]:
        return [cid for cid, _ in self.checkpoints]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_project(tmp_path: Path) -> Path:
    """Create an intentc project on disk and return the tmp_path.

    Creates:
      intent/project.ic
      intent/implementations/python.ic
      intent/models/models.ic          (no deps)
      intent/store/store.ic            (depends_on: [models])
      intent/store/store.icv           (agent_validation)
      intent/api/api.ic                (depends_on: [store])
      .intentc/config.yaml
    """
    intent_dir = tmp_path / "intent"
    intent_dir.mkdir()

    # project.ic
    (intent_dir / "project.ic").write_text(
        "---\nname: test-e2e-project\n---\nAn end-to-end test project.\n",
        encoding="utf-8",
    )

    # implementations/
    impl_dir = intent_dir / "implementations"
    impl_dir.mkdir()
    (impl_dir / "python.ic").write_text(
        "---\nname: python\n---\nPython 3.11, output to src/\n",
        encoding="utf-8",
    )

    # models feature (no deps)
    models_dir = intent_dir / "models"
    models_dir.mkdir()
    (models_dir / "models.ic").write_text(
        "---\nname: models\n---\nCore data models.\n",
        encoding="utf-8",
    )

    # store feature (depends on models)
    store_dir = intent_dir / "store"
    store_dir.mkdir()
    (store_dir / "store.ic").write_text(
        "---\nname: store\ndepends_on:\n  - models\n---\nPersistence layer.\n",
        encoding="utf-8",
    )

    # store validation file
    (store_dir / "store.icv").write_text(
        "---\ntarget: store\nvalidations:\n"
        "  - name: store-schema-check\n"
        "    type: agent_validation\n"
        "    severity: error\n"
        "    args:\n"
        "      rubric: Verify the store schema is valid\n"
        "---\n",
        encoding="utf-8",
    )

    # api feature (depends on store)
    api_dir = intent_dir / "api"
    api_dir.mkdir()
    (api_dir / "api.ic").write_text(
        "---\nname: api\ndepends_on:\n  - store\n---\nREST API layer.\n",
        encoding="utf-8",
    )

    # .intentc/config.yaml
    config_dir = tmp_path / ".intentc"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "default_profile: cli\nprofiles:\n  cli:\n    provider: cli\n",
        encoding="utf-8",
    )

    return tmp_path


def _init_git(tmp_path: Path) -> None:
    """Initialize a git repo with dummy user and an initial empty commit."""
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.name", "test"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "initial"],
        cwd=tmp_path, capture_output=True, check=True,
    )


def _wire_builder(
    tmp_path: Path,
    mock_agent: MockAgent | None = None,
) -> tuple[Builder, StateManager, MockVersionControl, MockAgent]:
    """Load the on-disk project and wire up a Builder with mock deps."""
    project = load_project(tmp_path / "intent")

    output_dir = str(tmp_path / "src")
    os.makedirs(output_dir, exist_ok=True)

    backend = SQLiteBackend(tmp_path, "src")
    # Disable FK constraints so that ValidationSuite's internally-generated
    # generation_id (val-xxx) doesn't fail the generations FK.
    backend._conn.execute("PRAGMA foreign_keys=OFF")
    state_manager = StateManager(tmp_path, "src", backend=backend)
    vc = MockVersionControl()
    profile = AgentProfile(name="test-agent", provider="cli")

    builder = Builder(
        project=project,
        state_manager=state_manager,
        version_control=vc,
        agent_profile=profile,
    )

    agent = mock_agent or MockAgent()
    builder._create_agent = lambda _p: agent

    return builder, state_manager, vc, agent


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEndToEnd:
    """Integration tests exercising the full build pipeline."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        _init_git(tmp_path)
        _setup_project(tmp_path)

        # Patch create_from_profile in the validations module so that the
        # ValidationSuite (created internally by the Builder) gets a MockAgent
        # instead of a real CLIAgent.
        self._val_patcher = patch(
            "intentc.build.validations.create_from_profile",
            side_effect=lambda _profile: MockAgent(),
        )
        self._val_patcher.start()

    @pytest.fixture(autouse=True)
    def teardown(self) -> None:
        yield
        self._val_patcher.stop()

    # --- test_full_build_pipeline ------------------------------------------

    def test_full_build_pipeline(self) -> None:
        builder, state_manager, vc, agent = _wire_builder(self.tmp_path)
        output_dir = str(self.tmp_path / "src")

        results, err = builder.build(BuildOptions(output_dir=output_dir))

        # No error
        assert err is None

        # 3 results, one per feature
        assert len(results) == 3

        # All built
        for r in results:
            assert r.status == TargetStatus.BUILT

        # Topological order: models before store before api
        targets = [r.target for r in results]
        assert targets.index("models") < targets.index("store")
        assert targets.index("store") < targets.index("api")

        # Each result has resolve_deps, build, checkpoint steps
        for r in results:
            phases = [s.phase for s in r.steps]
            assert "resolve_deps" in phases
            assert "build" in phases
            assert "checkpoint" in phases

        # The store feature (which has validations) also has a validate step
        store_result = next(r for r in results if r.target == "store")
        store_phases = [s.phase for s in store_result.steps]
        assert "validate" in store_phases

        # All share the same generation_id (valid UUID)
        gen_id = results[0].generation_id
        UUID(gen_id)  # raises if invalid
        for r in results:
            assert r.generation_id == gen_id

        # MockAgent received 3 build calls
        assert len(agent.build_calls) == 3

        # StateManager reports BUILT for all targets
        for t in ("models", "store", "api"):
            assert state_manager.get_status(t) == TargetStatus.BUILT

    # --- test_idempotent_rebuild -------------------------------------------

    def test_idempotent_rebuild(self) -> None:
        builder, state_manager, vc, agent = _wire_builder(self.tmp_path)
        output_dir = str(self.tmp_path / "src")

        # First build
        builder.build(BuildOptions(output_dir=output_dir))
        first_call_count = len(agent.build_calls)

        # Second build — nothing to do
        results, err = builder.build(BuildOptions(output_dir=output_dir))

        assert err is None
        assert len(results) == 0
        assert len(agent.build_calls) == first_call_count  # no additional calls

    # --- test_force_rebuild ------------------------------------------------

    def test_force_rebuild(self) -> None:
        builder, state_manager, vc, agent = _wire_builder(self.tmp_path)
        output_dir = str(self.tmp_path / "src")

        # Initial build
        builder.build(BuildOptions(output_dir=output_dir))
        agent.build_calls.clear()

        # Force rebuild
        results, err = builder.build(
            BuildOptions(output_dir=output_dir, force=True)
        )

        assert err is None
        assert len(results) == 3
        assert len(agent.build_calls) == 3
        for r in results:
            assert r.status == TargetStatus.BUILT

    # --- test_targeted_build_with_ancestors --------------------------------

    def test_targeted_build_with_ancestors(self) -> None:
        builder, state_manager, vc, agent = _wire_builder(self.tmp_path)
        output_dir = str(self.tmp_path / "src")

        results, err = builder.build(
            BuildOptions(target="api", output_dir=output_dir)
        )

        assert err is None
        # All 3 built (api depends on store depends on models)
        assert len(results) == 3
        targets = [r.target for r in results]
        assert targets.index("models") < targets.index("store")
        assert targets.index("store") < targets.index("api")

    # --- test_partial_build_then_continue ----------------------------------

    def test_partial_build_then_continue(self) -> None:
        builder, state_manager, vc, agent = _wire_builder(self.tmp_path)
        output_dir = str(self.tmp_path / "src")

        # Build only models
        results1, err1 = builder.build(
            BuildOptions(target="models", output_dir=output_dir)
        )
        assert err1 is None
        assert len(results1) == 1
        assert results1[0].target == "models"

        # Build all — models should be skipped
        results2, err2 = builder.build(BuildOptions(output_dir=output_dir))
        assert err2 is None
        assert len(results2) == 2
        built_targets = [r.target for r in results2]
        assert "models" not in built_targets
        assert "store" in built_targets
        assert "api" in built_targets

    # --- test_build_failure_stops_dag --------------------------------------

    def test_build_failure_stops_dag(self) -> None:
        failing_agent = MockAgent()
        call_count = 0
        original_build = failing_agent.build

        def fail_on_store(ctx: BuildContext) -> BuildResponse:
            nonlocal call_count
            call_count += 1
            # Fail when building store (second target)
            if ctx.intent.name == "store":
                raise AgentError("store build failed")
            return original_build(ctx)

        failing_agent.build = fail_on_store  # type: ignore[assignment]

        builder, state_manager, vc, _ = _wire_builder(
            self.tmp_path, mock_agent=failing_agent
        )
        output_dir = str(self.tmp_path / "src")

        results, err = builder.build(BuildOptions(output_dir=output_dir))

        # models succeeded
        assert results[0].target == "models"
        assert results[0].status == TargetStatus.BUILT

        # store failed
        assert results[1].target == "store"
        assert results[1].status == TargetStatus.FAILED

        # api was never attempted
        built_targets = [r.target for r in results]
        assert "api" not in built_targets

        # Error is returned
        assert err is not None

        # State reflects failure
        assert state_manager.get_status("store") == TargetStatus.FAILED
