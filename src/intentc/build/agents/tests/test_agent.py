"""Tests for intentc.build.agents."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

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
    create_from_profile,
    load_default_prompts,
    render_differencing_prompt,
    render_init_prompt,
    render_prompt,
)
from intentc.core import (
    Artifact,
    Implementation,
    IntentFile,
    ProjectIntent,
    Validation,
    ValidationFile,
)


def make_build_context(**overrides) -> BuildContext:
    defaults = dict(
        intent=IntentFile(name="build/agents", body="Build the agent module.", depends_on=["core/*"]),
        validations=[
            ValidationFile(
                target="build/agents",
                validations=[
                    Validation(
                        name="agents-tests-pass",
                        type="command_validation",
                        args={"command": "pytest", "cwd": "."},
                    ),
                    Validation(
                        name="agent-module-exists",
                        type="agent_validation",
                        args={"rubric": "Verify the module exists.\nCheck exports."},
                    ),
                ],
            )
        ],
        output_dir="out",
        generation_id="gen-1",
        dependency_names=["core/project", "core/specifications"],
        project_intent=ProjectIntent(name="intentc", body="A compiler of intent."),
        implementation=Implementation(name="python", body="Python 3.11+ with uv."),
        response_file_path="response.json",
        previous_errors=[],
        seed_prompt="",
        feature_path="build/agents",
    )
    defaults.update(overrides)
    return BuildContext(**defaults)


def make_differencing_context(**overrides) -> DifferencingContext:
    defaults = dict(
        output_dir_a="out_a",
        output_dir_b="out_b",
        project_intent=ProjectIntent(name="intentc", body="A compiler of intent."),
        response_file_path="diff_response.json",
        implementation=None,
    )
    defaults.update(overrides)
    return DifferencingContext(**defaults)


# ---------------------------------------------------------------------------
# Response and context models
# ---------------------------------------------------------------------------


def test_build_response_ignores_unknown_keys():
    response = BuildResponse.model_validate(
        {"status": "success", "summary": "done", "files_created": ["a.py"], "extra_field": "ignored"}
    )
    assert response.status == "success"
    assert response.files_created == ["a.py"]
    assert response.files_modified == []


def test_validation_response_defaults():
    response = ValidationResponse.model_validate({"name": "check", "status": "pass", "reason": "ok"})
    assert response.severity == "error"
    assert response.type == "agent_validation"
    assert response.duration_secs == 0.0


def test_differencing_response_with_dimensions():
    response = DifferencingResponse.model_validate(
        {
            "status": "divergent",
            "dimensions": [{"name": "public_api", "status": "fail", "rationale": "missing flag"}],
            "summary": "not equivalent",
        }
    )
    assert response.status == "divergent"
    assert isinstance(response.dimensions[0], DimensionResult)
    assert response.dimensions[0].name == "public_api"


def test_build_context_defaults():
    ctx = make_build_context(previous_errors=["boom"])
    assert ctx.feature_path == "build/agents"
    assert ctx.previous_errors == ["boom"]
    assert ctx.implementation is not None


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------


def test_render_prompt_fills_all_documented_variables():
    ctx = make_build_context()
    template = (
        "{project}|{implementation}|{feature}|{feature_name}|{output_dir}|"
        "{dependencies}|{validations}|{response_file}|{previous_errors}|{seed_prompt}"
    )
    rendered = render_prompt(template, ctx)
    assert "A compiler of intent." in rendered
    assert "Python 3.11+ with uv." in rendered
    assert "Build the agent module." in rendered
    assert "build/agents" in rendered
    assert "out" in rendered
    assert "- core/project" in rendered
    assert "- core/specifications" in rendered
    assert "agents-tests-pass" in rendered
    assert "response.json" in rendered


def test_render_prompt_dependencies_none_when_empty():
    ctx = make_build_context(dependency_names=[])
    rendered = render_prompt("{dependencies}", ctx)
    assert rendered == "(none)"


def test_render_prompt_previous_errors_empty_by_default():
    ctx = make_build_context(previous_errors=[])
    rendered = render_prompt("before{previous_errors}after", ctx)
    assert rendered == "beforeafter"


def test_render_prompt_previous_errors_bulleted():
    ctx = make_build_context(previous_errors=["first failure", "second failure"])
    rendered = render_prompt("{previous_errors}", ctx)
    assert "- first failure" in rendered
    assert "- second failure" in rendered


def test_render_prompt_single_validation():
    ctx = make_build_context()
    validation = Validation(name="only-me", type="file_exists", args={"paths": ["a.txt", "b.txt"]})
    rendered = render_prompt("{validation}", ctx, validation=validation)
    assert "only-me" in rendered
    assert "a.txt" in rendered
    assert "b.txt" in rendered


def test_render_prompt_never_raises_on_undocumented_placeholder():
    ctx = make_build_context()
    rendered = render_prompt("{output_dir_a} and {nonsense}", ctx)
    assert rendered == " and "


def test_render_prompt_artifacts_none_when_empty():
    ctx = make_build_context(artifacts=[])
    rendered = render_prompt("{artifacts}", ctx)
    assert rendered == "(none)"


def test_render_prompt_artifacts_inlines_small_utf8_text_file(tmp_path):
    schema_path = tmp_path / "task.schema.json"
    schema_path.write_text('{"type": "object"}', encoding="utf-8")
    artifact = Artifact(
        path="task.schema.json",
        kind="schema",
        note="Every Task must validate against this schema.",
        owner="store",
        resolved_paths=[schema_path],
    )
    ctx = make_build_context(artifacts=[artifact])
    rendered = render_prompt("{artifacts}", ctx)

    assert "task.schema.json" in rendered
    assert "(schema, from store)" in rendered
    assert "Every Task must validate against this schema." in rendered
    assert str(schema_path) in rendered
    assert "```json" in rendered
    assert '{"type": "object"}' in rendered


def test_render_prompt_artifacts_large_file_says_read_this_file(tmp_path):
    big_path = tmp_path / "big.csv"
    big_path.write_text("x" * (16 * 1024 + 1), encoding="utf-8")
    artifact = Artifact(path="big.csv", kind="fixture", owner="store", resolved_paths=[big_path])
    ctx = make_build_context(artifacts=[artifact])
    rendered = render_prompt("{artifacts}", ctx)

    assert "(read this file)" in rendered
    assert "x" * 100 not in rendered


def test_render_prompt_artifacts_binary_file_says_read_this_file(tmp_path):
    bin_path = tmp_path / "mockup.png"
    bin_path.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(range(256)))
    artifact = Artifact(path="mockup.png", kind="design", owner="store", resolved_paths=[bin_path])
    ctx = make_build_context(artifacts=[artifact])
    rendered = render_prompt("{artifacts}", ctx)

    assert "(read this file)" in rendered


def test_render_init_prompt_fills_variables():
    template = "{project_name}|{specifications}|{user_prompt}"
    rendered = render_init_prompt(template, "calculator", user_prompt="a calculator app")
    assert rendered.startswith("calculator|")
    assert "depends_on" in rendered
    assert rendered.endswith("|a calculator app")


def test_render_init_prompt_user_prompt_defaults_empty():
    rendered = render_init_prompt("[{user_prompt}]", "myproj")
    assert rendered == "[]"


def test_render_differencing_prompt():
    ctx = make_differencing_context()
    rendered = render_differencing_prompt("{output_dir_a}|{output_dir_b}|{project}|{response_file}", ctx)
    assert "out_a" in rendered
    assert "out_b" in rendered
    assert "A compiler of intent." in rendered
    assert "diff_response.json" in rendered


# ---------------------------------------------------------------------------
# Prompt bundling
# ---------------------------------------------------------------------------


def test_load_default_prompts_reads_bundled_files():
    templates = load_default_prompts()
    assert isinstance(templates, PromptTemplates)
    assert "{feature_name}" in templates.build
    assert "{validation}" in templates.validate_template
    assert "{seed_prompt}" in templates.plan


def test_load_default_prompts_build_validate_plan_carry_artifacts_placeholder():
    templates = load_default_prompts()
    assert "{artifacts}" in templates.build
    assert "{artifacts}" in templates.validate_template
    assert "{artifacts}" in templates.plan


def test_load_default_prompts_reads_bundled_init_prompt():
    templates = load_default_prompts()
    assert "{project_name}" in templates.init
    assert "{specifications}" in templates.init
    assert "{user_prompt}" in templates.init


# ---------------------------------------------------------------------------
# CLIAgent
# ---------------------------------------------------------------------------


def test_cli_agent_build_invokes_command_and_reads_response(tmp_path, monkeypatch):
    response_file = tmp_path / "response.json"
    response_file.write_text(
        json.dumps({"status": "success", "summary": "ok", "files_created": ["a.py"], "files_modified": []})
    )
    ctx = make_build_context(response_file_path=str(response_file), output_dir=str(tmp_path))

    captured_commands = []

    def fake_run(command, **kwargs):
        captured_commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="log line\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    profile = AgentProfile(name="test-cli", provider="cli", command="mytool --flag")
    logs = []
    agent = CLIAgent(profile, log=logs.append)

    result = agent.build(ctx)

    assert isinstance(result, BuildResponse)
    assert result.status == "success"
    assert not response_file.exists()
    assert captured_commands[0][:2] == ["mytool", "--flag"]
    assert any("log line" in line for line in logs)


def test_cli_agent_missing_command_raises():
    profile = AgentProfile(name="no-command", provider="cli")
    agent = CLIAgent(profile)
    ctx = make_build_context()
    with pytest.raises(AgentError):
        agent.build(ctx)


def test_cli_agent_missing_response_file_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0] if a else [], 0, stdout="", stderr=""))
    profile = AgentProfile(name="test-cli", provider="cli", command="mytool")
    agent = CLIAgent(profile)
    ctx = make_build_context(response_file_path=str(tmp_path / "missing.json"))
    with pytest.raises(AgentError):
        agent.build(ctx)


def test_cli_agent_invalid_json_raises(monkeypatch, tmp_path):
    response_file = tmp_path / "bad.json"
    response_file.write_text("{not valid json")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0] if a else [], 0, stdout="", stderr=""))
    profile = AgentProfile(name="test-cli", provider="cli", command="mytool")
    agent = CLIAgent(profile)
    ctx = make_build_context(response_file_path=str(response_file))
    with pytest.raises(AgentError):
        agent.build(ctx)


def test_cli_agent_validate_and_difference(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0] if a else [], 0, stdout="", stderr=""))
    profile = AgentProfile(name="test-cli", provider="cli", command="mytool")
    agent = CLIAgent(profile)

    validation_response_file = tmp_path / "validation.json"
    validation_response_file.write_text(json.dumps({"name": "check", "status": "pass", "reason": "good"}))
    ctx = make_build_context(response_file_path=str(validation_response_file))
    validation = Validation(name="check", type="agent_validation", args={"rubric": "check it"})
    result = agent.validate(ctx, validation)
    assert isinstance(result, ValidationResponse)
    assert result.status == "pass"

    diff_response_file = tmp_path / "diff.json"
    diff_response_file.write_text(
        json.dumps({"status": "equivalent", "dimensions": [], "summary": "same"})
    )
    diff_ctx = make_differencing_context(response_file_path=str(diff_response_file))
    diff_result = agent.difference(diff_ctx)
    assert isinstance(diff_result, DifferencingResponse)
    assert diff_result.status == "equivalent"


def test_cli_agent_plan_does_not_require_response_file(monkeypatch):
    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **k: calls.append(command) or subprocess.CompletedProcess(command, 0, stdout="", stderr=""),
    )
    profile = AgentProfile(name="test-cli", provider="cli", command="mytool")
    agent = CLIAgent(profile)
    ctx = make_build_context(seed_prompt="help me plan this")
    agent.plan(ctx)
    assert len(calls) == 1


def test_cli_agent_init_invokes_command_with_rendered_prompt(monkeypatch):
    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **k: calls.append(command) or subprocess.CompletedProcess(command, 0, stdout="", stderr=""),
    )
    profile = AgentProfile(
        name="test-cli",
        provider="cli",
        command="mytool",
        prompt_templates=PromptTemplates(init="{project_name}::{user_prompt}"),
    )
    agent = CLIAgent(profile)

    agent.init("myproj", "/tmp/intent", prompt="a calculator app")

    assert len(calls) == 1
    assert calls[0][-1] == "myproj::a calculator app"


# ---------------------------------------------------------------------------
# ClaudeAgent
# ---------------------------------------------------------------------------


class FakeStdout:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def __iter__(self):
        return iter(self._lines)


class FakePopen:
    instances: list["FakePopen"] = []

    def __init__(self, command, stdout=None, stderr=None, text=None):
        self.command = command
        self.stdout = FakeStdout(
            [
                json.dumps({"type": "system", "subtype": "init"}) + "\n",
                json.dumps(
                    {"type": "assistant", "message": {"content": [{"type": "text", "text": "working..."}]}}
                )
                + "\n",
                "not json\n",
                json.dumps({"type": "result", "result": "done"}) + "\n",
            ]
        )
        self.killed = False
        FakePopen.instances.append(self)

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True


@pytest.fixture(autouse=True)
def _reset_fake_popen():
    FakePopen.instances.clear()
    yield
    FakePopen.instances.clear()


def test_claude_agent_build_success(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    response_file = tmp_path / "response.json"
    response_file.write_text(json.dumps({"status": "success", "summary": "done", "files_created": [], "files_modified": []}))
    ctx = make_build_context(response_file_path=str(response_file), output_dir=str(tmp_path))
    logs = []
    profile = AgentProfile(name="claude-default", provider="claude")
    agent = ClaudeAgent(profile, log=logs.append)

    result = agent.build(ctx)

    assert result.status == "success"
    command = FakePopen.instances[0].command
    assert command[0] == "claude"
    assert "-p" in command
    assert "--output-format" in command
    assert "stream-json" in command
    assert "--permission-mode" in command
    assert "auto" in command
    assert "--permission-prompts" in command
    assert any("working..." in line for line in logs)


def test_claude_agent_build_missing_response_file_synthesizes(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (output_dir / "generated.py").write_text("print('hi')\n")
    ctx = make_build_context(response_file_path=str(tmp_path / "missing.json"), output_dir=str(output_dir))
    profile = AgentProfile(name="claude-default", provider="claude")
    agent = ClaudeAgent(profile)

    result = agent.build(ctx)

    assert result.status == "success"
    assert "generated.py" in result.files_created


def test_claude_agent_validate_missing_response_file_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    ctx = make_build_context(response_file_path=str(tmp_path / "missing.json"))
    profile = AgentProfile(name="claude-default", provider="claude")
    agent = ClaudeAgent(profile)
    validation = Validation(name="check", type="agent_validation", args={"rubric": "x"})
    with pytest.raises(AgentError):
        agent.validate(ctx, validation)


def test_claude_agent_bypass_permissions_flag(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    response_file = tmp_path / "response.json"
    response_file.write_text(json.dumps({"status": "success", "summary": "ok"}))
    ctx = make_build_context(response_file_path=str(response_file), output_dir=str(tmp_path))
    profile = AgentProfile(name="claude-yolo", provider="claude", permission_mode="bypassPermissions")
    agent = ClaudeAgent(profile)

    agent.build(ctx)

    command = FakePopen.instances[0].command
    assert "--dangerously-skip-permissions" in command
    assert "--permission-mode" not in command


def test_claude_agent_unknown_permission_mode_raises_before_launch(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    ctx = make_build_context(response_file_path=str(tmp_path / "response.json"))
    profile = AgentProfile(name="claude-bad", provider="claude", permission_mode="whatever")
    agent = ClaudeAgent(profile)

    with pytest.raises(AgentError):
        agent.build(ctx)

    assert FakePopen.instances == []


def test_claude_agent_model_and_effort_flags(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    response_file = tmp_path / "response.json"
    response_file.write_text(json.dumps({"status": "success", "summary": "ok"}))
    ctx = make_build_context(response_file_path=str(response_file), output_dir=str(tmp_path))
    profile = AgentProfile(name="claude-model", provider="claude", model_id="claude-sonnet-5", effort="high")
    agent = ClaudeAgent(profile)

    agent.build(ctx)

    command = FakePopen.instances[0].command
    assert "--model" in command
    assert "claude-sonnet-5" in command
    assert "--effort" in command
    assert "high" in command


def test_claude_agent_sandbox_settings_written_and_cleaned_up(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    monkeypatch.chdir(tmp_path)
    response_file = tmp_path / "response.json"
    response_file.write_text(json.dumps({"status": "success", "summary": "ok"}))
    ctx = make_build_context(response_file_path=str(response_file), output_dir=str(tmp_path))
    profile = AgentProfile(
        name="claude-sandboxed",
        provider="claude",
        sandbox_write_paths=[str(tmp_path)],
        sandbox_read_paths=["intent/"],
    )
    agent = ClaudeAgent(profile)

    settings_path = tmp_path / ".claude" / "settings.local.json"
    seen_during_run = {}

    original_wait = FakePopen.wait

    def wait_and_check(self, timeout=None):
        seen_during_run["exists"] = settings_path.exists()
        return original_wait(self, timeout=timeout)

    monkeypatch.setattr(FakePopen, "wait", wait_and_check)

    agent.build(ctx)

    assert seen_during_run["exists"] is True
    assert not settings_path.exists()


def test_claude_agent_no_sandbox_settings_when_paths_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    monkeypatch.chdir(tmp_path)
    response_file = tmp_path / "response.json"
    response_file.write_text(json.dumps({"status": "success", "summary": "ok"}))
    ctx = make_build_context(response_file_path=str(response_file), output_dir=str(tmp_path))
    profile = AgentProfile(name="claude-default", provider="claude")
    agent = ClaudeAgent(profile)

    agent.build(ctx)

    assert not (tmp_path / ".claude").exists()


def test_claude_agent_plan_uses_interactive_flags(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    profile = AgentProfile(name="claude-default", provider="claude")
    agent = ClaudeAgent(profile)
    ctx = make_build_context(seed_prompt="help me refine this feature")

    agent.plan(ctx)

    command = captured["command"]
    assert "-p" not in command
    assert "--permission-prompts" not in command
    assert "--permission-mode" in command
    assert "auto" in command


def test_claude_agent_init_interactive_uses_repl(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    profile = AgentProfile(
        name="claude-default",
        provider="claude",
        prompt_templates=PromptTemplates(init="init {project_name}"),
    )
    agent = ClaudeAgent(profile)

    agent.init("myproj", "/tmp/intent")

    command = captured["command"]
    assert command[0] == "claude"
    assert "-p" not in command
    assert "--permission-prompts" not in command
    assert command[-1] == "init myproj"
    assert FakePopen.instances == []


def test_claude_agent_init_one_shot_uses_noninteractive(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    profile = AgentProfile(
        name="claude-default",
        provider="claude",
        prompt_templates=PromptTemplates(init="init {project_name}: {user_prompt}"),
    )
    agent = ClaudeAgent(profile)

    agent.init("myproj", "/tmp/intent", prompt="a calculator app")

    assert len(FakePopen.instances) == 1
    command = FakePopen.instances[0].command
    assert command[0] == "claude"
    assert "-p" in command
    prompt_index = command.index("-p") + 1
    assert command[prompt_index] == "init myproj: a calculator app"


def test_claude_agent_init_one_shot_uses_sandbox_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    monkeypatch.chdir(tmp_path)
    intent_dir = tmp_path / "intent"
    profile = AgentProfile(
        name="claude-sandboxed",
        provider="claude",
        sandbox_write_paths=[str(intent_dir)],
        sandbox_read_paths=[str(intent_dir)],
    )
    agent = ClaudeAgent(profile)
    settings_path = tmp_path / ".claude" / "settings.local.json"
    seen_during_run = {}
    original_wait = FakePopen.wait

    def wait_and_check(self, timeout=None):
        seen_during_run["exists"] = settings_path.exists()
        return original_wait(self, timeout=timeout)

    monkeypatch.setattr(FakePopen, "wait", wait_and_check)

    agent.init("myproj", str(intent_dir), prompt="a calculator app")

    assert seen_during_run["exists"] is True
    assert not settings_path.exists()


# ---------------------------------------------------------------------------
# MockAgent
# ---------------------------------------------------------------------------


def test_mock_agent_init_records_calls():
    agent = MockAgent()
    agent.init("myproj", "/tmp/intent", prompt="a calculator app")
    agent.init("other", "/tmp/intent2")
    assert agent.init_calls == [
        ("myproj", "/tmp/intent", "a calculator app"),
        ("other", "/tmp/intent2", None),
    ]


def test_mock_agent_records_calls_and_returns_configured_responses():
    build_response = BuildResponse(status="success", summary="built", files_created=["x.py"])
    validation_response = ValidationResponse(name="v", status="fail", reason="nope")
    differencing_response = DifferencingResponse(status="divergent", dimensions=[], summary="diff")
    agent = MockAgent(
        name="mock-1",
        build_response=build_response,
        validation_response=validation_response,
        differencing_response=differencing_response,
    )

    ctx = make_build_context()
    diff_ctx = make_differencing_context()
    validation = Validation(name="v", type="agent_validation", args={"rubric": "x"})

    assert agent.build(ctx) is build_response
    assert agent.validate(ctx, validation) is validation_response
    assert agent.difference(diff_ctx) is differencing_response
    agent.plan(ctx)

    assert agent.build_calls == [ctx]
    assert agent.validate_calls == [(ctx, validation)]
    assert agent.difference_calls == [diff_ctx]
    assert agent.plan_calls == [ctx]
    assert agent.get_name() == "mock-1"
    assert agent.get_type() == "mock"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def test_create_from_profile_claude():
    profile = AgentProfile(name="c", provider="claude")
    agent = create_from_profile(profile)
    assert isinstance(agent, ClaudeAgent)


def test_create_from_profile_cli():
    profile = AgentProfile(name="c", provider="cli", command="tool")
    agent = create_from_profile(profile)
    assert isinstance(agent, CLIAgent)


def test_create_from_profile_unknown_raises():
    profile = AgentProfile(name="c", provider="codex")
    with pytest.raises(AgentError):
        create_from_profile(profile)


def test_agent_profile_defaults():
    profile = AgentProfile(name="default", provider="claude")
    assert profile.timeout == 3600.0
    assert profile.retries == 3
    assert profile.permission_mode == "auto"
    assert profile.sandbox_write_paths == []
    assert profile.sandbox_read_paths == []


def test_agent_is_abstract_interface():
    with pytest.raises(TypeError):
        Agent()
