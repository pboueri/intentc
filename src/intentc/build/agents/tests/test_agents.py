"""Tests for the agent module."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from intentc.core.models import (
    Implementation,
    IntentFile,
    ProjectIntent,
    Validation,
    ValidationFile,
    ValidationType,
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
    return ProjectIntent(name="test-project", body="A test project")


@pytest.fixture
def intent_file() -> IntentFile:
    return IntentFile(name="test-feature", body="Build a test feature")


@pytest.fixture
def validation_file() -> ValidationFile:
    return ValidationFile(
        target="test-feature",
        validations=[
            Validation(
                name="check-exists",
                type=ValidationType.FILE_CHECK,
                args={"path": "output.txt"},
            )
        ],
    )


@pytest.fixture
def implementation() -> Implementation:
    return Implementation(name="default", body="Python 3.11+")


@pytest.fixture
def build_ctx(
    project_intent: ProjectIntent,
    intent_file: IntentFile,
    validation_file: ValidationFile,
    implementation: Implementation,
    tmp_path: Path,
) -> BuildContext:
    return BuildContext(
        intent=intent_file,
        validations=[validation_file],
        output_dir=str(tmp_path / "output"),
        generation_id="gen-123",
        dependency_names=["dep-a"],
        project_intent=project_intent,
        implementation=implementation,
        response_file_path=str(tmp_path / "response.json"),
    )


@pytest.fixture
def diff_ctx(
    project_intent: ProjectIntent,
    implementation: Implementation,
    tmp_path: Path,
) -> DifferencingContext:
    return DifferencingContext(
        output_dir_a=str(tmp_path / "a"),
        output_dir_b=str(tmp_path / "b"),
        project_intent=project_intent,
        implementation=implementation,
        response_file_path=str(tmp_path / "diff-response.json"),
    )


@pytest.fixture
def default_profile() -> AgentProfile:
    return AgentProfile(name="test", provider="claude")


@pytest.fixture
def cli_profile() -> AgentProfile:
    return AgentProfile(
        name="test-cli",
        provider="cli",
        command="echo test",
    )


# ---------------------------------------------------------------------------
# AgentError
# ---------------------------------------------------------------------------


class TestAgentError:
    def test_is_exception(self):
        err = AgentError("something failed")
        assert isinstance(err, Exception)
        assert str(err) == "something failed"


# ---------------------------------------------------------------------------
# PromptTemplates & load_default_prompts
# ---------------------------------------------------------------------------


class TestPromptTemplates:
    def test_defaults(self):
        t = PromptTemplates()
        assert t.build == ""
        assert t.validate_template == ""
        assert t.plan == ""
        assert t.difference == ""

    def test_custom_values(self):
        t = PromptTemplates(build="custom build", validate_template="custom validate")
        assert t.build == "custom build"
        assert t.validate_template == "custom validate"

    def test_load_default_prompts(self):
        templates = load_default_prompts()
        assert isinstance(templates, PromptTemplates)
        # The bundled prompts should load (they exist in our package)
        assert len(templates.build) > 0
        assert len(templates.validate_template) > 0
        assert len(templates.plan) > 0
        assert len(templates.difference) > 0


# ---------------------------------------------------------------------------
# render_prompt
# ---------------------------------------------------------------------------


class TestRenderPrompt:
    def test_basic_rendering(self, build_ctx: BuildContext):
        template = "Project: {project}\nFeature: {feature}\nResponse: {response_file}"
        result = render_prompt(template, build_ctx)
        assert "A test project" in result
        assert "Build a test feature" in result
        assert "response.json" in result

    def test_previous_errors_rendering(self, build_ctx: BuildContext):
        build_ctx.previous_errors = ["Error 1", "Error 2"]
        template = "Do the thing\n{previous_errors}"
        result = render_prompt(template, build_ctx)
        assert "Error 1" in result
        assert "Error 2" in result
        assert "Previous Errors" in result

    def test_no_previous_errors(self, build_ctx: BuildContext):
        template = "Do the thing\n{previous_errors}"
        result = render_prompt(template, build_ctx)
        assert "Previous Errors" not in result

    def test_validations_rendering(self, build_ctx: BuildContext):
        template = "Validations: {validations}"
        result = render_prompt(template, build_ctx)
        assert "check-exists" in result


# ---------------------------------------------------------------------------
# render_differencing_prompt
# ---------------------------------------------------------------------------


class TestRenderDifferencingPrompt:
    def test_basic_rendering(self, diff_ctx: DifferencingContext):
        template = "Compare {output_dir_a} vs {output_dir_b} -> {response_file}"
        result = render_differencing_prompt(template, diff_ctx)
        assert str(diff_ctx.output_dir_a) in result
        assert str(diff_ctx.output_dir_b) in result
        assert "diff-response.json" in result


# ---------------------------------------------------------------------------
# AgentProfile
# ---------------------------------------------------------------------------


class TestAgentProfile:
    def test_defaults(self):
        p = AgentProfile(name="test", provider="claude")
        assert p.timeout == 3600.0
        assert p.retries == 3
        assert p.command == ""
        assert p.cli_args == []
        assert p.model_id is None
        assert p.prompt_templates is None
        assert p.sandbox_write_paths == []
        assert p.sandbox_read_paths == []

    def test_custom_values(self):
        p = AgentProfile(
            name="custom",
            provider="cli",
            command="my-tool",
            cli_args=["--flag"],
            timeout=60.0,
            retries=1,
            model_id="gpt-4",
            sandbox_write_paths=["/tmp/out"],
            sandbox_read_paths=["/tmp/in"],
        )
        assert p.timeout == 60.0
        assert p.retries == 1
        assert p.command == "my-tool"
        assert p.sandbox_write_paths == ["/tmp/out"]


# ---------------------------------------------------------------------------
# BuildContext
# ---------------------------------------------------------------------------


class TestBuildContext:
    def test_fields(self, build_ctx: BuildContext):
        assert build_ctx.intent.name == "test-feature"
        assert build_ctx.generation_id == "gen-123"
        assert build_ctx.dependency_names == ["dep-a"]
        assert build_ctx.previous_errors == []
        assert build_ctx.response_file_path.endswith("response.json")

    def test_defaults(self, project_intent: ProjectIntent, tmp_path: Path):
        ctx = BuildContext(
            intent=IntentFile(name="x"),
            output_dir=str(tmp_path),
            generation_id="g",
            project_intent=project_intent,
            response_file_path=str(tmp_path / "r.json"),
        )
        assert ctx.validations == []
        assert ctx.dependency_names == []
        assert ctx.previous_errors == []
        assert ctx.implementation is None


# ---------------------------------------------------------------------------
# DifferencingContext
# ---------------------------------------------------------------------------


class TestDifferencingContext:
    def test_fields(self, diff_ctx: DifferencingContext):
        assert "a" in diff_ctx.output_dir_a
        assert "b" in diff_ctx.output_dir_b
        assert diff_ctx.project_intent.name == "test-project"
        assert diff_ctx.implementation is not None

    def test_optional_implementation(self, project_intent: ProjectIntent, tmp_path: Path):
        ctx = DifferencingContext(
            output_dir_a=str(tmp_path / "a"),
            output_dir_b=str(tmp_path / "b"),
            project_intent=project_intent,
            response_file_path=str(tmp_path / "r.json"),
        )
        assert ctx.implementation is None


# ---------------------------------------------------------------------------
# Response types
# ---------------------------------------------------------------------------


class TestBuildResponse:
    def test_success(self):
        r = BuildResponse(status="success", summary="done", files_created=["a.py"])
        assert r.status == "success"
        assert r.files_created == ["a.py"]
        assert r.files_modified == []

    def test_failure(self):
        r = BuildResponse(status="failure", summary="oops")
        assert r.status == "failure"


class TestValidationResponse:
    def test_pass(self):
        r = ValidationResponse(name="test", status="pass", reason="all good")
        assert r.status == "pass"

    def test_fail(self):
        r = ValidationResponse(name="test", status="fail", reason="nope")
        assert r.status == "fail"


class TestDimensionResult:
    def test_fields(self):
        d = DimensionResult(name="public_api", status="pass", rationale="matches")
        assert d.name == "public_api"
        assert d.status == "pass"


class TestDifferencingResponse:
    def test_equivalent(self):
        r = DifferencingResponse(
            status="equivalent",
            dimensions=[
                DimensionResult(name="api", status="pass", rationale="ok"),
            ],
            summary="all good",
        )
        assert r.status == "equivalent"
        assert len(r.dimensions) == 1

    def test_divergent(self):
        r = DifferencingResponse(status="divergent", summary="differs")
        assert r.dimensions == []


# ---------------------------------------------------------------------------
# MockAgent
# ---------------------------------------------------------------------------


class TestMockAgent:
    def test_default_responses(self):
        agent = MockAgent()
        assert agent.get_name() == "mock"
        assert agent.get_type() == "mock"

    def test_custom_name(self):
        agent = MockAgent(name="custom-mock")
        assert agent.get_name() == "custom-mock"

    def test_build_records_call(self, build_ctx: BuildContext):
        agent = MockAgent()
        resp = agent.build(build_ctx)
        assert resp.status == "success"
        assert len(agent.build_calls) == 1
        assert agent.build_calls[0] is build_ctx

    def test_validate_records_call(
        self, build_ctx: BuildContext, validation_file: ValidationFile
    ):
        agent = MockAgent()
        resp = agent.validate(build_ctx, validation_file)
        assert resp.status == "pass"
        assert len(agent.validate_calls) == 1
        assert agent.validate_calls[0] == (build_ctx, validation_file)

    def test_difference_records_call(self, diff_ctx: DifferencingContext):
        agent = MockAgent()
        resp = agent.difference(diff_ctx)
        assert resp.status == "equivalent"
        assert len(agent.difference_calls) == 1
        assert agent.difference_calls[0] is diff_ctx

    def test_plan_records_call(self, build_ctx: BuildContext):
        agent = MockAgent()
        agent.plan(build_ctx)
        assert len(agent.plan_calls) == 1
        assert agent.plan_calls[0] is build_ctx

    def test_custom_build_response(self, build_ctx: BuildContext):
        custom_resp = BuildResponse(
            status="failure", summary="custom fail", files_created=["x.py"]
        )
        agent = MockAgent(build_response=custom_resp)
        resp = agent.build(build_ctx)
        assert resp.status == "failure"
        assert resp.summary == "custom fail"

    def test_custom_validation_response(
        self, build_ctx: BuildContext, validation_file: ValidationFile
    ):
        custom_resp = ValidationResponse(
            name="custom", status="fail", reason="custom reason"
        )
        agent = MockAgent(validation_response=custom_resp)
        resp = agent.validate(build_ctx, validation_file)
        assert resp.status == "fail"
        assert resp.name == "custom"

    def test_custom_differencing_response(self, diff_ctx: DifferencingContext):
        custom_resp = DifferencingResponse(
            status="divergent",
            dimensions=[
                DimensionResult(name="api", status="fail", rationale="mismatch"),
            ],
            summary="divergent",
        )
        agent = MockAgent(differencing_response=custom_resp)
        resp = agent.difference(diff_ctx)
        assert resp.status == "divergent"
        assert len(resp.dimensions) == 1

    def test_is_agent(self):
        agent = MockAgent()
        assert isinstance(agent, Agent)


# ---------------------------------------------------------------------------
# CLIAgent
# ---------------------------------------------------------------------------


class TestCLIAgent:
    def test_get_name_and_type(self, cli_profile: AgentProfile):
        agent = CLIAgent(cli_profile)
        assert agent.get_name() == "test-cli"
        assert agent.get_type() == "cli"

    def test_is_agent(self, cli_profile: AgentProfile):
        assert isinstance(CLIAgent(cli_profile), Agent)

    def test_build_reads_response_file(
        self, tmp_path: Path, project_intent: ProjectIntent
    ):
        response_path = str(tmp_path / "response.json")
        output_dir = str(tmp_path / "output")
        os.makedirs(output_dir, exist_ok=True)

        # Write a script that creates the response file
        script = tmp_path / "agent.sh"
        script.write_text(
            f'#!/bin/bash\ncat > {response_path} << \'RESP\'\n'
            '{"status": "success", "summary": "built", "files_created": ["main.py"], "files_modified": []}\n'
            "RESP\n"
        )
        script.chmod(0o755)

        profile = AgentProfile(
            name="test-cli",
            provider="cli",
            command=str(script),
            prompt_templates=PromptTemplates(build="build {feature} -> {response_file}{previous_errors}"),
        )

        ctx = BuildContext(
            intent=IntentFile(name="test"),
            output_dir=output_dir,
            generation_id="g1",
            project_intent=project_intent,
            response_file_path=response_path,
        )

        agent = CLIAgent(profile)
        resp = agent.build(ctx)
        assert resp.status == "success"
        assert resp.files_created == ["main.py"]

    def test_build_raises_on_missing_response(
        self, tmp_path: Path, project_intent: ProjectIntent
    ):
        response_path = str(tmp_path / "response.json")
        # Script that does nothing (no response file written)
        script = tmp_path / "agent.sh"
        script.write_text("#!/bin/bash\ntrue\n")
        script.chmod(0o755)

        profile = AgentProfile(
            name="test-cli",
            provider="cli",
            command=str(script),
            prompt_templates=PromptTemplates(build="{project}{implementation}{feature}{validations}{response_file}{previous_errors}"),
        )
        ctx = BuildContext(
            intent=IntentFile(name="test"),
            output_dir=str(tmp_path / "output"),
            generation_id="g1",
            project_intent=project_intent,
            response_file_path=response_path,
        )

        agent = CLIAgent(profile)
        with pytest.raises(AgentError, match="Response file not found"):
            agent.build(ctx)

    def test_build_raises_on_invalid_json(
        self, tmp_path: Path, project_intent: ProjectIntent
    ):
        response_path = str(tmp_path / "response.json")
        # Write invalid JSON to response file
        script = tmp_path / "agent.sh"
        script.write_text(
            f"#!/bin/bash\necho 'not json' > {response_path}\n"
        )
        script.chmod(0o755)

        profile = AgentProfile(
            name="test-cli",
            provider="cli",
            command=str(script),
            prompt_templates=PromptTemplates(build="{project}{implementation}{feature}{validations}{response_file}{previous_errors}"),
        )
        ctx = BuildContext(
            intent=IntentFile(name="test"),
            output_dir=str(tmp_path / "output"),
            generation_id="g1",
            project_intent=project_intent,
            response_file_path=response_path,
        )

        agent = CLIAgent(profile)
        with pytest.raises(AgentError, match="invalid JSON"):
            agent.build(ctx)

    def test_raises_on_no_command(self, project_intent: ProjectIntent, tmp_path: Path):
        profile = AgentProfile(name="test", provider="cli", command="")
        ctx = BuildContext(
            intent=IntentFile(name="test"),
            output_dir=str(tmp_path),
            generation_id="g1",
            project_intent=project_intent,
            response_file_path=str(tmp_path / "r.json"),
        )
        agent = CLIAgent(profile)
        with pytest.raises(AgentError, match="requires a command"):
            agent.build(ctx)

    def test_raises_on_command_failure(
        self, tmp_path: Path, project_intent: ProjectIntent
    ):
        profile = AgentProfile(
            name="test-cli",
            provider="cli",
            command="false",
            prompt_templates=PromptTemplates(build="{project}{implementation}{feature}{validations}{response_file}{previous_errors}"),
        )
        ctx = BuildContext(
            intent=IntentFile(name="test"),
            output_dir=str(tmp_path),
            generation_id="g1",
            project_intent=project_intent,
            response_file_path=str(tmp_path / "r.json"),
        )
        agent = CLIAgent(profile)
        with pytest.raises(AgentError, match="failed"):
            agent.build(ctx)

    def test_log_callback(self, cli_profile: AgentProfile, build_ctx: BuildContext):
        logs: list[str] = []
        # Use a command that will fail so we don't need a valid response file
        profile = AgentProfile(
            name="test",
            provider="cli",
            command="echo hello",
            prompt_templates=PromptTemplates(build="{project}{implementation}{feature}{validations}{response_file}{previous_errors}"),
        )
        agent = CLIAgent(profile, log=logs.append)
        # It will fail reading the response file, but the log should still be called
        with pytest.raises(AgentError):
            agent.build(build_ctx)
        assert any("agent:" in msg for msg in logs)


# ---------------------------------------------------------------------------
# ClaudeAgent
# ---------------------------------------------------------------------------


class TestClaudeAgent:
    def test_get_name_and_type(self, default_profile: AgentProfile):
        agent = ClaudeAgent(default_profile)
        assert agent.get_name() == "test"
        assert agent.get_type() == "claude"

    def test_is_agent(self, default_profile: AgentProfile):
        assert isinstance(ClaudeAgent(default_profile), Agent)

    def test_build_cmd_includes_required_flags(self, default_profile: AgentProfile):
        agent = ClaudeAgent(default_profile)
        cmd = agent._build_cmd("test prompt")
        assert "claude" in cmd
        assert "-p" in cmd
        assert "--verbose" in cmd
        assert "--output-format" in cmd
        assert "stream-json" in cmd
        assert "--dangerously-skip-permissions" in cmd

    def test_build_cmd_includes_model(self):
        profile = AgentProfile(name="test", provider="claude", model_id="sonnet")
        agent = ClaudeAgent(profile)
        cmd = agent._build_cmd("test")
        assert "--model" in cmd
        assert "sonnet" in cmd

    def test_build_cmd_includes_cli_args(self):
        profile = AgentProfile(
            name="test", provider="claude", cli_args=["--max-turns", "5"]
        )
        agent = ClaudeAgent(profile)
        cmd = agent._build_cmd("test")
        assert "--max-turns" in cmd
        assert "5" in cmd

    def test_sandbox_settings_written(self, tmp_path: Path):
        profile = AgentProfile(
            name="test",
            provider="claude",
            sandbox_write_paths=["/tmp/out"],
            sandbox_read_paths=["/tmp/in"],
        )
        agent = ClaudeAgent(profile)
        cwd = str(tmp_path)
        settings_path = agent._write_sandbox_settings(cwd)

        assert settings_path is not None
        assert os.path.exists(settings_path)

        with open(settings_path) as f:
            settings = json.load(f)

        assert settings["sandbox"]["enabled"] is True
        assert settings["sandbox"]["write_paths"] == ["/tmp/out"]
        assert settings["sandbox"]["read_paths"] == ["/tmp/in"]

        # Cleanup
        os.remove(settings_path)

    def test_sandbox_settings_not_written_when_empty(self, tmp_path: Path):
        profile = AgentProfile(name="test", provider="claude")
        agent = ClaudeAgent(profile)
        result = agent._write_sandbox_settings(str(tmp_path))
        assert result is None

    def test_build_synthesizes_response_on_missing_file(
        self, default_profile: AgentProfile, tmp_path: Path
    ):
        """When response file is missing but build succeeded, synthesize from output."""
        agent = ClaudeAgent(default_profile)
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        (output_dir / "main.py").write_text("print('hello')")
        (output_dir / "sub").mkdir()
        (output_dir / "sub" / "util.py").write_text("x = 1")

        resp = agent._read_build_response(
            str(tmp_path / "nonexistent.json"), str(output_dir)
        )
        assert resp.status == "success"
        assert "main.py" in resp.files_created
        assert os.path.join("sub", "util.py") in resp.files_created

    def test_build_reads_response_file(
        self, default_profile: AgentProfile, tmp_path: Path
    ):
        agent = ClaudeAgent(default_profile)
        response_path = tmp_path / "response.json"
        response_path.write_text(json.dumps({
            "status": "success",
            "summary": "done",
            "files_created": ["a.py"],
            "files_modified": [],
        }))
        resp = agent._read_build_response(str(response_path), str(tmp_path))
        assert resp.status == "success"
        assert resp.files_created == ["a.py"]


# ---------------------------------------------------------------------------
# create_from_profile
# ---------------------------------------------------------------------------


class TestCreateFromProfile:
    def test_claude_provider(self):
        profile = AgentProfile(name="test", provider="claude")
        agent = create_from_profile(profile)
        assert isinstance(agent, ClaudeAgent)

    def test_cli_provider(self):
        profile = AgentProfile(name="test", provider="cli", command="echo")
        agent = create_from_profile(profile)
        assert isinstance(agent, CLIAgent)

    def test_unknown_provider_raises(self):
        profile = AgentProfile(name="test", provider="unknown")
        with pytest.raises(AgentError, match="Unknown agent provider"):
            create_from_profile(profile)

    def test_case_insensitive_provider(self):
        profile = AgentProfile(name="test", provider="Claude")
        agent = create_from_profile(profile)
        assert isinstance(agent, ClaudeAgent)

    def test_passes_log_callback(self):
        logs: list[str] = []
        profile = AgentProfile(name="test", provider="claude")
        agent = create_from_profile(profile, log=logs.append)
        assert isinstance(agent, ClaudeAgent)
        # Log callback should be wired through
        assert agent._log is not None
