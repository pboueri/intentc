"""Tests for the validation suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from intentc.build.agents import (
    AgentProfile,
    BuildContext,
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
from intentc.core.project import FeatureNode, Project
from intentc.core.types import (
    IntentFile,
    ProjectIntent,
    Implementation,
    Severity,
    Validation,
    ValidationFile,
    ValidationType,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_profile(**overrides) -> AgentProfile:
    defaults = dict(name="test-agent", provider="cli", command="echo")
    defaults.update(overrides)
    return AgentProfile(**defaults)


def _make_validation(
    name: str = "check-1",
    severity: Severity = Severity.ERROR,
    **overrides,
) -> Validation:
    defaults = dict(
        name=name,
        type=ValidationType.AGENT_VALIDATION,
        severity=severity,
        args={"rubric": "Check something"},
    )
    defaults.update(overrides)
    return Validation(**defaults)


def _make_project(
    features: dict[str, FeatureNode] | None = None,
    assertions: list[ValidationFile] | None = None,
) -> Project:
    return Project(
        project_intent=ProjectIntent(name="test-project", body="# Test Project"),
        implementation=Implementation(name="impl", body="# Implementation\nPython 3.11"),
        features=features or {},
        assertions=assertions or [],
    )


def _simple_project() -> Project:
    """A project with two features: a -> b, each with validations."""
    return _make_project(
        features={
            "feat/a": FeatureNode(
                path="feat/a",
                intents=[IntentFile(name="a", body="# Feature A")],
                validations=[
                    ValidationFile(
                        target="feat/a",
                        validations=[
                            _make_validation(name="a-check-1"),
                            _make_validation(name="a-check-2"),
                        ],
                    ),
                ],
            ),
            "feat/b": FeatureNode(
                path="feat/b",
                intents=[IntentFile(name="b", body="# Feature B", depends_on=["feat/a"])],
                validations=[
                    ValidationFile(
                        target="feat/b",
                        validations=[_make_validation(name="b-check-1")],
                    ),
                ],
            ),
        },
    )


# ---------------------------------------------------------------------------
# ValidationContext
# ---------------------------------------------------------------------------


class TestValidationContext:
    def test_construction(self, tmp_path: Path):
        ctx = ValidationContext(
            project_intent=ProjectIntent(name="p", body="proj"),
            implementation=Implementation(name="impl", body="impl"),
            feature_intent=IntentFile(name="feat", body="feat body"),
            output_dir=str(tmp_path),
            response_file_path=str(tmp_path / "resp.json"),
        )
        assert ctx.project_intent.name == "p"
        assert ctx.feature_intent.name == "feat"
        assert ctx.output_dir == str(tmp_path)

    def test_optional_implementation(self, tmp_path: Path):
        ctx = ValidationContext(
            project_intent=ProjectIntent(name="p", body="proj"),
            implementation=None,
            feature_intent=IntentFile(name="feat", body=""),
            output_dir=str(tmp_path),
            response_file_path=str(tmp_path / "resp.json"),
        )
        assert ctx.implementation is None


# ---------------------------------------------------------------------------
# ValidationSuiteResult
# ---------------------------------------------------------------------------


class TestValidationSuiteResult:
    def test_construction(self):
        result = ValidationSuiteResult(
            target="feat/a",
            results=[
                ValidationResponse(name="c1", status="pass", reason="ok"),
                ValidationResponse(name="c2", status="fail", reason="bad"),
            ],
            passed=False,
            summary="1/2 passed, 1 error(s), 0 warning(s)",
        )
        assert result.target == "feat/a"
        assert len(result.results) == 2
        assert result.passed is False

    def test_defaults(self):
        result = ValidationSuiteResult(target="x")
        assert result.results == []
        assert result.passed is True
        assert result.summary == ""


# ---------------------------------------------------------------------------
# ValidationRunner interface
# ---------------------------------------------------------------------------


class TestValidationRunnerInterface:
    def test_is_abstract(self):
        with pytest.raises(TypeError):
            ValidationRunner()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# AgentValidationRunner
# ---------------------------------------------------------------------------


class TestAgentValidationRunner:
    def test_delegates_to_agent(self, tmp_path: Path):
        agent = MockAgent(
            validation_response=ValidationResponse(
                name="placeholder", status="pass", reason="All good"
            )
        )
        runner = AgentValidationRunner(agent)
        assert runner.type() == "agent_validation"

        v = _make_validation(name="my-check")
        ctx = ValidationContext(
            project_intent=ProjectIntent(name="p", body=""),
            feature_intent=IntentFile(name="f", body=""),
            output_dir=str(tmp_path),
            response_file_path=str(tmp_path / "resp.json"),
        )
        resp = runner.run(v, ctx)

        assert resp.status == "pass"
        assert resp.name == "my-check"
        assert len(agent.validate_calls) == 1

    def test_agent_error_returns_failure(self, tmp_path: Path):
        """If the agent raises AgentError, the runner returns a fail response."""
        from intentc.build.agents import AgentError

        agent = MockAgent()
        runner = AgentValidationRunner(agent)

        # Make the agent raise on validate
        def bad_validate(ctx, v):
            raise AgentError("broken")

        agent.validate = bad_validate  # type: ignore[assignment]

        v = _make_validation(name="err-check")
        ctx = ValidationContext(
            project_intent=ProjectIntent(name="p", body=""),
            feature_intent=IntentFile(name="f", body=""),
            output_dir=str(tmp_path),
            response_file_path=str(tmp_path / "resp.json"),
        )
        resp = runner.run(v, ctx)

        assert resp.status == "fail"
        assert resp.name == "err-check"
        assert "Agent error" in resp.reason


# ---------------------------------------------------------------------------
# ValidationSuite — lifecycle
# ---------------------------------------------------------------------------


class TestValidationSuiteLifecycle:
    def test_construction(self, tmp_path: Path):
        """Suite is constructed from Project, AgentProfile, output dir, and runner registry."""
        project = _simple_project()
        profile = _make_profile()
        suite = ValidationSuite(project, profile, str(tmp_path))

        # Agent is created internally — we can verify via the runner registry
        assert "agent_validation" in suite._runners

    def test_validate_feature(self, tmp_path: Path):
        """ValidateFeature loads .icv files and returns a ValidationSuiteResult."""
        project = _simple_project()
        profile = _make_profile()

        # Use a mock agent by monkey-patching create_from_profile
        mock_agent = MockAgent()
        import intentc.build.validations as val_mod
        original = val_mod.create_from_profile

        def mock_create(p):
            return mock_agent

        val_mod.create_from_profile = mock_create
        try:
            suite = ValidationSuite(project, profile, str(tmp_path))
            result = suite.validate_feature("feat/a")

            assert isinstance(result, ValidationSuiteResult)
            assert result.target == "feat/a"
            assert len(result.results) == 2
            assert result.results[0].name == "a-check-1"
            assert result.results[1].name == "a-check-2"
            assert result.passed is True
            assert len(mock_agent.validate_calls) == 2
        finally:
            val_mod.create_from_profile = original

    def test_validate_feature_unknown_raises(self, tmp_path: Path):
        """ValidateFeature with unknown feature raises KeyError."""
        project = _simple_project()
        profile = _make_profile()

        mock_agent = MockAgent()
        import intentc.build.validations as val_mod
        original = val_mod.create_from_profile
        val_mod.create_from_profile = lambda p: mock_agent
        try:
            suite = ValidationSuite(project, profile, str(tmp_path))
            with pytest.raises(KeyError, match="not found"):
                suite.validate_feature("nonexistent")
        finally:
            val_mod.create_from_profile = original

    def test_validate_project_topological_order(self, tmp_path: Path):
        """ValidateProject runs all features in topological order plus assertions."""
        assertions = [
            ValidationFile(
                target="project",
                validations=[_make_validation(name="assert-1")],
            ),
        ]
        project = _make_project(
            features={
                "feat/a": FeatureNode(
                    path="feat/a",
                    intents=[IntentFile(name="a", body="# A")],
                    validations=[
                        ValidationFile(
                            target="feat/a",
                            validations=[_make_validation(name="a-check")],
                        ),
                    ],
                ),
                "feat/b": FeatureNode(
                    path="feat/b",
                    intents=[IntentFile(name="b", body="# B", depends_on=["feat/a"])],
                    validations=[
                        ValidationFile(
                            target="feat/b",
                            validations=[_make_validation(name="b-check")],
                        ),
                    ],
                ),
            },
            assertions=assertions,
        )

        mock_agent = MockAgent()
        import intentc.build.validations as val_mod
        original = val_mod.create_from_profile
        val_mod.create_from_profile = lambda p: mock_agent
        try:
            suite = ValidationSuite(project, _make_profile(), str(tmp_path))
            results = suite.validate_project()

            # feat/a first (no deps), then feat/b, then project assertions
            assert len(results) == 3
            assert results[0].target == "feat/a"
            assert results[1].target == "feat/b"
            assert results[2].target == "project"
            assert results[2].results[0].name == "assert-1"
        finally:
            val_mod.create_from_profile = original

    def test_validate_entries(self, tmp_path: Path):
        """ValidateEntries accepts an arbitrary list of entries for a target."""
        project = _simple_project()
        mock_agent = MockAgent()

        import intentc.build.validations as val_mod
        original = val_mod.create_from_profile
        val_mod.create_from_profile = lambda p: mock_agent
        try:
            suite = ValidationSuite(project, _make_profile(), str(tmp_path))

            entries = [
                _make_validation(name="custom-1"),
                _make_validation(name="custom-2"),
            ]
            result = suite.validate_entries("feat/a", entries)

            assert result.target == "feat/a"
            assert len(result.results) == 2
            assert result.results[0].name == "custom-1"
            assert result.results[1].name == "custom-2"
        finally:
            val_mod.create_from_profile = original

    def test_severity_rollup_error_blocks(self, tmp_path: Path):
        """Error-severity failure causes passed=False."""
        project = _simple_project()

        fail_agent = MockAgent(
            validation_response=ValidationResponse(
                name="x", status="fail", reason="Failed"
            )
        )

        import intentc.build.validations as val_mod
        original = val_mod.create_from_profile
        val_mod.create_from_profile = lambda p: fail_agent
        try:
            suite = ValidationSuite(project, _make_profile(), str(tmp_path))

            entries = [_make_validation(name="err-check", severity=Severity.ERROR)]
            result = suite.validate_entries("feat/a", entries)

            assert result.passed is False
            assert "1 error" in result.summary
        finally:
            val_mod.create_from_profile = original

    def test_severity_rollup_warning_does_not_block(self, tmp_path: Path):
        """Warning-severity failure does NOT cause passed=False."""
        project = _simple_project()

        fail_agent = MockAgent(
            validation_response=ValidationResponse(
                name="x", status="fail", reason="Not great"
            )
        )

        import intentc.build.validations as val_mod
        original = val_mod.create_from_profile
        val_mod.create_from_profile = lambda p: fail_agent
        try:
            suite = ValidationSuite(project, _make_profile(), str(tmp_path))

            entries = [_make_validation(name="warn-check", severity=Severity.WARNING)]
            result = suite.validate_entries("feat/a", entries)

            assert result.passed is True
            assert "1 warning" in result.summary
        finally:
            val_mod.create_from_profile = original

    def test_mixed_severity(self, tmp_path: Path):
        """Mix of error pass + warning fail => passed=True."""
        project = _simple_project()
        mock_agent = MockAgent()

        # Make agent fail only for warning-severity checks
        call_count = [0]
        original_validate = mock_agent.validate

        def selective_validate(ctx, v):
            call_count[0] += 1
            if v.severity == Severity.WARNING:
                return ValidationResponse(name=v.name, status="fail", reason="warn fail")
            return ValidationResponse(name=v.name, status="pass", reason="ok")

        mock_agent.validate = selective_validate  # type: ignore[assignment]

        import intentc.build.validations as val_mod
        original_create = val_mod.create_from_profile
        val_mod.create_from_profile = lambda p: mock_agent
        try:
            suite = ValidationSuite(project, _make_profile(), str(tmp_path))

            entries = [
                _make_validation(name="err-ok", severity=Severity.ERROR),
                _make_validation(name="warn-bad", severity=Severity.WARNING),
            ]
            result = suite.validate_entries("feat/a", entries)

            assert result.passed is True
            assert result.results[0].status == "pass"
            assert result.results[1].status == "fail"
        finally:
            val_mod.create_from_profile = original_create

    def test_no_validations_returns_passed(self, tmp_path: Path):
        """Feature with no .icv entries returns passed=True with empty results."""
        project = _make_project(
            features={
                "feat/empty": FeatureNode(
                    path="feat/empty",
                    intents=[IntentFile(name="empty", body="")],
                    validations=[],
                ),
            },
        )

        mock_agent = MockAgent()
        import intentc.build.validations as val_mod
        original = val_mod.create_from_profile
        val_mod.create_from_profile = lambda p: mock_agent
        try:
            suite = ValidationSuite(project, _make_profile(), str(tmp_path))
            result = suite.validate_feature("feat/empty")

            assert result.passed is True
            assert result.results == []
        finally:
            val_mod.create_from_profile = original


# ---------------------------------------------------------------------------
# Runner registry extensibility
# ---------------------------------------------------------------------------


class _MockRunner(ValidationRunner):
    """A custom runner for testing the registry."""

    def __init__(self, type_name: str = "custom_check"):
        self._type = type_name
        self.calls: list[tuple[Validation, ValidationContext]] = []

    def run(self, validation: Validation, ctx: ValidationContext) -> ValidationResponse:
        self.calls.append((validation, ctx))
        return ValidationResponse(
            name=validation.name,
            status="pass",
            reason=f"Custom runner handled {validation.name}",
        )

    def type(self) -> str:
        return self._type


class TestRunnerRegistryExtensibility:
    def test_agent_runner_registered_by_default(self, tmp_path: Path):
        """AgentValidationRunner is registered by default for 'agent_validation'."""
        project = _make_project()
        mock_agent = MockAgent()

        import intentc.build.validations as val_mod
        original = val_mod.create_from_profile
        val_mod.create_from_profile = lambda p: mock_agent
        try:
            suite = ValidationSuite(project, _make_profile(), str(tmp_path))
            assert "agent_validation" in suite._runners
            assert isinstance(suite._runners["agent_validation"], AgentValidationRunner)
        finally:
            val_mod.create_from_profile = original

    def test_custom_runner_dispatched(self, tmp_path: Path):
        """A custom runner registered for a type is invoked for matching entries."""
        project = _make_project(
            features={
                "feat/a": FeatureNode(
                    path="feat/a",
                    intents=[IntentFile(name="a", body="")],
                    validations=[],
                ),
            },
        )
        mock_agent = MockAgent()
        custom_runner = _MockRunner(type_name="custom_check")

        import intentc.build.validations as val_mod
        original = val_mod.create_from_profile
        val_mod.create_from_profile = lambda p: mock_agent
        try:
            suite = ValidationSuite(
                project,
                _make_profile(),
                str(tmp_path),
                runner_registry={"custom_check": custom_runner},
            )

            entries = [
                Validation(
                    name="my-custom",
                    type=ValidationType.FILE_CHECK,  # We'll use a type whose .value matches
                    severity=Severity.ERROR,
                    args={},
                ),
            ]
            # Override type value to match our custom runner
            # Since ValidationType is an enum, we need to use a type that exists
            # or register for an existing type string.
            # Better approach: register runner for "file_check" and use FILE_CHECK type
            suite._runners["file_check"] = custom_runner

            entries = [
                Validation(
                    name="my-custom",
                    type=ValidationType.FILE_CHECK,
                    severity=Severity.ERROR,
                    args={"path": "some/file.py"},
                ),
            ]
            result = suite.validate_entries("feat/a", entries)

            assert len(custom_runner.calls) == 1
            assert custom_runner.calls[0][0].name == "my-custom"
            assert result.results[0].status == "pass"
            assert result.passed is True
        finally:
            val_mod.create_from_profile = original

    def test_register_runner_method(self, tmp_path: Path):
        """Runners can be registered after construction via register_runner."""
        project = _make_project(
            features={
                "feat/a": FeatureNode(
                    path="feat/a",
                    intents=[IntentFile(name="a", body="")],
                    validations=[],
                ),
            },
        )
        mock_agent = MockAgent()
        custom_runner = _MockRunner(type_name="llm_judge")

        import intentc.build.validations as val_mod
        original = val_mod.create_from_profile
        val_mod.create_from_profile = lambda p: mock_agent
        try:
            suite = ValidationSuite(project, _make_profile(), str(tmp_path))
            suite.register_runner(custom_runner)

            entries = [
                Validation(
                    name="judge-check",
                    type=ValidationType.LLM_JUDGE,
                    severity=Severity.ERROR,
                    args={},
                ),
            ]
            result = suite.validate_entries("feat/a", entries)

            assert len(custom_runner.calls) == 1
            assert result.results[0].name == "judge-check"
        finally:
            val_mod.create_from_profile = original

    def test_unknown_type_fails_with_descriptive_error(self, tmp_path: Path):
        """A validation entry with an unregistered type fails with a useful message."""
        project = _make_project(
            features={
                "feat/a": FeatureNode(
                    path="feat/a",
                    intents=[IntentFile(name="a", body="")],
                    validations=[],
                ),
            },
        )
        mock_agent = MockAgent()

        import intentc.build.validations as val_mod
        original = val_mod.create_from_profile
        val_mod.create_from_profile = lambda p: mock_agent
        try:
            suite = ValidationSuite(project, _make_profile(), str(tmp_path))

            entries = [
                Validation(
                    name="bad-type-check",
                    type=ValidationType.COMMAND_CHECK,
                    severity=Severity.ERROR,
                    args={},
                ),
            ]
            result = suite.validate_entries("feat/a", entries)

            assert result.passed is False
            assert result.results[0].status == "fail"
            assert "command_check" in result.results[0].reason
            assert "No runner registered" in result.results[0].reason
        finally:
            val_mod.create_from_profile = original

    def test_constructor_registry_overrides_default(self, tmp_path: Path):
        """A runner passed via constructor overrides the default for that type."""
        project = _make_project()
        mock_agent = MockAgent()
        custom_agent_runner = _MockRunner(type_name="agent_validation")

        import intentc.build.validations as val_mod
        original = val_mod.create_from_profile
        val_mod.create_from_profile = lambda p: mock_agent
        try:
            suite = ValidationSuite(
                project,
                _make_profile(),
                str(tmp_path),
                runner_registry={"agent_validation": custom_agent_runner},
            )

            # The custom runner should have replaced the default
            assert suite._runners["agent_validation"] is custom_agent_runner
        finally:
            val_mod.create_from_profile = original
