"""Claude Code agent implementation."""

from __future__ import annotations

import json
import os
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


class ClaudeAgent(Agent):
    """Agent specialization for Claude Code CLI."""

    def __init__(self, profile: AgentProfile, log: LogFn | None = None) -> None:
        self._profile = profile
        self._log = log
        self._templates = profile.prompt_templates or load_default_prompts()

    def _emit(self, message: str) -> None:
        if self._log:
            self._log(f"    agent: {message}")

    def _write_sandbox_settings(self) -> Path | None:
        """Write temporary .claude/settings.local.json for sandbox isolation.

        Returns the path if written, None otherwise.
        """
        if not self._profile.sandbox_write_paths and not self._profile.sandbox_read_paths:
            return None

        claude_dir = Path.cwd() / ".claude"
        claude_dir.mkdir(exist_ok=True)
        settings_path = claude_dir / "settings.local.json"

        settings = {
            "permissions": {
                "allow": ["Bash(*)", "WebFetch(*)", "WebSearch(*)"],
                "deny": [],
            },
            "sandbox": {
                "writable_paths": self._profile.sandbox_write_paths,
                "readable_paths": self._profile.sandbox_read_paths,
            },
        }
        settings_path.write_text(json.dumps(settings, indent=2))
        return settings_path

    def _cleanup_sandbox_settings(self, path: Path | None) -> None:
        """Remove temporary sandbox settings file."""
        if path and path.exists():
            path.unlink()

    def _build_base_cmd(self) -> list[str]:
        """Build the base claude CLI command list."""
        cmd = ["claude"]
        if self._profile.model_id:
            cmd.extend(["--model", self._profile.model_id])
        return cmd

    def _run_non_interactive(self, prompt: str, output_dir: str = "") -> None:
        """Run claude in non-interactive mode with streaming JSON output."""
        cmd = self._build_base_cmd()
        cmd.extend([
            "-p", prompt,
            "--verbose",
            "--output-format", "stream-json",
            "--dangerously-skip-permissions",
        ])
        cmd.extend(self._profile.cli_args)

        self._emit("starting claude")
        settings_path = self._write_sandbox_settings()
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # Forward assistant text events through the log callback
                if event.get("type") == "assistant":
                    for block in event.get("content", []):
                        if block.get("type") == "text":
                            text = block.get("text", "")
                            if text:
                                self._emit(text)
            proc.wait(timeout=self._profile.timeout)
            if proc.returncode != 0:
                raise AgentError(
                    f"Claude process exited with code {proc.returncode}"
                )
        except subprocess.TimeoutExpired:
            proc.kill()
            raise AgentError(
                f"Claude agent timed out after {self._profile.timeout}s"
            )
        finally:
            self._cleanup_sandbox_settings(settings_path)

    def _read_response_file(self, path: str, allow_missing: bool = False) -> dict | None:
        """Read and parse the JSON response file."""
        p = Path(path)
        if not p.exists():
            if allow_missing:
                return None
            raise AgentError(f"Response file not found: {path}")
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError as exc:
            raise AgentError(f"Invalid JSON in response file: {path}") from exc

    def _synthesize_build_response(self, output_dir: str) -> BuildResponse:
        """Synthesize a success BuildResponse by scanning the output directory."""
        files: list[str] = []
        out = Path(output_dir)
        if out.exists():
            for f in out.rglob("*"):
                if f.is_file():
                    files.append(str(f.relative_to(out)))
        return BuildResponse(
            status="success",
            summary="Build completed (response synthesized from output directory scan)",
            files_created=files,
        )

    def build(self, ctx: BuildContext) -> BuildResponse:
        prompt = render_prompt(self._templates.build, ctx)
        self._run_non_interactive(prompt, ctx.output_dir)
        data = self._read_response_file(ctx.response_file_path, allow_missing=True)
        if data is None:
            # Agent exited successfully but didn't write response file
            return self._synthesize_build_response(ctx.output_dir)
        return BuildResponse(**data)

    def validate(self, ctx: BuildContext, validation: Validation) -> ValidationResponse:
        validation_text = json.dumps(validation.model_dump(), indent=2)
        prompt = render_prompt(
            self._templates.validate_template,
            ctx,
            single_validation_text=validation_text,
        )
        self._run_non_interactive(prompt)
        data = self._read_response_file(ctx.response_file_path)
        if data is None:
            raise AgentError("Response file missing after validation")
        return ValidationResponse(**data)

    def difference(self, ctx: DifferencingContext) -> DifferencingResponse:
        prompt = render_differencing_prompt(self._templates.difference, ctx)
        self._run_non_interactive(prompt)
        data = self._read_response_file(ctx.response_file_path)
        if data is None:
            raise AgentError("Response file missing after differencing")
        return DifferencingResponse(**data)

    def plan(self, ctx: BuildContext) -> None:
        """Launch Claude Code in interactive REPL mode for planning."""
        prompt = render_prompt(self._templates.plan, ctx)
        cmd = self._build_base_cmd()
        cmd.extend(self._profile.cli_args)
        cmd.append(prompt)

        self._emit("starting interactive planning session")
        try:
            subprocess.run(
                cmd,
                timeout=self._profile.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise AgentError(
                f"Planning session timed out after {self._profile.timeout}s"
            ) from exc

    def get_name(self) -> str:
        return self._profile.name

    def get_type(self) -> str:
        return "claude"
