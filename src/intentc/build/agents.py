"""Agent interface, implementations, and supporting types for intentc."""

from __future__ import annotations

import abc
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from intentc.core.types import (
    Implementation,
    IntentFile,
    ProjectIntent,
    Validation,
    ValidationFile,
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AgentError(Exception):
    """Raised when an agent invocation fails."""


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------


class PromptTemplates(BaseModel):
    model_config = {"extra": "ignore"}

    build: str = ""
    validate_template: str = ""
    plan: str = ""
    difference: str = ""


def prompts_dir() -> Path:
    """Return the path to build agent prompt templates."""
    return Path.cwd() / "intent" / "build" / "agents" / "prompts"


def diff_prompts_dir() -> Path:
    """Return the path to differencing prompt templates."""
    return Path.cwd() / "intent" / "differencing" / "prompts"


def load_default_prompts() -> PromptTemplates:
    """Load default prompt templates from disk. Missing files yield empty strings."""
    templates: dict[str, str] = {}
    mapping = {
        "build": prompts_dir() / "build.prompt",
        "validate_template": prompts_dir() / "validate.prompt",
        "plan": prompts_dir() / "plan.prompt",
        "difference": diff_prompts_dir() / "difference.prompt",
    }
    for field, path in mapping.items():
        try:
            templates[field] = path.read_text(encoding="utf-8")
        except OSError:
            templates[field] = ""
    return PromptTemplates(**templates)


# ---------------------------------------------------------------------------
# Response types
# ---------------------------------------------------------------------------


class BuildResponse(BaseModel):
    model_config = {"extra": "ignore"}

    status: str
    summary: str
    files_created: list[str] = Field(default_factory=list)
    files_modified: list[str] = Field(default_factory=list)


class ValidationResponse(BaseModel):
    model_config = {"extra": "ignore"}

    name: str
    status: str
    reason: str


class DimensionResult(BaseModel):
    model_config = {"extra": "ignore"}

    name: str
    status: str
    rationale: str


class DifferencingResponse(BaseModel):
    model_config = {"extra": "ignore"}

    status: str
    dimensions: list[DimensionResult] = Field(default_factory=list)
    summary: str


# ---------------------------------------------------------------------------
# Contexts
# ---------------------------------------------------------------------------


class BuildContext(BaseModel):
    model_config = {"extra": "ignore"}

    intent: IntentFile
    validations: list[ValidationFile] = Field(default_factory=list)
    output_dir: str
    generation_id: str
    dependency_names: list[str] = Field(default_factory=list)
    project_intent: ProjectIntent
    implementation: Implementation | None = None
    response_file_path: str


class DifferencingContext(BaseModel):
    model_config = {"extra": "ignore"}

    output_dir_a: str
    output_dir_b: str
    project_intent: ProjectIntent
    response_file_path: str
    implementation: Implementation | None = None


# ---------------------------------------------------------------------------
# Agent profile
# ---------------------------------------------------------------------------


class AgentProfile(BaseModel):
    model_config = {"extra": "ignore"}

    name: str
    provider: str
    command: str = ""
    cli_args: list[str] = Field(default_factory=list)
    timeout: float = 3600.0
    retries: int = 3
    model_id: str | None = None
    prompt_templates: PromptTemplates | None = None
    sandbox_write_paths: list[str] = Field(default_factory=list)
    sandbox_read_paths: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------


def _get_templates(profile: AgentProfile | None) -> PromptTemplates:
    """Return the profile's custom templates or the defaults."""
    if profile is not None and profile.prompt_templates is not None:
        return profile.prompt_templates
    return load_default_prompts()


def render_prompt(
    template_kind: str,
    ctx: BuildContext,
    *,
    validation: Validation | None = None,
    profile: AgentProfile | None = None,
) -> str:
    """Render a build/validate/plan prompt template with context values."""
    templates = _get_templates(profile)
    template = getattr(templates, template_kind, "")
    if not template:
        return ""

    project_body = ctx.project_intent.body if ctx.project_intent else ""
    impl_body = ctx.implementation.body if ctx.implementation else ""
    feature_body = ctx.intent.body if ctx.intent else ""

    # Collect all validation text
    validations_text = ""
    for vf in ctx.validations:
        for v in vf.validations:
            validations_text += f"- {v.name} ({v.type}, {v.severity.value}): {v.args}\n"

    result = template.replace("{project}", project_body)
    result = result.replace("{implementation}", impl_body)
    result = result.replace("{feature}", feature_body)
    result = result.replace("{validations}", validations_text)
    result = result.replace("{response_file}", ctx.response_file_path)

    if validation is not None:
        val_text = f"{validation.name} ({validation.type}, {validation.severity.value}): {validation.args}"
        result = result.replace("{validation}", val_text)

    return result


def render_differencing_prompt(
    ctx: DifferencingContext,
    *,
    profile: AgentProfile | None = None,
) -> str:
    """Render the differencing prompt template with context values."""
    templates = _get_templates(profile)
    template = templates.difference
    if not template:
        return ""

    project_body = ctx.project_intent.body if ctx.project_intent else ""
    impl_body = ctx.implementation.body if ctx.implementation else ""

    result = template.replace("{project}", project_body)
    result = result.replace("{implementation}", impl_body)
    result = result.replace("{output_dir_a}", ctx.output_dir_a)
    result = result.replace("{output_dir_b}", ctx.output_dir_b)
    result = result.replace("{response_file}", ctx.response_file_path)

    return result


# ---------------------------------------------------------------------------
# Agent ABC
# ---------------------------------------------------------------------------


class Agent(abc.ABC):
    """Interface that all agents must implement."""

    @abc.abstractmethod
    def build(self, ctx: BuildContext) -> BuildResponse: ...

    @abc.abstractmethod
    def validate(self, ctx: BuildContext, validation: Validation) -> ValidationResponse: ...

    @abc.abstractmethod
    def difference(self, ctx: DifferencingContext) -> DifferencingResponse: ...

    @abc.abstractmethod
    def plan(self, ctx: BuildContext) -> None: ...

    @abc.abstractmethod
    def get_name(self) -> str: ...

    @abc.abstractmethod
    def get_type(self) -> str: ...


# ---------------------------------------------------------------------------
# Response file helpers
# ---------------------------------------------------------------------------


def _read_response_file(path: str, response_cls: type[BaseModel]) -> BaseModel:
    """Read and parse a JSON response file, then delete it."""
    p = Path(path)
    if not p.exists():
        raise AgentError(f"Response file not found: {path}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise AgentError(f"Invalid response file {path}: {exc}") from exc
    finally:
        p.unlink(missing_ok=True)
    return response_cls.model_validate(data)


# ---------------------------------------------------------------------------
# CLIAgent
# ---------------------------------------------------------------------------


class CLIAgent(Agent):
    """Generic agent that wraps any command-line tool."""

    def __init__(self, profile: AgentProfile) -> None:
        self._profile = profile

    def get_name(self) -> str:
        return self._profile.name

    def get_type(self) -> str:
        return "cli"

    def _run_command(self, prompt: str) -> None:
        """Execute the CLI command with the prompt as stdin."""
        if not self._profile.command:
            raise AgentError("CLIAgent requires a command in the profile")

        cmd = self._profile.command.split() + list(self._profile.cli_args)
        try:
            subprocess.run(
                cmd,
                input=prompt,
                text=True,
                timeout=self._profile.timeout,
                check=True,
            )
        except subprocess.TimeoutExpired as exc:
            raise AgentError(f"Agent timed out after {self._profile.timeout}s") from exc
        except subprocess.CalledProcessError as exc:
            raise AgentError(f"Agent command failed with exit code {exc.returncode}") from exc

    def build(self, ctx: BuildContext) -> BuildResponse:
        prompt = render_prompt("build", ctx, profile=self._profile)
        self._run_command(prompt)
        return _read_response_file(ctx.response_file_path, BuildResponse)  # type: ignore[return-value]

    def validate(self, ctx: BuildContext, validation: Validation) -> ValidationResponse:
        prompt = render_prompt("validate_template", ctx, validation=validation, profile=self._profile)
        self._run_command(prompt)
        return _read_response_file(ctx.response_file_path, ValidationResponse)  # type: ignore[return-value]

    def difference(self, ctx: DifferencingContext) -> DifferencingResponse:
        prompt = render_differencing_prompt(ctx, profile=self._profile)
        self._run_command(prompt)
        return _read_response_file(ctx.response_file_path, DifferencingResponse)  # type: ignore[return-value]

    def plan(self, ctx: BuildContext) -> None:
        prompt = render_prompt("plan", ctx, profile=self._profile)
        self._run_command(prompt)


# ---------------------------------------------------------------------------
# ClaudeAgent
# ---------------------------------------------------------------------------


class ClaudeAgent(Agent):
    """Agent implementation for Claude Code."""

    def __init__(self, profile: AgentProfile) -> None:
        self._profile = profile

    def get_name(self) -> str:
        return self._profile.name

    def get_type(self) -> str:
        return "claude"

    def _write_sandbox_settings(self) -> Path | None:
        """Write a temporary .claude/settings.local.json for sandbox enforcement."""
        if not self._profile.sandbox_write_paths and not self._profile.sandbox_read_paths:
            return None

        claude_dir = Path.cwd() / ".claude"
        claude_dir.mkdir(exist_ok=True)
        settings_path = claude_dir / "settings.local.json"

        settings: dict[str, Any] = {
            "permissions": {
                "allow": ["Bash(*)", "Read(*)", "Write(*)", "Edit(*)"],
                "deny": [],
            },
        }

        if self._profile.sandbox_write_paths:
            settings["sandbox"] = {
                "writePaths": self._profile.sandbox_write_paths,
                "readPaths": self._profile.sandbox_read_paths,
            }

        settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        return settings_path

    def _cleanup_sandbox_settings(self, path: Path | None) -> None:
        """Remove the temporary sandbox settings file."""
        if path is not None:
            path.unlink(missing_ok=True)

    def _build_non_interactive_cmd(self, prompt: str) -> list[str]:
        """Build the claude command for non-interactive (build/validate/diff) invocations."""
        cmd = [
            "claude",
            "-p",
            prompt,
            "--verbose",
            "--output-format",
            "stream-json",
            "--dangerously-skip-permissions",
        ]
        if self._profile.model_id:
            cmd.extend(["--model", self._profile.model_id])
        cmd.extend(self._profile.cli_args)
        return cmd

    def _run_streaming(self, cmd: list[str]) -> None:
        """Run a claude command, streaming JSON events to stderr."""
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.rstrip("\n")
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    # Print assistant text events to stderr for user visibility
                    if event.get("type") in ("assistant", "content_block_delta"):
                        text = event.get("text") or event.get("delta", {}).get("text", "")
                        if text:
                            print(text, end="", file=sys.stderr, flush=True)
                except json.JSONDecodeError:
                    pass
            proc.wait(timeout=self._profile.timeout)
            if proc.returncode != 0:
                stderr_output = proc.stderr.read() if proc.stderr else ""
                raise AgentError(
                    f"Claude command failed with exit code {proc.returncode}: {stderr_output}"
                )
        except subprocess.TimeoutExpired:
            proc.kill()
            raise AgentError(f"Claude agent timed out after {self._profile.timeout}s")

    def build(self, ctx: BuildContext) -> BuildResponse:
        prompt = render_prompt("build", ctx, profile=self._profile)
        cmd = self._build_non_interactive_cmd(prompt)
        settings_path = self._write_sandbox_settings()
        try:
            self._run_streaming(cmd)
        finally:
            self._cleanup_sandbox_settings(settings_path)
        return _read_response_file(ctx.response_file_path, BuildResponse)  # type: ignore[return-value]

    def validate(self, ctx: BuildContext, validation: Validation) -> ValidationResponse:
        prompt = render_prompt("validate_template", ctx, validation=validation, profile=self._profile)
        cmd = self._build_non_interactive_cmd(prompt)
        settings_path = self._write_sandbox_settings()
        try:
            self._run_streaming(cmd)
        finally:
            self._cleanup_sandbox_settings(settings_path)
        return _read_response_file(ctx.response_file_path, ValidationResponse)  # type: ignore[return-value]

    def difference(self, ctx: DifferencingContext) -> DifferencingResponse:
        prompt = render_differencing_prompt(ctx, profile=self._profile)
        cmd = self._build_non_interactive_cmd(prompt)
        settings_path = self._write_sandbox_settings()
        try:
            self._run_streaming(cmd)
        finally:
            self._cleanup_sandbox_settings(settings_path)
        return _read_response_file(ctx.response_file_path, DifferencingResponse)  # type: ignore[return-value]

    def plan(self, ctx: BuildContext) -> None:
        """Launch Claude Code in interactive REPL mode for planning."""
        prompt = render_prompt("plan", ctx, profile=self._profile)
        cmd = ["claude"]
        if self._profile.model_id:
            cmd.extend(["--model", self._profile.model_id])
        cmd.extend(self._profile.cli_args)
        cmd.append(prompt)
        subprocess.run(cmd)


# ---------------------------------------------------------------------------
# MockAgent
# ---------------------------------------------------------------------------


class MockAgent(Agent):
    """Test double that records calls and returns configurable responses."""

    def __init__(
        self,
        name: str = "mock",
        build_response: BuildResponse | None = None,
        validation_response: ValidationResponse | None = None,
        differencing_response: DifferencingResponse | None = None,
    ) -> None:
        self._name = name
        self._build_response = build_response or BuildResponse(
            status="success",
            summary="Mock build completed",
        )
        self._validation_response = validation_response or ValidationResponse(
            name="mock-validation",
            status="pass",
            reason="Mock validation passed",
        )
        self._differencing_response = differencing_response or DifferencingResponse(
            status="equivalent",
            dimensions=[],
            summary="Mock differencing completed",
        )
        self.build_calls: list[BuildContext] = []
        self.validate_calls: list[tuple[BuildContext, Validation]] = []
        self.difference_calls: list[DifferencingContext] = []
        self.plan_calls: list[BuildContext] = []

    def get_name(self) -> str:
        return self._name

    def get_type(self) -> str:
        return "mock"

    def build(self, ctx: BuildContext) -> BuildResponse:
        self.build_calls.append(ctx)
        return self._build_response

    def validate(self, ctx: BuildContext, validation: Validation) -> ValidationResponse:
        self.validate_calls.append((ctx, validation))
        return self._validation_response

    def difference(self, ctx: DifferencingContext) -> DifferencingResponse:
        self.difference_calls.append(ctx)
        return self._differencing_response

    def plan(self, ctx: BuildContext) -> None:
        self.plan_calls.append(ctx)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_from_profile(profile: AgentProfile) -> Agent:
    """Create an agent instance from an AgentProfile."""
    if profile.provider == "claude":
        return ClaudeAgent(profile)
    elif profile.provider == "cli":
        return CLIAgent(profile)
    else:
        raise AgentError(f"Unknown agent provider: {profile.provider!r}")
