"""Validation suite: runners, orchestration, and results for intentc validations."""

from __future__ import annotations

import abc
import json
import os
import secrets
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from intentc.build.agents import (
    Agent,
    AgentProfile,
    BuildContext,
    ValidationResponse,
    create_from_profile,
)
from intentc.core.models import (
    Implementation,
    IntentFile,
    ProjectIntent,
    Severity,
    Validation,
    ValidationFile,
)
from intentc.core.project import Project


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

LogFn = Callable[[str], None]


# ---------------------------------------------------------------------------
# ValidationContext
# ---------------------------------------------------------------------------


@dataclass
class ValidationContext:
    """What the runner needs to evaluate a validation."""

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
    """Aggregated result of running validations against a target."""

    target: str
    results: list[ValidationResponse] = field(default_factory=list)
    passed: bool = True
    summary: str = ""


# ---------------------------------------------------------------------------
# ValidationRunner interface
# ---------------------------------------------------------------------------


class ValidationRunner(abc.ABC):
    """Abstract runner interface. Each runner handles one validation type."""

    @abc.abstractmethod
    def run(self, validation: Validation, ctx: ValidationContext) -> ValidationResponse:
        ...

    @abc.abstractmethod
    def type(self) -> str:
        ...


# ---------------------------------------------------------------------------
# AgentValidationRunner
# ---------------------------------------------------------------------------


class AgentValidationRunner(ValidationRunner):
    """Built-in runner for type 'agent_validation'. Delegates to an Agent."""

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

        vf = ValidationFile(
            target="",
            validations=[validation],
        )

        try:
            response = self._agent.validate(build_ctx, vf)
            return response
        except Exception as exc:
            return ValidationResponse(
                name=validation.name,
                status="fail",
                reason=f"Agent error: {exc}",
            )


# ---------------------------------------------------------------------------
# ValidationSuite
# ---------------------------------------------------------------------------


class ValidationSuite:
    """Core orchestrator for running validations."""

    def __init__(
        self,
        project: Project,
        agent_profile: AgentProfile,
        output_dir: str,
        runner_registry: dict[str, ValidationRunner] | None = None,
        val_response_dir: Path | None = None,
        storage_backend: "StorageBackend | None" = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._project = project
        self._agent_profile = agent_profile
        self._output_dir = output_dir
        self._val_response_dir = val_response_dir
        self._storage_backend = storage_backend
        self._log = log or (lambda _msg: None)

        # Create agent and default runner
        agent = create_from_profile(agent_profile, log=self._log)
        default_runner = AgentValidationRunner(agent)

        self._runners: dict[str, ValidationRunner] = {
            default_runner.type(): default_runner,
        }
        if runner_registry:
            self._runners.update(runner_registry)

    def register_runner(self, runner: ValidationRunner) -> None:
        """Register a custom runner post-construction."""
        self._runners[runner.type()] = runner

    def validate_feature(self, feature: str) -> ValidationSuiteResult:
        """Load .icv files for a feature and run all validations."""
        if feature not in self._project.features:
            return ValidationSuiteResult(
                target=feature,
                passed=True,
                summary="Feature not found, no validations to run.",
            )

        node = self._project.features[feature]
        entries: list[Validation] = []
        for vf in node.validations:
            entries.extend(vf.validations)

        self._log(f"Validating feature '{feature}'... ({len(entries)} validations)")
        return self.validate_entries(feature, entries)

    def validate_project(self) -> list[ValidationSuiteResult]:
        """Run validations for every feature in topological order, plus assertions."""
        topo = self._project.topological_order()
        self._log(f"Validating project ({len(topo)} features)...")
        results: list[ValidationSuiteResult] = []

        for feature_path in topo:
            result = self.validate_feature(feature_path)
            results.append(result)

        # Project-level assertions
        assertion_entries: list[Validation] = []
        for vf in self._project.assertions:
            assertion_entries.extend(vf.validations)

        if assertion_entries:
            self._log(f"Running project-level assertions ({len(assertion_entries)} entries)...")
            assertion_result = self.validate_entries("project", assertion_entries)
            results.append(assertion_result)

        return results

    def validate_entries(
        self, target: str, entries: list[Validation]
    ) -> ValidationSuiteResult:
        """Run a specific list of validation entries against a target."""
        if not entries:
            return ValidationSuiteResult(
                target=target,
                passed=True,
                summary="0 passed out of 0 validations (0 errors, 0 warnings)",
            )

        ctx_base = self._build_validation_context(target)

        # Run in parallel, collect in original order
        results_by_index: dict[int, ValidationResponse] = {}

        def _run_one(idx: int, entry: Validation) -> tuple[int, ValidationResponse]:
            self._log(f"  Running validation '{entry.name}' ({entry.type.value})...")

            runner = self._runners.get(entry.type.value)
            if runner is None:
                resp = ValidationResponse(
                    name=entry.name,
                    status="fail",
                    reason=f"No runner registered for validation type: {entry.type.value}",
                )
            else:
                # Each validation gets its own response file path
                response_file = self._make_response_path(entry.name)
                ctx = ValidationContext(
                    project_intent=ctx_base.project_intent,
                    implementation=ctx_base.implementation,
                    feature_intent=ctx_base.feature_intent,
                    output_dir=ctx_base.output_dir,
                    response_file_path=str(response_file),
                )
                resp = runner.run(entry, ctx)

                # Persist to storage if available
                if self._storage_backend is not None:
                    self._persist_result(entry, resp, response_file)

            self._log(f"  Validation '{entry.name}': {resp.status}")
            if resp.status != "pass":
                self._log(f"    Reason: {resp.reason}")
            return idx, resp

        with ThreadPoolExecutor() as executor:
            futures = {
                executor.submit(_run_one, i, entry): i
                for i, entry in enumerate(entries)
            }
            for future in as_completed(futures):
                idx, resp = future.result()
                results_by_index[idx] = resp

        # Collect in original order
        ordered_results = [results_by_index[i] for i in range(len(entries))]

        # Compute suite result
        passed_count = sum(1 for r in ordered_results if r.status == "pass")
        failed = [
            (r, entries[i])
            for i, r in enumerate(ordered_results)
            if r.status != "pass"
        ]
        error_count = sum(1 for _, e in failed if e.severity == Severity.ERROR)
        warning_count = sum(1 for _, e in failed if e.severity == Severity.WARNING)

        suite_passed = error_count == 0
        summary = (
            f"{passed_count} passed out of {len(entries)} validations "
            f"({error_count} errors, {warning_count} warnings)"
        )

        return ValidationSuiteResult(
            target=target,
            results=ordered_results,
            passed=suite_passed,
            summary=summary,
        )

    # ---- internal helpers ----

    def _build_validation_context(self, target: str) -> ValidationContext:
        """Build a base ValidationContext for the given target."""
        project_intent = self._project.project_intent
        implementation = self._project.resolve_implementation()

        # Resolve feature intent
        if target == "project":
            feature_intent = IntentFile(
                name="project",
                body=project_intent.body,
            )
        elif target in self._project.features:
            node = self._project.features[target]
            feature_intent = node.intents[0] if node.intents else IntentFile(
                name=target, body=""
            )
        else:
            feature_intent = IntentFile(name=target, body="")

        return ValidationContext(
            project_intent=project_intent,
            implementation=implementation,
            feature_intent=feature_intent,
            output_dir=self._output_dir,
            response_file_path="",  # placeholder, overridden per validation
        )

    def _make_response_path(self, validation_name: str) -> Path:
        """Create a unique response file path for a validation."""
        base_dir = self._val_response_dir or Path(self._output_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        unique = secrets.token_hex(4)
        return base_dir / f"val-response-{validation_name}-{unique}.json"

    def _persist_result(
        self,
        entry: Validation,
        resp: ValidationResponse,
        response_file: Path,
    ) -> None:
        """Save validation result and agent response to storage, then clean up."""
        assert self._storage_backend is not None

        generation_id = f"val-{secrets.token_hex(4)}"

        # Create a generation record so the FK on validation_results is satisfied.
        self._storage_backend.create_generation(
            generation_id=generation_id,
            output_dir=self._output_dir,
        )

        val_result_id = self._storage_backend.save_validation_result(
            build_result_id=None,
            generation_id=generation_id,
            target=entry.name,
            validation_file_version_id=None,
            name=resp.name,
            type=entry.type.value,
            severity=entry.severity.value,
            status=resp.status,
            reason=resp.reason,
        )

        # Read and persist agent response JSON if file exists
        if response_file.exists():
            try:
                with open(response_file, "r", encoding="utf-8") as f:
                    response_json = json.load(f)
                self._storage_backend.save_agent_response(
                    build_result_id=None,
                    validation_result_id=val_result_id,
                    response_type="validation",
                    response_json=response_json,
                )
            except (json.JSONDecodeError, OSError):
                pass
            finally:
                try:
                    os.remove(response_file)
                except OSError:
                    pass
