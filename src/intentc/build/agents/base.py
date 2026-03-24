"""Agent abstract base class."""

from __future__ import annotations

import abc

from intentc.build.agents.models import (
    BuildContext,
    BuildResponse,
    DifferencingContext,
    DifferencingResponse,
    ValidationResponse,
)
from intentc.core.models import Validation


class Agent(abc.ABC):
    """Interface for all intentc agents."""

    @abc.abstractmethod
    def build(self, ctx: BuildContext) -> BuildResponse:
        """Run a build invocation."""

    @abc.abstractmethod
    def validate(self, ctx: BuildContext, validation: Validation) -> ValidationResponse:
        """Run a single validation invocation."""

    @abc.abstractmethod
    def difference(self, ctx: DifferencingContext) -> DifferencingResponse:
        """Run a differencing evaluation."""

    @abc.abstractmethod
    def plan(self, ctx: BuildContext) -> None:
        """Enter planning mode (interactive or single-shot)."""

    @abc.abstractmethod
    def get_name(self) -> str:
        """Return the agent's display name."""

    @abc.abstractmethod
    def get_type(self) -> str:
        """Return the agent's provider type."""
