"""Agent protocol and build context for intentc."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field

from core.types import Intent, Validation, ValidationFile


class BuildContext(BaseModel):
    """Context passed to an agent for a single build invocation."""

    intent: Intent = Field(default_factory=Intent)
    validations: list[ValidationFile] = Field(default_factory=list)
    project_root: str = ""
    output_dir: str = ""
    generation_id: str = ""
    dependency_names: list[str] = Field(default_factory=list)
    project_intent: Intent = Field(default_factory=Intent)

    model_config = {"extra": "ignore"}


class Agent(Protocol):
    """Protocol that all agent implementations must satisfy."""

    def build(self, build_ctx: BuildContext) -> list[str]:
        """Run the agent to generate code. Returns list of created/modified file paths."""
        ...

    def validate_with_llm(
        self, validation: Validation, generated_files: list[str]
    ) -> tuple[bool, str]:
        """Use the agent as an LLM judge to validate generated output.

        Returns (passed, explanation).
        """
        ...

    def get_name(self) -> str:
        """Return the human-readable name of this agent."""
        ...

    def get_type(self) -> str:
        """Return the agent type identifier (e.g. 'cli', 'claude', 'codex', 'mock')."""
        ...
