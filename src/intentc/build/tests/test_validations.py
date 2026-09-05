"""Tests for the validation suite and runners."""

from __future__ import annotations

from pathlib import Path

import pytest

from intentc.build.agents import AgentProfile, MockAgent, ValidationResponse
from intentc.build.storage import SQLiteBackend
from intentc.build.validations import (
    AgentValidationRunner,
    CommandValidationRunner,
    FileExistsRunner,
    ValidationContext,
    ValidationRunner,
    ValidationSuite,
    ValidationSuiteResult,
)
from intentc.core import (
    FeatureNode,
    IntentFile,
    Project,
    ProjectIntent,
    Severity,
    Validation,
    ValidationFile,
)

PROFILE = AgentProfile(name="test", provider="cli", command="unused")


def _project(tmp_path: Path, validations: list[Validation], assertions: list[Validation] | None = None) -> Project:
    intent_dir = tmp_path / "intent"
    intent_dir.mkdir(exist_ok=True)
    return Project(
        project_intent=ProjectIntent(name="p", body="project body"),
        features={
            "models": FeatureNode(
                path="models",
                intents=[IntentFile(name="models", body="models body")],
                validations=[ValidationFile(target="models", validations=validations)],
            ),
            "store": FeatureNode(path="store", intents=[IntentFile(name="store", depends_on=["models"])]),
        },
        assertions=[ValidationFile(target="project", validations=assertions or [])],
        intent_dir=intent_dir,
    )


def _suite(tmp_path: Path, project: Project, agent: MockAgent | None = None, **kwargs) -> ValidationSuite:
    (tmp_path / "src").mkdir(exist_ok=True)
    suite = ValidationSuite(project, PROFILE, "src", val_response_dir=tmp_path / "val", **kwargs)
    if agent is not None:
        suite.register_runner(AgentValidationRunner(agent))
    return suite


def _agent_validation(name: str, severity: Severity = Severity.ERROR) -> Validation:
    return Validation(name=name, severity=severity, args={"rubric": f"rubric for {name} that is long enough"})


# ---------------------------------------------------------------------------
# Lifecycle with a mock agent
# ---------------------------------------------------------------------------


def test_validate_feature_lifecycle(tmp_path: Path) -> None:
    agent = MockAgent()
    project = _project(tmp_path, [_agent_validation("a"), _agent_validation("b", Severity.WARNING)])
    logs: list[str] = []
    suite = _suite(tmp_path, project, agent, log=logs.append)
    result = suite.validate_feature("models")
    assert isinstance(result, ValidationSuiteResult)
    assert result.passed and result.passed_count == 2 and result.error_count == 0
    assert result.summary == "2/2 passed, 0 error(s), 0 warning(s)"
    assert [r.name for r in result.results] == ["a", "b"]
    assert result.results[1].severity == "warning"
    assert result.results[0].type == "agent_validation"
    assert len(agent.validate_calls) == 2
    ctx, validation = next(call for call in agent.validate_calls if call[1].name == "a")  # parallel: order varies
    assert ctx.generation_id.startswith("val-") and ctx.validations == [] and ctx.dependency_names == []
    assert ctx.intent.body == "models body" and ctx.feature_path == "models"
    assert any("Validating feature 'models'... (2 validations)" in l for l in logs)
    assert any("Validation 'a': pass" in l for l in logs)
    assert not list((tmp_path / "val").glob("*.json"))  # response files cleaned up


def test_severity_rollup(tmp_path: Path) -> None:
    def judge(ctx, v):
        return ValidationResponse(name=v.name, status="fail", reason="nope")

    agent = MockAgent(validate_side_effect=judge)
    project = _project(tmp_path, [_agent_validation("warn", Severity.WARNING)])
    result = _suite(tmp_path, project, agent).validate_feature("models")
    assert result.passed and result.warning_count == 1 and result.error_count == 0
    assert result.summary == "0/1 passed, 0 error(s), 1 warning(s)"

    project2 = _project(tmp_path, [_agent_validation("err")])
    result2 = _suite(tmp_path, project2, agent).validate_feature("models")
    assert not result2.passed and result2.error_count == 1


def test_non_pass_status_counts_as_failure(tmp_path: Path) -> None:
    agent = MockAgent(validate_side_effect=lambda ctx, v: ValidationResponse(name=v.name, status="unknown", reason="?"))
    result = _suite(tmp_path, _project(tmp_path, [_agent_validation("x")]), agent).validate_feature("models")
    assert not result.passed and result.error_count == 1


def test_agent_exception_is_failure(tmp_path: Path) -> None:
    def boom(ctx, v):
        raise RuntimeError("agent exploded")

    agent = MockAgent(validate_side_effect=boom)
    result = _suite(tmp_path, _project(tmp_path, [_agent_validation("x")]), agent).validate_feature("models")
    assert result.results[0].status == "fail" and "agent exploded" in result.results[0].reason


def test_validate_project_runs_features_then_assertions(tmp_path: Path) -> None:
    agent = MockAgent()
    project = _project(tmp_path, [_agent_validation("a")], assertions=[_agent_validation("e2e")])
    results = _suite(tmp_path, project, agent).validate_project()
    assert [r.target for r in results] == ["models", "store", "project"]
    assert results[1].summary == "0/0 passed, 0 error(s), 0 warning(s)"
    project_ctx = agent.validate_calls[-1][0]
    assert project_ctx.intent.name == "project" and project_ctx.intent.body == "project body"


def test_validate_feature_project_keyword_runs_assertions_only(tmp_path: Path) -> None:
    agent = MockAgent()
    project = _project(tmp_path, [_agent_validation("a")], assertions=[_agent_validation("e2e")])
    result = _suite(tmp_path, project, agent).validate_feature("project")
    assert result.target == "project" and [r.name for r in result.results] == ["e2e"]


def test_unknown_feature_and_empty_entries(tmp_path: Path) -> None:
    suite = _suite(tmp_path, _project(tmp_path, []), MockAgent())
    assert suite.validate_feature("nope").passed
    assert suite.validate_entries("models", []).summary == "0/0 passed, 0 error(s), 0 warning(s)"


def test_agent_created_lazily_from_profile(tmp_path: Path, monkeypatch) -> None:
    created: list[AgentProfile] = []

    def fake_create(profile, log=None):
        created.append(profile)
        return MockAgent()

    monkeypatch.setattr("intentc.build.validations.create_from_profile", fake_create)
    suite = _suite(tmp_path, _project(tmp_path, [Validation(name="c", type="command_validation", args={"command": "exit 0"})]))
    assert suite.validate_feature("models").passed
    assert created == []  # deterministic-only run never creates an agent
    suite.validate_entries("models", [_agent_validation("a")])
    assert created == [PROFILE]


def test_results_persisted_to_storage(tmp_path: Path) -> None:
    backend = SQLiteBackend(tmp_path, "src")
    agent = MockAgent()
    project = _project(tmp_path, [_agent_validation("a"), Validation(name="c", type="command_validation", args={"command": "exit 0"})])
    suite = _suite(tmp_path, project, agent, storage_backend=backend, generation_id="gen-x")
    backend.create_generation("gen-x", "src")
    suite.validate_feature("models")
    rows = backend._conn.execute("SELECT name, status, severity, generation_id FROM validation_results ORDER BY id").fetchall()
    assert {(r[0], r[1], r[2], r[3]) for r in rows} == {("a", "pass", "error", "gen-x"), ("c", "pass", "error", "gen-x")}
    responses = backend._conn.execute("SELECT response_type FROM agent_responses").fetchall()
    assert [r[0] for r in responses] == ["validation"]
    backend.close()


# ---------------------------------------------------------------------------
# Deterministic runners
# ---------------------------------------------------------------------------


def _ctx(tmp_path: Path) -> ValidationContext:
    return ValidationContext(
        project_intent=ProjectIntent(name="p"),
        implementation=None,
        feature_intent=IntentFile(name="f"),
        output_dir="src",
        response_file_path="",
        project_root=str(tmp_path),
        feature_path="f",
    )


def test_command_runner(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    runner = CommandValidationRunner()
    assert runner.type() == "command_validation"
    ok = runner.run(Validation(name="ok", type="command_validation", args={"command": "echo hi"}), _ctx(tmp_path))
    assert ok.status == "pass" and ok.reason == "exit 0: hi"
    bad = runner.run(Validation(name="bad", type="command_validation", args={"command": "echo oops; exit 3"}), _ctx(tmp_path))
    assert bad.status == "fail" and "exited 3" in bad.reason and "oops" in bad.reason


def test_command_runner_cwd_and_placeholder(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "root.txt").write_text("r")
    (tmp_path / "src" / "out.txt").write_text("o")
    runner = CommandValidationRunner()
    in_output = runner.run(Validation(name="a", type="command_validation", args={"command": "test -f out.txt"}), _ctx(tmp_path))
    assert in_output.status == "pass"
    in_root = runner.run(Validation(name="b", type="command_validation", args={"command": "test -f root.txt && test -f {output_dir}/out.txt", "cwd": "."}), _ctx(tmp_path))
    assert in_root.status == "pass"
    missing = runner.run(Validation(name="c", type="command_validation", args={"command": "true", "cwd": "nope"}), _ctx(tmp_path))
    assert missing.status == "fail" and "does not exist" in missing.reason


def test_command_runner_expect_output_and_timeout(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    runner = CommandValidationRunner()
    good = runner.run(Validation(name="a", type="command_validation", args={"command": "echo hello world", "expect_output": "hello"}), _ctx(tmp_path))
    assert good.status == "pass"
    bad = runner.run(Validation(name="b", type="command_validation", args={"command": "echo hello", "expect_output": "bye"}), _ctx(tmp_path))
    assert bad.status == "fail" and "expected output to contain 'bye'" in bad.reason
    slow = runner.run(Validation(name="c", type="command_validation", args={"command": "sleep 3", "timeout": 0.3}), _ctx(tmp_path))
    assert slow.status == "fail" and "timed out" in slow.reason


def test_file_exists_runner(tmp_path: Path) -> None:
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "a.py").write_text("")
    runner = FileExistsRunner()
    assert runner.type() == "file_exists"
    ok = runner.run(Validation(name="ok", type="file_exists", args={"paths": ["pkg/*.py", "pkg", "{output_dir}/pkg/a.py"]}), _ctx(tmp_path))
    assert ok.status == "pass"
    bad = runner.run(Validation(name="bad", type="file_exists", args={"paths": ["pkg/a.py", "pkg/b.py", "zzz/*"]}), _ctx(tmp_path))
    assert bad.status == "fail" and bad.reason == "missing in src: pkg/b.py, zzz/*"


def test_deterministic_failure_skips_agent_validations(tmp_path: Path) -> None:
    agent = MockAgent()
    project = _project(
        tmp_path,
        [
            _agent_validation("judge"),
            Validation(name="cmd", type="command_validation", args={"command": "exit 1"}),
            Validation(name="warn-cmd", type="command_validation", severity=Severity.WARNING, args={"command": "exit 1"}),
        ],
    )
    result = _suite(tmp_path, project, agent).validate_feature("models")
    assert agent.validate_calls == []
    by_name = {r.name: r for r in result.results}
    assert by_name["judge"].status == "fail" and "skipped: deterministic validation 'cmd' failed" in by_name["judge"].reason
    assert [r.name for r in result.results] == ["judge", "cmd", "warn-cmd"]  # original order kept
    assert result.error_count == 2 and result.warning_count == 1


def test_warning_deterministic_failure_does_not_skip_agents(tmp_path: Path) -> None:
    agent = MockAgent()
    project = _project(tmp_path, [Validation(name="w", type="command_validation", severity=Severity.WARNING, args={"command": "exit 1"}), _agent_validation("judge")])
    result = _suite(tmp_path, project, agent).validate_feature("models")
    assert len(agent.validate_calls) == 1 and result.passed and result.warning_count == 1


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class _RecordingRunner(ValidationRunner):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def type(self) -> str:
        return "custom_check"

    def run(self, validation: Validation, ctx: ValidationContext) -> ValidationResponse:
        self.calls.append(validation.name)
        return ValidationResponse(name=validation.name, status="pass", reason="custom ok")


def test_custom_runner_dispatch_and_unknown_type(tmp_path: Path) -> None:
    custom = _RecordingRunner()
    project = _project(tmp_path, [Validation(name="mine", type="custom_check"), Validation(name="weird", type="no_such_runner")])
    suite = _suite(tmp_path, project, MockAgent(), runner_registry={"custom_check": custom})
    result = suite.validate_feature("models")
    assert custom.calls == ["mine"]
    assert result.results[0].status == "pass"
    assert result.results[1].status == "fail"
    assert "No runner registered for validation type 'no_such_runner'" in result.results[1].reason


def test_register_runner_post_construction(tmp_path: Path) -> None:
    custom = _RecordingRunner()
    suite = _suite(tmp_path, _project(tmp_path, []), MockAgent())
    suite.register_runner(custom)
    result = suite.validate_entries("models", [Validation(name="x", type="custom_check")])
    assert result.passed and custom.calls == ["x"]
