"""Agent interface and implementations for building and validating intents."""

from __future__ import annotations

import abc
import json
import subprocess
import sys
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
    timeout: float = 3600.0  # seconds, default 1h
    retries: int = 3
    model_id: str | None = None
    prompt_templates: PromptTemplates | None = None
    sandbox_write_paths: list[str] = []
    sandbox_read_paths: list[str] = []


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
# Stream-JSON helpers
# ---------------------------------------------------------------------------


def _print_stream_event(event: dict) -> None:
    """Print a Claude stream-json event to stderr so the user can follow progress."""
    etype = event.get("type", "")

    if etype == "assistant":
        # Assistant text content
        message = event.get("message", {})
        for block in message.get("content", []):
            if block.get("type") == "text":
                text = block.get("text", "")
                if text:
                    print(text, file=sys.stderr, flush=True)

    elif etype == "content_block_delta":
        delta = event.get("delta", {})
        if delta.get("type") == "text_delta":
            text = delta.get("text", "")
            if text:
                print(text, end="", file=sys.stderr, flush=True)

    elif etype == "result":
        # Final result — print a newline to separate from streamed content
        print(file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# ClaudeAgent
# ---------------------------------------------------------------------------


class ClaudeAgent(Agent):
    """Specialization for Claude Code.

<<<<<<< HEAD
    Combines ``--dangerously-skip-permissions`` (so every tool runs without
    prompts, matching normal non-interactive behaviour) with Claude Code's
    built-in OS-level sandbox for filesystem isolation.

    The sandbox scopes the agent to:
    - **write** only to the paths listed in ``profile.sandbox_write_paths``
      (typically the output directory).
    - **read** only from the paths listed in ``profile.sandbox_read_paths``
      (typically the intent files for the target and its DAG ancestors, plus
      the output directory).

    All commands and network access remain fully allowed.  The builder is
    responsible for populating ``sandbox_write_paths`` and
    ``sandbox_read_paths`` on the profile before the agent is created.
=======
    Uses CLIAgent via composition, overriding the command to `claude`
    with flags: -p, --output-format stream-json, --dangerously-skip-permissions,
    and --model from the profile.  Streams agent output to stderr in real-time.
>>>>>>> 354a96f (build core/specifications [gen:1835255e-ed9b-4db6-a29d-de5349a5aeea])
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

    # -- sandbox settings ---------------------------------------------------

    def _sandbox_settings(self) -> dict:
        """Build Claude Code sandbox settings from the profile.

        The profile's ``sandbox_write_paths`` controls which directories the
        agent may write to (via ``allowWrite``).  ``sandbox_read_paths``
        controls which directories the agent may read from — everything
        outside those paths is denied (via ``denyRead`` set to ``/``
        combined with negative deny entries for allowed paths is not
        supported, so we use ``allowWrite`` for write scoping and rely on
        the permission layer for read scoping).

        When sandbox paths are configured the settings enable the sandbox
        with auto-allow so that all commands execute without prompts inside
        the sandbox boundary.
        """
        settings: dict = {
            "sandbox": {
                "enabled": True,
                "autoAllow": True,
                "allowUnsandboxedCommands": False,
                "filesystem": {},
            },
        }
        if self._profile.sandbox_write_paths:
            settings["sandbox"]["filesystem"]["allowWrite"] = [
                f"//{p}" if Path(p).is_absolute() else p
                for p in self._profile.sandbox_write_paths
            ]
        if self._profile.sandbox_read_paths:
            settings["sandbox"]["filesystem"]["denyWrite"] = [
                f"//{p}" if Path(p).is_absolute() else p
                for p in self._profile.sandbox_read_paths
            ]
        return settings

    def _write_sandbox_settings(self, cwd: str) -> Path:
        """Write sandbox settings to .claude/settings.local.json in the CWD."""
        settings_dir = Path(cwd) / ".claude"
        settings_dir.mkdir(parents=True, exist_ok=True)
        settings_path = settings_dir / "settings.local.json"
        settings_path.write_text(
            json.dumps(self._sandbox_settings(), indent=2),
            encoding="utf-8",
        )
        return settings_path

    def _cleanup_sandbox_settings(self, cwd: str) -> None:
        """Remove the temporary sandbox settings after an invocation."""
        settings_dir = Path(cwd) / ".claude"
        settings_path = settings_dir / "settings.local.json"
        if settings_path.exists():
            settings_path.unlink()
        # Remove .claude dir if empty
        if settings_dir.exists() and not any(settings_dir.iterdir()):
            settings_dir.rmdir()

    @property
    def _has_sandbox_paths(self) -> bool:
        return bool(self._profile.sandbox_write_paths or self._profile.sandbox_read_paths)

    # -- command building ----------------------------------------------------

    def _build_noninteractive_command(self, prompt: str) -> list[str]:
        """Build claude command for non-interactive (build/validate) use."""
        cmd = [
            "claude",
            "-p", prompt,
            "--output-format", "stream-json",
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

<<<<<<< HEAD
    def _run_noninteractive(
        self, prompt: str, ctx: BuildContext
    ) -> subprocess.CompletedProcess[str]:
        """Run Claude Code in non-interactive mode.

        When the profile has sandbox paths configured, writes a
        ``.claude/settings.local.json`` before invocation and cleans it up
        afterward.  The sandbox restricts filesystem access at the OS level
        while ``--dangerously-skip-permissions`` keeps all commands and
        network access fully allowed.
        """
        use_sandbox = self._has_sandbox_paths
        if use_sandbox:
            self._write_sandbox_settings(ctx.output_dir)
=======
    def _run_noninteractive(self, prompt: str, ctx: BuildContext) -> None:
        """Run Claude Code in non-interactive mode, streaming output to stderr."""
>>>>>>> 354a96f (build core/specifications [gen:1835255e-ed9b-4db6-a29d-de5349a5aeea])
        cmd = self._build_noninteractive_command(prompt)
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=ctx.output_dir,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.rstrip("\n")
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                _print_stream_event(event)
            proc.wait(timeout=self._profile.timeout)
            if proc.returncode and proc.returncode != 0:
                stderr_out = proc.stderr.read() if proc.stderr else ""
                raise AgentError(
                    f"Claude agent '{self._profile.name}' exited with code {proc.returncode}: {stderr_out}"
                )
        except subprocess.TimeoutExpired:
            proc.kill()
            raise AgentError(
                f"Claude agent '{self._profile.name}' timed out after {self._profile.timeout}s"
            )
        except FileNotFoundError:
            raise AgentError("Claude Code CLI not found. Install it to use the claude provider.")
        finally:
            if use_sandbox:
                self._cleanup_sandbox_settings(ctx.output_dir)


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
