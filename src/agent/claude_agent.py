from __future__ import annotations

import json
import subprocess
import sys
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


class ClaudeAgent:
    """Agent specialization for Claude Code."""

    def __init__(self, profile: AgentProfile) -> None:
        self._profile = profile

    def get_name(self) -> str:
        return self._profile.name

    def get_type(self) -> str:
        return "claude"

    def build(self, ctx: BuildContext) -> BuildResponse:
        templates = self._profile.prompt_templates
        if templates and templates.build:
            prompt = render_prompt(templates.build, ctx)
        else:
            prompt = render_prompt("{feature}", ctx)
        self._run_non_interactive(prompt, ctx.output_dir)
        data = _read_response_file(ctx.response_file_path)
        return BuildResponse(**data)

    def validate(self, ctx: BuildContext, validation: ValidationRef) -> ValidationResponse:
        templates = self._profile.prompt_templates
        validation_text = f"{validation.name} ({validation.type}, {validation.severity}): {validation.args}"
        if templates and templates.validate_template:
            prompt = render_validate_prompt(templates.validate_template, ctx, validation_text)
        else:
            prompt = validation_text
        self._run_non_interactive(prompt, ctx.output_dir)
        data = _read_response_file(ctx.response_file_path)
        return ValidationResponse(**data)

    def difference(self, ctx: DifferencingContext) -> DifferencingResponse:
        templates = self._profile.prompt_templates
        if templates and templates.difference:
            prompt = render_differencing_prompt(templates.difference, ctx)
        else:
            prompt = f"Compare {ctx.output_dir_a} and {ctx.output_dir_b}"
        self._run_non_interactive(prompt, ctx.output_dir_a)
        data = _read_response_file(ctx.response_file_path)
        return DifferencingResponse(**data)

    def plan(self, ctx: BuildContext) -> None:
        templates = self._profile.prompt_templates
        if templates and templates.plan:
            prompt = render_prompt(templates.plan, ctx)
        else:
            prompt = render_prompt("{feature}", ctx)

        cmd = ["claude"]
        if self._profile.model_id:
            cmd.extend(["--model", self._profile.model_id])
        cmd.extend(self._profile.cli_args)
        cmd.append(prompt)

        subprocess.run(cmd, cwd=ctx.output_dir)

    def _run_non_interactive(self, prompt: str, cwd: str) -> None:
        settings_path = self._setup_sandbox(cwd)
        try:
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

            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            if proc.stdout:
                for line in proc.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        if event.get("type") in ("assistant", "content_block_delta"):
                            text = event.get("text", "") or event.get("delta", {}).get("text", "")
                            if text:
                                print(text, end="", file=sys.stderr)
                    except json.JSONDecodeError:
                        pass

            proc.wait(timeout=self._profile.timeout)

            if proc.returncode != 0:
                stderr_out = proc.stderr.read() if proc.stderr else ""
                raise AgentError(
                    f"Claude agent failed (exit {proc.returncode}): {stderr_out}"
                )
        except subprocess.TimeoutExpired:
            proc.kill()
            raise AgentError(
                f"Claude agent timed out after {self._profile.timeout}s"
            )
        finally:
            self._cleanup_sandbox(settings_path)

    def _setup_sandbox(self, cwd: str) -> Path | None:
        if not self._profile.sandbox_write_paths and not self._profile.sandbox_read_paths:
            return None

        claude_dir = Path(cwd) / ".claude"
        claude_dir.mkdir(exist_ok=True)
        settings_path = claude_dir / "settings.local.json"

        settings = {
            "permissions": {
                "allow": ["*"],
                "deny": [],
            },
            "sandbox": {
                "enabled": True,
                "write_paths": self._profile.sandbox_write_paths,
                "read_paths": self._profile.sandbox_read_paths,
            },
        }
        settings_path.write_text(json.dumps(settings, indent=2))
        return settings_path

    def _cleanup_sandbox(self, settings_path: Path | None) -> None:
        if settings_path and settings_path.is_file():
            settings_path.unlink()


def _read_response_file(path: str) -> dict:
    p = Path(path)
    if not p.is_file():
        raise AgentError(f"Response file not found: {path}")
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        raise AgentError(f"Invalid JSON in response file {path}: {exc}") from exc
