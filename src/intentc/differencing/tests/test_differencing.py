"""Tests for the differencing workflow."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from intentc.build.agents import (
    AgentError,
    AgentProfile,
    DifferencingContext,
    DifferencingResponse,
    DimensionResult,
    MockAgent,
    load_default_prompts,
    render_differencing_prompt,
)
from intentc.core import Implementation, Project, ProjectIntent
from intentc.differencing import run_differencing

PROFILE = AgentProfile(name="p", provider="cli", command="unused")


def _project() -> Project:
    return Project(
        project_intent=ProjectIntent(name="p", body="PB"),
        implementations={"default": Implementation(name="default", body="IB"), "go": Implementation(name="go", body="GO")},
    )


@pytest.fixture()
def dirs(tmp_path: Path) -> tuple[str, str]:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    return str(tmp_path / "a"), str(tmp_path / "b")


def _patch_agent(monkeypatch, agent: MockAgent) -> None:
    monkeypatch.setattr("intentc.differencing.differencing.create_from_profile", lambda profile, log=None: agent)


def test_equivalent_response(dirs, monkeypatch) -> None:
    agent = MockAgent(
        differencing_response=DifferencingResponse(
            status="equivalent",
            dimensions=[DimensionResult(name="public_api", status="pass", rationale="same")],
            summary="all good",
        )
    )
    _patch_agent(monkeypatch, agent)
    logs: list[str] = []
    response = run_differencing(dirs[0], dirs[1], _project(), PROFILE, log=logs.append)
    assert response.status == "equivalent" and response.summary == "all good"
    ctx = agent.difference_calls[0]
    assert isinstance(ctx, DifferencingContext)
    assert ctx.output_dir_a == dirs[0] and ctx.output_dir_b == dirs[1]
    assert ctx.project_intent.body == "PB" and ctx.implementation.name == "default"
    assert not Path(ctx.response_file_path).exists()  # temp response file cleaned up
    assert any("Comparing" in l for l in logs)


def test_failed_dimension_forces_divergent(dirs, monkeypatch) -> None:
    agent = MockAgent(
        differencing_response=DifferencingResponse(
            status="equivalent",  # agent was inconsistent; a failing dimension wins
            dimensions=[DimensionResult(name="a", status="pass", rationale=""), DimensionResult(name="b", status="fail", rationale="x")],
            summary="",
        )
    )
    _patch_agent(monkeypatch, agent)
    assert run_differencing(dirs[0], dirs[1], _project(), PROFILE).status == "divergent"


def test_implementation_selection(dirs, monkeypatch) -> None:
    agent = MockAgent()
    _patch_agent(monkeypatch, agent)
    run_differencing(dirs[0], dirs[1], _project(), PROFILE, implementation="go")
    assert agent.difference_calls[0].implementation.body == "GO"
    with pytest.raises(KeyError):
        run_differencing(dirs[0], dirs[1], _project(), PROFILE, implementation="rust")


def test_missing_directories(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    with pytest.raises(AgentError, match="candidate directory does not exist"):
        run_differencing(str(tmp_path / "a"), str(tmp_path / "nope"), _project(), PROFILE)
    with pytest.raises(AgentError, match="reference directory does not exist"):
        run_differencing(str(tmp_path / "nope"), str(tmp_path / "a"), _project(), PROFILE)


class _SilentAgent(MockAgent):
    """Returns a response but never writes the file — the workflow must read the file, not trust the return."""

    def __init__(self, content: str | None) -> None:
        super().__init__()
        self._content = content

    def difference(self, ctx: DifferencingContext) -> DifferencingResponse:
        self.difference_calls.append(ctx)
        if self._content is not None:
            Path(ctx.response_file_path).write_text(self._content)
        return DifferencingResponse(status="equivalent", summary="trust me")


def test_missing_empty_and_malformed_response_files(dirs, monkeypatch) -> None:
    for content, message in [(None, "not found"), ("", "is empty"), ("{bad", "Malformed JSON"), ("[1]", "JSON object")]:
        _patch_agent(monkeypatch, _SilentAgent(content))
        with pytest.raises(AgentError, match=message):
            run_differencing(dirs[0], dirs[1], _project(), PROFILE)


def test_unknown_fields_tolerated(dirs, monkeypatch) -> None:
    payload = json.dumps({"status": "equivalent", "dimensions": [{"name": "x", "status": "pass", "rationale": "r", "confidence": 0.9}], "summary": "s", "extra": True})
    _patch_agent(monkeypatch, _SilentAgent(payload))
    assert run_differencing(dirs[0], dirs[1], _project(), PROFILE).status == "equivalent"


def test_prompt_rendering_uses_bundled_template() -> None:
    template = load_default_prompts().difference
    assert "public_api" in template
    ctx = DifferencingContext(output_dir_a="/ref", output_dir_b="/cand", project_intent=ProjectIntent(name="p", body="PB"), implementation=Implementation(name="i", body="IB"), response_file_path="/tmp/r.json")
    text = render_differencing_prompt(template, ctx)
    assert "/ref" in text and "/cand" in text and "PB" in text and "IB" in text and "/tmp/r.json" in text
    assert "{output_dir_a}" not in text and "{response_file}" not in text
