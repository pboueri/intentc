from __future__ import annotations

import json
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path

from intentc.core.types import Validation

from intentc.build.agents.types import (
    AgentError,
    AgentProfile,
    BuildContext,
    BuildResponse,
    DifferencingContext,
    DifferencingResponse,
    ValidationResponse,
)
from intentc.build.agents.prompts import render_prompt, render_differencing_prompt, render_validate_prompt

LogFn = Callable[[str], None]


class Agent(ABC):
    """Interface for all agent implementations."""

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


def _read_response_file(path: str) -> dict:
    """Read and parse a JSON response file."""
    p = Path(path)
    if not p.is_file():
        raise AgentError(f"Response file not found: {path}")
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        raise AgentError(f"Invalid JSON in response file {path}: {exc}") from exc


class CLIAgent(Agent):
    """Generic agent that wraps any command-line tool."""

    def __init__(self, profile: AgentProfile, log: LogFn | None = None) -> None:
        self._profile = profile
        self._log = log or (lambda _: None)

    def get_name(self) -> str:
        return self._profile.name

    def get_type(self) -> str:
        return "cli"

    def build(self, ctx: BuildContext) -> BuildResponse:
        prompt = render_prompt(self._profile.prompt_templates.build, ctx)
        self._run(prompt, ctx.output_dir)
        data = _read_response_file(ctx.response_file_path)
        return BuildResponse(**data)

    def validate(self, ctx: BuildContext, validation: Validation) -> ValidationResponse:
        validation_text = f"{validation.name} ({validation.type}, {validation.severity.value}): {validation.args}"
        prompt = render_validate_prompt(self._profile.prompt_templates.validate_template, ctx, validation_text)
        self._run(prompt, ctx.output_dir)
        data = _read_response_file(ctx.response_file_path)
        return ValidationResponse(**data)

    def difference(self, ctx: DifferencingContext) -> DifferencingResponse:
        prompt = render_differencing_prompt(self._profile.prompt_templates.difference, ctx)
        self._run(prompt, ctx.output_dir_a)
        data = _read_response_file(ctx.response_file_path)
        return DifferencingResponse(**data)

    def plan(self, ctx: BuildContext) -> None:
        prompt = render_prompt(self._profile.prompt_templates.plan, ctx)
        self._run(prompt, ctx.output_dir)

    def _run(self, prompt: str, cwd: str) -> None:
        """Execute the CLI command with the given prompt."""
        if not self._profile.command:
            raise AgentError("CLIAgent requires a command in the profile")
        cmd = [self._profile.command, *self._profile.cli_args, prompt]
        try:
            result = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=self._profile.timeout,
            )
            if result.returncode != 0:
                raise AgentError(
                    f"Agent command failed (exit {result.returncode}): {result.stderr}"
                )
        except subprocess.TimeoutExpired as exc:
            raise AgentError(
                f"Agent command timed out after {self._profile.timeout}s"
            ) from exc
        except FileNotFoundError as exc:
            raise AgentError(
                f"Agent command not found: {self._profile.command}"
            ) from exc


def create_from_profile(profile: AgentProfile, log: LogFn | None = None) -> Agent:
    """Factory function to create an agent from a profile."""
    from intentc.build.agents.claude_agent import ClaudeAgent
    from intentc.build.agents.prompts import load_default_prompts

    if not profile.prompt_templates:
        profile = profile.model_copy(update={"prompt_templates": load_default_prompts()})

    if profile.provider == "claude":
        return ClaudeAgent(profile, log=log)
    elif profile.provider == "cli":
        return CLIAgent(profile, log=log)
    else:
        raise AgentError(f"Unknown agent provider: {profile.provider!r}")
