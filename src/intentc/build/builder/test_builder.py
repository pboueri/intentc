"""Tests for the builder module."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from intentc.build.agents.mock_agent import MockAgent
from intentc.build.agents.models import (
    AgentError,
    AgentProfile,
    BuildContext,
    BuildResponse,
    ValidationResponse,
)
from intentc.build.builder.builder import Builder, BuildOptions
from intentc.build.state import StateManager, VersionControl
from intentc.build.storage.backend import (
    BuildResult,
    BuildStep,
    GenerationStatus,
    StorageBackend,
    TargetStatus,
)
from intentc.core.models import IntentFile, ProjectIntent, ValidationFile, Validation, ValidationType, Severity
from intentc.core.project import FeatureNode, Project


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class StubVersionControl(VersionControl):
    """In-memory version control for tests."""

    def __init__(self) -> None:
        self.commits: list[tuple[str, str]] = []  # (id, message)
        self._counter = 0
        self.restores: list[str] = []

    def checkpoint(self, message: str) -> str:
        self._counter += 1
        commit_id = f"abc{self._counter:04d}"
        self.commits.append((commit_id, message))
        return commit_id

    def diff(self, from_id: str, to_id: str) -> str:
        return f"diff {from_id}..{to_id}"

    def restore(self, commit_id: str) -> None:
        self.restores.append(commit_id)

    def log(self, target: str | None = None) -> list[str]:
        return [cid for cid, msg in self.commits if target is None or target in msg]


def _make_project(
    features: dict[str, list[str]] | None = None,
    with_validations: bool = False,
) -> Project:
    """Build a small project for testing.

    features: mapping of feature_path -> list of dependency paths.
    """
    features = features or {"feat_a": [], "feat_b": ["feat_a"]}
    nodes: dict[str, FeatureNode] = {}
    for path, deps in features.items():
        intents = [IntentFile(name=path, depends_on=deps, body=f"Intent for {path}")]
        validations: list[ValidationFile] = []
        if with_validations:
            validations = [
                ValidationFile(
                    target=path,
                    validations=[
                        Validation(
                            name=f"{path}-check",
                            type=ValidationType.AGENT_VALIDATION,
                            severity=Severity.ERROR,
                        )
                    ],
                )
            ]
        nodes[path] = FeatureNode(path=path, intents=intents, validations=validations)
    return Project(
        project_intent=ProjectIntent(name="test-project", body="A test project"),
        features=nodes,
    )


def _make_builder(
    project: Project | None = None,
    mock_agent: MockAgent | None = None,
    vc: VersionControl | None = None,
    tmp: Path | None = None,
) -> tuple[Builder, MockAgent, StubVersionControl, Path]:
    """Construct a Builder with sensible test defaults."""
    tmp = tmp or Path(tempfile.mkdtemp())
    project = project or _make_project()
    agent = mock_agent or MockAgent()
    version_control = vc or StubVersionControl()
    state_manager = StateManager(base_dir=tmp, output_dir="out")
    profile = AgentProfile(name="test", provider="mock", retries=3)
    builder = Builder(
        project=project,
        state_manager=state_manager,
        version_control=version_control,
        agent_profile=profile,
        create_agent=lambda _prof: agent,
    )
    return builder, agent, version_control, tmp


# ---------------------------------------------------------------------------
# Build pipeline tests
# ---------------------------------------------------------------------------


class TestBuildPipeline:
    """Verify the core build pipeline."""

    def test_build_single_target_success(self, tmp_path: Path) -> None:
        project = _make_project({"feat_a": []})
        builder, agent, vc, _ = _make_builder(project=project, tmp=tmp_path)

        results, err = builder.build(BuildOptions(output_dir=str(tmp_path / "out")))

        assert err is None
        assert len(results) == 1
        assert results[0].target == "feat_a"
        assert results[0].status == "success"
        assert results[0].generation_id
        assert results[0].commit_id
        assert len(results[0].steps) >= 3  # resolve_deps, build, checkpoint

    def test_build_respects_topological_order(self, tmp_path: Path) -> None:
        project = _make_project({"a": [], "b": ["a"], "c": ["b"]})
        builder, agent, vc, _ = _make_builder(project=project, tmp=tmp_path)

        results, err = builder.build(BuildOptions(output_dir=str(tmp_path / "out")))

        assert err is None
        targets = [r.target for r in results]
        assert targets == ["a", "b", "c"]

    def test_build_steps_have_timing(self, tmp_path: Path) -> None:
        project = _make_project({"feat_a": []})
        builder, agent, vc, _ = _make_builder(project=project, tmp=tmp_path)

        results, err = builder.build(BuildOptions(output_dir=str(tmp_path / "out")))

        assert err is None
        for step in results[0].steps:
            assert step.duration_secs >= 0
            assert step.phase in ("resolve_deps", "build", "validate", "checkpoint")

    def test_build_records_generation_id_across_targets(self, tmp_path: Path) -> None:
        project = _make_project({"a": [], "b": ["a"]})
        builder, agent, vc, _ = _make_builder(project=project, tmp=tmp_path)

        results, err = builder.build(BuildOptions(output_dir=str(tmp_path / "out")))

        assert err is None
        assert len(results) == 2
        # Same generation ID across targets
        assert results[0].generation_id == results[1].generation_id
        # UUID format
        assert len(results[0].generation_id) == 36

    def test_build_empty_set_returns_empty(self, tmp_path: Path) -> None:
        project = _make_project({"feat_a": []})
        builder, agent, vc, _ = _make_builder(project=project, tmp=tmp_path)

        # Build once
        builder.build(BuildOptions(output_dir=str(tmp_path / "out")))
        # Build again — nothing to do
        results, err = builder.build(BuildOptions(output_dir=str(tmp_path / "out")))

        assert err is None
        assert results == []

    def test_build_force_rebuilds(self, tmp_path: Path) -> None:
        project = _make_project({"feat_a": []})
        builder, agent, vc, _ = _make_builder(project=project, tmp=tmp_path)

        builder.build(BuildOptions(output_dir=str(tmp_path / "out")))
        results, err = builder.build(
            BuildOptions(output_dir=str(tmp_path / "out"), force=True)
        )

        assert err is None
        assert len(results) == 1
        assert results[0].status == "success"

    def test_build_target_with_ancestors(self, tmp_path: Path) -> None:
        project = _make_project({"a": [], "b": ["a"], "c": ["b"]})
        builder, agent, vc, _ = _make_builder(project=project, tmp=tmp_path)

        results, err = builder.build(
            BuildOptions(target="c", output_dir=str(tmp_path / "out"))
        )

        assert err is None
        targets = [r.target for r in results]
        assert "a" in targets
        assert "b" in targets
        assert "c" in targets

    def test_build_checkpoint_after_success(self, tmp_path: Path) -> None:
        project = _make_project({"feat_a": []})
        builder, agent, vc, _ = _make_builder(project=project, tmp=tmp_path)

        results, err = builder.build(BuildOptions(output_dir=str(tmp_path / "out")))

        assert err is None
        assert len(vc.commits) == 1
        assert "feat_a" in vc.commits[0][1]

    def test_dry_run_no_side_effects(self, tmp_path: Path) -> None:
        project = _make_project({"feat_a": []})
        builder, agent, vc, _ = _make_builder(project=project, tmp=tmp_path)

        results, err = builder.build(
            BuildOptions(output_dir=str(tmp_path / "out"), dry_run=True)
        )

        assert err is None
        assert len(results) == 1
        # No agent calls, no commits
        assert len(agent.calls) == 0
        assert len(vc.commits) == 0


# ---------------------------------------------------------------------------
# Failure and retry tests
# ---------------------------------------------------------------------------


class TestBuildFailure:
    """Verify failure handling and retries."""

    def test_agent_error_retries_then_fails(self, tmp_path: Path) -> None:
        project = _make_project({"feat_a": []})
        agent = MockAgent()
        call_count = 0

        # Make build always raise AgentError
        original_build = agent.build

        def _failing_build(ctx: BuildContext) -> BuildResponse:
            nonlocal call_count
            call_count += 1
            raise AgentError("boom")

        agent.build = _failing_build

        builder, _, vc, _ = _make_builder(
            project=project, mock_agent=agent, tmp=tmp_path
        )

        results, err = builder.build(BuildOptions(output_dir=str(tmp_path / "out")))

        assert err is not None
        assert "feat_a" in str(err)
        assert len(results) == 1
        assert results[0].status == "failed"
        # retries=3 means 3 total attempts
        assert call_count == 3

    def test_failure_stops_dag_walk(self, tmp_path: Path) -> None:
        project = _make_project({"a": [], "b": ["a"]})
        agent = MockAgent()

        def _failing_build(ctx: BuildContext) -> BuildResponse:
            raise AgentError("crash")

        agent.build = _failing_build

        builder, _, vc, _ = _make_builder(
            project=project, mock_agent=agent, tmp=tmp_path
        )

        results, err = builder.build(BuildOptions(output_dir=str(tmp_path / "out")))

        assert err is not None
        # Only 'a' attempted — 'b' never reached
        assert len(results) == 1
        assert results[0].target == "a"

    def test_failed_target_not_checkpointed(self, tmp_path: Path) -> None:
        project = _make_project({"feat_a": []})
        agent = MockAgent()
        agent.build = lambda ctx: (_ for _ in ()).throw(AgentError("fail"))

        builder, _, vc, _ = _make_builder(
            project=project, mock_agent=agent, tmp=tmp_path
        )

        builder.build(BuildOptions(output_dir=str(tmp_path / "out")))

        assert len(vc.commits) == 0

    def test_previous_errors_passed_on_retry(self, tmp_path: Path) -> None:
        project = _make_project({"feat_a": []})
        agent = MockAgent()
        call_count = 0
        seen_errors: list[list[str]] = []

        def _retry_build(ctx: BuildContext) -> BuildResponse:
            nonlocal call_count
            call_count += 1
            seen_errors.append(list(ctx.previous_errors))
            if call_count < 3:
                raise AgentError(f"error-{call_count}")
            return BuildResponse(status="success", summary="ok")

        agent.build = _retry_build

        builder, _, vc, _ = _make_builder(
            project=project, mock_agent=agent, tmp=tmp_path
        )

        results, err = builder.build(BuildOptions(output_dir=str(tmp_path / "out")))

        assert err is None
        assert call_count == 3
        assert seen_errors[0] == []  # First attempt, no prior errors
        assert "Agent error: error-1" in seen_errors[1][0]
        assert "Agent error: error-2" in seen_errors[2][1]


# ---------------------------------------------------------------------------
# Clean tests
# ---------------------------------------------------------------------------


class TestClean:
    """Verify the clean workflow."""

    def test_clean_restores_and_resets(self, tmp_path: Path) -> None:
        project = _make_project({"feat_a": []})
        builder, agent, vc, _ = _make_builder(project=project, tmp=tmp_path)
        out = str(tmp_path / "out")

        builder.build(BuildOptions(output_dir=out))
        builder.clean("feat_a", out)

        # Restore was called with the commit ID
        assert len(vc.restores) == 1
        # Status reset to pending
        assert builder._state_manager.get_status("feat_a") == TargetStatus.PENDING

    def test_clean_marks_descendants_outdated(self, tmp_path: Path) -> None:
        project = _make_project({"a": [], "b": ["a"]})
        builder, agent, vc, _ = _make_builder(project=project, tmp=tmp_path)
        out = str(tmp_path / "out")

        builder.build(BuildOptions(output_dir=out))
        builder.clean("a", out)

        assert builder._state_manager.get_status("a") == TargetStatus.PENDING
        assert builder._state_manager.get_status("b") == TargetStatus.OUTDATED

    def test_clean_no_result_is_noop(self, tmp_path: Path) -> None:
        project = _make_project({"feat_a": []})
        builder, agent, vc, _ = _make_builder(project=project, tmp=tmp_path)

        # No build happened — clean should be a no-op
        builder.clean("feat_a", str(tmp_path / "out"))
        assert len(vc.restores) == 0

    def test_clean_all_resets_state(self, tmp_path: Path) -> None:
        project = _make_project({"a": [], "b": ["a"]})
        builder, agent, vc, _ = _make_builder(project=project, tmp=tmp_path)
        out = str(tmp_path / "out")

        builder.build(BuildOptions(output_dir=out))
        builder.clean_all(out)

        assert builder._state_manager.get_status("a") == TargetStatus.PENDING
        assert builder._state_manager.get_status("b") == TargetStatus.PENDING


# ---------------------------------------------------------------------------
# Validate tests
# ---------------------------------------------------------------------------


class TestValidate:
    """Verify the standalone validate workflow."""

    def test_validate_returns_result(self, tmp_path: Path) -> None:
        project = _make_project({"feat_a": []}, with_validations=True)
        builder, agent, vc, _ = _make_builder(project=project, tmp=tmp_path)
        # Use cli provider so ValidationSuite can create an agent
        builder._agent_profile = AgentProfile(name="test", provider="cli", command="echo ok", retries=3)

        result = builder.validate("feat_a", str(tmp_path / "out"))

        assert isinstance(result, object)
        assert hasattr(result, "target")
        assert hasattr(result, "passed")

    def test_validate_does_not_modify_state(self, tmp_path: Path) -> None:
        project = _make_project({"feat_a": []}, with_validations=True)
        builder, agent, vc, _ = _make_builder(project=project, tmp=tmp_path)
        builder._agent_profile = AgentProfile(name="test", provider="cli", command="echo ok", retries=3)

        builder.validate("feat_a", str(tmp_path / "out"))

        # No commits, no status changes
        assert len(vc.commits) == 0
        assert builder._state_manager.get_status("feat_a") == TargetStatus.PENDING

    def test_validate_project(self, tmp_path: Path) -> None:
        project = _make_project({"a": [], "b": ["a"]}, with_validations=True)
        builder, agent, vc, _ = _make_builder(project=project, tmp=tmp_path)
        builder._agent_profile = AgentProfile(name="test", provider="cli", command="echo ok", retries=3)

        results = builder.validate(None, str(tmp_path / "out"))

        assert isinstance(results, list)
        assert len(results) == 2


# ---------------------------------------------------------------------------
# Invalidation tests
# ---------------------------------------------------------------------------


class TestDetectOutdated:
    """Verify the invalidation detection."""

    def test_detect_outdated_with_modified_intent(self, tmp_path: Path) -> None:
        # Create a real intent file on disk
        intent_path = tmp_path / "intent" / "feat_a" / "feat_a.ic"
        intent_path.parent.mkdir(parents=True)
        intent_path.write_text("name: feat_a\n---\nOriginal intent")

        intent = IntentFile(name="feat_a", body="Original intent", source_path=intent_path)
        node = FeatureNode(path="feat_a", intents=[intent])
        project = Project(
            project_intent=ProjectIntent(name="test"),
            features={"feat_a": node},
        )
        builder, agent, vc, _ = _make_builder(project=project, tmp=tmp_path)

        # Build the target
        results, err = builder.build(BuildOptions(output_dir=str(tmp_path / "out")))
        assert err is None

        # Touch the intent file to make it newer
        time.sleep(0.05)
        intent_path.write_text("name: feat_a\n---\nUpdated intent")

        outdated = builder.detect_outdated()
        assert "feat_a" in outdated

    def test_detect_outdated_no_changes(self, tmp_path: Path) -> None:
        project = _make_project({"feat_a": []})
        builder, agent, vc, _ = _make_builder(project=project, tmp=tmp_path)

        builder.build(BuildOptions(output_dir=str(tmp_path / "out")))

        # No source_path set so nothing to compare — not outdated
        outdated = builder.detect_outdated()
        assert outdated == []

    def test_detect_outdated_only_built_targets(self, tmp_path: Path) -> None:
        project = _make_project({"a": [], "b": ["a"]})
        builder, agent, vc, _ = _make_builder(project=project, tmp=tmp_path)

        # Only build 'a'
        builder.build(BuildOptions(target="a", output_dir=str(tmp_path / "out")))

        # 'b' is pending, not built — should not be in outdated
        outdated = builder.detect_outdated()
        assert "b" not in outdated


# ---------------------------------------------------------------------------
# BuildOptions tests
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
            target="core/project",
            force=True,
            dry_run=True,
            output_dir="/tmp/out",
            profile_override="fast",
            implementation="default",
        )
        assert opts.target == "core/project"
        assert opts.force is True
