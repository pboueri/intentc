from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from intentc.build.agents.types import AgentProfile
from intentc.build.state import BuildResult, BuildStep, TargetStatus
from intentc.build.validations import ValidationSuiteResult
from intentc.cli.config import Config, load_config, save_config
from intentc.cli.main import app
from intentc.core.project import Project, blank_project, write_project

runner = CliRunner()


# --- Config tests ---


class TestLoadConfig:
    def test_returns_defaults_when_file_missing(self, tmp_path: Path) -> None:
        config = load_config(tmp_path)
        assert config.default_output_dir == "src"
        assert config.default_profile.name == "default"
        assert config.default_profile.provider == "claude"
        assert config.default_profile.timeout == 3600.0
        assert config.default_profile.retries == 3

    def test_reads_config_file(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".intentc"
        config_dir.mkdir()
        config_path = config_dir / "config.yaml"
        config_path.write_text(yaml.dump({
            "default_profile": {
                "name": "myprofile",
                "provider": "cli",
                "timeout": 1800,
                "retries": 5,
            },
            "default_output_dir": "output",
        }))

        config = load_config(tmp_path)
        assert config.default_output_dir == "output"
        assert config.default_profile.name == "myprofile"
        assert config.default_profile.provider == "cli"
        assert config.default_profile.timeout == 1800.0
        assert config.default_profile.retries == 5

    def test_ignores_extra_fields(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".intentc"
        config_dir.mkdir()
        config_path = config_dir / "config.yaml"
        config_path.write_text(yaml.dump({
            "default_profile": {
                "name": "default",
                "provider": "claude",
            },
            "default_output_dir": "src",
            "unknown_field": "ignored",
        }))

        config = load_config(tmp_path)
        assert config.default_output_dir == "src"


class TestSaveConfig:
    def test_creates_config_file(self, tmp_path: Path) -> None:
        config = Config()
        path = save_config(config, tmp_path)
        assert path.exists()
        data = yaml.safe_load(path.read_text())
        assert data["default_output_dir"] == "src"
        assert data["default_profile"]["name"] == "default"

    def test_roundtrip(self, tmp_path: Path) -> None:
        config = Config(
            default_profile=AgentProfile(name="test", provider="cli", timeout=999, retries=2),
            default_output_dir="build",
        )
        save_config(config, tmp_path)
        loaded = load_config(tmp_path)
        assert loaded.default_output_dir == "build"
        assert loaded.default_profile.name == "test"
        assert loaded.default_profile.provider == "cli"
        assert loaded.default_profile.timeout == 999.0
        assert loaded.default_profile.retries == 2


# --- Helper to set up a project in tmp dir ---


def _setup_project(tmp_path: Path, name: str = "testproj") -> Path:
    """Create a blank project in tmp_path and return the project root."""
    project = blank_project(name)
    intent_dir = tmp_path / "intent"
    write_project(project, intent_dir)
    save_config(Config(), tmp_path)
    return tmp_path


# --- Command tests ---


class TestInitCommand:
    def test_init_creates_project(self, tmp_path: Path) -> None:
        os.chdir(tmp_path)
        result = runner.invoke(app, ["init", "myproject"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        assert (tmp_path / "intent" / "project.ic").exists()

    def test_init_with_existing_project_exits_2(self, tmp_path: Path) -> None:
        # First create the project
        os.chdir(tmp_path)
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0, result.output

        # Second call should fail
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 2

    def test_init_uses_cwd_name_as_default(self, tmp_path: Path) -> None:
        os.chdir(tmp_path)
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0, result.output
        assert (tmp_path / "intent" / "project.ic").exists()
        assert (tmp_path / ".intentc" / "config.yaml").exists()


class TestBuildCommand:
    def test_build_loads_project(self, tmp_path: Path) -> None:
        project_root = _setup_project(tmp_path)

        with patch("intentc.cli.main.load_project") as mock_load, \
             patch("intentc.build.builder.builder.Builder.build") as mock_build:
            mock_load.return_value = blank_project("test")
            mock_build.return_value = ([], None)

            os.chdir(project_root)
            result = runner.invoke(app, ["build"])
            assert result.exit_code == 0, result.output

    def test_build_with_flags(self, tmp_path: Path) -> None:
        project_root = _setup_project(tmp_path)

        with patch("intentc.cli.main.load_project") as mock_load, \
             patch("intentc.build.builder.builder.Builder.build") as mock_build:
            mock_load.return_value = blank_project("test")
            mock_build.return_value = ([], None)

            os.chdir(project_root)
            result = runner.invoke(app, [
                "build", "starter",
                "--force",
                "--dry-run",
                "--output-dir", "out",
                "--profile", "myprofile",
                "--implementation", "default",
            ])
            assert result.exit_code == 0, result.output
            mock_build.assert_called_once()
            opts = mock_build.call_args[0][0]
            assert opts.target == "starter"
            assert opts.force is True
            assert opts.dry_run is True
            assert opts.output_dir == "out"
            assert opts.profile_override == "myprofile"
            assert opts.implementation == "default"

    def test_build_exits_1_on_failure(self, tmp_path: Path) -> None:
        project_root = _setup_project(tmp_path)

        with patch("intentc.cli.main.load_project") as mock_load, \
             patch("intentc.build.builder.builder.Builder.build") as mock_build:
            mock_load.return_value = blank_project("test")
            failed_result = BuildResult(
                target="starter",
                generation_id="gen1",
                status=TargetStatus.FAILED,
            )
            mock_build.return_value = ([failed_result], RuntimeError("fail"))

            os.chdir(project_root)
            result = runner.invoke(app, ["build"])
            assert result.exit_code == 1


class TestValidateCommand:
    def test_validate_all(self, tmp_path: Path) -> None:
        project_root = _setup_project(tmp_path)

        with patch("intentc.cli.main.load_project") as mock_load, \
             patch("intentc.build.builder.builder.Builder.validate") as mock_validate:
            mock_load.return_value = blank_project("test")
            mock_validate.return_value = [
                ValidationSuiteResult(target="starter", passed=True, summary="1/1 passed")
            ]

            os.chdir(project_root)
            result = runner.invoke(app, ["validate"])
            assert result.exit_code == 0, result.output

    def test_validate_exits_1_on_failure(self, tmp_path: Path) -> None:
        project_root = _setup_project(tmp_path)

        with patch("intentc.cli.main.load_project") as mock_load, \
             patch("intentc.build.builder.builder.Builder.validate") as mock_validate:
            mock_load.return_value = blank_project("test")
            mock_validate.return_value = ValidationSuiteResult(
                target="starter", passed=False, summary="0/1 passed"
            )

            os.chdir(project_root)
            result = runner.invoke(app, ["validate", "starter"])
            assert result.exit_code == 1


class TestCleanCommand:
    def test_clean_requires_target_or_all(self, tmp_path: Path) -> None:
        project_root = _setup_project(tmp_path)
        os.chdir(project_root)
        result = runner.invoke(app, ["clean"])
        assert result.exit_code == 2

    def test_clean_all(self, tmp_path: Path) -> None:
        project_root = _setup_project(tmp_path)

        with patch("intentc.cli.main.load_project") as mock_load, \
             patch("intentc.build.builder.builder.Builder.clean_all") as mock_clean:
            mock_load.return_value = blank_project("test")

            os.chdir(project_root)
            result = runner.invoke(app, ["clean", "--all"])
            assert result.exit_code == 0, result.output
            mock_clean.assert_called_once()

    def test_clean_target(self, tmp_path: Path) -> None:
        project_root = _setup_project(tmp_path)

        with patch("intentc.cli.main.load_project") as mock_load, \
             patch("intentc.build.builder.builder.Builder.clean") as mock_clean:
            mock_load.return_value = blank_project("test")

            os.chdir(project_root)
            result = runner.invoke(app, ["clean", "starter"])
            assert result.exit_code == 0, result.output
            mock_clean.assert_called_once()


class TestPlanCommand:
    def test_plan_unknown_feature_exits_2(self, tmp_path: Path) -> None:
        project_root = _setup_project(tmp_path)
        os.chdir(project_root)
        result = runner.invoke(app, ["plan", "nonexistent"])
        assert result.exit_code == 2

    def test_plan_valid_feature(self, tmp_path: Path) -> None:
        project_root = _setup_project(tmp_path)

        with patch("intentc.build.agents.base.create_from_profile") as mock_create:
            mock_agent = MagicMock()
            mock_create.return_value = mock_agent

            os.chdir(project_root)
            result = runner.invoke(app, ["plan", "starter"])
            assert result.exit_code == 0, result.output
            mock_agent.plan.assert_called_once()


class TestStatusCommand:
    def test_status_runs(self, tmp_path: Path) -> None:
        project_root = _setup_project(tmp_path)

        with patch("intentc.build.state.StateManager.list_targets") as mock_list:
            mock_list.return_value = []

            os.chdir(project_root)
            result = runner.invoke(app, ["status"])
            assert result.exit_code == 0, result.output


class TestDiffCommand:
    def test_diff_no_result_exits_2(self, tmp_path: Path) -> None:
        project_root = _setup_project(tmp_path)

        with patch("intentc.build.state.StateManager.get_build_result") as mock_get:
            mock_get.return_value = None

            os.chdir(project_root)
            result = runner.invoke(app, ["diff", "starter"])
            assert result.exit_code == 2


class TestCompareCommand:
    def test_compare_delegates_to_run_differencing(self, tmp_path: Path) -> None:
        import sys
        import types

        project_root = _setup_project(tmp_path)

        # Create a fake differencing module since it doesn't exist yet
        fake_mod = types.ModuleType("intentc.differencing")
        mock_run = MagicMock()
        mock_result = MagicMock()
        mock_result.status = "equivalent"
        mock_result.summary = "All good"
        mock_result.dimensions = []
        mock_run.return_value = mock_result
        fake_mod.run_differencing = mock_run
        sys.modules["intentc.differencing"] = fake_mod

        try:
            os.chdir(project_root)
            result = runner.invoke(app, ["compare", "/tmp/a", "/tmp/b"])
            assert result.exit_code == 0, result.output
            mock_run.assert_called_once()
        finally:
            del sys.modules["intentc.differencing"]

    def test_compare_exits_1_if_divergent(self, tmp_path: Path) -> None:
        import sys
        import types

        project_root = _setup_project(tmp_path)

        fake_mod = types.ModuleType("intentc.differencing")
        mock_run = MagicMock()
        mock_result = MagicMock()
        mock_result.status = "divergent"
        mock_result.summary = "Differences found"
        mock_result.dimensions = []
        mock_run.return_value = mock_result
        fake_mod.run_differencing = mock_run
        sys.modules["intentc.differencing"] = fake_mod

        try:
            os.chdir(project_root)
            result = runner.invoke(app, ["compare", "/tmp/a", "/tmp/b"])
            assert result.exit_code == 1
        finally:
            del sys.modules["intentc.differencing"]


class TestLoadProjectOrExit:
    def test_exits_2_on_parse_errors(self, tmp_path: Path) -> None:
        os.chdir(tmp_path)
        # No intent dir exists, should trigger parse errors
        result = runner.invoke(app, ["build"])
        assert result.exit_code == 2
