"""Agent interface, contexts, responses, and implementations (CLI, Claude, mock).

All agent-related types live in this single module so that other packages
(builder, validations, cli, differencing) have one place to import from.
"""

from __future__ import annotations

import importlib.resources
import json
import shlex
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import BaseModel, ConfigDict, Field

from intentc.core import (
    Implementation,
    IntentFile,
    ProjectIntent,
    Validation,
    ValidationFile,
)


class AgentError(Exception):
    """Raised when an agent invocation fails."""


LogFn = Callable[[str], None]


def _noop_log(_message: str) -> None:
    return None


# ---------------------------------------------------------------------------
# Response types
# ---------------------------------------------------------------------------


class BuildResponse(BaseModel):
    """Written by the agent after a build invocation."""

    model_config = ConfigDict(extra="ignore")

    status: str
    summary: str = ""
    files_created: list[str] = Field(default_factory=list)
    files_modified: list[str] = Field(default_factory=list)


class ValidationResponse(BaseModel):
    """Written by the agent after a single validation is evaluated."""

    model_config = ConfigDict(extra="ignore")

    name: str
    status: str
    reason: str = ""
    severity: str = "error"
    type: str = "agent_validation"
    duration_secs: float = 0.0


class DimensionResult(BaseModel):
    """A single differencing dimension's outcome."""

    model_config = ConfigDict(extra="ignore")

    name: str
    status: str
    rationale: str = ""


class DifferencingResponse(BaseModel):
    """Written by the agent after a differencing evaluation."""

    model_config = ConfigDict(extra="ignore")

    status: str
    dimensions: list[DimensionResult] = Field(default_factory=list)
    summary: str = ""


# ---------------------------------------------------------------------------
# Contexts
# ---------------------------------------------------------------------------


class BuildContext(BaseModel):
    """Everything an agent needs to act on a target."""

    model_config = ConfigDict(extra="ignore")

    intent: IntentFile
    validations: list[ValidationFile] = Field(default_factory=list)
    output_dir: str
    generation_id: str
    dependency_names: list[str] = Field(default_factory=list)
    project_intent: ProjectIntent
    implementation: Optional[Implementation] = None
    response_file_path: str
    previous_errors: list[str] = Field(default_factory=list)
    seed_prompt: str = ""
    feature_path: str = ""


class DifferencingContext(BaseModel):
    """Everything an agent needs to perform a differencing evaluation."""

    model_config = ConfigDict(extra="ignore")

    output_dir_a: str
    output_dir_b: str
    project_intent: ProjectIntent
    response_file_path: str
    implementation: Optional[Implementation] = None


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------


class PromptTemplates(BaseModel):
    """Prompt template text for each agent action."""

    model_config = ConfigDict(extra="ignore")

    build: str = ""
    validate_template: str = ""
    plan: str = ""
    difference: str = ""


def _read_bundled_prompt(package: str, filename: str) -> str:
    """Read a bundled prompt file via importlib.resources, or "" if unavailable."""
    try:
        resource = importlib.resources.files(package).joinpath("prompts", filename)
        if resource.is_file():
            return resource.read_text(encoding="utf-8")
    except (ModuleNotFoundError, FileNotFoundError, NotADirectoryError):
        pass
    return ""


def load_default_prompts() -> PromptTemplates:
    """Load the default prompt templates bundled with the installed package."""
    return PromptTemplates(
        build=_read_bundled_prompt("intentc.build.agents", "build.prompt"),
        validate_template=_read_bundled_prompt("intentc.build.agents", "validate.prompt"),
        plan=_read_bundled_prompt("intentc.build.agents", "plan.prompt"),
        difference=_read_bundled_prompt("intentc.differencing", "difference.prompt"),
    )


class _SafeDict(dict):
    """A dict that renders missing keys as empty strings for str.format_map."""

    def __missing__(self, key: str) -> str:
        return ""


def _safe_format(template: str, variables: dict[str, Any]) -> str:
    return template.format_map(_SafeDict(variables))


def _render_validation_entry(validation: Validation) -> str:
    severity = validation.severity.value if hasattr(validation.severity, "value") else validation.severity
    lines = [
        f"- name: {validation.name}",
        f"  type: {validation.type}",
        f"  severity: {severity}",
    ]
    if validation.type == "command_validation":
        if "command" in validation.args:
            lines.append(f"  command: {validation.args['command']}")
        if "cwd" in validation.args:
            lines.append(f"  cwd: {validation.args['cwd']}")
    elif validation.type == "file_exists":
        paths = validation.args.get("paths", [])
        if paths:
            lines.append("  paths:")
            for path in paths:
                lines.append(f"    - {path}")
    elif validation.type == "agent_validation":
        rubric = validation.args.get("rubric")
        if rubric:
            lines.append("  rubric: |")
            for rubric_line in str(rubric).splitlines():
                lines.append(f"    {rubric_line}")
    else:
        for key, value in validation.args.items():
            lines.append(f"  {key}: {value}")
    return "\n".join(lines)


def _render_validations(validation_files: list[ValidationFile]) -> str:
    entries = [
        _render_validation_entry(validation)
        for validation_file in validation_files
        for validation in validation_file.validations
    ]
    return "\n\n".join(entries) if entries else "(none)"


def _render_dependencies(dependency_names: list[str]) -> str:
    if not dependency_names:
        return "(none)"
    return "\n".join(f"- {name}" for name in dependency_names)


def _render_previous_errors(previous_errors: list[str]) -> str:
    if not previous_errors:
        return ""
    bullets = "\n".join(f"- {error}" for error in previous_errors)
    return f"\n### Previous Attempt Errors\n{bullets}"


def render_prompt(
    template: str,
    ctx: BuildContext,
    validation: Optional[Validation] = None,
) -> str:
    """Render a build/validate/plan prompt template against a BuildContext."""
    variables = {
        "project": ctx.project_intent.body,
        "implementation": ctx.implementation.body if ctx.implementation else "",
        "feature": ctx.intent.body,
        "feature_name": ctx.feature_path or ctx.intent.name,
        "output_dir": ctx.output_dir,
        "dependencies": _render_dependencies(ctx.dependency_names),
        "validations": _render_validations(ctx.validations),
        "validation": _render_validation_entry(validation) if validation else "",
        "response_file": ctx.response_file_path,
        "previous_errors": _render_previous_errors(ctx.previous_errors),
        "seed_prompt": ctx.seed_prompt,
    }
    return _safe_format(template, variables)


def render_differencing_prompt(template: str, ctx: DifferencingContext) -> str:
    """Render a differencing prompt template against a DifferencingContext."""
    variables = {
        "project": ctx.project_intent.body,
        "implementation": ctx.implementation.body if ctx.implementation else "",
        "output_dir_a": ctx.output_dir_a,
        "output_dir_b": ctx.output_dir_b,
        "response_file": ctx.response_file_path,
    }
    return _safe_format(template, variables)


def _read_response_file(path: str, model_cls: type[BaseModel]) -> Any:
    response_path = Path(path)
    try:
        raw_text = response_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise AgentError(f"Agent response file not found: {path}") from exc
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise AgentError(f"Invalid JSON in agent response file {path}: {exc}") from exc
    response = model_cls.model_validate(data)
    response_path.unlink(missing_ok=True)
    return response


def _synthesize_build_response(output_dir: str) -> BuildResponse:
    base = Path(output_dir)
    files: list[str] = []
    if base.exists():
        files = sorted(str(p.relative_to(base)) for p in base.rglob("*") if p.is_file())
    return BuildResponse(
        status="success",
        summary="Response file missing; synthesized from files present in the output directory.",
        files_created=files,
        files_modified=[],
    )


# ---------------------------------------------------------------------------
# Agent interface
# ---------------------------------------------------------------------------


class Agent(ABC):
    """Common interface implemented by every agent provider."""

    @abstractmethod
    def build(self, ctx: BuildContext) -> BuildResponse: ...

    @abstractmethod
    def validate(self, ctx: BuildContext, validation: Validation) -> ValidationResponse: ...

    @abstractmethod
    def difference(self, ctx: DifferencingContext) -> DifferencingResponse: ...

    @abstractmethod
    def plan(self, ctx: BuildContext) -> None: ...

    @abstractmethod
    def get_name(self) -> str: ...

    @abstractmethod
    def get_type(self) -> str: ...


# ---------------------------------------------------------------------------
# AgentProfile
# ---------------------------------------------------------------------------

_VALID_PERMISSION_MODES = ("auto", "acceptEdits", "dontAsk", "plan", "bypassPermissions")


class AgentProfile(BaseModel):
    """Named, reusable agent configuration."""

    model_config = ConfigDict(extra="ignore")

    name: str
    provider: str
    command: str = ""
    cli_args: list[str] = Field(default_factory=list)
    timeout: float = 3600.0
    retries: int = 3
    model_id: Optional[str] = None
    effort: Optional[str] = None
    permission_mode: str = "auto"
    prompt_templates: Optional[PromptTemplates] = None
    sandbox_write_paths: list[str] = Field(default_factory=list)
    sandbox_read_paths: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# CLIAgent
# ---------------------------------------------------------------------------


class CLIAgent(Agent):
    """Generic base agent that wraps any non-interactive command-line tool."""

    def __init__(self, profile: AgentProfile, log: Optional[LogFn] = None) -> None:
        self.profile = profile
        self.log: LogFn = log or _noop_log
        self.templates = profile.prompt_templates or load_default_prompts()

    def get_name(self) -> str:
        return self.profile.name

    def get_type(self) -> str:
        return "cli"

    def _invoke(self, prompt: str) -> None:
        if not self.profile.command:
            raise AgentError(f"Agent profile '{self.profile.name}' has no command configured")
        command = shlex.split(self.profile.command) + list(self.profile.cli_args) + [prompt]
        self.log(f"    agent: running {self.profile.command}")
        try:
            result = subprocess.run(
                command,
                timeout=self.profile.timeout,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AgentError(f"Failed to invoke command '{self.profile.command}': {exc}") from exc
        for line in (result.stdout or "").splitlines():
            self.log(f"    agent: {line}")

    def build(self, ctx: BuildContext) -> BuildResponse:
        prompt = render_prompt(self.templates.build, ctx)
        self._invoke(prompt)
        return _read_response_file(ctx.response_file_path, BuildResponse)

    def validate(self, ctx: BuildContext, validation: Validation) -> ValidationResponse:
        prompt = render_prompt(self.templates.validate_template, ctx, validation=validation)
        self._invoke(prompt)
        return _read_response_file(ctx.response_file_path, ValidationResponse)

    def difference(self, ctx: DifferencingContext) -> DifferencingResponse:
        prompt = render_differencing_prompt(self.templates.difference, ctx)
        self._invoke(prompt)
        return _read_response_file(ctx.response_file_path, DifferencingResponse)

    def plan(self, ctx: BuildContext) -> None:
        prompt = render_prompt(self.templates.plan, ctx)
        self._invoke(prompt)


# ---------------------------------------------------------------------------
# ClaudeAgent
# ---------------------------------------------------------------------------


class ClaudeAgent(Agent):
    """Specialization for the Claude Code CLI."""

    def __init__(self, profile: AgentProfile, log: Optional[LogFn] = None) -> None:
        self.profile = profile
        self.log: LogFn = log or _noop_log
        self.templates = profile.prompt_templates or load_default_prompts()

    def get_name(self) -> str:
        return self.profile.name

    def get_type(self) -> str:
        return "claude"

    def _permission_flags(self, interactive: bool) -> list[str]:
        mode = self.profile.permission_mode
        if mode == "bypassPermissions":
            return ["--dangerously-skip-permissions"]
        if mode not in _VALID_PERMISSION_MODES:
            raise AgentError(
                f"Unknown permission_mode '{mode}' for agent profile '{self.profile.name}'"
            )
        if interactive:
            return ["--permission-mode", mode]
        return ["--permission-mode", mode, "--permission-prompts", "none"]

    def _build_command(self, prompt: str, interactive: bool) -> list[str]:
        if interactive:
            command = ["claude"]
            if self.profile.model_id:
                command += ["--model", self.profile.model_id]
            command += self._permission_flags(interactive=True)
            command += list(self.profile.cli_args)
            command.append(prompt)
            return command

        command = ["claude", "-p", prompt, "--verbose", "--output-format", "stream-json"]
        if self.profile.model_id:
            command += ["--model", self.profile.model_id]
        if self.profile.effort:
            command += ["--effort", self.profile.effort]
        command += self._permission_flags(interactive=False)
        command += list(self.profile.cli_args)
        return command

    def _write_sandbox_settings(self) -> Optional[Path]:
        if not (self.profile.sandbox_write_paths or self.profile.sandbox_read_paths):
            return None
        settings_dir = Path(".claude")
        settings_dir.mkdir(parents=True, exist_ok=True)
        settings_path = settings_dir / "settings.local.json"
        settings = {
            "sandbox": {
                "enabled": True,
                "network": "allow",
                "allowedWritePaths": list(self.profile.sandbox_write_paths),
                "allowedReadPaths": list(self.profile.sandbox_read_paths),
            }
        }
        settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        return settings_path

    def _cleanup_sandbox_settings(self, settings_path: Optional[Path]) -> None:
        if settings_path is not None and settings_path.exists():
            settings_path.unlink()

    def _log_stream_line(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return
        event_type = event.get("type")
        if event_type == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "text" and block.get("text"):
                    self.log(f"    agent: {block['text']}")
        elif event_type == "result":
            result_text = event.get("result")
            if result_text:
                self.log(f"    agent: {result_text}")

    def _run_noninteractive(self, prompt: str) -> None:
        command = self._build_command(prompt, interactive=False)
        settings_path = self._write_sandbox_settings()
        self.log("    agent: starting claude")
        try:
            try:
                proc = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
            except OSError as exc:
                raise AgentError(f"Failed to launch claude: {exc}") from exc
            try:
                for line in proc.stdout or []:
                    self._log_stream_line(line)
                proc.wait(timeout=self.profile.timeout)
            except subprocess.TimeoutExpired as exc:
                proc.kill()
                raise AgentError(f"claude invocation timed out after {self.profile.timeout}s") from exc
        finally:
            self._cleanup_sandbox_settings(settings_path)

    def build(self, ctx: BuildContext) -> BuildResponse:
        prompt = render_prompt(self.templates.build, ctx)
        self._run_noninteractive(prompt)
        if not Path(ctx.response_file_path).exists():
            self.log(
                f"    agent: response file missing at {ctx.response_file_path}; "
                "synthesizing BuildResponse from output directory"
            )
            return _synthesize_build_response(ctx.output_dir)
        return _read_response_file(ctx.response_file_path, BuildResponse)

    def validate(self, ctx: BuildContext, validation: Validation) -> ValidationResponse:
        prompt = render_prompt(self.templates.validate_template, ctx, validation=validation)
        self._run_noninteractive(prompt)
        return _read_response_file(ctx.response_file_path, ValidationResponse)

    def difference(self, ctx: DifferencingContext) -> DifferencingResponse:
        prompt = render_differencing_prompt(self.templates.difference, ctx)
        self._run_noninteractive(prompt)
        return _read_response_file(ctx.response_file_path, DifferencingResponse)

    def plan(self, ctx: BuildContext) -> None:
        prompt = render_prompt(self.templates.plan, ctx)
        command = self._build_command(prompt, interactive=True)
        self.log("    agent: entering interactive plan session")
        try:
            subprocess.run(command, check=False)
        except OSError as exc:
            raise AgentError(f"Failed to launch claude in interactive mode: {exc}") from exc


# ---------------------------------------------------------------------------
# MockAgent
# ---------------------------------------------------------------------------


class MockAgent(Agent):
    """Records all calls; returns configurable responses. For testing intentc itself."""

    def __init__(
        self,
        name: str = "mock",
        build_response: Optional[BuildResponse] = None,
        validation_response: Optional[ValidationResponse] = None,
        differencing_response: Optional[DifferencingResponse] = None,
    ) -> None:
        self.name = name
        self.build_calls: list[BuildContext] = []
        self.validate_calls: list[tuple[BuildContext, Validation]] = []
        self.difference_calls: list[DifferencingContext] = []
        self.plan_calls: list[BuildContext] = []
        self.build_response = build_response or BuildResponse(
            status="success", summary="mock build", files_created=[], files_modified=[]
        )
        self.validation_response = validation_response or ValidationResponse(
            name="mock", status="pass", reason="mock validation"
        )
        self.differencing_response = differencing_response or DifferencingResponse(
            status="equivalent", dimensions=[], summary="mock difference"
        )

    def build(self, ctx: BuildContext) -> BuildResponse:
        self.build_calls.append(ctx)
        return self.build_response

    def validate(self, ctx: BuildContext, validation: Validation) -> ValidationResponse:
        self.validate_calls.append((ctx, validation))
        return self.validation_response

    def difference(self, ctx: DifferencingContext) -> DifferencingResponse:
        self.difference_calls.append(ctx)
        return self.differencing_response

    def plan(self, ctx: BuildContext) -> None:
        self.plan_calls.append(ctx)

    def get_name(self) -> str:
        return self.name

    def get_type(self) -> str:
        return "mock"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_from_profile(profile: AgentProfile, log: Optional[LogFn] = None) -> Agent:
    """Construct the appropriate Agent implementation for the profile's provider."""
    if profile.provider == "claude":
        return ClaudeAgent(profile, log=log)
    if profile.provider == "cli":
        return CLIAgent(profile, log=log)
    raise AgentError(f"Unknown agent provider '{profile.provider}' for profile '{profile.name}'")
