"""Validator registry for intentc validation pipeline."""

from __future__ import annotations

from core.types import ValidationType

from validation.validators import (
    CommandCheckValidator,
    FileCheckValidator,
    FolderCheckValidator,
    JudgeAgent,
    LLMJudgeValidator,
    Validator,
)


class Registry:
    """Registry that maps ValidationType values to Validator instances.

    Built-in deterministic validators (file_check, folder_check, command_check)
    are registered automatically on construction. The LLM judge must be
    registered separately via :meth:`register_llm_judge` because it requires
    an agent dependency.
    """

    def __init__(self) -> None:
        self._validators: dict[ValidationType, Validator] = {}
        # Register all built-in deterministic validators
        self.register(FileCheckValidator())
        self.register(FolderCheckValidator())
        self.register(CommandCheckValidator())

    def register(self, v: Validator) -> None:
        """Register a validator, keyed by its validator_type()."""
        self._validators[v.validator_type()] = v

    def get(self, t: ValidationType) -> Validator:
        """Look up a validator by type. Raises KeyError if not registered."""
        if t not in self._validators:
            raise KeyError(f"no validator registered for type: {t.value}")
        return self._validators[t]

    def register_llm_judge(self, agent: JudgeAgent) -> None:
        """Register the LLM judge validator with the given agent."""
        self.register(LLMJudgeValidator(agent))

    @property
    def registered_types(self) -> list[ValidationType]:
        """Return a sorted list of all registered validation types."""
        return sorted(self._validators.keys(), key=lambda t: t.value)
