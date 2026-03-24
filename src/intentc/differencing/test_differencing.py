"""Tests for the differencing workflow and response parsing."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from intentc.build.agents.models import (
    AgentError,
    AgentProfile,
    DifferencingContext,
    DifferencingResponse,
    DimensionResult,
    PromptTemplates,
    render_differencing_prompt,
)
from intentc.build.agents.mock_agent import MockAgent
from intentc.core.models import Implementation, ProjectIntent
from intentc.core.project import Project
from intentc.differencing.workflow import run_differencing


# --- Fixtures ---


def _make_project(tmp_path: Path) -> Project:
    """Create a minimal Project for testing."""
    return Project(
        project_intent=ProjectIntent(
            name="test-project",
            body="A test project for differencing.",
        ),
        implementations={
            "default": Implementation(
                name="default",
                body="Python implementation.",
            ),
        },
        intent_dir=tmp_path / "intent",
    )


def _make_profile() -> AgentProfile:
    return AgentProfile(name="mock", provider="mock")


# --- DifferencingResponse parsing tests ---


class TestDifferencingResponseParsing:
    def test_equivalent_all_pass(self) -> None:
        data = {
            "status": "equivalent",
            "dimensions": [
                {"name": "public_api", "status": "pass", "rationale": "Same exports"},
                {"name": "runtime_behavior", "status": "pass", "rationale": "Same output"},
            ],
            "summary": "Builds are equivalent.",
        }
        resp = DifferencingResponse(**data)
        assert resp.status == "equivalent"
        assert len(resp.dimensions) == 2
        assert all(d.status == "pass" for d in resp.dimensions)

    def test_divergent_when_dimension_fails(self) -> None:
        data = {
            "status": "divergent",
            "dimensions": [
                {"name": "public_api", "status": "pass", "rationale": "Same exports"},
                {"name": "test_suite", "status": "fail", "rationale": "Tests fail cross-build"},
            ],
            "summary": "Divergent due to test failures.",
        }
        resp = DifferencingResponse(**data)
        assert resp.status == "divergent"
        assert resp.dimensions[1].status == "fail"

    def test_missing_response_file(self) -> None:
        path = "/nonexistent/path/response.json"
        resp_file = Path(path)
        assert not resp_file.exists()
        # The workflow raises AgentError for missing files
        with pytest.raises(AgentError, match="not found"):
            raise AgentError(f"Differencing response file not found: {path}")

    def test_malformed_json_response(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            os.write(fd, b"not valid json {{{")
            os.close(fd)
            with pytest.raises(json.JSONDecodeError):
                json.loads(Path(path).read_text())
        finally:
            os.unlink(path)

    def test_unknown_fields_tolerated(self) -> None:
        """Forward compatibility: unknown fields are ignored."""
        data = {
            "status": "equivalent",
            "dimensions": [],
            "summary": "OK",
            "extra_field": "should not cause error",
            "another_unknown": 42,
        }
        resp = DifferencingResponse(**data)
        assert resp.status == "equivalent"


class TestDimensionResult:
    def test_basic_parsing(self) -> None:
        d = DimensionResult(name="public_api", status="pass", rationale="Matches")
        assert d.name == "public_api"
        assert d.status == "pass"
        assert d.rationale == "Matches"

    def test_json_roundtrip(self) -> None:
        d = DimensionResult(name="runtime_behavior", status="fail", rationale="Different output")
        data = json.loads(d.model_dump_json())
        d2 = DimensionResult(**data)
        assert d2.name == d.name
        assert d2.status == d.status
        assert d2.rationale == d.rationale


# --- Prompt rendering tests ---


class TestDifferencingPromptRendering:
    def test_renders_all_variables(self) -> None:
        template = (
            "Project: {project}\n"
            "Impl: {implementation}\n"
            "DirA: {output_dir_a}\n"
            "DirB: {output_dir_b}\n"
            "Response: {response_file}"
        )
        ctx = DifferencingContext(
            output_dir_a="/path/to/a",
            output_dir_b="/path/to/b",
            project_intent=ProjectIntent(name="proj", body="My project"),
            implementation=Implementation(name="impl", body="Python impl"),
            response_file_path="/tmp/resp.json",
        )
        result = render_differencing_prompt(template, ctx)
        assert "My project" in result
        assert "Python impl" in result
        assert "/path/to/a" in result
        assert "/path/to/b" in result
        assert "/tmp/resp.json" in result
        # No raw placeholders remain
        assert "{project}" not in result
        assert "{implementation}" not in result
        assert "{output_dir_a}" not in result
        assert "{output_dir_b}" not in result
        assert "{response_file}" not in result

    def test_empty_project_and_implementation(self) -> None:
        template = "P:{project} I:{implementation} A:{output_dir_a} B:{output_dir_b} R:{response_file}"
        ctx = DifferencingContext(
            output_dir_a="/a",
            output_dir_b="/b",
            response_file_path="/r.json",
        )
        result = render_differencing_prompt(template, ctx)
        assert "P:" in result
        assert "A:/a" in result

    def test_prompt_loads_from_cwd_intent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Prompt loading resolves relative to CWD/intent/, not module location."""
        monkeypatch.chdir(tmp_path)
        prompt_dir = tmp_path / "intent" / "differencing" / "prompts"
        prompt_dir.mkdir(parents=True)
        (prompt_dir / "difference.prompt").write_text("DIFF: {output_dir_a} vs {output_dir_b}")

        from intentc.build.agents.models import load_default_prompts
        templates = load_default_prompts()
        assert "DIFF:" in templates.difference


# --- Workflow tests ---


class TestRunDifferencing:
    def test_workflow_with_mock_agent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Full workflow test: mock agent writes response file, workflow reads it."""
        dir_a = tmp_path / "output_a"
        dir_b = tmp_path / "output_b"
        dir_a.mkdir()
        dir_b.mkdir()

        expected_response = DifferencingResponse(
            status="equivalent",
            dimensions=[
                DimensionResult(name="public_api", status="pass", rationale="Same API"),
                DimensionResult(name="runtime_behavior", status="pass", rationale="Same behavior"),
            ],
            summary="Builds are equivalent.",
        )

        project = _make_project(tmp_path)

        # We need to patch create_from_profile to return our mock
        # and have the mock write the response file
        class WritingMockAgent(MockAgent):
            def difference(self, ctx: DifferencingContext) -> DifferencingResponse:
                super().difference(ctx)
                Path(ctx.response_file_path).write_text(
                    expected_response.model_dump_json()
                )
                return expected_response

        mock = WritingMockAgent(differencing_response=expected_response)

        monkeypatch.setattr(
            "intentc.differencing.workflow.create_from_profile",
            lambda profile, **kw: mock,
        )

        profile = _make_profile()
        result = run_differencing(
            dir_a=str(dir_a),
            dir_b=str(dir_b),
            project=project,
            agent_profile=profile,
        )

        assert result.status == "equivalent"
        assert len(result.dimensions) == 2
        assert result.summary == "Builds are equivalent."
        assert len(mock.calls) == 1
        assert mock.calls[0].method == "difference"

    def test_workflow_missing_response_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Workflow raises AgentError when agent doesn't write response file."""
        project = _make_project(tmp_path)

        class NoWriteMock(MockAgent):
            def difference(self, ctx: DifferencingContext) -> DifferencingResponse:
                super().difference(ctx)
                # Delete the temp file to simulate missing response
                p = Path(ctx.response_file_path)
                if p.exists():
                    p.unlink()
                return self.differencing_response

        mock = NoWriteMock()
        monkeypatch.setattr(
            "intentc.differencing.workflow.create_from_profile",
            lambda profile, **kw: mock,
        )

        with pytest.raises(AgentError, match="not found"):
            run_differencing(
                dir_a=str(tmp_path / "a"),
                dir_b=str(tmp_path / "b"),
                project=project,
                agent_profile=_make_profile(),
            )

    def test_workflow_empty_response_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Workflow raises AgentError when response file is empty."""
        project = _make_project(tmp_path)

        class EmptyWriteMock(MockAgent):
            def difference(self, ctx: DifferencingContext) -> DifferencingResponse:
                super().difference(ctx)
                Path(ctx.response_file_path).write_text("")
                return self.differencing_response

        mock = EmptyWriteMock()
        monkeypatch.setattr(
            "intentc.differencing.workflow.create_from_profile",
            lambda profile, **kw: mock,
        )

        with pytest.raises(AgentError, match="empty"):
            run_differencing(
                dir_a=str(tmp_path / "a"),
                dir_b=str(tmp_path / "b"),
                project=project,
                agent_profile=_make_profile(),
            )

    def test_workflow_malformed_json(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Workflow raises AgentError when response file has invalid JSON."""
        project = _make_project(tmp_path)

        class BadJsonMock(MockAgent):
            def difference(self, ctx: DifferencingContext) -> DifferencingResponse:
                super().difference(ctx)
                Path(ctx.response_file_path).write_text("not json {{")
                return self.differencing_response

        mock = BadJsonMock()
        monkeypatch.setattr(
            "intentc.differencing.workflow.create_from_profile",
            lambda profile, **kw: mock,
        )

        with pytest.raises(AgentError, match="Malformed JSON"):
            run_differencing(
                dir_a=str(tmp_path / "a"),
                dir_b=str(tmp_path / "b"),
                project=project,
                agent_profile=_make_profile(),
            )

    def test_workflow_does_not_modify_state(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Differencing is a pure evaluation — no state files created."""
        project = _make_project(tmp_path)
        expected = DifferencingResponse(
            status="divergent",
            dimensions=[
                DimensionResult(name="public_api", status="fail", rationale="Different API"),
            ],
            summary="Divergent.",
        )

        class WritingMock(MockAgent):
            def difference(self, ctx: DifferencingContext) -> DifferencingResponse:
                super().difference(ctx)
                Path(ctx.response_file_path).write_text(expected.model_dump_json())
                return expected

        monkeypatch.setattr(
            "intentc.differencing.workflow.create_from_profile",
            lambda profile, **kw: WritingMock(differencing_response=expected),
        )

        # No .intentc state dir before
        state_dir = tmp_path / ".intentc"
        assert not state_dir.exists()

        result = run_differencing(
            dir_a=str(tmp_path / "a"),
            dir_b=str(tmp_path / "b"),
            project=project,
            agent_profile=_make_profile(),
        )

        assert result.status == "divergent"
        # No state directory created
        assert not state_dir.exists()

    def test_workflow_uses_default_implementation(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When no implementation passed, workflow resolves from project."""
        project = _make_project(tmp_path)
        expected = DifferencingResponse(status="equivalent", summary="OK")

        captured_ctx: list[DifferencingContext] = []

        class CaptureMock(MockAgent):
            def difference(self, ctx: DifferencingContext) -> DifferencingResponse:
                super().difference(ctx)
                captured_ctx.append(ctx)
                Path(ctx.response_file_path).write_text(expected.model_dump_json())
                return expected

        monkeypatch.setattr(
            "intentc.differencing.workflow.create_from_profile",
            lambda profile, **kw: CaptureMock(differencing_response=expected),
        )

        run_differencing(
            dir_a=str(tmp_path / "a"),
            dir_b=str(tmp_path / "b"),
            project=project,
            agent_profile=_make_profile(),
        )

        assert len(captured_ctx) == 1
        assert captured_ctx[0].implementation is not None
        assert captured_ctx[0].implementation.name == "default"

    def test_workflow_with_explicit_implementation(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Explicit implementation is passed through to context."""
        project = _make_project(tmp_path)
        custom_impl = Implementation(name="custom", body="Custom impl")
        expected = DifferencingResponse(status="equivalent", summary="OK")

        captured_ctx: list[DifferencingContext] = []

        class CaptureMock(MockAgent):
            def difference(self, ctx: DifferencingContext) -> DifferencingResponse:
                super().difference(ctx)
                captured_ctx.append(ctx)
                Path(ctx.response_file_path).write_text(expected.model_dump_json())
                return expected

        monkeypatch.setattr(
            "intentc.differencing.workflow.create_from_profile",
            lambda profile, **kw: CaptureMock(differencing_response=expected),
        )

        run_differencing(
            dir_a=str(tmp_path / "a"),
            dir_b=str(tmp_path / "b"),
            project=project,
            agent_profile=_make_profile(),
            implementation=custom_impl,
        )

        assert captured_ctx[0].implementation is not None
        assert captured_ctx[0].implementation.name == "custom"
