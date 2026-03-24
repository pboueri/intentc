from __future__ import annotations

import json
import secrets
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from intentc.core.types import (
    IntentFile,
    Implementation,
    ProjectIntent,
    Severity,
    Validation,
    ValidationFile,
)
from intentc.core.project import Project
from intentc.build.agents.base import Agent, create_from_profile
from intentc.build.agents.types import (
    AgentProfile,
    BuildContext,
    ValidationResponse,
)
from intentc.build.storage.backend import StorageBackend


@dataclass
class ValidationContext:
    """What a runner needs to evaluate a validation."""

    project_intent: ProjectIntent
    implementation: Implementation | None
    feature_intent: IntentFile
    output_dir: str
    response_file_path: str


@dataclass
class ValidationSuiteResult:
    """Result of validating a target (feature or assertion set)."""

    target: str
    results: list[ValidationResponse] = field(default_factory=list)
    passed: bool = True
    summary: str = ""


class ValidationRunner(ABC):
    """Interface for validation runners."""

    @abstractmethod
    def run(self, validation: Validation, ctx: ValidationContext) -> ValidationResponse: ...

    @abstractmethod
    def type(self) -> str: ...


class AgentValidationRunner(ValidationRunner):
    """Built-in runner for agent_validation type."""

    def __init__(self, agent: Agent) -> None:
        self._agent = agent

    def type(self) -> str:
        return "agent_validation"

    def run(self, validation: Validation, ctx: ValidationContext) -> ValidationResponse:
        generation_id = f"val-{secrets.token_hex(4)}"
        build_ctx = BuildContext(
            intent=ctx.feature_intent,
            validations=[],
            output_dir=ctx.output_dir,
            generation_id=generation_id,
            dependency_names=[],
            project_intent=ctx.project_intent,
            implementation=ctx.implementation,
            response_file_path=ctx.response_file_path,
        )
        try:
            response = self._agent.validate(build_ctx, validation)
            return response
        except Exception as exc:
            return ValidationResponse(
                name=validation.name,
                status="fail",
                reason=f"Agent error: {exc}",
            )


class ValidationSuite:
    """Core orchestrator for running validations."""

    def __init__(
        self,
        project: Project,
        agent_profile: AgentProfile,
        output_dir: str,
        runner_registry: dict[str, ValidationRunner] | None = None,
        val_response_dir: Path | None = None,
        storage_backend: StorageBackend | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._project = project
        self._agent_profile = agent_profile
        self._output_dir = output_dir
        self._val_response_dir = val_response_dir
        self._storage_backend = storage_backend
        self._log = log or (lambda _: None)

        # Create agent and default runner
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
        """Load .icv files for a feature and run all validations."""
        if feature not in self._project.features:
            return ValidationSuiteResult(
                target=feature,
                passed=False,
                summary=f"Feature '{feature}' not found",
            )

        node = self._project.features[feature]
        entries: list[Validation] = []
        for vf in node.validations:
            entries.extend(vf.validations)

        self._log(f"Validating feature '{feature}'... ({len(entries)} validations)")
        return self.validate_entries(feature, entries)

    def validate_project(self) -> list[ValidationSuiteResult]:
        """Run validations for all features in topological order plus assertions."""
        order = self._project.topological_order()
        self._log(f"Validating project ({len(order)} features)...")

        results: list[ValidationSuiteResult] = []
        for feature in order:
            result = self.validate_feature(feature)
            results.append(result)

        # Project-level assertions
        if self._project.assertions:
            assertion_entries: list[Validation] = []
            for vf in self._project.assertions:
                assertion_entries.extend(vf.validations)
            self._log(f"Running project-level assertions ({len(assertion_entries)} entries)...")
            result = self.validate_entries("project", assertion_entries)
            results.append(result)

        return results

    def validate_entries(self, target: str, entries: list[Validation]) -> ValidationSuiteResult:
        """Run a list of validation entries against a target (in parallel)."""
        suite_result = ValidationSuiteResult(target=target)

        # Resolve feature intent for context
        feature_intent = self._resolve_feature_intent(target)
        implementation = self._project.resolve_implementation()

        def _run_one(entry: Validation) -> ValidationResponse:
            self._log(f"  Running validation '{entry.name}' ({entry.type})...")
            runner = self._registry.get(entry.type)
            if runner is None:
                return ValidationResponse(
                    name=entry.name,
                    status="fail",
                    reason=f"Unknown validation type: {entry.type!r}. No runner registered for this type.",
                )
            response_file_path = self._get_response_file_path(entry.name)
            ctx = ValidationContext(
                project_intent=self._project.project_intent,
                implementation=implementation,
                feature_intent=feature_intent,
                output_dir=self._output_dir,
                response_file_path=response_file_path,
            )
            return runner.run(entry, ctx)

        # Run all validations in parallel, collect results in original order
        with ThreadPoolExecutor() as executor:
            futures = {executor.submit(_run_one, entry): i for i, entry in enumerate(entries)}
            results_by_index: dict[int, ValidationResponse] = {}
            for future in as_completed(futures):
                idx = futures[future]
                results_by_index[idx] = future.result()

        for i, entry in enumerate(entries):
            response = results_by_index[i]
            suite_result.results.append(response)

            # Check if error-severity validation failed
            if response.status != "pass" and entry.severity == Severity.ERROR:
                suite_result.passed = False

            status_msg = response.status
            if response.status != "pass":
                status_msg = f"{response.status} - {response.reason}"
            self._log(f"  Validation '{entry.name}': {status_msg}")

        # Build summary
        total = len(entries)
        passed = sum(1 for r in suite_result.results if r.status == "pass")
        errors = sum(
            1
            for r, e in zip(suite_result.results, entries)
            if r.status != "pass" and e.severity == Severity.ERROR
        )
        warnings = sum(
            1
            for r, e in zip(suite_result.results, entries)
            if r.status != "pass" and e.severity == Severity.WARNING
        )
        suite_result.summary = (
            f"{passed}/{total} passed, {errors} error(s), {warnings} warning(s)"
        )

        return suite_result

    def _resolve_feature_intent(self, target: str) -> IntentFile:
        """Resolve the feature intent for a validation context."""
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

    def _get_response_file_path(self, validation_name: str) -> str:
        """Get path for the agent response file."""
        base = self._val_response_dir or Path(self._output_dir)
        return str(base / f"val_response_{validation_name}_{secrets.token_hex(4)}.json")
