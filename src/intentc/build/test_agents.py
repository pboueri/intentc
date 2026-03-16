"""Tests for agent interface and implementations."""

from __future__ import annotations

import json
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
    MockAgent,
    PromptTemplates,
    ValidationResponse,
    create_from_profile,
    load_default_prompts,
    render_prompt,
)
from intentc.core.types import (
    IntentFile,
    ProjectIntent,
    Implementation,
    Validation,
    ValidationFile,
    ValidationType,
    Severity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context(tmp_path: Path, **overrides) -> BuildContext:
    """Create a minimal BuildContext for testing."""
    response_file = tmp_path / "response.json"
    defaults = dict(
        intent=IntentFile(name="test-feature", body="# Test Feature\nBuild something."),
        validations=[],
        output_dir=str(tmp_path),
        generation_id="gen-test-1",
        dependency_names=[],
        project_intent=ProjectIntent(name="test-project", body="# Test Project"),
        implementation=Implementation(name="impl", body="# Implementation\nPython 3.11"),
        response_file_path=str(response_file),
    )
    defaults.update(overrides)
    return BuildContext(**defaults)


def _make_profile(**overrides) -> AgentProfile:
    """Create a minimal AgentProfile for testing."""
    defaults = dict(
        name="test-agent",
        provider="cli",
        command="echo",
    )
    defaults.update(overrides)
    return AgentProfile(**defaults)


def _make_validation(**overrides) -> Validation:
    """Create a test validation entry."""
    defaults = dict(
        name="test-check",
        type=ValidationType.AGENT_VALIDATION,
        severity=Severity.ERROR,
        args={"rubric": "Check something"},
    )
    defaults.update(overrides)
    return Validation(**defaults)


def _write_build_response(path: str, **overrides) -> None:
    """Write a valid BuildResponse JSON file."""
    data = {
        "status": "success",
        "summary": "Built successfully",
        "files_created": ["new_file.py"],
        "files_modified": [],
    }
    data.update(overrides)
    Path(path).write_text(json.dumps(data))


def _write_validation_response(path: str, **overrides) -> None:
    """Write a valid ValidationResponse JSON file."""
    data = {
        "name": "test-check",
        "status": "pass",
        "reason": "All good",
    }
    data.update(overrides)
    Path(path).write_text(json.dumps(data))


# ---------------------------------------------------------------------------
# Response types
# ---------------------------------------------------------------------------


class TestBuildResponse:
    def test_construction(self):
        resp = BuildResponse(
            status="success",
            summary="Done",
            files_created=["a.py"],
            files_modified=["b.py"],
        )
        assert resp.status == "success"
        assert resp.summary == "Done"
        assert resp.files_created == ["a.py"]
        assert resp.files_modified == ["b.py"]

    def test_defaults(self):
        resp = BuildResponse(status="failure", summary="Oops")
        assert resp.files_created == []
        assert resp.files_modified == []

    def test_extra_fields_ignored(self):
        resp = BuildResponse(
            status="success", summary="ok", unknown="ignored"  # type: ignore[call-arg]
        )
        assert not hasattr(resp, "unknown")


class TestValidationResponse:
    def test_construction(self):
        resp = ValidationResponse(name="check-1", status="pass", reason="Looks good")
        assert resp.name == "check-1"
        assert resp.status == "pass"
        assert resp.reason == "Looks good"

    def test_extra_fields_ignored(self):
        resp = ValidationResponse(
            name="c", status="fail", reason="bad", extra="x"  # type: ignore[call-arg]
        )
        assert not hasattr(resp, "extra")


# ---------------------------------------------------------------------------
# BuildContext
# ---------------------------------------------------------------------------


class TestBuildContext:
    def test_all_fields(self, tmp_path: Path):
        ctx = _make_context(tmp_path)
        assert ctx.intent.name == "test-feature"
        assert ctx.output_dir == str(tmp_path)
        assert ctx.generation_id == "gen-test-1"
        assert ctx.project_intent.name == "test-project"
        assert ctx.implementation is not None
        assert ctx.response_file_path.endswith("response.json")

    def test_optional_implementation(self, tmp_path: Path):
        ctx = _make_context(tmp_path, implementation=None)
        assert ctx.implementation is None

    def test_validations_list(self, tmp_path: Path):
        vf = ValidationFile(
            target="test-feature",
            validations=[_make_validation()],
        )
        ctx = _make_context(tmp_path, validations=[vf])
        assert len(ctx.validations) == 1
        assert ctx.validations[0].validations[0].name == "test-check"


# ---------------------------------------------------------------------------
# PromptTemplates & rendering
# ---------------------------------------------------------------------------


class TestPromptTemplates:
    def test_defaults_are_empty(self):
        pt = PromptTemplates()
        assert pt.build == ""
        assert pt.validate_template == ""
        assert pt.plan == ""

    def test_load_default_prompts(self):
        templates = load_default_prompts()
        # Should load from the prompts directory if it exists
        assert isinstance(templates, PromptTemplates)


class TestRenderPrompt:
    def test_replaces_all_variables(self, tmp_path: Path):
        template = (
            "Project: {project}\n"
            "Impl: {implementation}\n"
            "Feature: {feature}\n"
            "Validations: {validations}\n"
            "Response: {response_file}"
        )
        vf = ValidationFile(
            target="test",
            validations=[_make_validation(name="check-1")],
        )
        ctx = _make_context(tmp_path, validations=[vf])
        result = render_prompt(template, ctx)

        assert "# Test Project" in result
        assert "# Implementation" in result
        assert "# Test Feature" in result
        assert "check-1" in result
        assert "response.json" in result

    def test_single_validation_variable(self, tmp_path: Path):
        template = "Validate: {validation}"
        ctx = _make_context(tmp_path)
        v = _make_validation(name="my-check")
        result = render_prompt(template, ctx, validation=v)
        assert "my-check" in result

    def test_missing_implementation(self, tmp_path: Path):
        template = "Impl: {implementation}"
        ctx = _make_context(tmp_path, implementation=None)
        result = render_prompt(template, ctx)
        assert result == "Impl: "


# ---------------------------------------------------------------------------
# AgentProfile
# ---------------------------------------------------------------------------


class TestAgentProfile:
    def test_construction(self):
        p = AgentProfile(name="my-agent", provider="claude", model_id="opus")
        assert p.name == "my-agent"
        assert p.provider == "claude"
        assert p.model_id == "opus"
        assert p.timeout == 300.0
        assert p.retries == 3

    def test_defaults(self):
        p = AgentProfile(name="a", provider="cli")
        assert p.command == ""
        assert p.cli_args == []
        assert p.model_id is None
        assert p.prompt_templates is None


# ---------------------------------------------------------------------------
# Agent interface
# ---------------------------------------------------------------------------


class TestAgentInterface:
    def test_is_abstract(self):
        with pytest.raises(TypeError):
            Agent()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# MockAgent
# ---------------------------------------------------------------------------


class TestMockAgent:
    def test_defaults(self):
        agent = MockAgent()
        assert agent.get_name() == "mock"
        assert agent.get_type() == "mock"

    def test_build_records_calls(self, tmp_path: Path):
        agent = MockAgent()
        ctx = _make_context(tmp_path)
        resp = agent.build(ctx)

        assert resp.status == "success"
        assert len(agent.build_calls) == 1
        assert agent.build_calls[0].generation_id == "gen-test-1"

    def test_validate_records_calls(self, tmp_path: Path):
        agent = MockAgent()
        ctx = _make_context(tmp_path)
        v = _make_validation(name="my-val")
        resp = agent.validate(ctx, v)

        assert resp.status == "pass"
        assert resp.name == "my-val"
        assert len(agent.validate_calls) == 1
        assert agent.validate_calls[0][1].name == "my-val"

    def test_plan_records_calls(self, tmp_path: Path):
        agent = MockAgent()
        ctx = _make_context(tmp_path)
        agent.plan(ctx)
        assert len(agent.plan_calls) == 1

    def test_custom_responses(self, tmp_path: Path):
        custom_build = BuildResponse(
            status="failure", summary="Custom fail"
        )
        custom_val = ValidationResponse(
            name="custom", status="fail", reason="Custom reason"
        )
        agent = MockAgent(
            name="custom-mock",
            build_response=custom_build,
            validation_response=custom_val,
        )

        ctx = _make_context(tmp_path)
        assert agent.build(ctx).status == "failure"
        assert agent.build(ctx).summary == "Custom fail"

        v = _make_validation(name="some-val")
        val_resp = agent.validate(ctx, v)
        assert val_resp.status == "fail"
        assert val_resp.name == "some-val"  # Uses actual validation name

    def test_isinstance_agent(self):
        assert isinstance(MockAgent(), Agent)


# ---------------------------------------------------------------------------
# CLIAgent
# ---------------------------------------------------------------------------


class TestCLIAgent:
    def test_isinstance_agent(self):
        profile = _make_profile()
        agent = CLIAgent(profile)
        assert isinstance(agent, Agent)
        assert agent.get_type() == "cli"
        assert agent.get_name() == "test-agent"

    def test_build_reads_response_file(self, tmp_path: Path):
        profile = _make_profile(command="true")  # no-op command
        agent = CLIAgent(profile)
        ctx = _make_context(tmp_path)

        # Pre-write response file (as if agent wrote it)
        _write_build_response(ctx.response_file_path)

        with patch.object(agent, "_run", return_value=MagicMock()):
            resp = agent.build(ctx)

        assert resp.status == "success"
        assert resp.files_created == ["new_file.py"]

    def test_validate_reads_response_file(self, tmp_path: Path):
        profile = _make_profile(command="true")
        agent = CLIAgent(profile)
        ctx = _make_context(tmp_path)
        v = _make_validation(name="check-1")

        _write_validation_response(ctx.response_file_path, name="check-1")

        with patch.object(agent, "_run", return_value=MagicMock()):
            resp = agent.validate(ctx, v)

        assert resp.status == "pass"
        assert resp.name == "check-1"

    def test_missing_response_file_raises(self, tmp_path: Path):
        profile = _make_profile(command="true")
        agent = CLIAgent(profile)
        ctx = _make_context(tmp_path)

        with patch.object(agent, "_run", return_value=MagicMock()):
            with pytest.raises(AgentError, match="Response file not found"):
                agent.build(ctx)

    def test_invalid_json_response_raises(self, tmp_path: Path):
        profile = _make_profile(command="true")
        agent = CLIAgent(profile)
        ctx = _make_context(tmp_path)

        Path(ctx.response_file_path).write_text("NOT JSON")

        with patch.object(agent, "_run", return_value=MagicMock()):
            with pytest.raises(AgentError, match="Invalid JSON"):
                agent.build(ctx)

    def test_missing_command_raises(self, tmp_path: Path):
        profile = _make_profile(command="")
        agent = CLIAgent(profile)
        ctx = _make_context(tmp_path)

        with pytest.raises(AgentError, match="requires a command"):
            agent.build(ctx)

    def test_command_not_found_raises(self, tmp_path: Path):
        profile = _make_profile(command="nonexistent_binary_xyz_123")
        agent = CLIAgent(profile)
        ctx = _make_context(tmp_path)

        with pytest.raises(AgentError, match="Command not found"):
            agent.build(ctx)

    def test_timeout_raises(self, tmp_path: Path):
        profile = _make_profile(command="bash", timeout=0.1)
        profile.cli_args = ["-c", "sleep 10"]
        agent = CLIAgent(profile)
        ctx = _make_context(tmp_path)

        with pytest.raises(AgentError, match="timed out"):
            agent._run("", ctx)

    def test_build_command_includes_cli_args(self):
        profile = _make_profile(command="my-tool", cli_args=["--verbose", "--format=json"])
        agent = CLIAgent(profile)
        cmd = agent._build_command("do something")
        assert cmd == ["my-tool", "--verbose", "--format=json", "do something"]


# ---------------------------------------------------------------------------
# ClaudeAgent
# ---------------------------------------------------------------------------


class TestClaudeAgent:
    def test_isinstance_agent(self):
        profile = _make_profile(provider="claude")
        agent = ClaudeAgent(profile)
        assert isinstance(agent, Agent)
        assert agent.get_type() == "claude"
        assert agent.get_name() == "test-agent"

    def test_noninteractive_command_flags(self):
        profile = _make_profile(provider="claude", model_id="opus-4")
        agent = ClaudeAgent(profile)
        cmd = agent._build_noninteractive_command("build it")
        assert cmd[0] == "claude"
        assert "-p" in cmd
        assert "build it" in cmd
        assert "--output-format" in cmd
        assert "json" in cmd
        assert "--dangerously-skip-permissions" in cmd
        assert "--model" in cmd
        assert "opus-4" in cmd

    def test_noninteractive_command_no_model(self):
        profile = _make_profile(provider="claude")
        agent = ClaudeAgent(profile)
        cmd = agent._build_noninteractive_command("build it")
        assert "--model" not in cmd

    def test_noninteractive_command_extra_args(self):
        profile = _make_profile(provider="claude", cli_args=["--verbose"])
        agent = ClaudeAgent(profile)
        cmd = agent._build_noninteractive_command("build it")
        assert "--verbose" in cmd

    def test_interactive_command_no_p_flag(self):
        profile = _make_profile(provider="claude", model_id="opus-4")
        agent = ClaudeAgent(profile)
        cmd = agent._build_interactive_command("plan it")
        assert cmd[0] == "claude"
        assert "-p" not in cmd
        assert "--dangerously-skip-permissions" not in cmd
        assert "--output-format" not in cmd

    def test_build_reads_response_file(self, tmp_path: Path):
        profile = _make_profile(provider="claude")
        agent = ClaudeAgent(profile)
        ctx = _make_context(tmp_path)

        _write_build_response(ctx.response_file_path)

        with patch.object(agent, "_run_noninteractive", return_value=MagicMock()):
            resp = agent.build(ctx)

        assert resp.status == "success"

    def test_validate_reads_response_file(self, tmp_path: Path):
        profile = _make_profile(provider="claude")
        agent = ClaudeAgent(profile)
        ctx = _make_context(tmp_path)
        v = _make_validation(name="my-val")

        _write_validation_response(ctx.response_file_path, name="my-val")

        with patch.object(agent, "_run_noninteractive", return_value=MagicMock()):
            resp = agent.validate(ctx, v)

        assert resp.status == "pass"
        assert resp.name == "my-val"


# ---------------------------------------------------------------------------
# create_from_profile factory
# ---------------------------------------------------------------------------


class TestCreateFromProfile:
    def test_claude_provider(self):
        profile = _make_profile(provider="claude")
        agent = create_from_profile(profile)
        assert isinstance(agent, ClaudeAgent)

    def test_cli_provider(self):
        profile = _make_profile(provider="cli")
        agent = create_from_profile(profile)
        assert isinstance(agent, CLIAgent)

    def test_unknown_provider_raises(self):
        profile = _make_profile(provider="unknown")
        with pytest.raises(AgentError, match="Unknown agent provider"):
            create_from_profile(profile)

    def test_returned_agents_implement_interface(self):
        for provider in ["claude", "cli"]:
            profile = _make_profile(provider=provider, command="echo")
            agent = create_from_profile(profile)
            assert isinstance(agent, Agent)
            assert agent.get_name() == "test-agent"
