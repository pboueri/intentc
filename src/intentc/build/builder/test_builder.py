"""Tests for the Builder module."""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

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
    BuildStep,
    StateManager,
    TargetStatus,
    VersionControl,
)
from intentc.build.storage import SQLiteBackend
from intentc.core.project import FeatureNode, Project
from intentc.core.types import (
    IntentFile,
    ProjectIntent,
    Validation,
    ValidationFile,
    Severity,
)


# ---------------------------------------------------------------------------
# Mock VersionControl
# ---------------------------------------------------------------------------


class MockVersionControl(VersionControl):
    """In-memory version control for tests."""

    def __init__(self) -> None:
        self.checkpoints: list[tuple[str, str]] = []  # (id, message)
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


def _make_project(
    features: dict[str, list[str]] | None = None,
    validations: dict[str, list[ValidationFile]] | None = None,
) -> Project:
    """Create a simple project with given feature dependencies.

    features is a dict of feature_path -> list of dependency paths.
    """
    if features is None:
        features = {"a": []}

    nodes: dict[str, FeatureNode] = {}
    for path, deps in features.items():
        intent = IntentFile(name=path, depends_on=deps, body=f"Feature {path}")
        vf_list = (validations or {}).get(path, [])
        nodes[path] = FeatureNode(path=path, intents=[intent], validations=vf_list)

    return Project(
        project_intent=ProjectIntent(name="test-project", body="Test project"),
        features=nodes,
    )


def _make_builder(
    project: Project,
    tmp_dir: Path,
    mock_agent: MockAgent | None = None,
) -> tuple[Builder, MockVersionControl, MockAgent]:
    """Wire up a Builder with mock dependencies."""
    output_dir = str(tmp_dir / "output")
    os.makedirs(output_dir, exist_ok=True)

    backend = SQLiteBackend(tmp_dir, "output")
    state_manager = StateManager(tmp_dir, "output", backend=backend)
    vc = MockVersionControl()

    profile = AgentProfile(name="test-agent", provider="cli", retries=3)

    builder = Builder(
        project=project,
        state_manager=state_manager,
        version_control=vc,
        agent_profile=profile,
    )

    agent = mock_agent or MockAgent()
    builder._create_agent = lambda _profile: agent

    return builder, vc, agent


# ---------------------------------------------------------------------------
# Tests — Build Pipeline
# ---------------------------------------------------------------------------


class TestBuildPipeline:
    def test_build_single_target(self, tmp_path: Path) -> None:
        project = _make_project({"feat": []})
        builder, vc, agent = _make_builder(project, tmp_path)

        results, err = builder.build(BuildOptions(output_dir=str(tmp_path / "output")))

        assert err is None
        assert len(results) == 1
        assert results[0].target == "feat"
        assert results[0].status == TargetStatus.BUILT
        assert results[0].generation_id != ""
        assert len(agent.build_calls) == 1

    def test_build_topological_order(self, tmp_path: Path) -> None:
        project = _make_project({"a": [], "b": ["a"], "c": ["b"]})
        builder, vc, agent = _make_builder(project, tmp_path)

        results, err = builder.build(BuildOptions(output_dir=str(tmp_path / "output")))

        assert err is None
        targets = [r.target for r in results]
        assert targets == ["a", "b", "c"]

    def test_build_steps_recorded(self, tmp_path: Path) -> None:
        project = _make_project({"feat": []})
        builder, vc, agent = _make_builder(project, tmp_path)

        results, err = builder.build(BuildOptions(output_dir=str(tmp_path / "output")))

        assert err is None
        steps = results[0].steps
        phases = [s.phase for s in steps]
        assert "resolve_deps" in phases
        assert "build" in phases
        assert "checkpoint" in phases
        for step in steps:
            assert step.status == "success"
            assert step.duration_secs >= 0

    def test_build_step_timing(self, tmp_path: Path) -> None:
        project = _make_project({"feat": []})
        builder, vc, agent = _make_builder(project, tmp_path)

        results, err = builder.build(BuildOptions(output_dir=str(tmp_path / "output")))

        assert err is None
        result = results[0]
        assert result.total_duration_secs >= 0
        assert result.timestamp is not None

    def test_generation_id_shared(self, tmp_path: Path) -> None:
        project = _make_project({"a": [], "b": ["a"]})
        builder, vc, agent = _make_builder(project, tmp_path)

        results, err = builder.build(BuildOptions(output_dir=str(tmp_path / "output")))

        assert err is None
        assert len(results) == 2
        assert results[0].generation_id == results[1].generation_id
        assert results[0].generation_id != ""

    def test_checkpoint_after_build(self, tmp_path: Path) -> None:
        project = _make_project({"feat": []})
        builder, vc, agent = _make_builder(project, tmp_path)

        results, err = builder.build(BuildOptions(output_dir=str(tmp_path / "output")))

        assert err is None
        assert len(vc.checkpoints) == 1
        assert results[0].commit_id == vc.checkpoints[0][0]

    def test_build_specific_target_with_ancestors(self, tmp_path: Path) -> None:
        project = _make_project({"a": [], "b": ["a"], "c": ["b"]})
        builder, vc, agent = _make_builder(project, tmp_path)

        results, err = builder.build(
            BuildOptions(target="b", output_dir=str(tmp_path / "output"))
        )

        assert err is None
        targets = [r.target for r in results]
        assert "a" in targets
        assert "b" in targets
        assert "c" not in targets

    def test_skip_already_built(self, tmp_path: Path) -> None:
        project = _make_project({"a": [], "b": ["a"]})
        builder, vc, agent = _make_builder(project, tmp_path)

        # Build all
        builder.build(BuildOptions(output_dir=str(tmp_path / "output")))
        agent.build_calls.clear()

        # Build again — should skip
        results, err = builder.build(BuildOptions(output_dir=str(tmp_path / "output")))

        assert err is None
        assert len(results) == 0
        assert len(agent.build_calls) == 0

    def test_force_rebuilds(self, tmp_path: Path) -> None:
        project = _make_project({"feat": []})
        builder, vc, agent = _make_builder(project, tmp_path)

        builder.build(BuildOptions(output_dir=str(tmp_path / "output")))
        agent.build_calls.clear()

        results, err = builder.build(
            BuildOptions(output_dir=str(tmp_path / "output"), force=True)
        )

        assert err is None
        assert len(results) == 1
        assert len(agent.build_calls) == 1

    def test_dry_run(self, tmp_path: Path) -> None:
        project = _make_project({"feat": []})
        builder, vc, agent = _make_builder(project, tmp_path)

        results, err = builder.build(
            BuildOptions(output_dir=str(tmp_path / "output"), dry_run=True)
        )

        assert err is None
        assert len(results) == 1
        assert len(agent.build_calls) == 0
        assert len(vc.checkpoints) == 0

    def test_empty_build_set(self, tmp_path: Path) -> None:
        project = _make_project({"feat": []})
        builder, vc, agent = _make_builder(project, tmp_path)

        # Build first
        builder.build(BuildOptions(output_dir=str(tmp_path / "output")))

        # Second build with no pending targets
        results, err = builder.build(BuildOptions(output_dir=str(tmp_path / "output")))

        assert err is None
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Tests — Failure Handling
# ---------------------------------------------------------------------------


class TestBuildFailure:
    def test_agent_error_stops_dag(self, tmp_path: Path) -> None:
        project = _make_project({"a": [], "b": ["a"]})
        failing_agent = MockAgent(
            build_response=BuildResponse(status="success", summary="ok")
        )
        builder, vc, _ = _make_builder(project, tmp_path, mock_agent=failing_agent)

        call_count = 0
        original_build = failing_agent.build

        def fail_on_second(ctx: BuildContext) -> BuildResponse:
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise AgentError("boom")
            return original_build(ctx)

        failing_agent.build = fail_on_second  # type: ignore[assignment]

        results, err = builder.build(BuildOptions(output_dir=str(tmp_path / "output")))

        assert err is not None
        assert "b" in str(err)
        # a succeeded, b failed
        assert results[0].status == TargetStatus.BUILT
        assert results[1].status == TargetStatus.FAILED

    def test_agent_error_retries(self, tmp_path: Path) -> None:
        project = _make_project({"feat": []})
        agent = MockAgent()
        builder, vc, _ = _make_builder(project, tmp_path, mock_agent=agent)

        call_count = 0
        original_build = agent.build

        def fail_then_succeed(ctx: BuildContext) -> BuildResponse:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise AgentError("transient")
            return original_build(ctx)

        agent.build = fail_then_succeed  # type: ignore[assignment]

        results, err = builder.build(BuildOptions(output_dir=str(tmp_path / "output")))

        assert err is None
        assert results[0].status == TargetStatus.BUILT
        assert call_count == 3  # retries=3 means 3 total attempts

    def test_failed_target_not_checkpointed(self, tmp_path: Path) -> None:
        project = _make_project({"feat": []})
        agent = MockAgent()
        builder, vc, _ = _make_builder(project, tmp_path, mock_agent=agent)

        def always_fail(ctx: BuildContext) -> BuildResponse:
            raise AgentError("fail")

        agent.build = always_fail  # type: ignore[assignment]

        results, err = builder.build(BuildOptions(output_dir=str(tmp_path / "output")))

        assert err is not None
        assert len(vc.checkpoints) == 0

    def test_failed_does_not_rollback(self, tmp_path: Path) -> None:
        """Failed builds leave files on disk — no rollback."""
        project = _make_project({"feat": []})
        agent = MockAgent()
        builder, vc, _ = _make_builder(project, tmp_path, mock_agent=agent)

        def always_fail(ctx: BuildContext) -> BuildResponse:
            raise AgentError("fail")

        agent.build = always_fail  # type: ignore[assignment]

        results, err = builder.build(BuildOptions(output_dir=str(tmp_path / "output")))

        assert err is not None
        assert len(vc.restores) == 0  # No rollback


# ---------------------------------------------------------------------------
# Tests — Clean
# ---------------------------------------------------------------------------


class TestClean:
    def test_clean_resets_state(self, tmp_path: Path) -> None:
        project = _make_project({"feat": []})
        builder, vc, agent = _make_builder(project, tmp_path)

        builder.build(BuildOptions(output_dir=str(tmp_path / "output")))
        builder.clean("feat", str(tmp_path / "output"))

        status = builder._state_manager.get_status("feat")
        assert status == TargetStatus.PENDING

    def test_clean_restores_via_vc(self, tmp_path: Path) -> None:
        project = _make_project({"feat": []})
        builder, vc, agent = _make_builder(project, tmp_path)

        builder.build(BuildOptions(output_dir=str(tmp_path / "output")))
        builder.clean("feat", str(tmp_path / "output"))

        assert len(vc.restores) == 1

    def test_clean_marks_descendants_outdated(self, tmp_path: Path) -> None:
        project = _make_project({"a": [], "b": ["a"]})
        builder, vc, agent = _make_builder(project, tmp_path)

        builder.build(BuildOptions(output_dir=str(tmp_path / "output")))
        builder.clean("a", str(tmp_path / "output"))

        status_b = builder._state_manager.get_status("b")
        assert status_b == TargetStatus.OUTDATED

    def test_clean_no_result_is_noop(self, tmp_path: Path) -> None:
        project = _make_project({"feat": []})
        builder, vc, agent = _make_builder(project, tmp_path)

        builder.clean("feat", str(tmp_path / "output"))
        assert len(vc.restores) == 0

    def test_clean_all_resets_all_state(self, tmp_path: Path) -> None:
        project = _make_project({"a": [], "b": ["a"]})
        builder, vc, agent = _make_builder(project, tmp_path)

        builder.build(BuildOptions(output_dir=str(tmp_path / "output")))
        builder.clean_all(str(tmp_path / "output"))

        status_a = builder._state_manager.get_status("a")
        status_b = builder._state_manager.get_status("b")
        assert status_a == TargetStatus.PENDING
        assert status_b == TargetStatus.PENDING


# ---------------------------------------------------------------------------
# Tests — Validate
# ---------------------------------------------------------------------------


class TestValidate:
    def test_validate_returns_result(self, tmp_path: Path) -> None:
        project = _make_project({"feat": []})
        builder, vc, agent = _make_builder(project, tmp_path)

        result = builder.validate("feat", str(tmp_path / "output"))

        # Should return a ValidationSuiteResult, not modify state
        assert hasattr(result, "target")
        assert result.target == "feat"

    def test_validate_does_not_modify_state(self, tmp_path: Path) -> None:
        project = _make_project({"feat": []})
        builder, vc, agent = _make_builder(project, tmp_path)

        builder.validate("feat", str(tmp_path / "output"))

        status = builder._state_manager.get_status("feat")
        assert status == TargetStatus.PENDING

    def test_validate_project(self, tmp_path: Path) -> None:
        project = _make_project({"a": [], "b": ["a"]})
        builder, vc, agent = _make_builder(project, tmp_path)

        results = builder.validate("", str(tmp_path / "output"))

        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# Tests — Invalidation
# ---------------------------------------------------------------------------


class TestInvalidation:
    def test_detect_outdated_empty(self, tmp_path: Path) -> None:
        project = _make_project({"feat": []})
        builder, vc, agent = _make_builder(project, tmp_path)

        outdated = builder.detect_outdated()
        assert outdated == []

    def test_detect_outdated_with_newer_source(self, tmp_path: Path) -> None:
        # Create an intent file on disk
        intent_dir = tmp_path / "intent"
        feat_dir = intent_dir / "feat"
        feat_dir.mkdir(parents=True)
        ic_file = feat_dir / "feat.ic"
        ic_file.write_text("---\nname: feat\n---\nFeature body\n")

        intent = IntentFile(
            name="feat", body="Feature body", source_path=ic_file
        )
        node = FeatureNode(path="feat", intents=[intent])
        project = Project(
            project_intent=ProjectIntent(name="test", body="Test"),
            features={"feat": node},
            intent_dir=intent_dir,
        )

        builder, vc, agent = _make_builder(project, tmp_path)
        builder.build(BuildOptions(output_dir=str(tmp_path / "output")))

        # Touch the intent file to make it newer
        time.sleep(0.05)
        ic_file.write_text("---\nname: feat\n---\nUpdated body\n")

        outdated = builder.detect_outdated()
        assert "feat" in outdated

    def test_detect_outdated_does_not_modify_state(self, tmp_path: Path) -> None:
        project = _make_project({"feat": []})
        builder, vc, agent = _make_builder(project, tmp_path)
        builder.build(BuildOptions(output_dir=str(tmp_path / "output")))

        builder.detect_outdated()

        status = builder._state_manager.get_status("feat")
        assert status == TargetStatus.BUILT


# ---------------------------------------------------------------------------
# Tests — Profile Resolution
# ---------------------------------------------------------------------------


class TestProfileResolution:
    def test_profile_override(self, tmp_path: Path) -> None:
        project = _make_project({"feat": []})
        builder, vc, agent = _make_builder(project, tmp_path)

        profiles_seen: list[AgentProfile] = []
        original_create = builder._create_agent

        def capture_profile(profile: AgentProfile) -> MockAgent:
            profiles_seen.append(profile)
            return agent

        builder._create_agent = capture_profile

        builder.build(
            BuildOptions(
                output_dir=str(tmp_path / "output"),
                profile_override="custom-profile",
            )
        )

        assert len(profiles_seen) == 1
        assert profiles_seen[0].name == "custom-profile"
