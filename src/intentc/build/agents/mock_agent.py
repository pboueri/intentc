from __future__ import annotations

from intentc.core.types import Validation

from intentc.build.agents.types import (
    BuildContext,
    BuildResponse,
    DifferencingContext,
    DifferencingResponse,
    DimensionResult,
    ValidationResponse,
)


class MockAgent:
    """Mock agent for testing. Records all calls and returns configurable responses."""

    def __init__(
        self,
        name: str = "mock",
        build_response: BuildResponse | None = None,
        validation_response: ValidationResponse | None = None,
        differencing_response: DifferencingResponse | None = None,
    ) -> None:
        self._name = name
        self._build_response = build_response or BuildResponse(
            status="success", summary="mock build"
        )
        self._validation_response = validation_response or ValidationResponse(
            name="mock", status="pass", reason="mock validation"
        )
        self._differencing_response = differencing_response or DifferencingResponse(
            status="equivalent", summary="mock differencing"
        )
        self.build_calls: list[BuildContext] = []
        self.validate_calls: list[tuple[BuildContext, Validation]] = []
        self.difference_calls: list[DifferencingContext] = []
        self.plan_calls: list[BuildContext] = []

    def get_name(self) -> str:
        return self._name

    def get_type(self) -> str:
        return "mock"

    def build(self, ctx: BuildContext) -> BuildResponse:
        self.build_calls.append(ctx)
        return self._build_response

    def validate(self, ctx: BuildContext, validation: Validation) -> ValidationResponse:
        self.validate_calls.append((ctx, validation))
        return self._validation_response

    def difference(self, ctx: DifferencingContext) -> DifferencingResponse:
        self.difference_calls.append(ctx)
        return self._differencing_response

    def plan(self, ctx: BuildContext) -> None:
        self.plan_calls.append(ctx)
