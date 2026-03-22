"""Validation suite for intentc — runners, orchestration, and results."""

from __future__ import annotations

import abc
import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from intentc.build.agents import (
    Agent,
    AgentError,
    AgentProfile,
    BuildContext,
    ValidationResponse,
    create_from_profile,
)
from intentc.build.storage.backend import StorageBackend
from intentc.core.types import (
    Implementation,
    IntentFile,
    ProjectIntent,
    Severity,
    Validation,
    ValidationType,
)
from intentc.core.project import Project


# ---------------------------------------------------------------------------
# ValidationContext
# ---------------------------------------------------------------------------


@dataclass
class ValidationContext:
    """What a runner needs to evaluate a validation."""

    project_intent: ProjectIntent
    implementation: Implementation | None
    feature_intent: IntentFile
    output_dir: str
    response_file_path: str


# ---------------------------------------------------------------------------
# ValidationSuiteResult
# ---------------------------------------------------------------------------


@dataclass
class ValidationSuiteResult:
    """Aggregated result for all validations run against a target."""

    target: str
    results: list[ValidationResponse] = field(default_factory=list)
    passed: bool = True
    summary: str = ""

    def _build_summary(self, validations: list[Validation]) -> None:
        """Compute summary string and passed flag from results and their severities."""
        total = len(self.results)
        passed_count = sum(1 for r in self.results if r.status == "pass")
        # Build a map from validation name to severity
        severity_map: dict[str, Severity] = {}
        for v in validations:
            severity_map[v.name] = v.severity

        errors = 0
        warnings = 0
        for r in self.results:
            if r.status != "pass":
                sev = severity_map.get(r.name, Severity.ERROR)
                if sev == Severity.ERROR:
                    errors += 1
                else:
                    warnings += 1

        self.passed = errors == 0
        self.summary = (
            f"{passed_count}/{total} passed, {errors} error(s), {warnings} warning(s)"
        )


# ---------------------------------------------------------------------------
# ValidationRunner ABC
# ---------------------------------------------------------------------------


class ValidationRunner(abc.ABC):
    """Interface for validation type runners."""

    @abc.abstractmethod
    def run(self, validation: Validation, ctx: ValidationContext) -> ValidationResponse:
        """Evaluate a single validation entry."""

    @abc.abstractmethod
    def type(self) -> str:
        """Return the validation type this runner handles."""


# ---------------------------------------------------------------------------
# AgentValidationRunner
# ---------------------------------------------------------------------------


class AgentValidationRunner(ValidationRunner):
    """Built-in runner for agent_validation type."""

    def __init__(self, agent: Agent) -> None:
        self._agent = agent

    def run(self, validation: Validation, ctx: ValidationContext) -> ValidationResponse:
        build_ctx = BuildContext(
            intent=ctx.feature_intent,
            validations=[],
            output_dir=ctx.output_dir,
            generation_id=f"val-{secrets.token_hex(4)}",
            dependency_names=[],
            project_intent=ctx.project_intent,
            implementation=ctx.implementation,
            response_file_path=ctx.response_file_path,
        )
        try:
            return self._agent.validate(build_ctx, validation)
        except (AgentError, Exception) as exc:
            return ValidationResponse(
                name=validation.name,
                status="fail",
                reason=f"Agent error: {exc}",
            )

    def type(self) -> str:
        return "agent_validation"


# ---------------------------------------------------------------------------
# ValidationSuite
# ---------------------------------------------------------------------------


class ValidationSuite:
    """Orchestrates running validations for features and the whole project."""

    def __init__(
        self,
        project: Project,
        agent_profile: AgentProfile,
        output_dir: str,
        runner_registry: dict[str, ValidationRunner] | None = None,
        val_response_dir: Path | None = None,
        storage_backend: StorageBackend | None = None,
    ) -> None:
        self._project = project
        self._agent_profile = agent_profile
        self._output_dir = output_dir
        self._val_response_dir = val_response_dir
        self._storage_backend = storage_backend

        # Create the default agent and register the built-in runner
        agent = create_from_profile(agent_profile)
        self._registry: dict[str, ValidationRunner] = {
            "agent_validation": AgentValidationRunner(agent),
        }
        if runner_registry:
            self._registry.update(runner_registry)

    def register_runner(self, runner: ValidationRunner) -> None:
        """Register a custom validation runner."""
        self._registry[runner.type()] = runner

    def validate_feature(self, feature: str) -> ValidationSuiteResult:
        """Run all validations for a feature's .icv files."""
        node = self._project.features.get(feature)
        if node is None:
            return ValidationSuiteResult(target=feature, passed=True, summary="0/0 passed, 0 error(s), 0 warning(s)")

        all_entries: list[Validation] = []
        for vf in node.validations:
            all_entries.extend(vf.validations)

        return self.validate_entries(feature, all_entries)

    def validate_project(self) -> list[ValidationSuiteResult]:
        """Run validations for all features (topological order) plus project assertions."""
        results: list[ValidationSuiteResult] = []

        for feature_path in self._project.topological_order():
            results.append(self.validate_feature(feature_path))

        # Project-level assertions
        if self._project.assertions:
            all_entries: list[Validation] = []
            for vf in self._project.assertions:
                all_entries.extend(vf.validations)
            if all_entries:
                results.append(self.validate_entries("project", all_entries))

        return results

    def validate_entries(
        self, target: str, entries: list[Validation]
    ) -> ValidationSuiteResult:
        """Run a specific list of validation entries against a target."""
        suite_result = ValidationSuiteResult(target=target)

        for validation in entries:
            ctx = self._build_context(target, validation)
            val_type = validation.type
            runner = self._registry.get(val_type)

            if runner is None:
                response = ValidationResponse(
                    name=validation.name,
                    status="fail",
                    reason=f"Unknown validation type: {val_type!r} — no runner registered",
                )
            else:
                response = runner.run(validation, ctx)

            suite_result.results.append(response)

            # Persist if storage backend available
            if self._storage_backend is not None:
                self._persist_result(target, validation, response, ctx)

        suite_result._build_summary(entries)
        return suite_result

    def _build_context(self, target: str, validation: Validation) -> ValidationContext:
        """Construct a ValidationContext for a given target."""
        # Resolve feature intent
        if target == "project":
            feature_intent = IntentFile(
                name="project",
                body=self._project.project_intent.body,
            )
        else:
            node = self._project.features.get(target)
            if node and node.intents:
                feature_intent = node.intents[0]
            else:
                feature_intent = IntentFile(name=target, body="")

        # Resolve implementation
        impl: Implementation | None = None
        try:
            impl = self._project.resolve_implementation()
        except (KeyError, ValueError):
            pass

        # Response file path
        resp_dir = self._val_response_dir or Path(self._output_dir)
        resp_file = resp_dir / f"val-{secrets.token_hex(8)}.json"

        return ValidationContext(
            project_intent=self._project.project_intent,
            implementation=impl,
            feature_intent=feature_intent,
            output_dir=self._output_dir,
            response_file_path=str(resp_file),
        )

    def _persist_result(
        self,
        target: str,
        validation: Validation,
        response: ValidationResponse,
        ctx: ValidationContext,
    ) -> None:
        """Persist validation result and agent response to storage."""
        assert self._storage_backend is not None

        val_type = validation.type

        val_result_id = self._storage_backend.save_validation_result(
            build_result_id=None,
            generation_id=f"val-{secrets.token_hex(4)}",
            target=target,
            validation_file_version_id=None,
            name=validation.name,
            type=val_type,
            severity=validation.severity.value,
            status=response.status,
            reason=response.reason,
            duration_secs=None,
        )

        # Save agent response if response file exists
        resp_path = Path(ctx.response_file_path)
        if resp_path.exists():
            try:
                resp_data = json.loads(resp_path.read_text(encoding="utf-8"))
                self._storage_backend.save_agent_response(
                    build_result_id=None,
                    validation_result_id=val_result_id,
                    response_type="validation",
                    response_json=resp_data,
                )
                resp_path.unlink()
            except (json.JSONDecodeError, OSError):
                pass
