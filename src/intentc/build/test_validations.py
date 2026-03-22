from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from intentc.core.types import (
    IntentFile,
    Implementation,
    ProjectIntent,
    Severity,
    Validation,
    ValidationFile,
)
from intentc.core.project import FeatureNode, Project
from intentc.build.agents.types import (
    AgentProfile,
    ValidationResponse,
)
from intentc.build.agents.mock_agent import MockAgent
from intentc.build.validations import (
    AgentValidationRunner,
    ValidationContext,
    ValidationRunner,
    ValidationSuite,
    ValidationSuiteResult,
)


def _make_project(
    features: dict[str, FeatureNode] | None = None,
    assertions: list[ValidationFile] | None = None,
) -> Project:
    return Project(
        project_intent=ProjectIntent(name="test-project", body="Test project intent."),
        implementations={"default": Implementation(name="default", body="Python impl.")},
        assertions=assertions or [],
        features=features or {},
    )


def _make_profile() -> AgentProfile:
    return AgentProfile(name="test", provider="cli", command="echo")


# ---------------------------------------------------------------------------
# ValidationSuiteResult
# ---------------------------------------------------------------------------


class TestValidationSuiteResult:
    def test_defaults(self):
        r = ValidationSuiteResult(target="feat")
        assert r.target == "feat"
        assert r.results == []
        assert r.passed is True
        assert r.summary == ""


# ---------------------------------------------------------------------------
# AgentValidationRunner
# ---------------------------------------------------------------------------


class TestAgentValidationRunner:
    def test_returns_agent_response(self, tmp_path: Path):
        mock = MockAgent(
            validation_response=ValidationResponse(
                name="check-a", status="pass", reason="looks good"
            )
        )
        runner = AgentValidationRunner(mock)
        assert runner.type() == "agent_validation"

        ctx = ValidationContext(
            project_intent=ProjectIntent(name="p", body=""),
            implementation=None,
            feature_intent=IntentFile(name="feat", body=""),
            output_dir=str(tmp_path),
            response_file_path=str(tmp_path / "resp.json"),
        )
        validation = Validation(
            name="check-a",
            type="agent_validation",
            args={"rubric": "Check something"},
        )
        resp = runner.run(validation, ctx)
        assert resp.status == "pass"
        assert resp.name == "check-a"
        assert len(mock.validate_calls) == 1

    def test_agent_error_returns_failure(self, tmp_path: Path):
        """If agent raises, runner returns a failure response."""

        class FailingAgent(MockAgent):
            def validate(self, ctx, validation):
                raise RuntimeError("agent exploded")

        runner = AgentValidationRunner(FailingAgent())
        ctx = ValidationContext(
            project_intent=ProjectIntent(name="p", body=""),
            implementation=None,
            feature_intent=IntentFile(name="feat", body=""),
            output_dir=str(tmp_path),
            response_file_path=str(tmp_path / "resp.json"),
        )
        resp = runner.run(Validation(name="boom", args={"rubric": "x"}), ctx)
        assert resp.status == "fail"
        assert "agent exploded" in resp.reason


# ---------------------------------------------------------------------------
# ValidationSuite — lifecycle
# ---------------------------------------------------------------------------


class TestValidationSuiteLifecycle:
    """End-to-end lifecycle tests using MockAgent."""

    def _make_suite(
        self,
        project: Project,
        tmp_path: Path,
        mock: MockAgent | None = None,
        extra_runners: dict[str, ValidationRunner] | None = None,
        log: list[str] | None = None,
    ) -> ValidationSuite:
        mock = mock or MockAgent()
        profile = _make_profile()

        # We need to override create_from_profile to return our mock.
        # Monkey-patch within the validations module.
        import intentc.build.validations as vmod

        original = vmod.create_from_profile

        def _fake_create(p):
            return mock

        vmod.create_from_profile = _fake_create
        try:
            suite = ValidationSuite(
                project=project,
                agent_profile=profile,
                output_dir=str(tmp_path),
                runner_registry=extra_runners,
                val_response_dir=tmp_path,
                log=log.append if log is not None else None,
            )
        finally:
            vmod.create_from_profile = original
        return suite

    def test_validate_feature_runs_all_entries(self, tmp_path: Path):
        validations = [
            Validation(name="v1", args={"rubric": "check 1"}),
            Validation(name="v2", args={"rubric": "check 2"}),
        ]
        vf = ValidationFile(target="feat-a", validations=validations)
        node = FeatureNode(
            path="feat-a",
            intents=[IntentFile(name="feat-a", body="Feature A")],
            validations=[vf],
        )
        project = _make_project(features={"feat-a": node})

        mock = MockAgent(
            validation_response=ValidationResponse(
                name="mock", status="pass", reason="ok"
            )
        )
        log: list[str] = []
        suite = self._make_suite(project, tmp_path, mock=mock, log=log)

        result = suite.validate_feature("feat-a")

        assert result.target == "feat-a"
        assert len(result.results) == 2
        assert result.passed is True
        assert "2/2 passed" in result.summary
        assert len(mock.validate_calls) == 2
        # Check log messages
        assert any("Validating feature 'feat-a'" in m for m in log)

    def test_validate_feature_error_severity_blocks(self, tmp_path: Path):
        validations = [
            Validation(name="must-pass", severity=Severity.ERROR, args={"rubric": "x"}),
        ]
        vf = ValidationFile(target="feat-b", validations=validations)
        node = FeatureNode(
            path="feat-b",
            intents=[IntentFile(name="feat-b", body="B")],
            validations=[vf],
        )
        project = _make_project(features={"feat-b": node})

        mock = MockAgent(
            validation_response=ValidationResponse(
                name="must-pass", status="fail", reason="broken"
            )
        )
        suite = self._make_suite(project, tmp_path, mock=mock)
        result = suite.validate_feature("feat-b")

        assert result.passed is False
        assert "1 error(s)" in result.summary

    def test_validate_feature_warning_does_not_block(self, tmp_path: Path):
        validations = [
            Validation(
                name="advisory", severity=Severity.WARNING, args={"rubric": "x"}
            ),
        ]
        vf = ValidationFile(target="feat-c", validations=validations)
        node = FeatureNode(
            path="feat-c",
            intents=[IntentFile(name="feat-c", body="C")],
            validations=[vf],
        )
        project = _make_project(features={"feat-c": node})

        mock = MockAgent(
            validation_response=ValidationResponse(
                name="advisory", status="fail", reason="meh"
            )
        )
        suite = self._make_suite(project, tmp_path, mock=mock)
        result = suite.validate_feature("feat-c")

        assert result.passed is True
        assert "1 warning(s)" in result.summary
        assert "0 error(s)" in result.summary

    def test_validate_feature_unknown_feature(self, tmp_path: Path):
        project = _make_project()
        suite = self._make_suite(project, tmp_path)
        result = suite.validate_feature("nonexistent")
        assert result.passed is False
        assert "not found" in result.summary

    def test_validate_project_topological_order(self, tmp_path: Path):
        """Features are validated in topological order, assertions last."""
        node_a = FeatureNode(
            path="a",
            intents=[IntentFile(name="a", body="A")],
            validations=[
                ValidationFile(
                    target="a",
                    validations=[Validation(name="va", args={"rubric": "x"})],
                )
            ],
        )
        node_b = FeatureNode(
            path="b",
            intents=[IntentFile(name="b", body="B", depends_on=["a"])],
            validations=[
                ValidationFile(
                    target="b",
                    validations=[Validation(name="vb", args={"rubric": "x"})],
                )
            ],
        )
        assertions = [
            ValidationFile(
                target="project",
                validations=[Validation(name="assert-1", args={"rubric": "x"})],
            )
        ]
        project = _make_project(
            features={"a": node_a, "b": node_b},
            assertions=assertions,
        )

        mock = MockAgent(
            validation_response=ValidationResponse(
                name="mock", status="pass", reason="ok"
            )
        )
        log: list[str] = []
        suite = self._make_suite(project, tmp_path, mock=mock, log=log)
        results = suite.validate_project()

        # a before b, then project assertions
        assert len(results) == 3
        assert results[0].target == "a"
        assert results[1].target == "b"
        assert results[2].target == "project"
        assert any("Validating project" in m for m in log)
        assert any("project-level assertions" in m for m in log)

    def test_validate_entries_directly(self, tmp_path: Path):
        """validate_entries can be called with arbitrary entries."""
        entries = [
            Validation(name="custom-1", args={"rubric": "x"}),
            Validation(name="custom-2", args={"rubric": "y"}),
        ]
        project = _make_project(features={
            "t": FeatureNode(
                path="t",
                intents=[IntentFile(name="t", body="T")],
            )
        })
        mock = MockAgent(
            validation_response=ValidationResponse(
                name="mock", status="pass", reason="ok"
            )
        )
        suite = self._make_suite(project, tmp_path, mock=mock)
        result = suite.validate_entries("t", entries)
        assert len(result.results) == 2
        assert result.passed is True

    def test_feature_intent_resolution_project(self, tmp_path: Path):
        """For target 'project', feature intent body comes from project intent."""
        project = _make_project()
        mock = MockAgent(
            validation_response=ValidationResponse(
                name="m", status="pass", reason="ok"
            )
        )
        suite = self._make_suite(project, tmp_path, mock=mock)
        result = suite.validate_entries(
            "project", [Validation(name="p", args={"rubric": "x"})]
        )
        # The agent should have been called with project intent body
        ctx_used = mock.validate_calls[0][0]
        assert ctx_used.intent.name == "project"
        assert ctx_used.intent.body == "Test project intent."

    def test_feature_intent_resolution_unknown_target(self, tmp_path: Path):
        """For an unknown target, feature intent is empty."""
        project = _make_project()
        mock = MockAgent(
            validation_response=ValidationResponse(
                name="m", status="pass", reason="ok"
            )
        )
        suite = self._make_suite(project, tmp_path, mock=mock)
        result = suite.validate_entries(
            "unknown", [Validation(name="u", args={"rubric": "x"})]
        )
        ctx_used = mock.validate_calls[0][0]
        assert ctx_used.intent.name == "unknown"
        assert ctx_used.intent.body == ""


# ---------------------------------------------------------------------------
# Runner registry and extensibility
# ---------------------------------------------------------------------------


class TestRunnerRegistry:
    def _make_suite_with_mock(
        self, tmp_path: Path, extra_runners: dict[str, ValidationRunner] | None = None
    ) -> ValidationSuite:
        import intentc.build.validations as vmod

        original = vmod.create_from_profile
        vmod.create_from_profile = lambda p: MockAgent()
        try:
            suite = ValidationSuite(
                project=_make_project(features={
                    "f": FeatureNode(
                        path="f",
                        intents=[IntentFile(name="f", body="F")],
                    )
                }),
                agent_profile=_make_profile(),
                output_dir=str(tmp_path),
                runner_registry=extra_runners,
                val_response_dir=tmp_path,
            )
        finally:
            vmod.create_from_profile = original
        return suite

    def test_agent_validation_registered_by_default(self, tmp_path: Path):
        suite = self._make_suite_with_mock(tmp_path)
        assert "agent_validation" in suite._registry

    def test_custom_runner_dispatched(self, tmp_path: Path):
        class CustomRunner(ValidationRunner):
            def __init__(self):
                self.invoked = False

            def type(self) -> str:
                return "custom_check"

            def run(self, validation, ctx):
                self.invoked = True
                return ValidationResponse(
                    name=validation.name, status="pass", reason="custom ok"
                )

        custom = CustomRunner()
        suite = self._make_suite_with_mock(tmp_path, extra_runners={"custom_check": custom})

        entry = Validation(name="c1", type="custom_check", args={})
        result = suite.validate_entries("f", [entry])

        assert custom.invoked
        assert result.results[0].status == "pass"
        assert result.results[0].reason == "custom ok"

    def test_register_runner_post_construction(self, tmp_path: Path):
        class LateRunner(ValidationRunner):
            def __init__(self):
                self.called = False

            def type(self) -> str:
                return "late_type"

            def run(self, validation, ctx):
                self.called = True
                return ValidationResponse(
                    name=validation.name, status="pass", reason="late"
                )

        suite = self._make_suite_with_mock(tmp_path)
        late = LateRunner()
        suite.register_runner(late)

        entry = Validation(name="l1", type="late_type", args={})
        result = suite.validate_entries("f", [entry])
        assert late.called
        assert result.results[0].status == "pass"

    def test_unknown_type_fails_with_descriptive_error(self, tmp_path: Path):
        suite = self._make_suite_with_mock(tmp_path)
        entry = Validation(name="bad", type="nonexistent_type", args={})
        result = suite.validate_entries("f", [entry])

        assert result.passed is False
        assert result.results[0].status == "fail"
        assert "nonexistent_type" in result.results[0].reason
        assert "No runner registered" in result.results[0].reason
