"""MockAgent for testing - records calls and returns configurable responses."""

from __future__ import annotations

from core.types import Validation

from .base import BuildContext


class MockAgent:
    """A test-double agent that records invocations and returns canned responses.

    Attributes:
        build_calls: List of :class:`BuildContext` objects passed to :meth:`build`.
        build_files: The file list that :meth:`build` will return.
        build_error: If set, :meth:`build` will raise this exception.
        validate_calls: List of ``(validation, generated_files)`` tuples.
        validate_result: The ``(passed, explanation)`` tuple returned by
            :meth:`validate_with_llm`.
    """

    def __init__(self) -> None:
        self.build_calls: list[BuildContext] = []
        self.build_files: list[str] = []
        self.build_error: Exception | None = None
        self.validate_calls: list[tuple[Validation, list[str]]] = []
        self.validate_result: tuple[bool, str] = (True, "mock pass")

    def build(self, build_ctx: BuildContext) -> list[str]:
        self.build_calls.append(build_ctx)
        if self.build_error:
            raise self.build_error
        return list(self.build_files)

    def validate_with_llm(
        self, validation: Validation, generated_files: list[str]
    ) -> tuple[bool, str]:
        self.validate_calls.append((validation, generated_files))
        return self.validate_result

    def get_name(self) -> str:
        return "mock"

    def get_type(self) -> str:
        return "mock"
