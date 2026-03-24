"""Validation suite for verifying generated code meets intent."""

from __future__ import annotations

import abc
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from intentc.build.agents.base import Agent
from intentc.build.agents.factory import create_from_profile
from intentc.build.agents.models import (
    AgentProfile,
    BuildContext,
    ValidationResponse,
)
from intentc.build.storage.backend import StorageBackend
from intentc.core.models import (
    Implementation,
    IntentFile,
    ProjectIntent,
    Severity,
    Validation,
    ValidationFile,
)
from intentc.core.project import Project


@dataclass
class ValidationContext:
    """What a runner needs to evaluate a validation."""

    project_intent: IntentFile
    implementation: Implementation | None
    feature_intent: IntentFile
    output_dir: str
    response_file_path: str


@dataclass
class ValidationSuiteResult:
    """Result of validating a target (feature or project)."""

    target: str
    results: list[ValidationResponse] = field(default_factory=list)
    passed: bool = True
    summary: str = ""


class ValidationRunner(abc.ABC):
    """Interface for validation runners. Each handles one validation type."""

    @abc.abstractmethod
    def run(self, validation: Validation, ctx: ValidationContext) -> ValidationResponse:
        """Execute a single validation."""

    @abc.abstractmethod
    def type(self) -> str:
        """Return the validation type this runner handles."""


class AgentValidationRunner(ValidationRunner):
    """Built-in runner for agent_validation type."""

    def __init__(self, agent: Agent) -> None:
        self._agent = agent

    def run(self, validation: Validation, ctx: ValidationContext) -> ValidationResponse:
        """Run an agent-based validation."""
        generation_id = f"val-{os.urandom(4).hex()}"

        build_ctx = BuildContext(
            intent=ctx.feature_intent,
            validations=[],
            output_dir=ctx.output_dir,
            generation_id=generation_id,
            dependency_names=[],
            project_intent=ProjectIntent(
                name=ctx.project_intent.name,
                body=ctx.project_intent.body,
            ),
            implementation=ctx.implementation,
            response_file_path=ctx.response_file_path,
        )

        try:
            response = self._agent.validate(build_ctx, validation)
            return response
        except Exception as e:
            return ValidationResponse(
                name=validation.name,
                status="fail",
                reason=f"Agent error: {e}",
            )

    def type(self) -> str:
        return "agent_validation"


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

        # Create agent and set up default runner
        agent = create_from_profile(agent_profile)
        self._runners: dict[str, ValidationRunner] = {
            "agent_validation": AgentValidationRunner(agent),
        }

        # Merge user-provided runners
        if runner_registry:
            self._runners.update(runner_registry)

    def register_runner(self, runner: ValidationRunner) -> None:
        """Register a custom validation runner."""
        self._runners[runner.type()] = runner

    def validate_feature(self, feature: str) -> ValidationSuiteResult:
        """Validate a single feature by loading its .icv files."""
        if feature not in self._project.features:
            return ValidationSuiteResult(
                target=feature,
                passed=False,
                summary=f"Feature '{feature}' not found in project",
            )

        node = self._project.features[feature]
        all_entries: list[Validation] = []
        for vf in node.validations:
            all_entries.extend(vf.validations)

        self._log(f"Validating feature '{feature}'... ({len(all_entries)} validations)")
        return self.validate_entries(feature, all_entries)

    def validate_project(self) -> list[ValidationSuiteResult]:
        """Validate all features in topological order plus project-level assertions."""
        features = self._project.topological_order()
        self._log(f"Validating project ({len(features)} features)...")

        results: list[ValidationSuiteResult] = []
        for feature in features:
            result = self.validate_feature(feature)
            results.append(result)

        # Project-level assertions
        if self._project.assertions:
            assertion_entries: list[Validation] = []
            for vf in self._project.assertions:
                assertion_entries.extend(vf.validations)

            self._log(f"Running project-level assertions ({len(assertion_entries)} entries)...")
            assertion_result = self.validate_entries("project", assertion_entries)
            results.append(assertion_result)

        return results

    def validate_entries(
        self, target: str, entries: list[Validation]
    ) -> ValidationSuiteResult:
        """Run a list of validation entries against a target."""
        if not entries:
            return ValidationSuiteResult(target=target, summary="0 passed, 0 total, 0 errors, 0 warnings")

        # Resolve feature intent for context
        feature_intent = self._resolve_feature_intent(target)
        implementation = self._project.resolve_implementation()

        # Build project intent as IntentFile
        pi = self._project.project_intent
        project_intent_file = IntentFile(name=pi.name, body=pi.body)

        # Run validations in parallel
        results: list[ValidationResponse | None] = [None] * len(entries)

        def _run_one(index: int, validation: Validation) -> tuple[int, ValidationResponse]:
            self._log(f"  Running validation '{validation.name}' ({validation.type})...")

            type_key = validation.type.value if hasattr(validation.type, 'value') else str(validation.type)
            runner = self._runners.get(type_key)
            if runner is None:
                resp = ValidationResponse(
                    name=validation.name,
                    status="fail",
                    reason=f"No runner registered for validation type: {type_key}",
                )
            else:
                response_dir = self._val_response_dir or Path(self._output_dir)
                response_file = response_dir / f".val-response-{validation.name}-{os.urandom(4).hex()}.json"
                response_file.parent.mkdir(parents=True, exist_ok=True)

                ctx = ValidationContext(
                    project_intent=project_intent_file,
                    implementation=implementation,
                    feature_intent=feature_intent,
                    output_dir=self._output_dir,
                    response_file_path=str(response_file),
                )
                resp = runner.run(validation, ctx)

                # Clean up response file if it exists
                if response_file.exists():
                    if self._storage_backend:
                        try:
                            raw = json.loads(response_file.read_text())
                            self._storage_backend.save_agent_response(
                                build_result_id=None,
                                validation_result_id=None,
                                response_type="validation",
                                response_json=raw,
                            )
                        except (json.JSONDecodeError, OSError):
                            pass
                    try:
                        response_file.unlink()
                    except OSError:
                        pass

            status_msg = resp.status
            if resp.status != "pass":
                status_msg = f"{resp.status} - {resp.reason}"
            self._log(f"  Validation '{validation.name}': {status_msg}")

            return index, resp

        with ThreadPoolExecutor() as executor:
            futures = {
                executor.submit(_run_one, i, entry): i
                for i, entry in enumerate(entries)
            }
            for future in as_completed(futures):
                idx, resp = future.result()
                results[idx] = resp

        # Build suite result
        final_results = [r for r in results if r is not None]

        # Persist validation results if storage backend provided
        if self._storage_backend:
            for i, resp in enumerate(final_results):
                entry = entries[i]
                try:
                    self._storage_backend.save_validation_result(
                        build_result_id=None,
                        generation_id="",
                        target=target,
                        validation_file_version_id=None,
                        name=resp.name,
                        type=entry.type.value if hasattr(entry.type, 'value') else str(entry.type),
                        severity=entry.severity.value if hasattr(entry.severity, 'value') else str(entry.severity),
                        status=resp.status,
                        reason=resp.reason,
                    )
                except Exception:
                    pass

        passed_count = sum(1 for r in final_results if r.status == "pass")
        failed = [
            (r, entries[i])
            for i, r in enumerate(final_results)
            if r.status != "pass"
        ]
        error_count = sum(1 for _, e in failed if e.severity == Severity.ERROR)
        warning_count = sum(1 for _, e in failed if e.severity == Severity.WARNING)

        has_error_failure = any(
            r.status != "pass" and entries[i].severity == Severity.ERROR
            for i, r in enumerate(final_results)
        )

        return ValidationSuiteResult(
            target=target,
            results=final_results,
            passed=not has_error_failure,
            summary=f"{passed_count} passed, {len(final_results)} total, {error_count} errors, {warning_count} warnings",
        )

    def _resolve_feature_intent(self, target: str) -> IntentFile:
        """Resolve feature intent for a target."""
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
