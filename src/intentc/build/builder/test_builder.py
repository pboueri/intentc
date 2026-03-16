"""Tests for the builder — the core workflow engine of intentc."""

from __future__ import annotations

import os
import time
import uuid
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
from intentc.build.state import (
    BuildResult,
    BuildStep,
    StateManager,
    TargetStatus,
    VersionControl,
)
from intentc.build.validations import ValidationSuite, ValidationSuiteResult
from intentc.core.project import FeatureNode, Project
from intentc.core.types import (
    IntentFile,
    ProjectIntent,
    Severity,
    Validation,
    ValidationFile,
    ValidationType,
)

from intentc.build.builder.builder import Builder, BuildOptions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockVersionControl(VersionControl):
    """In-memory version control for testing."""

    def __init__(self) -> None:
        self.checkpoints: list[tuple[str, str]] = []  # (message, id)
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


def _simple_project() -> Project:
    """Single feature 'a' with no deps."""
    return Project(
        project_intent=ProjectIntent(name="test", body="Test project"),
        features={
            "a": FeatureNode(
                path="a",
                intents=[IntentFile(name="a", body="Feature A")],
            ),
        },
    )


def _chain_project() -> Project:
    """Linear chain: a -> b -> c."""
    return Project(
        project_intent=ProjectIntent(name="test", body="Test project"),
        features={
            "a": FeatureNode(path="a", intents=[IntentFile(name="a", body="A")]),
            "b": FeatureNode(
                path="b",
                intents=[IntentFile(name="b", body="B", depends_on=["a"])],
            ),
            "c": FeatureNode(
                path="c",
                intents=[IntentFile(name="c", body="C", depends_on=["b"])],
            ),
        },
    )


def _diamond_project() -> Project:
    """Diamond: a -> b,c -> d."""
    return Project(
        project_intent=ProjectIntent(name="test", body="Test project"),
        features={
            "a": FeatureNode(path="a", intents=[IntentFile(name="a", body="A")]),
            "b": FeatureNode(
                path="b",
                intents=[IntentFile(name="b", body="B", depends_on=["a"])],
            ),
            "c": FeatureNode(
                path="c",
                intents=[IntentFile(name="c", body="C", depends_on=["a"])],
            ),
            "d": FeatureNode(
                path="d",
                intents=[IntentFile(name="d", body="D", depends_on=["b", "c"])],
            ),
        },
    )


def _project_with_validations() -> Project:
    """Single feature 'a' with a validation."""
    return Project(
        project_intent=ProjectIntent(name="test", body="Test project"),
        features={
            "a": FeatureNode(
                path="a",
                intents=[IntentFile(name="a", body="Feature A")],
                validations=[
                    ValidationFile(
                        target="a",
                        validations=[
                            Validation(
                                name="check-a",
                                type=ValidationType.AGENT_VALIDATION,
                                severity=Severity.ERROR,
                                args={"rubric": "Check feature A"},
                            ),
                        ],
                    ),
                ],
            ),
        },
    )


def _make_builder(
    project: Project,
    tmp_path: Path,
    *,
    agent: MockAgent | None = None,
    vc: MockVersionControl | None = None,
) -> tuple[Builder, MockAgent, MockVersionControl, StateManager]:
    """Create a builder with mock dependencies."""
    mock_agent = agent or MockAgent()
    mock_vc = vc or MockVersionControl()
    profile = AgentProfile(name="mock", provider="cli")
    sm = StateManager(tmp_path, "out")
    output_dir = tmp_path / "out"
    output_dir.mkdir(exist_ok=True)
    builder = Builder(
        project=project,
        state_manager=sm,
        version_control=mock_vc,
        agent_profile=profile,
    )
    # Inject the mock agent so we don't need a real agent factory
    builder._create_agent = lambda profile: mock_agent  # type: ignore[attr-defined]
    return builder, mock_agent, mock_vc, sm


# ---------------------------------------------------------------------------
# BuildOptions
# ---------------------------------------------------------------------------


class TestBuildOptions:
    def test_defaults(self):
        opts = BuildOptions(output_dir="out")
        assert opts.target == ""
        assert opts.force is False
        assert opts.dry_run is False
        assert opts.output_dir == "out"
        assert opts.profile_override == ""

    def test_all_fields(self):
        opts = BuildOptions(
            target="feat/a",
            force=True,
            dry_run=True,
            output_dir="/tmp/out",
            profile_override="custom",
        )
        assert opts.target == "feat/a"
        assert opts.force is True
        assert opts.dry_run is True
        assert opts.output_dir == "/tmp/out"
        assert opts.profile_override == "custom"


# ---------------------------------------------------------------------------
# Builder construction
# ---------------------------------------------------------------------------


class TestBuilderConstruction:
    def test_injected_dependencies(self, tmp_path: Path):
        project = _simple_project()
        profile = AgentProfile(name="test", provider="cli")
        sm = StateManager(tmp_path, "out")
        vc = MockVersionControl()
        builder = Builder(
            project=project,
            state_manager=sm,
            version_control=vc,
            agent_profile=profile,
        )
        assert builder.project is project
        assert builder.state_manager is sm
        assert builder.version_control is vc
        assert builder.agent_profile is profile


# ---------------------------------------------------------------------------
# Build pipeline
# ---------------------------------------------------------------------------


class TestBuildPipeline:
    def test_build_single_target(self, tmp_path: Path):
        """Build a single feature, verify result structure."""
        project = _simple_project()
        builder, agent, vc, sm = _make_builder(project, tmp_path)
        opts = BuildOptions(target="a", output_dir=str(tmp_path / "out"))

        results, err = builder.build(opts)

        assert err is None
        assert len(results) == 1
        assert results[0].target == "a"
        assert results[0].status == TargetStatus.BUILT
        assert results[0].generation_id  # non-empty UUID
        assert results[0].total_duration > timedelta(0)
        assert results[0].timestamp is not None
        assert len(agent.build_calls) == 1

    def test_build_all_pending(self, tmp_path: Path):
        """Build all pending targets when no target specified."""
        project = _chain_project()
        builder, agent, vc, sm = _make_builder(project, tmp_path)
        opts = BuildOptions(output_dir=str(tmp_path / "out"))

        results, err = builder.build(opts)

        assert err is None
        assert len(results) == 3
        assert len(agent.build_calls) == 3

    def test_build_topological_order(self, tmp_path: Path):
        """Targets are built in dependency-first order."""
        project = _chain_project()
        builder, agent, vc, sm = _make_builder(project, tmp_path)
        opts = BuildOptions(output_dir=str(tmp_path / "out"))

        results, err = builder.build(opts)

        assert err is None
        built_order = [r.target for r in results]
        assert built_order.index("a") < built_order.index("b")
        assert built_order.index("b") < built_order.index("c")

    def test_build_diamond_topological_order(self, tmp_path: Path):
        """Diamond DAG is built respecting all dependencies."""
        project = _diamond_project()
        builder, agent, vc, sm = _make_builder(project, tmp_path)
        opts = BuildOptions(output_dir=str(tmp_path / "out"))

        results, err = builder.build(opts)

        assert err is None
        built_order = [r.target for r in results]
        assert built_order.index("a") < built_order.index("b")
        assert built_order.index("a") < built_order.index("c")
        assert built_order.index("b") < built_order.index("d")
        assert built_order.index("c") < built_order.index("d")

    def test_generation_id_shared(self, tmp_path: Path):
        """All results in a single build share the same generation ID."""
        project = _chain_project()
        builder, agent, vc, sm = _make_builder(project, tmp_path)
        opts = BuildOptions(output_dir=str(tmp_path / "out"))

        results, err = builder.build(opts)

        gen_ids = {r.generation_id for r in results}
        assert len(gen_ids) == 1
        # Verify it's a valid UUID
        uuid.UUID(gen_ids.pop())

    def test_build_empty_set_returns_early(self, tmp_path: Path):
        """If everything is already built, return empty results."""
        project = _simple_project()
        builder, agent, vc, sm = _make_builder(project, tmp_path)
        opts = BuildOptions(target="a", output_dir=str(tmp_path / "out"))

        # Build once
        builder.build(opts)
        # Build again — already built
        results, err = builder.build(opts)

        assert err is None
        assert len(results) == 0

    def test_skip_already_built(self, tmp_path: Path):
        """Targets with status 'built' are skipped unless force=True."""
        project = _chain_project()
        builder, agent, vc, sm = _make_builder(project, tmp_path)
        opts = BuildOptions(output_dir=str(tmp_path / "out"))

        # Build all
        builder.build(opts)
        agent.build_calls.clear()

        # Build again — all built, should skip
        results, err = builder.build(opts)
        assert len(results) == 0
        assert len(agent.build_calls) == 0

    def test_force_rebuilds(self, tmp_path: Path):
        """force=True rebuilds even already-built targets."""
        project = _simple_project()
        builder, agent, vc, sm = _make_builder(project, tmp_path)
        opts = BuildOptions(target="a", output_dir=str(tmp_path / "out"))

        # Build once
        builder.build(opts)
        agent.build_calls.clear()

        # Force rebuild
        force_opts = BuildOptions(target="a", output_dir=str(tmp_path / "out"), force=True)
        results, err = builder.build(force_opts)

        assert err is None
        assert len(results) == 1
        assert len(agent.build_calls) == 1

    def test_dry_run_no_side_effects(self, tmp_path: Path):
        """dry_run returns the build set without executing anything."""
        project = _chain_project()
        builder, agent, vc, sm = _make_builder(project, tmp_path)
        opts = BuildOptions(output_dir=str(tmp_path / "out"), dry_run=True)

        results, err = builder.build(opts)

        assert err is None
        assert len(results) == 3
        # No agent calls
        assert len(agent.build_calls) == 0
        # No checkpoints
        assert len(vc.checkpoints) == 0
        # State unchanged
        assert sm.get_status("a") == TargetStatus.PENDING

    def test_build_target_with_unbuilt_ancestors(self, tmp_path: Path):
        """Building a target collects its unbuilt ancestors."""
        project = _chain_project()
        builder, agent, vc, sm = _make_builder(project, tmp_path)
        opts = BuildOptions(target="c", output_dir=str(tmp_path / "out"))

        results, err = builder.build(opts)

        assert err is None
        # Should build a, b, c (ancestors + target)
        built_targets = [r.target for r in results]
        assert "a" in built_targets
        assert "b" in built_targets
        assert "c" in built_targets

    def test_build_target_skips_already_built_ancestors(self, tmp_path: Path):
        """When targeting c, already-built ancestors are skipped."""
        project = _chain_project()
        builder, agent, vc, sm = _make_builder(project, tmp_path)

        # Build 'a' first
        builder.build(BuildOptions(target="a", output_dir=str(tmp_path / "out")))
        agent.build_calls.clear()

        # Now build 'c' — should only build b and c
        results, err = builder.build(BuildOptions(target="c", output_dir=str(tmp_path / "out")))

        assert err is None
        built_targets = [r.target for r in results]
        assert "a" not in built_targets
        assert "b" in built_targets
        assert "c" in built_targets

    def test_creates_output_dir(self, tmp_path: Path):
        """Output directory is created if it doesn't exist."""
        project = _simple_project()
        builder, agent, vc, sm = _make_builder(project, tmp_path)
        out = tmp_path / "new_output"
        opts = BuildOptions(target="a", output_dir=str(out))

        builder.build(opts)

        assert out.is_dir()


# ---------------------------------------------------------------------------
# Build steps
# ---------------------------------------------------------------------------


class TestBuildSteps:
    def test_steps_recorded(self, tmp_path: Path):
        """Each target's result has timed build steps."""
        project = _simple_project()
        builder, agent, vc, sm = _make_builder(project, tmp_path)
        opts = BuildOptions(target="a", output_dir=str(tmp_path / "out"))

        results, err = builder.build(opts)

        steps = results[0].steps
        phases = [s.phase for s in steps]
        assert "resolve_deps" in phases
        assert "build" in phases
        assert "checkpoint" in phases
        for step in steps:
            assert step.status == "success"
            assert step.duration >= timedelta(0)

    def test_steps_include_validate_when_validations_exist(self, tmp_path: Path):
        """validate step appears when the target has validations."""
        project = _project_with_validations()
        builder, agent, vc, sm = _make_builder(project, tmp_path)
        opts = BuildOptions(target="a", output_dir=str(tmp_path / "out"))

        results, err = builder.build(opts)

        phases = [s.phase for s in results[0].steps]
        assert "validate" in phases

    def test_steps_skip_validate_when_no_validations(self, tmp_path: Path):
        """validate step is skipped when no validations are defined."""
        project = _simple_project()
        builder, agent, vc, sm = _make_builder(project, tmp_path)
        opts = BuildOptions(target="a", output_dir=str(tmp_path / "out"))

        results, err = builder.build(opts)

        phases = [s.phase for s in results[0].steps]
        assert "validate" not in phases


# ---------------------------------------------------------------------------
# Checkpoint (atomicity)
# ---------------------------------------------------------------------------


class TestCheckpoint:
    def test_checkpoint_after_successful_build(self, tmp_path: Path):
        """A successful build creates a checkpoint and records the commit ID."""
        project = _simple_project()
        builder, agent, vc, sm = _make_builder(project, tmp_path)
        opts = BuildOptions(target="a", output_dir=str(tmp_path / "out"))

        results, err = builder.build(opts)

        assert len(vc.checkpoints) == 1
        assert results[0].commit_id == "commit-0"

    def test_checkpoint_per_target(self, tmp_path: Path):
        """Each target gets its own checkpoint."""
        project = _chain_project()
        builder, agent, vc, sm = _make_builder(project, tmp_path)
        opts = BuildOptions(output_dir=str(tmp_path / "out"))

        results, err = builder.build(opts)

        assert len(vc.checkpoints) == 3

    def test_no_checkpoint_on_failure(self, tmp_path: Path):
        """Failed targets do not get checkpointed."""
        project = _simple_project()
        failing_agent = MockAgent(
            build_response=BuildResponse(status="failure", summary="crash"),
        )
        failing_agent.build = MagicMock(side_effect=AgentError("boom"))
        builder, _, vc, sm = _make_builder(project, tmp_path, agent=failing_agent)
        opts = BuildOptions(target="a", output_dir=str(tmp_path / "out"))

        results, err = builder.build(opts)

        assert len(vc.checkpoints) == 0


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


class TestStatePersistence:
    def test_result_saved_to_state_manager(self, tmp_path: Path):
        """Build results are persisted via state manager."""
        project = _simple_project()
        builder, agent, vc, sm = _make_builder(project, tmp_path)
        opts = BuildOptions(target="a", output_dir=str(tmp_path / "out"))

        builder.build(opts)

        assert sm.get_status("a") == TargetStatus.BUILT
        result = sm.get_build_result("a")
        assert result is not None
        assert result.target == "a"

    def test_failed_target_saved_as_failed(self, tmp_path: Path):
        """Failed targets are saved with FAILED status."""
        project = _simple_project()
        failing_agent = MockAgent()
        failing_agent.build = MagicMock(side_effect=AgentError("boom"))
        builder, _, vc, sm = _make_builder(project, tmp_path, agent=failing_agent)
        opts = BuildOptions(target="a", output_dir=str(tmp_path / "out"))

        results, err = builder.build(opts)

        assert sm.get_status("a") == TargetStatus.FAILED


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


class TestFailureHandling:
    def test_agent_error_fails_target(self, tmp_path: Path):
        """AgentError marks the target as failed."""
        project = _simple_project()
        failing_agent = MockAgent()
        failing_agent.build = MagicMock(side_effect=AgentError("boom"))
        builder, _, vc, sm = _make_builder(project, tmp_path, agent=failing_agent)
        opts = BuildOptions(target="a", output_dir=str(tmp_path / "out"))

        results, err = builder.build(opts)

        assert err is not None
        assert len(results) == 1
        assert results[0].status == TargetStatus.FAILED

    def test_dag_walk_stops_on_failure(self, tmp_path: Path):
        """When a target fails, the DAG walk stops immediately."""
        project = _chain_project()
        targets_seen: list[str] = []

        def failing_on_b(ctx):
            targets_seen.append(ctx.intent.name)
            if ctx.intent.name == "b":
                raise AgentError("b failed")
            return BuildResponse(status="success", summary="ok")

        agent = MockAgent()
        agent.build = MagicMock(side_effect=failing_on_b)
        # Use retries=1 so the failure isn't retried away
        profile = AgentProfile(name="mock", provider="cli", retries=1)
        sm = StateManager(tmp_path, "out")
        vc = MockVersionControl()
        (tmp_path / "out").mkdir(exist_ok=True)
        builder = Builder(
            project=project,
            state_manager=sm,
            version_control=vc,
            agent_profile=profile,
        )
        builder._create_agent = lambda p: agent  # type: ignore[attr-defined]
        opts = BuildOptions(output_dir=str(tmp_path / "out"))

        results, err = builder.build(opts)

        assert err is not None
        # a built successfully, b failed, c never attempted
        built_targets = [r.target for r in results]
        assert "a" in built_targets
        assert "b" in built_targets
        assert "c" not in built_targets

    def test_validation_failure_fails_target(self, tmp_path: Path):
        """Validation failure marks the target as failed, no retry."""
        project = _project_with_validations()
        failing_val_agent = MockAgent(
            validation_response=ValidationResponse(
                name="check-a", status="fail", reason="bad output"
            ),
        )
        builder, _, vc, sm = _make_builder(project, tmp_path, agent=failing_val_agent)
        opts = BuildOptions(target="a", output_dir=str(tmp_path / "out"))

        results, err = builder.build(opts)

        assert err is not None
        assert results[0].status == TargetStatus.FAILED
        # Build was called exactly once — validation failures are not retried
        assert len(failing_val_agent.build_calls) == 1


# ---------------------------------------------------------------------------
# Retries
# ---------------------------------------------------------------------------


class TestRetries:
    def test_retries_on_agent_error(self, tmp_path: Path):
        """Agent errors trigger retries up to AgentProfile.retries."""
        project = _simple_project()
        call_count = 0

        def succeed_on_third(ctx):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise AgentError("transient")
            return BuildResponse(status="success", summary="ok")

        agent = MockAgent()
        agent.build = MagicMock(side_effect=succeed_on_third)
        profile = AgentProfile(name="mock", provider="cli", retries=3)
        sm = StateManager(tmp_path, "out")
        vc = MockVersionControl()
        (tmp_path / "out").mkdir(exist_ok=True)
        builder = Builder(
            project=project,
            state_manager=sm,
            version_control=vc,
            agent_profile=profile,
        )
        builder._create_agent = lambda p: agent  # type: ignore[attr-defined]
        opts = BuildOptions(target="a", output_dir=str(tmp_path / "out"))

        results, err = builder.build(opts)

        assert err is None
        assert results[0].status == TargetStatus.BUILT
        assert call_count == 3

    def test_retries_exhausted(self, tmp_path: Path):
        """If all retries fail, the target is marked failed."""
        project = _simple_project()
        agent = MockAgent()
        agent.build = MagicMock(side_effect=AgentError("always fails"))
        profile = AgentProfile(name="mock", provider="cli", retries=2)
        sm = StateManager(tmp_path, "out")
        vc = MockVersionControl()
        (tmp_path / "out").mkdir(exist_ok=True)
        builder = Builder(
            project=project,
            state_manager=sm,
            version_control=vc,
            agent_profile=profile,
        )
        builder._create_agent = lambda p: agent  # type: ignore[attr-defined]
        opts = BuildOptions(target="a", output_dir=str(tmp_path / "out"))

        results, err = builder.build(opts)

        assert err is not None
        assert results[0].status == TargetStatus.FAILED

    def test_validation_failures_not_retried(self, tmp_path: Path):
        """Validation failures do not trigger retries."""
        project = _project_with_validations()
        agent = MockAgent(
            validation_response=ValidationResponse(
                name="check-a", status="fail", reason="bad"
            ),
        )
        builder, _, vc, sm = _make_builder(project, tmp_path, agent=agent)
        opts = BuildOptions(target="a", output_dir=str(tmp_path / "out"))

        results, err = builder.build(opts)

        # Build called once, validation failed, no retry
        assert len(agent.build_calls) == 1


# ---------------------------------------------------------------------------
# Build context
# ---------------------------------------------------------------------------


class TestBuildContext:
    def test_context_has_dependency_names(self, tmp_path: Path):
        """resolve_deps passes dependency names to the agent."""
        project = _chain_project()
        builder, agent, vc, sm = _make_builder(project, tmp_path)
        opts = BuildOptions(output_dir=str(tmp_path / "out"))

        builder.build(opts)

        # 'b' depends on 'a'
        b_ctx = [c for c in agent.build_calls if c.intent.name == "b"][0]
        assert "a" in b_ctx.dependency_names

    def test_context_has_generation_id(self, tmp_path: Path):
        """BuildContext includes the generation ID."""
        project = _simple_project()
        builder, agent, vc, sm = _make_builder(project, tmp_path)
        opts = BuildOptions(target="a", output_dir=str(tmp_path / "out"))

        results, _ = builder.build(opts)

        ctx = agent.build_calls[0]
        assert ctx.generation_id == results[0].generation_id

    def test_context_has_project_intent(self, tmp_path: Path):
        """BuildContext includes the project intent."""
        project = _simple_project()
        builder, agent, vc, sm = _make_builder(project, tmp_path)
        opts = BuildOptions(target="a", output_dir=str(tmp_path / "out"))

        builder.build(opts)

        ctx = agent.build_calls[0]
        assert ctx.project_intent.name == "test"


# ---------------------------------------------------------------------------
# Outdated / pending targets
# ---------------------------------------------------------------------------


class TestBuildSetSelection:
    def test_builds_pending_targets(self, tmp_path: Path):
        """Targets with status 'pending' are included in the build set."""
        project = _simple_project()
        builder, agent, vc, sm = _make_builder(project, tmp_path)
        opts = BuildOptions(output_dir=str(tmp_path / "out"))

        results, err = builder.build(opts)

        assert len(results) == 1

    def test_builds_outdated_targets(self, tmp_path: Path):
        """Targets with status 'outdated' are included in the build set."""
        project = _simple_project()
        builder, agent, vc, sm = _make_builder(project, tmp_path)
        opts = BuildOptions(output_dir=str(tmp_path / "out"))

        # Build then mark outdated
        builder.build(opts)
        sm.set_status("a", TargetStatus.OUTDATED)
        agent.build_calls.clear()

        results, err = builder.build(opts)
        assert len(results) == 1
        assert results[0].target == "a"

    def test_force_includes_all_targets(self, tmp_path: Path):
        """force=True includes all targets regardless of status."""
        project = _chain_project()
        builder, agent, vc, sm = _make_builder(project, tmp_path)
        opts = BuildOptions(output_dir=str(tmp_path / "out"))

        # Build all
        builder.build(opts)
        agent.build_calls.clear()

        # Force — all 3 rebuilt
        force_opts = BuildOptions(output_dir=str(tmp_path / "out"), force=True)
        results, err = builder.build(force_opts)
        assert len(results) == 3


# ---------------------------------------------------------------------------
# Profile override
# ---------------------------------------------------------------------------


class TestProfileOverride:
    def test_profile_override_takes_precedence(self, tmp_path: Path):
        """profileOverride in BuildOptions overrides the builder's agentProfile."""
        project = _simple_project()
        profile = AgentProfile(name="default", provider="cli")
        sm = StateManager(tmp_path, "out")
        vc = MockVersionControl()
        (tmp_path / "out").mkdir(exist_ok=True)

        created_profiles: list[AgentProfile] = []

        def track_create(p):
            created_profiles.append(p)
            return MockAgent()

        builder = Builder(
            project=project,
            state_manager=sm,
            version_control=vc,
            agent_profile=profile,
        )
        builder._create_agent = track_create  # type: ignore[attr-defined]

        override = AgentProfile(name="override", provider="cli")
        opts = BuildOptions(
            target="a",
            output_dir=str(tmp_path / "out"),
            profile_override="override",
        )
        # Register the override profile so the builder can find it
        builder._named_profiles = {"override": override}  # type: ignore[attr-defined]

        results, err = builder.build(opts)

        # The builder should have used the override profile
        assert any(p.name == "override" for p in created_profiles)


# ---------------------------------------------------------------------------
# Clean
# ---------------------------------------------------------------------------


class TestClean:
    def test_clean_resets_to_pending(self, tmp_path: Path):
        """Clean resets a target's state to pending."""
        project = _simple_project()
        builder, agent, vc, sm = _make_builder(project, tmp_path)
        opts = BuildOptions(target="a", output_dir=str(tmp_path / "out"))

        builder.build(opts)
        assert sm.get_status("a") == TargetStatus.BUILT

        builder.clean("a", str(tmp_path / "out"))

        assert sm.get_status("a") == TargetStatus.PENDING

    def test_clean_creates_revert_commit(self, tmp_path: Path):
        """Clean creates a revert commit via version control."""
        project = _simple_project()
        builder, agent, vc, sm = _make_builder(project, tmp_path)
        opts = BuildOptions(target="a", output_dir=str(tmp_path / "out"))

        builder.build(opts)
        initial_checkpoints = len(vc.checkpoints)

        builder.clean("a", str(tmp_path / "out"))

        # A revert checkpoint was created
        assert len(vc.restores) == 1

    def test_clean_marks_descendants_outdated(self, tmp_path: Path):
        """Clean marks all descendants as outdated."""
        project = _chain_project()
        builder, agent, vc, sm = _make_builder(project, tmp_path)
        opts = BuildOptions(output_dir=str(tmp_path / "out"))

        builder.build(opts)
        builder.clean("a", str(tmp_path / "out"))

        assert sm.get_status("a") == TargetStatus.PENDING
        assert sm.get_status("b") == TargetStatus.OUTDATED
        assert sm.get_status("c") == TargetStatus.OUTDATED

    def test_clean_no_prior_build_returns_early(self, tmp_path: Path):
        """Clean on a never-built target does nothing."""
        project = _simple_project()
        builder, agent, vc, sm = _make_builder(project, tmp_path)

        # Should not raise
        builder.clean("a", str(tmp_path / "out"))

        assert len(vc.restores) == 0
        assert sm.get_status("a") == TargetStatus.PENDING


# ---------------------------------------------------------------------------
# CleanAll
# ---------------------------------------------------------------------------


class TestCleanAll:
    def test_clean_all_resets_state(self, tmp_path: Path):
        """CleanAll resets all target state."""
        project = _chain_project()
        builder, agent, vc, sm = _make_builder(project, tmp_path)
        opts = BuildOptions(output_dir=str(tmp_path / "out"))

        builder.build(opts)
        builder.clean_all(str(tmp_path / "out"))

        assert sm.list_targets() == []

    def test_clean_all_does_not_modify_files(self, tmp_path: Path):
        """CleanAll resets state but doesn't modify files."""
        project = _simple_project()
        builder, agent, vc, sm = _make_builder(project, tmp_path)
        opts = BuildOptions(target="a", output_dir=str(tmp_path / "out"))

        builder.build(opts)
        builder.clean_all(str(tmp_path / "out"))

        # No restores — version control history preserved
        assert len(vc.restores) == 0


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


class TestValidate:
    def test_validate_single_target(self, tmp_path: Path):
        """Validate runs validations for a target without modifying state."""
        # Use a project without validations so the suite passes trivially
        project = _simple_project()
        builder, agent, vc, sm = _make_builder(project, tmp_path)
        opts = BuildOptions(target="a", output_dir=str(tmp_path / "out"))

        # Build first
        builder.build(opts)
        initial_status = sm.get_status("a")

        result = builder.validate("a", str(tmp_path / "out"))

        assert isinstance(result, ValidationSuiteResult)
        assert result.target == "a"
        # State unchanged
        assert sm.get_status("a") == initial_status

    def test_validate_project(self, tmp_path: Path):
        """Validate with no target validates the entire project."""
        project = _project_with_validations()
        builder, agent, vc, sm = _make_builder(project, tmp_path)

        results = builder.validate(None, str(tmp_path / "out"))

        assert isinstance(results, list)

    def test_validate_does_not_modify_state(self, tmp_path: Path):
        """Validate never writes to the state manager."""
        project = _project_with_validations()
        builder, agent, vc, sm = _make_builder(project, tmp_path)

        builder.validate("a", str(tmp_path / "out"))

        # Feature was never built, status still pending
        assert sm.get_status("a") == TargetStatus.PENDING


# ---------------------------------------------------------------------------
# DetectOutdated
# ---------------------------------------------------------------------------


class TestDetectOutdated:
    def test_detect_outdated_finds_stale_targets(self, tmp_path: Path):
        """Targets whose source files are newer than their build timestamp are outdated."""
        # Create a project with intent files on disk
        intent_dir = tmp_path / "intent"
        intent_dir.mkdir()
        (intent_dir / "project.ic").write_text("---\nname: test\n---\n# Test")

        feat_dir = intent_dir / "a"
        feat_dir.mkdir()
        ic_file = feat_dir / "a.ic"
        ic_file.write_text("---\nname: a\n---\n# A")

        project = _simple_project()
        project.intent_dir = intent_dir
        builder, agent, vc, sm = _make_builder(project, tmp_path)
        opts = BuildOptions(target="a", output_dir=str(tmp_path / "out"))

        builder.build(opts)

        # Now touch the .ic file to make it newer
        time.sleep(0.05)
        ic_file.write_text("---\nname: a\n---\n# A updated")

        outdated = builder.detect_outdated()

        assert "a" in outdated

    def test_detect_outdated_returns_empty_when_fresh(self, tmp_path: Path):
        """Returns empty list when all targets are up to date."""
        intent_dir = tmp_path / "intent"
        intent_dir.mkdir()
        (intent_dir / "project.ic").write_text("---\nname: test\n---\n# Test")

        feat_dir = intent_dir / "a"
        feat_dir.mkdir()
        (feat_dir / "a.ic").write_text("---\nname: a\n---\n# A")

        project = _simple_project()
        project.intent_dir = intent_dir
        builder, agent, vc, sm = _make_builder(project, tmp_path)
        opts = BuildOptions(target="a", output_dir=str(tmp_path / "out"))

        builder.build(opts)

        outdated = builder.detect_outdated()
        assert outdated == []

    def test_detect_outdated_only_checks_built_targets(self, tmp_path: Path):
        """Only targets with status 'built' are checked for staleness."""
        project = _simple_project()
        project.intent_dir = tmp_path / "intent"
        builder, agent, vc, sm = _make_builder(project, tmp_path)

        # Never built — should not appear as outdated
        outdated = builder.detect_outdated()
        assert outdated == []

    def test_detect_outdated_checks_icv_files(self, tmp_path: Path):
        """Detects outdated targets when .icv files are modified."""
        intent_dir = tmp_path / "intent"
        intent_dir.mkdir()
        (intent_dir / "project.ic").write_text("---\nname: test\n---\n# Test")

        feat_dir = intent_dir / "a"
        feat_dir.mkdir()
        (feat_dir / "a.ic").write_text("---\nname: a\n---\n# A")
        icv_file = feat_dir / "a.icv"
        icv_file.write_text("target: a\nvalidations: []")

        project = _simple_project()
        project.intent_dir = intent_dir
        builder, agent, vc, sm = _make_builder(project, tmp_path)
        opts = BuildOptions(target="a", output_dir=str(tmp_path / "out"))

        builder.build(opts)

        # Touch the .icv file
        time.sleep(0.05)
        icv_file.write_text("target: a\nvalidations:\n  - name: new")

        outdated = builder.detect_outdated()
        assert "a" in outdated

    def test_detect_outdated_does_not_modify_state(self, tmp_path: Path):
        """DetectOutdated is read-only — does not update state."""
        intent_dir = tmp_path / "intent"
        intent_dir.mkdir()
        (intent_dir / "project.ic").write_text("---\nname: test\n---\n# Test")

        feat_dir = intent_dir / "a"
        feat_dir.mkdir()
        ic_file = feat_dir / "a.ic"
        ic_file.write_text("---\nname: a\n---\n# A")

        project = _simple_project()
        project.intent_dir = intent_dir
        builder, agent, vc, sm = _make_builder(project, tmp_path)
        opts = BuildOptions(target="a", output_dir=str(tmp_path / "out"))

        builder.build(opts)

        # Touch file
        time.sleep(0.05)
        ic_file.write_text("---\nname: a\n---\n# Updated")

        builder.detect_outdated()

        # Status should still be BUILT, not modified
        assert sm.get_status("a") == TargetStatus.BUILT
