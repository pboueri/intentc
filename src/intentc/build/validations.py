"""Validation suite — runs validations against built features and the project."""

from __future__ import annotations

import abc
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from pydantic import BaseModel

from intentc.build.agents import (
    AgentError,
    AgentProfile,
    BuildContext,
    ValidationResponse,
    create_from_profile,
)
from intentc.core.project import Project
from intentc.core.types import (
    Implementation,
    IntentFile,
    ProjectIntent,
    Severity,
    Validation,
    ValidationFile,
)


# ---------------------------------------------------------------------------
# ValidationContext
# ---------------------------------------------------------------------------


class ValidationContext(BaseModel):
    """What a runner needs to evaluate a validation."""

    model_config = {"extra": "ignore"}

    project_intent: ProjectIntent
    implementation: Implementation | None = None
    feature_intent: IntentFile
    output_dir: str
    response_file_path: str


# ---------------------------------------------------------------------------
# ValidationSuiteResult
# ---------------------------------------------------------------------------


class ValidationSuiteResult(BaseModel):
    """The result of running a suite of validations against a target."""

    model_config = {"extra": "ignore"}

    target: str
    results: list[ValidationResponse] = []
    passed: bool = True
    summary: str = ""


# ---------------------------------------------------------------------------
# ValidationRunner interface
# ---------------------------------------------------------------------------


class ValidationRunner(abc.ABC):
    """Interface for validation evaluation strategies."""

    @abc.abstractmethod
    def run(self, validation: Validation, ctx: ValidationContext) -> ValidationResponse:
        """Evaluate a single validation entry and return the response."""

    @abc.abstractmethod
    def type(self) -> str:
        """Return the validation type this runner handles."""


# ---------------------------------------------------------------------------
# AgentValidationRunner
# ---------------------------------------------------------------------------


class AgentValidationRunner(ValidationRunner):
    """Built-in runner for type 'agent_validation'.

    Accepts a default AgentProfile and creates agents on demand per validation,
    merging any per-validation agent_profile override from the Validation entry.
    This allows each validation to run with its own agent configuration while
    sharing the suite-level default.
    """

    def __init__(self, default_profile: AgentProfile) -> None:
        self._default_profile = default_profile

    def run(self, validation: Validation, ctx: ValidationContext) -> ValidationResponse:
        profile = self._resolve_profile(validation)
        agent = create_from_profile(profile)
        build_ctx = BuildContext(
            intent=ctx.feature_intent,
            validations=[],
            output_dir=ctx.output_dir,
            generation_id=f"val-{uuid.uuid4().hex[:8]}",
            dependency_names=[],
            project_intent=ctx.project_intent,
            implementation=ctx.implementation,
            response_file_path=ctx.response_file_path,
        )
        try:
            return agent.validate(build_ctx, validation)
        except AgentError as exc:
            return ValidationResponse(
                name=validation.name,
                status="fail",
                reason=f"Agent error: {exc}",
            )
        except Exception as exc:
            return ValidationResponse(
                name=validation.name,
                status="fail",
                reason=f"Unexpected error: {exc}",
            )

    def _resolve_profile(self, validation: Validation) -> AgentProfile:
        """Return the effective AgentProfile for this validation.

        If the validation has an agent_profile override, merge its fields
        on top of the default profile. Otherwise return the default as-is.
        """
        override = validation.agent_profile
        if override is None:
            return self._default_profile
        return self._default_profile.model_copy(update={
            k: v for k, v in {
                "provider": override.provider,
                "model_id": override.model_id,
                "timeout": override.timeout,
            }.items()
            if v is not None
        })

    def type(self) -> str:
        return "agent_validation"


# ---------------------------------------------------------------------------
# ValidationSuite
# ---------------------------------------------------------------------------


def _build_summary(results: list[ValidationResponse], entries: list[Validation]) -> str:
    """Build a human-readable summary from results and their severity."""
    severity_map = {v.name: v.severity for v in entries}
    total = len(results)
    passed = sum(1 for r in results if r.status == "pass")
    errors = sum(
        1 for r in results
        if r.status != "pass" and severity_map.get(r.name, Severity.ERROR) == Severity.ERROR
    )
    warnings = sum(
        1 for r in results
        if r.status != "pass" and severity_map.get(r.name, Severity.ERROR) == Severity.WARNING
    )
    return f"{passed}/{total} passed, {errors} error(s), {warnings} warning(s)"


class ValidationSuite:
    """Core orchestrator for running validations.

    Takes a project, an agent profile, and an output directory. Can validate
    a specific feature or the entire project. Validations within a single
    target run concurrently up to *max_workers* at a time.
    """

    def __init__(
        self,
        project: Project,
        agent_profile: AgentProfile,
        output_dir: str,
        runner_registry: dict[str, ValidationRunner] | None = None,
        max_workers: int = 5,
    ) -> None:
        self._project = project
        self._agent_profile = agent_profile
        self._output_dir = output_dir
        self._max_workers = max_workers

        # Initialize registry with the built-in agent runner
        self._runners: dict[str, ValidationRunner] = {}
        agent_runner = AgentValidationRunner(agent_profile)
        self._runners[agent_runner.type()] = agent_runner

        # Merge caller-provided runners (can override defaults)
        if runner_registry:
            self._runners.update(runner_registry)

    def register_runner(self, runner: ValidationRunner) -> None:
        """Register a custom validation runner."""
        self._runners[runner.type()] = runner

    def validate_feature(self, feature: str) -> ValidationSuiteResult:
        """Validate a specific feature by loading its .icv files and running each entry."""
        self._project._require_feature(feature)
        node = self._project.features[feature]

        all_entries: list[Validation] = []
        for vf in node.validations:
            all_entries.extend(vf.validations)

        return self.validate_entries(feature, all_entries)

    def validate_project(self) -> list[ValidationSuiteResult]:
        """Validate all features in topological order plus project-level assertions."""
        results: list[ValidationSuiteResult] = []

        for feature_path in self._project.topological_order():
            result = self.validate_feature(feature_path)
            results.append(result)

        # Project-level assertions
        if self._project.assertions:
            assertion_entries: list[Validation] = []
            for vf in self._project.assertions:
                assertion_entries.extend(vf.validations)
            if assertion_entries:
                result = self._validate_assertions(assertion_entries)
                results.append(result)

        return results

    def validate_entries(
        self, target: str, entries: list[Validation]
    ) -> ValidationSuiteResult:
        """Run a list of validation entries against a target concurrently.

        All entries for the same target are submitted to a thread pool and
        run in parallel (up to *max_workers*). Result order matches input order.
        """
        if not entries:
            return ValidationSuiteResult(
                target=target,
                results=[],
                passed=True,
                summary=_build_summary([], []),
            )

        feature_intent = self._resolve_feature_intent(target)

        def _run_one(entry: Validation) -> ValidationResponse:
            runner = self._runners.get(entry.type.value)
            if runner is None:
                return ValidationResponse(
                    name=entry.name,
                    status="fail",
                    reason=(
                        f"No runner registered for validation type '{entry.type.value}'. "
                        f"Registered types: {', '.join(sorted(self._runners))}"
                    ),
                )
            intentc_dir = Path(".intentc") / self._output_dir
            intentc_dir.mkdir(parents=True, exist_ok=True)
            response_file = intentc_dir / f".intentc-val-{entry.name}-{uuid.uuid4().hex[:8]}.json"
            ctx = ValidationContext(
                project_intent=self._project.project_intent,
                implementation=self._project.implementation,
                feature_intent=feature_intent,
                output_dir=self._output_dir,
                response_file_path=str(response_file),
            )
            return runner.run(entry, ctx)

        workers = min(self._max_workers, len(entries))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_run_one, entry): i for i, entry in enumerate(entries)}
            responses: list[ValidationResponse | None] = [None] * len(entries)
            for future in as_completed(futures):
                idx = futures[future]
                responses[idx] = future.result()

        # Determine pass/fail based on error-severity entries
        severity_map = {v.name: v.severity for v in entries}
        passed = not any(
            r.status != "pass" and severity_map.get(r.name, Severity.ERROR) == Severity.ERROR
            for r in responses
            if r is not None
        )

        return ValidationSuiteResult(
            target=target,
            results=[r for r in responses if r is not None],
            passed=passed,
            summary=_build_summary([r for r in responses if r is not None], entries),
        )

    def _validate_assertions(self, entries: list[Validation]) -> ValidationSuiteResult:
        """Run project-level assertions."""
        return self.validate_entries("project", entries)

    def _resolve_feature_intent(self, target: str) -> IntentFile:
        """Get the primary intent for a target, or a blank one for 'project'."""
        if target == "project":
            return IntentFile(
                name="project",
                body=self._project.project_intent.body,
            )
        if target in self._project.features:
            node = self._project.features[target]
            if node.intents:
                return node.intents[0]
        return IntentFile(name=target, body="")
