"""Tests for intentc.build.builder."""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest

from intentc.build.agents import (
    Agent,
    AgentError,
    AgentProfile,
    BuildContext,
    BuildResponse,
    CLIAgent,
    DifferencingContext,
    DifferencingResponse,
    ValidationResponse,
)
from intentc.build.builder.builder import Builder, BuildOptions
from intentc.build.state import GitVersionControl, StateManager, TargetStatus
from intentc.build.storage import GenerationStatus
from intentc.core import (
    FeatureNode,
    Implementation,
    IntentFile,
    Project,
    ProjectIntent,
    Severity,
    Validation,
    ValidationFile,
    ValidationType,
    write_intent_file,
    write_validation_file,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _init_repo(repo_dir: Path) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo_dir, check=True)


def _make_feature(
    intent_dir: Path, path: str, depends_on: list[str], body: str, with_validation: bool = True
) -> FeatureNode:
    leaf = path.rsplit("/", 1)[-1]
    intent = IntentFile(name=leaf, depends_on=depends_on, body=body)
    intent.source_path = write_intent_file(intent, intent_dir / path / f"{leaf}.ic")

    validations: list[ValidationFile] = []
    if with_validation:
        vf = ValidationFile(
            target=path,
            version=1,
            validations=[
                Validation(
                    name=f"{leaf}-check",
                    type=ValidationType.AGENT_VALIDATION.value,
                    severity=Severity.ERROR,
                    args={"rubric": "Check the feature was built correctly and completely."},
                )
            ],
        )
        vf.source_path = write_validation_file(vf, intent_dir / path / "validation.icv")
        validations.append(vf)
    return FeatureNode(path=path, intents=[intent], validations=validations)


def make_project(tmp_path: Path, with_validation: bool = True) -> Project:
    """a <- b <- c: a chain of three features, each depending on the previous."""
    intent_dir = tmp_path / "intent"

    project_intent = ProjectIntent(name="demo", body="A demo project.")
    project_intent.source_path = write_intent_file(project_intent, intent_dir / "project.ic")

    default_impl = Implementation(name="default", body="Python 3.11, uv, pydantic.")
    default_impl.source_path = write_intent_file(default_impl, intent_dir / "implementations" / "default.ic")

    features = {
        "a": _make_feature(intent_dir, "a", [], "Build A.", with_validation),
        "b": _make_feature(intent_dir, "b", ["a"], "Build B, depends on A.", with_validation),
        "c": _make_feature(intent_dir, "c", ["b"], "Build C, depends on B.", with_validation),
    }

    return Project(
        project_intent=project_intent,
        implementations={"default": default_impl},
        assertions=[],
        features=features,
        intent_dir=intent_dir,
    )


def make_builder(
    tmp_path: Path,
    project: Project,
    create_agent,
    log=None,
    retries: int = 3,
) -> tuple[Builder, Path]:
    _init_repo(tmp_path)
    output_dir = tmp_path / "out"
    state_manager = StateManager(base_dir=tmp_path, output_dir="out")
    vc = GitVersionControl(tmp_path, output_dir="out")
    profile = AgentProfile(name="default-profile", provider="mock", retries=retries)
    builder = Builder(
        project=project,
        state_manager=state_manager,
        version_control=vc,
        agent_profile=profile,
        create_agent=create_agent,
        log=log,
    )
    return builder, output_dir


class ScriptedAgent(Agent):
    """A configurable agent for testing the builder's pipeline.

    `build_responses[target]` is a list of BuildResponse/AgentError items consumed
    in order for successive attempts against that target; once exhausted, a plain
    success response is synthesized. `validation_status[target]` fixes the status
    every validation entry returns for that target (default "pass").
    """

    def __init__(self) -> None:
        self.build_calls: list[BuildContext] = []
        self.validate_calls: list[tuple[BuildContext, Validation]] = []
        self.build_responses: dict[str, list] = {}
        self.validation_status: dict[str, str] = {}

    def build(self, ctx: BuildContext) -> BuildResponse:
        self.build_calls.append(ctx)
        target = ctx.feature_path
        queue = self.build_responses.get(target)
        if queue:
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            response = item
        else:
            response = BuildResponse(
                status="success", summary=f"built {target}", files_created=[f"{target}.py"], files_modified=[]
            )
        for filename in [*response.files_created, *response.files_modified]:
            file_path = Path(ctx.output_dir) / filename
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(f"# {target}\n", encoding="utf-8")
        return response

    def validate(self, ctx: BuildContext, validation: Validation) -> ValidationResponse:
        self.validate_calls.append((ctx, validation))
        # AgentValidationRunner builds its own BuildContext and does not carry
        # feature_path through, so key off the feature intent's name instead.
        status = self.validation_status.get(ctx.intent.name, "pass")
        return ValidationResponse(name=validation.name, status=status, reason="scripted")

    def difference(self, ctx: DifferencingContext) -> DifferencingResponse:
        raise NotImplementedError

    def plan(self, ctx: BuildContext) -> None:
        return None

    def init(self, project_name: str, intent_dir: str, prompt: str | None = None) -> None:
        return None

    def get_name(self) -> str:
        return "scripted"

    def get_type(self) -> str:
        return "scripted"


def make_factory(agent: Agent):
    calls: list[AgentProfile] = []

    def factory(profile: AgentProfile) -> Agent:
        calls.append(profile)
        return agent

    factory.calls = calls
    return factory


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_builder_uses_state_managers_backend(tmp_path):
    project = make_project(tmp_path)
    agent = ScriptedAgent()
    builder, _ = make_builder(tmp_path, project, make_factory(agent))
    assert builder._storage is builder._state_manager.backend


def test_default_create_agent_wraps_create_from_profile_with_builder_log(tmp_path):
    project = make_project(tmp_path)
    _init_repo(tmp_path)
    state_manager = StateManager(base_dir=tmp_path, output_dir="out")
    vc = GitVersionControl(tmp_path, output_dir="out")
    profile = AgentProfile(name="p", provider="cli")
    logs: list[str] = []
    builder = Builder(project, state_manager, vc, profile, log=logs.append)

    agent = builder._create_agent(profile)

    assert isinstance(agent, CLIAgent)
    assert agent.log is builder.log


# ---------------------------------------------------------------------------
# Build pipeline: happy path, ordering, generation id
# ---------------------------------------------------------------------------


def test_build_walks_dag_in_topological_order_with_shared_generation_id(tmp_path):
    project = make_project(tmp_path)
    agent = ScriptedAgent()
    builder, output_dir = make_builder(tmp_path, project, make_factory(agent))

    opts = BuildOptions(output_dir=str(output_dir))
    results, error = builder.build(opts)

    assert error is None
    assert [r.target for r in results] == ["a", "b", "c"]
    assert all(r.status == TargetStatus.BUILT for r in results)

    generation_ids = {r.generation_id for r in results}
    assert len(generation_ids) == 1
    uuid.UUID(next(iter(generation_ids)))  # does not raise

    for result in results:
        assert [step.phase for step in result.steps] == ["resolve_deps", "build", "validate", "checkpoint"]
        assert all(step.status == "success" for step in result.steps)
        assert result.commit_id
        assert result.attempts == 1

    assert [ctx.feature_path for ctx in agent.build_calls] == ["a", "b", "c"]
    assert agent.build_calls[1].dependency_names == ["a"]
    assert agent.build_calls[2].dependency_names == ["b"]

    for target in ("a", "b", "c"):
        assert builder._state_manager.get_status(target) == TargetStatus.BUILT


def test_build_creates_output_dir_and_records_generation(tmp_path):
    project = make_project(tmp_path)
    agent = ScriptedAgent()
    builder, output_dir = make_builder(tmp_path, project, make_factory(agent))
    assert not output_dir.exists()

    results, error = builder.build(BuildOptions(output_dir=str(output_dir)))

    assert error is None
    assert output_dir.is_dir()
    generation_id = results[0].generation_id
    generation = builder._storage.get_generation(generation_id)
    assert generation is not None
    assert generation["status"] == GenerationStatus.COMPLETED.value
    assert len(generation["logs"]) > 0


def test_next_targets_reflects_buildable_features(tmp_path):
    project = make_project(tmp_path)
    agent = ScriptedAgent()
    builder, output_dir = make_builder(tmp_path, project, make_factory(agent))

    assert builder.next_targets() == ["a"]

    builder.build(BuildOptions(target="a", output_dir=str(output_dir)))
    assert builder.next_targets() == ["b"]


def test_unknown_target_raises_key_error(tmp_path):
    project = make_project(tmp_path)
    agent = ScriptedAgent()
    builder, output_dir = make_builder(tmp_path, project, make_factory(agent))

    with pytest.raises(KeyError):
        builder.build(BuildOptions(target="nope", output_dir=str(output_dir)))


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------


def test_dry_run_reports_plan_without_side_effects(tmp_path):
    project = make_project(tmp_path)
    agent = ScriptedAgent()
    builder, output_dir = make_builder(tmp_path, project, make_factory(agent))

    results, error = builder.build(BuildOptions(output_dir=str(output_dir), dry_run=True))

    assert error is None
    assert [r.target for r in results] == ["a", "b", "c"]
    assert all(r.status == TargetStatus.PENDING for r in results)
    assert all(r.steps == [] for r in results)
    assert agent.build_calls == []
    assert not output_dir.exists()
    for target in ("a", "b", "c"):
        assert builder._state_manager.get_status(target) == TargetStatus.PENDING


# ---------------------------------------------------------------------------
# Skip already-built targets / force rebuild
# ---------------------------------------------------------------------------


def test_already_built_targets_are_skipped_without_force(tmp_path):
    project = make_project(tmp_path)
    agent = ScriptedAgent()
    builder, output_dir = make_builder(tmp_path, project, make_factory(agent))
    builder.build(BuildOptions(output_dir=str(output_dir)))
    assert len(agent.build_calls) == 3

    results, error = builder.build(BuildOptions(output_dir=str(output_dir)))

    assert error is None
    assert results == []
    assert len(agent.build_calls) == 3


def test_force_rebuilds_everything(tmp_path):
    project = make_project(tmp_path)
    agent = ScriptedAgent()
    builder, output_dir = make_builder(tmp_path, project, make_factory(agent))
    builder.build(BuildOptions(output_dir=str(output_dir)))
    assert len(agent.build_calls) == 3

    results, error = builder.build(BuildOptions(output_dir=str(output_dir), force=True))

    assert error is None
    assert len(results) == 3
    assert len(agent.build_calls) == 6


# ---------------------------------------------------------------------------
# Atomicity, retries, failure handling
# ---------------------------------------------------------------------------


def test_validation_failure_stops_dag_walk_and_does_not_checkpoint(tmp_path):
    project = make_project(tmp_path)
    agent = ScriptedAgent()
    agent.validation_status["a"] = "fail"
    builder, output_dir = make_builder(tmp_path, project, make_factory(agent), retries=1)

    results, error = builder.build(BuildOptions(output_dir=str(output_dir)))

    assert error is not None
    assert isinstance(error, RuntimeError)
    assert "a" in str(error)
    assert [r.target for r in results] == ["a"]

    failed_result = results[0]
    assert failed_result.status == TargetStatus.FAILED
    assert failed_result.commit_id == ""
    # The build step itself succeeded, so its files are still reported.
    assert failed_result.files_created == ["a.py"]

    assert builder._state_manager.get_status("a") == TargetStatus.FAILED
    assert builder._state_manager.get_status("b") == TargetStatus.PENDING
    assert builder._state_manager.get_status("c") == TargetStatus.PENDING
    assert agent.validate_calls  # validation was attempted
    assert not any(step.phase == "checkpoint" for step in failed_result.steps)
    assert builder._version_control.log() == []


def test_agent_error_retries_from_build_step_with_previous_errors(tmp_path):
    project = make_project(tmp_path)
    agent = ScriptedAgent()
    agent.build_responses["a"] = [
        AgentError("boom-1"),
        AgentError("boom-2"),
        BuildResponse(status="success", summary="ok", files_created=["a.py"], files_modified=[]),
    ]
    builder, output_dir = make_builder(tmp_path, project, make_factory(agent), retries=3)

    results, error = builder.build(BuildOptions(target="a", output_dir=str(output_dir)))

    assert error is None
    assert len(results) == 1
    result = results[0]
    assert result.status == TargetStatus.BUILT
    assert result.attempts == 3

    a_calls = [ctx for ctx in agent.build_calls if ctx.feature_path == "a"]
    assert len(a_calls) == 3
    assert a_calls[0].previous_errors == []
    assert a_calls[1].previous_errors == ["agent error: boom-1"]
    assert a_calls[2].previous_errors == ["agent error: boom-1", "agent error: boom-2"]


def test_exhausting_all_retries_marks_target_failed(tmp_path):
    project = make_project(tmp_path)
    agent = ScriptedAgent()
    agent.build_responses["a"] = [AgentError("always fails")] * 5
    builder, output_dir = make_builder(tmp_path, project, make_factory(agent), retries=2)

    results, error = builder.build(BuildOptions(target="a", output_dir=str(output_dir)))

    assert error is not None
    assert results[0].status == TargetStatus.FAILED
    assert results[0].attempts == 2
    a_calls = [ctx for ctx in agent.build_calls if ctx.feature_path == "a"]
    assert len(a_calls) == 2


# ---------------------------------------------------------------------------
# Profile resolution and sandboxing
# ---------------------------------------------------------------------------


def test_profile_override_takes_priority_over_builder_profile(tmp_path):
    project = make_project(tmp_path)
    agent = ScriptedAgent()
    factory = make_factory(agent)
    builder, output_dir = make_builder(tmp_path, project, factory)

    builder.build(BuildOptions(target="a", output_dir=str(output_dir), profile_override="special-profile"))

    build_profile_calls = [p for p in factory.calls if p.name == "special-profile"]
    assert build_profile_calls, "expected the overridden profile name to be used"


def test_sandbox_paths_are_absolute_and_scoped(tmp_path):
    project = make_project(tmp_path)
    agent = ScriptedAgent()
    factory = make_factory(agent)
    builder, output_dir = make_builder(tmp_path, project, factory)

    builder.build(BuildOptions(target="a", output_dir=str(output_dir)))

    profile = factory.calls[0]
    assert str(output_dir.resolve()) in profile.sandbox_write_paths
    assert str(builder._state_manager.build_response_dir.resolve()) in profile.sandbox_write_paths
    assert str(builder._state_manager.val_response_dir.resolve()) in profile.sandbox_write_paths
    for path in profile.sandbox_write_paths + profile.sandbox_read_paths:
        assert Path(path).is_absolute()

    a_intent_path = str(project.features["a"].intents[0].source_path.resolve())
    project_ic_path = str(project.project_intent.source_path.resolve())
    impl_dir_path = str((project.intent_dir / "implementations").resolve())
    assert a_intent_path in profile.sandbox_read_paths
    assert project_ic_path in profile.sandbox_read_paths
    assert impl_dir_path in profile.sandbox_read_paths


# ---------------------------------------------------------------------------
# Clean / CleanAll
# ---------------------------------------------------------------------------


def test_clean_reverts_target_resets_state_and_marks_descendants_outdated(tmp_path):
    project = make_project(tmp_path)
    agent = ScriptedAgent()
    builder, output_dir = make_builder(tmp_path, project, make_factory(agent))
    builder.build(BuildOptions(output_dir=str(output_dir)))

    assert (output_dir / "a.py").exists()
    assert (output_dir / "b.py").exists()
    assert (output_dir / "c.py").exists()

    builder.clean("b", str(output_dir))

    assert (output_dir / "a.py").exists()
    assert not (output_dir / "b.py").exists()
    assert not (output_dir / "c.py").exists()

    assert builder._state_manager.get_status("b") == TargetStatus.PENDING
    assert builder._state_manager.get_build_result("b") is None
    assert builder._state_manager.get_status("c") == TargetStatus.OUTDATED
    assert builder._state_manager.get_status("a") == TargetStatus.BUILT


def test_clean_on_never_built_target_is_a_noop(tmp_path):
    project = make_project(tmp_path)
    agent = ScriptedAgent()
    builder, output_dir = make_builder(tmp_path, project, make_factory(agent))

    builder.clean("a", str(output_dir))  # should not raise

    assert builder._state_manager.get_status("a") == TargetStatus.PENDING


def test_clean_all_resets_state_without_touching_files(tmp_path):
    project = make_project(tmp_path)
    agent = ScriptedAgent()
    builder, output_dir = make_builder(tmp_path, project, make_factory(agent))
    builder.build(BuildOptions(output_dir=str(output_dir)))

    builder.clean_all(str(output_dir))

    assert builder._state_manager.list_targets() == []
    assert (output_dir / "a.py").exists()
    assert (output_dir / "b.py").exists()
    assert (output_dir / "c.py").exists()


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


def test_validate_feature_delegates_to_suite_without_modifying_state(tmp_path):
    project = make_project(tmp_path)
    agent = ScriptedAgent()
    builder, output_dir = make_builder(tmp_path, project, make_factory(agent))
    builder.build(BuildOptions(output_dir=str(output_dir)))

    result = builder.validate("a", str(output_dir))

    assert result.target == "a"
    assert result.passed is True
    assert builder._state_manager.get_status("a") == TargetStatus.BUILT
    assert builder._state_manager.get_build_result("a").commit_id  # unchanged, still present


def test_validate_project_delegates_to_suite_for_every_feature(tmp_path):
    project = make_project(tmp_path)
    agent = ScriptedAgent()
    builder, output_dir = make_builder(tmp_path, project, make_factory(agent))
    builder.build(BuildOptions(output_dir=str(output_dir)))

    results = builder.validate("", str(output_dir))

    assert [r.target for r in results] == ["a", "b", "c", "project"]
    assert all(r.passed for r in results)


# ---------------------------------------------------------------------------
# Invalidation
# ---------------------------------------------------------------------------


def test_detect_outdated_is_empty_immediately_after_a_build(tmp_path):
    project = make_project(tmp_path)
    agent = ScriptedAgent()
    builder, output_dir = make_builder(tmp_path, project, make_factory(agent))
    builder.build(BuildOptions(output_dir=str(output_dir)))

    assert builder.detect_outdated() == []


def test_editing_an_intent_file_marks_it_and_its_dependents_outdated_on_rebuild(tmp_path):
    project = make_project(tmp_path)
    agent = ScriptedAgent()
    logs: list[str] = []
    builder, output_dir = make_builder(tmp_path, project, make_factory(agent), log=logs.append)
    builder.build(BuildOptions(output_dir=str(output_dir)))
    assert len(agent.build_calls) == 3

    a_intent = project.features["a"].intents[0]
    a_intent.body = "Build A, but differently now."
    write_intent_file(a_intent, a_intent.source_path)

    assert builder.detect_outdated() == ["a"]

    results, error = builder.build(BuildOptions(output_dir=str(output_dir)))

    assert error is None
    assert [r.target for r in results] == ["a", "b", "c"]
    assert len(agent.build_calls) == 6
    assert any("Marked 'a' outdated: intent changed" in line for line in logs)
    assert any("Marked 'b' outdated: dependency 'a' changed" in line for line in logs)
    assert any("Marked 'c' outdated: dependency 'a' changed" in line for line in logs)


def test_refresh_outdated_leaves_pending_and_failed_descendants_alone(tmp_path):
    project = make_project(tmp_path)
    agent = ScriptedAgent()
    builder, output_dir = make_builder(tmp_path, project, make_factory(agent))
    builder.build(BuildOptions(output_dir=str(output_dir)))

    # Simulate a previous failed rebuild of "c" that hasn't been retried yet.
    builder._state_manager.set_status("c", TargetStatus.FAILED)

    a_intent = project.features["a"].intents[0]
    a_intent.body = "Build A, revised."
    write_intent_file(a_intent, a_intent.source_path)

    changed = builder.refresh_outdated()

    assert "a" in changed
    assert "b" in changed
    assert "c" not in changed
    assert builder._state_manager.get_status("a") == TargetStatus.OUTDATED
    assert builder._state_manager.get_status("b") == TargetStatus.OUTDATED
    assert builder._state_manager.get_status("c") == TargetStatus.FAILED
