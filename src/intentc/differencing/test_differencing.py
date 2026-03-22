from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from intentc.build.agents.mock_agent import MockAgent
from intentc.build.agents.types import (
    AgentError,
    AgentProfile,
    DifferencingContext,
    DifferencingResponse,
    DimensionResult,
)
from intentc.build.agents.prompts import render_differencing_prompt
from intentc.core.project import Project
from intentc.core.types import Implementation, ProjectIntent
from intentc.differencing.workflow import run_differencing


def _make_project(tmp_path: Path) -> Project:
    """Create a minimal Project for testing."""
    return Project(
        project_intent=ProjectIntent(name="test-project", body="Test project body"),
        implementations={"default": Implementation(name="default", body="Test impl body")},
    )


def _make_profile() -> AgentProfile:
    return AgentProfile(name="mock", provider="claude")


class TestRunDifferencing:
    """Tests for the run_differencing workflow function."""

    def test_equivalent_response(self, tmp_path):
        """A valid JSON response with status 'equivalent' and all dimensions passing."""
        project = _make_project(tmp_path)
        dir_a = str(tmp_path / "a")
        dir_b = str(tmp_path / "b")
        Path(dir_a).mkdir()
        Path(dir_b).mkdir()

        response_data = {
            "status": "equivalent",
            "dimensions": [
                {"name": "public_api", "status": "pass", "rationale": "Same interfaces"},
                {"name": "test_suite", "status": "pass", "rationale": "All tests pass"},
                {"name": "runtime_behavior", "status": "pass", "rationale": "Same behavior"},
            ],
            "summary": "Builds are equivalent",
        }

        mock_agent = MockAgent(
            differencing_response=DifferencingResponse(**response_data),
        )

        def mock_create(profile):
            return mock_agent

        with patch("intentc.differencing.workflow.create_from_profile", side_effect=mock_create):
            # Write response file after agent.difference is called
            original_difference = mock_agent.difference

            def write_and_return(ctx):
                result = original_difference(ctx)
                Path(ctx.response_file_path).write_text(json.dumps(response_data))
                return result

            mock_agent.difference = write_and_return

            result = run_differencing(
                dir_a=dir_a,
                dir_b=dir_b,
                project=project,
                agent_profile=_make_profile(),
            )

        assert result.status == "equivalent"
        assert len(result.dimensions) == 3
        assert all(d.status == "pass" for d in result.dimensions)
        assert result.summary == "Builds are equivalent"

    def test_divergent_response(self, tmp_path):
        """A valid JSON response with a failing dimension results in 'divergent'."""
        project = _make_project(tmp_path)
        dir_a = str(tmp_path / "a")
        dir_b = str(tmp_path / "b")
        Path(dir_a).mkdir()
        Path(dir_b).mkdir()

        response_data = {
            "status": "divergent",
            "dimensions": [
                {"name": "public_api", "status": "pass", "rationale": "Same interfaces"},
                {"name": "test_suite", "status": "fail", "rationale": "Tests fail in B"},
            ],
            "summary": "Builds diverge on test suite",
        }

        mock_agent = MockAgent()

        def mock_create(profile):
            return mock_agent

        with patch("intentc.differencing.workflow.create_from_profile", side_effect=mock_create):
            original_difference = mock_agent.difference

            def write_and_return(ctx):
                result = original_difference(ctx)
                Path(ctx.response_file_path).write_text(json.dumps(response_data))
                return result

            mock_agent.difference = write_and_return

            result = run_differencing(
                dir_a=dir_a,
                dir_b=dir_b,
                project=project,
                agent_profile=_make_profile(),
            )

        assert result.status == "divergent"
        assert any(d.status == "fail" for d in result.dimensions)

    def test_missing_response_file(self, tmp_path):
        """A missing response file produces a descriptive error."""
        project = _make_project(tmp_path)
        dir_a = str(tmp_path / "a")
        dir_b = str(tmp_path / "b")
        Path(dir_a).mkdir()
        Path(dir_b).mkdir()

        mock_agent = MockAgent()

        def mock_create(profile):
            return mock_agent

        with patch("intentc.differencing.workflow.create_from_profile", side_effect=mock_create):
            original_difference = mock_agent.difference

            def delete_and_return(ctx):
                result = original_difference(ctx)
                # Remove the temp file to simulate missing response
                Path(ctx.response_file_path).unlink(missing_ok=True)
                return result

            mock_agent.difference = delete_and_return

            with pytest.raises(AgentError, match="Differencing response file not found"):
                run_differencing(
                    dir_a=dir_a,
                    dir_b=dir_b,
                    project=project,
                    agent_profile=_make_profile(),
                )

    def test_empty_response_file(self, tmp_path):
        """An empty response file produces a descriptive error."""
        project = _make_project(tmp_path)
        dir_a = str(tmp_path / "a")
        dir_b = str(tmp_path / "b")
        Path(dir_a).mkdir()
        Path(dir_b).mkdir()

        mock_agent = MockAgent()

        def mock_create(profile):
            return mock_agent

        with patch("intentc.differencing.workflow.create_from_profile", side_effect=mock_create):
            original_difference = mock_agent.difference

            def write_empty_and_return(ctx):
                result = original_difference(ctx)
                Path(ctx.response_file_path).write_text("")
                return result

            mock_agent.difference = write_empty_and_return

            with pytest.raises(AgentError, match="Differencing response file is empty"):
                run_differencing(
                    dir_a=dir_a,
                    dir_b=dir_b,
                    project=project,
                    agent_profile=_make_profile(),
                )

    def test_malformed_json_response(self, tmp_path):
        """Malformed JSON in the response file produces a descriptive error."""
        project = _make_project(tmp_path)
        dir_a = str(tmp_path / "a")
        dir_b = str(tmp_path / "b")
        Path(dir_a).mkdir()
        Path(dir_b).mkdir()

        mock_agent = MockAgent()

        def mock_create(profile):
            return mock_agent

        with patch("intentc.differencing.workflow.create_from_profile", side_effect=mock_create):
            original_difference = mock_agent.difference

            def write_bad_json(ctx):
                result = original_difference(ctx)
                Path(ctx.response_file_path).write_text("{not valid json")
                return result

            mock_agent.difference = write_bad_json

            with pytest.raises(AgentError, match="Malformed JSON in differencing response file"):
                run_differencing(
                    dir_a=dir_a,
                    dir_b=dir_b,
                    project=project,
                    agent_profile=_make_profile(),
                )

    def test_unknown_fields_tolerated(self, tmp_path):
        """Unknown fields in the response are tolerated (forward compatibility)."""
        project = _make_project(tmp_path)
        dir_a = str(tmp_path / "a")
        dir_b = str(tmp_path / "b")
        Path(dir_a).mkdir()
        Path(dir_b).mkdir()

        response_data = {
            "status": "equivalent",
            "dimensions": [
                {"name": "public_api", "status": "pass", "rationale": "ok", "extra_field": True},
            ],
            "summary": "All good",
            "future_field": "should be ignored",
        }

        mock_agent = MockAgent()

        def mock_create(profile):
            return mock_agent

        with patch("intentc.differencing.workflow.create_from_profile", side_effect=mock_create):
            original_difference = mock_agent.difference

            def write_and_return(ctx):
                result = original_difference(ctx)
                Path(ctx.response_file_path).write_text(json.dumps(response_data))
                return result

            mock_agent.difference = write_and_return

            result = run_differencing(
                dir_a=dir_a,
                dir_b=dir_b,
                project=project,
                agent_profile=_make_profile(),
            )

        assert result.status == "equivalent"

    def test_does_not_modify_state(self, tmp_path):
        """Differencing is a pure evaluation — no state changes."""
        project = _make_project(tmp_path)
        dir_a = str(tmp_path / "a")
        dir_b = str(tmp_path / "b")
        Path(dir_a).mkdir()
        Path(dir_b).mkdir()

        response_data = {
            "status": "equivalent",
            "dimensions": [],
            "summary": "ok",
        }

        mock_agent = MockAgent()

        def mock_create(profile):
            return mock_agent

        with patch("intentc.differencing.workflow.create_from_profile", side_effect=mock_create):
            original_difference = mock_agent.difference

            def write_and_return(ctx):
                result = original_difference(ctx)
                Path(ctx.response_file_path).write_text(json.dumps(response_data))
                return result

            mock_agent.difference = write_and_return

            run_differencing(
                dir_a=dir_a,
                dir_b=dir_b,
                project=project,
                agent_profile=_make_profile(),
            )

        # Verify agent.difference was called with correct context
        assert len(mock_agent.difference_calls) == 1
        ctx = mock_agent.difference_calls[0]
        assert ctx.output_dir_a == dir_a
        assert ctx.output_dir_b == dir_b

    def test_resolves_implementation(self, tmp_path):
        """Implementation is resolved from the project when not specified."""
        project = _make_project(tmp_path)
        dir_a = str(tmp_path / "a")
        dir_b = str(tmp_path / "b")
        Path(dir_a).mkdir()
        Path(dir_b).mkdir()

        response_data = {
            "status": "equivalent",
            "dimensions": [],
            "summary": "ok",
        }

        mock_agent = MockAgent()

        def mock_create(profile):
            return mock_agent

        with patch("intentc.differencing.workflow.create_from_profile", side_effect=mock_create):
            original_difference = mock_agent.difference

            def write_and_return(ctx):
                result = original_difference(ctx)
                Path(ctx.response_file_path).write_text(json.dumps(response_data))
                return result

            mock_agent.difference = write_and_return

            result = run_differencing(
                dir_a=dir_a,
                dir_b=dir_b,
                project=project,
                agent_profile=_make_profile(),
            )

        ctx = mock_agent.difference_calls[0]
        assert ctx.implementation is not None
        assert ctx.implementation.name == "default"


class TestDifferencingPromptRendering:
    """Tests for the differencing prompt template rendering."""

    def test_render_differencing_prompt_substitutes_variables(self):
        """The rendering function substitutes all template variables from DifferencingContext."""
        ctx = DifferencingContext(
            output_dir_a="/path/to/a",
            output_dir_b="/path/to/b",
            project_intent=ProjectIntent(name="myproject", body="My project description"),
            implementation=Implementation(name="python", body="Python implementation"),
            response_file_path="/tmp/response.json",
        )

        template = (
            "Project: {project}\n"
            "Implementation: {implementation}\n"
            "Dir A: {output_dir_a}\n"
            "Dir B: {output_dir_b}\n"
            "Response: {response_file}"
        )

        result = render_differencing_prompt(template, ctx)

        assert "My project description" in result
        assert "Python implementation" in result
        assert "/path/to/a" in result
        assert "/path/to/b" in result
        assert "/tmp/response.json" in result
        # No raw template placeholders remain
        assert "{project}" not in result
        assert "{implementation}" not in result
        assert "{output_dir_a}" not in result
        assert "{output_dir_b}" not in result
        assert "{response_file}" not in result

    def test_render_with_no_implementation(self):
        """Rendering works when implementation is None."""
        ctx = DifferencingContext(
            output_dir_a="/a",
            output_dir_b="/b",
            project_intent=ProjectIntent(name="p", body="proj"),
            response_file_path="/resp.json",
        )

        template = "Impl: {implementation}, A: {output_dir_a}"
        result = render_differencing_prompt(template, ctx)

        assert "Impl: " in result
        assert "/a" in result

    def test_prompt_loads_from_intent_dir(self, tmp_path):
        """The prompt loading mechanism resolves paths relative to cwd/intent/."""
        from intentc.build.agents.prompts import load_default_prompts

        diff_dir = tmp_path / "intent" / "differencing" / "prompts"
        diff_dir.mkdir(parents=True)
        (diff_dir / "difference.prompt").write_text(
            "Compare {output_dir_a} vs {output_dir_b}"
        )

        original = Path.cwd
        try:
            Path.cwd = staticmethod(lambda: tmp_path)
            templates = load_default_prompts()
            assert templates.difference == "Compare {output_dir_a} vs {output_dir_b}"
        finally:
            Path.cwd = original


class TestDifferencingResponseParsing:
    """Tests for DifferencingResponse parsing correctness."""

    def test_parse_equivalent_all_pass(self):
        data = {
            "status": "equivalent",
            "dimensions": [
                {"name": "public_api", "status": "pass", "rationale": "Same API"},
                {"name": "test_suite", "status": "pass", "rationale": "All pass"},
                {"name": "runtime_behavior", "status": "pass", "rationale": "Same"},
            ],
            "summary": "Functionally equivalent",
        }
        resp = DifferencingResponse(**data)
        assert resp.status == "equivalent"
        assert len(resp.dimensions) == 3
        assert all(d.status == "pass" for d in resp.dimensions)

    def test_parse_divergent_with_fail(self):
        data = {
            "status": "divergent",
            "dimensions": [
                {"name": "public_api", "status": "pass", "rationale": "ok"},
                {"name": "test_suite", "status": "fail", "rationale": "Tests fail"},
            ],
            "summary": "Test suite diverges",
        }
        resp = DifferencingResponse(**data)
        assert resp.status == "divergent"
        assert any(d.status == "fail" for d in resp.dimensions)

    def test_json_serialization_roundtrip(self):
        resp = DifferencingResponse(
            status="equivalent",
            dimensions=[
                DimensionResult(name="public_api", status="pass", rationale="ok"),
            ],
            summary="all good",
        )
        data = json.loads(resp.model_dump_json())
        restored = DifferencingResponse(**data)
        assert restored.status == resp.status
        assert len(restored.dimensions) == 1
        assert restored.summary == resp.summary
