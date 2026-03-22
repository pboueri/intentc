"""Tests for the agent module."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from intentc.core.types import (
    Implementation,
    IntentFile,
    ProjectIntent,
    Validation,
    ValidationFile,
)
from intentc.build.agents import (
    Agent,
    AgentError,
    AgentProfile,
    BuildContext,
    BuildResponse,
    CLIAgent,
    ClaudeAgent,
    DifferencingContext,
    DifferencingResponse,
    DimensionResult,
    MockAgent,
    PromptTemplates,
    ValidationResponse,
    create_from_profile,
    load_default_prompts,
    render_differencing_prompt,
    render_prompt,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project_intent() -> ProjectIntent:
    return ProjectIntent(name="test-project", body="A test project.")


@pytest.fixture
def intent_file() -> IntentFile:
    return IntentFile(name="test-feature", body="Implement a test feature.")


@pytest.fixture
def implementation() -> Implementation:
    return Implementation(name="default", body="Python 3.11+")


@pytest.fixture
def validation_file() -> ValidationFile:
    return ValidationFile(
        target="test-feature",
        validations=[
            Validation(name="check-exists", args={"rubric": "File should exist"}),
        ],
    )


@pytest.fixture
def build_ctx(
    project_intent: ProjectIntent,
    intent_file: IntentFile,
    implementation: Implementation,
    validation_file: ValidationFile,
    tmp_path: Path,
) -> BuildContext:
    return BuildContext(
        intent=intent_file,
        validations=[validation_file],
        output_dir=str(tmp_path / "output"),
        generation_id="gen-001",
        dependency_names=["dep-a"],
        project_intent=project_intent,
        implementation=implementation,
        response_file_path=str(tmp_path / "response.json"),
    )


@pytest.fixture
def diff_ctx(project_intent: ProjectIntent, tmp_path: Path) -> DifferencingContext:
    return DifferencingContext(
        output_dir_a=str(tmp_path / "a"),
        output_dir_b=str(tmp_path / "b"),
        project_intent=project_intent,
        response_file_path=str(tmp_path / "diff_response.json"),
    )


@pytest.fixture
def profile() -> AgentProfile:
    return AgentProfile(name="test-agent", provider="claude")


# ---------------------------------------------------------------------------
# AgentError
# ---------------------------------------------------------------------------


class TestAgentError:
    def test_is_exception(self) -> None:
        err = AgentError("boom")
        assert isinstance(err, Exception)
        assert str(err) == "boom"


# ---------------------------------------------------------------------------
# PromptTemplates
# ---------------------------------------------------------------------------


class TestPromptTemplates:
    def test_defaults(self) -> None:
        pt = PromptTemplates()
        assert pt.build == ""
        assert pt.validate_template == ""
        assert pt.plan == ""
        assert pt.difference == ""

    def test_custom_values(self) -> None:
        pt = PromptTemplates(build="do build", validate_template="do validate")
        assert pt.build == "do build"
        assert pt.validate_template == "do validate"


# ---------------------------------------------------------------------------
# load_default_prompts
# ---------------------------------------------------------------------------


class TestLoadDefaultPrompts:
    def test_missing_files_yield_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        templates = load_default_prompts()
        assert templates.build == ""
        assert templates.validate_template == ""
        assert templates.plan == ""
        assert templates.difference == ""

    def test_loads_existing_files(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        prompts = tmp_path / "intent" / "build" / "agents" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "build.prompt").write_text("build {project}")
        (prompts / "validate.prompt").write_text("validate {validation}")
        (prompts / "plan.prompt").write_text("plan {feature}")

        diff_prompts = tmp_path / "intent" / "differencing" / "prompts"
        diff_prompts.mkdir(parents=True)
        (diff_prompts / "difference.prompt").write_text("diff {output_dir_a}")

        templates = load_default_prompts()
        assert "build" in templates.build
        assert "validate" in templates.validate_template
        assert "plan" in templates.plan
        assert "diff" in templates.difference


# ---------------------------------------------------------------------------
# Response types
# ---------------------------------------------------------------------------


class TestBuildResponse:
    def test_from_dict(self) -> None:
        resp = BuildResponse(
            status="success",
            summary="done",
            files_created=["a.py"],
            files_modified=["b.py"],
        )
        assert resp.status == "success"
        assert resp.files_created == ["a.py"]

    def test_defaults(self) -> None:
        resp = BuildResponse(status="failure", summary="oops")
        assert resp.files_created == []
        assert resp.files_modified == []


class TestValidationResponse:
    def test_fields(self) -> None:
        resp = ValidationResponse(name="v1", status="pass", reason="ok")
        assert resp.name == "v1"
        assert resp.status == "pass"


class TestDifferencingResponse:
    def test_with_dimensions(self) -> None:
        dim = DimensionResult(name="public_api", status="pass", rationale="same")
        resp = DifferencingResponse(status="equivalent", dimensions=[dim], summary="ok")
        assert len(resp.dimensions) == 1
        assert resp.dimensions[0].name == "public_api"


# ---------------------------------------------------------------------------
# BuildContext
# ---------------------------------------------------------------------------


class TestBuildContext:
    def test_fields(self, build_ctx: BuildContext) -> None:
        assert build_ctx.intent.name == "test-feature"
        assert build_ctx.generation_id == "gen-001"
        assert build_ctx.dependency_names == ["dep-a"]
        assert build_ctx.response_file_path.endswith("response.json")

    def test_defaults(self, project_intent: ProjectIntent, intent_file: IntentFile) -> None:
        ctx = BuildContext(
            intent=intent_file,
            output_dir="/out",
            generation_id="g1",
            project_intent=project_intent,
            response_file_path="/resp.json",
        )
        assert ctx.validations == []
        assert ctx.dependency_names == []
        assert ctx.implementation is None


# ---------------------------------------------------------------------------
# DifferencingContext
# ---------------------------------------------------------------------------


class TestDifferencingContext:
    def test_fields(self, diff_ctx: DifferencingContext) -> None:
        assert diff_ctx.output_dir_a.endswith("/a")
        assert diff_ctx.output_dir_b.endswith("/b")
        assert diff_ctx.implementation is None


# ---------------------------------------------------------------------------
# AgentProfile
# ---------------------------------------------------------------------------


class TestAgentProfile:
    def test_defaults(self) -> None:
        p = AgentProfile(name="default", provider="claude")
        assert p.timeout == 3600.0
        assert p.retries == 3
        assert p.command == ""
        assert p.cli_args == []
        assert p.model_id is None
        assert p.prompt_templates is None
        assert p.sandbox_write_paths == []
        assert p.sandbox_read_paths == []

    def test_custom(self) -> None:
        p = AgentProfile(
            name="custom",
            provider="cli",
            command="my-tool run",
            timeout=120.0,
            retries=1,
            model_id="gpt-4",
            sandbox_write_paths=["/out"],
            sandbox_read_paths=["/in"],
        )
        assert p.timeout == 120.0
        assert p.model_id == "gpt-4"


# ---------------------------------------------------------------------------
# render_prompt / render_differencing_prompt
# ---------------------------------------------------------------------------


class TestRenderPrompt:
    def test_build_template(self, build_ctx: BuildContext) -> None:
        profile = AgentProfile(
            name="t",
            provider="claude",
            prompt_templates=PromptTemplates(
                build="Project: {project}\nImpl: {implementation}\nFeature: {feature}\nValidations: {validations}\nResponse: {response_file}"
            ),
        )
        result = render_prompt("build", build_ctx, profile=profile)
        assert "A test project." in result
        assert "Python 3.11+" in result
        assert "Implement a test feature." in result
        assert "check-exists" in result
        assert build_ctx.response_file_path in result

    def test_validate_template(self, build_ctx: BuildContext) -> None:
        val = Validation(name="v1", args={"rubric": "check it"})
        profile = AgentProfile(
            name="t",
            provider="claude",
            prompt_templates=PromptTemplates(
                validate_template="Validate: {validation}"
            ),
        )
        result = render_prompt("validate_template", build_ctx, validation=val, profile=profile)
        assert "v1" in result

    def test_empty_template(self, build_ctx: BuildContext) -> None:
        profile = AgentProfile(
            name="t",
            provider="claude",
            prompt_templates=PromptTemplates(),
        )
        result = render_prompt("build", build_ctx, profile=profile)
        assert result == ""

    def test_differencing_prompt(self, diff_ctx: DifferencingContext) -> None:
        profile = AgentProfile(
            name="t",
            provider="claude",
            prompt_templates=PromptTemplates(
                difference="A: {output_dir_a}\nB: {output_dir_b}\nProject: {project}\nResp: {response_file}"
            ),
        )
        result = render_differencing_prompt(diff_ctx, profile=profile)
        assert diff_ctx.output_dir_a in result
        assert diff_ctx.output_dir_b in result


# ---------------------------------------------------------------------------
# MockAgent
# ---------------------------------------------------------------------------


class TestMockAgent:
    def test_defaults(self) -> None:
        agent = MockAgent()
        assert agent.get_name() == "mock"
        assert agent.get_type() == "mock"

    def test_is_agent(self) -> None:
        assert isinstance(MockAgent(), Agent)

    def test_build_records_call(self, build_ctx: BuildContext) -> None:
        agent = MockAgent()
        resp = agent.build(build_ctx)
        assert resp.status == "success"
        assert len(agent.build_calls) == 1
        assert agent.build_calls[0] is build_ctx

    def test_validate_records_call(self, build_ctx: BuildContext) -> None:
        val = Validation(name="v1", args={"rubric": "x"})
        agent = MockAgent()
        resp = agent.validate(build_ctx, val)
        assert resp.status == "pass"
        assert len(agent.validate_calls) == 1
        assert agent.validate_calls[0] == (build_ctx, val)

    def test_difference_records_call(self, diff_ctx: DifferencingContext) -> None:
        agent = MockAgent()
        resp = agent.difference(diff_ctx)
        assert resp.status == "equivalent"
        assert len(agent.difference_calls) == 1
        assert agent.difference_calls[0] is diff_ctx

    def test_plan_records_call(self, build_ctx: BuildContext) -> None:
        agent = MockAgent()
        agent.plan(build_ctx)
        assert len(agent.plan_calls) == 1

    def test_custom_responses(self, build_ctx: BuildContext, diff_ctx: DifferencingContext) -> None:
        custom_build = BuildResponse(status="failure", summary="nope")
        custom_val = ValidationResponse(name="x", status="fail", reason="bad")
        custom_diff = DifferencingResponse(
            status="divergent",
            dimensions=[DimensionResult(name="public_api", status="fail", rationale="different")],
            summary="not the same",
        )
        agent = MockAgent(
            name="custom-mock",
            build_response=custom_build,
            validation_response=custom_val,
            differencing_response=custom_diff,
        )
        assert agent.get_name() == "custom-mock"
        assert agent.build(build_ctx).status == "failure"

        val = Validation(name="v1", args={})
        assert agent.validate(build_ctx, val).status == "fail"
        assert agent.difference(diff_ctx).status == "divergent"


# ---------------------------------------------------------------------------
# create_from_profile
# ---------------------------------------------------------------------------


class TestCreateFromProfile:
    def test_claude_provider(self) -> None:
        p = AgentProfile(name="c", provider="claude")
        agent = create_from_profile(p)
        assert isinstance(agent, ClaudeAgent)
        assert agent.get_type() == "claude"

    def test_cli_provider(self) -> None:
        p = AgentProfile(name="g", provider="cli", command="echo")
        agent = create_from_profile(p)
        assert isinstance(agent, CLIAgent)
        assert agent.get_type() == "cli"

    def test_unknown_provider(self) -> None:
        p = AgentProfile(name="u", provider="unknown")
        with pytest.raises(AgentError, match="Unknown agent provider"):
            create_from_profile(p)


# ---------------------------------------------------------------------------
# CLIAgent
# ---------------------------------------------------------------------------


class TestCLIAgent:
    def test_get_name_and_type(self) -> None:
        p = AgentProfile(name="my-cli", provider="cli", command="echo")
        agent = CLIAgent(p)
        assert agent.get_name() == "my-cli"
        assert agent.get_type() == "cli"

    def test_build_reads_response_file(self, build_ctx: BuildContext, tmp_path: Path) -> None:
        # Write a response file that the "agent" would produce
        resp_path = tmp_path / "response.json"
        resp_path.write_text(json.dumps({
            "status": "success",
            "summary": "built it",
            "files_created": ["main.py"],
            "files_modified": [],
        }))

        # Use a command that writes to the response file and exits
        p = AgentProfile(
            name="test",
            provider="cli",
            command="true",  # no-op; we pre-wrote the response
            prompt_templates=PromptTemplates(build="x"),
        )
        agent = CLIAgent(p)
        # Override response path to our pre-written file
        build_ctx.response_file_path = str(resp_path)
        resp = agent.build(build_ctx)
        assert resp.status == "success"
        assert resp.files_created == ["main.py"]

    def test_missing_command_raises(self, build_ctx: BuildContext) -> None:
        p = AgentProfile(name="empty", provider="cli")
        agent = CLIAgent(p)
        with pytest.raises(AgentError, match="requires a command"):
            agent.build(build_ctx)


# ---------------------------------------------------------------------------
# ClaudeAgent
# ---------------------------------------------------------------------------


class TestClaudeAgent:
    def test_get_name_and_type(self) -> None:
        p = AgentProfile(name="my-claude", provider="claude")
        agent = ClaudeAgent(p)
        assert agent.get_name() == "my-claude"
        assert agent.get_type() == "claude"

    def test_build_cmd_construction(self) -> None:
        p = AgentProfile(
            name="test",
            provider="claude",
            model_id="opus-4",
            cli_args=["--max-turns", "5"],
        )
        agent = ClaudeAgent(p)
        cmd = agent._build_non_interactive_cmd("hello")
        assert cmd[0] == "claude"
        assert "-p" in cmd
        assert "hello" in cmd
        assert "--verbose" in cmd
        assert "--output-format" in cmd
        assert "stream-json" in cmd
        assert "--dangerously-skip-permissions" in cmd
        assert "--model" in cmd
        assert "opus-4" in cmd
        assert "--max-turns" in cmd
        assert "5" in cmd

    def test_build_cmd_no_model(self) -> None:
        p = AgentProfile(name="test", provider="claude")
        agent = ClaudeAgent(p)
        cmd = agent._build_non_interactive_cmd("hi")
        assert "--model" not in cmd

    def test_sandbox_settings_written_and_cleaned(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        p = AgentProfile(
            name="sandboxed",
            provider="claude",
            sandbox_write_paths=["/out"],
            sandbox_read_paths=["/in/a", "/in/b"],
        )
        agent = ClaudeAgent(p)
        settings_path = agent._write_sandbox_settings()
        assert settings_path is not None
        assert settings_path.exists()

        data = json.loads(settings_path.read_text())
        assert data["sandbox"]["writePaths"] == ["/out"]
        assert data["sandbox"]["readPaths"] == ["/in/a", "/in/b"]

        agent._cleanup_sandbox_settings(settings_path)
        assert not settings_path.exists()

    def test_no_sandbox_when_no_paths(self) -> None:
        p = AgentProfile(name="no-sandbox", provider="claude")
        agent = ClaudeAgent(p)
        assert agent._write_sandbox_settings() is None


# ---------------------------------------------------------------------------
# Response file reading
# ---------------------------------------------------------------------------


class TestResponseFileReading:
    def test_missing_file_raises(self) -> None:
        from intentc.build.agents import _read_response_file

        with pytest.raises(AgentError, match="not found"):
            _read_response_file("/nonexistent/path.json", BuildResponse)

    def test_invalid_json_raises(self, tmp_path: Path) -> None:
        from intentc.build.agents import _read_response_file

        bad = tmp_path / "bad.json"
        bad.write_text("not json at all")
        with pytest.raises(AgentError, match="Invalid response file"):
            _read_response_file(str(bad), BuildResponse)

    def test_valid_json_parsed_and_deleted(self, tmp_path: Path) -> None:
        from intentc.build.agents import _read_response_file

        resp_file = tmp_path / "resp.json"
        resp_file.write_text(json.dumps({
            "status": "success",
            "summary": "ok",
            "files_created": [],
            "files_modified": [],
        }))
        result = _read_response_file(str(resp_file), BuildResponse)
        assert result.status == "success"
        assert not resp_file.exists()  # deleted after reading


# ---------------------------------------------------------------------------
# Agent ABC
# ---------------------------------------------------------------------------


class TestAgentABC:
    def test_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            Agent()  # type: ignore[abstract]

    def test_subclass_must_implement_all(self) -> None:
        class Incomplete(Agent):
            def build(self, ctx: BuildContext) -> BuildResponse:
                return BuildResponse(status="success", summary="")

        with pytest.raises(TypeError):
            Incomplete()  # type: ignore[abstract]
