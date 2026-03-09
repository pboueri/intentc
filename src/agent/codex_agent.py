"""CodexAgent - intentc agent backed by the Codex CLI."""

from __future__ import annotations

from core.types import AgentProfile, Validation

from .base import BuildContext
from .cli_agent import CLIAgent


class CodexAgent:
    """Agent that delegates to the ``codex`` CLI.

    Composes a :class:`CLIAgent` with Codex-specific command and flags:
    - Command is always ``codex``
    - Translates :pyattr:`AgentProfile.model_id` to ``--model`` flag
    """

    def __init__(self, profile: AgentProfile) -> None:
        modified = profile.model_copy()
        modified.command = "codex"

        extra_args: list[str] = []

        if modified.model_id:
            extra_args.extend(["--model", modified.model_id])

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
        return "codex"
