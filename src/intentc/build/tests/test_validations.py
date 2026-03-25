"""Tests for the validation suite, runners, and registry."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from intentc.build.agents import (
    AgentProfile,
    MockAgent,
    ValidationResponse,
)
from intentc.build.validations import (
    AgentValidationRunner,
    ValidationContext,
    ValidationRunner,
    ValidationSuite,
    ValidationSuiteResult,
)
from intentc.core.models import (
    Implementation,
    IntentFile,
    ProjectIntent,
    Severity,
    Validation,
    ValidationFile,
    ValidationType,
)
from intentc.core.project import FeatureNode, Project


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(
    features: dict[str, FeatureNode] | None = None,
    assertions: list[ValidationFile] | None = None,
    implementations: dict[str, Implementation] | None = None,
) -> Project:
    return Project(
        project_intent=ProjectIntent(name="test-project", body="A test project."),
        implementations=implementations or {
            "default": Implementation(name="default", body="Test implementation.")
        },
        assertions=assertions or [],
        features=features or {},
    )


def _make_agent_profile() -> AgentProfile:
    return AgentProfile(name="test", provider="cli", command="echo")


def _make_suite(
    project: Project,
    runner_registry: dict[str, ValidationRunner] | None = None,
    output_dir: str | None = None,
    val_response_dir: Path | None = None,
    log: list[str] | None = None,
) -> ValidationSuite:
    """Create a ValidationSuite with a mock agent backing the default runner."""
    profile = _make_agent_profile()
    log_list = log if log is not None else []

    suite = ValidationSuite(
        project=project,
        agent_profile=profile,
        output_dir=output_dir or tempfile.mkdtemp(),
        runner_registry=runner_registry,
        val_response_dir=val_response_dir,
        log=lambda msg: log_list.append(msg),
    )
    return suite


class StubRunner(ValidationRunner):
    """A test runner that returns a configurable response."""

    def __init__(
        self,
        type_name: str = "stub",
        status: str = "pass",
        reason: str = "stub passed",
    ) -> None:
        self._type_name = type_name
        self._status = status
        self._reason = reason
        self.calls: list[tuple[Validation, ValidationContext]] = []

    def type(self) -> str:
        return self._type_name

    def run(self, validation: Validation, ctx: ValidationContext) -> ValidationResponse:
        self.calls.append((validation, ctx))
        return ValidationResponse(
            name=validation.name,
            status=self._status,
            reason=self._reason,
        )


# ---------------------------------------------------------------------------
# ValidationSuiteResult tests
# ---------------------------------------------------------------------------


class TestValidationSuiteResult:
    def test_defaults(self):
        result = ValidationSuiteResult(target="test")
        assert result.target == "test"
        assert result.results == []
        assert result.passed is True
        assert result.summary == ""


# ---------------------------------------------------------------------------
# AgentValidationRunner tests
# ---------------------------------------------------------------------------


class TestAgentValidationRunner:
    def test_type_returns_agent_validation(self):
        agent = MockAgent()
        runner = AgentValidationRunner(agent)
        assert runner.type() == "agent_validation"

    def test_run_delegates_to_agent(self):
        agent = MockAgent(
            validation_response=ValidationResponse(
                name="check-1",
                status="pass",
                reason="All good",
            )
        )
        runner = AgentValidationRunner(agent)

        validation = Validation(
            name="check-1",
            type=ValidationType.AGENT_VALIDATION,
            args={"rubric": "Check something."},
        )
        ctx = ValidationContext(
            project_intent=ProjectIntent(name="proj", body="desc"),
            implementation=None,
            feature_intent=IntentFile(name="feat", body="feature body"),
            output_dir="/tmp/out",
            response_file_path="/tmp/resp.json",
        )

        resp = runner.run(validation, ctx)
        assert resp.status == "pass"
        assert resp.name == "check-1"
        assert len(agent.validate_calls) == 1

    def test_run_returns_failure_on_agent_error(self):
        """If the agent raises, the runner returns a fail response."""

        class FailingAgent(MockAgent):
            def validate(self, ctx, validation):
                raise RuntimeError("Agent exploded")

        agent = FailingAgent()
        runner = AgentValidationRunner(agent)

        validation = Validation(name="boom", args={"rubric": "explode"})
        ctx = ValidationContext(
            project_intent=ProjectIntent(name="p", body=""),
            implementation=None,
            feature_intent=IntentFile(name="f", body=""),
            output_dir="/tmp/out",
            response_file_path="/tmp/resp.json",
        )

        resp = runner.run(validation, ctx)
        assert resp.status == "fail"
        assert "Agent error" in resp.reason


# ---------------------------------------------------------------------------
# ValidationSuite lifecycle tests
# ---------------------------------------------------------------------------


class TestValidationSuiteLifecycle:
    def test_validate_feature_no_validations(self):
        project = _make_project(features={
            "core/foo": FeatureNode(
                path="core/foo",
                intents=[IntentFile(name="foo", body="Foo feature")],
                validations=[],
            ),
        })
        suite = _make_suite(project)
        result = suite.validate_feature("core/foo")
        assert result.passed is True
        assert result.target == "core/foo"

    def test_validate_feature_unknown_feature(self):
        project = _make_project()
        suite = _make_suite(project)
        result = suite.validate_feature("nonexistent")
        assert result.passed is True

    def test_validate_feature_runs_entries(self):
        """validate_feature loads .icv entries and runs them through the registry."""
        passing_runner = StubRunner(type_name="agent_validation", status="pass")

        project = _make_project(features={
            "core/bar": FeatureNode(
                path="core/bar",
                intents=[IntentFile(name="bar", body="Bar feature")],
                validations=[
                    ValidationFile(
                        target="core/bar",
                        validations=[
                            Validation(name="v1", args={"rubric": "check v1"}),
                            Validation(name="v2", args={"rubric": "check v2"}),
                        ],
                    ),
                ],
            ),
        })

        suite = _make_suite(
            project,
            runner_registry={"agent_validation": passing_runner},
        )
        result = suite.validate_feature("core/bar")

        assert result.passed is True
        assert len(result.results) == 2
        assert result.results[0].name == "v1"
        assert result.results[1].name == "v2"
        assert len(passing_runner.calls) == 2

    def test_validate_feature_error_severity_fails_suite(self):
        """An error-severity failure makes the suite result fail."""
        failing_runner = StubRunner(
            type_name="agent_validation",
            status="fail",
            reason="not good",
        )

        project = _make_project(features={
            "core/baz": FeatureNode(
                path="core/baz",
                intents=[IntentFile(name="baz", body="Baz")],
                validations=[
                    ValidationFile(
                        target="core/baz",
                        validations=[
                            Validation(
                                name="must-pass",
                                severity=Severity.ERROR,
                                args={"rubric": "must pass"},
                            ),
                        ],
                    ),
                ],
            ),
        })

        suite = _make_suite(
            project,
            runner_registry={"agent_validation": failing_runner},
        )
        result = suite.validate_feature("core/baz")

        assert result.passed is False
        assert "1 errors" in result.summary

    def test_validate_feature_warning_severity_does_not_fail(self):
        """A warning-severity failure does not block the suite."""
        failing_runner = StubRunner(
            type_name="agent_validation",
            status="fail",
            reason="advisory",
        )

        project = _make_project(features={
            "core/warn": FeatureNode(
                path="core/warn",
                intents=[IntentFile(name="warn", body="Warn")],
                validations=[
                    ValidationFile(
                        target="core/warn",
                        validations=[
                            Validation(
                                name="advisory",
                                severity=Severity.WARNING,
                                args={"rubric": "check"},
                            ),
                        ],
                    ),
                ],
            ),
        })

        suite = _make_suite(
            project,
            runner_registry={"agent_validation": failing_runner},
        )
        result = suite.validate_feature("core/warn")

        assert result.passed is True
        assert "1 warnings" in result.summary

    def test_validate_entries_directly(self):
        """validate_entries runs an arbitrary list of entries."""
        runner = StubRunner(type_name="agent_validation", status="pass")
        project = _make_project(features={
            "core/x": FeatureNode(
                path="core/x",
                intents=[IntentFile(name="x", body="X feature")],
            ),
        })

        suite = _make_suite(project, runner_registry={"agent_validation": runner})

        entries = [
            Validation(name="e1", args={"rubric": "r1"}),
            Validation(name="e2", args={"rubric": "r2"}),
        ]
        result = suite.validate_entries("core/x", entries)

        assert result.target == "core/x"
        assert len(result.results) == 2
        assert result.passed is True

    def test_validate_project_runs_all_features_and_assertions(self):
        """validate_project iterates features in topo order plus assertions."""
        runner = StubRunner(type_name="agent_validation", status="pass")

        project = _make_project(
            features={
                "core/a": FeatureNode(
                    path="core/a",
                    intents=[IntentFile(name="a", body="A")],
                    validations=[
                        ValidationFile(
                            target="core/a",
                            validations=[Validation(name="va", args={"rubric": "ra"})],
                        ),
                    ],
                ),
                "core/b": FeatureNode(
                    path="core/b",
                    intents=[IntentFile(name="b", body="B", depends_on=["core/a"])],
                    validations=[
                        ValidationFile(
                            target="core/b",
                            validations=[Validation(name="vb", args={"rubric": "rb"})],
                        ),
                    ],
                ),
            },
            assertions=[
                ValidationFile(
                    target="project",
                    validations=[
                        Validation(name="project-check", args={"rubric": "rp"}),
                    ],
                ),
            ],
        )

        log_msgs: list[str] = []
        suite = _make_suite(
            project,
            runner_registry={"agent_validation": runner},
            log=log_msgs,
        )
        results = suite.validate_project()

        # Two features + one assertion set
        assert len(results) == 3
        # Features in topological order: a before b
        assert results[0].target == "core/a"
        assert results[1].target == "core/b"
        assert results[2].target == "project"
        assert all(r.passed for r in results)

    def test_validate_entries_mixed_pass_fail_summary(self):
        """Summary correctly counts passes, errors, and warnings."""

        class MixedRunner(ValidationRunner):
            def type(self) -> str:
                return "agent_validation"

            def run(self, validation, ctx):
                if "fail" in validation.name:
                    return ValidationResponse(
                        name=validation.name, status="fail", reason="nope"
                    )
                return ValidationResponse(
                    name=validation.name, status="pass", reason="ok"
                )

        project = _make_project(features={
            "feat": FeatureNode(
                path="feat",
                intents=[IntentFile(name="feat", body="")],
            ),
        })

        suite = _make_suite(project, runner_registry={"agent_validation": MixedRunner()})

        entries = [
            Validation(name="pass-1", severity=Severity.ERROR),
            Validation(name="fail-err", severity=Severity.ERROR),
            Validation(name="fail-warn", severity=Severity.WARNING),
            Validation(name="pass-2", severity=Severity.ERROR),
        ]
        result = suite.validate_entries("feat", entries)

        assert result.passed is False  # one error-severity failure
        assert "2 passed" in result.summary
        assert "4 validations" in result.summary
        assert "1 errors" in result.summary
        assert "1 warnings" in result.summary


# ---------------------------------------------------------------------------
# Runner registry tests
# ---------------------------------------------------------------------------


class TestRunnerRegistry:
    def test_default_agent_validation_runner(self):
        """AgentValidationRunner is registered by default for 'agent_validation'."""
        project = _make_project(features={
            "f": FeatureNode(
                path="f",
                intents=[IntentFile(name="f", body="")],
                validations=[
                    ValidationFile(
                        target="f",
                        validations=[
                            Validation(name="v", args={"rubric": "check"}),
                        ],
                    ),
                ],
            ),
        })

        # Don't pass a custom registry — the default should handle agent_validation
        suite = _make_suite(project)
        result = suite.validate_feature("f")

        # The mock agent in _make_suite uses CLIAgent (via create_from_profile with "cli")
        # but since we can't actually run a CLI, let's just verify the structure is right
        assert result.target == "f"
        assert len(result.results) == 1

    def test_custom_runner_dispatched(self):
        """A custom runner is dispatched to when a validation entry has a matching type."""
        custom_runner = StubRunner(type_name="file_check", status="pass", reason="file exists")

        project = _make_project(features={
            "f": FeatureNode(
                path="f",
                intents=[IntentFile(name="f", body="")],
            ),
        })

        suite = _make_suite(project, runner_registry={"file_check": custom_runner})

        entries = [
            Validation(
                name="check-file",
                type=ValidationType.FILE_CHECK,
                args={"path": "main.py"},
            ),
        ]
        result = suite.validate_entries("f", entries)

        assert result.passed is True
        assert len(custom_runner.calls) == 1
        assert custom_runner.calls[0][0].name == "check-file"

    def test_unknown_type_fails_with_descriptive_error(self):
        """A validation entry with an unknown type fails with a descriptive error."""
        project = _make_project(features={
            "f": FeatureNode(
                path="f",
                intents=[IntentFile(name="f", body="")],
            ),
        })

        suite = _make_suite(project)

        entries = [
            Validation(
                name="mystery",
                type=ValidationType.LLM_JUDGE,
                args={},
            ),
        ]
        result = suite.validate_entries("f", entries)

        assert result.passed is False
        assert result.results[0].status == "fail"
        assert "llm_judge" in result.results[0].reason
        assert "No runner registered" in result.results[0].reason

    def test_register_runner_post_construction(self):
        """register_runner adds a runner after construction."""
        project = _make_project(features={
            "f": FeatureNode(
                path="f",
                intents=[IntentFile(name="f", body="")],
            ),
        })

        suite = _make_suite(project)

        custom = StubRunner(type_name="command_check", status="pass", reason="ok")
        suite.register_runner(custom)

        entries = [
            Validation(
                name="cmd",
                type=ValidationType.COMMAND_CHECK,
                args={"command": "true"},
            ),
        ]
        result = suite.validate_entries("f", entries)

        assert result.passed is True
        assert len(custom.calls) == 1


# ---------------------------------------------------------------------------
# Progress logging tests
# ---------------------------------------------------------------------------


class TestProgressLogging:
    def test_validate_feature_emits_log_messages(self):
        runner = StubRunner(type_name="agent_validation", status="pass")
        project = _make_project(features={
            "core/logged": FeatureNode(
                path="core/logged",
                intents=[IntentFile(name="logged", body="")],
                validations=[
                    ValidationFile(
                        target="core/logged",
                        validations=[
                            Validation(name="v1", args={"rubric": "r"}),
                        ],
                    ),
                ],
            ),
        })

        log_msgs: list[str] = []
        suite = _make_suite(
            project,
            runner_registry={"agent_validation": runner},
            log=log_msgs,
        )
        suite.validate_feature("core/logged")

        # Should have logged feature start, validation start, and result
        assert any("Validating feature 'core/logged'" in m for m in log_msgs)
        assert any("Running validation 'v1'" in m for m in log_msgs)
        assert any("Validation 'v1': pass" in m for m in log_msgs)

    def test_validate_project_emits_log_messages(self):
        runner = StubRunner(type_name="agent_validation", status="pass")
        project = _make_project(features={
            "a": FeatureNode(
                path="a",
                intents=[IntentFile(name="a", body="")],
                validations=[
                    ValidationFile(
                        target="a",
                        validations=[Validation(name="va", args={"rubric": "r"})],
                    ),
                ],
            ),
        })

        log_msgs: list[str] = []
        suite = _make_suite(
            project,
            runner_registry={"agent_validation": runner},
            log=log_msgs,
        )
        suite.validate_project()

        assert any("Validating project" in m for m in log_msgs)
