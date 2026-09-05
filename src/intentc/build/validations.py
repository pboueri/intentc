"""Validation suite: runs `.icv` validation entries against generated code.

Deterministic runners (`command_validation`, `file_exists`) are preferred over
agent judgement wherever possible; `agent_validation` is the fallback for
checks that require natural-language judgement. This module is independent of
the build pipeline -- it can be invoked directly against a feature or the
whole project.
"""

from __future__ import annotations

import glob
import re
import secrets
import subprocess
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Optional

from pydantic import BaseModel, ConfigDict, Field

from intentc.build.agents import (
    Agent,
    AgentError,
    AgentProfile,
    BuildContext,
    ValidationResponse,
    create_from_profile,
)
from intentc.build.storage import StorageBackend
from intentc.core import Implementation, IntentFile, Project, ProjectIntent, Severity, Validation

LogFn = Callable[[str], None]

_DETERMINISTIC_TYPES = {"command_validation", "file_exists"}
_SLASH_RE = re.compile(r"[\\/]")


def _noop_log(_message: str) -> None:
    return None


def _response_file_name(target: str, validation_name: str) -> str:
    safe_target = _SLASH_RE.sub("_", target)
    safe_name = _SLASH_RE.sub("_", validation_name)
    return f"{safe_target}-{safe_name}-{secrets.token_hex(4)}.json"


def _resolve_output_path(entry: str, output_dir: str) -> str:
    resolved = entry.replace("{output_dir}", output_dir)
    prefix = output_dir.rstrip("/") + "/"
    if resolved == output_dir or resolved.startswith(prefix):
        return resolved
    return str(Path(output_dir) / resolved)


# ---------------------------------------------------------------------------
# Context and result types
# ---------------------------------------------------------------------------


class ValidationContext(BaseModel):
    """Everything a runner needs to evaluate a single validation entry."""

    model_config = ConfigDict(extra="ignore")

    project_intent: ProjectIntent
    implementation: Optional[Implementation] = None
    feature_intent: IntentFile
    output_dir: str
    response_file_path: str
    project_root: str = ""


class ValidationSuiteResult(BaseModel):
    """The rolled-up outcome of running a set of validations against a target."""

    model_config = ConfigDict(extra="ignore")

    target: str
    results: list[ValidationResponse] = Field(default_factory=list)
    passed: bool = True
    summary: str = ""
    passed_count: int = 0
    error_count: int = 0
    warning_count: int = 0
    duration_secs: float = 0.0


# ---------------------------------------------------------------------------
# Runner interface
# ---------------------------------------------------------------------------


class ValidationRunner(ABC):
    """Evaluates one validation entry of a single `type`."""

    @abstractmethod
    def run(self, validation: Validation, ctx: ValidationContext) -> ValidationResponse: ...

    @abstractmethod
    def type(self) -> str: ...


class CommandValidationRunner(ValidationRunner):
    """Runs a shell command; passes when it exits 0."""

    def type(self) -> str:
        return "command_validation"

    def run(self, validation: Validation, ctx: ValidationContext) -> ValidationResponse:
        args = validation.args
        command = str(args.get("command", "")).replace("{output_dir}", ctx.output_dir)
        cwd = self._resolve_cwd(ctx, args.get("cwd"))
        timeout = args.get("timeout", 600)
        expect_output = args.get("expect_output")
        if expect_output:
            expect_output = str(expect_output).replace("{output_dir}", ctx.output_dir)

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return ValidationResponse(
                name=validation.name, status="fail", reason=f"timed out after {timeout}s"
            )
        except OSError as exc:
            return ValidationResponse(
                name=validation.name, status="fail", reason=f"failed to run command: {exc}"
            )

        combined = (result.stdout or "") + (result.stderr or "")
        lines = combined.strip("\n").splitlines()

        if result.returncode != 0:
            tail = "\n".join(lines[-30:])
            reason = f"exit {result.returncode}" + (f"\n{tail}" if tail else "")
            return ValidationResponse(name=validation.name, status="fail", reason=reason)

        if expect_output and expect_output not in combined:
            return ValidationResponse(
                name=validation.name,
                status="fail",
                reason=f"expected output '{expect_output}' not found in command output",
            )

        reason = "exit 0" + (f"\n{lines[-1]}" if lines else "")
        return ValidationResponse(name=validation.name, status="pass", reason=reason)

    @staticmethod
    def _resolve_cwd(ctx: ValidationContext, cwd_arg: Optional[str]) -> str:
        project_root = ctx.project_root or "."
        if not cwd_arg:
            return ctx.output_dir
        if cwd_arg == ".":
            return project_root
        return str(Path(project_root) / cwd_arg)


class FileExistsRunner(ValidationRunner):
    """Passes when every path/glob (relative to the output dir) matches something."""

    def type(self) -> str:
        return "file_exists"

    def run(self, validation: Validation, ctx: ValidationContext) -> ValidationResponse:
        paths = validation.args.get("paths", [])
        unmatched: list[str] = []
        for entry in paths:
            resolved = _resolve_output_path(str(entry), ctx.output_dir)
            if not glob.glob(resolved):
                unmatched.append(entry)
        if unmatched:
            return ValidationResponse(
                name=validation.name,
                status="fail",
                reason=f"no match for: {', '.join(unmatched)}",
            )
        return ValidationResponse(name=validation.name, status="pass", reason="all paths matched")


class AgentValidationRunner(ValidationRunner):
    """Delegates judgement of a natural-language rubric to an agent."""

    def __init__(self, agent: Agent) -> None:
        self.agent = agent

    def type(self) -> str:
        return "agent_validation"

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
            return self.agent.validate(build_ctx, validation)
        except AgentError as exc:
            return ValidationResponse(name=validation.name, status="fail", reason=f"agent error: {exc}")


# ---------------------------------------------------------------------------
# ValidationSuite
# ---------------------------------------------------------------------------

_PROFILE_OVERRIDE_FIELDS = ("provider", "model_id", "timeout")


class ValidationSuite:
    """Orchestrates running validations for a feature or the whole project."""

    def __init__(
        self,
        project: Project,
        agent_profile: AgentProfile,
        output_dir: str,
        runner_registry: Optional[dict[str, ValidationRunner]] = None,
        val_response_dir: Optional[Path] = None,
        storage_backend: Optional[StorageBackend] = None,
        log: Optional[LogFn] = None,
        implementation: Optional[Implementation] = None,
        build_result_id: Optional[int] = None,
        generation_id: Optional[str] = None,
        create_agent: Optional[Callable[[AgentProfile], Agent]] = None,
    ) -> None:
        self.project = project
        self.agent_profile = agent_profile
        self.output_dir = output_dir
        self.val_response_dir = val_response_dir
        self.storage_backend = storage_backend
        self.log: LogFn = log or _noop_log
        self.implementation = implementation if implementation is not None else project.resolve_implementation()
        self.build_result_id = build_result_id
        self.generation_id = generation_id
        self.create_agent = create_agent or create_from_profile
        self._runners: dict[str, ValidationRunner] = {
            "command_validation": CommandValidationRunner(),
            "file_exists": FileExistsRunner(),
        }
        if runner_registry:
            self._runners.update(runner_registry)

    def register_runner(self, runner: ValidationRunner) -> None:
        """Register (or replace) a runner for post-construction extensibility."""
        self._runners[runner.type()] = runner

    # -- Public lifecycle ----------------------------------------------------

    def validate_feature(self, feature: str) -> ValidationSuiteResult:
        entries: list[Validation] = []
        node = self.project.features.get(feature)
        if node is not None:
            for vf in node.validations:
                entries.extend(vf.validations)
        self.log(f"Validating feature '{feature}'... ({len(entries)} validations)")
        return self.validate_entries(feature, entries)

    def validate_project(self) -> list[ValidationSuiteResult]:
        order = self.project.topological_order()
        self.log(f"Validating project ({len(order)} features)...")
        results = [self.validate_feature(feature) for feature in order]

        assertion_entries = [v for vf in self.project.assertions for v in vf.validations]
        self.log(f"Running project-level assertions ({len(assertion_entries)} entries)...")
        results.append(self.validate_entries("project", assertion_entries))
        return results

    def validate_entries(self, target: str, entries: list[Validation]) -> ValidationSuiteResult:
        start = time.monotonic()

        generation_id = self.generation_id
        if generation_id is None:
            generation_id = f"val-{secrets.token_hex(4)}"
            if self.storage_backend is not None:
                self.storage_backend.create_generation(generation_id, self.output_dir)

        deterministic_entries = [e for e in entries if e.type in _DETERMINISTIC_TYPES]
        other_entries = [e for e in entries if e.type not in _DETERMINISTIC_TYPES]

        responses: dict[str, ValidationResponse] = {}
        failed_deterministic_name: Optional[str] = None

        for entry in deterministic_entries:
            response = self._run_entry_timed(target, entry)
            responses[entry.name] = response
            self._persist(target, generation_id, entry, response)
            if failed_deterministic_name is None and response.status != "pass" and entry.severity == Severity.ERROR:
                failed_deterministic_name = entry.name

        if other_entries:
            if failed_deterministic_name is not None:
                for entry in other_entries:
                    response = ValidationResponse(
                        name=entry.name,
                        status="fail",
                        reason=f"skipped: deterministic validation '{failed_deterministic_name}' failed",
                        severity=self._severity_value(entry),
                        type=entry.type,
                    )
                    responses[entry.name] = response
                    self._persist(target, generation_id, entry, response)
            else:
                with ThreadPoolExecutor(max_workers=len(other_entries)) as pool:
                    future_map = {
                        pool.submit(self._run_entry_timed, target, entry): entry for entry in other_entries
                    }
                    for future in as_completed(future_map):
                        entry = future_map[future]
                        response = future.result()
                        responses[entry.name] = response
                        self._persist(target, generation_id, entry, response)

        ordered = [responses[entry.name] for entry in entries]
        duration = time.monotonic() - start
        return self._build_suite_result(target, ordered, duration)

    # -- Internals -------------------------------------------------------------

    @staticmethod
    def _severity_value(entry: Validation) -> str:
        return entry.severity.value if isinstance(entry.severity, Severity) else str(entry.severity)

    def _run_entry_timed(self, target: str, entry: Validation) -> ValidationResponse:
        self.log(f"  Running validation '{entry.name}' ({entry.type})...")
        start = time.monotonic()
        runner = self._resolve_runner(entry)
        if runner is None:
            response = ValidationResponse(
                name=entry.name,
                status="fail",
                reason=f"No runner registered for validation type '{entry.type}'",
            )
        else:
            ctx = self._make_context(target, entry.name)
            try:
                response = runner.run(entry, ctx)
            except Exception as exc:  # noqa: BLE001 - a runner failure is still a validation failure
                response = ValidationResponse(name=entry.name, status="fail", reason=f"runner error: {exc}")

        duration = time.monotonic() - start
        response.name = entry.name
        response.severity = self._severity_value(entry)
        response.type = entry.type
        response.duration_secs = duration

        self.log(f"  Validation '{entry.name}': {response.status} ({duration:.1f}s)")
        if response.status != "pass":
            for line in response.reason.splitlines()[:5]:
                self.log(f"    reason: {line}")
        return response

    def _resolve_runner(self, entry: Validation) -> Optional[ValidationRunner]:
        if entry.type in self._runners:
            return self._runners[entry.type]
        if entry.type == "agent_validation":
            profile = self._resolve_profile(entry)
            agent = self.create_agent(profile)
            return AgentValidationRunner(agent)
        return None

    def _resolve_profile(self, entry: Validation) -> AgentProfile:
        # There is no dedicated per-validation `agent_profile` field on the core
        # `Validation` model, so the map override described for this entry lives
        # in `args.agent_profile` -- the one place a `.icv` author's extra keys
        # survive parsing.
        override = entry.args.get("agent_profile")
        if not isinstance(override, dict) or not override:
            return self.agent_profile
        updates = {field: override[field] for field in _PROFILE_OVERRIDE_FIELDS if field in override}
        if not updates:
            return self.agent_profile
        return self.agent_profile.model_copy(update=updates)

    def _resolve_feature_intent(self, target: str) -> IntentFile:
        if target == "project":
            return IntentFile(name="project", body=self.project.project_intent.body)
        node = self.project.features.get(target)
        if node is not None and node.intents:
            return node.intents[0]
        return IntentFile(name=target, body="")

    def _project_root(self) -> str:
        if self.project.intent_dir is not None:
            return str(Path(self.project.intent_dir).parent)
        return str(Path.cwd())

    def _make_context(self, target: str, validation_name: str) -> ValidationContext:
        response_dir = Path(self.val_response_dir) if self.val_response_dir is not None else Path(self.output_dir)
        response_dir.mkdir(parents=True, exist_ok=True)
        response_file_path = str(response_dir / _response_file_name(target, validation_name))
        return ValidationContext(
            project_intent=self.project.project_intent,
            implementation=self.implementation,
            feature_intent=self._resolve_feature_intent(target),
            output_dir=self.output_dir,
            response_file_path=response_file_path,
            project_root=self._project_root(),
        )

    def _persist(self, target: str, generation_id: str, entry: Validation, response: ValidationResponse) -> None:
        if self.storage_backend is None:
            return
        validation_result_id = self.storage_backend.save_validation_result(
            build_result_id=self.build_result_id,
            generation_id=generation_id,
            target=target,
            validation_file_version_id=None,
            name=response.name,
            type=response.type,
            severity=response.severity,
            status=response.status,
            reason=response.reason,
            duration_secs=response.duration_secs,
        )
        self.storage_backend.save_agent_response(
            build_result_id=self.build_result_id,
            validation_result_id=validation_result_id,
            response_type=entry.type,
            response_json=response.model_dump(),
        )

    @staticmethod
    def _build_suite_result(
        target: str, ordered: list[ValidationResponse], duration: float
    ) -> ValidationSuiteResult:
        passed_count = sum(1 for r in ordered if r.status == "pass")
        error_count = sum(1 for r in ordered if r.status != "pass" and r.severity == Severity.ERROR.value)
        warning_count = sum(1 for r in ordered if r.status != "pass" and r.severity == Severity.WARNING.value)
        total = len(ordered)
        summary = f"{passed_count}/{total} passed, {error_count} error(s), {warning_count} warning(s)"
        return ValidationSuiteResult(
            target=target,
            results=ordered,
            passed=error_count == 0,
            summary=summary,
            passed_count=passed_count,
            error_count=error_count,
            warning_count=warning_count,
            duration_secs=duration,
        )
