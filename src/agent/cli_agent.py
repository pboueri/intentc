"""CLIAgent - wraps any CLI tool as an intentc agent."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path

from core.types import AgentProfile, Validation

from .base import BuildContext

logger = logging.getLogger(__name__)


def _log_output_line(line: str) -> None:
    """Log a stdout line, parsing JSON events for readability."""
    stripped = line.rstrip()
    try:
        event = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        if stripped:
            logger.debug("  [agent] %s", stripped)
        return

    event_type = event.get("type", "")

    if event_type == "assistant":
        _log_assistant_event(event)
    elif event_type == "result":
        is_error = event.get("is_error", False)
        if is_error:
            logger.warning("  [agent] finished with error")
        else:
            logger.debug("  [agent] finished")
    elif event_type == "system":
        _log_system_event(event)
    elif event_type == "user":
        _log_user_event(event)
    elif event_type == "rate_limit_event":
        pass  # noisy, suppress
    else:
        logger.debug("  [agent:out] %s", stripped[:200])


def _log_assistant_event(event: dict) -> None:
    """Log assistant message blocks (text and tool_use)."""
    msg = event.get("message", {})
    for block in msg.get("content", []):
        kind = block.get("type", "")
        if kind == "text":
            text = block.get("text", "").strip()
            if text:
                logger.info("  [agent] %s", text[:500])
        elif kind == "tool_use":
            name = block.get("name", "?")
            input_data = block.get("input", {})
            detail = (
                input_data.get("file_path")
                or input_data.get("command", "")[:200]
                or ""
            )
            if detail:
                logger.info("  [agent] %s %s", name, detail)
            else:
                logger.info("  [agent] %s", name)


def _log_system_event(event: dict) -> None:
    """Log system events (init, hooks, tasks) concisely."""
    subtype = event.get("subtype", "")
    if subtype == "init":
        session_id = event.get("session_id", "?")
        logger.debug("  [agent] session %s started", session_id[:12])
    elif subtype == "task_started":
        desc = event.get("description", "")
        logger.info("  [agent] subtask: %s", desc[:200])
    elif subtype == "task_progress":
        desc = event.get("description", "")
        if desc:
            logger.debug("  [agent] subtask progress: %s", desc[:200])
    elif subtype in ("hook_started", "hook_response"):
        pass  # suppress hook noise
    else:
        logger.debug("  [agent:sys] %s", subtype)


def _log_user_event(event: dict) -> None:
    """Log user events (tool results) concisely."""
    msg = event.get("message", {})
    for block in msg.get("content", []):
        if block.get("type") == "tool_result":
            if block.get("is_error"):
                content = block.get("content", "")[:200]
                logger.warning("  [agent] tool error: %s", content)
            # Suppress successful tool results — the tool_use log is enough


class CLIAgent:
    """Agent implementation that delegates to an arbitrary CLI command.

    The CLI tool receives a prompt on stdin (or via a ``-p`` flag) and is
    expected to write generated files into the output directory.  File
    detection is done by parsing stdout for paths or by scanning the output
    directory for recently-modified files.
    """

    def __init__(self, profile: AgentProfile) -> None:
        self.profile = profile

    # ------------------------------------------------------------------
    # Agent protocol
    # ------------------------------------------------------------------

    def build(self, build_ctx: BuildContext) -> list[str]:
        """Construct a prompt, run the CLI tool, and return generated file paths."""
        prompt = self._construct_build_prompt(build_ctx)
        last_exc: Exception | None = None

        for attempt in range(1, self.profile.retries + 1):
            try:
                stdout, stderr, rc = self._run_command(
                    prompt, cwd=build_ctx.output_dir
                )
                if rc != 0:
                    raise RuntimeError(
                        f"Command exited with code {rc}.\nstderr: {stderr}"
                    )
                return self._detect_files(stdout, build_ctx.output_dir)
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Build attempt %d/%d failed: %s",
                    attempt,
                    self.profile.retries,
                    exc,
                )
                if attempt < self.profile.retries:
                    time.sleep(self.profile.rate_limit.total_seconds())

        raise RuntimeError(
            f"All {self.profile.retries} build attempts failed"
        ) from last_exc

    def validate_with_llm(
        self, validation: Validation, generated_files: list[str]
    ) -> tuple[bool, str]:
        """Run the CLI tool as an LLM judge and parse pass/fail."""
        prompt = self._construct_validate_prompt(validation, generated_files)
        stdout, _stderr, _rc = self._run_command(prompt)
        return self._parse_validation_output(stdout)

    def get_name(self) -> str:
        return self.profile.name

    def get_type(self) -> str:
        return "cli"

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _construct_build_prompt(self, build_ctx: BuildContext) -> str:
        """Build the prompt string sent to the CLI tool."""
        # If the profile supplies a custom build template, use it verbatim.
        if self.profile.prompt_templates.build:
            return self.profile.prompt_templates.build

        parts: list[str] = []

        # System prompt
        system = self.profile.prompt_templates.system or (
            "You are a code generation agent. Generate the requested code "
            "following best practices. You MUST write all generated files to "
            "the output directory specified in <output-dir> using file write tools. "
            "Create the necessary subdirectories under the output directory. "
            "Do NOT just output code as text - actually write the files to disk. "
            "You only have access to files within the output directory. "
            "Do NOT read, reference, or depend on any files outside of it."
        )
        parts.append(f"<system>\n{system}\n</system>")

        # Project-level intent
        if build_ctx.project_intent.content:
            parts.append(
                f"<project-intent>\n{build_ctx.project_intent.content}\n</project-intent>"
            )

        # Feature intent
        if build_ctx.intent.content:
            parts.append(
                f"<feature-intent name=\"{build_ctx.intent.name}\">\n"
                f"{build_ctx.intent.content}\n</feature-intent>"
            )

        # Validations
        if build_ctx.validations:
            val_lines: list[str] = []
            for vf in build_ctx.validations:
                for v in vf.validations:
                    val_lines.append(
                        f"- [{v.type.value}] {v.name}: {v.parameters}"
                    )
            if val_lines:
                parts.append(
                    "<validations>\n" + "\n".join(val_lines) + "\n</validations>"
                )

        # Output directory
        if build_ctx.output_dir:
            parts.append(f"<output-dir>{build_ctx.output_dir}</output-dir>")

        # Dependencies
        if build_ctx.dependency_names:
            deps = ", ".join(build_ctx.dependency_names)
            parts.append(f"<dependencies>{deps}</dependencies>")

        return "\n\n".join(parts)

    def _construct_validate_prompt(
        self, validation: Validation, generated_files: list[str]
    ) -> str:
        """Build the prompt used for LLM-judge validation."""
        if self.profile.prompt_templates.validate_prompt:
            return self.profile.prompt_templates.validate_prompt

        parts: list[str] = []
        parts.append(
            "You are a code review judge. Evaluate the following code "
            "against the rubric and respond with PASS or FAIL on the first "
            "line, followed by an explanation."
        )

        # Rubric
        rubric = validation.parameters.get("rubric", validation.name)
        parts.append(f"<rubric>\n{rubric}\n</rubric>")

        # Context files
        if generated_files:
            parts.append("<files>")
            for fpath in generated_files:
                try:
                    content = Path(fpath).read_text(errors="replace")
                    parts.append(f"--- {fpath} ---\n{content}")
                except OSError:
                    parts.append(f"--- {fpath} --- (unreadable)")
            parts.append("</files>")

        parts.append(
            "Respond with exactly PASS or FAIL on the first line, "
            "then an explanation."
        )
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Subprocess execution
    # ------------------------------------------------------------------

    def _run_command(
        self, prompt: str, *, cwd: str | None = None
    ) -> tuple[str, str, int]:
        """Execute the CLI command with the given prompt.

        Streams stderr to the logger in real-time for visibility during
        long-running commands.  Stdout is captured for file-path detection.

        Parameters
        ----------
        cwd:
            Working directory for the subprocess.  When set, the agent
            process starts in this directory, limiting its default
            filesystem scope.

        Returns (stdout, stderr, exit_code).
        """
        command = self.profile.command
        if not command:
            raise ValueError("AgentProfile.command must be set for CLIAgent")

        cmd_parts = [command, *self.profile.cli_args]
        timeout_secs = self.profile.timeout.total_seconds() or None

        if cwd:
            os.makedirs(cwd, exist_ok=True)

        logger.debug("Running command: %s", " ".join(cmd_parts))

        proc = subprocess.Popen(
            cmd_parts,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
        )

        # All three pipes are read/written in threads to avoid deadlocks
        # when the prompt exceeds the OS pipe buffer (~64KB).
        import threading

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        def _write_stdin() -> None:
            if prompt:
                proc.stdin.write(prompt)
            proc.stdin.close()

        def _read_stdout() -> None:
            for line in proc.stdout:
                stdout_lines.append(line)
                _log_output_line(line)

        def _read_stderr() -> None:
            for line in proc.stderr:
                stderr_lines.append(line)
                logger.debug("  [agent:err] %s", line.rstrip())

        t_in = threading.Thread(target=_write_stdin, daemon=True)
        t_out = threading.Thread(target=_read_stdout, daemon=True)
        t_err = threading.Thread(target=_read_stderr, daemon=True)
        t_in.start()
        t_out.start()
        t_err.start()

        proc.wait(timeout=timeout_secs)
        t_in.join()
        t_out.join()
        t_err.join()

        return "".join(stdout_lines), "".join(stderr_lines), proc.returncode

    # ------------------------------------------------------------------
    # File detection
    # ------------------------------------------------------------------

    def _detect_files(self, stdout: str, output_dir: str) -> list[str]:
        """Detect files created or modified by the CLI tool.

        Strategy:
        1. Parse stdout for lines that look like absolute file paths within
           *output_dir*.
        2. Fallback: walk *output_dir* and collect all files.
        """
        found: list[str] = []

        # Strategy 1 - parse stdout for paths
        if output_dir:
            norm_dir = os.path.normpath(output_dir)
            for line in stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                # Strip common prefixes like "Created: ", "wrote ", etc.
                for prefix in ("Created: ", "created: ", "Wrote: ", "wrote ", "File: "):
                    if line.startswith(prefix):
                        line = line[len(prefix):]
                        break
                norm_line = os.path.normpath(line)
                if os.path.isabs(norm_line) and norm_line.startswith(norm_dir):
                    if os.path.isfile(norm_line):
                        found.append(norm_line)

        # Strategy 2 - fallback: walk output_dir
        if not found and output_dir and os.path.isdir(output_dir):
            for root, _dirs, files in os.walk(output_dir):
                for fname in files:
                    found.append(os.path.join(root, fname))

        return sorted(set(found))

    # ------------------------------------------------------------------
    # Validation output parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_validation_output(stdout: str) -> tuple[bool, str]:
        """Parse PASS/FAIL from the first line of LLM output."""
        lines = stdout.strip().splitlines()
        if not lines:
            return False, "No output from validation agent"

        first = lines[0].strip().upper()
        explanation = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""

        if first.startswith("PASS"):
            return True, explanation or "Passed"
        return False, explanation or "Failed"
