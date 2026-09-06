"""Tests for intentc.differencing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from intentc.build.agents import (
    AgentError,
    AgentProfile,
    DifferencingResponse,
    DimensionResult,
    MockAgent,
)
from intentc.core import Implementation, Project, ProjectIntent
from intentc.differencing import run_differencing
from intentc.differencing.differencing import _read_differencing_response


def make_project(**implementations: Implementation) -> Project:
    return Project(
        project_intent=ProjectIntent(name="project", body="Build a thing."),
        implementations=implementations,
    )


def make_profile() -> AgentProfile:
    return AgentProfile(name="mock", provider="cli", command="echo")


@pytest.fixture
def mock_agent(monkeypatch) -> MockAgent:
    agent = MockAgent(name="mock")
    monkeypatch.setattr(
        "intentc.differencing.differencing.create_from_profile", lambda profile: agent
    )
    return agent


def write_response(agent: MockAgent) -> None:
    """Make the mock agent's difference() write its configured response to the response file."""

    original_difference = agent.difference

    def _difference(ctx):
        response = original_difference(ctx)
        Path(ctx.response_file_path).write_text(response.model_dump_json())
        return response

    agent.difference = _difference  # type: ignore[method-assign]


class TestRunDifferencing:
    def test_equivalent_when_all_dimensions_pass(self, tmp_path, mock_agent):
        mock_agent.differencing_response = DifferencingResponse(
            status="equivalent",
            dimensions=[
                DimensionResult(name="public_api", status="pass", rationale="same exports"),
                DimensionResult(name="runtime_behavior", status="pass", rationale="same output"),
            ],
            summary="Builds behave identically.",
        )
        write_response(mock_agent)
        project = make_project()

        response = run_differencing(str(tmp_path / "a"), str(tmp_path / "b"), project, make_profile())

        assert response.status == "equivalent"
        assert len(response.dimensions) == 2
        assert response.summary == "Builds behave identically."

    def test_divergent_when_a_dimension_fails(self, tmp_path, mock_agent):
        mock_agent.differencing_response = DifferencingResponse(
            status="divergent",
            dimensions=[
                DimensionResult(name="public_api", status="pass", rationale="same exports"),
                DimensionResult(name="runtime_behavior", status="fail", rationale="different output"),
            ],
            summary="Runtime behavior diverges.",
        )
        write_response(mock_agent)
        project = make_project()

        response = run_differencing(str(tmp_path / "a"), str(tmp_path / "b"), project, make_profile())

        assert response.status == "divergent"

    def test_constructs_context_with_both_dirs_and_project_intent(self, tmp_path, mock_agent):
        write_response(mock_agent)
        project = make_project()

        run_differencing(str(tmp_path / "a"), str(tmp_path / "b"), project, make_profile())

        assert len(mock_agent.difference_calls) == 1
        ctx = mock_agent.difference_calls[0]
        assert ctx.output_dir_a == str(tmp_path / "a")
        assert ctx.output_dir_b == str(tmp_path / "b")
        assert ctx.project_intent == project.project_intent
        assert ctx.implementation is None
        assert ctx.response_file_path

    def test_resolves_named_implementation(self, tmp_path, mock_agent):
        write_response(mock_agent)
        impl = Implementation(name="python", body="Use Python.")
        project = make_project(python=impl)

        run_differencing(
            str(tmp_path / "a"), str(tmp_path / "b"), project, make_profile(), implementation="python"
        )

        ctx = mock_agent.difference_calls[0]
        assert ctx.implementation == impl

    def test_unknown_implementation_raises_key_error(self, tmp_path, mock_agent):
        write_response(mock_agent)
        project = make_project(python=Implementation(name="python", body="Use Python."))

        with pytest.raises(KeyError):
            run_differencing(
                str(tmp_path / "a"), str(tmp_path / "b"), project, make_profile(), implementation="go"
            )

    def test_ambiguous_implementation_raises_value_error(self, tmp_path, mock_agent):
        write_response(mock_agent)
        project = make_project(
            python=Implementation(name="python", body="Use Python."),
            go=Implementation(name="go", body="Use Go."),
        )

        with pytest.raises(ValueError):
            run_differencing(str(tmp_path / "a"), str(tmp_path / "b"), project, make_profile())

    def test_missing_response_file_raises_descriptive_agent_error(self, tmp_path, mock_agent):
        # mock_agent.difference() is never made to write a response file.
        project = make_project()

        with pytest.raises(AgentError, match="not found"):
            run_differencing(str(tmp_path / "a"), str(tmp_path / "b"), project, make_profile())

    def test_does_not_modify_build_state(self, tmp_path, mock_agent):
        write_response(mock_agent)
        project = make_project()
        before = set(tmp_path.iterdir())

        run_differencing(str(tmp_path / "a"), str(tmp_path / "b"), project, make_profile())

        # The two output directories are never created or touched by differencing itself.
        after = set(tmp_path.iterdir())
        assert before == after


class TestReadDifferencingResponse:
    def test_parses_valid_response(self, tmp_path):
        response_file = tmp_path / "response.json"
        response_file.write_text(
            json.dumps(
                {
                    "status": "equivalent",
                    "dimensions": [{"name": "public_api", "status": "pass", "rationale": "ok"}],
                    "summary": "all good",
                }
            )
        )

        response = _read_differencing_response(str(response_file))

        assert response.status == "equivalent"
        assert response.dimensions[0].name == "public_api"

    def test_unknown_fields_are_tolerated(self, tmp_path):
        response_file = tmp_path / "response.json"
        response_file.write_text(
            json.dumps({"status": "equivalent", "dimensions": [], "summary": "ok", "extra": "ignored"})
        )

        response = _read_differencing_response(str(response_file))

        assert response.status == "equivalent"

    def test_missing_file_raises_descriptive_error(self, tmp_path):
        with pytest.raises(AgentError, match="not found"):
            _read_differencing_response(str(tmp_path / "missing.json"))

    def test_empty_file_raises_descriptive_error(self, tmp_path):
        response_file = tmp_path / "response.json"
        response_file.write_text("")

        with pytest.raises(AgentError, match="empty"):
            _read_differencing_response(str(response_file))

    def test_malformed_json_raises_descriptive_error(self, tmp_path):
        response_file = tmp_path / "response.json"
        response_file.write_text("{not valid json")

        with pytest.raises(AgentError, match="Malformed JSON"):
            _read_differencing_response(str(response_file))
