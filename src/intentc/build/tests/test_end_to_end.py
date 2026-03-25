"""End-to-end integration tests for the intentc build pipeline.

Exercises the full build pipeline in an isolated temporary directory with a
clean git repository. The agent is mocked — this validates orchestration,
not agent output.
"""

from __future__ import annotations

import subprocess
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from intentc.build.agents import (
    AgentError,
    AgentProfile,
    BuildContext,
    BuildResponse,
    MockAgent,
)
from intentc.build.builder.builder import Builder, BuildOptions
from intentc.build.state.state import StateManager, VersionControl
from intentc.build.storage.backend import (
    BuildResult,
    BuildStep,
    GenerationStatus,
    StorageBackend,
    TargetStatus,
)
from intentc.core.project import load_project


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockVersionControl(VersionControl):
    """In-memory version control for end-to-end tests."""

    def __init__(self) -> None:
        self.checkpoints: list[tuple[str, str]] = []
        self.restores: list[str] = []
        self._counter = 0

    def checkpoint(self, message: str) -> str:
        self._counter += 1
        commit_id = f"mock-commit-{self._counter:04d}"
        self.checkpoints.append((message, commit_id))
        return commit_id

    def diff(self, from_id: str, to_id: str) -> str:
        return f"diff {from_id}..{to_id}"

    def restore(self, commit_id: str) -> None:
        self.restores.append(commit_id)

    def log(self, target: str | None = None) -> list[str]:
        return [cid for _, cid in self.checkpoints]


class FakeStorageBackend(StorageBackend):
    """In-memory storage backend that avoids SQLite FK constraints."""

    def __init__(self, base_dir: Path, output_dir: str) -> None:
        super().__init__(base_dir, output_dir)
        self._statuses: dict[str, TargetStatus] = {}
        self._results: dict[str, BuildResult] = {}
        self._generations: dict[str, dict] = {}

    def create_generation(self, generation_id, output_dir, profile_name=None, options=None):
        self._generations[generation_id] = {
            "status": GenerationStatus.RUNNING.value,
        }

    def complete_generation(self, generation_id, status):
        if generation_id in self._generations:
            self._generations[generation_id]["status"] = status.value

    def log_generation_event(self, generation_id, message):
        pass

    def get_generation(self, generation_id):
        return self._generations.get(generation_id)

    def record_intent_version(self, name, source_path, content_hash):
        return 1

    def record_validation_version(self, target, source_path, content_hash):
        return 1

    def save_build_result(self, target, result, intent_version_id=None,
                          git_diff=None, files_created=None, files_modified=None):
        self._results[target] = result
        self._statuses[target] = (
            TargetStatus(result.status)
            if result.status in TargetStatus._value2member_map_
            else TargetStatus.PENDING
        )
        return 1

    def get_build_result(self, target):
        return self._results.get(target)

    def get_build_history(self, target, limit=50):
        r = self._results.get(target)
        return [r] if r else []

    def save_build_step(self, build_result_id, step, log, step_order):
        pass

    def save_validation_result(self, build_result_id, generation_id, target,
                                validation_file_version_id, name, type, severity,
                                status, reason="", duration_secs=None):
        return 1

    def save_agent_response(self, build_result_id, validation_result_id,
                            response_type, response_json):
        pass

    def get_status(self, target):
        return self._statuses.get(target, TargetStatus.PENDING)

    def set_status(self, target, status):
        self._statuses[target] = status

    def list_targets(self):
        return list(self._statuses.items())

    def reset(self, target):
        self._statuses[target] = TargetStatus.PENDING
        self._results.pop(target, None)

    def reset_all(self):
        self._statuses.clear()
        self._results.clear()


def _init_git(tmp_dir: Path) -> None:
    """Initialize a git repo with a dummy user and initial commit."""
    subprocess.run(["git", "init"], cwd=tmp_dir, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Test"],
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


def _create_project_files(tmp_dir: Path) -> None:
    """Create minimal intentc project files on disk."""
    intent_dir = tmp_dir / "intent"
    intent_dir.mkdir(parents=True)

    # project.ic
    (intent_dir / "project.ic").write_text(
        "---\nname: e2e-test\n---\nA test project for e2e.\n"
    )

    # implementation
    impl_dir = intent_dir / "implementations"
    impl_dir.mkdir()
    (impl_dir / "default.ic").write_text(
        "---\nname: default\n---\nPython 3.11, output to src/\n"
    )

    # Feature: models (no deps)
    models_dir = intent_dir / "models"
    models_dir.mkdir()
    (models_dir / "models.ic").write_text(
        "---\nname: models\ndepends_on: []\n---\nData models.\n"
    )

    # Feature: store (depends on models)
    store_dir = intent_dir / "store"
    store_dir.mkdir()
    (store_dir / "store.ic").write_text(
        "---\nname: store\ndepends_on: [models]\n---\nStorage layer.\n"
    )

    # Feature: api (depends on store) — with validation
    api_dir = intent_dir / "api"
    api_dir.mkdir()
    (api_dir / "api.ic").write_text(
        "---\nname: api\ndepends_on: [store]\n---\nAPI layer.\n"
    )
    (api_dir / "validations.icv").write_text(
        "target: api\nvalidations:\n"
        "  - name: api-check\n"
        "    type: agent_validation\n"
        "    severity: error\n"
        "    args:\n"
        "      rubric: Check api\n"
    )

    # Config
    config_dir = tmp_dir / ".intentc"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "default_profile: cli\n"
    )


def _setup(
    tmp_dir: Path,
    mock_agent: MockAgent | None = None,
) -> tuple[Builder, MockAgent, StateManager, MockVersionControl, FakeStorageBackend]:
    """Full setup: git, project files, load, wire builder."""
    _init_git(tmp_dir)
    _create_project_files(tmp_dir)

    project = load_project(tmp_dir / "intent")
    agent = mock_agent or MockAgent()
    vc = MockVersionControl()
    storage = FakeStorageBackend(tmp_dir, "src")

    state_mgr = StateManager(
        base_dir=tmp_dir,
        output_dir="src",
        backend=storage,
    )

    profile = AgentProfile(name="test", provider="cli", retries=1)
    builder = Builder(
        project=project,
        state_manager=state_mgr,
        version_control=vc,
        agent_profile=profile,
        create_agent=lambda _p: agent,
    )

    return builder, agent, state_mgr, vc, storage


def _build(builder: Builder, agent: MockAgent, **kwargs) -> tuple[list[BuildResult], RuntimeError | None]:
    """Run build with create_from_profile patched to return the mock agent."""
    with patch("intentc.build.validations.create_from_profile", return_value=agent):
        return builder.build(BuildOptions(**kwargs))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEndToEnd:
    """End-to-end build pipeline integration tests."""

    def test_full_build_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_dir = Path(tmpdir)
            builder, agent, state_mgr, vc, storage = _setup(tmp_dir)
            output_dir = str(tmp_dir / "src")

            results, error = _build(builder, agent, output_dir=output_dir)

            # No error
            assert error is None

            # 3 results, one per feature
            assert len(results) == 3

            # All built
            for r in results:
                assert r.status == "built"

            # Topological order: models before store before api
            targets = [r.target for r in results]
            assert targets.index("models") < targets.index("store")
            assert targets.index("store") < targets.index("api")

            # Build steps: resolve_deps, build, checkpoint for each
            for r in results:
                phases = [s.phase for s in r.steps]
                assert "resolve_deps" in phases
                assert "build" in phases
                assert "checkpoint" in phases

            # The feature with validations (api) has a validate step
            api_result = [r for r in results if r.target == "api"][0]
            api_phases = [s.phase for s in api_result.steps]
            assert "validate" in api_phases

            # All share the same generation_id (valid UUID)
            gen_ids = {r.generation_id for r in results}
            assert len(gen_ids) == 1
            gen_id = gen_ids.pop()
            assert gen_id is not None
            uuid.UUID(gen_id)  # raises if invalid

            # MockAgent.build_calls has 3 entries
            assert len(agent.build_calls) == 3

            # StateManager reports BUILT for all
            assert state_mgr.get_status("models") == TargetStatus.BUILT
            assert state_mgr.get_status("store") == TargetStatus.BUILT
            assert state_mgr.get_status("api") == TargetStatus.BUILT

    def test_idempotent_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_dir = Path(tmpdir)
            builder, agent, state_mgr, vc, storage = _setup(tmp_dir)
            output_dir = str(tmp_dir / "src")

            # First build
            _build(builder, agent, output_dir=output_dir)
            first_call_count = len(agent.build_calls)

            # Second build — nothing to do
            results, error = _build(builder, agent, output_dir=output_dir)

            assert error is None
            assert results == []
            assert len(agent.build_calls) == first_call_count  # no new calls

    def test_force_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_dir = Path(tmpdir)
            builder, agent, state_mgr, vc, storage = _setup(tmp_dir)
            output_dir = str(tmp_dir / "src")

            # First build
            _build(builder, agent, output_dir=output_dir)

            # Force rebuild
            results, error = _build(builder, agent, output_dir=output_dir, force=True)

            assert error is None
            assert len(results) == 3
            for r in results:
                assert r.status == "built"
            # 3 original + 3 forced = 6 total
            assert len(agent.build_calls) == 6

    def test_targeted_build_with_ancestors(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_dir = Path(tmpdir)
            builder, agent, state_mgr, vc, storage = _setup(tmp_dir)
            output_dir = str(tmp_dir / "src")

            results, error = _build(builder, agent, target="api", output_dir=output_dir)

            assert error is None

            # All 3 built (api depends on store depends on models)
            assert len(results) == 3
            targets = [r.target for r in results]
            assert targets == ["models", "store", "api"]

    def test_partial_build_then_continue(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_dir = Path(tmpdir)
            builder, agent, state_mgr, vc, storage = _setup(tmp_dir)
            output_dir = str(tmp_dir / "src")

            # Build only models
            results1, error1 = _build(builder, agent, target="models", output_dir=output_dir)
            assert error1 is None
            assert len(results1) == 1
            assert results1[0].target == "models"

            # Build all — models should be skipped
            results2, error2 = _build(builder, agent, output_dir=output_dir)
            assert error2 is None
            assert len(results2) == 2
            targets2 = [r.target for r in results2]
            assert "models" not in targets2
            assert "store" in targets2
            assert "api" in targets2

    def test_build_failure_stops_dag(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_dir = Path(tmpdir)

            class FailOnStoreAgent(MockAgent):
                """Agent that raises AgentError when building 'store'."""

                def build(self, ctx: BuildContext) -> BuildResponse:
                    self.build_calls.append(ctx)
                    if ctx.intent.name == "store":
                        raise AgentError("store build failed")
                    return BuildResponse(status="success", summary="ok")

            failing_agent = FailOnStoreAgent()
            builder, agent, state_mgr, vc, storage = _setup(tmp_dir, mock_agent=failing_agent)
            output_dir = str(tmp_dir / "src")

            results, error = _build(builder, failing_agent, output_dir=output_dir)

            # models built successfully
            models_results = [r for r in results if r.target == "models"]
            assert len(models_results) == 1
            assert models_results[0].status == "built"

            # store failed
            store_results = [r for r in results if r.target == "store"]
            assert len(store_results) == 1
            assert store_results[0].status == "failed"

            # api never attempted
            api_results = [r for r in results if r.target == "api"]
            assert len(api_results) == 0

            # Error returned
            assert error is not None

            # State reflects failure
            assert state_mgr.get_status("store") == TargetStatus.FAILED
