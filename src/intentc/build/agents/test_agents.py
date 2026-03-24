from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from intentc.core.types import (
    IntentFile,
    ProjectIntent,
    Implementation,
    Validation,
    ValidationFile,
    Severity,
)
from intentc.build.agents.types import (
    AgentError,
    AgentProfile,
    BuildContext,
    BuildResponse,
    DifferencingContext,
    DifferencingResponse,
    DimensionResult,
    PromptTemplates,
    ValidationResponse,
)
from intentc.build.agents.prompts import (
    load_default_prompts,
    render_differencing_prompt,
    render_prompt,
)
from intentc.build.agents.base import Agent, CLIAgent, create_from_profile
from intentc.build.agents.claude_agent import ClaudeAgent
from intentc.build.agents.mock_agent import MockAgent


def _make_ctx(tmp_path: Path) -> BuildContext:
    """Helper to create a minimal BuildContext for testing."""
    return BuildContext(
        intent=IntentFile(name="test-feature", body="Test feature body"),
        validations=[],
        output_dir=str(tmp_path),
        generation_id="gen-123",
        dependency_names=[],
        project_intent=ProjectIntent(name="test-project", body="Project body"),
        implementation=Implementation(name="default", body="Impl body"),
        response_file_path=str(tmp_path / "response.json"),
    )


def _make_diff_ctx(tmp_path: Path) -> DifferencingContext:
    """Helper to create a minimal DifferencingContext for testing."""
    return DifferencingContext(
        output_dir_a=str(tmp_path / "a"),
        output_dir_b=str(tmp_path / "b"),
        project_intent=ProjectIntent(name="test-project", body="Project body"),
        response_file_path=str(tmp_path / "diff_response.json"),
        implementation=Implementation(name="default", body="Impl body"),
    )


class TestAgentError:
    def test_is_exception(self):
        err = AgentError("something went wrong")
        assert isinstance(err, Exception)
        assert str(err) == "something went wrong"


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
            command="myagent",
            cli_args=["--flag"],
            timeout=60.0,
            retries=1,
            model_id="gpt-4",
            sandbox_write_paths=["/tmp/out"],
            sandbox_read_paths=["/tmp/in"],
        )
        assert p.name == "custom"
        assert p.provider == "cli"
        assert p.command == "myagent"
        assert p.timeout == 60.0
        assert p.retries == 1
        assert p.model_id == "gpt-4"


class TestPromptTemplates:
    def test_defaults(self):
        pt = PromptTemplates()
        assert pt.build == ""
        assert pt.validate_template == ""
        assert pt.plan == ""
        assert pt.difference == ""

    def test_custom(self):
        pt = PromptTemplates(build="Build: {feature}", validate_template="Validate: {validation}")
        assert pt.build == "Build: {feature}"
        assert pt.validate_template == "Validate: {validation}"


class TestBuildContext:
    def test_fields(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        assert ctx.intent.name == "test-feature"
        assert ctx.output_dir == str(tmp_path)
        assert ctx.generation_id == "gen-123"
        assert ctx.project_intent.name == "test-project"
        assert ctx.implementation is not None
        assert ctx.response_file_path == str(tmp_path / "response.json")
        assert ctx.dependency_names == []
        assert ctx.validations == []


class TestDifferencingContext:
    def test_fields(self, tmp_path):
        ctx = _make_diff_ctx(tmp_path)
        assert ctx.output_dir_a == str(tmp_path / "a")
        assert ctx.output_dir_b == str(tmp_path / "b")
        assert ctx.project_intent.name == "test-project"
        assert ctx.implementation is not None

    def test_optional_implementation(self, tmp_path):
        ctx = DifferencingContext(
            output_dir_a="/a",
            output_dir_b="/b",
            project_intent=ProjectIntent(name="p"),
            response_file_path="/resp.json",
        )
        assert ctx.implementation is None


class TestResponseTypes:
    def test_build_response(self):
        r = BuildResponse(status="success", summary="done")
        assert r.status == "success"
        assert r.files_created == []
        assert r.files_modified == []

    def test_validation_response(self):
        r = ValidationResponse(name="check", status="pass", reason="ok")
        assert r.status == "pass"

    def test_differencing_response(self):
        dim = DimensionResult(name="public_api", status="pass", rationale="same")
        r = DifferencingResponse(status="equivalent", dimensions=[dim], summary="all good")
        assert r.status == "equivalent"
        assert len(r.dimensions) == 1
        assert r.dimensions[0].name == "public_api"


class TestRenderPrompt:
    def test_render_build_prompt(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        template = "Project: {project}\nFeature: {feature}\nResponse: {response_file}"
        result = render_prompt(template, ctx)
        assert "Project body" in result
        assert "Test feature body" in result
        assert str(tmp_path / "response.json") in result

    def test_render_differencing_prompt(self, tmp_path):
        ctx = _make_diff_ctx(tmp_path)
        template = "Compare {output_dir_a} vs {output_dir_b}, response: {response_file}"
        result = render_differencing_prompt(template, ctx)
        assert str(tmp_path / "a") in result
        assert str(tmp_path / "b") in result


class TestLoadDefaultPrompts:
    def test_loads_from_intent_dir(self, tmp_path):
        prompts_dir = tmp_path / "intent" / "build" / "agents" / "prompts"
        prompts_dir.mkdir(parents=True)
        (prompts_dir / "build.prompt").write_text("build template")
        (prompts_dir / "validate.prompt").write_text("validate template")
        (prompts_dir / "plan.prompt").write_text("plan template")

        diff_dir = tmp_path / "intent" / "differencing" / "prompts"
        diff_dir.mkdir(parents=True)
        (diff_dir / "difference.prompt").write_text("diff template")

        with patch("intentc.build.agents.prompts.Path") as mock_path:
            mock_path.cwd.return_value = tmp_path
            # Need to allow Path / operations to work normally
            mock_path.__truediv__ = Path.__truediv__
            # Just test that load_default_prompts runs; for a proper test
            # we'd need to fully mock Path operations
        # Use monkeypatch instead
        import intentc.build.agents.prompts as prompts_mod
        original = Path.cwd
        try:
            Path.cwd = staticmethod(lambda: tmp_path)
            result = load_default_prompts()
            assert result.build == "build template"
            assert result.validate_template == "validate template"
            assert result.plan == "plan template"
            assert result.difference == "diff template"
        finally:
            Path.cwd = original

    def test_missing_files_return_empty(self, tmp_path):
        import intentc.build.agents.prompts as prompts_mod
        original = Path.cwd
        try:
            Path.cwd = staticmethod(lambda: tmp_path)
            result = load_default_prompts()
            assert result.build == ""
            assert result.validate_template == ""
            assert result.plan == ""
            assert result.difference == ""
        finally:
            Path.cwd = original


class TestMockAgent:
    def test_default_responses(self):
        agent = MockAgent()
        assert agent.get_name() == "mock"
        assert agent.get_type() == "mock"

    def test_records_build_calls(self, tmp_path):
        agent = MockAgent()
        ctx = _make_ctx(tmp_path)
        result = agent.build(ctx)
        assert result.status == "success"
        assert len(agent.build_calls) == 1
        assert agent.build_calls[0] is ctx

    def test_records_validate_calls(self, tmp_path):
        agent = MockAgent()
        ctx = _make_ctx(tmp_path)
        v = Validation(name="check1", args={"rubric": "test"})
        result = agent.validate(ctx, v)
        assert result.status == "pass"
        assert len(agent.validate_calls) == 1
        assert agent.validate_calls[0] == (ctx, v)

    def test_records_difference_calls(self, tmp_path):
        agent = MockAgent()
        ctx = _make_diff_ctx(tmp_path)
        result = agent.difference(ctx)
        assert result.status == "equivalent"
        assert len(agent.difference_calls) == 1

    def test_records_plan_calls(self, tmp_path):
        agent = MockAgent()
        ctx = _make_ctx(tmp_path)
        agent.plan(ctx)
        assert len(agent.plan_calls) == 1

    def test_custom_responses(self, tmp_path):
        custom_build = BuildResponse(status="failure", summary="custom fail")
        custom_val = ValidationResponse(name="x", status="fail", reason="nope")
        custom_diff = DifferencingResponse(status="divergent", summary="different")
        agent = MockAgent(
            name="custom",
            build_response=custom_build,
            validation_response=custom_val,
            differencing_response=custom_diff,
        )
        ctx = _make_ctx(tmp_path)
        assert agent.build(ctx).status == "failure"
        assert agent.validate(ctx, Validation(name="v")).status == "fail"
        assert agent.difference(_make_diff_ctx(tmp_path)).status == "divergent"
        assert agent.get_name() == "custom"


class TestCreateFromProfile:
    def test_claude_provider(self):
        profile = AgentProfile(name="claude-agent", provider="claude")
        agent = create_from_profile(profile)
        assert isinstance(agent, ClaudeAgent)
        assert agent.get_name() == "claude-agent"
        assert agent.get_type() == "claude"

    def test_cli_provider(self):
        profile = AgentProfile(name="cli-agent", provider="cli", command="echo")
        agent = create_from_profile(profile)
        assert isinstance(agent, CLIAgent)
        assert agent.get_name() == "cli-agent"
        assert agent.get_type() == "cli"

    def test_unknown_provider_raises(self):
        profile = AgentProfile(name="bad", provider="unknown")
        with pytest.raises(AgentError, match="Unknown agent provider"):
            create_from_profile(profile)


class TestCLIAgent:
    def test_build_reads_response_file(self, tmp_path):
        response_data = {
            "status": "success",
            "summary": "built it",
            "files_created": ["main.py"],
            "files_modified": [],
        }
        response_path = tmp_path / "response.json"
        response_path.write_text(json.dumps(response_data))

        profile = AgentProfile(
            name="test-cli",
            provider="cli",
            command="echo",
            prompt_templates=PromptTemplates(build="{feature}"),
        )
        agent = CLIAgent(profile)
        ctx = _make_ctx(tmp_path)
        ctx.response_file_path = str(response_path)

        with patch("intentc.build.agents.base.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = agent.build(ctx)

        assert result.status == "success"
        assert result.summary == "built it"
        assert result.files_created == ["main.py"]

    def test_build_missing_response_raises(self, tmp_path):
        profile = AgentProfile(
            name="test-cli",
            provider="cli",
            command="echo",
            prompt_templates=PromptTemplates(build="{feature}"),
        )
        agent = CLIAgent(profile)
        ctx = _make_ctx(tmp_path)

        with patch("intentc.build.agents.base.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with pytest.raises(AgentError, match="Response file not found"):
                agent.build(ctx)

    def test_build_invalid_json_raises(self, tmp_path):
        response_path = tmp_path / "response.json"
        response_path.write_text("not json")

        profile = AgentProfile(
            name="test-cli",
            provider="cli",
            command="echo",
            prompt_templates=PromptTemplates(build="{feature}"),
        )
        agent = CLIAgent(profile)
        ctx = _make_ctx(tmp_path)
        ctx.response_file_path = str(response_path)

        with patch("intentc.build.agents.base.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with pytest.raises(AgentError, match="Invalid JSON"):
                agent.build(ctx)

    def test_no_command_raises(self, tmp_path):
        profile = AgentProfile(name="no-cmd", provider="cli", prompt_templates=PromptTemplates(build="{feature}"))
        agent = CLIAgent(profile)
        ctx = _make_ctx(tmp_path)
        with pytest.raises(AgentError, match="requires a command"):
            agent.build(ctx)

    def test_command_failure_raises(self, tmp_path):
        profile = AgentProfile(
            name="fail",
            provider="cli",
            command="echo",
            prompt_templates=PromptTemplates(build="{feature}"),
        )
        agent = CLIAgent(profile)
        ctx = _make_ctx(tmp_path)

        with patch("intentc.build.agents.base.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="error output")
            with pytest.raises(AgentError, match="failed"):
                agent.build(ctx)

    def test_uses_list_args(self, tmp_path):
        """Verify subprocess is called with a list, not a string (shell injection protection)."""
        response_path = tmp_path / "response.json"
        response_path.write_text(json.dumps({"status": "success", "summary": "ok", "files_created": [], "files_modified": []}))

        profile = AgentProfile(
            name="test",
            provider="cli",
            command="myagent",
            cli_args=["--verbose"],
            prompt_templates=PromptTemplates(build="{feature}"),
        )
        agent = CLIAgent(profile)
        ctx = _make_ctx(tmp_path)
        ctx.response_file_path = str(response_path)

        with patch("intentc.build.agents.base.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            agent.build(ctx)
            call_args = mock_run.call_args
            cmd = call_args[0][0]
            assert isinstance(cmd, list)
            assert cmd[0] == "myagent"
            assert "--verbose" in cmd


class TestClaudeAgent:
    def test_get_type(self):
        profile = AgentProfile(name="claude-test", provider="claude")
        agent = ClaudeAgent(profile)
        assert agent.get_type() == "claude"
        assert agent.get_name() == "claude-test"

    def test_build_uses_claude_flags(self, tmp_path):
        response_path = tmp_path / "response.json"
        response_path.write_text(json.dumps({
            "status": "success", "summary": "done",
            "files_created": [], "files_modified": [],
        }))

        profile = AgentProfile(
            name="claude-test",
            provider="claude",
            model_id="sonnet",
            prompt_templates=PromptTemplates(build="{feature}"),
        )
        agent = ClaudeAgent(profile)
        ctx = _make_ctx(tmp_path)
        ctx.response_file_path = str(response_path)

        with patch("intentc.build.agents.claude_agent.subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.stdout = iter([])
            mock_proc.stderr = MagicMock()
            mock_proc.stderr.read.return_value = ""
            mock_proc.returncode = 0
            mock_proc.wait.return_value = 0
            mock_popen.return_value = mock_proc

            result = agent.build(ctx)

            cmd = mock_popen.call_args[0][0]
            assert cmd[0] == "claude"
            assert "-p" in cmd
            assert "--output-format" in cmd
            assert "stream-json" in cmd
            assert "--dangerously-skip-permissions" in cmd
            assert "--model" in cmd
            assert "sonnet" in cmd

        assert result.status == "success"

    def test_sandbox_settings(self, tmp_path):
        profile = AgentProfile(
            name="sandbox-test",
            provider="claude",
            sandbox_write_paths=[str(tmp_path / "out")],
            sandbox_read_paths=[str(tmp_path / "in")],
            prompt_templates=PromptTemplates(build="{feature}"),
        )
        agent = ClaudeAgent(profile)
        ctx = _make_ctx(tmp_path)

        response_path = tmp_path / "response.json"
        response_path.write_text(json.dumps({
            "status": "success", "summary": "done",
            "files_created": [], "files_modified": [],
        }))
        ctx.response_file_path = str(response_path)

        with patch("intentc.build.agents.claude_agent.subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.stdout = iter([])
            mock_proc.stderr = MagicMock()
            mock_proc.stderr.read.return_value = ""
            mock_proc.returncode = 0
            mock_proc.wait.return_value = 0
            mock_popen.return_value = mock_proc

            agent.build(ctx)

        # Settings file should have been created and then cleaned up
        settings_path = tmp_path / ".claude" / "settings.local.json"
        assert not settings_path.exists(), "Settings file should be cleaned up"


class TestAgentInterface:
    """Verify that Agent is an abstract base class with the right methods."""

    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            Agent()

    def test_required_methods(self):
        methods = {"build", "validate", "difference", "plan", "get_name", "get_type"}
        abstract_methods = set(Agent.__abstractmethods__)
        assert methods == abstract_methods


class TestImports:
    """Verify that all expected types are importable from the agents package."""

    def test_package_exports(self):
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

    def test_build_package_reexports(self):
        from intentc.build import (
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
