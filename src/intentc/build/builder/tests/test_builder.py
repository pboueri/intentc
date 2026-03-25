"""Tests for the Builder workflow engine."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from intentc.build.agents import (
    AgentError,
    AgentProfile,
    BuildContext,
    BuildResponse,
    MockAgent,
    ValidationResponse,
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
from intentc.build.validations import ValidationSuiteResult
from intentc.core.models import IntentFile, ProjectIntent, ValidationFile, Validation, ValidationType, Severity
from intentc.core.project import FeatureNode, Project


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeVersionControl(VersionControl):
    """In-memory version control for testing."""

    def __init__(self) -> None:
        self.checkpoints: list[tuple[str, str]] = []  # (message, commit_id)
        self.restores: list[str] = []
        self._counter = 0

    def checkpoint(self, message: str) -> str:
        self._counter += 1
        commit_id = f"fake-commit-{self._counter:04d}"
        self.checkpoints.append((message, commit_id))
        return commit_id

    def diff(self, from_id: str, to_id: str) -> str:
        return f"diff {from_id}..{to_id}"

    def restore(self, commit_id: str) -> None:
        self.restores.append(commit_id)

    def log(self, target: str | None = None) -> list[str]:
        return [cid for _, cid in self.checkpoints]


class FakeStorageBackend(StorageBackend):
    """Minimal in-memory storage for tests."""

    def __init__(self) -> None:
        super().__init__(Path("/tmp/fake"), "src")
        self._statuses: dict[str, TargetStatus] = {}
        self._results: dict[str, BuildResult] = {}
        self._generations: dict[str, dict] = {}
        self._gen_events: list[tuple[str, str]] = []
        self._saved_results: list[tuple[str, BuildResult]] = []
        self._saved_steps: list[tuple[int, BuildStep]] = []
        self._saved_agent_responses: list[dict] = []

    def create_generation(self, generation_id, output_dir, profile_name=None, options=None):
        self._generations[generation_id] = {
            "status": GenerationStatus.RUNNING.value,
            "output_dir": output_dir,
            "profile": profile_name,
        }

    def complete_generation(self, generation_id, status):
        if generation_id in self._generations:
            self._generations[generation_id]["status"] = status.value

    def log_generation_event(self, generation_id, message):
        self._gen_events.append((generation_id, message))

    def get_generation(self, generation_id):
        return self._generations.get(generation_id)

    def record_intent_version(self, name, source_path, content_hash):
        return 1

    def record_validation_version(self, target, source_path, content_hash):
        return 1

    def save_build_result(self, target, result, intent_version_id=None,
                          git_diff=None, files_created=None, files_modified=None):
        self._results[target] = result
        self._statuses[target] = TargetStatus(result.status) if result.status in TargetStatus._value2member_map_ else TargetStatus.PENDING
        self._saved_results.append((target, result))
        return len(self._saved_results)

    def get_build_result(self, target):
        return self._results.get(target)

    def get_build_history(self, target, limit=50):
        r = self._results.get(target)
        return [r] if r else []

    def save_build_step(self, build_result_id, step, log, step_order):
        self._saved_steps.append((build_result_id, step))

    def save_validation_result(self, build_result_id, generation_id, target,
                                validation_file_version_id, name, type, severity,
                                status, reason="", duration_secs=None):
        return 1

    def save_agent_response(self, build_result_id, validation_result_id,
                            response_type, response_json):
        self._saved_agent_responses.append(response_json)

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


def _make_project(
    features: dict[str, list[str]] | None = None,
    with_validations: bool = False,
) -> Project:
    """Build a simple Project with given features and deps.

    features: {feature_path: [dep1, dep2, ...]}
    """
    if features is None:
        features = {"core": [], "api": ["core"]}

    project_intent = ProjectIntent(name="test-project", body="A test project")
    feat_nodes: dict[str, FeatureNode] = {}

    for path, deps in features.items():
        intent = IntentFile(name=path, depends_on=deps, body=f"Feature {path}")
        validations = []
        if with_validations:
            val = Validation(
                name=f"{path}-check",
                type=ValidationType.AGENT_VALIDATION,
                severity=Severity.ERROR,
                args={"rubric": f"Check {path}"},
            )
            vf = ValidationFile(target=path, validations=[val])
            validations = [vf]
        feat_nodes[path] = FeatureNode(path=path, intents=[intent], validations=validations)

    return Project(
        project_intent=project_intent,
        features=feat_nodes,
    )


def _make_builder(
    project: Project | None = None,
    mock_agent: MockAgent | None = None,
    storage: FakeStorageBackend | None = None,
    vc: FakeVersionControl | None = None,
) -> tuple[Builder, MockAgent, FakeStorageBackend, FakeVersionControl]:
    """Create a Builder with test doubles."""
    project = project or _make_project()
    agent = mock_agent or MockAgent()
    storage_backend = storage or FakeStorageBackend()
    version_control = vc or FakeVersionControl()

    profile = AgentProfile(name="test", provider="cli")

    with tempfile.TemporaryDirectory() as tmpdir:
        state_mgr = StateManager(
            base_dir=Path(tmpdir),
            output_dir="src",
            backend=storage_backend,
        )

        builder = Builder(
            project=project,
            state_manager=state_mgr,
            version_control=version_control,
            agent_profile=profile,
            create_agent=lambda _p: agent,
        )

        # Patch state_manager to survive tmpdir cleanup by keeping refs alive
        builder._state_manager = state_mgr

    return builder, agent, storage_backend, version_control


# ---------------------------------------------------------------------------
# Tests: Build pipeline
# ---------------------------------------------------------------------------


class TestBuildPipeline:
    """Tests for the core build() method."""

    def test_build_empty_project(self):
        """Building a project with no features returns empty results."""
        project = _make_project(features={})
        builder, agent, storage, vc = _make_builder(project=project)

        results, error = builder.build(BuildOptions(output_dir="/tmp/out"))

        assert results == []
        assert error is None
        assert len(agent.build_calls) == 0

    def test_build_single_target(self):
        """Building a single-feature project invokes the agent once."""
        project = _make_project(features={"core": []})
        builder, agent, storage, vc = _make_builder(project=project)

        with tempfile.TemporaryDirectory() as out_dir:
            results, error = builder.build(
                BuildOptions(output_dir=out_dir)
            )

        assert error is None
        assert len(results) == 1
        assert results[0].target == "core"
        assert results[0].status == "built"
        assert len(agent.build_calls) == 1

    def test_build_topological_order(self):
        """Targets are built in dependency-first order."""
        project = _make_project(features={
            "core": [],
            "api": ["core"],
            "cli": ["api"],
        })
        builder, agent, storage, vc = _make_builder(project=project)

        with tempfile.TemporaryDirectory() as out_dir:
            results, error = builder.build(BuildOptions(output_dir=out_dir))

        assert error is None
        targets_built = [r.target for r in results]
        assert targets_built == ["core", "api", "cli"]

    def test_build_specific_target_with_ancestors(self):
        """Building a specific target also builds its ancestors."""
        project = _make_project(features={
            "core": [],
            "api": ["core"],
            "cli": ["api"],
        })
        builder, agent, storage, vc = _make_builder(project=project)

        with tempfile.TemporaryDirectory() as out_dir:
            results, error = builder.build(
                BuildOptions(target="api", output_dir=out_dir)
            )

        assert error is None
        targets_built = [r.target for r in results]
        assert "core" in targets_built
        assert "api" in targets_built
        assert "cli" not in targets_built

    def test_build_skips_already_built(self):
        """Already-built targets are skipped unless force is set."""
        project = _make_project(features={"core": [], "api": ["core"]})
        builder, agent, storage, vc = _make_builder(project=project)

        # Mark core as built
        storage.set_status("core", TargetStatus.BUILT)
        storage._results["core"] = BuildResult(target="core", status="built")

        with tempfile.TemporaryDirectory() as out_dir:
            results, error = builder.build(BuildOptions(output_dir=out_dir))

        assert error is None
        # Only api should be built (core was skipped)
        targets_built = [r.target for r in results]
        assert "api" in targets_built
        assert "core" not in targets_built

    def test_build_force_rebuilds_built(self):
        """Force flag rebuilds already-built targets."""
        project = _make_project(features={"core": []})
        builder, agent, storage, vc = _make_builder(project=project)

        storage.set_status("core", TargetStatus.BUILT)
        storage._results["core"] = BuildResult(target="core", status="built")

        with tempfile.TemporaryDirectory() as out_dir:
            results, error = builder.build(
                BuildOptions(output_dir=out_dir, force=True)
            )

        assert error is None
        assert len(results) == 1
        assert results[0].target == "core"

    def test_build_dry_run(self):
        """Dry run returns build plan without side effects."""
        project = _make_project(features={"core": [], "api": ["core"]})
        builder, agent, storage, vc = _make_builder(project=project)

        results, error = builder.build(
            BuildOptions(output_dir="/tmp/out", dry_run=True)
        )

        assert error is None
        assert len(results) == 2
        assert len(agent.build_calls) == 0
        assert len(vc.checkpoints) == 0

    def test_build_generation_id_shared(self):
        """All targets in a build share the same generation ID."""
        project = _make_project(features={"core": [], "api": ["core"]})
        builder, agent, storage, vc = _make_builder(project=project)

        with tempfile.TemporaryDirectory() as out_dir:
            results, error = builder.build(BuildOptions(output_dir=out_dir))

        assert error is None
        gen_ids = {r.generation_id for r in results}
        assert len(gen_ids) == 1
        assert gen_ids.pop() is not None

    def test_build_steps_order(self):
        """Each target produces steps in order: resolve_deps, build, checkpoint."""
        project = _make_project(features={"core": []})
        builder, agent, storage, vc = _make_builder(project=project)

        with tempfile.TemporaryDirectory() as out_dir:
            results, error = builder.build(BuildOptions(output_dir=out_dir))

        assert error is None
        steps = results[0].steps
        phases = [s.phase for s in steps]
        assert phases == ["resolve_deps", "build", "checkpoint"]

    def test_build_checkpoint_after_success(self):
        """Checkpoint only happens after build succeeds (atomicity)."""
        project = _make_project(features={"core": []})
        builder, agent, storage, vc = _make_builder(project=project)

        with tempfile.TemporaryDirectory() as out_dir:
            results, error = builder.build(BuildOptions(output_dir=out_dir))

        assert error is None
        assert len(vc.checkpoints) == 1
        msg, _ = vc.checkpoints[0]
        assert "core" in msg

    def test_build_commit_id_on_result(self):
        """BuildResult has commit_id from the checkpoint step."""
        project = _make_project(features={"core": []})
        builder, agent, storage, vc = _make_builder(project=project)

        with tempfile.TemporaryDirectory() as out_dir:
            results, error = builder.build(BuildOptions(output_dir=out_dir))

        assert error is None
        assert results[0].commit_id.startswith("fake-commit-")

    def test_build_step_timing(self):
        """Build steps have non-negative durations."""
        project = _make_project(features={"core": []})
        builder, agent, storage, vc = _make_builder(project=project)

        with tempfile.TemporaryDirectory() as out_dir:
            results, error = builder.build(BuildOptions(output_dir=out_dir))

        assert error is None
        for step in results[0].steps:
            assert step.duration_secs >= 0

    def test_build_total_duration(self):
        """Total duration is sum of step durations."""
        project = _make_project(features={"core": []})
        builder, agent, storage, vc = _make_builder(project=project)

        with tempfile.TemporaryDirectory() as out_dir:
            results, error = builder.build(BuildOptions(output_dir=out_dir))

        assert error is None
        result = results[0]
        expected = sum(s.duration_secs for s in result.steps)
        assert abs(result.total_duration_secs - expected) < 0.001


# ---------------------------------------------------------------------------
# Tests: Build failure and retries
# ---------------------------------------------------------------------------


class TestBuildFailure:
    """Tests for failure handling and retries."""

    def test_build_failure_stops_dag(self):
        """A failed target stops the DAG walk immediately."""
        project = _make_project(features={"core": [], "api": ["core"]})
        failing_agent = MockAgent(
            build_response=BuildResponse(
                status="failure",
                summary="Compilation error",
            )
        )
        builder, agent, storage, vc = _make_builder(
            project=project, mock_agent=failing_agent
        )

        with tempfile.TemporaryDirectory() as out_dir:
            results, error = builder.build(BuildOptions(output_dir=out_dir))

        assert error is not None
        assert "core" in str(error)
        # Only core was attempted, api was not reached
        targets = [r.target for r in results]
        assert "core" in targets
        assert "api" not in targets

    def test_build_failure_no_checkpoint(self):
        """Failed targets are not checkpointed."""
        project = _make_project(features={"core": []})
        failing_agent = MockAgent(
            build_response=BuildResponse(
                status="failure",
                summary="Build error",
            )
        )
        builder, _, storage, vc = _make_builder(
            project=project, mock_agent=failing_agent
        )

        with tempfile.TemporaryDirectory() as out_dir:
            results, error = builder.build(BuildOptions(output_dir=out_dir))

        assert error is not None
        assert len(vc.checkpoints) == 0

    def test_build_failure_marked_failed(self):
        """Failed targets have status 'failed'."""
        project = _make_project(features={"core": []})
        failing_agent = MockAgent(
            build_response=BuildResponse(
                status="failure",
                summary="Build error",
            )
        )
        builder, _, storage, vc = _make_builder(
            project=project, mock_agent=failing_agent
        )

        with tempfile.TemporaryDirectory() as out_dir:
            results, error = builder.build(BuildOptions(output_dir=out_dir))

        assert results[0].status == "failed"

    def test_build_retries_on_agent_error(self):
        """Builder retries on AgentError up to profile.retries times."""
        project = _make_project(features={"core": []})

        call_count = 0

        class FailThenSucceedAgent(MockAgent):
            def build(self, ctx: BuildContext) -> BuildResponse:
                nonlocal call_count
                call_count += 1
                if call_count < 3:
                    raise AgentError("Transient failure")
                return BuildResponse(status="success", summary="OK")

        agent = FailThenSucceedAgent()
        profile = AgentProfile(name="test", provider="cli", retries=3)

        with tempfile.TemporaryDirectory() as tmpdir:
            storage = FakeStorageBackend()
            vc = FakeVersionControl()
            state_mgr = StateManager(
                base_dir=Path(tmpdir),
                output_dir="src",
                backend=storage,
            )

            builder = Builder(
                project=project,
                state_manager=state_mgr,
                version_control=vc,
                agent_profile=profile,
                create_agent=lambda _p: agent,
            )

            results, error = builder.build(
                BuildOptions(output_dir=os.path.join(tmpdir, "out"))
            )

        assert error is None
        assert results[0].status == "built"
        assert call_count == 3

    def test_build_error_returned_not_raised(self):
        """Build errors are returned, not raised."""
        project = _make_project(features={"core": []})
        failing_agent = MockAgent(
            build_response=BuildResponse(status="failure", summary="fail")
        )
        builder, _, storage, vc = _make_builder(
            project=project, mock_agent=failing_agent
        )

        with tempfile.TemporaryDirectory() as out_dir:
            # Should not raise
            results, error = builder.build(BuildOptions(output_dir=out_dir))

        assert isinstance(error, RuntimeError)

    def test_generation_marked_failed_on_error(self):
        """Generation status is set to failed when a target fails."""
        project = _make_project(features={"core": []})
        failing_agent = MockAgent(
            build_response=BuildResponse(status="failure", summary="fail")
        )
        builder, _, storage, vc = _make_builder(
            project=project, mock_agent=failing_agent
        )

        with tempfile.TemporaryDirectory() as out_dir:
            builder.build(BuildOptions(output_dir=out_dir))

        # Check generation was completed with failed status
        gen_id = list(storage._generations.keys())[0]
        assert storage._generations[gen_id]["status"] == GenerationStatus.FAILED.value

    def test_generation_marked_completed_on_success(self):
        """Generation status is set to completed on success."""
        project = _make_project(features={"core": []})
        builder, _, storage, vc = _make_builder(project=project)

        with tempfile.TemporaryDirectory() as out_dir:
            builder.build(BuildOptions(output_dir=out_dir))

        gen_id = list(storage._generations.keys())[0]
        assert storage._generations[gen_id]["status"] == GenerationStatus.COMPLETED.value


# ---------------------------------------------------------------------------
# Tests: Clean
# ---------------------------------------------------------------------------


class TestClean:
    """Tests for the clean() and clean_all() methods."""

    def test_clean_resets_state(self):
        """Clean resets target state to pending."""
        project = _make_project(features={"core": [], "api": ["core"]})
        builder, _, storage, vc = _make_builder(project=project)

        storage.set_status("core", TargetStatus.BUILT)
        storage._results["core"] = BuildResult(
            target="core", status="built", commit_id="abc123"
        )

        builder.clean("core", "/tmp/out")

        assert storage.get_status("core") == TargetStatus.PENDING

    def test_clean_restores_via_version_control(self):
        """Clean calls version_control.restore with the commit_id."""
        project = _make_project(features={"core": []})
        builder, _, storage, vc = _make_builder(project=project)

        storage.set_status("core", TargetStatus.BUILT)
        storage._results["core"] = BuildResult(
            target="core", status="built", commit_id="abc123"
        )

        builder.clean("core", "/tmp/out")

        assert "abc123" in vc.restores

    def test_clean_marks_descendants_outdated(self):
        """Clean marks all descendants of the target as outdated."""
        project = _make_project(features={"core": [], "api": ["core"]})
        builder, _, storage, vc = _make_builder(project=project)

        storage.set_status("core", TargetStatus.BUILT)
        storage.set_status("api", TargetStatus.BUILT)
        storage._results["core"] = BuildResult(
            target="core", status="built", commit_id="abc123"
        )

        builder.clean("core", "/tmp/out")

        assert storage.get_status("api") == TargetStatus.OUTDATED

    def test_clean_no_result_does_nothing(self):
        """Clean with no prior build result returns early."""
        project = _make_project(features={"core": []})
        builder, _, storage, vc = _make_builder(project=project)

        builder.clean("core", "/tmp/out")  # Should not raise

        assert len(vc.restores) == 0

    def test_clean_all_resets_all_state(self):
        """CleanAll resets all state without modifying files."""
        project = _make_project(features={"core": [], "api": ["core"]})
        builder, _, storage, vc = _make_builder(project=project)

        storage.set_status("core", TargetStatus.BUILT)
        storage.set_status("api", TargetStatus.BUILT)

        builder.clean_all("/tmp/out")

        assert len(storage._statuses) == 0
        assert len(vc.restores) == 0  # No file modifications


# ---------------------------------------------------------------------------
# Tests: Validate
# ---------------------------------------------------------------------------


class TestValidate:
    """Tests for the validate() method."""

    def test_validate_returns_result(self):
        """Validate returns a ValidationSuiteResult without modifying state."""
        project = _make_project(features={"core": []}, with_validations=True)
        builder, _, storage, vc = _make_builder(project=project)

        result = builder.validate("core", "/tmp/out")

        assert isinstance(result, ValidationSuiteResult)
        assert result.target == "core"

    def test_validate_does_not_modify_state(self):
        """Validate does not save build results or change target status."""
        project = _make_project(features={"core": []}, with_validations=True)
        builder, _, storage, vc = _make_builder(project=project)

        storage.set_status("core", TargetStatus.BUILT)
        builder.validate("core", "/tmp/out")

        assert storage.get_status("core") == TargetStatus.BUILT
        assert len(vc.checkpoints) == 0


# ---------------------------------------------------------------------------
# Tests: Detect outdated
# ---------------------------------------------------------------------------


class TestDetectOutdated:
    """Tests for the detect_outdated() method."""

    def test_detect_outdated_with_newer_files(self):
        """Targets with source files newer than build timestamp are outdated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create an intent file
            ic_path = Path(tmpdir) / "core.ic"
            ic_path.write_text("# Core intent")

            intent = IntentFile(
                name="core",
                body="Core intent",
                source_path=ic_path,
            )
            node = FeatureNode(path="core", intents=[intent])
            project = Project(
                project_intent=ProjectIntent(name="test", body="test"),
                features={"core": node},
            )

            builder, _, storage, vc = _make_builder(project=project)

            # Mark as built with an old timestamp
            old_time = datetime.now() - timedelta(hours=1)
            storage.set_status("core", TargetStatus.BUILT)
            storage._results["core"] = BuildResult(
                target="core",
                status="built",
                timestamp=old_time.isoformat(),
            )

            # Touch the file to make it newer
            os.utime(ic_path, None)

            outdated = builder.detect_outdated()

        assert "core" in outdated

    def test_detect_outdated_current_files(self):
        """Targets with source files older than build are not outdated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ic_path = Path(tmpdir) / "core.ic"
            ic_path.write_text("# Core intent")

            intent = IntentFile(
                name="core",
                body="Core intent",
                source_path=ic_path,
            )
            node = FeatureNode(path="core", intents=[intent])
            project = Project(
                project_intent=ProjectIntent(name="test", body="test"),
                features={"core": node},
            )

            builder, _, storage, vc = _make_builder(project=project)

            # Mark as built with a future timestamp
            future_time = datetime.now() + timedelta(hours=1)
            storage.set_status("core", TargetStatus.BUILT)
            storage._results["core"] = BuildResult(
                target="core",
                status="built",
                timestamp=future_time.isoformat(),
            )

            outdated = builder.detect_outdated()

        assert "core" not in outdated

    def test_detect_outdated_skips_non_built(self):
        """Targets not in 'built' status are ignored."""
        project = _make_project(features={"core": []})
        builder, _, storage, vc = _make_builder(project=project)

        storage.set_status("core", TargetStatus.PENDING)

        outdated = builder.detect_outdated()

        assert outdated == []

    def test_detect_outdated_does_not_modify_state(self):
        """detect_outdated() does not change any state."""
        project = _make_project(features={"core": []})
        builder, _, storage, vc = _make_builder(project=project)

        storage.set_status("core", TargetStatus.BUILT)
        storage._results["core"] = BuildResult(
            target="core",
            status="built",
            timestamp=datetime.now().isoformat(),
        )

        builder.detect_outdated()

        assert storage.get_status("core") == TargetStatus.BUILT


# ---------------------------------------------------------------------------
# Tests: Build context
# ---------------------------------------------------------------------------


class TestBuildContext:
    """Tests for correct BuildContext construction."""

    def test_build_context_has_dependency_names(self):
        """BuildContext includes resolved dependency names."""
        project = _make_project(features={"core": [], "api": ["core"]})
        builder, agent, storage, vc = _make_builder(project=project)

        with tempfile.TemporaryDirectory() as out_dir:
            builder.build(BuildOptions(output_dir=out_dir))

        # api's build context should have "core" as dependency
        api_ctx = [c for c in agent.build_calls if c.intent.name == "api"]
        assert len(api_ctx) == 1
        assert "core" in api_ctx[0].dependency_names

    def test_build_context_has_generation_id(self):
        """BuildContext has the shared generation ID."""
        project = _make_project(features={"core": []})
        builder, agent, storage, vc = _make_builder(project=project)

        with tempfile.TemporaryDirectory() as out_dir:
            builder.build(BuildOptions(output_dir=out_dir))

        assert agent.build_calls[0].generation_id is not None
        assert len(agent.build_calls[0].generation_id) > 0

    def test_build_context_has_project_intent(self):
        """BuildContext has the project intent."""
        project = _make_project(features={"core": []})
        builder, agent, storage, vc = _make_builder(project=project)

        with tempfile.TemporaryDirectory() as out_dir:
            builder.build(BuildOptions(output_dir=out_dir))

        assert agent.build_calls[0].project_intent.name == "test-project"


# ---------------------------------------------------------------------------
# Tests: Profile resolution
# ---------------------------------------------------------------------------


class TestProfileResolution:
    """Tests for agent profile resolution."""

    def test_profile_override(self):
        """Profile override takes priority over builder's profile."""
        project = _make_project(features={"core": []})
        builder, agent, storage, vc = _make_builder(project=project)

        # The create_agent mock doesn't use the profile, but we can verify
        # the builder's _resolve_profile method
        resolved = builder._resolve_profile("custom")
        assert resolved.name == "custom"

    def test_profile_default(self):
        """No override falls back to builder's agent_profile."""
        project = _make_project(features={"core": []})
        builder, agent, storage, vc = _make_builder(project=project)

        resolved = builder._resolve_profile("")
        assert resolved.name == "test"


# ---------------------------------------------------------------------------
# Tests: Logging
# ---------------------------------------------------------------------------


class TestLogging:
    """Tests for progress logging."""

    def test_log_callback_invoked(self):
        """Builder invokes log callback at significant steps."""
        project = _make_project(features={"core": []})
        logs: list[str] = []

        profile = AgentProfile(name="test", provider="cli")
        storage = FakeStorageBackend()
        vc = FakeVersionControl()
        agent = MockAgent()

        with tempfile.TemporaryDirectory() as tmpdir:
            state_mgr = StateManager(
                base_dir=Path(tmpdir),
                output_dir="src",
                backend=storage,
            )

            builder = Builder(
                project=project,
                state_manager=state_mgr,
                version_control=vc,
                agent_profile=profile,
                log=logs.append,
                create_agent=lambda _p: agent,
            )

            builder.build(BuildOptions(output_dir=os.path.join(tmpdir, "out")))

        assert len(logs) > 0
        assert any("Build plan" in msg for msg in logs)
        assert any("core" in msg for msg in logs)
