from __future__ import annotations

import json
import subprocess
import sys
from abc import ABC, abstractmethod
from pathlib import Path

from agent.types import (
    AgentError,
    AgentProfile,
    BuildContext,
    BuildResponse,
    DifferencingContext,
    DifferencingResponse,
    ValidationRef,
    ValidationResponse,
)
from agent.prompts import render_prompt, render_differencing_prompt, render_validate_prompt


class Agent(ABC):
    """Interface for all agent implementations."""

    @abstractmethod
    def build(self, ctx: BuildContext) -> BuildResponse: ...

    @abstractmethod
    def validate(self, ctx: BuildContext, validation: ValidationRef) -> ValidationResponse: ...

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

    def __init__(self, profile: AgentProfile) -> None:
        self._profile = profile

    def get_name(self) -> str:
        return self._profile.name

    def get_type(self) -> str:
        return "cli"

    def build(self, ctx: BuildContext) -> BuildResponse:
        templates = self._profile.prompt_templates
        if templates and templates.build:
            prompt = render_prompt(templates.build, ctx)
        else:
            prompt = render_prompt("{feature}", ctx)
        self._run(prompt, ctx.output_dir)
        data = _read_response_file(ctx.response_file_path)
        return BuildResponse(**data)

    def validate(self, ctx: BuildContext, validation: ValidationRef) -> ValidationResponse:
        templates = self._profile.prompt_templates
        validation_text = f"{validation.name} ({validation.type}, {validation.severity}): {validation.args}"
        if templates and templates.validate_template:
            prompt = render_validate_prompt(templates.validate_template, ctx, validation_text)
        else:
            prompt = validation_text
        self._run(prompt, ctx.output_dir)
        data = _read_response_file(ctx.response_file_path)
        return ValidationResponse(**data)

    def difference(self, ctx: DifferencingContext) -> DifferencingResponse:
        templates = self._profile.prompt_templates
        if templates and templates.difference:
            prompt = render_differencing_prompt(templates.difference, ctx)
        else:
            prompt = f"Compare {ctx.output_dir_a} and {ctx.output_dir_b}"
        self._run(prompt, ctx.output_dir_a)
        data = _read_response_file(ctx.response_file_path)
        return DifferencingResponse(**data)

    def plan(self, ctx: BuildContext) -> None:
        templates = self._profile.prompt_templates
        if templates and templates.plan:
            prompt = render_prompt(templates.plan, ctx)
        else:
            prompt = render_prompt("{feature}", ctx)
        self._run(prompt, ctx.output_dir)

    def _run(self, prompt: str, cwd: str) -> None:
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


def create_from_profile(profile: AgentProfile) -> Agent:
    """Factory function to create an agent from a profile."""
    from agent.claude_agent import ClaudeAgent

    if profile.provider == "claude":
        return ClaudeAgent(profile)
    elif profile.provider == "cli":
        return CLIAgent(profile)
    else:
        raise AgentError(f"Unknown agent provider: {profile.provider!r}")
