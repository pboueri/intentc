"""Tests for intentc.differencing."""

from __future__ import annotations

import json
import subprocess

import pytest

from intentc.build.agents import (
    AgentError,
    AgentProfile,
    CLIAgent,
    DifferencingContext,
    DifferencingResponse,
    DimensionResult,
    MockAgent,
)
from intentc.core import Implementation, Project, ProjectIntent
from intentc.differencing import run_differencing


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
        "intentc.differencing.differencing.create_from_profile",
        lambda profile, log=None: agent,
    )
    return agent


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
        project = make_project()

        response = run_differencing(str(tmp_path / "a"), str(tmp_path / "b"), project, make_profile())

        assert response.status == "divergent"

    def test_returns_exactly_what_the_agent_returns(self, tmp_path, mock_agent):
        # run_differencing must not re-read the response file itself: the agent already
        # reads, validates, and deletes it, and hands back the parsed response.
        expected = DifferencingResponse(status="equivalent", dimensions=[], summary="from the agent")
        mock_agent.differencing_response = expected
        project = make_project()

        response = run_differencing(str(tmp_path / "a"), str(tmp_path / "b"), project, make_profile())

        assert response is expected

    def test_constructs_context_with_both_dirs_and_project_intent(self, tmp_path, mock_agent):
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
        impl = Implementation(name="python", body="Use Python.")
        project = make_project(python=impl)

        run_differencing(
            str(tmp_path / "a"), str(tmp_path / "b"), project, make_profile(), implementation="python"
        )

        ctx = mock_agent.difference_calls[0]
        assert ctx.implementation == impl

    def test_unknown_implementation_raises_key_error(self, tmp_path, mock_agent):
        project = make_project(python=Implementation(name="python", body="Use Python."))

        with pytest.raises(KeyError):
            run_differencing(
                str(tmp_path / "a"), str(tmp_path / "b"), project, make_profile(), implementation="go"
            )

    def test_ambiguous_implementation_raises_value_error(self, tmp_path, mock_agent):
        project = make_project(
            python=Implementation(name="python", body="Use Python."),
            go=Implementation(name="go", body="Use Go."),
        )

        with pytest.raises(ValueError):
            run_differencing(str(tmp_path / "a"), str(tmp_path / "b"), project, make_profile())

    def test_agent_error_propagates_unchanged(self, tmp_path, monkeypatch):
        # A missing/malformed response file is the *agent's* concern (it owns the
        # response-file lifecycle) -- run_differencing must let the AgentError
        # it raises propagate untouched rather than trying to read the file itself.
        agent = MockAgent(name="mock")

        def _raise_missing(ctx):
            raise AgentError(f"Agent response file not found: {ctx.response_file_path}")

        agent.difference = _raise_missing  # type: ignore[method-assign]
        monkeypatch.setattr(
            "intentc.differencing.differencing.create_from_profile",
            lambda profile, log=None: agent,
        )
        project = make_project()

        with pytest.raises(AgentError, match="not found"):
            run_differencing(str(tmp_path / "a"), str(tmp_path / "b"), project, make_profile())

    def test_does_not_modify_build_state(self, tmp_path, mock_agent):
        project = make_project()
        before = set(tmp_path.iterdir())

        run_differencing(str(tmp_path / "a"), str(tmp_path / "b"), project, make_profile())

        # The two output directories are never created or touched by differencing itself.
        after = set(tmp_path.iterdir())
        assert before == after

    def test_threads_log_callback_into_agent_creation(self, tmp_path, monkeypatch):
        captured = {}

        def fake_create_from_profile(profile, log=None):
            captured["log"] = log
            return MockAgent(name="mock")

        monkeypatch.setattr(
            "intentc.differencing.differencing.create_from_profile", fake_create_from_profile
        )
        project = make_project()
        messages = []

        run_differencing(
            str(tmp_path / "a"), str(tmp_path / "b"), project, make_profile(), log=messages.append
        )

        assert captured["log"] == messages.append


class TestDifferencingResponseParsing:
    """DifferencingResponse is defined in the agents module; these tests exercise the
    shared response-file mechanism (read, validate, delete) through CLIAgent.difference,
    the same path run_differencing relies on for a real (non-mock) agent."""

    def make_agent(self, monkeypatch) -> CLIAgent:
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0] if a else [], 0, stdout="", stderr=""),
        )
        profile = AgentProfile(name="test-cli", provider="cli", command="mytool")
        return CLIAgent(profile)

    def make_ctx(self, response_file_path: str) -> DifferencingContext:
        return DifferencingContext(
            output_dir_a="a",
            output_dir_b="b",
            project_intent=ProjectIntent(name="project", body="Build a thing."),
            response_file_path=response_file_path,
        )

    def test_equivalent_when_all_dimensions_pass(self, monkeypatch, tmp_path):
        agent = self.make_agent(monkeypatch)
        response_file = tmp_path / "diff.json"
        response_file.write_text(
            json.dumps(
                {
                    "status": "equivalent",
                    "dimensions": [{"name": "public_api", "status": "pass", "rationale": "ok"}],
                    "summary": "all good",
                }
            )
        )

        response = agent.difference(self.make_ctx(str(response_file)))

        assert response.status == "equivalent"
        assert response.dimensions[0].name == "public_api"

    def test_divergent_when_a_dimension_fails(self, monkeypatch, tmp_path):
        agent = self.make_agent(monkeypatch)
        response_file = tmp_path / "diff.json"
        response_file.write_text(
            json.dumps(
                {
                    "status": "divergent",
                    "dimensions": [
                        {"name": "public_api", "status": "pass", "rationale": "ok"},
                        {"name": "runtime_behavior", "status": "fail", "rationale": "differs"},
                    ],
                    "summary": "diverges",
                }
            )
        )

        response = agent.difference(self.make_ctx(str(response_file)))

        assert response.status == "divergent"

    def test_unknown_fields_are_tolerated(self, monkeypatch, tmp_path):
        agent = self.make_agent(monkeypatch)
        response_file = tmp_path / "diff.json"
        response_file.write_text(
            json.dumps({"status": "equivalent", "dimensions": [], "summary": "ok", "extra": "ignored"})
        )

        response = agent.difference(self.make_ctx(str(response_file)))

        assert response.status == "equivalent"

    def test_missing_response_file_raises_descriptive_error(self, monkeypatch, tmp_path):
        agent = self.make_agent(monkeypatch)

        with pytest.raises(AgentError, match="not found"):
            agent.difference(self.make_ctx(str(tmp_path / "missing.json")))

    def test_malformed_json_raises_descriptive_error(self, monkeypatch, tmp_path):
        agent = self.make_agent(monkeypatch)
        response_file = tmp_path / "diff.json"
        response_file.write_text("{not valid json")

        with pytest.raises(AgentError, match="Invalid JSON"):
            agent.difference(self.make_ctx(str(response_file)))

    def test_response_file_is_deleted_after_reading(self, monkeypatch, tmp_path):
        agent = self.make_agent(monkeypatch)
        response_file = tmp_path / "diff.json"
        response_file.write_text(json.dumps({"status": "equivalent", "dimensions": [], "summary": "ok"}))

        agent.difference(self.make_ctx(str(response_file)))

        assert not response_file.exists()
