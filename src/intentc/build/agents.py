"""Agent interface and implementations for building and validating intents."""

from __future__ import annotations

import abc
import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel

from intentc.core.types import IntentFile, ProjectIntent, Implementation, ValidationFile, Validation


# ---------------------------------------------------------------------------
# Response types
# ---------------------------------------------------------------------------


class BuildResponse(BaseModel):
    """Structured response from an agent after a build invocation."""

    model_config = {"extra": "ignore"}

    status: str  # "success" or "failure"
    summary: str
    files_created: list[str] = []
    files_modified: list[str] = []


class ValidationResponse(BaseModel):
    """Structured response from an agent after a validation invocation."""

    model_config = {"extra": "ignore"}

    name: str
    status: str  # "pass" or "fail"
    reason: str


# ---------------------------------------------------------------------------
# BuildContext
# ---------------------------------------------------------------------------


class BuildContext(BaseModel):
    """Everything the agent needs to act on a target."""

    model_config = {"extra": "ignore"}

    intent: IntentFile
    validations: list[ValidationFile] = []
    output_dir: str
    generation_id: str
    dependency_names: list[str] = []
    project_intent: ProjectIntent
    implementation: Implementation | None = None
    response_file_path: str


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "intent" / "build" / "agents" / "prompts"


class PromptTemplates(BaseModel):
    """Prompt template paths or content for agent operations."""

    model_config = {"extra": "ignore", "populate_by_name": True}

    build: str = ""
    validate_template: str = ""
    plan: str = ""


def load_default_prompts() -> PromptTemplates:
    """Load the default prompt templates from the prompts/ directory."""
    templates = PromptTemplates()
    build_path = _PROMPTS_DIR / "build.prompt"
    validate_path = _PROMPTS_DIR / "validate.prompt"
    plan_path = _PROMPTS_DIR / "plan.prompt"

    if build_path.exists():
        templates.build = build_path.read_text(encoding="utf-8")
    if validate_path.exists():
        templates.validate_template = validate_path.read_text(encoding="utf-8")
    if plan_path.exists():
        templates.plan = plan_path.read_text(encoding="utf-8")

    return templates


def render_prompt(template: str, ctx: BuildContext, validation: Validation | None = None) -> str:
    """Render a prompt template with BuildContext variables.

    Template variables:
        {project} — the project-level intent body
        {implementation} — the implementation-level intent body
        {feature} — the target feature intent body
        {validations} — all validation text for the target
        {validation} — the single validation being evaluated
        {response_file} — path to the response file
    """
    project_text = ctx.project_intent.body
    implementation_text = ctx.implementation.body if ctx.implementation else ""
    feature_text = ctx.intent.body

    # Format all validations as text
    validation_lines: list[str] = []
    for vf in ctx.validations:
        for v in vf.validations:
            validation_lines.append(f"- {v.name} ({v.type.value}, {v.severity.value}): {v.args}")
    validations_text = "\n".join(validation_lines)

    # Format single validation
    single_validation_text = ""
    if validation is not None:
        single_validation_text = (
            f"name: {validation.name}\n"
            f"type: {validation.type.value}\n"
            f"severity: {validation.severity.value}\n"
            f"args: {validation.args}"
        )

    return (
        template
        .replace("{project}", project_text)
        .replace("{implementation}", implementation_text)
        .replace("{feature}", feature_text)
        .replace("{validations}", validations_text)
        .replace("{validation}", single_validation_text)
        .replace("{response_file}", ctx.response_file_path)
    )


# ---------------------------------------------------------------------------
# AgentProfile
# ---------------------------------------------------------------------------


class AgentProfile(BaseModel):
    """Named, reusable agent configuration."""

    model_config = {"extra": "ignore"}

    name: str
    provider: str  # "claude", "codex", "cli"
    command: str = ""
    cli_args: list[str] = []
    timeout: float = 300.0  # seconds, default 5m
    retries: int = 3
    model_id: str | None = None
    prompt_templates: PromptTemplates | None = None


# ---------------------------------------------------------------------------
# Agent interface
# ---------------------------------------------------------------------------


class Agent(abc.ABC):
    """Abstract base class for all agents."""

    @abc.abstractmethod
    def build(self, ctx: BuildContext) -> BuildResponse:
        """Execute a build for the given context."""

    @abc.abstractmethod
    def validate(self, ctx: BuildContext, validation: Validation) -> ValidationResponse:
        """Evaluate a single validation against the implementation."""

    @abc.abstractmethod
    def plan(self, ctx: BuildContext) -> None:
        """Enter planning mode — interactive or single-shot."""

    @abc.abstractmethod
    def get_name(self) -> str:
        """Return the agent's name."""

    @abc.abstractmethod
    def get_type(self) -> str:
        """Return the agent's type identifier."""


# ---------------------------------------------------------------------------
# CLIAgent
# ---------------------------------------------------------------------------


def _read_response_file(path: str) -> dict:
    """Read and parse the JSON response file written by the agent."""
    response_path = Path(path)
    if not response_path.exists():
        raise AgentError(f"Response file not found: {path}")
    try:
        return json.loads(response_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AgentError(f"Invalid JSON in response file {path}: {exc}")


class AgentError(Exception):
    """Raised when an agent invocation fails."""


class CLIAgent(Agent):
    """Generic agent that wraps any command-line tool.

    Constructs a prompt from BuildContext using the prompt templates,
    passes it to the command, then reads the response file after the
    process exits.
    """

    def __init__(self, profile: AgentProfile) -> None:
        self._profile = profile
        self._templates = profile.prompt_templates or load_default_prompts()

    def build(self, ctx: BuildContext) -> BuildResponse:
        prompt = render_prompt(self._templates.build, ctx)
        self._run(prompt, ctx)
        data = _read_response_file(ctx.response_file_path)
        return BuildResponse(**data)

    def validate(self, ctx: BuildContext, validation: Validation) -> ValidationResponse:
        prompt = render_prompt(self._templates.validate_template, ctx, validation=validation)
        self._run(prompt, ctx)
        data = _read_response_file(ctx.response_file_path)
        return ValidationResponse(**data)

    def plan(self, ctx: BuildContext) -> None:
        prompt = render_prompt(self._templates.plan, ctx)
        self._run(prompt, ctx)

    def get_name(self) -> str:
        return self._profile.name

    def get_type(self) -> str:
        return "cli"

    def _build_command(self, prompt: str) -> list[str]:
        """Build the command line for invocation."""
        if not self._profile.command:
            raise AgentError("CLIAgent requires a command in the profile")
        return [self._profile.command, *self._profile.cli_args, prompt]

    def _run(self, prompt: str, ctx: BuildContext) -> subprocess.CompletedProcess[str]:
        """Execute the CLI command with the given prompt."""
        cmd = self._build_command(prompt)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._profile.timeout,
                cwd=ctx.output_dir,
            )
            return result
        except subprocess.TimeoutExpired:
            raise AgentError(
                f"Agent '{self._profile.name}' timed out after {self._profile.timeout}s"
            )
        except FileNotFoundError:
            raise AgentError(
                f"Command not found: {self._profile.command}"
            )


# ---------------------------------------------------------------------------
# ClaudeAgent
# ---------------------------------------------------------------------------


class ClaudeAgent(Agent):
    """Specialization for Claude Code.

    Uses CLIAgent via composition, overriding the command to `claude`
    with flags: -p, --output-format json, --dangerously-skip-permissions,
    and --model from the profile.
    """

    def __init__(self, profile: AgentProfile) -> None:
        self._profile = profile
        self._templates = profile.prompt_templates or load_default_prompts()

    def build(self, ctx: BuildContext) -> BuildResponse:
        prompt = render_prompt(self._templates.build, ctx)
        self._run_noninteractive(prompt, ctx)
        data = _read_response_file(ctx.response_file_path)
        return BuildResponse(**data)

    def validate(self, ctx: BuildContext, validation: Validation) -> ValidationResponse:
        prompt = render_prompt(self._templates.validate_template, ctx, validation=validation)
        self._run_noninteractive(prompt, ctx)
        data = _read_response_file(ctx.response_file_path)
        return ValidationResponse(**data)

    def plan(self, ctx: BuildContext) -> None:
        """Launch Claude Code in interactive REPL mode for planning."""
        prompt = render_prompt(self._templates.plan, ctx)
        cmd = self._build_interactive_command(prompt)
        subprocess.run(cmd, cwd=ctx.output_dir)

    def get_name(self) -> str:
        return self._profile.name

    def get_type(self) -> str:
        return "claude"

    def _build_noninteractive_command(self, prompt: str) -> list[str]:
        """Build claude command for non-interactive (build/validate) use."""
        cmd = [
            "claude",
            "-p", prompt,
            "--output-format", "json",
            "--dangerously-skip-permissions",
        ]
        if self._profile.model_id:
            cmd.extend(["--model", self._profile.model_id])
        cmd.extend(self._profile.cli_args)
        return cmd

    def _build_interactive_command(self, prompt: str) -> list[str]:
        """Build claude command for interactive REPL (plan) mode."""
        cmd = ["claude"]
        if self._profile.model_id:
            cmd.extend(["--model", self._profile.model_id])
        cmd.extend(self._profile.cli_args)
        return cmd

    def _run_noninteractive(
        self, prompt: str, ctx: BuildContext
    ) -> subprocess.CompletedProcess[str]:
        """Run Claude Code in non-interactive mode."""
        cmd = self._build_noninteractive_command(prompt)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._profile.timeout,
                cwd=ctx.output_dir,
            )
            return result
        except subprocess.TimeoutExpired:
            raise AgentError(
                f"Claude agent '{self._profile.name}' timed out after {self._profile.timeout}s"
            )
        except FileNotFoundError:
            raise AgentError("Claude Code CLI not found. Install it to use the claude provider.")


# ---------------------------------------------------------------------------
# MockAgent
# ---------------------------------------------------------------------------


class MockAgent(Agent):
    """Test agent that records calls and returns configurable responses."""

    def __init__(
        self,
        name: str = "mock",
        build_response: BuildResponse | None = None,
        validation_response: ValidationResponse | None = None,
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
        self.build_calls: list[BuildContext] = []
        self.validate_calls: list[tuple[BuildContext, Validation]] = []
        self.plan_calls: list[BuildContext] = []

    def build(self, ctx: BuildContext) -> BuildResponse:
        self.build_calls.append(ctx)
        return self._build_response

    def validate(self, ctx: BuildContext, validation: Validation) -> ValidationResponse:
        self.validate_calls.append((ctx, validation))
        # Return response with the actual validation name
        return ValidationResponse(
            name=validation.name,
            status=self._validation_response.status,
            reason=self._validation_response.reason,
        )

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
    """Create an agent from a named profile.

    Args:
        profile: The agent profile configuration.

    Returns:
        An Agent instance matching the profile's provider.

    Raises:
        AgentError: If the provider is unknown.
    """
    match profile.provider:
        case "claude":
            return ClaudeAgent(profile)
        case "cli":
            return CLIAgent(profile)
        case _:
            raise AgentError(
                f"Unknown agent provider '{profile.provider}'. "
                f"Supported providers: claude, cli"
            )
