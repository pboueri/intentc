"""Agent interface and implementations for building, validating, and differencing intents."""

from __future__ import annotations

import abc
import json
import subprocess
import sys
from dataclasses import dataclass
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


class DimensionResult(BaseModel):
    """Per-axis evaluation result within a differencing response."""

    model_config = {"extra": "ignore"}

    name: str
    status: str  # "pass" or "fail"
    rationale: str


class DifferencingResponse(BaseModel):
    """Structured response from an agent after a differencing evaluation."""

    model_config = {"extra": "ignore"}

    status: str  # "equivalent" or "divergent"
    dimensions: list[DimensionResult] = []
    summary: str


# ---------------------------------------------------------------------------
# Contexts
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


class DifferencingContext(BaseModel):
    """Everything the agent needs to perform a differencing evaluation."""

    model_config = {"extra": "ignore"}

    output_dir_a: str
    output_dir_b: str
    project_intent: ProjectIntent
    response_file_path: str
    implementation: Implementation | None = None


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

def _prompts_dir() -> Path:
    """Resolve the prompts directory relative to the project's intent/ directory."""
    return Path.cwd() / "intent" / "build" / "agents" / "prompts"


def _diff_prompts_dir() -> Path:
    """Resolve the differencing prompts directory relative to the project's intent/ directory."""
    return Path.cwd() / "intent" / "differencing" / "prompts"


class PromptTemplates(BaseModel):
    """Prompt template paths or content for agent operations."""

    model_config = {"extra": "ignore", "populate_by_name": True}

    build: str = ""
    validate_template: str = ""
    plan: str = ""
    difference: str = ""


def load_default_prompts() -> PromptTemplates:
    """Load the default prompt templates from the prompts/ directory."""
    templates = PromptTemplates()
    prompts_dir = _prompts_dir()
    build_path = prompts_dir / "build.prompt"
    validate_path = prompts_dir / "validate.prompt"
    plan_path = prompts_dir / "plan.prompt"
    diff_path = _diff_prompts_dir() / "difference.prompt"

    if build_path.exists():
        templates.build = build_path.read_text(encoding="utf-8")
    if validate_path.exists():
        templates.validate_template = validate_path.read_text(encoding="utf-8")
    if plan_path.exists():
        templates.plan = plan_path.read_text(encoding="utf-8")
    if diff_path.exists():
        templates.difference = diff_path.read_text(encoding="utf-8")

    return templates


def render_prompt(template: str, ctx: BuildContext, validation: Validation | None = None) -> str:
    """Render a prompt template with BuildContext variables.

    Template variables:
        {project} -- the project-level intent body
        {implementation} -- the implementation-level intent body
        {feature} -- the target feature intent body
        {validations} -- all validation text for the target
        {validation} -- the single validation being evaluated
        {response_file} -- path to the response file
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


def render_differencing_prompt(template: str, ctx: DifferencingContext) -> str:
    """Render a prompt template with DifferencingContext variables.

    Template variables:
        {project} -- the project-level intent body
        {implementation} -- the implementation-level intent body
        {output_dir_a} -- reference output directory
        {output_dir_b} -- candidate output directory
        {response_file} -- path to the response file
    """
    project_text = ctx.project_intent.body
    implementation_text = ctx.implementation.body if ctx.implementation else ""

    return (
        template
        .replace("{project}", project_text)
        .replace("{implementation}", implementation_text)
        .replace("{output_dir_a}", ctx.output_dir_a)
        .replace("{output_dir_b}", ctx.output_dir_b)
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
    def difference(self, ctx: DifferencingContext) -> DifferencingResponse:
        """Evaluate functional equivalence between two builds."""

    @abc.abstractmethod
    def plan(self, ctx: BuildContext) -> None:
        """Enter planning mode -- interactive or single-shot."""

    @abc.abstractmethod
    def get_name(self) -> str:
        """Return the agent's name."""

    @abc.abstractmethod
    def get_type(self) -> str:
        """Return the agent's type identifier."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class AgentError(Exception):
    """Raised when an agent invocation fails."""


@dataclass
class _SandboxContext:
    """Tracks state needed to restore settings after a sandboxed invocation."""

    settings_path: Path
    original_content: str | None


def _find_git_root(start: str) -> str | None:
    """Walk up from start to find the nearest .git directory."""
    path = Path(start).resolve()
    while path != path.parent:
        if (path / ".git").exists():
            return str(path)
        path = path.parent
    return None


def _read_response_file(path: str) -> dict:
    """Read and parse the JSON response file written by the agent."""
    response_path = Path(path)
    if not response_path.exists():
        raise AgentError(f"Response file not found: {path}")
    try:
        return json.loads(response_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AgentError(f"Invalid JSON in response file {path}: {exc}")


# ---------------------------------------------------------------------------
# CLIAgent
# ---------------------------------------------------------------------------


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
        self._run(prompt, ctx.output_dir)
        data = _read_response_file(ctx.response_file_path)
        return BuildResponse(**data)

    def validate(self, ctx: BuildContext, validation: Validation) -> ValidationResponse:
        prompt = render_prompt(self._templates.validate_template, ctx, validation=validation)
        self._run(prompt, ctx.output_dir)
        data = _read_response_file(ctx.response_file_path)
        return ValidationResponse(**data)

    def difference(self, ctx: DifferencingContext) -> DifferencingResponse:
        prompt = render_differencing_prompt(self._templates.difference, ctx)
        self._run(prompt, ctx.output_dir_a)
        data = _read_response_file(ctx.response_file_path)
        return DifferencingResponse(**data)

    def plan(self, ctx: BuildContext) -> None:
        prompt = render_prompt(self._templates.plan, ctx)
        self._run(prompt, ctx.output_dir)

    def get_name(self) -> str:
        return self._profile.name

    def get_type(self) -> str:
        return "cli"

    def _build_command(self, prompt: str) -> list[str]:
        """Build the command line for invocation."""
        if not self._profile.command:
            raise AgentError("CLIAgent requires a command in the profile")
        return [self._profile.command, *self._profile.cli_args, prompt]

    def _run(self, prompt: str, cwd: str) -> subprocess.CompletedProcess[str]:
        """Execute the CLI command with the given prompt."""
        cmd = self._build_command(prompt)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._profile.timeout,
                cwd=cwd,
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
        print(file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# ClaudeAgent
# ---------------------------------------------------------------------------


class ClaudeAgent(Agent):
    """Specialization for Claude Code.

    Uses CLIAgent via composition, overriding the command to ``claude``
    with flags: -p, --output-format stream-json, --dangerously-skip-permissions,
    and --model from the profile.  Streams agent output to stderr in real-time.
    """

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
        """Launch Claude Code in interactive REPL mode for planning."""
        prompt = render_prompt(self._templates.plan, ctx)
        cmd = self._build_interactive_command(prompt)
        subprocess.run(cmd, cwd=ctx.output_dir)

    def get_name(self) -> str:
        return self._profile.name

    def get_type(self) -> str:
        return "claude"

    def _build_noninteractive_command(self, prompt: str) -> list[str]:
        """Build claude command for non-interactive (build/validate/difference) use."""
        cmd = [
            "claude",
            "-p", prompt,
            "--verbose",
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
        cmd.append(prompt)
        return cmd

    def _write_sandbox_settings(self, cwd: str) -> _SandboxContext | None:
        """Write temporary sandbox settings to the project's settings.local.json.

        When sandbox paths are configured on the profile, this writes a
        ``.claude/settings.local.json`` at the git root (or CWD if no git repo)
        that enables Claude Code's OS-level sandbox. Any existing content is
        preserved and merged — only the ``sandbox`` key is added/overwritten.
        The caller must call ``_cleanup_sandbox_settings`` to restore the file.
        """
        if not self._profile.sandbox_write_paths and not self._profile.sandbox_read_paths:
            return None

        project_root = _find_git_root(cwd) or cwd
        settings_dir = Path(project_root) / ".claude"
        settings_path = settings_dir / "settings.local.json"

        # Save original content for restoration
        original_content: str | None = None
        if settings_path.exists():
            original_content = settings_path.read_text(encoding="utf-8")

        # Merge sandbox config into existing settings
        existing: dict = {}
        if original_content:
            try:
                existing = json.loads(original_content)
            except json.JSONDecodeError:
                existing = {}

        existing["sandbox"] = {
            "enabled": True,
            "filesystem": {
                "allowWrite": [
                    "//" + p.lstrip("/") for p in self._profile.sandbox_write_paths
                ],
                "allowRead": [
                    "//" + p.lstrip("/") for p in self._profile.sandbox_read_paths
                ],
            },
        }

        settings_dir.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        return _SandboxContext(settings_path=settings_path, original_content=original_content)

    def _cleanup_sandbox_settings(self, ctx: _SandboxContext | None) -> None:
        """Restore the original settings.local.json after a sandboxed invocation."""
        if ctx is None:
            return
        if ctx.original_content is not None:
            ctx.settings_path.write_text(ctx.original_content, encoding="utf-8")
        elif ctx.settings_path.exists():
            ctx.settings_path.unlink()
            # Remove .claude dir if we created it and it's now empty
            try:
                ctx.settings_path.parent.rmdir()
            except OSError:
                pass

    def _run_noninteractive(self, prompt: str, cwd: str) -> None:
        """Run Claude Code in non-interactive mode, streaming output to stderr."""
        sandbox_ctx = self._write_sandbox_settings(cwd)
        try:
            cmd = self._build_noninteractive_command(prompt)
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=cwd,
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
            self._cleanup_sandbox_settings(sandbox_ctx)


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

    def build(self, ctx: BuildContext) -> BuildResponse:
        self.build_calls.append(ctx)
        return self._build_response

    def validate(self, ctx: BuildContext, validation: Validation) -> ValidationResponse:
        self.validate_calls.append((ctx, validation))
        return ValidationResponse(
            name=validation.name,
            status=self._validation_response.status,
            reason=self._validation_response.reason,
        )

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


# Alias for the factory function matching the spec's naming convention.
CreateFromProfile = create_from_profile
