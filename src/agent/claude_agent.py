"""ClaudeAgent - intentc agent backed by the Claude CLI."""

from __future__ import annotations

import json
import logging
import time

from core.types import AgentProfile, Validation

from .base import BuildContext
from .cli_agent import CLIAgent

logger = logging.getLogger(__name__)


class ClaudeAgent:
    """Agent that delegates to the ``claude`` CLI.

    Composes a :class:`CLIAgent` with Claude-specific command and flags:
    - Command is always ``claude``
    - Adds ``-p`` (print/pipe mode) and ``--output-format stream-json``
    - Translates :pyattr:`AgentProfile.tools` to ``--allowedTools`` flags
    - Translates :pyattr:`AgentProfile.model_id` to ``--model`` flag

    Uses stream-json output for real-time visibility into what the agent
    is doing during long builds.
    """

    def __init__(self, profile: AgentProfile) -> None:
        modified = profile.model_copy()
        modified.command = "claude"

        # Build Claude-specific CLI args
        # stream-json requires --verbose
        extra_args: list[str] = [
            "-p",
            "--output-format", "stream-json",
            "--verbose",
            "--dangerously-skip-permissions"
        ]

        if modified.model_id:
            extra_args.extend(["--model", modified.model_id])

        enabled_tools = [t.name for t in modified.tools if t.enabled]
        if not enabled_tools:
            # Full toolset — filesystem access is sandboxed by the OS
            enabled_tools = ["Write", "Edit", "Bash", "Read", "Glob", "Grep"]
        for tool_name in enabled_tools:
            extra_args.extend(["--allowedTools", tool_name])

        modified.cli_args = extra_args + list(modified.cli_args)

        self._cli_agent = CLIAgent(modified)

    # ------------------------------------------------------------------
    # Agent protocol
    # ------------------------------------------------------------------

    def build(self, build_ctx: BuildContext) -> list[str]:
        prompt = self._cli_agent._construct_build_prompt(build_ctx)
        last_exc: Exception | None = None

        for attempt in range(1, self._cli_agent.profile.retries + 1):
            try:
                stdout, stderr, rc = self._cli_agent._run_command(
                    prompt, cwd=build_ctx.output_dir
                )
                if rc != 0:
                    raise RuntimeError(
                        f"Command exited with code {rc}.\nstderr: {stderr}"
                    )
                result_text, written_files = _parse_stream_json(stdout)

                if written_files:
                    # Use files detected from tool_use events
                    return sorted(set(written_files))

                # Fallback: detect from result text or directory walk
                return self._cli_agent._detect_files(
                    result_text, build_ctx.output_dir
                )
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Build attempt %d/%d failed: %s",
                    attempt,
                    self._cli_agent.profile.retries,
                    exc,
                )
                if attempt < self._cli_agent.profile.retries:
                    time.sleep(
                        self._cli_agent.profile.rate_limit.total_seconds()
                    )

        raise RuntimeError(
            f"All {self._cli_agent.profile.retries} build attempts failed"
        ) from last_exc

    def validate_with_llm(
        self, validation: Validation, generated_files: list[str]
    ) -> tuple[bool, str]:
        return self._cli_agent.validate_with_llm(validation, generated_files)

    def get_name(self) -> str:
        return self._cli_agent.get_name()

    def get_type(self) -> str:
        return "claude"


# ------------------------------------------------------------------
# stream-json parsing
# ------------------------------------------------------------------


def _parse_stream_json(stdout: str) -> tuple[str, list[str]]:
    """Parse Claude stream-json output.

    Returns (result_text, written_files).
    """
    result_text = ""
    written_files: list[str] = []

    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        event_type = event.get("type")

        if event_type == "result":
            result_text = event.get("result", "")

        elif event_type == "assistant":
            msg = event.get("message", {})
            for block in msg.get("content", []):
                if block.get("type") == "tool_use":
                    name = block.get("name", "")
                    input_data = block.get("input", {})
                    # Detect file writes from Write/Edit tool use
                    if name in ("Write", "file_write"):
                        fp = input_data.get("file_path", "")
                        if fp:
                            written_files.append(fp)

    return result_text, written_files
