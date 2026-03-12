"""Tests for the agent package."""

from __future__ import annotations

import io
import subprocess
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

from core.types import (
    AgentProfile,
    Intent,
    PromptTemplates,
    ToolConfig,
    Validation,
    ValidationFile,
    ValidationType,
)

from agent.base import BuildContext
from agent.cli_agent import CLIAgent
from agent.claude_agent import ClaudeAgent
from agent.codex_agent import CodexAgent
from agent.factory import create_from_profile
from agent.mock import MockAgent


# ---------------------------------------------------------------
# MockAgent tests
# ---------------------------------------------------------------


def test_mock_agent_defaults():
    agent = MockAgent()
    assert agent.get_name() == "mock"
    assert agent.get_type() == "mock"
    assert agent.build_calls == []
    assert agent.build_files == []
    assert agent.build_error is None
    assert agent.validate_calls == []
    assert agent.validate_result == (True, "mock pass")


def test_mock_agent_build_records_calls():
    agent = MockAgent()
    agent.build_files = ["/tmp/out/main.py"]

    ctx = BuildContext(
        intent=Intent(name="auth", content="Build auth module"),
        output_dir="/tmp/out",
    )
    result = agent.build(ctx)

    assert result == ["/tmp/out/main.py"]
    assert len(agent.build_calls) == 1
    assert agent.build_calls[0].intent.name == "auth"


def test_mock_agent_build_raises_configured_error():
    agent = MockAgent()
    agent.build_error = RuntimeError("simulated failure")

    ctx = BuildContext(intent=Intent(name="fail"))
    with pytest.raises(RuntimeError, match="simulated failure"):
        agent.build(ctx)

    assert len(agent.build_calls) == 1


def test_mock_agent_validate_records_calls():
    agent = MockAgent()
    agent.validate_result = (False, "bad code")

    v = Validation(name="check1", type=ValidationType.LLM_JUDGE)
    passed, msg = agent.validate_with_llm(v, ["/tmp/file.py"])

    assert passed is False
    assert msg == "bad code"
    assert len(agent.validate_calls) == 1
    assert agent.validate_calls[0] == (v, ["/tmp/file.py"])


def test_mock_agent_build_returns_copy():
    """Mutating the returned list should not affect the agent's internal state."""
    agent = MockAgent()
    agent.build_files = ["/a.py"]

    ctx = BuildContext()
    result = agent.build(ctx)
    result.append("/b.py")

    assert agent.build_files == ["/a.py"]


# ---------------------------------------------------------------
# CLIAgent prompt construction tests
# ---------------------------------------------------------------


def _make_profile(**overrides) -> AgentProfile:
    defaults = {
        "name": "test-cli",
        "provider": "cli",
        "command": "echo",
        "retries": 1,
        "timeout": timedelta(seconds=10),
        "rate_limit": timedelta(seconds=0),
    }
    defaults.update(overrides)
    return AgentProfile(**defaults)


def test_cli_agent_build_prompt_default():
    profile = _make_profile()
    agent = CLIAgent(profile)

    ctx = BuildContext(
        intent=Intent(name="auth", content="Build an auth module"),
        project_intent=Intent(name="project", content="E-commerce app"),
        output_dir="/tmp/out",
        dependency_names=["core", "db"],
        validations=[
            ValidationFile(
                target="auth",
                validations=[
                    Validation(
                        name="file_exists",
                        type=ValidationType.FILE_CHECK,
                        parameters={"path": "auth.py"},
                    )
                ],
            )
        ],
    )

    prompt = agent._construct_build_prompt(ctx)

    assert "<system>" in prompt
    assert "code generation agent" in prompt
    assert "<project-intent>" in prompt
    assert "E-commerce app" in prompt
    assert '<feature-intent name="auth">' in prompt
    assert "Build an auth module" in prompt
    assert "<validations>" in prompt
    assert "file_exists" in prompt
    assert "<output-dir>/tmp/out</output-dir>" in prompt
    assert "<dependencies>core, db</dependencies>" in prompt


def test_cli_agent_build_prompt_custom_template():
    profile = _make_profile(
        prompt_templates=PromptTemplates(build="Custom build prompt here")
    )
    agent = CLIAgent(profile)

    prompt = agent._construct_build_prompt(BuildContext())
    assert prompt == "Custom build prompt here"


def test_cli_agent_build_prompt_custom_system():
    profile = _make_profile(
        prompt_templates=PromptTemplates(system="You are a Rust expert.")
    )
    agent = CLIAgent(profile)

    prompt = agent._construct_build_prompt(BuildContext())
    assert "You are a Rust expert." in prompt
    assert "<system>" in prompt


def test_cli_agent_validate_prompt_default():
    profile = _make_profile()
    agent = CLIAgent(profile)

    v = Validation(
        name="code_quality",
        type=ValidationType.LLM_JUDGE,
        parameters={"rubric": "Code must follow PEP 8"},
    )

    prompt = agent._construct_validate_prompt(v, ["/tmp/nonexistent.py"])
    assert "code review judge" in prompt
    assert "Code must follow PEP 8" in prompt
    assert "PASS or FAIL" in prompt


def test_cli_agent_validate_prompt_custom_template():
    profile = _make_profile(
        prompt_templates=PromptTemplates(validate="Custom validate prompt")
    )
    agent = CLIAgent(profile)

    v = Validation(name="check1")
    prompt = agent._construct_validate_prompt(v, [])
    assert prompt == "Custom validate prompt"


def test_cli_agent_name_and_type():
    profile = _make_profile(name="my-agent")
    agent = CLIAgent(profile)

    assert agent.get_name() == "my-agent"
    assert agent.get_type() == "cli"


# ---------------------------------------------------------------
# CLIAgent command execution tests (mocked subprocess)
# ---------------------------------------------------------------


def _mock_popen(stdout="", stderr="", returncode=0):
    """Create a mock Popen that yields stdout/stderr line by line."""
    mock_proc = MagicMock()
    mock_proc.stdin = MagicMock()
    mock_proc.stdout = io.StringIO(stdout)
    mock_proc.stderr = io.StringIO(stderr)
    mock_proc.returncode = returncode
    mock_proc.wait.return_value = returncode
    return mock_proc


def test_cli_agent_run_command():
    profile = _make_profile()
    agent = CLIAgent(profile)

    mock_proc = _mock_popen(stdout="output\n", stderr="")

    with patch("agent.cli_agent.subprocess.Popen", return_value=mock_proc) as mock_cls:
        stdout, stderr, rc = agent._run_command("hello prompt")

    assert stdout == "output\n"
    assert stderr == ""
    assert rc == 0
    mock_cls.assert_called_once()
    mock_proc.stdin.write.assert_called_once_with("hello prompt")


def test_cli_agent_run_command_no_command_raises():
    profile = _make_profile(command="")
    agent = CLIAgent(profile)

    with pytest.raises(ValueError, match="command must be set"):
        agent._run_command("test")


def test_cli_agent_build_success(tmp_path):
    """Successful build that returns files via directory scan."""
    outfile = tmp_path / "result.py"
    outfile.write_text("print('hello')")

    profile = _make_profile()
    agent = CLIAgent(profile)

    ctx = BuildContext(
        intent=Intent(name="test"),
        output_dir=str(tmp_path),
    )

    with patch("agent.cli_agent.subprocess.Popen", return_value=_mock_popen()):
        files = agent.build(ctx)

    assert str(outfile) in files


def test_cli_agent_build_detects_files_from_stdout(tmp_path):
    """Files mentioned in stdout are detected."""
    outfile = tmp_path / "generated.py"
    outfile.write_text("code")

    profile = _make_profile()
    agent = CLIAgent(profile)

    ctx = BuildContext(
        intent=Intent(name="test"),
        output_dir=str(tmp_path),
    )

    with patch(
        "agent.cli_agent.subprocess.Popen",
        return_value=_mock_popen(stdout=f"Created: {outfile}\n"),
    ):
        files = agent.build(ctx)

    assert str(outfile) in files


def test_cli_agent_build_retry_on_failure():
    """Build retries the configured number of times."""
    profile = _make_profile(retries=3, rate_limit=timedelta(seconds=0))
    agent = CLIAgent(profile)

    ctx = BuildContext(intent=Intent(name="test"), output_dir="/tmp/out")

    with patch(
        "agent.cli_agent.subprocess.Popen",
        return_value=_mock_popen(stderr="error\n", returncode=1),
    ):
        with pytest.raises(RuntimeError, match="All 3 build attempts failed"):
            agent.build(ctx)


def test_cli_agent_build_retry_succeeds_on_second_attempt(tmp_path):
    """Build succeeds after initial failure."""
    outfile = tmp_path / "ok.py"
    outfile.write_text("ok")

    profile = _make_profile(retries=3, rate_limit=timedelta(seconds=0))
    agent = CLIAgent(profile)

    ctx = BuildContext(intent=Intent(name="test"), output_dir=str(tmp_path))

    with patch(
        "agent.cli_agent.subprocess.Popen",
        side_effect=[
            _mock_popen(stderr="error\n", returncode=1),
            _mock_popen(),
        ],
    ):
        files = agent.build(ctx)

    assert str(outfile) in files


def test_cli_agent_validate_pass():
    profile = _make_profile()
    agent = CLIAgent(profile)

    v = Validation(name="check1", type=ValidationType.LLM_JUDGE)

    with patch(
        "agent.cli_agent.subprocess.Popen",
        return_value=_mock_popen(stdout="PASS\nLooks good\n"),
    ):
        passed, explanation = agent.validate_with_llm(v, ["/tmp/test.py"])

    assert passed is True
    assert explanation == "Looks good"


def test_cli_agent_validate_fail():
    profile = _make_profile()
    agent = CLIAgent(profile)

    v = Validation(name="check1", type=ValidationType.LLM_JUDGE)

    with patch(
        "agent.cli_agent.subprocess.Popen",
        return_value=_mock_popen(stdout="FAIL\nMissing error handling\n"),
    ):
        passed, explanation = agent.validate_with_llm(v, [])

    assert passed is False
    assert explanation == "Missing error handling"


def test_cli_agent_validate_empty_output():
    profile = _make_profile()
    agent = CLIAgent(profile)

    v = Validation(name="check1")

    with patch("agent.cli_agent.subprocess.Popen", return_value=_mock_popen()):
        passed, explanation = agent.validate_with_llm(v, [])

    assert passed is False
    assert "No output" in explanation


# ---------------------------------------------------------------
# CLIAgent._parse_validation_output
# ---------------------------------------------------------------


def test_parse_validation_output_pass():
    assert CLIAgent._parse_validation_output("PASS\nGood job") == (True, "Good job")


def test_parse_validation_output_pass_lowercase():
    assert CLIAgent._parse_validation_output("pass\n") == (True, "Passed")


def test_parse_validation_output_fail():
    assert CLIAgent._parse_validation_output("FAIL\nBad code") == (False, "Bad code")


def test_parse_validation_output_empty():
    assert CLIAgent._parse_validation_output("") == (False, "No output from validation agent")


# ---------------------------------------------------------------
# CLIAgent._detect_files
# ---------------------------------------------------------------


def test_detect_files_from_stdout(tmp_path):
    f = tmp_path / "hello.py"
    f.write_text("x")

    agent = CLIAgent(_make_profile())
    result = agent._detect_files(f"Created: {f}\n", str(tmp_path))
    assert str(f) in result


def test_detect_files_fallback_walk(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    f = sub / "file.py"
    f.write_text("code")

    agent = CLIAgent(_make_profile())
    result = agent._detect_files("no paths here", str(tmp_path))
    assert str(f) in result


def test_detect_files_empty_dir(tmp_path):
    agent = CLIAgent(_make_profile())
    result = agent._detect_files("nothing", str(tmp_path))
    assert result == []


# ---------------------------------------------------------------
# ClaudeAgent tests
# ---------------------------------------------------------------


def test_claude_agent_sets_command():
    profile = AgentProfile(
        name="my-claude",
        provider="claude",
        model_id="claude-sonnet-4-6",
        tools=[ToolConfig(name="bash", enabled=True), ToolConfig(name="read", enabled=False)],
    )
    agent = ClaudeAgent(profile)

    inner_profile = agent._cli_agent.profile
    assert inner_profile.command == "claude"
    assert "-p" in inner_profile.cli_args
    assert "--output-format" in inner_profile.cli_args
    assert "stream-json" in inner_profile.cli_args
    assert "--verbose" in inner_profile.cli_args
    assert "--model" in inner_profile.cli_args
    assert "claude-sonnet-4-6" in inner_profile.cli_args
    # Only enabled tools get --allowedTools
    assert inner_profile.cli_args.count("--allowedTools") == 1
    assert "bash" in inner_profile.cli_args
    assert "read" not in inner_profile.cli_args


def test_claude_agent_no_model_id():
    profile = AgentProfile(name="claude-default", provider="claude")
    agent = ClaudeAgent(profile)

    assert "--model" not in agent._cli_agent.profile.cli_args


def test_claude_agent_preserves_extra_cli_args():
    profile = AgentProfile(
        name="claude-custom",
        provider="claude",
        cli_args=["--verbose"],
    )
    agent = ClaudeAgent(profile)
    assert "--verbose" in agent._cli_agent.profile.cli_args
    assert "-p" in agent._cli_agent.profile.cli_args


def test_claude_agent_type():
    agent = ClaudeAgent(AgentProfile(name="c"))
    assert agent.get_type() == "claude"


def test_claude_agent_name():
    agent = ClaudeAgent(AgentProfile(name="my-claude"))
    assert agent.get_name() == "my-claude"


def test_claude_agent_delegates_build():
    profile = AgentProfile(name="c", provider="claude")
    agent = ClaudeAgent(profile)

    ctx = BuildContext(intent=Intent(name="test"))

    stream_out = '{"type":"result","result":"done","is_error":false}\n'
    with patch("agent.cli_agent.subprocess.Popen", return_value=_mock_popen(stdout=stream_out)):
        result = agent.build(ctx)

    assert isinstance(result, list)


def test_claude_agent_build_detects_written_files(tmp_path):
    """ClaudeAgent extracts file paths from stream-json tool_use events."""
    profile = AgentProfile(name="c", provider="claude")
    agent = ClaudeAgent(profile)

    out_file = tmp_path / "main.py"
    out_file.write_text("print('hi')")

    ctx = BuildContext(intent=Intent(name="test"), output_dir=str(tmp_path))

    import json
    tool_use_event = json.dumps({
        "type": "assistant",
        "message": {
            "content": [{
                "type": "tool_use",
                "name": "Write",
                "input": {"file_path": str(out_file), "content": "print('hi')"},
            }],
        },
    })
    result_event = json.dumps({"type": "result", "result": "done", "is_error": False})
    stream_out = tool_use_event + "\n" + result_event + "\n"

    with patch("agent.cli_agent.subprocess.Popen", return_value=_mock_popen(stdout=stream_out)):
        files = agent.build(ctx)

    assert str(out_file) in files


def test_claude_agent_build_falls_back_to_dir_walk(tmp_path):
    """When no Write tool_use events, falls back to directory walk."""
    profile = AgentProfile(name="c", provider="claude")
    agent = ClaudeAgent(profile)

    out_file = tmp_path / "generated.py"
    out_file.write_text("code")

    ctx = BuildContext(intent=Intent(name="test"), output_dir=str(tmp_path))

    stream_out = '{"type":"result","result":"I created the file.","is_error":false}\n'
    with patch("agent.cli_agent.subprocess.Popen", return_value=_mock_popen(stdout=stream_out)):
        files = agent.build(ctx)

    assert str(out_file) in files


# ---------------------------------------------------------------
# CodexAgent tests
# ---------------------------------------------------------------


def test_codex_agent_sets_command():
    profile = AgentProfile(
        name="my-codex",
        provider="codex",
        model_id="o3",
    )
    agent = CodexAgent(profile)

    inner_profile = agent._cli_agent.profile
    assert inner_profile.command == "codex"
    assert "--model" in inner_profile.cli_args
    assert "o3" in inner_profile.cli_args


def test_codex_agent_no_model_id():
    profile = AgentProfile(name="codex-default", provider="codex")
    agent = CodexAgent(profile)
    assert "--model" not in agent._cli_agent.profile.cli_args


def test_codex_agent_type():
    agent = CodexAgent(AgentProfile(name="cx"))
    assert agent.get_type() == "codex"


def test_codex_agent_preserves_extra_cli_args():
    profile = AgentProfile(
        name="codex-custom",
        provider="codex",
        cli_args=["--quiet"],
    )
    agent = CodexAgent(profile)
    assert "--quiet" in agent._cli_agent.profile.cli_args


# ---------------------------------------------------------------
# Factory tests
# ---------------------------------------------------------------


def test_factory_creates_claude():
    profile = AgentProfile(name="c", provider="claude")
    agent = create_from_profile(profile)
    assert isinstance(agent, ClaudeAgent)
    assert agent.get_type() == "claude"


def test_factory_creates_codex():
    profile = AgentProfile(name="cx", provider="codex")
    agent = create_from_profile(profile)
    assert isinstance(agent, CodexAgent)
    assert agent.get_type() == "codex"


def test_factory_creates_cli():
    profile = AgentProfile(name="generic", provider="cli", command="my-tool")
    agent = create_from_profile(profile)
    assert isinstance(agent, CLIAgent)
    assert agent.get_type() == "cli"


def test_factory_case_insensitive():
    profile = AgentProfile(name="c", provider="Claude")
    agent = create_from_profile(profile)
    assert isinstance(agent, ClaudeAgent)


def test_factory_unknown_provider():
    profile = AgentProfile(name="x", provider="unknown")
    with pytest.raises(ValueError, match="Unknown agent provider"):
        create_from_profile(profile)


# ---------------------------------------------------------------
# BuildContext tests
# ---------------------------------------------------------------


def test_build_context_defaults():
    ctx = BuildContext()
    assert ctx.intent.name == ""
    assert ctx.validations == []
    assert ctx.project_root == ""
    assert ctx.output_dir == ""
    assert ctx.generation_id == ""
    assert ctx.dependency_names == []
    assert ctx.project_intent.name == ""


def test_build_context_full():
    ctx = BuildContext(
        intent=Intent(name="auth", content="Build auth"),
        validations=[ValidationFile(target="auth")],
        project_root="/proj",
        output_dir="/out",
        generation_id="gen-1",
        dependency_names=["core"],
        project_intent=Intent(name="project", content="App desc"),
    )
    assert ctx.intent.name == "auth"
    assert len(ctx.validations) == 1
    assert ctx.project_root == "/proj"
    assert ctx.generation_id == "gen-1"
