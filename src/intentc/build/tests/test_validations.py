"""Tests for intentc.build.validations."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from intentc.build.agents import AgentProfile, MockAgent, ValidationResponse, create_from_profile
from intentc.build.storage import SQLiteBackend
from intentc.build.validations import (
    CommandValidationRunner,
    FileExistsRunner,
    ValidationContext,
    ValidationRunner,
    ValidationSuite,
    ValidationSuiteResult,
)
from intentc.core import (
    Artifact,
    FeatureNode,
    Implementation,
    IntentFile,
    Project,
    ProjectIntent,
    Severity,
    Validation,
    ValidationFile,
)


def make_agent_profile(name: str = "validator") -> AgentProfile:
    return AgentProfile(name=name, provider="claude", model_id="mock-model")


def make_project(
    tmp_path: Path,
    features: dict[str, list[Validation]],
    assertions: Optional[list[Validation]] = None,
    depends_on: Optional[dict[str, list[str]]] = None,
) -> Project:
    depends_on = depends_on or {}
    feature_nodes: dict[str, FeatureNode] = {}
    for name, validations in features.items():
        intent = IntentFile(
            name=name.rsplit("/", 1)[-1],
            depends_on=list(depends_on.get(name, [])),
            body=f"# {name}\n\nBody for {name}.",
        )
        feature_nodes[name] = FeatureNode(
            path=name,
            intents=[intent],
            validations=[ValidationFile(target=name, validations=validations)],
        )

    assertion_files = [ValidationFile(target="project", validations=assertions)] if assertions else []

    intent_dir = tmp_path / "intent"
    intent_dir.mkdir(parents=True, exist_ok=True)

    return Project(
        project_intent=ProjectIntent(name="proj", body="# Project\n\nDescribes the project."),
        implementations={"default": Implementation(name="default", body="Python.")},
        assertions=assertion_files,
        features=feature_nodes,
        intent_dir=intent_dir,
    )


def make_suite(project: Project, output_dir: Path, **overrides) -> ValidationSuite:
    defaults = dict(
        project=project,
        agent_profile=make_agent_profile(),
        output_dir=str(output_dir),
    )
    defaults.update(overrides)
    return ValidationSuite(**defaults)


# ---------------------------------------------------------------------------
# Deterministic runners
# ---------------------------------------------------------------------------


def test_command_validation_pass_and_fail(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    project = make_project(tmp_path, {"feat": []})
    suite = make_suite(project, output_dir)

    entries = [
        Validation(name="ok", type="command_validation", args={"command": "exit 0"}),
        Validation(name="bad", type="command_validation", args={"command": "exit 3"}),
    ]
    result = suite.validate_entries("feat", entries)

    ok, bad = result.results
    assert ok.status == "pass"
    assert bad.status == "fail"
    assert "exit 3" in bad.reason
    assert result.passed is False
    assert result.passed_count == 1
    assert result.error_count == 1
    assert result.summary == "1/2 passed, 1 error(s), 0 warning(s)"


def test_command_validation_output_dir_placeholder(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (output_dir / "marker.txt").write_text("hi", encoding="utf-8")
    project = make_project(tmp_path, {"feat": []})
    suite = make_suite(project, output_dir)

    entry = Validation(
        name="check-marker",
        type="command_validation",
        args={"command": "test -f {output_dir}/marker.txt"},
    )
    result = suite.validate_entries("feat", [entry])
    assert result.results[0].status == "pass"


def test_command_validation_cwd_dot_runs_in_project_root(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (tmp_path / "root_marker.txt").write_text("hi", encoding="utf-8")
    project = make_project(tmp_path, {"feat": []})
    suite = make_suite(project, output_dir)

    entry = Validation(
        name="check-root",
        type="command_validation",
        args={"command": "test -f root_marker.txt", "cwd": "."},
    )
    result = suite.validate_entries("feat", [entry])
    assert result.results[0].status == "pass"


def test_file_exists_pass_and_fail_names_unmatched(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (output_dir / "a.py").write_text("x", encoding="utf-8")
    project = make_project(tmp_path, {"feat": []})
    suite = make_suite(project, output_dir)

    entry = Validation(name="files", type="file_exists", args={"paths": ["a.py", "missing.py"]})
    result = suite.validate_entries("feat", [entry])
    assert result.results[0].status == "fail"
    assert "missing.py" in result.results[0].reason

    entry_ok = Validation(name="files-ok", type="file_exists", args={"paths": ["a.py"]})
    result_ok = suite.validate_entries("feat", [entry_ok])
    assert result_ok.results[0].status == "pass"


def test_deterministic_error_skips_agent_validations(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    project = make_project(tmp_path, {"feat": []})
    mock_agent = MockAgent()
    suite = make_suite(project, output_dir, create_agent=lambda profile: mock_agent)

    det_fail = Validation(name="det-fail", type="command_validation", args={"command": "exit 1"})
    agent_entry = Validation(name="agent-check", type="agent_validation", args={"rubric": "x" * 50})

    result = suite.validate_entries("feat", [det_fail, agent_entry])

    agent_result = next(r for r in result.results if r.name == "agent-check")
    assert agent_result.status == "fail"
    assert "det-fail" in agent_result.reason
    assert mock_agent.validate_calls == []


def test_deterministic_warning_does_not_skip_agent_validations(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    project = make_project(tmp_path, {"feat": []})
    mock_agent = MockAgent(validation_response=ValidationResponse(name="agent-check", status="pass", reason="ok"))
    suite = make_suite(project, output_dir, create_agent=lambda profile: mock_agent)

    det_fail = Validation(
        name="det-warn", type="command_validation", args={"command": "exit 1"}, severity=Severity.WARNING
    )
    agent_entry = Validation(name="agent-check", type="agent_validation", args={"rubric": "x" * 50})

    suite.validate_entries("feat", [det_fail, agent_entry])

    assert len(mock_agent.validate_calls) == 1


# ---------------------------------------------------------------------------
# Suite lifecycle
# ---------------------------------------------------------------------------


def test_validate_feature_stamps_metadata_and_rolls_up_counts(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (output_dir / "README.md").write_text("hi", encoding="utf-8")
    validations = [
        Validation(name="files-exist", type="file_exists", args={"paths": ["README.md"]}, severity=Severity.ERROR),
        Validation(
            name="warn-check", type="command_validation", args={"command": "exit 1"}, severity=Severity.WARNING
        ),
    ]
    project = make_project(tmp_path, {"feat": validations})
    suite = make_suite(project, output_dir)

    result = suite.validate_feature("feat")

    assert isinstance(result, ValidationSuiteResult)
    assert result.target == "feat"
    assert result.passed_count == 1
    assert result.error_count == 0
    assert result.warning_count == 1
    assert result.passed is True
    assert result.summary == "1/2 passed, 0 error(s), 1 warning(s)"
    for response in result.results:
        assert response.duration_secs >= 0
        assert response.type in {"file_exists", "command_validation"}
        assert response.severity in {"error", "warning"}


def test_validate_project_runs_all_features_in_order_plus_assertions(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (output_dir / "a.txt").write_text("a", encoding="utf-8")
    (output_dir / "b.txt").write_text("b", encoding="utf-8")

    feat_a = [Validation(name="a-exists", type="file_exists", args={"paths": ["a.txt"]})]
    feat_b = [Validation(name="b-exists", type="file_exists", args={"paths": ["b.txt"]})]
    assertions = [Validation(name="project-exists", type="file_exists", args={"paths": ["a.txt", "b.txt"]})]

    project = make_project(
        tmp_path,
        {"a": feat_a, "b": feat_b},
        assertions=assertions,
        depends_on={"b": ["a"]},
    )
    suite = make_suite(project, output_dir)

    results = suite.validate_project()

    assert [r.target for r in results] == ["a", "b", "project"]
    assert all(r.passed for r in results)


def test_validate_entries_accepts_arbitrary_subset(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    project = make_project(tmp_path, {"feat": []})
    suite = make_suite(project, output_dir)

    entry = Validation(name="adhoc", type="file_exists", args={"paths": ["{output_dir}"]})
    result = suite.validate_entries("some-other-target", [entry])
    assert result.target == "some-other-target"
    assert result.results[0].status == "pass"


def test_default_create_agent_is_create_from_profile(tmp_path: Path) -> None:
    project = make_project(tmp_path, {"feat": []})
    suite = make_suite(project, tmp_path / "out")
    assert suite.create_agent is create_from_profile


def test_agent_validation_uses_create_agent_factory_lazily(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    project = make_project(tmp_path, {"feat": []})
    mock_agent = MockAgent(
        validation_response=ValidationResponse(name="agent-check", status="pass", reason="looks good")
    )
    created_profiles: list[AgentProfile] = []

    def factory(profile: AgentProfile) -> MockAgent:
        created_profiles.append(profile)
        return mock_agent

    suite = make_suite(project, output_dir, create_agent=factory)
    assert created_profiles == []  # not created at construction time

    entry = Validation(name="agent-check", type="agent_validation", args={"rubric": "y" * 50})
    result = suite.validate_entries("feat", [entry])

    assert result.results[0].status == "pass"
    assert len(mock_agent.validate_calls) == 1
    build_ctx, validation = mock_agent.validate_calls[0]
    assert validation.name == "agent-check"
    assert build_ctx.generation_id.startswith("val-")
    assert build_ctx.dependency_names == []
    assert build_ctx.validations == []
    assert created_profiles == [suite.agent_profile]


def test_agent_validation_per_entry_profile_override(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    project = make_project(tmp_path, {"feat": []})
    mock_agent = MockAgent()
    captured: list[AgentProfile] = []

    def factory(profile: AgentProfile) -> MockAgent:
        captured.append(profile)
        return mock_agent

    base_profile = AgentProfile(name="base", provider="claude", model_id="fast", timeout=30)
    suite = make_suite(project, output_dir, agent_profile=base_profile, create_agent=factory)

    entry = Validation(
        name="agent-check",
        type="agent_validation",
        args={"rubric": "z" * 50, "agent_profile": {"model_id": "careful", "timeout": 120}},
    )
    suite.validate_entries("feat", [entry])

    assert captured[0].model_id == "careful"
    assert captured[0].timeout == 120
    assert captured[0].provider == "claude"
    assert captured[0].name == "base"


def test_agent_validation_passes_artifacts_into_build_context(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    project = make_project(tmp_path, {"feat": []})
    schema_path = tmp_path / "schema.json"
    schema_path.write_text("{}", encoding="utf-8")
    artifact = Artifact(
        path="schema.json", kind="schema", note="constrains it", owner="feat", resolved_paths=[schema_path]
    )
    project.features["feat"].intents[0].artifacts = [artifact]

    mock_agent = MockAgent(validation_response=ValidationResponse(name="agent-check", status="pass"))
    suite = make_suite(project, output_dir, create_agent=lambda profile: mock_agent)

    entry = Validation(name="agent-check", type="agent_validation", args={"rubric": "y" * 50})
    suite.validate_entries("feat", [entry])

    build_ctx, _ = mock_agent.validate_calls[0]
    assert build_ctx.artifacts == [artifact]


def test_command_validation_substitutes_intent_dir(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    project = make_project(tmp_path, {"feat": []})
    (tmp_path / "intent" / "marker.txt").write_text("hi", encoding="utf-8")
    suite = make_suite(project, output_dir)

    entry = Validation(
        name="check-intent-dir",
        type="command_validation",
        args={"command": "test -f {intent_dir}/marker.txt"},
    )
    result = suite.validate_entries("feat", [entry])
    assert result.results[0].status == "pass"


def test_command_validation_substitutes_intent_dir_in_expect_output(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    project = make_project(tmp_path, {"feat": []})
    suite = make_suite(project, output_dir)

    entry = Validation(
        name="check-expect-output",
        type="command_validation",
        args={"command": "echo {intent_dir}", "expect_output": "{intent_dir}"},
    )
    result = suite.validate_entries("feat", [entry])
    assert result.results[0].status == "pass"


def test_file_exists_substitutes_intent_dir(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    project = make_project(tmp_path, {"feat": []})
    (tmp_path / "intent" / "schema.json").write_text("{}", encoding="utf-8")
    suite = make_suite(project, output_dir)

    entry = Validation(name="files", type="file_exists", args={"paths": ["{intent_dir}/schema.json"]})
    result = suite.validate_entries("feat", [entry])
    assert result.results[0].status == "pass"


def test_feature_intent_resolution(tmp_path: Path) -> None:
    project = make_project(tmp_path, {"feat": []})
    suite = make_suite(project, tmp_path / "out")

    project_intent_file = suite._resolve_feature_intent("project")
    assert project_intent_file.name == "project"
    assert project_intent_file.body == project.project_intent.body

    unknown_intent_file = suite._resolve_feature_intent("nope")
    assert unknown_intent_file.name == "nope"
    assert unknown_intent_file.body == ""

    known_intent_file = suite._resolve_feature_intent("feat")
    assert known_intent_file.name == "feat"


# ---------------------------------------------------------------------------
# Runner registry extensibility
# ---------------------------------------------------------------------------


def test_default_runners_dispatch_without_extra_registration(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    project = make_project(tmp_path, {"feat": []})
    suite = make_suite(project, output_dir)

    command_entry = Validation(name="cmd", type="command_validation", args={"command": "exit 0"})
    file_entry = Validation(name="file", type="file_exists", args={"paths": ["{output_dir}"]})
    result = suite.validate_entries("feat", [command_entry, file_entry])

    assert all(r.status == "pass" for r in result.results)


def test_runner_registry_constructor_override(tmp_path: Path) -> None:
    class AlwaysPassRunner(ValidationRunner):
        def type(self) -> str:
            return "command_validation"

        def run(self, validation: Validation, ctx: ValidationContext) -> ValidationResponse:
            return ValidationResponse(name=validation.name, status="pass", reason="stubbed")

    project = make_project(tmp_path, {"feat": []})
    suite = make_suite(
        project, tmp_path / "out", runner_registry={"command_validation": AlwaysPassRunner()}
    )
    entry = Validation(name="always", type="command_validation", args={"command": "exit 1"})
    result = suite.validate_entries("feat", [entry])
    assert result.results[0].status == "pass"
    assert result.results[0].reason == "stubbed"


def test_register_runner_custom_type_is_dispatched(tmp_path: Path) -> None:
    calls: list[str] = []

    class EchoRunner(ValidationRunner):
        def type(self) -> str:
            return "echo"

        def run(self, validation: Validation, ctx: ValidationContext) -> ValidationResponse:
            calls.append(validation.name)
            return ValidationResponse(name=validation.name, status="pass", reason="echoed")

    project = make_project(tmp_path, {"feat": []})
    suite = make_suite(project, tmp_path / "out")
    suite.register_runner(EchoRunner())

    entry = Validation(name="custom", type="echo", args={})
    result = suite.validate_entries("feat", [entry])

    assert calls == ["custom"]
    assert result.results[0].status == "pass"
    assert result.results[0].type == "echo"


def test_unknown_type_fails_with_descriptive_reason(tmp_path: Path) -> None:
    project = make_project(tmp_path, {"feat": []})
    suite = make_suite(project, tmp_path / "out")

    entry = Validation(name="mystery", type="totally_unknown", args={})
    result = suite.validate_entries("feat", [entry])

    assert result.results[0].status == "fail"
    assert "totally_unknown" in result.results[0].reason
    assert "No runner registered" in result.results[0].reason


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_storage_backend_persists_results(tmp_path: Path) -> None:
    backend = SQLiteBackend(base_dir=tmp_path, output_dir="out")
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (output_dir / "a.txt").write_text("a", encoding="utf-8")

    project = make_project(
        tmp_path, {"feat": [Validation(name="a-exists", type="file_exists", args={"paths": ["a.txt"]})]}
    )
    suite = make_suite(project, output_dir, storage_backend=backend)

    result = suite.validate_feature("feat")
    assert result.passed

    saved = backend.get_validation_results("feat")
    assert any(row["name"] == "a-exists" and row["status"] == "pass" for row in saved)
    backend.close()


def test_reused_generation_id_is_not_recreated(tmp_path: Path) -> None:
    backend = SQLiteBackend(base_dir=tmp_path, output_dir="out")
    backend.create_generation("build-123", "out")
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (output_dir / "a.txt").write_text("a", encoding="utf-8")

    project = make_project(
        tmp_path, {"feat": [Validation(name="a-exists", type="file_exists", args={"paths": ["a.txt"]})]}
    )
    suite = make_suite(project, output_dir, storage_backend=backend, generation_id="build-123")

    result = suite.validate_feature("feat")
    assert result.passed
    backend.close()
