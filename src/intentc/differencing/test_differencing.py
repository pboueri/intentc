"""Tests for the differencing workflow."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from intentc.build.agents import (
    AgentError,
    AgentProfile,
    DifferencingContext,
    DifferencingResponse,
    DimensionResult,
    MockAgent,
    render_differencing_prompt,
)
from intentc.core.project import Project
from intentc.core.types import Implementation, ProjectIntent
from intentc.differencing.differencing import run_differencing


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project_intent() -> ProjectIntent:
    return ProjectIntent(name="test-project", body="A test project intent")


@pytest.fixture
def implementation() -> Implementation:
    return Implementation(name="default", body="Python 3.11+ implementation")


@pytest.fixture
def project(project_intent: ProjectIntent, implementation: Implementation) -> Project:
    return Project(
        project_intent=project_intent,
        implementations={"default": implementation},
        assertions=[],
        features={},
    )


@pytest.fixture
def mock_profile() -> AgentProfile:
    return AgentProfile(name="mock", provider="mock")


@pytest.fixture
def equivalent_response() -> DifferencingResponse:
    return DifferencingResponse(
        status="equivalent",
        dimensions=[
            DimensionResult(name="public_api", status="pass", rationale="APIs match"),
            DimensionResult(name="test_suite", status="pass", rationale="Tests pass"),
            DimensionResult(name="runtime_behavior", status="pass", rationale="Behavior matches"),
        ],
        summary="Builds are functionally equivalent",
    )


@pytest.fixture
def divergent_response() -> DifferencingResponse:
    return DifferencingResponse(
        status="divergent",
        dimensions=[
            DimensionResult(name="public_api", status="pass", rationale="APIs match"),
            DimensionResult(name="test_suite", status="fail", rationale="3 tests fail on build B"),
        ],
        summary="Builds diverge on test suite",
    )


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


class TestResponseParsing:
    """Test DifferencingResponse parsing from JSON."""

    def test_equivalent_response_parsed(self) -> None:
        """Valid JSON with status 'equivalent' and all dimensions passing."""
        data = {
            "status": "equivalent",
            "dimensions": [
                {"name": "public_api", "status": "pass", "rationale": "Same exports"},
                {"name": "runtime_behavior", "status": "pass", "rationale": "Same output"},
            ],
            "summary": "Equivalent builds",
        }
        resp = DifferencingResponse(**data)
        assert resp.status == "equivalent"
        assert len(resp.dimensions) == 2
        assert all(d.status == "pass" for d in resp.dimensions)

    def test_divergent_response_parsed(self) -> None:
        """Any dimension with status 'fail' results in 'divergent'."""
        data = {
            "status": "divergent",
            "dimensions": [
                {"name": "public_api", "status": "pass", "rationale": "OK"},
                {"name": "test_suite", "status": "fail", "rationale": "Tests fail"},
            ],
            "summary": "Divergent",
        }
        resp = DifferencingResponse(**data)
        assert resp.status == "divergent"
        assert resp.dimensions[1].status == "fail"

    def test_unknown_fields_tolerated(self) -> None:
        """Unknown fields in the response are ignored (forward compatibility)."""
        data = {
            "status": "equivalent",
            "dimensions": [
                {
                    "name": "public_api",
                    "status": "pass",
                    "rationale": "OK",
                    "extra_field": "ignored",
                },
            ],
            "summary": "OK",
            "future_field": "also ignored",
        }
        resp = DifferencingResponse(**data)
        assert resp.status == "equivalent"
        assert len(resp.dimensions) == 1


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------


class TestRunDifferencing:
    """Test the run_differencing workflow function."""

    def test_returns_equivalent_response(
        self,
        tmp_path: Path,
        project: Project,
        mock_profile: AgentProfile,
        equivalent_response: DifferencingResponse,
    ) -> None:
        """Workflow returns parsed DifferencingResponse on success."""
        dir_a = str(tmp_path / "a")
        dir_b = str(tmp_path / "b")

        mock_agent = MockAgent(differencing_response=equivalent_response)

        def _write_response(ctx: DifferencingContext) -> DifferencingResponse:
            # Simulate agent writing response file
            Path(ctx.response_file_path).write_text(
                equivalent_response.model_dump_json(), encoding="utf-8"
            )
            mock_agent.difference_calls.append(ctx)
            return equivalent_response

        mock_agent.difference = _write_response  # type: ignore[assignment]

        with patch("intentc.differencing.differencing.create_from_profile", return_value=mock_agent):
            result = run_differencing(
                dir_a=dir_a,
                dir_b=dir_b,
                project=project,
                agent_profile=mock_profile,
            )

        assert result.status == "equivalent"
        assert len(result.dimensions) == 3

    def test_returns_divergent_response(
        self,
        tmp_path: Path,
        project: Project,
        mock_profile: AgentProfile,
        divergent_response: DifferencingResponse,
    ) -> None:
        """Workflow returns divergent response when a dimension fails."""
        dir_a = str(tmp_path / "a")
        dir_b = str(tmp_path / "b")

        mock_agent = MockAgent(differencing_response=divergent_response)

        def _write_response(ctx: DifferencingContext) -> DifferencingResponse:
            Path(ctx.response_file_path).write_text(
                divergent_response.model_dump_json(), encoding="utf-8"
            )
            mock_agent.difference_calls.append(ctx)
            return divergent_response

        mock_agent.difference = _write_response  # type: ignore[assignment]

        with patch("intentc.differencing.differencing.create_from_profile", return_value=mock_agent):
            result = run_differencing(
                dir_a=dir_a,
                dir_b=dir_b,
                project=project,
                agent_profile=mock_profile,
            )

        assert result.status == "divergent"

    def test_missing_response_file_raises(
        self,
        tmp_path: Path,
        project: Project,
        mock_profile: AgentProfile,
    ) -> None:
        """Missing response file produces a descriptive AgentError."""
        mock_agent = MockAgent()

        def _no_write(ctx: DifferencingContext) -> DifferencingResponse:
            # Delete the temp file to simulate missing response
            Path(ctx.response_file_path).unlink(missing_ok=True)
            return mock_agent._differencing_response

        mock_agent.difference = _no_write  # type: ignore[assignment]

        with patch("intentc.differencing.differencing.create_from_profile", return_value=mock_agent):
            with pytest.raises(AgentError, match="Differencing response file not found"):
                run_differencing(
                    dir_a=str(tmp_path / "a"),
                    dir_b=str(tmp_path / "b"),
                    project=project,
                    agent_profile=mock_profile,
                )

    def test_empty_response_file_raises(
        self,
        tmp_path: Path,
        project: Project,
        mock_profile: AgentProfile,
    ) -> None:
        """Empty response file produces a descriptive AgentError."""
        mock_agent = MockAgent()

        def _write_empty(ctx: DifferencingContext) -> DifferencingResponse:
            Path(ctx.response_file_path).write_text("", encoding="utf-8")
            return mock_agent._differencing_response

        mock_agent.difference = _write_empty  # type: ignore[assignment]

        with patch("intentc.differencing.differencing.create_from_profile", return_value=mock_agent):
            with pytest.raises(AgentError, match="Differencing response file is empty"):
                run_differencing(
                    dir_a=str(tmp_path / "a"),
                    dir_b=str(tmp_path / "b"),
                    project=project,
                    agent_profile=mock_profile,
                )

    def test_malformed_json_raises(
        self,
        tmp_path: Path,
        project: Project,
        mock_profile: AgentProfile,
    ) -> None:
        """Malformed JSON in response file produces a descriptive AgentError."""
        mock_agent = MockAgent()

        def _write_bad_json(ctx: DifferencingContext) -> DifferencingResponse:
            Path(ctx.response_file_path).write_text("{bad json", encoding="utf-8")
            return mock_agent._differencing_response

        mock_agent.difference = _write_bad_json  # type: ignore[assignment]

        with patch("intentc.differencing.differencing.create_from_profile", return_value=mock_agent):
            with pytest.raises(AgentError, match="Malformed JSON in differencing response file"):
                run_differencing(
                    dir_a=str(tmp_path / "a"),
                    dir_b=str(tmp_path / "b"),
                    project=project,
                    agent_profile=mock_profile,
                )

    def test_does_not_modify_build_state(
        self,
        tmp_path: Path,
        project: Project,
        mock_profile: AgentProfile,
        equivalent_response: DifferencingResponse,
    ) -> None:
        """Differencing is a pure evaluation — no build state is modified."""
        mock_agent = MockAgent(differencing_response=equivalent_response)

        def _write_response(ctx: DifferencingContext) -> DifferencingResponse:
            Path(ctx.response_file_path).write_text(
                equivalent_response.model_dump_json(), encoding="utf-8"
            )
            return equivalent_response

        mock_agent.difference = _write_response  # type: ignore[assignment]

        with patch("intentc.differencing.differencing.create_from_profile", return_value=mock_agent):
            result = run_differencing(
                dir_a=str(tmp_path / "a"),
                dir_b=str(tmp_path / "b"),
                project=project,
                agent_profile=mock_profile,
            )

        # No build calls, no validate calls — only differencing
        assert len(mock_agent.build_calls) == 0
        assert len(mock_agent.validate_calls) == 0
        assert result.status == "equivalent"

    def test_passes_implementation_to_context(
        self,
        tmp_path: Path,
        project: Project,
        mock_profile: AgentProfile,
        equivalent_response: DifferencingResponse,
        implementation: Implementation,
    ) -> None:
        """Implementation is passed through to the DifferencingContext."""
        captured_ctx: list[DifferencingContext] = []

        mock_agent = MockAgent(differencing_response=equivalent_response)

        def _capture(ctx: DifferencingContext) -> DifferencingResponse:
            captured_ctx.append(ctx)
            Path(ctx.response_file_path).write_text(
                equivalent_response.model_dump_json(), encoding="utf-8"
            )
            return equivalent_response

        mock_agent.difference = _capture  # type: ignore[assignment]

        with patch("intentc.differencing.differencing.create_from_profile", return_value=mock_agent):
            run_differencing(
                dir_a=str(tmp_path / "a"),
                dir_b=str(tmp_path / "b"),
                project=project,
                agent_profile=mock_profile,
                implementation=implementation,
            )

        assert len(captured_ctx) == 1
        assert captured_ctx[0].implementation is not None
        assert captured_ctx[0].implementation.name == "default"
        assert captured_ctx[0].output_dir_a == str(tmp_path / "a")
        assert captured_ctx[0].output_dir_b == str(tmp_path / "b")


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------


class TestPromptRendering:
    """Test that the differencing prompt template rendering works."""

    def test_render_differencing_prompt_substitutes_variables(self) -> None:
        """Template variables are substituted with actual values."""
        template = (
            "Project: {project}\n"
            "Implementation: {implementation}\n"
            "Dir A: {output_dir_a}\n"
            "Dir B: {output_dir_b}\n"
            "Response: {response_file}\n"
        )
        ctx = DifferencingContext(
            output_dir_a="/path/to/a",
            output_dir_b="/path/to/b",
            project_intent=ProjectIntent(name="proj", body="My project"),
            implementation=Implementation(name="impl", body="Python impl"),
            response_file_path="/tmp/response.json",
        )
        rendered = render_differencing_prompt(template, ctx)

        assert "My project" in rendered
        assert "Python impl" in rendered
        assert "/path/to/a" in rendered
        assert "/path/to/b" in rendered
        assert "/tmp/response.json" in rendered
        # No raw template placeholders remain
        assert "{project}" not in rendered
        assert "{implementation}" not in rendered
        assert "{output_dir_a}" not in rendered
        assert "{output_dir_b}" not in rendered
        assert "{response_file}" not in rendered

    def test_render_with_no_implementation(self) -> None:
        """When implementation is None, the placeholder is replaced with empty string."""
        template = "Impl: {implementation}"
        ctx = DifferencingContext(
            output_dir_a="/a",
            output_dir_b="/b",
            project_intent=ProjectIntent(name="proj", body="P"),
            implementation=None,
            response_file_path="/tmp/r.json",
        )
        rendered = render_differencing_prompt(template, ctx)
        assert rendered == "Impl: "

    def test_prompt_contains_actual_values(self) -> None:
        """Rendered prompt contains the actual values, not raw placeholders."""
        template = "Compare {output_dir_a} vs {output_dir_b} for {project}"
        ctx = DifferencingContext(
            output_dir_a="/builds/v1",
            output_dir_b="/builds/v2",
            project_intent=ProjectIntent(name="p", body="My Cool App"),
            response_file_path="/tmp/resp.json",
        )
        rendered = render_differencing_prompt(template, ctx)
        assert "Compare /builds/v1 vs /builds/v2 for My Cool App" == rendered
