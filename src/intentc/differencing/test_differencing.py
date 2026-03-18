"""Tests for differencing workflow and response parsing."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from intentc.build.agents import (
    AgentProfile,
    DifferencingContext,
    DifferencingResponse,
    DimensionResult,
    MockAgent,
)
from intentc.core.project import Project
from intentc.core.types import ProjectIntent, Implementation
from intentc.differencing.differencing import run_differencing


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(**overrides) -> Project:
    """Create a minimal Project for testing."""
    defaults = dict(
        project_intent=ProjectIntent(name="test-project", body="# Test Project"),
        implementation=Implementation(name="impl", body="# Impl\nPython 3.11"),
    )
    defaults.update(overrides)
    return Project(**defaults)


def _make_profile(**overrides) -> AgentProfile:
    """Create a minimal AgentProfile for testing."""
    defaults = dict(
        name="test-agent",
        provider="cli",
        command="echo",
    )
    defaults.update(overrides)
    return AgentProfile(**defaults)


# ---------------------------------------------------------------------------
# DifferencingResponse parsing
# ---------------------------------------------------------------------------


class TestDifferencingResponseParsing:
    """Verify DifferencingResponse parsing works correctly."""

    def test_valid_equivalent_response(self):
        """A valid JSON response with status 'equivalent' and all dimensions passing."""
        data = {
            "status": "equivalent",
            "dimensions": [
                {"name": "public_api", "status": "pass", "rationale": "Same API surface"},
                {"name": "test_suite", "status": "pass", "rationale": "All tests cross-pass"},
                {"name": "runtime_behavior", "status": "pass", "rationale": "Same outputs"},
                {"name": "dependency_compatibility", "status": "pass", "rationale": "Same deps"},
                {"name": "configuration_compatibility", "status": "pass", "rationale": "Same config"},
            ],
            "summary": "Builds are functionally equivalent",
        }
        resp = DifferencingResponse(**data)
        assert resp.status == "equivalent"
        assert len(resp.dimensions) == 5
        assert all(d.status == "pass" for d in resp.dimensions)

    def test_divergent_when_any_dimension_fails(self):
        """A valid JSON response with any dimension having status 'fail' results in divergent."""
        data = {
            "status": "divergent",
            "dimensions": [
                {"name": "public_api", "status": "pass", "rationale": "Same API surface"},
                {"name": "test_suite", "status": "fail", "rationale": "3 tests fail in cross-run"},
                {"name": "runtime_behavior", "status": "pass", "rationale": "Same outputs"},
            ],
            "summary": "Test suite divergence detected",
        }
        resp = DifferencingResponse(**data)
        assert resp.status == "divergent"
        assert any(d.status == "fail" for d in resp.dimensions)

    def test_missing_response_file_error(self, tmp_path: Path):
        """A missing response file produces a descriptive error."""
        missing_path = str(tmp_path / "nonexistent.json")
        project = _make_project()
        profile = _make_profile()

        mock_agent = MockAgent()
        # Mock agent won't write a response file — simulate missing file
        with patch("intentc.differencing.differencing.create_from_profile", return_value=mock_agent):
            with pytest.raises(Exception, match="response file"):
                run_differencing(
                    str(tmp_path / "a"),
                    str(tmp_path / "b"),
                    project,
                    profile,
                )

    def test_malformed_json_error(self, tmp_path: Path):
        """A malformed JSON response file produces a descriptive error."""
        project = _make_project()
        profile = _make_profile()

        mock_agent = MockAgent()

        def write_bad_json(ctx: DifferencingContext) -> DifferencingResponse:
            mock_agent.difference_calls.append(ctx)
            Path(ctx.response_file_path).write_text("NOT VALID JSON {{{")
            return mock_agent._differencing_response

        mock_agent.difference = write_bad_json  # type: ignore[assignment]

        with patch("intentc.differencing.differencing.create_from_profile", return_value=mock_agent):
            with pytest.raises(Exception, match="Malformed JSON"):
                run_differencing(
                    str(tmp_path / "a"),
                    str(tmp_path / "b"),
                    project,
                    profile,
                )

    def test_unknown_fields_tolerated(self):
        """Unknown fields in the response are tolerated (forward compatibility)."""
        data = {
            "status": "equivalent",
            "dimensions": [
                {
                    "name": "public_api",
                    "status": "pass",
                    "rationale": "Same API",
                    "future_field": "should be ignored",
                },
            ],
            "summary": "All good",
            "extra_top_level": "also ignored",
        }
        resp = DifferencingResponse(**data)
        assert resp.status == "equivalent"
        assert not hasattr(resp, "extra_top_level")
        assert not hasattr(resp.dimensions[0], "future_field")


# ---------------------------------------------------------------------------
# Differencing workflow
# ---------------------------------------------------------------------------


class TestRunDifferencing:
    """Verify the differencing workflow function."""

    def test_constructs_context_and_returns_response(self, tmp_path: Path):
        """The workflow constructs a DifferencingContext, calls the agent, and returns the response."""
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()

        project = _make_project()
        profile = _make_profile()

        equivalent_response = DifferencingResponse(
            status="equivalent",
            dimensions=[
                DimensionResult(name="public_api", status="pass", rationale="Same API"),
            ],
            summary="Equivalent",
        )

        mock_agent = MockAgent(differencing_response=equivalent_response)

        def mock_difference(ctx: DifferencingContext) -> DifferencingResponse:
            mock_agent.difference_calls.append(ctx)
            # Write the response file as the real agent would
            Path(ctx.response_file_path).write_text(
                equivalent_response.model_dump_json()
            )
            return equivalent_response

        mock_agent.difference = mock_difference  # type: ignore[assignment]

        with patch("intentc.differencing.differencing.create_from_profile", return_value=mock_agent):
            resp = run_differencing(str(dir_a), str(dir_b), project, profile)

        assert resp.status == "equivalent"
        assert len(resp.dimensions) == 1
        assert resp.dimensions[0].name == "public_api"
        # Verify the context was correctly constructed
        assert len(mock_agent.difference_calls) == 1
        ctx = mock_agent.difference_calls[0]
        assert ctx.output_dir_a == str(dir_a)
        assert ctx.output_dir_b == str(dir_b)
        assert ctx.project_intent.name == "test-project"
        assert ctx.implementation is not None

    def test_does_not_modify_build_state(self, tmp_path: Path):
        """Differencing is a pure evaluation — no state files created."""
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()

        project = _make_project()
        profile = _make_profile()

        mock_agent = MockAgent()

        def mock_difference(ctx: DifferencingContext) -> DifferencingResponse:
            mock_agent.difference_calls.append(ctx)
            Path(ctx.response_file_path).write_text(
                mock_agent._differencing_response.model_dump_json()
            )
            return mock_agent._differencing_response

        mock_agent.difference = mock_difference  # type: ignore[assignment]

        with patch("intentc.differencing.differencing.create_from_profile", return_value=mock_agent):
            run_differencing(str(dir_a), str(dir_b), project, profile)

        # No .intentc state directory should be created
        assert not (tmp_path / ".intentc").exists()

    def test_empty_response_file_error(self, tmp_path: Path):
        """An empty response file produces a descriptive error."""
        project = _make_project()
        profile = _make_profile()

        mock_agent = MockAgent()

        def mock_difference(ctx: DifferencingContext) -> DifferencingResponse:
            mock_agent.difference_calls.append(ctx)
            Path(ctx.response_file_path).write_text("")
            return mock_agent._differencing_response

        mock_agent.difference = mock_difference  # type: ignore[assignment]

        with patch("intentc.differencing.differencing.create_from_profile", return_value=mock_agent):
            with pytest.raises(Exception, match="empty"):
                run_differencing(
                    str(tmp_path / "a"),
                    str(tmp_path / "b"),
                    project,
                    profile,
                )

    def test_divergent_response_returned(self, tmp_path: Path):
        """A divergent response is returned without error."""
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()

        project = _make_project()
        profile = _make_profile()

        divergent_response = DifferencingResponse(
            status="divergent",
            dimensions=[
                DimensionResult(name="runtime_behavior", status="fail", rationale="Different output"),
            ],
            summary="Not equivalent",
        )

        mock_agent = MockAgent(differencing_response=divergent_response)

        def mock_difference(ctx: DifferencingContext) -> DifferencingResponse:
            mock_agent.difference_calls.append(ctx)
            Path(ctx.response_file_path).write_text(
                divergent_response.model_dump_json()
            )
            return divergent_response

        mock_agent.difference = mock_difference  # type: ignore[assignment]

        with patch("intentc.differencing.differencing.create_from_profile", return_value=mock_agent):
            resp = run_differencing(str(dir_a), str(dir_b), project, profile)

        assert resp.status == "divergent"
        assert resp.dimensions[0].status == "fail"

    def test_passes_implementation_to_context(self, tmp_path: Path):
        """The project implementation is passed through to the DifferencingContext."""
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()

        impl = Implementation(name="custom-impl", body="# Custom implementation")
        project = _make_project(implementation=impl)
        profile = _make_profile()

        mock_agent = MockAgent()

        def mock_difference(ctx: DifferencingContext) -> DifferencingResponse:
            mock_agent.difference_calls.append(ctx)
            Path(ctx.response_file_path).write_text(
                mock_agent._differencing_response.model_dump_json()
            )
            return mock_agent._differencing_response

        mock_agent.difference = mock_difference  # type: ignore[assignment]

        with patch("intentc.differencing.differencing.create_from_profile", return_value=mock_agent):
            run_differencing(str(dir_a), str(dir_b), project, profile)

        ctx = mock_agent.difference_calls[0]
        assert ctx.implementation is not None
        assert ctx.implementation.name == "custom-impl"

    def test_none_implementation_passed(self, tmp_path: Path):
        """A project with no implementation passes None to context."""
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()

        project = _make_project(implementation=None)
        profile = _make_profile()

        mock_agent = MockAgent()

        def mock_difference(ctx: DifferencingContext) -> DifferencingResponse:
            mock_agent.difference_calls.append(ctx)
            Path(ctx.response_file_path).write_text(
                mock_agent._differencing_response.model_dump_json()
            )
            return mock_agent._differencing_response

        mock_agent.difference = mock_difference  # type: ignore[assignment]

        with patch("intentc.differencing.differencing.create_from_profile", return_value=mock_agent):
            run_differencing(str(dir_a), str(dir_b), project, profile)

        ctx = mock_agent.difference_calls[0]
        assert ctx.implementation is None
