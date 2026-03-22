"""Tests for intentc.build.validations."""

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
from intentc.core.project import Project
from intentc.core.types import (
    Implementation,
    IntentFile,
    ProjectIntent,
    Severity,
    Validation,
    ValidationFile,
    ValidationType,
)
from intentc.core.project import FeatureNode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(
    features: dict[str, FeatureNode] | None = None,
    assertions: list[ValidationFile] | None = None,
) -> Project:
    """Create a minimal Project for testing."""
    return Project(
        project_intent=ProjectIntent(name="test-project", body="A test project."),
        implementations={"default": Implementation(name="default", body="Python impl")},
        features=features or {},
        assertions=assertions or [],
    )


def _mock_profile() -> AgentProfile:
    return AgentProfile(name="mock", provider="mock")


class _CountingRunner(ValidationRunner):
    """Test runner that counts invocations and returns configurable results."""

    def __init__(self, type_name: str, status: str = "pass", reason: str = "ok") -> None:
        self._type_name = type_name
        self._status = status
        self._reason = reason
        self.calls: list[tuple[Validation, ValidationContext]] = []

    def run(self, validation: Validation, ctx: ValidationContext) -> ValidationResponse:
        self.calls.append((validation, ctx))
        return ValidationResponse(
            name=validation.name,
            status=self._status,
            reason=self._reason,
        )

    def type(self) -> str:
        return self._type_name


# ---------------------------------------------------------------------------
# ValidationSuiteResult tests
# ---------------------------------------------------------------------------


class TestValidationSuiteResult:
    def test_build_summary_all_pass(self) -> None:
        validations = [
            Validation(name="v1", severity=Severity.ERROR),
            Validation(name="v2", severity=Severity.WARNING),
        ]
        result = ValidationSuiteResult(
            target="feat",
            results=[
                ValidationResponse(name="v1", status="pass", reason="ok"),
                ValidationResponse(name="v2", status="pass", reason="ok"),
            ],
        )
        result._build_summary(validations)
        assert result.passed is True
        assert result.summary == "2/2 passed, 0 error(s), 0 warning(s)"

    def test_build_summary_error_failure(self) -> None:
        validations = [
            Validation(name="v1", severity=Severity.ERROR),
            Validation(name="v2", severity=Severity.WARNING),
        ]
        result = ValidationSuiteResult(
            target="feat",
            results=[
                ValidationResponse(name="v1", status="fail", reason="bad"),
                ValidationResponse(name="v2", status="pass", reason="ok"),
            ],
        )
        result._build_summary(validations)
        assert result.passed is False
        assert result.summary == "1/2 passed, 1 error(s), 0 warning(s)"

    def test_build_summary_warning_only_failure(self) -> None:
        validations = [
            Validation(name="v1", severity=Severity.WARNING),
        ]
        result = ValidationSuiteResult(
            target="feat",
            results=[
                ValidationResponse(name="v1", status="fail", reason="warn"),
            ],
        )
        result._build_summary(validations)
        assert result.passed is True  # warnings don't block
        assert result.summary == "0/1 passed, 0 error(s), 1 warning(s)"

    def test_build_summary_non_pass_status(self) -> None:
        """Any non-'pass' status counts as failure."""
        validations = [
            Validation(name="v1", severity=Severity.ERROR),
        ]
        result = ValidationSuiteResult(
            target="feat",
            results=[
                ValidationResponse(name="v1", status="error", reason="something"),
            ],
        )
        result._build_summary(validations)
        assert result.passed is False
        assert "1 error(s)" in result.summary


# ---------------------------------------------------------------------------
# AgentValidationRunner tests
# ---------------------------------------------------------------------------


class TestAgentValidationRunner:
    def test_run_calls_agent_validate(self) -> None:
        mock_agent = MockAgent(
            validation_response=ValidationResponse(
                name="check-x", status="pass", reason="looks good"
            )
        )
        runner = AgentValidationRunner(mock_agent)
        assert runner.type() == "agent_validation"

        validation = Validation(name="check-x", args={"rubric": "check x"})
        ctx = ValidationContext(
            project_intent=ProjectIntent(name="proj", body="body"),
            implementation=None,
            feature_intent=IntentFile(name="feat", body="feat body"),
            output_dir="/tmp/out",
            response_file_path="/tmp/resp.json",
        )
        resp = runner.run(validation, ctx)

        assert resp.status == "pass"
        assert resp.name == "check-x"
        assert len(mock_agent.validate_calls) == 1

        # Check that BuildContext was constructed correctly
        build_ctx, val = mock_agent.validate_calls[0]
        assert build_ctx.validations == []
        assert build_ctx.dependency_names == []
        assert build_ctx.generation_id.startswith("val-")
        assert build_ctx.output_dir == "/tmp/out"
        assert val is validation

    def test_run_agent_error_returns_fail(self) -> None:
        """If agent raises, runner returns a failure response."""
        from intentc.build.agents import AgentError

        class FailingAgent(MockAgent):
            def validate(self, ctx, validation):
                raise AgentError("boom")

        runner = AgentValidationRunner(FailingAgent())
        validation = Validation(name="will-fail")
        ctx = ValidationContext(
            project_intent=ProjectIntent(name="p", body=""),
            implementation=None,
            feature_intent=IntentFile(name="f", body=""),
            output_dir="/tmp",
            response_file_path="/tmp/r.json",
        )
        resp = runner.run(validation, ctx)
        assert resp.status == "fail"
        assert "boom" in resp.reason


# ---------------------------------------------------------------------------
# ValidationSuite lifecycle tests
# ---------------------------------------------------------------------------


class TestValidationSuiteLifecycle:
    def test_validate_feature_runs_icv_entries(self) -> None:
        """ValidateFeature loads .icv files, runs each entry, returns result."""
        validations = [
            Validation(name="v1", args={"rubric": "check 1"}),
            Validation(name="v2", severity=Severity.WARNING, args={"rubric": "check 2"}),
        ]
        node = FeatureNode(
            path="feat/a",
            intents=[IntentFile(name="feat-a", body="do something")],
            validations=[ValidationFile(target="feat/a", validations=validations)],
        )
        project = _make_project(features={"feat/a": node})

        mock_agent = MockAgent()
        # Override create_from_profile to return our mock
        import intentc.build.validations as val_mod
        original = val_mod.create_from_profile

        def mock_create(profile):
            return mock_agent

        val_mod.create_from_profile = mock_create
        try:
            suite = ValidationSuite(
                project=project,
                agent_profile=_mock_profile(),
                output_dir="/tmp/out",
            )
            result = suite.validate_feature("feat/a")
        finally:
            val_mod.create_from_profile = original

        assert result.target == "feat/a"
        assert len(result.results) == 2
        assert result.passed is True
        assert "2/2 passed" in result.summary
        # Agent was called twice
        assert len(mock_agent.validate_calls) == 2

    def test_validate_feature_unknown_returns_empty(self) -> None:
        """Unknown feature returns an empty passing result."""
        project = _make_project()

        mock_agent = MockAgent()
        import intentc.build.validations as val_mod
        original = val_mod.create_from_profile
        val_mod.create_from_profile = lambda p: mock_agent
        try:
            suite = ValidationSuite(
                project=project,
                agent_profile=_mock_profile(),
                output_dir="/tmp/out",
            )
            result = suite.validate_feature("nonexistent")
        finally:
            val_mod.create_from_profile = original

        assert result.passed is True
        assert len(result.results) == 0

    def test_validate_project_topological_order(self) -> None:
        """ValidateProject runs features in topological order."""
        node_a = FeatureNode(
            path="a",
            intents=[IntentFile(name="a", body="a body")],
            validations=[
                ValidationFile(
                    target="a",
                    validations=[Validation(name="va")],
                )
            ],
        )
        node_b = FeatureNode(
            path="b",
            intents=[IntentFile(name="b", body="b body", depends_on=["a"])],
            validations=[
                ValidationFile(
                    target="b",
                    validations=[Validation(name="vb")],
                )
            ],
        )
        project = _make_project(features={"a": node_a, "b": node_b})

        mock_agent = MockAgent()
        import intentc.build.validations as val_mod
        original = val_mod.create_from_profile
        val_mod.create_from_profile = lambda p: mock_agent
        try:
            suite = ValidationSuite(
                project=project,
                agent_profile=_mock_profile(),
                output_dir="/tmp/out",
            )
            results = suite.validate_project()
        finally:
            val_mod.create_from_profile = original

        # Should have 2 results (one per feature), in topological order
        assert len(results) == 2
        targets = [r.target for r in results]
        assert targets.index("a") < targets.index("b")

    def test_validate_project_includes_assertions(self) -> None:
        """ValidateProject includes project-level assertions."""
        assertions = [
            ValidationFile(
                target="project",
                validations=[Validation(name="proj-check")],
            )
        ]
        project = _make_project(assertions=assertions)

        mock_agent = MockAgent()
        import intentc.build.validations as val_mod
        original = val_mod.create_from_profile
        val_mod.create_from_profile = lambda p: mock_agent
        try:
            suite = ValidationSuite(
                project=project,
                agent_profile=_mock_profile(),
                output_dir="/tmp/out",
            )
            results = suite.validate_project()
        finally:
            val_mod.create_from_profile = original

        # Should have 1 result for assertions
        assert len(results) == 1
        assert results[0].target == "project"

    def test_validate_entries_directly(self) -> None:
        """ValidateEntries accepts an arbitrary list of entries."""
        entries = [
            Validation(name="e1", args={"rubric": "r1"}),
            Validation(name="e2", args={"rubric": "r2"}),
        ]
        project = _make_project()

        mock_agent = MockAgent()
        import intentc.build.validations as val_mod
        original = val_mod.create_from_profile
        val_mod.create_from_profile = lambda p: mock_agent
        try:
            suite = ValidationSuite(
                project=project,
                agent_profile=_mock_profile(),
                output_dir="/tmp/out",
            )
            result = suite.validate_entries("custom-target", entries)
        finally:
            val_mod.create_from_profile = original

        assert result.target == "custom-target"
        assert len(result.results) == 2
        assert result.passed is True

    def test_severity_rollup_error_blocks(self) -> None:
        """Error-severity failure sets passed=False."""
        mock_agent = MockAgent(
            validation_response=ValidationResponse(
                name="x", status="fail", reason="bad"
            )
        )
        entries = [
            Validation(name="x", severity=Severity.ERROR),
        ]
        project = _make_project()

        import intentc.build.validations as val_mod
        original = val_mod.create_from_profile
        val_mod.create_from_profile = lambda p: mock_agent
        try:
            suite = ValidationSuite(
                project=project,
                agent_profile=_mock_profile(),
                output_dir="/tmp/out",
            )
            result = suite.validate_entries("t", entries)
        finally:
            val_mod.create_from_profile = original

        assert result.passed is False
        assert "1 error(s)" in result.summary

    def test_severity_rollup_warning_does_not_block(self) -> None:
        """Warning-severity failure does NOT set passed=False."""
        mock_agent = MockAgent(
            validation_response=ValidationResponse(
                name="w", status="fail", reason="warn"
            )
        )
        entries = [
            Validation(name="w", severity=Severity.WARNING),
        ]
        project = _make_project()

        import intentc.build.validations as val_mod
        original = val_mod.create_from_profile
        val_mod.create_from_profile = lambda p: mock_agent
        try:
            suite = ValidationSuite(
                project=project,
                agent_profile=_mock_profile(),
                output_dir="/tmp/out",
            )
            result = suite.validate_entries("t", entries)
        finally:
            val_mod.create_from_profile = original

        assert result.passed is True
        assert "1 warning(s)" in result.summary

    def test_feature_intent_resolution_project(self) -> None:
        """For target 'project', feature intent uses project body."""
        mock_agent = MockAgent()
        project = _make_project()

        import intentc.build.validations as val_mod
        original = val_mod.create_from_profile
        val_mod.create_from_profile = lambda p: mock_agent
        try:
            suite = ValidationSuite(
                project=project,
                agent_profile=_mock_profile(),
                output_dir="/tmp/out",
            )
            entries = [Validation(name="pc")]
            suite.validate_entries("project", entries)
        finally:
            val_mod.create_from_profile = original

        # Check the BuildContext intent
        build_ctx, _ = mock_agent.validate_calls[0]
        assert build_ctx.intent.name == "project"
        assert build_ctx.intent.body == "A test project."

    def test_feature_intent_resolution_known_feature(self) -> None:
        """For a known feature, uses the first intent."""
        node = FeatureNode(
            path="f",
            intents=[IntentFile(name="f", body="feature body")],
            validations=[],
        )
        project = _make_project(features={"f": node})
        mock_agent = MockAgent()

        import intentc.build.validations as val_mod
        original = val_mod.create_from_profile
        val_mod.create_from_profile = lambda p: mock_agent
        try:
            suite = ValidationSuite(
                project=project,
                agent_profile=_mock_profile(),
                output_dir="/tmp/out",
            )
            suite.validate_entries("f", [Validation(name="x")])
        finally:
            val_mod.create_from_profile = original

        build_ctx, _ = mock_agent.validate_calls[0]
        assert build_ctx.intent.name == "f"
        assert build_ctx.intent.body == "feature body"

    def test_feature_intent_resolution_unknown(self) -> None:
        """For an unknown target, creates empty IntentFile."""
        project = _make_project()
        mock_agent = MockAgent()

        import intentc.build.validations as val_mod
        original = val_mod.create_from_profile
        val_mod.create_from_profile = lambda p: mock_agent
        try:
            suite = ValidationSuite(
                project=project,
                agent_profile=_mock_profile(),
                output_dir="/tmp/out",
            )
            suite.validate_entries("unknown", [Validation(name="x")])
        finally:
            val_mod.create_from_profile = original

        build_ctx, _ = mock_agent.validate_calls[0]
        assert build_ctx.intent.name == "unknown"
        assert build_ctx.intent.body == ""


# ---------------------------------------------------------------------------
# Runner registry tests
# ---------------------------------------------------------------------------


class TestRunnerRegistry:
    def test_default_agent_validation_runner(self) -> None:
        """AgentValidationRunner is registered by default."""
        mock_agent = MockAgent()
        import intentc.build.validations as val_mod
        original = val_mod.create_from_profile
        val_mod.create_from_profile = lambda p: mock_agent
        try:
            suite = ValidationSuite(
                project=_make_project(),
                agent_profile=_mock_profile(),
                output_dir="/tmp/out",
            )
            entries = [Validation(name="av", type=ValidationType.AGENT_VALIDATION)]
            result = suite.validate_entries("t", entries)
        finally:
            val_mod.create_from_profile = original

        assert len(result.results) == 1
        assert result.results[0].status == "pass"
        assert len(mock_agent.validate_calls) == 1

    def test_custom_runner_dispatched(self) -> None:
        """A custom runner is dispatched to when type matches."""
        custom = _CountingRunner("custom_check", status="pass", reason="custom ok")

        mock_agent = MockAgent()
        import intentc.build.validations as val_mod
        original = val_mod.create_from_profile
        val_mod.create_from_profile = lambda p: mock_agent
        try:
            suite = ValidationSuite(
                project=_make_project(),
                agent_profile=_mock_profile(),
                output_dir="/tmp/out",
                runner_registry={"custom_check": custom},
            )
            entries = [
                Validation(name="cc", type="custom_check"),  # type: ignore[arg-type]
            ]
            result = suite.validate_entries("t", entries)
        finally:
            val_mod.create_from_profile = original

        assert len(result.results) == 1
        assert result.results[0].status == "pass"
        assert result.results[0].reason == "custom ok"
        assert len(custom.calls) == 1
        # Agent should NOT have been called
        assert len(mock_agent.validate_calls) == 0

    def test_register_runner_post_construction(self) -> None:
        """register_runner adds a runner after construction."""
        custom = _CountingRunner("late_check")

        mock_agent = MockAgent()
        import intentc.build.validations as val_mod
        original = val_mod.create_from_profile
        val_mod.create_from_profile = lambda p: mock_agent
        try:
            suite = ValidationSuite(
                project=_make_project(),
                agent_profile=_mock_profile(),
                output_dir="/tmp/out",
            )
            suite.register_runner(custom)
            entries = [Validation(name="lc", type="late_check")]  # type: ignore[arg-type]
            result = suite.validate_entries("t", entries)
        finally:
            val_mod.create_from_profile = original

        assert len(custom.calls) == 1
        assert result.results[0].status == "pass"

    def test_unknown_type_fails_with_error(self) -> None:
        """A validation entry with an unknown type fails with descriptive error."""
        mock_agent = MockAgent()
        import intentc.build.validations as val_mod
        original = val_mod.create_from_profile
        val_mod.create_from_profile = lambda p: mock_agent
        try:
            suite = ValidationSuite(
                project=_make_project(),
                agent_profile=_mock_profile(),
                output_dir="/tmp/out",
            )
            entries = [
                Validation(name="bad", type="nonexistent_type"),  # type: ignore[arg-type]
            ]
            result = suite.validate_entries("t", entries)
        finally:
            val_mod.create_from_profile = original

        assert len(result.results) == 1
        assert result.results[0].status == "fail"
        assert "nonexistent_type" in result.results[0].reason
        assert "no runner registered" in result.results[0].reason
