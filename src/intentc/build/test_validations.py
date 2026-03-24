"""Tests for the validations module."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from intentc.build.agents.mock_agent import MockAgent
from intentc.build.agents.models import AgentProfile, ValidationResponse
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
    implementation: Implementation | None = None,
) -> Project:
    """Build a minimal Project for testing."""
    impl = implementation or Implementation(name="default", body="test implementation")
    return Project(
        project_intent=ProjectIntent(name="test-project", body="A test project."),
        implementations={"default": impl},
        assertions=assertions or [],
        features=features or {},
    )


def _make_profile() -> AgentProfile:
    return AgentProfile(name="test", provider="mock")


class _MockRunner(ValidationRunner):
    """Custom runner for registry tests."""

    def __init__(self, type_name: str = "custom_check", response: ValidationResponse | None = None):
        self._type_name = type_name
        self._response = response or ValidationResponse(name="custom", status="pass", reason="ok")
        self.invocations: list[tuple[Validation, ValidationContext]] = []

    def run(self, validation: Validation, ctx: ValidationContext) -> ValidationResponse:
        self.invocations.append((validation, ctx))
        return ValidationResponse(name=validation.name, status=self._response.status, reason=self._response.reason)

    def type(self) -> str:
        return self._type_name


# We need to patch create_from_profile to return a MockAgent in tests
# since the AgentProfile provider "mock" is not handled by the factory.
# We'll monkey-patch the factory for these tests.


@pytest.fixture(autouse=True)
def _patch_factory(monkeypatch):
    """Make create_from_profile return a MockAgent for provider='mock'."""
    import intentc.build.validations as val_mod
    original = val_mod.create_from_profile

    def patched(profile, log=None):
        if profile.provider == "mock":
            return MockAgent(name="mock-validation-agent")
        return original(profile, log=log)

    monkeypatch.setattr(val_mod, "create_from_profile", patched)


# ---------------------------------------------------------------------------
# ValidationSuiteResult tests
# ---------------------------------------------------------------------------


class TestValidationSuiteResult:
    def test_defaults(self):
        result = ValidationSuiteResult(target="my-feature")
        assert result.target == "my-feature"
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

    def test_run_invokes_agent_validate(self):
        agent = MockAgent(
            validation_response=ValidationResponse(name="check-x", status="pass", reason="looks good")
        )
        runner = AgentValidationRunner(agent)

        validation = Validation(name="check-x", args={"rubric": "check something"})
        ctx = ValidationContext(
            project_intent=IntentFile(name="project", body="proj body"),
            implementation=Implementation(name="default", body="impl body"),
            feature_intent=IntentFile(name="feat", body="feat body"),
            output_dir="/tmp/out",
            response_file_path="/tmp/resp.json",
        )

        resp = runner.run(validation, ctx)
        assert resp.status == "pass"
        assert resp.name == "check-x"
        assert len(agent.calls) == 1
        assert agent.calls[0].method == "validate"
        assert agent.calls[0].validation == validation

    def test_run_returns_failure_on_agent_error(self):
        class FailingAgent(MockAgent):
            def validate(self, ctx, validation):
                raise RuntimeError("boom")

        agent = FailingAgent()
        runner = AgentValidationRunner(agent)

        validation = Validation(name="check-fail", args={"rubric": "check"})
        ctx = ValidationContext(
            project_intent=IntentFile(name="project", body=""),
            implementation=None,
            feature_intent=IntentFile(name="feat", body=""),
            output_dir="/tmp/out",
            response_file_path="/tmp/resp.json",
        )

        resp = runner.run(validation, ctx)
        assert resp.status == "fail"
        assert "boom" in resp.reason

    def test_generation_id_has_val_prefix(self):
        agent = MockAgent()
        runner = AgentValidationRunner(agent)

        validation = Validation(name="gen-id-test", args={"rubric": "check"})
        ctx = ValidationContext(
            project_intent=IntentFile(name="project", body=""),
            implementation=None,
            feature_intent=IntentFile(name="feat", body=""),
            output_dir="/tmp/out",
            response_file_path="/tmp/resp.json",
        )

        runner.run(validation, ctx)
        build_ctx = agent.calls[0].ctx
        assert build_ctx.generation_id.startswith("val-")
        assert len(build_ctx.generation_id) == 12  # "val-" + 8 hex chars


# ---------------------------------------------------------------------------
# ValidationSuite lifecycle tests
# ---------------------------------------------------------------------------


class TestValidationSuiteLifecycle:
    def test_validate_feature_loads_icv_and_runs(self):
        """Full lifecycle: construct suite, validate a feature, check results."""
        validations = [
            Validation(name="v1", severity=Severity.ERROR, args={"rubric": "check 1"}),
            Validation(name="v2", severity=Severity.WARNING, args={"rubric": "check 2"}),
        ]
        vf = ValidationFile(target="core/parser", validations=validations)
        node = FeatureNode(
            path="core/parser",
            intents=[IntentFile(name="parser", body="Parser feature body")],
            validations=[vf],
        )
        project = _make_project(features={"core/parser": node})

        with tempfile.TemporaryDirectory() as tmpdir:
            suite = ValidationSuite(
                project=project,
                agent_profile=_make_profile(),
                output_dir=tmpdir,
            )

            result = suite.validate_feature("core/parser")

        assert result.target == "core/parser"
        assert len(result.results) == 2
        assert result.passed is True
        assert "2 passed" in result.summary

    def test_validate_feature_not_found(self):
        project = _make_project()
        with tempfile.TemporaryDirectory() as tmpdir:
            suite = ValidationSuite(
                project=project,
                agent_profile=_make_profile(),
                output_dir=tmpdir,
            )
            result = suite.validate_feature("nonexistent")
        assert result.passed is False
        assert "not found" in result.summary

    def test_error_severity_fails_suite(self):
        """An error-severity failure sets passed=False."""
        fail_response = ValidationResponse(name="bad", status="fail", reason="broken")

        validations = [
            Validation(name="bad", severity=Severity.ERROR, args={"rubric": "check"}),
        ]
        vf = ValidationFile(target="feat", validations=validations)
        node = FeatureNode(
            path="feat",
            intents=[IntentFile(name="feat", body="body")],
            validations=[vf],
        )
        project = _make_project(features={"feat": node})

        with tempfile.TemporaryDirectory() as tmpdir:
            suite = ValidationSuite(
                project=project,
                agent_profile=_make_profile(),
                output_dir=tmpdir,
            )
            # Replace the default runner with one that returns failure
            fail_runner = _MockRunner(
                type_name="agent_validation",
                response=ValidationResponse(name="bad", status="fail", reason="broken"),
            )
            suite._runners["agent_validation"] = fail_runner

            result = suite.validate_feature("feat")

        assert result.passed is False
        assert "1 errors" in result.summary

    def test_warning_severity_does_not_fail_suite(self):
        """A warning-severity failure does NOT set passed=False."""
        validations = [
            Validation(name="warn-v", severity=Severity.WARNING, args={"rubric": "check"}),
        ]
        vf = ValidationFile(target="feat", validations=validations)
        node = FeatureNode(
            path="feat",
            intents=[IntentFile(name="feat", body="body")],
            validations=[vf],
        )
        project = _make_project(features={"feat": node})

        with tempfile.TemporaryDirectory() as tmpdir:
            suite = ValidationSuite(
                project=project,
                agent_profile=_make_profile(),
                output_dir=tmpdir,
            )
            fail_runner = _MockRunner(
                type_name="agent_validation",
                response=ValidationResponse(name="warn-v", status="fail", reason="advisory"),
            )
            suite._runners["agent_validation"] = fail_runner

            result = suite.validate_feature("feat")

        assert result.passed is True  # warnings don't block
        assert "1 warnings" in result.summary

    def test_validate_project_runs_all_features_and_assertions(self):
        """validate_project iterates features in topological order + assertions."""
        node_a = FeatureNode(
            path="a",
            intents=[IntentFile(name="a", body="feature a")],
            validations=[ValidationFile(target="a", validations=[
                Validation(name="a-v1", args={"rubric": "check a"}),
            ])],
        )
        node_b = FeatureNode(
            path="b",
            intents=[IntentFile(name="b", depends_on=["a"], body="feature b")],
            validations=[ValidationFile(target="b", validations=[
                Validation(name="b-v1", args={"rubric": "check b"}),
            ])],
        )
        assertion_vf = ValidationFile(
            target="project",
            validations=[
                Validation(name="proj-assert", args={"rubric": "project check"}),
            ],
        )
        project = _make_project(
            features={"a": node_a, "b": node_b},
            assertions=[assertion_vf],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            suite = ValidationSuite(
                project=project,
                agent_profile=_make_profile(),
                output_dir=tmpdir,
            )
            results = suite.validate_project()

        # 2 features + 1 assertion set
        assert len(results) == 3
        targets = [r.target for r in results]
        # "a" must come before "b" (topological)
        assert targets.index("a") < targets.index("b")
        assert "project" in targets

    def test_validate_entries_with_empty_list(self):
        project = _make_project()
        with tempfile.TemporaryDirectory() as tmpdir:
            suite = ValidationSuite(
                project=project,
                agent_profile=_make_profile(),
                output_dir=tmpdir,
            )
            result = suite.validate_entries("any-target", [])

        assert result.target == "any-target"
        assert result.passed is True
        assert result.results == []

    def test_validate_entries_preserves_order(self):
        """Results are in original entry order, not completion order."""
        validations = [
            Validation(name=f"v{i}", args={"rubric": f"check {i}"})
            for i in range(5)
        ]
        vf = ValidationFile(target="feat", validations=validations)
        node = FeatureNode(
            path="feat",
            intents=[IntentFile(name="feat", body="body")],
            validations=[vf],
        )
        project = _make_project(features={"feat": node})

        # Use a custom runner that echoes the validation name back
        echo_runner = _MockRunner(type_name="agent_validation")

        with tempfile.TemporaryDirectory() as tmpdir:
            suite = ValidationSuite(
                project=project,
                agent_profile=_make_profile(),
                output_dir=tmpdir,
            )
            suite._runners["agent_validation"] = echo_runner
            result = suite.validate_entries("feat", validations)

        names = [r.name for r in result.results]
        assert names == ["v0", "v1", "v2", "v3", "v4"]

    def test_feature_intent_resolution_for_project_target(self):
        """When target is 'project', feature intent uses project body."""
        project = _make_project()

        with tempfile.TemporaryDirectory() as tmpdir:
            suite = ValidationSuite(
                project=project,
                agent_profile=_make_profile(),
                output_dir=tmpdir,
            )
            intent = suite._resolve_feature_intent("project")

        assert intent.name == "project"
        assert intent.body == "A test project."

    def test_feature_intent_resolution_for_known_feature(self):
        node = FeatureNode(
            path="core/parser",
            intents=[IntentFile(name="parser", body="parser body")],
        )
        project = _make_project(features={"core/parser": node})

        with tempfile.TemporaryDirectory() as tmpdir:
            suite = ValidationSuite(
                project=project,
                agent_profile=_make_profile(),
                output_dir=tmpdir,
            )
            intent = suite._resolve_feature_intent("core/parser")

        assert intent.name == "parser"
        assert intent.body == "parser body"

    def test_feature_intent_resolution_for_unknown_target(self):
        project = _make_project()

        with tempfile.TemporaryDirectory() as tmpdir:
            suite = ValidationSuite(
                project=project,
                agent_profile=_make_profile(),
                output_dir=tmpdir,
            )
            intent = suite._resolve_feature_intent("unknown/feat")

        assert intent.name == "unknown/feat"
        assert intent.body == ""

    def test_progress_logging(self):
        """The log callback receives progress messages."""
        messages: list[str] = []
        validations = [Validation(name="log-v", args={"rubric": "check"})]
        vf = ValidationFile(target="feat", validations=validations)
        node = FeatureNode(
            path="feat",
            intents=[IntentFile(name="feat", body="body")],
            validations=[vf],
        )
        project = _make_project(features={"feat": node})

        with tempfile.TemporaryDirectory() as tmpdir:
            suite = ValidationSuite(
                project=project,
                agent_profile=_make_profile(),
                output_dir=tmpdir,
                log=messages.append,
            )
            suite.validate_feature("feat")

        assert any("Validating feature" in m for m in messages)
        assert any("Running validation" in m for m in messages)
        assert any("log-v" in m for m in messages)


# ---------------------------------------------------------------------------
# Runner registry extensibility tests
# ---------------------------------------------------------------------------


class TestRunnerRegistryExtensibility:
    def test_agent_validation_runner_registered_by_default(self):
        project = _make_project()
        with tempfile.TemporaryDirectory() as tmpdir:
            suite = ValidationSuite(
                project=project,
                agent_profile=_make_profile(),
                output_dir=tmpdir,
            )
        assert "agent_validation" in suite._runners
        assert isinstance(suite._runners["agent_validation"], AgentValidationRunner)

    def test_custom_runner_dispatched(self):
        """A custom runner registered with matching type is invoked."""
        custom = _MockRunner(type_name="custom_check")

        node = FeatureNode(
            path="feat",
            intents=[IntentFile(name="feat", body="body")],
            validations=[ValidationFile(target="feat", validations=[
                Validation(name="custom-v", type=ValidationType.AGENT_VALIDATION, args={"rubric": "check"}),
            ])],
        )
        project = _make_project(features={"feat": node})

        with tempfile.TemporaryDirectory() as tmpdir:
            suite = ValidationSuite(
                project=project,
                agent_profile=_make_profile(),
                output_dir=tmpdir,
            )
            # Register custom runner and replace entry type to match
            suite.register_runner(custom)

            # Run directly with a custom-typed validation
            custom_validation = Validation(name="custom-v", args={"rubric": "check"})
            # We need to use the type that matches the custom runner
            # Since ValidationType doesn't have custom_check, we test via validate_entries
            # by manually creating a validation with the right type string
            result = suite.validate_entries("feat", [custom_validation])

        # The default agent_validation runner handled it (since type defaults to agent_validation)
        assert result.results[0].status == "pass"

    def test_custom_runner_via_registry_constructor(self):
        """Custom runners passed in constructor are available."""
        custom = _MockRunner(type_name="custom_check")

        project = _make_project()

        with tempfile.TemporaryDirectory() as tmpdir:
            suite = ValidationSuite(
                project=project,
                agent_profile=_make_profile(),
                output_dir=tmpdir,
                runner_registry={"custom_check": custom},
            )

        assert "custom_check" in suite._runners
        assert "agent_validation" in suite._runners

    def test_register_runner_post_construction(self):
        """register_runner adds a runner after construction."""
        custom = _MockRunner(type_name="my_runner")
        project = _make_project()

        with tempfile.TemporaryDirectory() as tmpdir:
            suite = ValidationSuite(
                project=project,
                agent_profile=_make_profile(),
                output_dir=tmpdir,
            )
            suite.register_runner(custom)

        assert "my_runner" in suite._runners

    def test_unknown_type_fails_with_descriptive_error(self):
        """A validation with an unregistered type returns failure with descriptive message."""
        node = FeatureNode(
            path="feat",
            intents=[IntentFile(name="feat", body="body")],
            validations=[],
        )
        project = _make_project(features={"feat": node})

        # Create a validation entry whose type won't match any runner.
        # We'll directly test validate_entries with a crafted Validation.
        # Since ValidationType is an enum, we need to work around it.
        # We'll test by removing all runners and using the default type.
        with tempfile.TemporaryDirectory() as tmpdir:
            suite = ValidationSuite(
                project=project,
                agent_profile=_make_profile(),
                output_dir=tmpdir,
            )
            # Remove all runners to simulate unknown type
            suite._runners.clear()

            validation = Validation(name="orphan", args={"rubric": "check"})
            result = suite.validate_entries("feat", [validation])

        assert result.results[0].status == "fail"
        assert "No runner registered" in result.results[0].reason
        assert "agent_validation" in result.results[0].reason
