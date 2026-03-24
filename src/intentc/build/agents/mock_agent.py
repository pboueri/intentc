"""Mock agent for testing intentc."""

from __future__ import annotations

from dataclasses import dataclass, field

from intentc.build.agents.base import Agent
from intentc.build.agents.models import (
    BuildContext,
    BuildResponse,
    DifferencingContext,
    DifferencingResponse,
    LogFn,
    ValidationResponse,
)
from intentc.core.models import Validation


@dataclass
class MockCall:
    """Records a single call to the mock agent."""

    method: str
    ctx: BuildContext | DifferencingContext | None = None
    validation: Validation | None = None


class MockAgent(Agent):
    """Test double that records calls and returns configurable responses."""

    def __init__(
        self,
        name: str = "mock",
        build_response: BuildResponse | None = None,
        validation_response: ValidationResponse | None = None,
        differencing_response: DifferencingResponse | None = None,
        log: LogFn | None = None,
    ) -> None:
        self._name = name
        self._log = log
        self.calls: list[MockCall] = []
        self.build_response = build_response or BuildResponse(
            status="success",
            summary="Mock build completed",
        )
        self.validation_response = validation_response or ValidationResponse(
            name="mock-validation",
            status="pass",
            reason="Mock validation passed",
        )
        self.differencing_response = differencing_response or DifferencingResponse(
            status="equivalent",
            summary="Mock differencing completed",
        )

    def build(self, ctx: BuildContext) -> BuildResponse:
        self.calls.append(MockCall(method="build", ctx=ctx))
        return self.build_response

    def validate(self, ctx: BuildContext, validation: Validation) -> ValidationResponse:
        self.calls.append(MockCall(method="validate", ctx=ctx, validation=validation))
        return self.validation_response

    def difference(self, ctx: DifferencingContext) -> DifferencingResponse:
        self.calls.append(MockCall(method="difference", ctx=ctx))
        return self.differencing_response

    def plan(self, ctx: BuildContext) -> None:
        self.calls.append(MockCall(method="plan", ctx=ctx))

    def get_name(self) -> str:
        return self._name

    def get_type(self) -> str:
        return "mock"
