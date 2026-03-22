"""Tests for the agent module."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
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
    PromptTemplates,
    ValidationResponse,
    _read_response_file,
    create_from_profile,
    load_default_prompts,
    render_differencing_prompt,
    render_prompt,
)
from intentc.core.types import (
    Implementation,
    IntentFile,
    ProjectIntent,
    Validation,
    ValidationFile,
    ValidationType,
    Severity,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project_intent() -> ProjectIntent:
    return ProjectIntent(name="test-project", body="A test project")


@pytest.fixture
def intent_file() -> IntentFile:
    return IntentFile(name="test-feature", body="Implement a test feature")


@pytest.fixture
def validation() -> Validation:
    return Validation(
        name="test-val",
        type=ValidationType.AGENT_VALIDATION,
        severity=Severity.ERROR,
        args={"rubric": "Check it works"},
    )


@pytest.fixture
def validation_file(validation: Validation) -> ValidationFile:
    return ValidationFile(target="test-feature", validations=[validation])


@pytest.fixture
def build_context(
    intent_file: IntentFile,
    validation_file: ValidationFile,
    project_intent: ProjectIntent,
    tmp_path: Path,
) -> BuildContext:
    return BuildContext(
        intent=intent_file,
        validations=[validation_file],
        output_dir=str(tmp_path / "output"),
        generation_id="gen-123",
        dependency_names=["dep1"],
        project_intent=project_intent,
        response_file_path=str(tmp_path / "response.json"),
    )


@pytest.fixture
def diff_context(project_intent: ProjectIntent, tmp_path: Path) -> DifferencingContext:
    return DifferencingContext(
        output_dir_a=str(tmp_path / "a"),
        output_dir_b=str(tmp_path / "b"),
        project_intent=project_intent,
        response_file_path=str(tmp_path / "diff_response.json"),
    )


@pytest.fixture
def profile() -> AgentProfile:
    return AgentProfile(name="test-agent", provider="cli", command="echo")


@pytest.fixture
def claude_profile() -> AgentProfile:
    return AgentProfile(name="claude-agent", provider="claude")


# ---------------------------------------------------------------------------
# Response types
# ---------------------------------------------------------------------------


class TestBuildResponse:
    def test_create(self):
        r = BuildResponse(
            status="success",
            summary="Built it",
            files_created=["a.py"],
            files_modified=["b.py"],
        )
        assert r.status == "success"
        assert r.files_created == ["a.py"]

    def test_defaults(self):
        r = BuildResponse(status="failure", summary="Oops")
        assert r.files_created == []
        assert r.files_modified == []

    def test_extra_ignored(self):
        r = BuildResponse(status="success", summary="ok", extra_field="ignored")
        assert not hasattr(r, "extra_field")


class TestValidationResponse:
    def test_create(self):
        r = ValidationResponse(name="v1", status="pass", reason="looks good")
        assert r.name == "v1"
        assert r.status == "pass"


class TestDifferencingResponse:
    def test_create(self):
        dim = DimensionResult(name="api", status="pass", rationale="same API")
        r = DifferencingResponse(
            status="equivalent", dimensions=[dim], summary="All good"
        )
        assert r.status == "equivalent"
        assert len(r.dimensions) == 1
        assert r.dimensions[0].name == "api"


# ---------------------------------------------------------------------------
# PromptTemplates
# ---------------------------------------------------------------------------


class TestPromptTemplates:
    def test_defaults(self):
        pt = PromptTemplates()
        assert pt.build == ""
        assert pt.validate_template == ""
        assert pt.plan == ""
        assert pt.difference == ""

    def test_custom(self):
        pt = PromptTemplates(build="Build: {feature}", validate_template="Val: {validation}")
        assert pt.build == "Build: {feature}"


# ---------------------------------------------------------------------------
# load_default_prompts
# ---------------------------------------------------------------------------


class TestLoadDefaultPrompts:
    def test_loads_from_cwd(self, tmp_path: Path):
        prompts = tmp_path / "intent" / "build" / "agents" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "build.prompt").write_text("BUILD {feature}")
        (prompts / "validate.prompt").write_text("VALIDATE {validation}")
        (prompts / "plan.prompt").write_text("PLAN {feature}")

        diff_prompts = tmp_path / "intent" / "differencing" / "prompts"
        diff_prompts.mkdir(parents=True)
        (diff_prompts / "difference.prompt").write_text("DIFF {output_dir_a}")

        with patch("intentc.build.agents.Path.cwd", return_value=tmp_path):
            templates = load_default_prompts()

        assert templates.build == "BUILD {feature}"
        assert templates.validate_template == "VALIDATE {validation}"
        assert templates.plan == "PLAN {feature}"
        assert templates.difference == "DIFF {output_dir_a}"

    def test_missing_files_yield_empty(self, tmp_path: Path):
        with patch("intentc.build.agents.Path.cwd", return_value=tmp_path):
            templates = load_default_prompts()
        assert templates.build == ""
        assert templates.validate_template == ""


# ---------------------------------------------------------------------------
# render_prompt
# ---------------------------------------------------------------------------


class TestRenderPrompt:
    def test_render_build(self, build_context: BuildContext):
        template = "Project: {project}\nImpl: {implementation}\nFeature: {feature}\nVals: {validations}\nResp: {response_file}"
        result = render_prompt(template, build_context)
        assert "A test project" in result
        assert "Implement a test feature" in result
        assert "test-val" in result
        assert "response.json" in result
        # No implementation set
        assert "Impl: \n" in result

    def test_render_with_implementation(self, build_context: BuildContext):
        build_context.implementation = Implementation(name="py", body="Use Python")
        template = "{implementation}"
        result = render_prompt(template, build_context)
        assert result == "Use Python"

    def test_render_with_validation(self, build_context: BuildContext, validation: Validation):
        template = "Check: {validation}"
        result = render_prompt(template, build_context, validation=validation)
        assert "test-val" in result


# ---------------------------------------------------------------------------
# render_differencing_prompt
# ---------------------------------------------------------------------------


class TestRenderDifferencingPrompt:
    def test_render(self, diff_context: DifferencingContext):
        template = "A: {output_dir_a}, B: {output_dir_b}, Project: {project}, Resp: {response_file}"
        result = render_differencing_prompt(template, diff_context)
        assert diff_context.output_dir_a in result
        assert diff_context.output_dir_b in result
        assert "A test project" in result


# ---------------------------------------------------------------------------
# AgentProfile
# ---------------------------------------------------------------------------


class TestAgentProfile:
    def test_defaults(self):
        p = AgentProfile(name="a", provider="claude")
        assert p.timeout == 3600.0
        assert p.retries == 3
        assert p.command == ""
        assert p.cli_args == []
        assert p.model_id is None
        assert p.prompt_templates is None
        assert p.sandbox_write_paths == []
        assert p.sandbox_read_paths == []

    def test_full(self):
        p = AgentProfile(
            name="custom",
            provider="cli",
            command="my-tool",
            cli_args=["--flag"],
            timeout=60.0,
            retries=1,
            model_id="gpt-4",
            sandbox_write_paths=["/out"],
            sandbox_read_paths=["/in"],
        )
        assert p.provider == "cli"
        assert p.timeout == 60.0


# ---------------------------------------------------------------------------
# BuildContext & DifferencingContext
# ---------------------------------------------------------------------------


class TestBuildContext:
    def test_fields(self, build_context: BuildContext):
        assert build_context.intent.name == "test-feature"
        assert build_context.generation_id == "gen-123"
        assert build_context.dependency_names == ["dep1"]
        assert build_context.implementation is None

    def test_defaults(self, project_intent: ProjectIntent, intent_file: IntentFile):
        ctx = BuildContext(
            intent=intent_file,
            output_dir="/out",
            generation_id="g1",
            project_intent=project_intent,
            response_file_path="/resp.json",
        )
        assert ctx.validations == []
        assert ctx.dependency_names == []


class TestDifferencingContext:
    def test_fields(self, diff_context: DifferencingContext):
        assert diff_context.implementation is None
        assert "a" in diff_context.output_dir_a

    def test_with_implementation(self, project_intent: ProjectIntent):
        impl = Implementation(name="go", body="Use Go")
        ctx = DifferencingContext(
            output_dir_a="/a",
            output_dir_b="/b",
            project_intent=project_intent,
            response_file_path="/r.json",
            implementation=impl,
        )
        assert ctx.implementation is not None
        assert ctx.implementation.name == "go"


# ---------------------------------------------------------------------------
# _read_response_file
# ---------------------------------------------------------------------------


class TestReadResponseFile:
    def test_valid_json(self, tmp_path: Path):
        p = tmp_path / "resp.json"
        p.write_text(json.dumps({"status": "success", "summary": "ok"}))
        data = _read_response_file(str(p))
        assert data["status"] == "success"

    def test_missing_file(self):
        with pytest.raises(AgentError, match="not found"):
            _read_response_file("/nonexistent/path.json")

    def test_invalid_json(self, tmp_path: Path):
        p = tmp_path / "bad.json"
        p.write_text("not json{{{")
        with pytest.raises(AgentError, match="Invalid response"):
            _read_response_file(str(p))


# ---------------------------------------------------------------------------
# MockAgent
# ---------------------------------------------------------------------------


class TestMockAgent:
    def test_defaults(self):
        agent = MockAgent()
        assert agent.get_name() == "mock"
        assert agent.get_type() == "mock"

    def test_build_records_calls(self, build_context: BuildContext):
        agent = MockAgent()
        resp = agent.build(build_context)
        assert resp.status == "success"
        assert len(agent.build_calls) == 1
        assert agent.build_calls[0] is build_context

    def test_validate_records_calls(self, build_context: BuildContext, validation: Validation):
        agent = MockAgent()
        resp = agent.validate(build_context, validation)
        assert resp.status == "pass"
        assert len(agent.validate_calls) == 1
        assert agent.validate_calls[0] == (build_context, validation)

    def test_difference_records_calls(self, diff_context: DifferencingContext):
        agent = MockAgent()
        resp = agent.difference(diff_context)
        assert resp.status == "equivalent"
        assert len(agent.difference_calls) == 1
        assert agent.difference_calls[0] is diff_context

    def test_plan_records_calls(self, build_context: BuildContext):
        agent = MockAgent()
        agent.plan(build_context)
        assert len(agent.plan_calls) == 1

    def test_custom_responses(self, build_context: BuildContext, diff_context: DifferencingContext):
        custom_build = BuildResponse(status="failure", summary="boom")
        custom_val = ValidationResponse(name="v", status="fail", reason="bad")
        custom_diff = DifferencingResponse(
            status="divergent",
            dimensions=[DimensionResult(name="api", status="fail", rationale="diff")],
            summary="not same",
        )
        agent = MockAgent(
            name="custom-mock",
            build_response=custom_build,
            validation_response=custom_val,
            differencing_response=custom_diff,
        )
        assert agent.get_name() == "custom-mock"
        assert agent.build(build_context).status == "failure"
        v = Validation(name="x", type=ValidationType.AGENT_VALIDATION, severity=Severity.ERROR)
        assert agent.validate(build_context, v).status == "fail"
        assert agent.difference(diff_context).status == "divergent"

    def test_is_agent_subclass(self):
        assert isinstance(MockAgent(), Agent)


# ---------------------------------------------------------------------------
# CLIAgent
# ---------------------------------------------------------------------------


class TestCLIAgent:
    def test_build_success(self, tmp_path: Path, build_context: BuildContext, profile: AgentProfile):
        profile.prompt_templates = PromptTemplates(build="build {feature}")
        resp_data = {
            "status": "success",
            "summary": "done",
            "files_created": ["new.py"],
            "files_modified": [],
        }
        resp_path = Path(build_context.response_file_path)
        resp_path.parent.mkdir(parents=True, exist_ok=True)

        agent = CLIAgent(profile)
        with patch("intentc.build.agents.subprocess.run") as mock_run:
            # Write the response file when the command runs
            def side_effect(*args, **kwargs):
                resp_path.write_text(json.dumps(resp_data))
                return MagicMock(returncode=0)

            mock_run.side_effect = side_effect
            result = agent.build(build_context)

        assert result.status == "success"
        assert result.files_created == ["new.py"]

    def test_build_command_failure(self, build_context: BuildContext, profile: AgentProfile):
        profile.prompt_templates = PromptTemplates(build="build")
        agent = CLIAgent(profile)
        with patch("intentc.build.agents.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "echo", stderr=b"err")
            with pytest.raises(AgentError, match="failed with exit code"):
                agent.build(build_context)

    def test_no_command_raises(self, build_context: BuildContext):
        profile = AgentProfile(name="no-cmd", provider="cli", command="")
        profile.prompt_templates = PromptTemplates(build="b")
        agent = CLIAgent(profile)
        with pytest.raises(AgentError, match="requires a command"):
            agent.build(build_context)

    def test_get_name_and_type(self, profile: AgentProfile):
        agent = CLIAgent(profile)
        assert agent.get_name() == "test-agent"
        assert agent.get_type() == "cli"

    def test_is_agent_subclass(self, profile: AgentProfile):
        assert isinstance(CLIAgent(profile), Agent)


# ---------------------------------------------------------------------------
# ClaudeAgent
# ---------------------------------------------------------------------------


class TestClaudeAgent:
    def test_build_constructs_correct_command(
        self, build_context: BuildContext, claude_profile: AgentProfile, tmp_path: Path
    ):
        claude_profile.prompt_templates = PromptTemplates(build="build {feature}")
        claude_profile.model_id = "claude-sonnet-4-6-20250514"
        agent = ClaudeAgent(claude_profile)

        resp_data = {"status": "success", "summary": "built", "files_created": [], "files_modified": []}
        resp_path = Path(build_context.response_file_path)
        resp_path.parent.mkdir(parents=True, exist_ok=True)
        resp_path.write_text(json.dumps(resp_data))

        with patch("intentc.build.agents.subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.stdout = iter([])
            mock_proc.stderr = MagicMock()
            mock_proc.stderr.read.return_value = b""
            mock_proc.returncode = 0
            mock_proc.wait.return_value = 0
            mock_popen.return_value = mock_proc

            result = agent.build(build_context)

        assert result.status == "success"
        # Verify command flags
        call_args = mock_popen.call_args
        cmd = call_args[0][0]
        assert cmd[0] == "claude"
        assert "-p" in cmd
        assert "--verbose" in cmd
        assert "--output-format" in cmd
        assert "stream-json" in cmd
        assert "--dangerously-skip-permissions" in cmd
        assert "--model" in cmd
        assert "claude-sonnet-4-6-20250514" in cmd

    def test_sandbox_settings_written_and_cleaned(
        self, build_context: BuildContext, tmp_path: Path
    ):
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        build_context.output_dir = str(output_dir)

        profile = AgentProfile(
            name="sandboxed",
            provider="claude",
            sandbox_write_paths=[str(output_dir)],
            sandbox_read_paths=["/intent"],
        )
        profile.prompt_templates = PromptTemplates(build="build")
        agent = ClaudeAgent(profile)

        resp_path = Path(build_context.response_file_path)
        resp_path.parent.mkdir(parents=True, exist_ok=True)
        resp_path.write_text(json.dumps({"status": "success", "summary": "ok"}))

        settings_written = {}

        with patch("intentc.build.agents.subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.stdout = iter([])
            mock_proc.stderr = MagicMock()
            mock_proc.stderr.read.return_value = b""
            mock_proc.returncode = 0
            mock_proc.wait.return_value = 0
            mock_popen.return_value = mock_proc

            # Intercept settings file write
            orig_write = Path.write_text

            def capture_write(self_path, content, *args, **kwargs):
                if "settings.local.json" in str(self_path):
                    settings_written["path"] = self_path
                    settings_written["content"] = content
                return orig_write(self_path, content, *args, **kwargs)

            with patch.object(Path, "write_text", capture_write):
                agent.build(build_context)

        # Settings file should have been written
        assert "path" in settings_written
        data = json.loads(settings_written["content"])
        assert str(output_dir) in data["sandbox"]["write_paths"]
        assert "/intent" in data["sandbox"]["read_paths"]

    def test_get_type(self, claude_profile: AgentProfile):
        agent = ClaudeAgent(claude_profile)
        assert agent.get_type() == "claude"

    def test_is_agent_subclass(self, claude_profile: AgentProfile):
        assert isinstance(ClaudeAgent(claude_profile), Agent)


# ---------------------------------------------------------------------------
# create_from_profile
# ---------------------------------------------------------------------------


class TestCreateFromProfile:
    def test_claude(self):
        p = AgentProfile(name="c", provider="claude")
        agent = create_from_profile(p)
        assert isinstance(agent, ClaudeAgent)

    def test_cli(self):
        p = AgentProfile(name="c", provider="cli", command="echo")
        agent = create_from_profile(p)
        assert isinstance(agent, CLIAgent)

    def test_unknown_raises(self):
        p = AgentProfile(name="u", provider="unknown")
        with pytest.raises(AgentError, match="Unknown agent provider"):
            create_from_profile(p)


# ---------------------------------------------------------------------------
# AgentError
# ---------------------------------------------------------------------------


class TestAgentError:
    def test_is_exception(self):
        err = AgentError("something broke")
        assert isinstance(err, Exception)
        assert str(err) == "something broke"
