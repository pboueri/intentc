"""Tests for the intentc CLI: config, commands, exit codes, and rendered output."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from intentc.build.agents import AgentError, AgentProfile, BuildContext, BuildResponse, MockAgent, ValidationResponse
from intentc.cli.config import Config, ConfigError, load_config, save_config
from intentc.cli.main import app

runner = CliRunner()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture()
def project(tmp_path: Path, monkeypatch) -> Path:
    """A three-feature project in a git repo, with a cli-provider config; cwd is the project root."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "T")
    intent = tmp_path / "intent"
    _write(intent / "project.ic", "---\nname: demo\n---\n\n# Demo\n")
    _write(intent / "implementations" / "default.ic", "---\nname: default\n---\n\nPython.\n")
    _write(intent / "models" / "models.ic", "---\nname: models\n---\n\nModels body.\n")
    _write(intent / "models" / "validations.icv", "target: models\nvalidations:\n  - name: models-exist\n    type: file_exists\n    args:\n      paths: ['models.py']\n")
    _write(intent / "store" / "store.ic", "---\nname: store\ndepends_on: [models]\n---\n\nStore body.\n")
    _write(intent / "store" / "validations.icv", "target: store\nvalidations:\n  - name: store-ok\n    args:\n      rubric: the store persists tasks to disk and reads them back\n")
    _write(intent / "api" / "api.ic", "---\nname: api\ndepends_on: [store]\n---\n\nAPI body.\n")
    _write(intent / "api" / "validations.icv", "target: api\nvalidations:\n  - name: api-ok\n    type: command_validation\n    args:\n      command: 'true'\n")
    _write(tmp_path / ".intentc" / "config.yaml", "default_profile:\n  name: default\n  provider: cli\n  command: echo\ndefault_output_dir: src\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "intent")
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture()
def mock_agent():
    """Route every agent creation (builder, suite, differencing, init) to one MockAgent that writes files."""
    agent = MockAgent()

    def build(ctx: BuildContext) -> BuildResponse:
        out = Path(ctx.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{ctx.intent.name}.py").write_text(f"# {ctx.intent.name}\n")
        return BuildResponse(status="success", summary=f"wrote {ctx.intent.name}.py", files_created=[f"{ctx.intent.name}.py"])

    agent.build_side_effect = build
    with patch("intentc.build.builder.builder.create_from_profile", return_value=agent), patch(
        "intentc.build.validations.create_from_profile", return_value=agent
    ), patch("intentc.differencing.differencing.create_from_profile", return_value=agent), patch(
        "intentc.cli.main.create_from_profile", return_value=agent
    ):
        yield agent


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class TestConfig:
    def test_defaults_when_missing(self, tmp_path: Path) -> None:
        config = load_config(tmp_path)
        assert config.default_output_dir == "src"
        assert config.default_profile.provider == "claude" and config.default_profile.retries == 3

    def test_save_and_load(self, tmp_path: Path) -> None:
        config = Config(default_profile=AgentProfile(name="fast", provider="claude", model_id="haiku", effort="low", timeout=60, retries=1), default_output_dir="out")
        path = save_config(config, tmp_path)
        assert path == tmp_path / ".intentc" / "config.yaml"
        loaded = load_config(tmp_path)
        assert loaded.default_profile.model_id == "haiku" and loaded.default_profile.effort == "low"
        assert loaded.default_profile.permission_mode == "auto" and "permission_mode: auto" in path.read_text()
        assert loaded.default_profile.timeout == 60 and loaded.default_output_dir == "out"

    def test_ignores_unknown_and_partial(self, tmp_path: Path) -> None:
        _write(tmp_path / ".intentc" / "config.yaml", "default_profile:\n  provider: cli\n  command: mytool\nfoo: bar\n")
        config = load_config(tmp_path)
        assert config.default_profile.name == "default" and config.default_profile.command == "mytool"
        assert config.default_output_dir == "src"

    def test_malformed_config_raises(self, tmp_path: Path) -> None:
        _write(tmp_path / ".intentc" / "config.yaml", "default_profile: [not a mapping]\n")
        with pytest.raises(ConfigError, match="default_profile"):
            load_config(tmp_path)
        _write(tmp_path / ".intentc" / "config.yaml", ": : :\n")
        with pytest.raises(ConfigError):
            load_config(tmp_path)
        _write(tmp_path / ".intentc" / "config.yaml", "default_profile:\n  provider: claude\n  retries: lots\n")
        with pytest.raises(ConfigError, match="retries"):
            load_config(tmp_path)


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


class TestInit:
    def test_no_interactive_creates_skeleton(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["init", "myproj", "--no-interactive"])
        assert result.exit_code == 0, result.output
        assert (tmp_path / "intent" / "project.ic").exists()
        assert (tmp_path / "intent" / "implementations" / "default.ic").exists()
        assert (tmp_path / "intent" / "starter" / "starter.ic").exists()
        assert (tmp_path / ".intentc" / "config.yaml").exists()
        assert "intentc check" in result.output and "myproj" in (tmp_path / "intent" / "project.ic").read_text()

    def test_default_name_is_directory(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        assert runner.invoke(app, ["init", "--no-interactive"]).exit_code == 0
        assert tmp_path.name in (tmp_path / "intent" / "project.ic").read_text()

    def test_refuses_to_overwrite(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        _write(tmp_path / "intent" / "project.ic", "---\nname: x\n---\n")
        result = runner.invoke(app, ["init", "--no-interactive"])
        assert result.exit_code == 2 and "already exists" in result.output

    def test_mutually_exclusive_flags(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        assert runner.invoke(app, ["init", "--no-interactive", "-P", "desc"]).exit_code == 2

    def test_interactive_and_oneshot_call_agent(self, tmp_path: Path, monkeypatch, mock_agent) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["init", "calc"])
        assert result.exit_code == 0, result.output
        assert mock_agent.init_calls == [("calc", str(tmp_path / "intent"), None)]
        assert (tmp_path / ".intentc" / "config.yaml").exists()

        (tmp_path / "two").mkdir(exist_ok=True)
        monkeypatch.chdir(tmp_path / "two")
        result = runner.invoke(app, ["init", "calc2", "-P", "A calculator"])
        assert result.exit_code == 0, result.output
        assert mock_agent.init_calls[-1] == ("calc2", str(tmp_path / "two" / "intent"), "A calculator")

    def test_agent_leaving_broken_project_fails(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        agent = MockAgent()
        agent.init = lambda name, intent_dir, prompt=None: _write(Path(intent_dir) / "bad" / "bad.ic", "---\nname: bad\ndepends_on: [ghost]\n---\n")  # type: ignore[method-assign]
        with patch("intentc.cli.main.create_from_profile", return_value=agent):
            result = runner.invoke(app, ["init", "x"])
        assert result.exit_code == 1 and "unknown dependency 'ghost'" in result.output


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


class TestCheck:
    def test_clean_project(self, project: Path) -> None:
        result = runner.invoke(app, ["check"])
        assert result.exit_code == 0, result.output
        assert "0 error(s), 0 warning(s) across 3 feature(s)" in result.output
        assert "api ← store" in result.output

    def test_warnings_do_not_fail_unless_strict(self, project: Path) -> None:
        (project / "intent" / "api" / "validations.icv").unlink()
        result = runner.invoke(app, ["check"])
        assert result.exit_code == 0 and "no validations" in result.output
        assert runner.invoke(app, ["check", "--strict"]).exit_code == 1

    def test_errors_fail(self, project: Path) -> None:
        _write(project / "intent" / "orphan" / "v.icv", "target: orphan\nvalidations: []\n")
        result = runner.invoke(app, ["check"])
        assert result.exit_code == 1 and "no intent to validate" in result.output

    def test_parse_errors_exit_2_without_traceback(self, project: Path) -> None:
        _write(project / "intent" / "api" / "api.ic", "---\nname: api\ndepends_on: [stor]\n---\n")
        result = runner.invoke(app, ["check"])
        assert result.exit_code == 2
        assert "problem(s) in" in result.output and "did you mean 'store'" in result.output
        assert "Traceback" not in result.output

    def test_missing_intent_dir(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["check"])
        assert result.exit_code == 2 and "intentc init" in result.output


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


class TestBuild:
    def test_full_build(self, project: Path, mock_agent) -> None:
        result = runner.invoke(app, ["build"])
        assert result.exit_code == 0, result.output
        assert "Build plan: 3 target(s): models, store, api" in result.output
        assert "3 built, 0 failed" in result.output
        assert "All targets built." in result.output
        assert (project / "src" / "models.py").exists()
        assert len(mock_agent.build_calls) == 3 and len(mock_agent.validate_calls) == 1
        log = subprocess.run(["git", "log", "--oneline"], cwd=project, capture_output=True, text=True).stdout
        assert "build api [gen:" in log

    def test_targeted_build_and_next_hint(self, project: Path, mock_agent) -> None:
        result = runner.invoke(app, ["build", "models"])
        assert result.exit_code == 0, result.output
        assert "Next you can build: store" in result.output

    def test_dry_run_renders_plan_only(self, project: Path, mock_agent) -> None:
        result = runner.invoke(app, ["build", "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "Build plan (dry run) — 3 target(s):" in result.output
        assert "1. models" in result.output and "Build Results" not in result.output
        assert mock_agent.build_calls == []

    def test_nothing_to_build(self, project: Path, mock_agent) -> None:
        assert runner.invoke(app, ["build"]).exit_code == 0
        result = runner.invoke(app, ["build"])
        assert result.exit_code == 0 and "Nothing to build" in result.output and "--force" in result.output

    def test_failure_shows_step_and_hint(self, project: Path, mock_agent) -> None:
        def crash(ctx: BuildContext) -> BuildResponse:
            if ctx.intent.name == "store":
                raise AgentError("store exploded")
            Path(ctx.output_dir).mkdir(exist_ok=True)
            (Path(ctx.output_dir) / f"{ctx.intent.name}.py").write_text("")
            return BuildResponse(status="success", summary="ok")

        mock_agent.build_side_effect = crash
        result = runner.invoke(app, ["build"])
        assert result.exit_code == 1
        assert "'store' failed in step 'build'" in result.output
        assert "store exploded" in result.output
        assert "intentc build store" in result.output
        assert "1 built, 1 failed" in result.output

    def test_validation_failure_reasons_are_shown(self, project: Path, mock_agent) -> None:
        mock_agent.validate_side_effect = lambda ctx, v: ValidationResponse(name=v.name, status="fail", reason="tasks vanish on reload")
        result = runner.invoke(app, ["build", "-p", "default"])
        assert result.exit_code == 1
        assert "failed in step 'validate'" in result.output and "tasks vanish on reload" in result.output

    def test_unknown_target_is_usage_error(self, project: Path) -> None:
        result = runner.invoke(app, ["build", "does/not/exist"])
        assert result.exit_code == 2 and "Unknown feature 'does/not/exist'" in result.output and "Traceback" not in result.output

    def test_unknown_implementation_is_usage_error(self, project: Path) -> None:
        result = runner.invoke(app, ["build", "-i", "rust"])
        assert result.exit_code == 2 and "Implementation 'rust' not found" in result.output

    def test_check_errors_block_build(self, project: Path, mock_agent) -> None:
        _write(project / "intent" / "orphan" / "v.icv", "target: orphan\nvalidations: []\n")
        result = runner.invoke(app, ["build"])
        assert result.exit_code == 2 and "intentc check" in result.output and mock_agent.build_calls == []

    def test_broken_config_is_usage_error(self, project: Path) -> None:
        _write(project / ".intentc" / "config.yaml", "default_profile: 12\n")
        result = runner.invoke(app, ["build"])
        assert result.exit_code == 2 and "config.yaml" in result.output

    def test_missing_agent_binary(self, project: Path, monkeypatch) -> None:
        _write(project / ".intentc" / "config.yaml", "default_profile:\n  provider: claude\n")
        monkeypatch.setenv("PATH", str(project))
        result = runner.invoke(app, ["build", "models", "-p", "default"])
        # With retries the build fails rather than crashing; the agent error is reported in the failure table.
        assert result.exit_code == 1 and "Is Claude Code installed" in result.output and "Traceback" not in result.output

    def test_not_a_git_repo_hint(self, tmp_path: Path, monkeypatch, mock_agent) -> None:
        monkeypatch.chdir(tmp_path)
        _write(tmp_path / "intent" / "project.ic", "---\nname: p\n---\n")
        _write(tmp_path / "intent" / "a" / "a.ic", "---\nname: a\n---\n\nBody.\n")
        _write(tmp_path / "intent" / "a" / "v.icv", "target: a\nvalidations:\n  - name: x\n    type: command_validation\n    args:\n      command: 'true'\n")
        result = runner.invoke(app, ["build"])
        assert result.exit_code == 1 and "git" in result.output.lower() and "Traceback" not in result.output


# ---------------------------------------------------------------------------
# validate / status / clean / diff / log / compare / plan
# ---------------------------------------------------------------------------


class TestValidate:
    def test_validate_all(self, project: Path, mock_agent) -> None:
        runner.invoke(app, ["build"])
        result = runner.invoke(app, ["validate"])
        assert result.exit_code == 0, result.output
        assert "3/3 passed, 0 error(s), 0 warning(s)" in result.output

    def test_warning_only_failures_exit_zero(self, project: Path, mock_agent) -> None:
        _write(project / "intent" / "api" / "validations.icv", "target: api\nvalidations:\n  - name: soft\n    type: command_validation\n    severity: warning\n    args:\n      command: 'false'\n")
        result = runner.invoke(app, ["validate", "api"])
        assert result.exit_code == 0, result.output
        assert "warning" in result.output and "0/1 passed, 0 error(s), 1 warning(s)" in result.output

    def test_error_failure_exits_one(self, project: Path, mock_agent) -> None:
        result = runner.invoke(app, ["validate", "models"])  # nothing built yet → models.py missing
        assert result.exit_code == 1 and "missing in src: models.py" in result.output

    def test_unknown_target(self, project: Path) -> None:
        assert runner.invoke(app, ["validate", "nope"]).exit_code == 2


class TestStatus:
    def test_status_before_and_after_build(self, project: Path, mock_agent) -> None:
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0, result.output
        assert "pending" in result.output and "Nothing built yet" in result.output
        runner.invoke(app, ["build", "models"])
        result = runner.invoke(app, ["status"])
        assert "built" in result.output and "Next you can build: store" in result.output

    def test_status_marks_stale_and_filters(self, project: Path, mock_agent) -> None:
        runner.invoke(app, ["build"])
        ic = project / "intent" / "models" / "models.ic"
        ic.write_text(ic.read_text() + "\nMore.\n")
        result = runner.invoke(app, ["status", "--outdated"])
        assert result.exit_code == 0, result.output
        assert "outdated" in result.output and "models" in result.output and "api" in result.output


class TestCleanDiffLog:
    def test_clean_and_diff(self, project: Path, mock_agent) -> None:
        runner.invoke(app, ["build"])
        result = runner.invoke(app, ["diff", "models"])
        assert result.exit_code == 0, result.output
        assert "models.py" in result.output and "1 created" in result.output
        assert runner.invoke(app, ["diff", "models", "--stat"]).exit_code == 0
        result = runner.invoke(app, ["clean", "models"])
        assert result.exit_code == 0, result.output
        assert "Cleaned 'models'" in result.output
        assert not (project / "src" / "models.py").exists()  # reverted to before the checkpoint
        status = runner.invoke(app, ["status"]).output
        assert "outdated" in status and "pending" in status
        assert runner.invoke(app, ["clean", "--all"]).exit_code == 0
        assert runner.invoke(app, ["clean"]).exit_code == 2
        assert runner.invoke(app, ["clean", "nope"]).exit_code == 2

    def test_diff_and_log_without_build(self, project: Path) -> None:
        result = runner.invoke(app, ["diff", "models"])
        assert result.exit_code == 2 and "intentc build models" in result.output
        assert runner.invoke(app, ["log", "models"]).exit_code == 2

    def test_log_shows_history(self, project: Path, mock_agent) -> None:
        runner.invoke(app, ["build", "models"])
        runner.invoke(app, ["build", "models", "--force"])
        result = runner.invoke(app, ["log", "models"])
        assert result.exit_code == 0, result.output
        assert "Build history for models" in result.output and "Latest build steps" in result.output
        assert result.output.count("built") >= 2 and "models-exist" in result.output


class TestCompareAndPlan:
    def test_compare(self, project: Path, mock_agent) -> None:
        (project / "a").mkdir()
        (project / "b").mkdir()
        result = runner.invoke(app, ["compare", "a", "b"])
        assert result.exit_code == 0, result.output
        assert "equivalent" in result.output and len(mock_agent.difference_calls) == 1
        assert runner.invoke(app, ["compare", "a", "missing"]).exit_code == 2

    def test_plan_creates_missing_feature(self, project: Path, mock_agent) -> None:
        result = runner.invoke(app, ["plan", "core/new_thing", "make it do the thing"])
        assert result.exit_code == 0, result.output
        assert (project / "intent" / "core" / "new_thing" / "new_thing.ic").exists()
        assert "Created new feature" in result.output
        ctx = mock_agent.plan_calls[0]
        assert ctx.seed_prompt == "make it do the thing" and ctx.feature_path == "core/new_thing"

    def test_plan_existing_feature(self, project: Path, mock_agent) -> None:
        assert runner.invoke(app, ["plan", "store", "add deletion"]).exit_code == 0
        assert mock_agent.plan_calls[0].dependency_names == ["models"]


def test_help_lists_all_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ["init", "check", "build", "validate", "clean", "plan", "status", "diff", "log", "compare"]:
        assert command in result.output


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    assert "Usage" in result.output
