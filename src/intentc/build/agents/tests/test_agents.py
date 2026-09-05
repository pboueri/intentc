"""Tests for the agents module."""

from __future__ import annotations

import json
import os
import stat
import sys
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
    read_response_json,
    render_differencing_prompt,
    render_init_prompt,
    render_prompt,
)
from intentc.core import Implementation, IntentFile, ProjectIntent, Severity, Validation, ValidationFile


def _ctx(tmp_path: Path, **overrides) -> BuildContext:
    fields = dict(
        intent=IntentFile(name="models", body="Build the models."),
        validations=[
            ValidationFile(
                target="models",
                validations=[
                    Validation(name="v1", args={"rubric": "must be good\nand thorough"}),
                    Validation(name="v2", type="command_validation", severity=Severity.WARNING, args={"command": "exit 0"}),
                ],
            )
        ],
        output_dir="src",
        generation_id="gen-1",
        dependency_names=["core/a", "core/b"],
        project_intent=ProjectIntent(name="p", body="PROJECT BODY"),
        implementation=Implementation(name="default", body="IMPL BODY"),
        response_file_path=str(tmp_path / "resp.json"),
        feature_path="core/models",
    )
    fields.update(overrides)
    return BuildContext(**fields)


# ---------------------------------------------------------------------------
# Types and defaults
# ---------------------------------------------------------------------------


def test_profile_defaults() -> None:
    p = AgentProfile(name="d", provider="claude")
    assert p.timeout == 3600.0
    assert p.retries == 3
    assert p.model_id is None and p.effort is None
    assert p.sandbox_read_paths == [] and p.sandbox_write_paths == []


def test_responses_ignore_unknown_fields() -> None:
    assert BuildResponse(status="success", summary="s", extra="x").status == "success"
    assert ValidationResponse(name="n", status="pass", reason="r", nonsense=1).severity == "error"
    r = DifferencingResponse(status="divergent", dimensions=[{"name": "a", "status": "fail", "rationale": "x", "zzz": 1}], summary="s")
    assert isinstance(r.dimensions[0], DimensionResult)


def test_build_context_defaults(tmp_path: Path) -> None:
    ctx = BuildContext(
        intent=IntentFile(name="x"), output_dir="o", generation_id="g", project_intent=ProjectIntent(name="p"), response_file_path=""
    )
    assert ctx.validations == [] and ctx.dependency_names == [] and ctx.previous_errors == []
    assert ctx.seed_prompt == "" and ctx.feature_path == "" and ctx.implementation is None


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


def test_load_default_prompts_bundled() -> None:
    t = load_default_prompts()
    assert "{response_file}" in t.build
    assert "{validation}" in t.validate_template
    assert "{seed_prompt}" in t.plan
    assert "{output_dir_a}" in t.difference
    assert "{project_name}" in t.init


def test_render_prompt_substitutes_everything(tmp_path: Path) -> None:
    t = load_default_prompts()
    ctx = _ctx(tmp_path, previous_errors=["boom happened"])
    text = render_prompt(t.build, ctx)
    for needle in ["PROJECT BODY", "IMPL BODY", "Build the models.", "core/models", "`src`", "- core/a", "- core/b",
                   "name: v1", "rubric: |", "and thorough", "name: v2", "severity: warning", "boom happened", str(tmp_path / "resp.json")]:
        assert needle in text, needle
    assert "{" not in text.replace("{{", "").replace("}}", "").replace('{\n  "status"', "")


def test_render_prompt_json_braces_survive(tmp_path: Path) -> None:
    text = render_prompt(load_default_prompts().build, _ctx(tmp_path))
    assert '"status": "success" or "failure"' in text
    assert "{{" not in text


def test_render_prompt_no_previous_errors_section(tmp_path: Path) -> None:
    text = render_prompt(load_default_prompts().build, _ctx(tmp_path))
    assert "Previous attempts failed" not in text


def test_render_prompt_single_validation(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    text = render_prompt(load_default_prompts().validate_template, ctx, ctx.validations[0].validations[1])
    assert "name: v2" in text and "name: v1" not in text


def test_render_prompt_never_raises_on_unknown_placeholder(tmp_path: Path) -> None:
    assert render_prompt("hello {unknown} {feature_name}", _ctx(tmp_path)) == "hello {unknown} core/models"


def test_render_prompt_empty_dependencies_and_validations(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, dependency_names=[], validations=[], implementation=None)
    text = render_prompt("{dependencies}|{validations}|{implementation}", ctx)
    assert text == "(none)|(no validations defined)|"


def test_render_differencing_prompt(tmp_path: Path) -> None:
    ctx = DifferencingContext(
        output_dir_a="/a", output_dir_b="/b", project_intent=ProjectIntent(name="p", body="PB"),
        implementation=Implementation(name="i", body="IB"), response_file_path="/r.json",
    )
    text = render_differencing_prompt(load_default_prompts().difference, ctx)
    assert "/a" in text and "/b" in text and "PB" in text and "IB" in text and "/r.json" in text
    assert "{output_dir_a}" not in text


def test_render_init_prompt() -> None:
    t = load_default_prompts().init
    interactive = render_init_prompt(t, "calc")
    assert "calc" in interactive and "command_validation" in interactive
    oneshot = render_init_prompt(t, "calc", "A calculator app")
    assert "A calculator app" in oneshot and "without asking questions" in oneshot


# ---------------------------------------------------------------------------
# read_response_json
# ---------------------------------------------------------------------------


def test_read_response_json_errors(tmp_path: Path) -> None:
    with pytest.raises(AgentError, match="not found"):
        read_response_json(str(tmp_path / "missing.json"))
    empty = tmp_path / "empty.json"
    empty.write_text("")
    with pytest.raises(AgentError, match="empty"):
        read_response_json(str(empty))
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(AgentError, match="invalid JSON"):
        read_response_json(str(bad))
    lst = tmp_path / "list.json"
    lst.write_text("[1]")
    with pytest.raises(AgentError, match="JSON object"):
        read_response_json(str(lst))
    with pytest.raises(AgentError):
        read_response_json("")


# ---------------------------------------------------------------------------
# MockAgent
# ---------------------------------------------------------------------------


def test_mock_agent_records_calls_and_writes_response_files(tmp_path: Path) -> None:
    agent = MockAgent()
    assert isinstance(agent, Agent)
    assert agent.get_type() == "mock" and agent.get_name() == "mock"
    ctx = _ctx(tmp_path)
    resp = agent.build(ctx)
    assert resp.status == "success"
    assert json.loads(Path(ctx.response_file_path).read_text())["status"] == "success"
    assert agent.build_calls == [ctx]

    v = ctx.validations[0].validations[0]
    vresp = agent.validate(ctx, v)
    assert vresp.name == "v1" and vresp.status == "pass"
    assert agent.validate_calls[0][1] is v

    dctx = DifferencingContext(output_dir_a="a", output_dir_b="b", project_intent=ProjectIntent(name="p"), response_file_path=str(tmp_path / "d.json"))
    assert agent.difference(dctx).status == "equivalent"
    assert agent.difference_calls == [dctx]
    agent.plan(ctx)
    agent.init("proj", "intent", None)
    assert agent.plan_calls == [ctx] and agent.init_calls == [("proj", "intent", None)]


def test_mock_agent_side_effects(tmp_path: Path) -> None:
    def boom(ctx: BuildContext) -> BuildResponse:
        raise AgentError("crash")

    agent = MockAgent(build_side_effect=boom)
    with pytest.raises(AgentError):
        agent.build(_ctx(tmp_path))
    agent2 = MockAgent(validate_side_effect=lambda ctx, v: ValidationResponse(name=v.name, status="fail", reason="nope"))
    assert agent2.validate(_ctx(tmp_path), Validation(name="z", args={"rubric": "r"})).status == "fail"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def test_create_from_profile() -> None:
    assert isinstance(create_from_profile(AgentProfile(name="a", provider="claude")), ClaudeAgent)
    assert isinstance(create_from_profile(AgentProfile(name="a", provider="cli", command="cat")), CLIAgent)
    with pytest.raises(AgentError, match="Unknown agent provider 'codex'"):
        create_from_profile(AgentProfile(name="a", provider="codex"))


# ---------------------------------------------------------------------------
# CLIAgent with a real subprocess (a tiny python script as the "agent")
# ---------------------------------------------------------------------------


def _write_script(tmp_path: Path, body: str) -> str:
    script = tmp_path / "agent.py"
    script.write_text(body)
    return f"{sys.executable} {script}"


def test_cli_agent_build_via_response_file(tmp_path: Path) -> None:
    command = _write_script(
        tmp_path,
        "import os, sys, json\n"
        "prompt = sys.stdin.read()\n"
        "assert 'Build the models.' in prompt\n"
        "json.dump({'status': 'success', 'summary': 'did it', 'files_created': ['x.py']}, open(os.environ['INTENTC_RESPONSE_FILE'], 'w'))\n"
        "print('working...')\n",
    )
    logs: list[str] = []
    agent = create_from_profile(AgentProfile(name="p", provider="cli", command=command), log=logs.append)
    resp = agent.build(_ctx(tmp_path))
    assert resp.summary == "did it" and resp.files_created == ["x.py"]
    assert any("working..." in l for l in logs)
    assert agent.get_type() == "cli" and agent.get_name() == "p"


def test_cli_agent_validate_and_difference(tmp_path: Path) -> None:
    command = _write_script(
        tmp_path,
        "import os, sys, json\n"
        "prompt = sys.stdin.read()\n"
        "if 'Reference directory' in prompt:\n"
        "    data = {'status': 'divergent', 'dimensions': [{'name': 'public_api', 'status': 'fail', 'rationale': 'r'}], 'summary': 's'}\n"
        "else:\n"
        "    data = {'name': 'v1', 'status': 'pass', 'reason': 'fine'}\n"
        "json.dump(data, open(os.environ['INTENTC_RESPONSE_FILE'], 'w'))\n",
    )
    agent = CLIAgent(AgentProfile(name="p", provider="cli", command=command))
    ctx = _ctx(tmp_path)
    assert agent.validate(ctx, ctx.validations[0].validations[0]).status == "pass"
    dctx = DifferencingContext(output_dir_a="a", output_dir_b="b", project_intent=ProjectIntent(name="p"), response_file_path=str(tmp_path / "d.json"))
    assert agent.difference(dctx).status == "divergent"


def test_cli_agent_failures(tmp_path: Path) -> None:
    failing = _write_script(tmp_path, "import sys; print('bad', file=sys.stderr); sys.exit(3)\n")
    agent = CLIAgent(AgentProfile(name="p", provider="cli", command=failing))
    with pytest.raises(AgentError, match="exit 3"):
        agent.build(_ctx(tmp_path))
    no_file = _write_script(tmp_path, "import sys; sys.stdin.read()\n")
    with pytest.raises(AgentError, match="not found"):
        CLIAgent(AgentProfile(name="p", provider="cli", command=no_file)).build(_ctx(tmp_path))
    with pytest.raises(AgentError, match="no 'command'"):
        CLIAgent(AgentProfile(name="p", provider="cli")).build(_ctx(tmp_path))
    with pytest.raises(AgentError, match="Failed to run"):
        CLIAgent(AgentProfile(name="p", provider="cli", command="/definitely/not/here")).build(_ctx(tmp_path))


def test_cli_agent_timeout(tmp_path: Path) -> None:
    slow = _write_script(tmp_path, "import time; time.sleep(5)\n")
    agent = CLIAgent(AgentProfile(name="p", provider="cli", command=slow, timeout=0.5))
    with pytest.raises(AgentError, match="timed out"):
        agent.build(_ctx(tmp_path))


# ---------------------------------------------------------------------------
# ClaudeAgent (command construction and sandbox settings only — no real claude)
# ---------------------------------------------------------------------------


def test_claude_command_flags() -> None:
    agent = ClaudeAgent(AgentProfile(name="c", provider="claude", model_id="opus", effort="high", cli_args=["--foo"]))
    cmd = agent.build_command("PROMPT")
    assert cmd[:3] == ["claude", "-p", "PROMPT"]
    for flag in ["--verbose", "--output-format", "stream-json", "--dangerously-skip-permissions", "--model", "opus", "--effort", "high", "--foo"]:
        assert flag in cmd
    assert cmd[cmd.index("--model") + 1] == "opus"
    interactive = agent.interactive_command("PROMPT")
    assert interactive[0] == "claude" and "-p" not in interactive and interactive[-1] == "PROMPT"
    assert "--model" in interactive and "--foo" in interactive
    assert agent.get_type() == "claude"


def test_claude_sandbox_settings(tmp_path: Path) -> None:
    plain = ClaudeAgent(AgentProfile(name="c", provider="claude"))
    assert plain.sandbox_settings() is None
    boxed = ClaudeAgent(AgentProfile(name="c", provider="claude", sandbox_write_paths=["/out"], sandbox_read_paths=["/out", "/intent"]))
    settings = boxed.sandbox_settings()
    assert settings["sandbox"] == {"enabled": True, "write_paths": ["/out"], "read_paths": ["/out", "/intent"]}
    assert "Bash(*)" in settings["permissions"]["allow"]
    path = boxed._write_sandbox_settings(str(tmp_path))
    assert path == str(tmp_path / ".claude" / "settings.local.json")
    assert json.loads(Path(path).read_text())["sandbox"]["enabled"] is True


def test_claude_stream_json_parsing(tmp_path: Path) -> None:
    logs: list[str] = []
    agent = ClaudeAgent(AgentProfile(name="c", provider="claude"), log=logs.append)
    agent._handle_stream_line(json.dumps({"type": "system", "subtype": "init"}))
    agent._handle_stream_line("not json at all")
    agent._handle_stream_line(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "line one\nline two"}, {"type": "tool_use"}]}}))
    agent._handle_stream_line(json.dumps({"type": "result", "result": "done"}))
    assert logs == ["    agent: line one", "    agent: line two"]


def test_claude_agent_runs_fake_binary(tmp_path: Path, monkeypatch) -> None:
    """A fake `claude` on PATH proves streaming, sandbox cleanup and response handling end to end."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "claude"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys, os\n"
        "print(json.dumps({'type': 'system', 'subtype': 'init'}))\n"
        "print(json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'hello from fake'}]}}))\n"
        "assert os.path.exists(os.path.join(os.getcwd(), '.claude', 'settings.local.json'))\n"
        "print(json.dumps({'type': 'result', 'result': 'ok'}))\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "gen.py").write_text("x = 1\n")
    logs: list[str] = []
    agent = ClaudeAgent(AgentProfile(name="c", provider="claude", sandbox_write_paths=[str(tmp_path / "src")]), log=logs.append)
    resp = agent.build(_ctx(tmp_path, response_file_path=str(tmp_path / "never-written.json")))
    assert resp.status == "success"
    assert resp.files_created == ["gen.py"]  # synthesised from the output directory
    assert any("hello from fake" in l for l in logs)
    assert not (tmp_path / ".claude" / "settings.local.json").exists()  # cleaned up

    with pytest.raises(AgentError, match="not found"):
        agent.validate(_ctx(tmp_path, response_file_path=str(tmp_path / "nope.json")), Validation(name="v", args={"rubric": "r"}))


def test_claude_missing_binary(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    agent = ClaudeAgent(AgentProfile(name="c", provider="claude"))
    with pytest.raises(AgentError, match="Is Claude Code installed"):
        agent.build(_ctx(tmp_path))


def test_prompt_templates_override(tmp_path: Path) -> None:
    templates = PromptTemplates(build="CUSTOM {feature}")
    agent = CLIAgent(AgentProfile(name="p", provider="cli", command="cat", prompt_templates=templates))
    assert agent._templates.build == "CUSTOM {feature}"
