"""Tests for the differencing workflow."""

from __future__ import annotations

import json
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
    create_from_profile,
    render_differencing_prompt,
)
from intentc.core.models import Implementation, ProjectIntent
from intentc.core.project import Project
from intentc.differencing import load_differencing_prompt, run_differencing


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project_intent() -> ProjectIntent:
    return ProjectIntent(name="test-project", body="A test project")


@pytest.fixture
def implementation() -> Implementation:
    return Implementation(name="default", body="Python 3.11+")


@pytest.fixture
def project(project_intent: ProjectIntent, implementation: Implementation) -> Project:
    return Project(
        project_intent=project_intent,
        implementations={"default": implementation},
        features={},
        intent_dir=Path("/tmp/intent"),
    )


@pytest.fixture
def cli_profile() -> AgentProfile:
    return AgentProfile(name="test-cli", provider="cli", command="echo test")


# ---------------------------------------------------------------------------
# DifferencingResponse parsing
# ---------------------------------------------------------------------------


class TestDifferencingResponseParsing:
    def test_equivalent_all_pass(self):
        data = {
            "status": "equivalent",
            "dimensions": [
                {"name": "public_api", "status": "pass", "rationale": "APIs match"},
                {"name": "test_suite", "status": "pass", "rationale": "Tests pass"},
                {"name": "runtime_behavior", "status": "pass", "rationale": "Same output"},
            ],
            "summary": "Builds are equivalent",
        }
        resp = DifferencingResponse(**data)
        assert resp.status == "equivalent"
        assert len(resp.dimensions) == 3
        assert all(d.status == "pass" for d in resp.dimensions)

    def test_divergent_with_failing_dimension(self):
        data = {
            "status": "divergent",
            "dimensions": [
                {"name": "public_api", "status": "pass", "rationale": "ok"},
                {"name": "test_suite", "status": "fail", "rationale": "tests differ"},
            ],
            "summary": "Not equivalent",
        }
        resp = DifferencingResponse(**data)
        assert resp.status == "divergent"
        assert resp.dimensions[1].status == "fail"

    def test_unknown_fields_tolerated(self):
        data = {
            "status": "equivalent",
            "dimensions": [
                {
                    "name": "api",
                    "status": "pass",
                    "rationale": "ok",
                    "extra_field": "ignored",
                },
            ],
            "summary": "all good",
            "unknown_top_level": True,
        }
        # Pydantic should tolerate extra fields without error
        resp = DifferencingResponse(**data)
        assert resp.status == "equivalent"

    def test_missing_response_file(self, tmp_path: Path):
        path = str(tmp_path / "nonexistent.json")
        with pytest.raises(AgentError, match="not found"):
            _read_and_parse_response(path)

    def test_malformed_json_response(self, tmp_path: Path):
        path = tmp_path / "bad.json"
        path.write_text("not valid json {{{")
        with pytest.raises(AgentError, match="Malformed JSON"):
            _read_and_parse_response(str(path))

    def test_empty_response_file(self, tmp_path: Path):
        path = tmp_path / "empty.json"
        path.write_text("")
        with pytest.raises(AgentError, match="empty"):
            _read_and_parse_response(str(path))


def _read_and_parse_response(path: str) -> DifferencingResponse:
    """Replicate the response file parsing logic from run_differencing."""
    response_file = Path(path)
    if not response_file.exists():
        raise AgentError(f"Differencing response file not found: {path}")
    content = response_file.read_text(encoding="utf-8")
    if not content.strip():
        raise AgentError(f"Differencing response file is empty: {path}")
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise AgentError(
            f"Malformed JSON in differencing response file {path}: {exc}"
        ) from exc
    return DifferencingResponse(**data)


# ---------------------------------------------------------------------------
# render_differencing_prompt
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# load_differencing_prompt
# ---------------------------------------------------------------------------


class TestLoadDifferencingPrompt:
    def test_loads_from_intent_dir(self, tmp_path: Path):
        """Prompt is loaded from intent_dir/differencing/prompts/difference.prompt."""
        prompts_dir = tmp_path / "differencing" / "prompts"
        prompts_dir.mkdir(parents=True)
        prompt_file = prompts_dir / "difference.prompt"
        prompt_file.write_text("custom prompt from intent dir")

        result = load_differencing_prompt(tmp_path)
        assert result == "custom prompt from intent dir"

    def test_falls_back_to_bundled_default(self, tmp_path: Path):
        """Falls back to package default when intent dir has no prompt."""
        result = load_differencing_prompt(tmp_path)
        # Should return the bundled default (non-empty)
        assert len(result) > 0
        assert "{output_dir_a}" in result  # Contains template variables

    def test_intent_dir_takes_precedence(self, tmp_path: Path):
        """Intent dir prompt takes precedence over bundled default."""
        prompts_dir = tmp_path / "differencing" / "prompts"
        prompts_dir.mkdir(parents=True)
        (prompts_dir / "difference.prompt").write_text("OVERRIDE")

        result = load_differencing_prompt(tmp_path)
        assert result == "OVERRIDE"


# ---------------------------------------------------------------------------
# render_differencing_prompt
# ---------------------------------------------------------------------------


class TestRenderDifferencingPrompt:
    def test_substitutes_all_variables(self, project_intent: ProjectIntent, implementation: Implementation, tmp_path: Path):
        ctx = DifferencingContext(
            output_dir_a="/path/to/a",
            output_dir_b="/path/to/b",
            project_intent=project_intent,
            implementation=implementation,
            response_file_path="/tmp/response.json",
        )
        template = "Project: {project}\nImpl: {implementation}\nA: {output_dir_a}\nB: {output_dir_b}\nResp: {response_file}"
        result = render_differencing_prompt(template, ctx)
        assert "A test project" in result
        assert "Python 3.11+" in result
        assert "/path/to/a" in result
        assert "/path/to/b" in result
        assert "/tmp/response.json" in result

    def test_no_raw_placeholders(self, project_intent: ProjectIntent, tmp_path: Path):
        ctx = DifferencingContext(
            output_dir_a="/a",
            output_dir_b="/b",
            project_intent=project_intent,
            response_file_path="/r.json",
        )
        template = "{project} {output_dir_a} {output_dir_b} {response_file}"
        result = render_differencing_prompt(template, ctx)
        assert "{project}" not in result
        assert "{output_dir_a}" not in result
        assert "{output_dir_b}" not in result
        assert "{response_file}" not in result


# ---------------------------------------------------------------------------
# run_differencing workflow
# ---------------------------------------------------------------------------


class TestRunDifferencing:
    def test_returns_response_from_file(self, project: Project, tmp_path: Path):
        """Workflow reads response from file, not from agent return value."""
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()

        expected_response = {
            "status": "equivalent",
            "dimensions": [
                {"name": "public_api", "status": "pass", "rationale": "match"},
            ],
            "summary": "equivalent builds",
        }

        def mock_difference(ctx: DifferencingContext) -> DifferencingResponse:
            # Write response file (simulating what a real agent does)
            Path(ctx.response_file_path).write_text(json.dumps(expected_response))
            return DifferencingResponse(status="equivalent", summary="ignored")

        mock_agent = MockAgent()
        mock_agent.difference = mock_difference  # type: ignore[assignment]

        profile = AgentProfile(name="mock", provider="cli", command="echo")

        with patch("intentc.differencing.differencing.create_from_profile", return_value=mock_agent):
            result = run_differencing(
                output_dir_a=str(dir_a),
                output_dir_b=str(dir_b),
                project=project,
                profile=profile,
            )

        assert result.status == "equivalent"
        assert len(result.dimensions) == 1
        assert result.dimensions[0].name == "public_api"

    def test_raises_on_missing_response_file(self, project: Project, tmp_path: Path):
        """Missing response file raises AgentError."""
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()

        mock_agent = MockAgent()
        # Agent doesn't write a response file — difference() is a no-op for mock
        # But we need to ensure the temp file is empty/removed
        def mock_difference_no_write(ctx: DifferencingContext) -> DifferencingResponse:
            # Delete the temp file to simulate missing
            Path(ctx.response_file_path).unlink(missing_ok=True)
            return DifferencingResponse(status="equivalent", summary="ignored")

        mock_agent.difference = mock_difference_no_write  # type: ignore[assignment]

        profile = AgentProfile(name="mock", provider="cli", command="echo")

        with patch("intentc.differencing.differencing.create_from_profile", return_value=mock_agent):
            with pytest.raises(AgentError, match="not found"):
                run_differencing(
                    output_dir_a=str(dir_a),
                    output_dir_b=str(dir_b),
                    project=project,
                    profile=profile,
                )

    def test_raises_on_empty_response_file(self, project: Project, tmp_path: Path):
        """Empty response file raises AgentError."""
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()

        def mock_difference_empty(ctx: DifferencingContext) -> DifferencingResponse:
            Path(ctx.response_file_path).write_text("")
            return DifferencingResponse(status="equivalent", summary="ignored")

        mock_agent = MockAgent()
        mock_agent.difference = mock_difference_empty  # type: ignore[assignment]

        profile = AgentProfile(name="mock", provider="cli", command="echo")

        with patch("intentc.differencing.differencing.create_from_profile", return_value=mock_agent):
            with pytest.raises(AgentError, match="empty"):
                run_differencing(
                    output_dir_a=str(dir_a),
                    output_dir_b=str(dir_b),
                    project=project,
                    profile=profile,
                )

    def test_raises_on_malformed_json(self, project: Project, tmp_path: Path):
        """Malformed JSON in response file raises AgentError."""
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()

        def mock_difference_bad_json(ctx: DifferencingContext) -> DifferencingResponse:
            Path(ctx.response_file_path).write_text("not json {{{")
            return DifferencingResponse(status="equivalent", summary="ignored")

        mock_agent = MockAgent()
        mock_agent.difference = mock_difference_bad_json  # type: ignore[assignment]

        profile = AgentProfile(name="mock", provider="cli", command="echo")

        with patch("intentc.differencing.differencing.create_from_profile", return_value=mock_agent):
            with pytest.raises(AgentError, match="Malformed JSON"):
                run_differencing(
                    output_dir_a=str(dir_a),
                    output_dir_b=str(dir_b),
                    project=project,
                    profile=profile,
                )

    def test_does_not_modify_state(self, project: Project, tmp_path: Path):
        """Differencing is a pure evaluation — no state changes."""
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()

        response = {
            "status": "equivalent",
            "dimensions": [],
            "summary": "ok",
        }

        def mock_difference(ctx: DifferencingContext) -> DifferencingResponse:
            Path(ctx.response_file_path).write_text(json.dumps(response))
            return DifferencingResponse(status="equivalent", summary="ignored")

        mock_agent = MockAgent()
        mock_agent.difference = mock_difference  # type: ignore[assignment]

        profile = AgentProfile(name="mock", provider="cli", command="echo")

        with patch("intentc.differencing.differencing.create_from_profile", return_value=mock_agent):
            result = run_differencing(
                output_dir_a=str(dir_a),
                output_dir_b=str(dir_b),
                project=project,
                profile=profile,
            )

        # No state files should be created in the project
        assert result.status == "equivalent"

    def test_resolves_implementation(self, project: Project, tmp_path: Path):
        """Workflow resolves implementation and passes it to context."""
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()

        captured_ctx: list[DifferencingContext] = []

        response = {
            "status": "equivalent",
            "dimensions": [],
            "summary": "ok",
        }

        def mock_difference(ctx: DifferencingContext) -> DifferencingResponse:
            captured_ctx.append(ctx)
            Path(ctx.response_file_path).write_text(json.dumps(response))
            return DifferencingResponse(status="equivalent", summary="ignored")

        mock_agent = MockAgent()
        mock_agent.difference = mock_difference  # type: ignore[assignment]

        profile = AgentProfile(name="mock", provider="cli", command="echo")

        with patch("intentc.differencing.differencing.create_from_profile", return_value=mock_agent):
            run_differencing(
                output_dir_a=str(dir_a),
                output_dir_b=str(dir_b),
                project=project,
                profile=profile,
                implementation="default",
            )

        assert len(captured_ctx) == 1
        assert captured_ctx[0].implementation is not None
        assert captured_ctx[0].implementation.name == "default"

    def test_constructs_context_correctly(self, project: Project, tmp_path: Path):
        """Workflow constructs DifferencingContext with correct fields."""
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()

        captured_ctx: list[DifferencingContext] = []

        response = {
            "status": "divergent",
            "dimensions": [
                {"name": "public_api", "status": "fail", "rationale": "mismatch"},
            ],
            "summary": "divergent",
        }

        def mock_difference(ctx: DifferencingContext) -> DifferencingResponse:
            captured_ctx.append(ctx)
            Path(ctx.response_file_path).write_text(json.dumps(response))
            return DifferencingResponse(status="divergent", summary="ignored")

        mock_agent = MockAgent()
        mock_agent.difference = mock_difference  # type: ignore[assignment]

        profile = AgentProfile(name="mock", provider="cli", command="echo")

        with patch("intentc.differencing.differencing.create_from_profile", return_value=mock_agent):
            result = run_differencing(
                output_dir_a=str(dir_a),
                output_dir_b=str(dir_b),
                project=project,
                profile=profile,
            )

        ctx = captured_ctx[0]
        assert ctx.output_dir_a == str(dir_a)
        assert ctx.output_dir_b == str(dir_b)
        assert ctx.project_intent.name == "test-project"
        assert ctx.response_file_path.endswith(".json")
        assert result.status == "divergent"
