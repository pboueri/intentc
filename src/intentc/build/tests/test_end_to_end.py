"""End-to-end integration test for the full `intentc build` pipeline.

Exercises real project loading, real state/storage, and a real (in-memory)
version control implementation against a mocked agent, all inside a fresh
temporary directory with its own git repository. Validates orchestration
(DAG order, state transitions, retries, invalidation) rather than agent
output.
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path
from typing import Optional

import yaml

from intentc.build.agents import AgentError, AgentProfile, MockAgent
from intentc.build.builder.builder import Builder, BuildOptions
from intentc.build.state import StateManager, TargetStatus, VersionControl
from intentc.core import (
    Implementation,
    IntentFile,
    ProjectIntent,
    Severity,
    Validation,
    ValidationFile,
    ValidationType,
    load_project,
    write_intent_file,
    write_validation_file,
)


# ---------------------------------------------------------------------------
# MockVersionControl
# ---------------------------------------------------------------------------


class MockVersionControl(VersionControl):
    """In-memory `VersionControl`: assigns incrementing fake commit ids and
    never touches disk or a real git repo."""

    def __init__(self) -> None:
        self._commits: list[str] = []

    def checkpoint(self, message: str) -> str:
        commit_id = f"commit-{len(self._commits) + 1}"
        self._commits.append(commit_id)
        return commit_id

    def diff(self, from_id: str, to_id: str) -> str:
        return f"diff {from_id}..{to_id}"

    def restore(self, commit_id: str) -> None:
        return None

    def log(self, target: Optional[str] = None) -> list[str]:
        return list(self._commits)

    def has_changes(self) -> bool:
        return True

    def snapshot(self, message: str, ref_name: str) -> str:
        commit_id = f"snapshot-{len(self._commits) + 1}"
        self._commits.append(commit_id)
        return commit_id

    def materialize(self, commit_id: str, dest_dir) -> None:
        return None


# ---------------------------------------------------------------------------
# Conditional-failure agent for the DAG-stop test
# ---------------------------------------------------------------------------


class ConditionalFailAgent(MockAgent):
    """A `MockAgent` that raises `AgentError` when building any target named
    in `fail_targets`."""

    def __init__(self, fail_targets: set[str]) -> None:
        super().__init__()
        self.fail_targets = fail_targets

    def build(self, ctx):
        self.build_calls.append(ctx)
        if ctx.feature_path in self.fail_targets:
            raise AgentError(f"mock failure building '{ctx.feature_path}'")
        return self.build_response


# ---------------------------------------------------------------------------
# Project / builder setup
# ---------------------------------------------------------------------------


def _init_git_repo(repo_dir: Path) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo_dir, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-q", "-m", "initial commit"], cwd=repo_dir, check=True
    )


def _write_config(tmp_dir: Path) -> None:
    config_dir = tmp_dir / ".intentc"
    config_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "default_profile": "default",
        "profiles": {"default": {"provider": "cli", "command": "true"}},
    }
    (config_dir / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def _write_project(tmp_dir: Path, store_validations: Optional[list[Validation]] = None) -> Path:
    """Write a minimal intentc project on disk: models <- store <- api."""
    intent_dir = tmp_dir / "intent"

    project_intent = ProjectIntent(name="demo", body="# Demo Project\n\nA demo project for end-to-end tests.")
    project_intent.source_path = write_intent_file(project_intent, intent_dir / "project.ic")

    implementation = Implementation(
        name="default", body="# Default Implementation\n\nPython 3.11, uv, output to src/."
    )
    implementation.source_path = write_intent_file(
        implementation, intent_dir / "implementations" / "default.ic"
    )

    models_intent = IntentFile(name="models", depends_on=[], body="# Models\n\nDefine the data models.")
    models_intent.source_path = write_intent_file(models_intent, intent_dir / "models" / "models.ic")

    store_intent = IntentFile(
        name="store", depends_on=["models"], body="# Store\n\nDefine the storage layer."
    )
    store_intent.source_path = write_intent_file(store_intent, intent_dir / "store" / "store.ic")

    if store_validations:
        store_vf = ValidationFile(target="store", version=1, validations=store_validations)
        store_vf.source_path = write_validation_file(store_vf, intent_dir / "store" / "validation.icv")

    api_intent = IntentFile(name="api", depends_on=["store"], body="# API\n\nDefine the API layer.")
    api_intent.source_path = write_intent_file(api_intent, intent_dir / "api" / "api.ic")

    api_vf = ValidationFile(
        target="api",
        version=1,
        validations=[
            Validation(
                name="api-check",
                type=ValidationType.AGENT_VALIDATION.value,
                severity=Severity.ERROR,
                args={"rubric": "Check that the API layer was built correctly and completely."},
            )
        ],
    )
    api_vf.source_path = write_validation_file(api_vf, intent_dir / "api" / "api.icv")

    _write_config(tmp_dir)
    return intent_dir


def _setup(
    tmp_path: Path,
    agent: Optional[MockAgent] = None,
    retries: int = 3,
    store_validations: Optional[list[Validation]] = None,
):
    _init_git_repo(tmp_path)
    intent_dir = _write_project(tmp_path, store_validations=store_validations)
    project = load_project(intent_dir)

    output_dir = tmp_path / "src"
    state_manager = StateManager(base_dir=tmp_path, output_dir="src")
    vc = MockVersionControl()
    profile = AgentProfile(name="default", provider="cli", retries=retries)
    agent = agent if agent is not None else MockAgent()

    builder = Builder(project=project, state_manager=state_manager, version_control=vc, agent_profile=profile)
    builder._create_agent = lambda p: agent

    return builder, project, output_dir, intent_dir, agent


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_full_build_pipeline(self, tmp_path):
        builder, _project, output_dir, _intent_dir, agent = _setup(tmp_path)

        results, error = builder.build(BuildOptions(output_dir=str(output_dir)))

        assert error is None
        assert len(results) == 3
        assert [r.target for r in results] == ["models", "store", "api"]
        assert all(r.status == TargetStatus.BUILT for r in results)

        generation_ids = {r.generation_id for r in results}
        assert len(generation_ids) == 1
        uuid.UUID(next(iter(generation_ids)))  # does not raise

        for result in results:
            phases = [step.phase for step in result.steps]
            assert "resolve_deps" in phases
            assert "build" in phases
            assert "checkpoint" in phases

        api_result = next(r for r in results if r.target == "api")
        assert "validate" in [step.phase for step in api_result.steps]

        assert len(agent.build_calls) == 3
        for target in ("models", "store", "api"):
            assert builder._state_manager.get_status(target) == TargetStatus.BUILT

    def test_idempotent_rebuild(self, tmp_path):
        builder, _project, output_dir, _intent_dir, agent = _setup(tmp_path)
        builder.build(BuildOptions(output_dir=str(output_dir)))
        assert len(agent.build_calls) == 3

        results, error = builder.build(BuildOptions(output_dir=str(output_dir)))

        assert results == []
        assert error is None
        assert len(agent.build_calls) == 3
        for target in ("models", "store", "api"):
            assert builder._state_manager.get_status(target) == TargetStatus.BUILT

    def test_force_rebuild(self, tmp_path):
        builder, _project, output_dir, _intent_dir, agent = _setup(tmp_path)
        builder.build(BuildOptions(output_dir=str(output_dir)))
        assert len(agent.build_calls) == 3

        results, error = builder.build(BuildOptions(output_dir=str(output_dir), force=True))

        assert error is None
        assert len(results) == 3
        assert all(r.status == TargetStatus.BUILT for r in results)
        assert len(agent.build_calls) == 6

    def test_targeted_build_with_ancestors(self, tmp_path):
        builder, _project, output_dir, _intent_dir, agent = _setup(tmp_path)

        results, error = builder.build(BuildOptions(target="api", output_dir=str(output_dir)))

        assert error is None
        assert [r.target for r in results] == ["models", "store", "api"]
        assert all(r.status == TargetStatus.BUILT for r in results)
        assert len(agent.build_calls) == 3

    def test_partial_build_then_continue(self, tmp_path):
        builder, _project, output_dir, _intent_dir, agent = _setup(tmp_path)

        results1, error1 = builder.build(BuildOptions(target="models", output_dir=str(output_dir)))
        assert error1 is None
        assert [r.target for r in results1] == ["models"]

        results2, error2 = builder.build(BuildOptions(output_dir=str(output_dir)))
        assert error2 is None
        assert {r.target for r in results2} == {"store", "api"}

        assert len(agent.build_calls) == 3
        assert sum(1 for ctx in agent.build_calls if ctx.feature_path == "models") == 1

    def test_build_failure_stops_dag(self, tmp_path):
        agent = ConditionalFailAgent(fail_targets={"store"})
        builder, _project, output_dir, _intent_dir, agent = _setup(tmp_path, agent=agent, retries=1)

        results, error = builder.build(BuildOptions(output_dir=str(output_dir)))

        assert error is not None
        assert [r.target for r in results] == ["models", "store"]
        assert results[0].status == TargetStatus.BUILT
        assert results[1].status == TargetStatus.FAILED
        assert not any(ctx.feature_path == "api" for ctx in agent.build_calls)
        assert builder._state_manager.get_status("store") == TargetStatus.FAILED

    def test_edited_intent_triggers_rebuild(self, tmp_path):
        builder, _project, output_dir, intent_dir, agent = _setup(tmp_path)
        builder.build(BuildOptions(output_dir=str(output_dir)))
        assert len(agent.build_calls) == 3
        assert builder._state_manager.get_status("store") == TargetStatus.BUILT

        store_ic = intent_dir / "store" / "store.ic"
        with store_ic.open("a", encoding="utf-8") as f:
            f.write("\nAppended detail about the store feature.\n")

        builder._project = load_project(intent_dir)

        changed = builder.refresh_outdated()
        assert "store" in changed
        assert builder._state_manager.get_status("store") == TargetStatus.OUTDATED

        results, error = builder.build(BuildOptions(output_dir=str(output_dir)))

        assert error is None
        assert {r.target for r in results} == {"store", "api"}
        assert len(agent.build_calls) == 5
        assert sum(1 for ctx in agent.build_calls if ctx.feature_path == "models") == 1

    def test_deterministic_validation_failure_retries_then_fails(self, tmp_path):
        store_validations = [
            Validation(
                name="store-cmd-check",
                type=ValidationType.COMMAND_VALIDATION.value,
                severity=Severity.ERROR,
                args={"command": "exit 1"},
            )
        ]
        builder, _project, output_dir, _intent_dir, agent = _setup(
            tmp_path, retries=2, store_validations=store_validations
        )

        results, error = builder.build(BuildOptions(output_dir=str(output_dir)))

        assert error is not None
        assert [r.target for r in results] == ["models", "store"]

        store_result = results[1]
        assert store_result.status == TargetStatus.FAILED
        assert store_result.attempts == 2

        store_calls = [ctx for ctx in agent.build_calls if ctx.feature_path == "store"]
        assert len(store_calls) == 2
        assert store_calls[1].previous_errors

        validation_results = builder._state_manager.backend.get_validation_results("store")
        assert any("exit 1" in (r.get("reason") or "") for r in validation_results)

        assert not any(ctx.feature_path == "api" for ctx in agent.build_calls)
        assert builder._state_manager.get_status("store") == TargetStatus.FAILED
