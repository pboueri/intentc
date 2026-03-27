"""Agent module: interfaces, types, and implementations for intentc agents."""

from __future__ import annotations

import abc
import importlib.resources
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

from intentc.core.models import (
    Implementation,
    IntentFile,
    ProjectIntent,
    ValidationFile,
)

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

LogFn = Callable[[str], None]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AgentError(Exception):
    """Raised when an agent invocation fails."""


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------


class PromptTemplates(BaseModel):
    """Template overrides for agent prompts."""

    build: str = ""
    validate_template: str = ""
    plan: str = ""
    difference: str = ""
    init: str = ""


def load_default_prompts() -> PromptTemplates:
    """Load default prompt templates bundled with the package.

    Uses importlib.resources to locate prompts within the installed package.
    Missing files result in empty template fields (no error raised).
    """
    templates: dict[str, str] = {}

    # Build, validate, plan prompts
    agent_prompts = importlib.resources.files("intentc.build.agents") / "prompts"
    for field, filename in [
        ("build", "build.prompt"),
        ("validate_template", "validate.prompt"),
        ("plan", "plan.prompt"),
        ("init", "init.prompt"),
    ]:
        try:
            templates[field] = (agent_prompts / filename).read_text(encoding="utf-8")
        except (FileNotFoundError, TypeError):
            templates[field] = ""

    # Differencing prompt
    diff_prompts = importlib.resources.files("intentc.differencing") / "prompts"
    try:
        templates["difference"] = (diff_prompts / "difference.prompt").read_text(
            encoding="utf-8"
        )
    except (FileNotFoundError, TypeError):
        templates["difference"] = ""

    return PromptTemplates(**templates)


def render_prompt(
    template: str,
    ctx: BuildContext,
) -> str:
    """Render a build/validate/plan prompt template with BuildContext values."""
    validations_text = "\n\n".join(
        v.model_dump_json(indent=2) for v in ctx.validations
    )
    previous_errors_text = ""
    if ctx.previous_errors:
        bullets = "\n".join(f"- {e}" for e in ctx.previous_errors)
        previous_errors_text = (
            f"\n### Previous Errors\nThe following errors occurred in prior attempts. "
            f"Fix these issues:\n{bullets}\n"
        )

    return template.format(
        project=ctx.project_intent.body if ctx.project_intent else "",
        implementation=ctx.implementation.body if ctx.implementation else "",
        feature=ctx.intent.body if ctx.intent else "",
        validations=validations_text,
        validation=validations_text,
        response_file=ctx.response_file_path,
        previous_errors=previous_errors_text,
        seed_prompt=ctx.seed_prompt,
    )


def render_init_prompt(
    template: str,
    project_name: str,
    user_prompt: str | None = None,
) -> str:
    """Render the init prompt template."""
    specifications = _get_specifications_summary()
    user_prompt_section = ""
    if user_prompt:
        user_prompt_section = (
            f"The user has provided the following project description. "
            f"Generate the full project structure directly without asking questions.\n\n"
            f"## Project Description\n\n{user_prompt}"
        )

    return template.format(
        project_name=project_name,
        specifications=specifications,
        user_prompt=user_prompt_section,
    )


def _get_specifications_summary() -> str:
    """Return a summary of .ic and .icv file format conventions."""
    return """### .ic files (Intent files)

.ic files use YAML frontmatter followed by a Markdown body:

```
---
name: feature_name
depends_on:
  - path/to/dependency
tags:
  - optional_tag
---

# Feature Title

Description of what this feature does, written in Markdown.
```

Required fields:
- `name` — unique identifier

Optional fields:
- `depends_on` — list of feature directory paths this feature depends on
- `tags` — list of string tags
- `authors` — list of author identifiers

For `project.ic`, there is no `depends_on` field. For `implementations/*.ic`, `depends_on` is optional.

### .icv files (Validation files)

.icv files are pure YAML:

```yaml
target: feature/path
agent_profile: null
validations:
  - name: validation_name
    type: agent_validation
    severity: error  # or: warning
    args:
      rubric: "Description of what to validate..."
```

Validation types:
- `agent_validation` — evaluated by an AI agent using a rubric

### Directory structure

```
intent/
  project.ic                    # Project-level description (required)
  implementations/
    default.ic                  # Implementation approach
  feature_name/
    feature_name.ic             # Feature intent
    validation.icv              # Feature validations (optional)
  another_feature/
    another_feature.ic
```

Features are directories under `intent/`. Each feature directory contains at least one .ic file. The directory path relative to `intent/` is the feature's identifier used in `depends_on`.
"""


def render_differencing_prompt(
    template: str,
    ctx: DifferencingContext,
) -> str:
    """Render a differencing prompt template with DifferencingContext values."""
    return template.format(
        project=ctx.project_intent.body if ctx.project_intent else "",
        implementation=ctx.implementation.body if ctx.implementation else "",
        output_dir_a=ctx.output_dir_a,
        output_dir_b=ctx.output_dir_b,
        response_file=ctx.response_file_path,
    )


# ---------------------------------------------------------------------------
# Agent profile
# ---------------------------------------------------------------------------


class AgentProfile(BaseModel):
    """Named, reusable agent configuration."""

    name: str
    provider: str  # "claude", "codex", or "cli"
    command: str = ""
    cli_args: list[str] = Field(default_factory=list)
    timeout: float = 3600.0
    retries: int = 3
    model_id: str | None = None
    effort: str | None = None  # Claude-specific: "low", "medium", "high", "max"
    prompt_templates: PromptTemplates | None = None
    sandbox_write_paths: list[str] = Field(default_factory=list)
    sandbox_read_paths: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Contexts
# ---------------------------------------------------------------------------


class BuildContext(BaseModel):
    """Everything the agent needs to act on a target."""

    intent: IntentFile
    validations: list[ValidationFile] = Field(default_factory=list)
    output_dir: str
    generation_id: str
    dependency_names: list[str] = Field(default_factory=list)
    project_intent: ProjectIntent
    implementation: Implementation | None = None
    response_file_path: str
    previous_errors: list[str] = Field(default_factory=list)
    seed_prompt: str = ""


class DifferencingContext(BaseModel):
    """Everything the agent needs to perform a differencing evaluation."""

    output_dir_a: str
    output_dir_b: str
    project_intent: ProjectIntent
    response_file_path: str
    implementation: Implementation | None = None


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class BuildResponse(BaseModel):
    """Written by the agent after a build invocation."""

    status: str  # "success" or "failure"
    summary: str
    files_created: list[str] = Field(default_factory=list)
    files_modified: list[str] = Field(default_factory=list)


class ValidationResponse(BaseModel):
    """Written after a single validation is evaluated."""

    name: str
    status: str  # "pass" or "fail"
    reason: str


class DimensionResult(BaseModel):
    """Per-axis evaluation result for differencing."""

    name: str
    status: str  # "pass" or "fail"
    rationale: str


class DifferencingResponse(BaseModel):
    """Written by the agent after a differencing evaluation."""

    status: str  # "equivalent" or "divergent"
    dimensions: list[DimensionResult] = Field(default_factory=list)
    summary: str


# ---------------------------------------------------------------------------
# Agent interface
# ---------------------------------------------------------------------------


class Agent(abc.ABC):
    """Abstract agent interface. All agents implement these methods."""

    @abc.abstractmethod
    def build(self, ctx: BuildContext) -> BuildResponse: ...

    @abc.abstractmethod
    def validate(self, ctx: BuildContext, validation: ValidationFile) -> ValidationResponse: ...

    @abc.abstractmethod
    def difference(self, ctx: DifferencingContext) -> DifferencingResponse: ...

    @abc.abstractmethod
    def plan(self, ctx: BuildContext) -> None: ...

    @abc.abstractmethod
    def init(self, project_name: str, intent_dir: str, prompt: str | None = None) -> None: ...

    @abc.abstractmethod
    def get_name(self) -> str: ...

    @abc.abstractmethod
    def get_type(self) -> str: ...


# ---------------------------------------------------------------------------
# CLIAgent
# ---------------------------------------------------------------------------


class CLIAgent(Agent):
    """Generic agent wrapping any command-line tool."""

    def __init__(
        self,
        profile: AgentProfile,
        log: LogFn | None = None,
    ) -> None:
        self._profile = profile
        self._log = log or (lambda _msg: None)
        self._templates = profile.prompt_templates or load_default_prompts()

    def get_name(self) -> str:
        return self._profile.name

    def get_type(self) -> str:
        return "cli"

    def build(self, ctx: BuildContext) -> BuildResponse:
        prompt = render_prompt(self._templates.build, ctx)
        self._run_command(prompt, ctx.response_file_path, timeout=self._profile.timeout)
        return self._read_build_response(ctx.response_file_path)

    def validate(self, ctx: BuildContext, validation: ValidationFile) -> ValidationResponse:
        prompt = render_prompt(self._templates.validate_template, ctx)
        self._run_command(prompt, ctx.response_file_path, timeout=self._profile.timeout)
        return self._read_validation_response(ctx.response_file_path)

    def difference(self, ctx: DifferencingContext) -> DifferencingResponse:
        prompt = render_differencing_prompt(self._templates.difference, ctx)
        self._run_command(prompt, ctx.response_file_path, timeout=self._profile.timeout)
        return self._read_differencing_response(ctx.response_file_path)

    def plan(self, ctx: BuildContext) -> None:
        prompt = render_prompt(self._templates.plan, ctx)
        self._run_command(prompt, ctx.response_file_path, timeout=self._profile.timeout)

    def init(self, project_name: str, intent_dir: str, prompt: str | None = None) -> None:
        rendered = render_init_prompt(self._templates.init, project_name, prompt)
        self._run_command(rendered, "", timeout=self._profile.timeout)

    def _run_command(
        self,
        prompt: str,
        response_file_path: str,
        timeout: float,
    ) -> None:
        command = self._profile.command
        if not command:
            raise AgentError("CLIAgent requires a command in the profile")

        cmd = command.split() + self._profile.cli_args
        self._log(f"    agent: running {cmd[0]}")

        try:
            result = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise AgentError(
                f"Agent command timed out after {timeout}s: {command}"
            ) from exc
        except OSError as exc:
            raise AgentError(f"Failed to run agent command: {command}: {exc}") from exc

        if result.returncode != 0:
            raise AgentError(
                f"Agent command failed (exit {result.returncode}): {result.stderr or result.stdout}"
            )

    def _read_build_response(self, path: str) -> BuildResponse:
        return BuildResponse(**self._read_json(path))

    def _read_validation_response(self, path: str) -> ValidationResponse:
        return ValidationResponse(**self._read_json(path))

    def _read_differencing_response(self, path: str) -> DifferencingResponse:
        return DifferencingResponse(**self._read_json(path))

    def _read_json(self, path: str) -> dict:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError as exc:
            raise AgentError(f"Response file not found: {path}") from exc
        except json.JSONDecodeError as exc:
            raise AgentError(
                f"Response file contains invalid JSON: {path}: {exc}"
            ) from exc


# ---------------------------------------------------------------------------
# ClaudeAgent
# ---------------------------------------------------------------------------


class ClaudeAgent(Agent):
    """Agent specialization for Claude Code."""

    def __init__(
        self,
        profile: AgentProfile,
        log: LogFn | None = None,
    ) -> None:
        self._profile = profile
        self._log = log or (lambda _msg: None)
        self._templates = profile.prompt_templates or load_default_prompts()

    def get_name(self) -> str:
        return self._profile.name

    def get_type(self) -> str:
        return "claude"

    def build(self, ctx: BuildContext) -> BuildResponse:
        prompt = render_prompt(self._templates.build, ctx)
        self._run_non_interactive(prompt, ctx.output_dir, ctx.response_file_path)
        return self._read_build_response(ctx.response_file_path, ctx.output_dir)

    def validate(self, ctx: BuildContext, validation: ValidationFile) -> ValidationResponse:
        prompt = render_prompt(self._templates.validate_template, ctx)
        self._run_non_interactive(prompt, ctx.output_dir, ctx.response_file_path)
        return self._read_validation_response(ctx.response_file_path)

    def difference(self, ctx: DifferencingContext) -> DifferencingResponse:
        prompt = render_differencing_prompt(self._templates.difference, ctx)
        self._run_non_interactive(prompt, ctx.output_dir_a, ctx.response_file_path)
        return self._read_differencing_response(ctx.response_file_path)

    def plan(self, ctx: BuildContext) -> None:
        prompt = render_prompt(self._templates.plan, ctx)
        self._run_interactive(prompt, ctx.output_dir)

    def init(self, project_name: str, intent_dir: str, prompt: str | None = None) -> None:
        rendered = render_init_prompt(self._templates.init, project_name, prompt)
        # intent_dir's parent is the project root
        project_root = str(Path(intent_dir).parent)
        if prompt is not None:
            # Single-shot mode: run non-interactively with -p
            self._run_non_interactive(rendered, project_root, "")
        else:
            # Interactive mode: launch REPL
            self._run_interactive(rendered, project_root)

    # ---- internal helpers ----

    def _run_non_interactive(
        self,
        prompt: str,
        cwd: str,
        response_file_path: str,
    ) -> None:
        self._log("    agent: starting claude")

        settings_path = self._write_sandbox_settings(cwd)

        try:
            cmd = self._build_cmd(prompt)
            self._log(f"    agent: running claude with {len(prompt)} char prompt")

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                cwd=cwd,
            )

            assert process.stdout is not None
            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if event.get("type") == "assistant":
                    message = event.get("message", {})
                    content_blocks = message.get("content", [])
                    for block in content_blocks:
                        if block.get("type") == "text":
                            text = block.get("text", "")
                            for text_line in text.splitlines():
                                self._log(f"    agent: {text_line}")

            returncode = process.wait(timeout=self._profile.timeout)

            if returncode != 0:
                raise AgentError(f"Claude process exited with code {returncode}")

        finally:
            if settings_path and os.path.exists(settings_path):
                os.remove(settings_path)

    def _run_interactive(self, prompt: str, cwd: str) -> None:
        """Launch Claude Code in interactive REPL mode for planning."""
        self._log("    agent: starting claude (interactive)")

        cmd = ["claude"]
        if self._profile.model_id:
            cmd.extend(["--model", self._profile.model_id])
        if self._profile.effort:
            cmd.extend(["--effort", self._profile.effort])
        cmd.extend(self._profile.cli_args)
        cmd.append(prompt)

        try:
            subprocess.run(cmd, cwd=cwd, check=False)
        except OSError as exc:
            raise AgentError(f"Failed to launch Claude interactive mode: {exc}") from exc

    def _build_cmd(self, prompt: str) -> list[str]:
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
        if self._profile.effort:
            cmd.extend(["--effort", self._profile.effort])
        cmd.extend(self._profile.cli_args)
        return cmd

    def _write_sandbox_settings(self, cwd: str) -> str | None:
        """Write temporary .claude/settings.local.json for sandbox enforcement."""
        if not self._profile.sandbox_write_paths and not self._profile.sandbox_read_paths:
            return None

        claude_dir = os.path.join(cwd, ".claude")
        os.makedirs(claude_dir, exist_ok=True)
        settings_path = os.path.join(claude_dir, "settings.local.json")

        settings = {
            "permissions": {
                "allow": ["Bash(*)", "WebFetch(*)", "WebSearch(*)"],
                "deny": [],
            },
            "sandbox": {
                "enabled": True,
                "write_paths": self._profile.sandbox_write_paths,
                "read_paths": self._profile.sandbox_read_paths,
            },
        }

        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)

        return settings_path

    def _read_build_response(
        self, path: str, output_dir: str
    ) -> BuildResponse:
        """Read build response, synthesizing one if file missing but exit was ok."""
        if os.path.exists(path):
            data = self._read_json(path)
            return BuildResponse(**data)

        # Synthesize success response by scanning output directory
        files: list[str] = []
        output = Path(output_dir)
        if output.is_dir():
            for p in output.rglob("*"):
                if p.is_file():
                    files.append(str(p.relative_to(output)))

        return BuildResponse(
            status="success",
            summary="Build completed (response file missing, synthesized from output directory)",
            files_created=files,
        )

    def _read_validation_response(self, path: str) -> ValidationResponse:
        return ValidationResponse(**self._read_json(path))

    def _read_differencing_response(self, path: str) -> DifferencingResponse:
        return DifferencingResponse(**self._read_json(path))

    def _read_json(self, path: str) -> dict:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError as exc:
            raise AgentError(f"Response file not found: {path}") from exc
        except json.JSONDecodeError as exc:
            raise AgentError(
                f"Response file contains invalid JSON: {path}: {exc}"
            ) from exc


# ---------------------------------------------------------------------------
# MockAgent
# ---------------------------------------------------------------------------


class MockAgent(Agent):
    """Mock agent for testing. Records calls and returns configurable responses."""

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
            summary="Mock differencing completed",
        )
        self.build_calls: list[BuildContext] = []
        self.validate_calls: list[tuple[BuildContext, ValidationFile]] = []
        self.difference_calls: list[DifferencingContext] = []
        self.plan_calls: list[BuildContext] = []
        self.init_calls: list[tuple[str, str, str | None]] = []

    def get_name(self) -> str:
        return self._name

    def get_type(self) -> str:
        return "mock"

    def build(self, ctx: BuildContext) -> BuildResponse:
        self.build_calls.append(ctx)
        return self._build_response

    def validate(self, ctx: BuildContext, validation: ValidationFile) -> ValidationResponse:
        self.validate_calls.append((ctx, validation))
        return self._validation_response

    def difference(self, ctx: DifferencingContext) -> DifferencingResponse:
        self.difference_calls.append(ctx)
        return self._differencing_response

    def plan(self, ctx: BuildContext) -> None:
        self.plan_calls.append(ctx)

    def init(self, project_name: str, intent_dir: str, prompt: str | None = None) -> None:
        self.init_calls.append((project_name, intent_dir, prompt))


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_from_profile(
    profile: AgentProfile,
    log: LogFn | None = None,
) -> Agent:
    """Create an agent from an AgentProfile.

    Args:
        profile: The agent profile to create from.
        log: Optional logging callback.

    Returns:
        An Agent implementation.

    Raises:
        AgentError: If the provider is unknown.
    """
    provider = profile.provider.lower()
    if provider == "claude":
        return ClaudeAgent(profile, log=log)
    if provider == "cli":
        return CLIAgent(profile, log=log)
    raise AgentError(
        f"Unknown agent provider: {profile.provider!r}. "
        f"Supported providers: 'claude', 'cli'"
    )
