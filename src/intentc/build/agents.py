"""Agent module for intentc — types, interfaces, and implementations."""

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
# Response types
# ---------------------------------------------------------------------------


class BuildResponse(BaseModel):
    model_config = {"extra": "ignore"}

    status: str  # "success" or "failure"
    summary: str
    files_created: list[str] = Field(default_factory=list)
    files_modified: list[str] = Field(default_factory=list)


class ValidationResponse(BaseModel):
    model_config = {"extra": "ignore"}

    name: str
    status: str  # "pass" or "fail"
    reason: str


class DimensionResult(BaseModel):
    model_config = {"extra": "ignore"}

    name: str
    status: str  # "pass" or "fail"
    rationale: str


class DifferencingResponse(BaseModel):
    model_config = {"extra": "ignore"}

    status: str  # "equivalent" or "divergent"
    dimensions: list[DimensionResult] = Field(default_factory=list)
    summary: str


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
    """Return the path to the agent prompt templates directory."""
    return Path.cwd() / "intent" / "build" / "agents" / "prompts"


def diff_prompts_dir() -> Path:
    """Return the path to the differencing prompt templates directory."""
    return Path.cwd() / "intent" / "differencing" / "prompts"


def load_default_prompts() -> PromptTemplates:
    """Load default prompt templates from disk. Missing files yield empty strings."""
    templates: dict[str, str] = {}
    build_path = prompts_dir() / "build.prompt"
    validate_path = prompts_dir() / "validate.prompt"
    plan_path = prompts_dir() / "plan.prompt"
    diff_path = diff_prompts_dir() / "difference.prompt"

    for key, path in [
        ("build", build_path),
        ("validate_template", validate_path),
        ("plan", plan_path),
        ("difference", diff_path),
    ]:
        if path.exists():
            templates[key] = path.read_text(encoding="utf-8")
        else:
            templates[key] = ""

    return PromptTemplates(**templates)


def render_prompt(
    template: str,
    ctx: BuildContext,
    validation: Validation | None = None,
) -> str:
    """Render a prompt template with BuildContext variables."""
    result = template
    result = result.replace("{project}", ctx.project_intent.body)
    result = result.replace(
        "{implementation}",
        ctx.implementation.body if ctx.implementation else "",
    )
    result = result.replace("{feature}", ctx.intent.body)
    # Validations: join all validation text
    val_texts = []
    for vf in ctx.validations:
        for v in vf.validations:
            val_texts.append(
                f"- {v.name} ({v.type}, {v.severity.value}): {v.args}"
            )
    result = result.replace("{validations}", "\n".join(val_texts))
    # Single validation (for validate template)
    if validation is not None:
        result = result.replace(
            "{validation}",
            f"- {validation.name} ({validation.type}, {validation.severity.value}): {validation.args}",
        )
    result = result.replace("{response_file}", ctx.response_file_path)
    return result


def render_differencing_prompt(
    template: str,
    ctx: DifferencingContext,
) -> str:
    """Render a differencing prompt template with DifferencingContext variables."""
    result = template
    result = result.replace("{project}", ctx.project_intent.body)
    result = result.replace(
        "{implementation}",
        ctx.implementation.body if ctx.implementation else "",
    )
    result = result.replace("{output_dir_a}", ctx.output_dir_a)
    result = result.replace("{output_dir_b}", ctx.output_dir_b)
    result = result.replace("{response_file}", ctx.response_file_path)
    return result


# ---------------------------------------------------------------------------
# Agent profile
# ---------------------------------------------------------------------------


class AgentProfile(BaseModel):
    model_config = {"extra": "ignore"}

    name: str
    provider: str  # "claude", "codex", or "cli"
    command: str = ""
    cli_args: list[str] = Field(default_factory=list)
    timeout: float = 3600.0
    retries: int = 3
    model_id: str | None = None
    prompt_templates: PromptTemplates | None = None
    sandbox_write_paths: list[str] = Field(default_factory=list)
    sandbox_read_paths: list[str] = Field(default_factory=list)


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
# Agent interface
# ---------------------------------------------------------------------------


class Agent(abc.ABC):
    """Abstract base class for all agents."""

    @abc.abstractmethod
    def build(self, ctx: BuildContext) -> BuildResponse:
        """Run a build for the given context."""

    @abc.abstractmethod
    def validate(self, ctx: BuildContext, validation: Validation) -> ValidationResponse:
        """Run a single validation."""

    @abc.abstractmethod
    def difference(self, ctx: DifferencingContext) -> DifferencingResponse:
        """Run a differencing evaluation."""

    @abc.abstractmethod
    def plan(self, ctx: BuildContext) -> None:
        """Enter planning mode for the given context."""

    @abc.abstractmethod
    def get_name(self) -> str:
        """Return the agent's name."""

    @abc.abstractmethod
    def get_type(self) -> str:
        """Return the agent's type/provider."""


# ---------------------------------------------------------------------------
# Helper: read and parse response file
# ---------------------------------------------------------------------------


def _read_response_file(path: str) -> dict[str, Any]:
    """Read and parse a JSON response file. Raises AgentError on failure."""
    p = Path(path)
    if not p.exists():
        raise AgentError(f"Response file not found: {path}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise AgentError(f"Invalid response file {path}: {exc}") from exc
    return data


# ---------------------------------------------------------------------------
# CLIAgent
# ---------------------------------------------------------------------------


class CLIAgent(Agent):
    """Generic agent wrapping any command-line tool."""

    def __init__(self, profile: AgentProfile) -> None:
        self._profile = profile
        self._templates = profile.prompt_templates or load_default_prompts()

    def build(self, ctx: BuildContext) -> BuildResponse:
        prompt = render_prompt(self._templates.build, ctx)
        self._run_command(prompt, ctx.response_file_path)
        data = _read_response_file(ctx.response_file_path)
        return BuildResponse(**data)

    def validate(self, ctx: BuildContext, validation: Validation) -> ValidationResponse:
        prompt = render_prompt(self._templates.validate_template, ctx, validation=validation)
        self._run_command(prompt, ctx.response_file_path)
        data = _read_response_file(ctx.response_file_path)
        return ValidationResponse(**data)

    def difference(self, ctx: DifferencingContext) -> DifferencingResponse:
        prompt = render_differencing_prompt(self._templates.difference, ctx)
        self._run_command(prompt, ctx.response_file_path)
        data = _read_response_file(ctx.response_file_path)
        return DifferencingResponse(**data)

    def plan(self, ctx: BuildContext) -> None:
        prompt = render_prompt(self._templates.plan, ctx)
        self._run_command(prompt, ctx.response_file_path)

    def get_name(self) -> str:
        return self._profile.name

    def get_type(self) -> str:
        return self._profile.provider

    def _run_command(self, prompt: str, response_file_path: str) -> None:
        """Execute the CLI command with the given prompt."""
        if not self._profile.command:
            raise AgentError("CLIAgent requires a command in the profile")
        cmd = self._profile.command.split() + [prompt] + list(self._profile.cli_args)
        try:
            subprocess.run(
                cmd,
                timeout=self._profile.timeout,
                check=True,
                capture_output=True,
            )
        except subprocess.TimeoutExpired as exc:
            raise AgentError(f"Agent command timed out after {self._profile.timeout}s") from exc
        except subprocess.CalledProcessError as exc:
            raise AgentError(
                f"Agent command failed with exit code {exc.returncode}: {exc.stderr}"
            ) from exc


# ---------------------------------------------------------------------------
# ClaudeAgent
# ---------------------------------------------------------------------------


class ClaudeAgent(Agent):
    """Agent specialized for Claude Code."""

    def __init__(self, profile: AgentProfile) -> None:
        self._profile = profile
        self._templates = profile.prompt_templates or load_default_prompts()

    def build(self, ctx: BuildContext) -> BuildResponse:
        prompt = render_prompt(self._templates.build, ctx)
        self._run_noninteractive(prompt, ctx.output_dir)
        data = _read_response_file(ctx.response_file_path)
        return BuildResponse(**data)

    def validate(self, ctx: BuildContext, validation: Validation) -> ValidationResponse:
        prompt = render_prompt(self._templates.validate_template, ctx, validation=validation)
        self._run_noninteractive(prompt, ctx.output_dir)
        data = _read_response_file(ctx.response_file_path)
        return ValidationResponse(**data)

    def difference(self, ctx: DifferencingContext) -> DifferencingResponse:
        prompt = render_differencing_prompt(self._templates.difference, ctx)
        self._run_noninteractive(prompt, ctx.output_dir_a)
        data = _read_response_file(ctx.response_file_path)
        return DifferencingResponse(**data)

    def plan(self, ctx: BuildContext) -> None:
        prompt = render_prompt(self._templates.plan, ctx)
        cmd = ["claude"]
        if self._profile.model_id:
            cmd.extend(["--model", self._profile.model_id])
        cmd.extend(self._profile.cli_args)
        cmd.append(prompt)
        try:
            subprocess.run(cmd, timeout=self._profile.timeout, check=True)
        except subprocess.TimeoutExpired as exc:
            raise AgentError(f"Claude plan timed out after {self._profile.timeout}s") from exc
        except subprocess.CalledProcessError as exc:
            raise AgentError(f"Claude plan failed: {exc}") from exc

    def get_name(self) -> str:
        return self._profile.name

    def get_type(self) -> str:
        return "claude"

    def _run_noninteractive(self, prompt: str, cwd: str) -> None:
        """Run Claude Code in non-interactive mode with streaming JSON output."""
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

        settings_path: Path | None = None
        try:
            # Write sandbox settings if sandbox paths are configured
            if self._profile.sandbox_write_paths or self._profile.sandbox_read_paths:
                settings_path = self._write_sandbox_settings(cwd)

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
            )
            # Stream stdout line-by-line, printing relevant events to stderr
            assert proc.stdout is not None
            for raw_line in proc.stdout:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    # Print assistant text events to stderr for real-time visibility
                    if isinstance(event, dict):
                        event_type = event.get("type", "")
                        if event_type in ("assistant", "content", "content_block_delta"):
                            text = event.get("text", event.get("delta", {}).get("text", ""))
                            if text:
                                print(text, end="", file=sys.stderr, flush=True)
                except json.JSONDecodeError:
                    pass

            proc.wait(timeout=self._profile.timeout)
            if proc.returncode != 0:
                stderr_output = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
                raise AgentError(
                    f"Claude command failed with exit code {proc.returncode}: {stderr_output}"
                )
        except subprocess.TimeoutExpired as exc:
            proc.kill()  # type: ignore[possibly-undefined]
            raise AgentError(f"Claude command timed out after {self._profile.timeout}s") from exc
        finally:
            if settings_path and settings_path.exists():
                settings_path.unlink()
                # Remove .claude dir if empty
                claude_dir = settings_path.parent
                try:
                    claude_dir.rmdir()
                except OSError:
                    pass

    def _write_sandbox_settings(self, cwd: str) -> Path:
        """Write a temporary .claude/settings.local.json for sandbox enforcement."""
        claude_dir = Path(cwd) / ".claude"
        claude_dir.mkdir(exist_ok=True)
        settings_path = claude_dir / "settings.local.json"

        allow_patterns: list[str] = []
        for wp in self._profile.sandbox_write_paths:
            allow_patterns.append(wp)
        for rp in self._profile.sandbox_read_paths:
            allow_patterns.append(rp)

        settings = {
            "permissions": {
                "allow": [
                    "Bash(*)",
                    "Read(*)",
                    "Write(*)",
                    "Edit(*)",
                ],
                "deny": [],
            },
            "sandbox": {
                "write_paths": list(self._profile.sandbox_write_paths),
                "read_paths": list(self._profile.sandbox_read_paths),
            },
        }
        settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        return settings_path


# ---------------------------------------------------------------------------
# MockAgent
# ---------------------------------------------------------------------------


class MockAgent(Agent):
    """Mock agent for testing. Records calls, returns configurable responses."""

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
            name="mock_validation",
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

    def get_name(self) -> str:
        return self._name

    def get_type(self) -> str:
        return "mock"


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
