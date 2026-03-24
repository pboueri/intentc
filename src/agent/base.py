"""Agent abstract base class (standalone copy)."""

from __future__ import annotations

import abc

from agent.models import (
    BuildContext,
    BuildResponse,
    DifferencingContext,
    DifferencingResponse,
    Validation,
    ValidationResponse,
)


class Agent(abc.ABC):
    """Interface for all intentc agents."""

    @abc.abstractmethod
    def build(self, ctx: BuildContext) -> BuildResponse: ...

    @abc.abstractmethod
    def validate(self, ctx: BuildContext, validation: Validation) -> ValidationResponse: ...

    @abc.abstractmethod
    def difference(self, ctx: DifferencingContext) -> DifferencingResponse: ...

    @abc.abstractmethod
    def plan(self, ctx: BuildContext) -> None: ...

    @abc.abstractmethod
    def get_name(self) -> str: ...

    @abc.abstractmethod
    def get_type(self) -> str: ...
