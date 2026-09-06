"""Tests for the intentc CLI commands.

Uses typer's CliRunner against real temporary intent/ projects. `Builder` is
monkeypatched with fakes for the build/validate UX tests so no real agent or
git repository is required; the deterministic commands (check, status/log/diff
plumbing via storage) exercise the real project loader and a real SQLite
state backend.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pytest
from typer.testing import CliRunner

from intentc.build.agents import AgentError, AgentProfile, ValidationResponse
from intentc.build.builder.builder import BuildOptions
from intentc.build.state import TargetStatus
from intentc.build.storage import BuildResult, BuildStep
from intentc.build.validations import ValidationSuiteResult
from intentc.cli import main
from intentc.core import (
    Implementation,
    IntentFile,
    ProjectIntent,
    Severity,
    Validation,
    ValidationFile,
    ValidationType,
    write_intent_file,
    write_validation_file,
)

runner = CliRunner()


def _write_project(tmp_path: Path) -> Path:
    """models <- api, both with a passing agent_validation."""
    intent_dir = tmp_path / "intent"

    project_intent = ProjectIntent(name="demo", body="A demo project.")
    write_intent_file(project_intent, intent_dir / "project.ic")

    implementation = Implementation(name="default", body="Python 3.11.")
    write_intent_file(implementation, intent_dir / "implementations" / "default.ic")

    models_intent = IntentFile(name="models", depends_on=[], body="Define the models.")
    write_intent_file(models_intent, intent_dir / "models" / "models.ic")
    models_vf = ValidationFile(
        target="models",
        version=1,
        validations=[
            Validation(
                name="models-check",
                type=ValidationType.FILE_EXISTS.value,
                severity=Severity.ERROR,
                args={"paths": ["README.md"]},
            )
        ],
    )
    write_validation_file(models_vf, intent_dir / "models" / "validation.icv")

    api_intent = IntentFile(name="api", depends_on=["models"], body="Define the API.")
    write_intent_file(api_intent, intent_dir / "api" / "api.ic")
    api_vf = ValidationFile(
        target="api",
        version=1,
        validations=[
            Validation(
                name="api-check",
                type=ValidationType.FILE_EXISTS.value,
                severity=Severity.ERROR,
                args={"paths": ["README.md"]},
            )
        ],
    )
    write_validation_file(api_vf, intent_dir / "api" / "api.icv")

    return intent_dir


class _RejectBuilder:
    """Fails the test if constructed -- used to prove a command exits before
    wiring the builder."""

    def __init__(self, *args, **kwargs) -> None:
        raise AssertionError("Builder should not have been constructed")


class FakeBuilder:
    """Stands in for `intentc.build.builder.builder.Builder` in CLI tests."""

    def __init__(self, project, state_manager, version_control, agent_profile, log=None, **_kwargs) -> None:
        self.project = project
        self.state_manager = state_manager
        self.version_control = version_control
        self.agent_profile = agent_profile
        self.log = log
        self.build_opts: Optional[BuildOptions] = None
        self.build_return = ([], None)
        self.validate_return: ValidationSuiteResult | list[ValidationSuiteResult] = ValidationSuiteResult(
            target="", results=[], passed=True, summary="0/0 passed, 0 error(s), 0 warning(s)"
        )
        self.next_targets_return: list[str] = []
        self.validate_calls: list[tuple[str, str]] = []

    def build(self, opts: BuildOptions):
        self.build_opts = opts
        return self.build_return

    def validate(self, target: str, output_dir: str):
        self.validate_calls.append((target, output_dir))
        return self.validate_return

    def next_targets(self) -> list[str]:
        return self.next_targets_return

    def refresh_outdated(self) -> list[str]:
        return []

    def clean(self, target: str, output_dir: str) -> None:
        self.cleaned = target

    def clean_all(self, output_dir: str) -> None:
        self.cleaned_all = True


@pytest.fixture()
def project_dir(tmp_path: Path, monkeypatch) -> Path:
    _write_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# App-level behavior
# ---------------------------------------------------------------------------


class TestAppBasics:
    def test_no_args_shows_help(self) -> None:
        result = runner.invoke(main.app, [])
        assert "Usage" in result.output

    def test_help_mentions_check(self) -> None:
        result = runner.invoke(main.app, ["--help"])
        assert result.exit_code == 0
        assert "check" in result.output


class TestMissingProject:
    def test_check_without_intent_dir_exits_2(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(main.app, ["check"])
        assert result.exit_code == 2
        assert "Run 'intentc init'" in result.output


class TestUnknownTarget:
    def test_build_unknown_target_exits_2(self, project_dir: Path) -> None:
        result = runner.invoke(main.app, ["build", "does/not/exist"])
        assert result.exit_code == 2
        assert "Unknown feature 'does/not/exist'" in result.output
        assert "Available:" in result.output


class TestMalformedConfig:
    def test_build_with_malformed_config_exits_2(self, project_dir: Path) -> None:
        config_path = project_dir / ".intentc" / "config.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("default_profile: not-a-mapping\n", encoding="utf-8")

        result = runner.invoke(main.app, ["build"])

        assert result.exit_code == 2
        assert str(config_path) in result.output


# ---------------------------------------------------------------------------
# build UX
# ---------------------------------------------------------------------------


class TestBuildUX:
    def test_refuses_when_check_reports_errors(self, tmp_path: Path, monkeypatch) -> None:
        # A validations.icv with no accompanying .ic file is a check_project error.
        intent_dir = tmp_path / "intent"
        project_intent = ProjectIntent(name="demo", body="A demo project.")
        write_intent_file(project_intent, intent_dir / "project.ic")
        implementation = Implementation(name="default", body="Python 3.11.")
        write_intent_file(implementation, intent_dir / "implementations" / "default.ic")
        orphan_vf = ValidationFile(target="orphan", version=1, validations=[])
        write_validation_file(orphan_vf, intent_dir / "orphan" / "validation.icv")

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(main, "Builder", _RejectBuilder)

        result = runner.invoke(main.app, ["build"])

        assert result.exit_code == 2

    def test_dry_run_renders_plan_not_results_table(self, project_dir: Path, monkeypatch) -> None:
        fake = FakeBuilder(None, None, None, None)
        fake.build_return = (
            [
                BuildResult(target="models", status=TargetStatus.PENDING),
                BuildResult(target="api", status=TargetStatus.PENDING),
            ],
            None,
        )
        monkeypatch.setattr(main, "Builder", lambda *a, **kw: fake)

        result = runner.invoke(main.app, ["build", "--dry-run"])

        assert result.exit_code == 0
        assert "Build plan (dry run)" in result.output
        assert "1. models" in result.output
        assert "Build Results" not in result.output
        assert fake.build_opts is not None
        assert fake.build_opts.dry_run is True

    def test_success_prints_next_targets(self, project_dir: Path, monkeypatch) -> None:
        fake = FakeBuilder(None, None, None, None)
        fake.build_return = (
            [BuildResult(target="models", status=TargetStatus.BUILT, attempts=1, total_duration_secs=0.5)],
            None,
        )
        fake.next_targets_return = ["api"]
        monkeypatch.setattr(main, "Builder", lambda *a, **kw: fake)

        result = runner.invoke(main.app, ["build", "models"])

        assert result.exit_code == 0
        assert "Next you can build: api" in result.output

    def test_success_with_nothing_left_prints_all_built(self, project_dir: Path, monkeypatch) -> None:
        fake = FakeBuilder(None, None, None, None)
        fake.build_return = (
            [BuildResult(target="api", status=TargetStatus.BUILT, attempts=1, total_duration_secs=0.5)],
            None,
        )
        fake.next_targets_return = []
        monkeypatch.setattr(main, "Builder", lambda *a, **kw: fake)

        result = runner.invoke(main.app, ["build"])

        assert result.exit_code == 0
        assert "All targets built." in result.output

    def test_nothing_to_build_message(self, project_dir: Path, monkeypatch) -> None:
        fake = FakeBuilder(None, None, None, None)
        fake.build_return = ([], None)
        monkeypatch.setattr(main, "Builder", lambda *a, **kw: fake)

        result = runner.invoke(main.app, ["build"])

        assert result.exit_code == 0
        assert "Nothing to build" in result.output
        assert "--force" in result.output

    def test_failure_prints_step_summary_and_retry_hint(self, project_dir: Path, monkeypatch) -> None:
        failed_result = BuildResult(
            target="api",
            status=TargetStatus.FAILED,
            attempts=2,
            total_duration_secs=1.2,
            steps=[
                BuildStep(phase="build", status="success", duration_secs=0.5, summary="built ok"),
                BuildStep(phase="validate", status="failed", duration_secs=0.7, summary="validation failed: 0/1 passed"),
            ],
        )
        fake = FakeBuilder(None, None, None, None)
        fake.build_return = ([failed_result], RuntimeError("Build failed for target 'api': validation failed"))
        monkeypatch.setattr(main, "Builder", lambda *a, **kw: fake)

        result = runner.invoke(main.app, ["build"])

        assert result.exit_code == 1
        assert "validate" in result.output
        assert "validation failed: 0/1 passed" in result.output
        assert "Fix the intent or the output, then run: intentc build api" in result.output


# ---------------------------------------------------------------------------
# validate UX
# ---------------------------------------------------------------------------


class TestValidateUX:
    def test_warning_only_exits_0_and_labelled_distinctly(self, project_dir: Path, monkeypatch) -> None:
        fake = FakeBuilder(None, None, None, None)
        fake.validate_return = ValidationSuiteResult(
            target="api",
            results=[
                ValidationResponse(
                    name="rubric-check", status="fail", severity="warning", reason="a bit thin", type="agent_validation"
                )
            ],
            passed=True,
            summary="0/1 passed, 0 error(s), 1 warning(s)",
            passed_count=0,
            error_count=0,
            warning_count=1,
        )
        monkeypatch.setattr(main, "Builder", lambda *a, **kw: fake)

        result = runner.invoke(main.app, ["validate", "api"])

        assert result.exit_code == 0
        assert "warning" in result.output
        assert "0/1 passed, 0 error(s), 1 warning(s)" in result.output

    def test_error_exits_1(self, project_dir: Path, monkeypatch) -> None:
        fake = FakeBuilder(None, None, None, None)
        fake.validate_return = ValidationSuiteResult(
            target="api",
            results=[ValidationResponse(name="cmd-check", status="fail", severity="error", reason="exit 1")],
            passed=False,
            summary="0/1 passed, 1 error(s), 0 warning(s)",
            passed_count=0,
            error_count=1,
            warning_count=0,
        )
        monkeypatch.setattr(main, "Builder", lambda *a, **kw: fake)

        result = runner.invoke(main.app, ["validate", "api"])

        assert result.exit_code == 1
        assert "error" in result.output


# ---------------------------------------------------------------------------
# clean
# ---------------------------------------------------------------------------


class TestClean:
    def test_clean_requires_target_or_all(self, project_dir: Path, monkeypatch) -> None:
        monkeypatch.setattr(main, "Builder", _RejectBuilder)
        result = runner.invoke(main.app, ["clean"])
        assert result.exit_code == 2

    def test_clean_unknown_target_exits_2(self, project_dir: Path, monkeypatch) -> None:
        monkeypatch.setattr(main, "Builder", _RejectBuilder)
        result = runner.invoke(main.app, ["clean", "does/not/exist"])
        assert result.exit_code == 2
        assert "Unknown feature" in result.output

    def test_clean_all(self, project_dir: Path, monkeypatch) -> None:
        fake = FakeBuilder(None, None, None, None)
        monkeypatch.setattr(main, "Builder", lambda *a, **kw: fake)
        result = runner.invoke(main.app, ["clean", "--all"])
        assert result.exit_code == 0
        assert fake.cleaned_all is True


# ---------------------------------------------------------------------------
# status / diff / log (real state manager, no fake builder needed)
# ---------------------------------------------------------------------------


class TestStatus:
    def test_pending_targets_shown_before_any_build(self, project_dir: Path) -> None:
        result = runner.invoke(main.app, ["status"])
        assert result.exit_code == 0
        assert "models" in result.output
        assert "api" in result.output
        # Nothing is built yet, but 'models' has no deps, so it's buildable next.
        assert "Next you can build: models" in result.output


class TestDiffAndLog:
    def test_diff_without_build_result_exits_2(self, project_dir: Path) -> None:
        result = runner.invoke(main.app, ["diff", "api"])
        assert result.exit_code == 2
        assert "No build recorded for 'api'" in result.output

    def test_log_without_build_result_exits_2(self, project_dir: Path) -> None:
        result = runner.invoke(main.app, ["log", "api"])
        assert result.exit_code == 2
        assert "No build recorded for 'api'" in result.output


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


class TestInitNoInteractive:
    def test_creates_project_and_config(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(main.app, ["init", "myproj", "--no-interactive"])

        assert result.exit_code == 0
        assert (tmp_path / "intent" / "project.ic").is_file()
        assert (tmp_path / ".intentc" / "config.yaml").is_file()
        assert "Created:" in result.output

    def test_refuses_to_overwrite(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        runner.invoke(main.app, ["init", "myproj", "--no-interactive"])

        result = runner.invoke(main.app, ["init", "myproj", "--no-interactive"])

        assert result.exit_code == 2

    def test_no_interactive_and_prompt_are_mutually_exclusive(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            main.app, ["init", "myproj", "--no-interactive", "-P", "a calculator"]
        )
        assert result.exit_code == 2


class _FakeInitAgent:
    """Stands in for an Agent in interactive/one-shot init CLI tests."""

    instances: list["_FakeInitAgent"] = []

    def __init__(self, profile, log=None) -> None:
        self.profile = profile
        self.log = log
        self.init_calls: list[tuple[str, str, Optional[str]]] = []
        self.write_extra_feature = True
        _FakeInitAgent.instances.append(self)

    def init(self, project_name: str, intent_dir: str, prompt: Optional[str] = None) -> None:
        self.init_calls.append((project_name, intent_dir, prompt))
        if self.write_extra_feature:
            extra = IntentFile(name="extra", depends_on=[], body="An extra agent-authored feature.")
            write_intent_file(extra, Path(intent_dir) / "extra" / "extra.ic")
            extra_vf = ValidationFile(
                target="extra",
                version=1,
                validations=[
                    Validation(
                        name="extra-check",
                        type=ValidationType.FILE_EXISTS.value,
                        severity=Severity.ERROR,
                        args={"paths": ["README.md"]},
                    )
                ],
            )
            write_validation_file(extra_vf, Path(intent_dir) / "extra" / "validation.icv")


class TestInitInteractive:
    def test_launches_agent_and_validates_result(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        _FakeInitAgent.instances = []
        monkeypatch.setattr(main, "create_from_profile", lambda profile, log=None: _FakeInitAgent(profile, log))

        result = runner.invoke(main.app, ["init", "myproj"])

        assert result.exit_code == 0, result.output
        assert len(_FakeInitAgent.instances) == 1
        agent = _FakeInitAgent.instances[0]
        assert agent.init_calls == [("myproj", str(tmp_path / "intent"), None)]
        assert agent.profile.sandbox_write_paths == [str(tmp_path / "intent")]
        assert (tmp_path / "intent" / "extra" / "extra.ic").is_file()
        assert (tmp_path / ".intentc" / "config.yaml").is_file()
        assert "Next steps" in result.output

    def test_agent_error_exits_1(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)

        class _RaisingAgent:
            def __init__(self, profile, log=None) -> None:
                pass

            def init(self, project_name, intent_dir, prompt=None) -> None:
                raise AgentError("boom")

        monkeypatch.setattr(main, "create_from_profile", lambda profile, log=None: _RaisingAgent(profile, log))

        result = runner.invoke(main.app, ["init", "myproj"])

        assert result.exit_code == 1
        assert "boom" in result.output

    def test_invalid_agent_output_exits_1(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)

        class _BrokenAgent:
            def __init__(self, profile, log=None) -> None:
                pass

            def init(self, project_name, intent_dir, prompt=None) -> None:
                (Path(intent_dir) / "project.ic").write_text("not valid frontmatter", encoding="utf-8")

        monkeypatch.setattr(main, "create_from_profile", lambda profile, log=None: _BrokenAgent(profile, log))

        result = runner.invoke(main.app, ["init", "myproj"])

        assert result.exit_code == 1


class TestInitOneShot:
    def test_prompt_flag_runs_single_shot_and_skips_next_steps(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        _FakeInitAgent.instances = []
        monkeypatch.setattr(main, "create_from_profile", lambda profile, log=None: _FakeInitAgent(profile, log))

        result = runner.invoke(main.app, ["init", "myproj", "-P", "a calculator app"])

        assert result.exit_code == 0, result.output
        agent = _FakeInitAgent.instances[0]
        assert agent.init_calls == [("myproj", str(tmp_path / "intent"), "a calculator app")]
        assert "Next steps" not in result.output


# ---------------------------------------------------------------------------
# compare (differencing module not yet built -- must fail gracefully)
# ---------------------------------------------------------------------------


class TestCompare:
    def test_missing_directory_exits_2(self, project_dir: Path) -> None:
        result = runner.invoke(main.app, ["compare", "does-not-exist-a", "does-not-exist-b"])
        assert result.exit_code == 2
        assert "Directory not found" in result.output