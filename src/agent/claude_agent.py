"""ClaudeAgent - intentc agent backed by the Claude CLI."""

from __future__ import annotations

from core.types import AgentProfile, Validation

from .base import BuildContext
from .cli_agent import CLIAgent


class ClaudeAgent:
    """Agent that delegates to the ``claude`` CLI.

    Composes a :class:`CLIAgent` with Claude-specific command and flags:
    - Command is always ``claude``
    - Adds ``-p`` (print/pipe mode) and ``--output-format text``
    - Translates :pyattr:`AgentProfile.tools` to ``--allowedTools`` flags
    - Translates :pyattr:`AgentProfile.model_id` to ``--model`` flag
    """

    def __init__(self, profile: AgentProfile) -> None:
        modified = profile.model_copy()
        modified.command = "claude"

        # Build Claude-specific CLI args
        extra_args: list[str] = ["-p", "--output-format", "text"]

        if modified.model_id:
            extra_args.extend(["--model", modified.model_id])

        for tool in modified.tools:
            if tool.enabled:
                extra_args.extend(["--allowedTools", tool.name])

        modified.cli_args = extra_args + list(modified.cli_args)

        self._cli_agent = CLIAgent(modified)

    # ------------------------------------------------------------------
    # Delegate all Agent protocol methods
    # ------------------------------------------------------------------

    def build(self, build_ctx: BuildContext) -> list[str]:
        return self._cli_agent.build(build_ctx)

    def validate_with_llm(
        self, validation: Validation, generated_files: list[str]
    ) -> tuple[bool, str]:
        return self._cli_agent.validate_with_llm(validation, generated_files)

    def get_name(self) -> str:
        return self._cli_agent.get_name()

    def get_type(self) -> str:
        return "claude"
