from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

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
    BuildStep,
    StateManager,
    TargetStatus,
    VersionControl,
)
from intentc.build.storage.backend import GenerationStatus
from intentc.build.storage.sqlite_backend import SQLiteBackend
from intentc.core.project import FeatureNode, Project
from intentc.core.types import (
    Implementation,
    IntentFile,
    ProjectIntent,
    Validation,
    ValidationFile,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(
    features: dict[str, FeatureNode] | None = None,
    intent_dir: Path | None = None,
) -> Project:
    if features is None:
        features = {
            "core": FeatureNode(
                path="core",
                intents=[IntentFile(name="core", body="core feature")],
            ),
            "auth": FeatureNode(
                path="auth",
                intents=[IntentFile(name="auth", depends_on=["core"], body="auth feature")],
            ),
            "api": FeatureNode(
                path="api",
                intents=[IntentFile(name="api", depends_on=["auth"], body="api feature")],
            ),
        }
    return Project(
        project_intent=ProjectIntent(name="test-project", body="A test project"),
        features=features,
        intent_dir=intent_dir,
    )


def _make_profile() -> AgentProfile:
    return AgentProfile(name="test", provider="cli", command="echo", retries=3)


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


def _make_builder(
    tmp_path: Path,
    project: Project | None = None,
    mock_agent: MockAgent | None = None,
    vc: MockVersionControl | None = None,
    profile: AgentProfile | None = None,
) -> tuple[Builder, MockAgent, MockVersionControl, StateManager]:
    proj = project or _make_project()
    agent = mock_agent or MockAgent()
    version_control = vc or MockVersionControl()
    prof = profile or _make_profile()
    backend = SQLiteBackend(tmp_path, "out")
    sm = StateManager(tmp_path, "out", backend=backend)

    builder = Builder(
        project=proj,
        state_manager=sm,
        version_control=version_control,
        agent_profile=prof,
        create_agent=lambda _profile: agent,
    )
    return builder, agent, version_control, sm


# ---------------------------------------------------------------------------
# Build pipeline tests
# ---------------------------------------------------------------------------


class TestBuildPipeline:
    def test_build_all_targets_in_topological_order(self, tmp_path: Path) -> None:
        builder, agent, vc, sm = _make_builder(tmp_path)
        output_dir = str(tmp_path / "output")
        opts = BuildOptions(output_dir=output_dir)

        results, error = builder.build(opts)

        assert error is None
        assert len(results) == 3
        assert results[0].target == "core"
        assert results[1].target == "auth"
        assert results[2].target == "api"
        # All should be built
        for r in results:
            assert r.status == TargetStatus.BUILT
        # Agent was called 3 times
        assert len(agent.build_calls) == 3

    def test_build_specific_target_builds_ancestors(self, tmp_path: Path) -> None:
        builder, agent, vc, sm = _make_builder(tmp_path)
        output_dir = str(tmp_path / "output")
        opts = BuildOptions(target="auth", output_dir=output_dir)

        results, error = builder.build(opts)

        assert error is None
        assert len(results) == 2
        assert results[0].target == "core"
        assert results[1].target == "auth"

    def test_build_skips_already_built(self, tmp_path: Path) -> None:
        builder, agent, vc, sm = _make_builder(tmp_path)
        output_dir = str(tmp_path / "output")

        # Build all first
        opts = BuildOptions(output_dir=output_dir)
        builder.build(opts)
        call_count_after_first = len(agent.build_calls)

        # Build again without force
        results, error = builder.build(opts)
        assert error is None
        assert len(results) == 0  # nothing to build
        assert len(agent.build_calls) == call_count_after_first

    def test_build_force_rebuilds(self, tmp_path: Path) -> None:
        builder, agent, vc, sm = _make_builder(tmp_path)
        output_dir = str(tmp_path / "output")

        # Build all first
        opts = BuildOptions(output_dir=output_dir)
        builder.build(opts)

        # Force rebuild
        opts = BuildOptions(output_dir=output_dir, force=True)
        results, error = builder.build(opts)

        assert error is None
        assert len(results) == 3

    def test_build_empty_project_returns_empty(self, tmp_path: Path) -> None:
        project = _make_project(features={})
        builder, agent, vc, sm = _make_builder(tmp_path, project=project)
        opts = BuildOptions(output_dir=str(tmp_path / "output"))

        results, error = builder.build(opts)

        assert error is None
        assert len(results) == 0

    def test_dry_run_returns_statuses_without_building(self, tmp_path: Path) -> None:
        builder, agent, vc, sm = _make_builder(tmp_path)
        opts = BuildOptions(output_dir=str(tmp_path / "output"), dry_run=True)

        results, error = builder.build(opts)

        assert error is None
        assert len(results) == 3
        assert len(agent.build_calls) == 0  # no agent calls
        assert len(vc.checkpoints) == 0  # no checkpoints

    def test_build_steps_recorded(self, tmp_path: Path) -> None:
        builder, agent, vc, sm = _make_builder(tmp_path)
        output_dir = str(tmp_path / "output")
        opts = BuildOptions(target="core", output_dir=output_dir)

        results, error = builder.build(opts)

        assert error is None
        assert len(results) == 1
        result = results[0]
        phases = [s.phase for s in result.steps]
        assert "resolve_deps" in phases
        assert "build" in phases
        assert "checkpoint" in phases

    def test_build_result_has_generation_id(self, tmp_path: Path) -> None:
        builder, agent, vc, sm = _make_builder(tmp_path)
        opts = BuildOptions(output_dir=str(tmp_path / "output"))

        results, error = builder.build(opts)

        assert error is None
        gen_id = results[0].generation_id
        assert gen_id  # non-empty
        # All results share the same generation ID
        for r in results:
            assert r.generation_id == gen_id

    def test_build_result_has_commit_id(self, tmp_path: Path) -> None:
        builder, agent, vc, sm = _make_builder(tmp_path)
        opts = BuildOptions(target="core", output_dir=str(tmp_path / "output"))

        results, error = builder.build(opts)

        assert error is None
        assert results[0].commit_id.startswith("abc")

    def test_build_result_has_duration(self, tmp_path: Path) -> None:
        builder, agent, vc, sm = _make_builder(tmp_path)
        opts = BuildOptions(target="core", output_dir=str(tmp_path / "output"))

        results, error = builder.build(opts)

        assert error is None
        assert results[0].total_duration >= timedelta(0)

    def test_build_result_has_timestamp(self, tmp_path: Path) -> None:
        builder, agent, vc, sm = _make_builder(tmp_path)
        before = datetime.now(timezone.utc)
        opts = BuildOptions(target="core", output_dir=str(tmp_path / "output"))

        results, error = builder.build(opts)

        assert error is None
        assert results[0].timestamp >= before


class TestBuildFailure:
    def test_agent_error_stops_dag_walk(self, tmp_path: Path) -> None:
        call_count = 0

        class FailingAgent:
            def __init__(self) -> None:
                self.build_calls: list = []

            def get_name(self) -> str:
                return "failing"

            def get_type(self) -> str:
                return "mock"

            def build(self, ctx: BuildContext) -> BuildResponse:
                nonlocal call_count
                call_count += 1
                raise AgentError("agent crashed")

            def validate(self, ctx, validation):
                pass

        agent = FailingAgent()
        proj = _make_project()
        vc = MockVersionControl()
        prof = _make_profile()
        backend = SQLiteBackend(tmp_path, "out")
        sm = StateManager(tmp_path, "out", backend=backend)

        builder = Builder(
            project=proj,
            state_manager=sm,
            version_control=vc,
            agent_profile=prof,
            create_agent=lambda _: agent,
        )

        opts = BuildOptions(output_dir=str(tmp_path / "output"))
        results, error = builder.build(opts)

        assert error is not None
        assert "core" in str(error)
        # Only one target attempted (core), and it retried 3 times
        assert call_count == 3
        assert len(results) == 1
        assert results[0].status == TargetStatus.FAILED

    def test_failure_returns_runtime_error(self, tmp_path: Path) -> None:
        agent = MockAgent(
            build_response=BuildResponse(status="failure", summary="bad code")
        )
        builder, _, vc, sm = _make_builder(tmp_path, mock_agent=agent)
        opts = BuildOptions(target="core", output_dir=str(tmp_path / "output"))

        results, error = builder.build(opts)

        assert error is not None
        assert isinstance(error, RuntimeError)

    def test_failure_does_not_checkpoint(self, tmp_path: Path) -> None:
        agent = MockAgent(
            build_response=BuildResponse(status="failure", summary="bad code")
        )
        builder, _, vc, sm = _make_builder(tmp_path, mock_agent=agent)
        opts = BuildOptions(target="core", output_dir=str(tmp_path / "output"))

        results, error = builder.build(opts)

        assert error is not None
        assert len(vc.checkpoints) == 0

    def test_retries_on_agent_error(self, tmp_path: Path) -> None:
        attempts = 0

        class RetryAgent:
            def get_name(self) -> str:
                return "retry"

            def get_type(self) -> str:
                return "mock"

            def build(self, ctx: BuildContext) -> BuildResponse:
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    raise AgentError("transient error")
                return BuildResponse(status="success", summary="ok")

            def validate(self, ctx, validation):
                pass

        proj = _make_project(features={
            "single": FeatureNode(
                path="single",
                intents=[IntentFile(name="single", body="test")],
            ),
        })
        vc = MockVersionControl()
        prof = AgentProfile(name="test", provider="cli", command="echo", retries=3)
        backend = SQLiteBackend(tmp_path, "out")
        sm = StateManager(tmp_path, "out", backend=backend)

        builder = Builder(
            project=proj,
            state_manager=sm,
            version_control=vc,
            agent_profile=prof,
            create_agent=lambda _: RetryAgent(),
        )

        opts = BuildOptions(output_dir=str(tmp_path / "output"))
        results, error = builder.build(opts)

        assert error is None
        assert attempts == 3
        assert results[0].status == TargetStatus.BUILT


class TestBuildWithValidations:
    def test_validation_step_runs_when_validations_exist(self, tmp_path: Path) -> None:
        """When validations exist, the validate phase appears in the steps.
        Since the CLIAgent's validate can't produce a response file in test,
        the validation will fail — but the step is still recorded."""
        features = {
            "feat": FeatureNode(
                path="feat",
                intents=[IntentFile(name="feat", body="feature")],
                validations=[
                    ValidationFile(
                        target="feat",
                        validations=[
                            Validation(name="check1", type="agent_validation"),
                        ],
                    )
                ],
            ),
        }
        project = _make_project(features=features)
        builder, agent, vc, sm = _make_builder(tmp_path, project=project)
        output_dir = str(tmp_path / "output")
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        opts = BuildOptions(output_dir=output_dir)

        results, error = builder.build(opts)

        # The validate step should be present in the result
        phases = [s.phase for s in results[0].steps]
        assert "validate" in phases

    def test_no_validation_step_when_no_validations(self, tmp_path: Path) -> None:
        builder, agent, vc, sm = _make_builder(tmp_path)
        opts = BuildOptions(target="core", output_dir=str(tmp_path / "output"))

        results, error = builder.build(opts)

        assert error is None
        phases = [s.phase for s in results[0].steps]
        assert "validate" not in phases


# ---------------------------------------------------------------------------
# Clean tests
# ---------------------------------------------------------------------------


class TestClean:
    def test_clean_resets_state_to_pending(self, tmp_path: Path) -> None:
        builder, agent, vc, sm = _make_builder(tmp_path)
        output_dir = str(tmp_path / "output")

        # Build first
        opts = BuildOptions(target="core", output_dir=output_dir)
        builder.build(opts)
        assert sm.get_status("core") == TargetStatus.BUILT

        # Clean
        builder.clean("core", output_dir)
        assert sm.get_status("core") == TargetStatus.PENDING

    def test_clean_calls_version_control_restore(self, tmp_path: Path) -> None:
        builder, agent, vc, sm = _make_builder(tmp_path)
        output_dir = str(tmp_path / "output")

        opts = BuildOptions(target="core", output_dir=output_dir)
        builder.build(opts)

        builder.clean("core", output_dir)
        assert len(vc.restores) == 1

    def test_clean_marks_descendants_outdated(self, tmp_path: Path) -> None:
        builder, agent, vc, sm = _make_builder(tmp_path)
        output_dir = str(tmp_path / "output")

        # Build all
        opts = BuildOptions(output_dir=output_dir)
        builder.build(opts)

        # Clean core
        builder.clean("core", output_dir)
        assert sm.get_status("auth") == TargetStatus.OUTDATED
        assert sm.get_status("api") == TargetStatus.OUTDATED

    def test_clean_nonexistent_target_is_noop(self, tmp_path: Path) -> None:
        builder, agent, vc, sm = _make_builder(tmp_path)
        # Should not raise
        builder.clean("nonexistent", str(tmp_path / "output"))

    def test_clean_all_resets_all_state(self, tmp_path: Path) -> None:
        builder, agent, vc, sm = _make_builder(tmp_path)
        output_dir = str(tmp_path / "output")

        # Build all
        opts = BuildOptions(output_dir=output_dir)
        builder.build(opts)

        builder.clean_all(output_dir)
        targets = sm.list_targets()
        assert len(targets) == 0


# ---------------------------------------------------------------------------
# Validate tests
# ---------------------------------------------------------------------------


class TestValidate:
    def test_validate_feature_returns_result(self, tmp_path: Path) -> None:
        # Feature with no validations — suite returns passed
        features = {
            "feat": FeatureNode(
                path="feat",
                intents=[IntentFile(name="feat", body="feature")],
            ),
        }
        project = _make_project(features=features)
        builder, agent, vc, sm = _make_builder(tmp_path, project=project)

        result = builder.validate("feat", str(tmp_path / "output"))
        assert result.target == "feat"
        assert result.passed is True

    def test_validate_project_returns_list(self, tmp_path: Path) -> None:
        builder, agent, vc, sm = _make_builder(tmp_path)

        results = builder.validate(None, str(tmp_path / "output"))
        assert isinstance(results, list)

    def test_validate_does_not_modify_state(self, tmp_path: Path) -> None:
        builder, agent, vc, sm = _make_builder(tmp_path)

        builder.validate(None, str(tmp_path / "output"))
        targets = sm.list_targets()
        assert len(targets) == 0


# ---------------------------------------------------------------------------
# Invalidation tests
# ---------------------------------------------------------------------------


class TestDetectOutdated:
    def test_detects_outdated_when_source_modified(self, tmp_path: Path) -> None:
        # Create intent files on disk
        intent_path = tmp_path / "intents" / "feat.ic"
        intent_path.parent.mkdir(parents=True, exist_ok=True)
        intent_path.write_text("---\nname: feat\n---\noriginal")

        features = {
            "feat": FeatureNode(
                path="feat",
                intents=[IntentFile(name="feat", body="original", source_path=intent_path)],
            ),
        }
        project = _make_project(features=features, intent_dir=tmp_path / "intents")
        builder, agent, vc, sm = _make_builder(tmp_path, project=project)

        # Build
        opts = BuildOptions(output_dir=str(tmp_path / "output"))
        builder.build(opts)

        # Touch the intent file (make it newer)
        time.sleep(0.05)
        intent_path.write_text("---\nname: feat\n---\nupdated")

        outdated = builder.detect_outdated()
        assert "feat" in outdated

    def test_no_outdated_when_unchanged(self, tmp_path: Path) -> None:
        intent_path = tmp_path / "intents" / "feat.ic"
        intent_path.parent.mkdir(parents=True, exist_ok=True)
        intent_path.write_text("---\nname: feat\n---\noriginal")

        features = {
            "feat": FeatureNode(
                path="feat",
                intents=[IntentFile(name="feat", body="original", source_path=intent_path)],
            ),
        }
        project = _make_project(features=features, intent_dir=tmp_path / "intents")
        builder, agent, vc, sm = _make_builder(tmp_path, project=project)

        # Small delay so build timestamp is after file mtime
        time.sleep(0.05)

        opts = BuildOptions(output_dir=str(tmp_path / "output"))
        builder.build(opts)

        outdated = builder.detect_outdated()
        assert "feat" not in outdated

    def test_detect_outdated_does_not_modify_state(self, tmp_path: Path) -> None:
        builder, agent, vc, sm = _make_builder(tmp_path)
        opts = BuildOptions(output_dir=str(tmp_path / "output"))
        builder.build(opts)

        statuses_before = {t: s for t, s in sm.list_targets()}
        builder.detect_outdated()
        statuses_after = {t: s for t, s in sm.list_targets()}

        assert statuses_before == statuses_after


# ---------------------------------------------------------------------------
# Build options tests
# ---------------------------------------------------------------------------


class TestBuildOptions:
    def test_defaults(self) -> None:
        opts = BuildOptions()
        assert opts.target == ""
        assert opts.force is False
        assert opts.dry_run is False
        assert opts.output_dir == ""
        assert opts.profile_override == ""
        assert opts.implementation == ""

    def test_custom_values(self) -> None:
        opts = BuildOptions(
            target="feat",
            force=True,
            dry_run=True,
            output_dir="/tmp/out",
            profile_override="custom",
            implementation="alt",
        )
        assert opts.target == "feat"
        assert opts.force is True
        assert opts.implementation == "alt"


# ---------------------------------------------------------------------------
# Progress logging tests
# ---------------------------------------------------------------------------


class TestProgressLogging:
    def test_log_callback_receives_messages(self, tmp_path: Path) -> None:
        messages: list[str] = []
        proj = _make_project(features={
            "single": FeatureNode(
                path="single",
                intents=[IntentFile(name="single", body="test")],
            ),
        })
        agent = MockAgent()
        vc = MockVersionControl()
        prof = _make_profile()
        backend = SQLiteBackend(tmp_path, "out")
        sm = StateManager(tmp_path, "out", backend=backend)

        builder = Builder(
            project=proj,
            state_manager=sm,
            version_control=vc,
            agent_profile=prof,
            create_agent=lambda _: agent,
            log=messages.append,
        )

        opts = BuildOptions(output_dir=str(tmp_path / "output"))
        builder.build(opts)

        assert len(messages) > 0
        assert any("single" in m for m in messages)

    def test_no_log_callback_is_safe(self, tmp_path: Path) -> None:
        builder, agent, vc, sm = _make_builder(tmp_path)
        opts = BuildOptions(output_dir=str(tmp_path / "output"))
        # Should not raise
        results, error = builder.build(opts)
        assert error is None
