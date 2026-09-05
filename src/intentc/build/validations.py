"""Validations: runners, the ValidationSuite orchestrator, and suite results."""

from __future__ import annotations

import abc
import glob
import json
import os
import secrets
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from intentc.build.agents import (
    Agent,
    AgentProfile,
    BuildContext,
    ValidationResponse,
    create_from_profile,
)
from intentc.build.storage import StorageBackend
from intentc.core.models import (
    Implementation,
    IntentFile,
    ProjectIntent,
    Severity,
    Validation,
    ValidationType,
)
from intentc.core.project import Project

LogFn = Callable[[str], None]
DETERMINISTIC_TYPES = {ValidationType.COMMAND_VALIDATION.value, ValidationType.FILE_EXISTS.value}
_OUTPUT_TAIL_LINES = 30


def _noop(_message: str) -> None:
    return None


@dataclass
class ValidationContext:
    """What a runner needs to evaluate one validation."""

    project_intent: ProjectIntent
    implementation: Implementation | None
    feature_intent: IntentFile
    output_dir: str
    response_file_path: str
    project_root: str = "."
    feature_path: str = ""


@dataclass
class ValidationSuiteResult:
    target: str
    results: list[ValidationResponse] = field(default_factory=list)
    passed: bool = True
    summary: str = ""
    passed_count: int = 0
    error_count: int = 0
    warning_count: int = 0
    duration_secs: float = 0.0


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------


class ValidationRunner(abc.ABC):
    @abc.abstractmethod
    def run(self, validation: Validation, ctx: ValidationContext) -> ValidationResponse: ...

    @abc.abstractmethod
    def type(self) -> str: ...


class AgentValidationRunner(ValidationRunner):
    """Asks an agent to judge a rubric. The agent is created by the suite and injected."""

    def __init__(self, agent: Agent) -> None:
        self._agent = agent

    def type(self) -> str:
        return ValidationType.AGENT_VALIDATION.value

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
            feature_path=ctx.feature_path,
        )
        try:
            response = self._agent.validate(build_ctx, validation)
        except Exception as exc:  # noqa: BLE001 - any agent failure is a validation failure
            return ValidationResponse(name=validation.name, status="fail", reason=f"Agent error: {exc}")
        if not response.name:
            response.name = validation.name
        return response


def _substitute(value: str, ctx: ValidationContext) -> str:
    return value.replace("{output_dir}", ctx.output_dir)


class CommandValidationRunner(ValidationRunner):
    """Runs a shell command; exit code 0 passes."""

    def type(self) -> str:
        return ValidationType.COMMAND_VALIDATION.value

    def run(self, validation: Validation, ctx: ValidationContext) -> ValidationResponse:
        args = validation.args
        command = _substitute(str(args.get("command", "")), ctx)
        if not command.strip():
            return ValidationResponse(name=validation.name, status="fail", reason="command_validation has no 'command'")
        cwd_arg = str(args.get("cwd", "") or "")
        root = Path(ctx.project_root or ".")
        if not cwd_arg:
            cwd = Path(ctx.output_dir)
            if not cwd.is_absolute():
                cwd = root / cwd
        elif cwd_arg == ".":
            cwd = root
        else:
            cwd = Path(_substitute(cwd_arg, ctx))
            if not cwd.is_absolute():
                cwd = root / cwd
        if not cwd.is_dir():
            return ValidationResponse(
                name=validation.name, status="fail", reason=f"working directory does not exist: {cwd}"
            )
        try:
            timeout = float(args.get("timeout", 600) or 600)
        except (TypeError, ValueError):
            timeout = 600.0
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return ValidationResponse(
                name=validation.name, status="fail", reason=f"command timed out after {timeout:.0f}s: {command}"
            )
        output = (proc.stdout or "") + (proc.stderr or "")
        lines = output.strip().splitlines()
        tail = "\n".join(lines[-_OUTPUT_TAIL_LINES:])
        expect = args.get("expect_output")
        if proc.returncode != 0:
            return ValidationResponse(
                name=validation.name,
                status="fail",
                reason=f"command exited {proc.returncode}: {command}\n{tail}".rstrip(),
            )
        if expect:
            expected = _substitute(str(expect), ctx)
            if expected not in output:
                return ValidationResponse(
                    name=validation.name,
                    status="fail",
                    reason=f"expected output to contain {expected!r}: {command}\n{tail}".rstrip(),
                )
        last = lines[-1] if lines else ""
        return ValidationResponse(name=validation.name, status="pass", reason=f"exit 0{': ' + last if last else ''}")


class FileExistsRunner(ValidationRunner):
    """Every path/glob (relative to the output directory) must match something."""

    def type(self) -> str:
        return ValidationType.FILE_EXISTS.value

    def run(self, validation: Validation, ctx: ValidationContext) -> ValidationResponse:
        paths = validation.args.get("paths") or []
        if not isinstance(paths, list) or not paths:
            return ValidationResponse(name=validation.name, status="fail", reason="file_exists has no 'paths'")
        base = Path(ctx.output_dir)
        if not base.is_absolute():
            base = Path(ctx.project_root or ".") / base
        root = Path(ctx.project_root or ".")
        missing: list[str] = []
        for entry in paths:
            raw = str(entry)
            pattern = _substitute(raw, ctx)
            candidate = Path(pattern)
            if candidate.is_absolute():
                full = str(candidate)
            elif "{output_dir}" in raw:
                full = str(root / candidate)  # already anchored at the output dir by the placeholder
            else:
                full = str(base / candidate)
            if not glob.glob(full, recursive=True):
                missing.append(pattern)
        if missing:
            return ValidationResponse(
                name=validation.name,
                status="fail",
                reason=f"missing in {ctx.output_dir}: {', '.join(missing)}",
            )
        return ValidationResponse(name=validation.name, status="pass", reason=f"all {len(paths)} path(s) exist")


# ---------------------------------------------------------------------------
# Suite
# ---------------------------------------------------------------------------


class ValidationSuite:
    """Runs validations for a feature, a set of entries, or the whole project."""

    def __init__(
        self,
        project: Project,
        agent_profile: AgentProfile,
        output_dir: str,
        runner_registry: dict[str, ValidationRunner] | None = None,
        val_response_dir: Path | None = None,
        storage_backend: StorageBackend | None = None,
        log: LogFn | None = None,
        implementation: Implementation | None = None,
        build_result_id: int | None = None,
        generation_id: str | None = None,
        create_agent: Callable[[AgentProfile], Agent] | None = None,
    ) -> None:
        self._project = project
        self._agent_profile = agent_profile
        self._output_dir = output_dir
        self._val_response_dir = val_response_dir
        self._storage = storage_backend
        self._log = log or _noop
        self._implementation = implementation
        self._build_result_id = build_result_id
        self._generation_id = generation_id
        self._create_agent = create_agent or (lambda profile: create_from_profile(profile, log=self._log))
        self._agent: Agent | None = None
        self._runners: dict[str, ValidationRunner] = {}
        for runner in (CommandValidationRunner(), FileExistsRunner()):
            self._runners[runner.type()] = runner
        if runner_registry:
            self._runners.update(runner_registry)

    # Agent creation is lazy so deterministic-only suites never touch an agent.
    def _agent_runner(self) -> ValidationRunner:
        runner = self._runners.get(ValidationType.AGENT_VALIDATION.value)
        if runner is None:
            if self._agent is None:
                self._agent = self._create_agent(self._agent_profile)
            runner = AgentValidationRunner(self._agent)
            self._runners[runner.type()] = runner
        return runner

    def register_runner(self, runner: ValidationRunner) -> None:
        self._runners[runner.type()] = runner

    def _runner_for(self, vtype: str) -> ValidationRunner | None:
        if vtype == ValidationType.AGENT_VALIDATION.value:
            return self._agent_runner()
        return self._runners.get(vtype)

    # -- public API ----------------------------------------------------------

    def validate_feature(self, feature: str) -> ValidationSuiteResult:
        if feature == "project":
            entries = [v for vf in self._project.assertions for v in vf.validations]
            self._log(f"Running project-level assertions ({len(entries)} entries)...")
            return self.validate_entries("project", entries)
        if feature not in self._project.features:
            return ValidationSuiteResult(
                target=feature, passed=True, summary=f"Feature '{feature}' not found — nothing to validate"
            )
        node = self._project.features[feature]
        entries = [v for vf in node.validations for v in vf.validations]
        self._log(f"Validating feature '{feature}'... ({len(entries)} validations)")
        return self.validate_entries(feature, entries)

    def validate_project(self) -> list[ValidationSuiteResult]:
        order = self._project.topological_order()
        self._log(f"Validating project ({len(order)} features)...")
        results = [self.validate_feature(fp) for fp in order]
        assertions = [v for vf in self._project.assertions for v in vf.validations]
        if assertions:
            self._log(f"Running project-level assertions ({len(assertions)} entries)...")
            results.append(self.validate_entries("project", assertions))
        return results

    def validate_entries(self, target: str, entries: list[Validation]) -> ValidationSuiteResult:
        started = time.monotonic()
        if not entries:
            return self._finish(target, entries, [], started)

        self._current_target = target
        ctx = self._context(target)
        responses: dict[int, ValidationResponse] = {}

        deterministic = [(i, e) for i, e in enumerate(entries) if e.type in DETERMINISTIC_TYPES]
        agentic = [(i, e) for i, e in enumerate(entries) if e.type not in DETERMINISTIC_TYPES]

        blocking_failure: str | None = None
        for index, entry in deterministic:
            responses[index] = self._run_one(entry, ctx)
            if responses[index].status != "pass" and entry.severity == Severity.ERROR and blocking_failure is None:
                blocking_failure = entry.name

        if agentic:
            if blocking_failure is not None:
                for index, entry in agentic:
                    resp = ValidationResponse(
                        name=entry.name,
                        status="fail",
                        reason=f"skipped: deterministic validation '{blocking_failure}' failed",
                    )
                    self._stamp(entry, resp, 0.0)
                    self._log(f"  Validation '{entry.name}': skipped ({blocking_failure} failed)")
                    responses[index] = resp
            else:
                with ThreadPoolExecutor(max_workers=max(1, min(8, len(agentic)))) as pool:
                    futures = {index: pool.submit(self._run_one, entry, ctx) for index, entry in agentic}
                    for index, future in futures.items():
                        responses[index] = future.result()

        ordered = [responses[i] for i in range(len(entries))]
        return self._finish(target, entries, ordered, started)

    # -- internals -----------------------------------------------------------

    def _context(self, target: str) -> ValidationContext:
        implementation = self._implementation
        if implementation is None:
            try:
                implementation = self._project.resolve_implementation()
            except (KeyError, ValueError):
                implementation = None
        if target == "project":
            feature_intent = IntentFile(name="project", body=self._project.project_intent.body)
        elif target in self._project.features and self._project.features[target].intents:
            feature_intent = self._project.features[target].intents[0]
        else:
            feature_intent = IntentFile(name=target, body="")
        root = str(self._project.intent_dir.parent) if self._project.intent_dir else os.getcwd()
        return ValidationContext(
            project_intent=self._project.project_intent,
            implementation=implementation,
            feature_intent=feature_intent,
            output_dir=self._output_dir,
            response_file_path="",
            project_root=root,
            feature_path=target,
        )

    def _response_path(self, name: str) -> Path:
        base = self._val_response_dir if self._val_response_dir is not None else Path(self._output_dir)
        base.mkdir(parents=True, exist_ok=True)
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)
        return base / f"val-{safe}-{secrets.token_hex(4)}.json"

    def _run_one(self, entry: Validation, ctx: ValidationContext) -> ValidationResponse:
        self._log(f"  Running validation '{entry.name}' ({entry.type})...")
        started = time.monotonic()
        runner = self._runner_for(entry.type)
        response_file = self._response_path(entry.name)
        if runner is None:
            response = ValidationResponse(
                name=entry.name,
                status="fail",
                reason=f"No runner registered for validation type '{entry.type}'",
            )
        else:
            run_ctx = ValidationContext(**{**ctx.__dict__, "response_file_path": str(response_file)})
            try:
                response = runner.run(entry, run_ctx)
            except Exception as exc:  # noqa: BLE001
                response = ValidationResponse(name=entry.name, status="fail", reason=f"Runner error: {exc}")
        duration = time.monotonic() - started
        self._stamp(entry, response, duration)
        self._log(f"  Validation '{entry.name}': {response.status} ({duration:.1f}s)")
        if response.status != "pass":
            for line in response.reason.splitlines()[:5]:
                self._log(f"    reason: {line}")
        self._persist(entry, response, response_file)
        return response

    @staticmethod
    def _stamp(entry: Validation, response: ValidationResponse, duration: float) -> None:
        response.severity = entry.severity.value
        response.type = entry.type
        response.duration_secs = round(duration, 3)
        if not response.name:
            response.name = entry.name

    def _persist(self, entry: Validation, response: ValidationResponse, response_file: Path) -> None:
        raw: dict[str, Any] | None = None
        if response_file.exists():
            try:
                raw = json.loads(response_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = None
            finally:
                try:
                    response_file.unlink()
                except OSError:
                    pass
        if self._storage is None:
            return
        generation_id = self._generation_id or f"val-{secrets.token_hex(4)}"
        self._storage.create_generation(generation_id, self._output_dir)
        result_id = self._storage.save_validation_result(
            self._build_result_id,
            generation_id,
            self._current_target,
            None,
            response.name,
            entry.type,
            entry.severity.value,
            response.status,
            response.reason,
            response.duration_secs,
        )
        if isinstance(raw, dict):
            self._storage.save_agent_response(None, result_id, "validation", raw)

    _current_target: str = ""

    def _finish(
        self, target: str, entries: list[Validation], results: list[ValidationResponse], started: float
    ) -> ValidationSuiteResult:
        passed_count = sum(1 for r in results if r.status == "pass")
        failures = [(r, e) for r, e in zip(results, entries) if r.status != "pass"]
        error_count = sum(1 for _, e in failures if e.severity == Severity.ERROR)
        warning_count = sum(1 for _, e in failures if e.severity == Severity.WARNING)
        total = len(entries)
        return ValidationSuiteResult(
            target=target,
            results=results,
            passed=error_count == 0,
            summary=f"{passed_count}/{total} passed, {error_count} error(s), {warning_count} warning(s)",
            passed_count=passed_count,
            error_count=error_count,
            warning_count=warning_count,
            duration_secs=round(time.monotonic() - started, 3),
        )
