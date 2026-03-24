"""Generic CLI agent that wraps any command-line tool."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from intentc.build.agents.base import Agent
from intentc.build.agents.models import (
    AgentError,
    AgentProfile,
    BuildContext,
    BuildResponse,
    DifferencingContext,
    DifferencingResponse,
    LogFn,
    PromptTemplates,
    ValidationResponse,
    load_default_prompts,
    render_differencing_prompt,
    render_prompt,
)
from intentc.core.models import Validation


class CLIAgent(Agent):
    """Generic agent that wraps any CLI tool."""

    def __init__(self, profile: AgentProfile, log: LogFn | None = None) -> None:
        self._profile = profile
        self._log = log
        templates = profile.prompt_templates or load_default_prompts()
        self._templates = templates

    def _emit(self, message: str) -> None:
        if self._log:
            self._log(f"    agent: {message}")

    def _run_command(self, prompt: str) -> None:
        """Run the CLI command with the given prompt."""
        if not self._profile.command:
            raise AgentError("CLIAgent requires a command in the profile")

        cmd = [self._profile.command, *self._profile.cli_args, prompt]
        self._emit(f"running: {self._profile.command}")
        try:
            result = subprocess.run(
                cmd,
                timeout=self._profile.timeout,
                capture_output=True,
                text=True,
            )
        except subprocess.TimeoutExpired as exc:
            raise AgentError(
                f"Agent command timed out after {self._profile.timeout}s"
            ) from exc
        except FileNotFoundError as exc:
            raise AgentError(
                f"Agent command not found: {self._profile.command}"
            ) from exc

        if result.returncode != 0:
            raise AgentError(
                f"Agent command failed (exit {result.returncode}): {result.stderr}"
            )

    def _read_response_file(self, path: str) -> dict:
        """Read and parse the JSON response file."""
        p = Path(path)
        if not p.exists():
            raise AgentError(f"Response file not found: {path}")
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError as exc:
            raise AgentError(f"Invalid JSON in response file: {path}") from exc

    def build(self, ctx: BuildContext) -> BuildResponse:
        prompt = render_prompt(self._templates.build, ctx)
        self._run_command(prompt)
        data = self._read_response_file(ctx.response_file_path)
        return BuildResponse(**data)

    def validate(self, ctx: BuildContext, validation: Validation) -> ValidationResponse:
        validation_text = json.dumps(validation.model_dump(), indent=2)
        prompt = render_prompt(
            self._templates.validate_template,
            ctx,
            single_validation_text=validation_text,
        )
        self._run_command(prompt)
        data = self._read_response_file(ctx.response_file_path)
        return ValidationResponse(**data)

    def difference(self, ctx: DifferencingContext) -> DifferencingResponse:
        prompt = render_differencing_prompt(self._templates.difference, ctx)
        self._run_command(prompt)
        data = self._read_response_file(ctx.response_file_path)
        return DifferencingResponse(**data)

    def plan(self, ctx: BuildContext) -> None:
        prompt = render_prompt(self._templates.plan, ctx)
        self._run_command(prompt)

    def get_name(self) -> str:
        return self._profile.name

    def get_type(self) -> str:
        return "cli"
