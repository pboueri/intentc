"""Tests for the agent module."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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
    MockCall,
    PromptTemplates,
    ValidationResponse,
    create_from_profile,
    load_default_prompts,
    render_differencing_prompt,
    render_prompt,
)
from intentc.core.models import (
    Implementation,
    IntentFile,
    ProjectIntent,
    Validation,
    ValidationFile,
    ValidationType,
    Severity,
)


# --- Fixtures ---


@pytest.fixture
def intent_file():
    return IntentFile(name="test-feature", body="Build a test feature")


@pytest.fixture
def project_intent():
    return ProjectIntent(name="test-project", body="A test project")


@pytest.fixture
def implementation():
    return Implementation(name="default", body="Python implementation")


@pytest.fixture
def build_ctx(intent_file, project_intent, implementation, tmp_path):
    return BuildContext(
        intent=intent_file,
        output_dir=str(tmp_path / "output"),
        generation_id="gen-123",
        project_intent=project_intent,
        implementation=implementation,
        response_file_path=str(tmp_path / "response.json"),
    )


@pytest.fixture
def diff_ctx(project_intent, tmp_path):
    return DifferencingContext(
        output_dir_a=str(tmp_path / "a"),
        output_dir_b=str(tmp_path / "b"),
        project_intent=project_intent,
        response_file_path=str(tmp_path / "diff_response.json"),
    )


@pytest.fixture
def claude_profile():
    return AgentProfile(
        name="test-claude",
        provider="claude",
        prompt_templates=PromptTemplates(
            build="Build: {feature}",
            validate_template="Validate: {validation}",
            plan="Plan: {feature}",
            difference="Diff: {output_dir_a} vs {output_dir_b}",
        ),
    )


@pytest.fixture
def cli_profile():
    return AgentProfile(
        name="test-cli",
        provider="cli",
        command="echo",
        prompt_templates=PromptTemplates(
            build="Build: {feature}",
            validate_template="Validate: {validation}",
            plan="Plan: {feature}",
            difference="Diff: {output_dir_a} vs {output_dir_b}",
        ),
    )


# --- AgentError ---


class TestAgentError:
    def test_is_exception(self):
        err = AgentError("something broke")
        assert isinstance(err, Exception)
        assert str(err) == "something broke"


# --- AgentProfile ---


class TestAgentProfile:
    def test_defaults(self):
        profile = AgentProfile(name="default", provider="claude")
        assert profile.timeout == 3600.0
        assert profile.retries == 3
        assert profile.command == ""
        assert profile.cli_args == []
        assert profile.model_id is None
        assert profile.prompt_templates is None
        assert profile.sandbox_write_paths == []
        assert profile.sandbox_read_paths == []

    def test_custom_values(self):
        profile = AgentProfile(
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
        assert profile.timeout == 60.0
        assert profile.retries == 1
        assert profile.model_id == "gpt-4"


# --- PromptTemplates ---


class TestPromptTemplates:
    def test_defaults_empty(self):
        pt = PromptTemplates()
        assert pt.build == ""
        assert pt.validate_template == ""
        assert pt.plan == ""
        assert pt.difference == ""

    def test_custom_templates(self):
        pt = PromptTemplates(build="custom build", validate_template="custom validate")
        assert pt.build == "custom build"
        assert pt.validate_template == "custom validate"


# --- load_default_prompts ---


class TestLoadDefaultPrompts:
    def test_loads_from_intent_dir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        prompts_dir = tmp_path / "intent" / "build" / "agents" / "prompts"
        prompts_dir.mkdir(parents=True)
        (prompts_dir / "build.prompt").write_text("build template")
        (prompts_dir / "validate.prompt").write_text("validate template")
        (prompts_dir / "plan.prompt").write_text("plan template")

        diff_dir = tmp_path / "intent" / "differencing" / "prompts"
        diff_dir.mkdir(parents=True)
        (diff_dir / "difference.prompt").write_text("diff template")

        templates = load_default_prompts()
        assert templates.build == "build template"
        assert templates.validate_template == "validate template"
        assert templates.plan == "plan template"
        assert templates.difference == "diff template"

    def test_missing_files_return_empty(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        templates = load_default_prompts()
        assert templates.build == ""
        assert templates.validate_template == ""
        assert templates.plan == ""
        assert templates.difference == ""


# --- render_prompt ---


class TestRenderPrompt:
    def test_basic_render(self, build_ctx):
        template = "Project: {project}\nFeature: {feature}\nImpl: {implementation}"
        result = render_prompt(template, build_ctx)
        assert "A test project" in result
        assert "Build a test feature" in result
        assert "Python implementation" in result

    def test_response_file_placeholder(self, build_ctx):
        template = "Write to: {response_file}"
        result = render_prompt(template, build_ctx)
        assert "response.json" in result

    def test_previous_errors_empty(self, build_ctx):
        template = "Errors: {previous_errors}"
        result = render_prompt(template, build_ctx)
        assert "Previous Errors" not in result

    def test_previous_errors_rendered(self, build_ctx):
        build_ctx.previous_errors = ["Error 1", "Error 2"]
        template = "Errors: {previous_errors}"
        result = render_prompt(template, build_ctx)
        assert "- Error 1" in result
        assert "- Error 2" in result
        assert "Previous Errors" in result

    def test_validations_rendered(self, build_ctx):
        vf = ValidationFile(
            target="test",
            validations=[Validation(name="v1", type=ValidationType.AGENT_VALIDATION)],
        )
        build_ctx.validations = [vf]
        template = "Validations: {validations}"
        result = render_prompt(template, build_ctx)
        assert "v1" in result


# --- render_differencing_prompt ---


class TestRenderDifferencingPrompt:
    def test_basic_render(self, diff_ctx):
        template = "A: {output_dir_a}, B: {output_dir_b}, Project: {project}"
        result = render_differencing_prompt(template, diff_ctx)
        assert str(diff_ctx.output_dir_a) in result
        assert str(diff_ctx.output_dir_b) in result
        assert "A test project" in result


# --- Response types ---


class TestBuildResponse:
    def test_success(self):
        r = BuildResponse(status="success", summary="done", files_created=["a.py"])
        assert r.status == "success"
        assert r.files_created == ["a.py"]
        assert r.files_modified == []

    def test_from_json(self):
        data = {"status": "failure", "summary": "oops"}
        r = BuildResponse(**data)
        assert r.status == "failure"


class TestValidationResponse:
    def test_pass(self):
        r = ValidationResponse(name="check1", status="pass", reason="ok")
        assert r.status == "pass"


class TestDifferencingResponse:
    def test_equivalent(self):
        dim = DimensionResult(name="api", status="pass", rationale="same")
        r = DifferencingResponse(status="equivalent", dimensions=[dim], summary="ok")
        assert r.status == "equivalent"
        assert len(r.dimensions) == 1


# --- BuildContext ---


class TestBuildContext:
    def test_defaults(self, intent_file):
        ctx = BuildContext(intent=intent_file)
        assert ctx.validations == []
        assert ctx.output_dir == ""
        assert ctx.generation_id == ""
        assert ctx.dependency_names == []
        assert ctx.project_intent is None
        assert ctx.implementation is None
        assert ctx.response_file_path == ""
        assert ctx.previous_errors == []


# --- DifferencingContext ---


class TestDifferencingContext:
    def test_defaults(self):
        ctx = DifferencingContext()
        assert ctx.output_dir_a == ""
        assert ctx.output_dir_b == ""
        assert ctx.project_intent is None
        assert ctx.implementation is None
        assert ctx.response_file_path == ""


# --- MockAgent ---


class TestMockAgent:
    def test_get_name_and_type(self):
        agent = MockAgent(name="my-mock")
        assert agent.get_name() == "my-mock"
        assert agent.get_type() == "mock"

    def test_isinstance_agent(self):
        agent = MockAgent()
        assert isinstance(agent, Agent)

    def test_build_records_call(self, build_ctx):
        agent = MockAgent()
        resp = agent.build(build_ctx)
        assert resp.status == "success"
        assert len(agent.calls) == 1
        assert agent.calls[0].method == "build"
        assert agent.calls[0].ctx is build_ctx

    def test_validate_records_call(self, build_ctx):
        agent = MockAgent()
        v = Validation(name="check1")
        resp = agent.validate(build_ctx, v)
        assert resp.status == "pass"
        assert len(agent.calls) == 1
        assert agent.calls[0].method == "validate"
        assert agent.calls[0].validation is v

    def test_difference_records_call(self, diff_ctx):
        agent = MockAgent()
        resp = agent.difference(diff_ctx)
        assert resp.status == "equivalent"
        assert len(agent.calls) == 1
        assert agent.calls[0].method == "difference"

    def test_plan_records_call(self, build_ctx):
        agent = MockAgent()
        agent.plan(build_ctx)
        assert len(agent.calls) == 1
        assert agent.calls[0].method == "plan"

    def test_custom_responses(self, build_ctx, diff_ctx):
        custom_build = BuildResponse(status="failure", summary="nope")
        custom_val = ValidationResponse(name="x", status="fail", reason="bad")
        custom_diff = DifferencingResponse(status="divergent", summary="different")
        agent = MockAgent(
            build_response=custom_build,
            validation_response=custom_val,
            differencing_response=custom_diff,
        )
        assert agent.build(build_ctx).status == "failure"
        assert agent.validate(build_ctx, Validation(name="x")).status == "fail"
        assert agent.difference(diff_ctx).status == "divergent"


# --- create_from_profile ---


class TestCreateFromProfile:
    def test_claude_provider(self, claude_profile):
        agent = create_from_profile(claude_profile)
        assert isinstance(agent, ClaudeAgent)
        assert agent.get_type() == "claude"

    def test_cli_provider(self, cli_profile):
        agent = create_from_profile(cli_profile)
        assert isinstance(agent, CLIAgent)
        assert agent.get_type() == "cli"

    def test_unknown_provider(self):
        profile = AgentProfile(name="bad", provider="unknown")
        with pytest.raises(AgentError, match="Unknown agent provider"):
            create_from_profile(profile)

    def test_log_callback_passed(self, claude_profile):
        log = MagicMock()
        agent = create_from_profile(claude_profile, log=log)
        assert isinstance(agent, ClaudeAgent)


# --- CLIAgent ---


class TestCLIAgent:
    def test_get_name_and_type(self, cli_profile):
        agent = CLIAgent(profile=cli_profile)
        assert agent.get_name() == "test-cli"
        assert agent.get_type() == "cli"

    def test_isinstance_agent(self, cli_profile):
        agent = CLIAgent(profile=cli_profile)
        assert isinstance(agent, Agent)

    def test_build_reads_response_file(self, cli_profile, build_ctx, tmp_path):
        response_data = {
            "status": "success",
            "summary": "built",
            "files_created": ["main.py"],
            "files_modified": [],
        }
        resp_path = tmp_path / "response.json"
        resp_path.write_text(json.dumps(response_data))
        build_ctx.response_file_path = str(resp_path)

        with patch("intentc.build.agents.cli_agent.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            agent = CLIAgent(profile=cli_profile)
            resp = agent.build(build_ctx)

        assert resp.status == "success"
        assert resp.files_created == ["main.py"]
        mock_run.assert_called_once()
        # Verify command is passed as a list (no shell injection)
        call_args = mock_run.call_args
        assert isinstance(call_args[0][0], list)

    def test_missing_command_raises(self, build_ctx):
        profile = AgentProfile(
            name="no-cmd",
            provider="cli",
            prompt_templates=PromptTemplates(build="test"),
        )
        agent = CLIAgent(profile=profile)
        with pytest.raises(AgentError, match="requires a command"):
            agent.build(build_ctx)

    def test_command_not_found_raises(self, cli_profile, build_ctx):
        cli_profile.command = "nonexistent-tool-xyz"
        agent = CLIAgent(profile=cli_profile)
        with pytest.raises(AgentError, match="not found"):
            agent.build(build_ctx)

    def test_missing_response_file_raises(self, cli_profile, build_ctx, tmp_path):
        build_ctx.response_file_path = str(tmp_path / "missing.json")
        with patch("intentc.build.agents.cli_agent.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            agent = CLIAgent(profile=cli_profile)
            with pytest.raises(AgentError, match="Response file not found"):
                agent.build(build_ctx)

    def test_invalid_json_raises(self, cli_profile, build_ctx, tmp_path):
        resp_path = tmp_path / "response.json"
        resp_path.write_text("not json")
        build_ctx.response_file_path = str(resp_path)
        with patch("intentc.build.agents.cli_agent.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            agent = CLIAgent(profile=cli_profile)
            with pytest.raises(AgentError, match="Invalid JSON"):
                agent.build(build_ctx)

    def test_nonzero_exit_raises(self, cli_profile, build_ctx):
        with patch("intentc.build.agents.cli_agent.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="error output")
            agent = CLIAgent(profile=cli_profile)
            with pytest.raises(AgentError, match="failed"):
                agent.build(build_ctx)

    def test_log_callback(self, cli_profile, build_ctx, tmp_path):
        log = MagicMock()
        resp_path = tmp_path / "response.json"
        resp_path.write_text(json.dumps({"status": "success", "summary": "ok"}))
        build_ctx.response_file_path = str(resp_path)

        with patch("intentc.build.agents.cli_agent.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            agent = CLIAgent(profile=cli_profile, log=log)
            agent.build(build_ctx)

        log.assert_called()
        assert any("agent:" in str(c) for c in log.call_args_list)

    def test_validate_reads_response(self, cli_profile, build_ctx, tmp_path):
        resp_data = {"name": "check1", "status": "pass", "reason": "ok"}
        resp_path = tmp_path / "response.json"
        resp_path.write_text(json.dumps(resp_data))
        build_ctx.response_file_path = str(resp_path)

        with patch("intentc.build.agents.cli_agent.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            agent = CLIAgent(profile=cli_profile)
            resp = agent.validate(build_ctx, Validation(name="check1"))

        assert resp.status == "pass"
        assert resp.name == "check1"

    def test_difference_reads_response(self, cli_profile, diff_ctx, tmp_path):
        resp_data = {
            "status": "equivalent",
            "dimensions": [{"name": "api", "status": "pass", "rationale": "same"}],
            "summary": "ok",
        }
        resp_path = tmp_path / "diff_response.json"
        resp_path.write_text(json.dumps(resp_data))
        diff_ctx.response_file_path = str(resp_path)

        with patch("intentc.build.agents.cli_agent.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            agent = CLIAgent(profile=cli_profile)
            resp = agent.difference(diff_ctx)

        assert resp.status == "equivalent"
        assert len(resp.dimensions) == 1


# --- ClaudeAgent ---


class TestClaudeAgent:
    def test_get_name_and_type(self, claude_profile):
        agent = ClaudeAgent(profile=claude_profile)
        assert agent.get_name() == "test-claude"
        assert agent.get_type() == "claude"

    def test_isinstance_agent(self, claude_profile):
        agent = ClaudeAgent(profile=claude_profile)
        assert isinstance(agent, Agent)

    def test_build_base_cmd_without_model(self, claude_profile):
        agent = ClaudeAgent(profile=claude_profile)
        cmd = agent._build_base_cmd()
        assert cmd == ["claude"]

    def test_build_base_cmd_with_model(self, claude_profile):
        claude_profile.model_id = "claude-sonnet-4-6"
        agent = ClaudeAgent(profile=claude_profile)
        cmd = agent._build_base_cmd()
        assert cmd == ["claude", "--model", "claude-sonnet-4-6"]

    def test_sandbox_settings_written(self, claude_profile, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        claude_profile.sandbox_write_paths = ["/tmp/out"]
        claude_profile.sandbox_read_paths = ["/tmp/in"]
        agent = ClaudeAgent(profile=claude_profile)
        path = agent._write_sandbox_settings()
        assert path is not None
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["sandbox"]["writable_paths"] == ["/tmp/out"]
        assert data["sandbox"]["readable_paths"] == ["/tmp/in"]
        # Cleanup
        agent._cleanup_sandbox_settings(path)
        assert not path.exists()

    def test_sandbox_settings_not_written_when_empty(self, claude_profile):
        agent = ClaudeAgent(profile=claude_profile)
        path = agent._write_sandbox_settings()
        assert path is None

    def test_synthesize_build_response(self, claude_profile, tmp_path):
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        (out_dir / "main.py").write_text("print('hi')")
        (out_dir / "sub").mkdir()
        (out_dir / "sub" / "helper.py").write_text("pass")

        agent = ClaudeAgent(profile=claude_profile)
        resp = agent._synthesize_build_response(str(out_dir))
        assert resp.status == "success"
        assert sorted(resp.files_created) == ["main.py", "sub/helper.py"]

    def test_synthesize_build_response_empty_dir(self, claude_profile, tmp_path):
        agent = ClaudeAgent(profile=claude_profile)
        resp = agent._synthesize_build_response(str(tmp_path / "nonexistent"))
        assert resp.status == "success"
        assert resp.files_created == []

    def test_build_with_response_file(self, claude_profile, build_ctx, tmp_path):
        resp_data = {"status": "success", "summary": "done", "files_created": ["a.py"]}
        resp_path = Path(build_ctx.response_file_path)
        resp_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(ClaudeAgent, "_run_non_interactive") as mock_run:
            # Simulate agent writing the response file
            def write_response(*args, **kwargs):
                resp_path.write_text(json.dumps(resp_data))
            mock_run.side_effect = write_response

            agent = ClaudeAgent(profile=claude_profile)
            resp = agent.build(build_ctx)

        assert resp.status == "success"
        assert resp.files_created == ["a.py"]

    def test_build_without_response_file_synthesizes(self, claude_profile, build_ctx, tmp_path):
        out_dir = Path(build_ctx.output_dir)
        out_dir.mkdir(parents=True)
        (out_dir / "generated.py").write_text("pass")

        with patch.object(ClaudeAgent, "_run_non_interactive"):
            agent = ClaudeAgent(profile=claude_profile)
            resp = agent.build(build_ctx)

        assert resp.status == "success"
        assert "generated.py" in resp.files_created

    def test_validate_missing_response_raises(self, claude_profile, build_ctx):
        with patch.object(ClaudeAgent, "_run_non_interactive"):
            agent = ClaudeAgent(profile=claude_profile)
            with pytest.raises(AgentError, match="Response file not found"):
                agent.validate(build_ctx, Validation(name="check"))

    def test_difference_missing_response_raises(self, claude_profile, diff_ctx):
        with patch.object(ClaudeAgent, "_run_non_interactive"):
            agent = ClaudeAgent(profile=claude_profile)
            with pytest.raises(AgentError, match="Response file not found"):
                agent.difference(diff_ctx)

    def test_cli_args_appended(self, claude_profile):
        claude_profile.cli_args = ["--max-tokens", "1000"]
        agent = ClaudeAgent(profile=claude_profile)
        # Verify the profile is stored and args would be appended
        assert agent._profile.cli_args == ["--max-tokens", "1000"]
