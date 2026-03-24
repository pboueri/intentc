"""Tests for the intentc CLI module."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from intentc.build.agents.models import AgentProfile
from intentc.build.storage.backend import BuildResult, TargetStatus
from intentc.cli.config import Config, load_config, save_config
from intentc.cli.main import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        config = load_config(tmp_path)
        assert config.default_output_dir == "src"
        assert config.default_profile.name == "default"
        assert config.default_profile.provider == "claude"
        assert config.default_profile.timeout == 3600
        assert config.default_profile.retries == 3

    def test_loads_valid_config(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".intentc"
        config_dir.mkdir()
        config_file = config_dir / "config.yaml"
        config_file.write_text(
            yaml.dump(
                {
                    "default_profile": {
                        "name": "custom",
                        "provider": "cli",
                        "timeout": 1800,
                        "retries": 5,
                    },
                    "default_output_dir": "out",
                }
            )
        )
        config = load_config(tmp_path)
        assert config.default_profile.name == "custom"
        assert config.default_profile.provider == "cli"
        assert config.default_profile.timeout == 1800
        assert config.default_profile.retries == 5
        assert config.default_output_dir == "out"

    def test_ignores_extra_fields(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".intentc"
        config_dir.mkdir()
        config_file = config_dir / "config.yaml"
        config_file.write_text(
            yaml.dump(
                {
                    "default_profile": {"name": "test", "provider": "claude"},
                    "default_output_dir": "src",
                    "unknown_field": "should be ignored",
                }
            )
        )
        config = load_config(tmp_path)
        assert config.default_profile.name == "test"

    def test_malformed_yaml_returns_defaults(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".intentc"
        config_dir.mkdir()
        config_file = config_dir / "config.yaml"
        config_file.write_text(":::: not valid yaml {{{{")
        config = load_config(tmp_path)
        assert config.default_output_dir == "src"


class TestSaveConfig:
    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        config = Config(
            default_profile=AgentProfile(
                name="roundtrip", provider="cli", timeout=999, retries=2
            ),
            default_output_dir="build",
        )
        path = save_config(config, tmp_path)
        assert path.exists()

        loaded = load_config(tmp_path)
        assert loaded.default_profile.name == "roundtrip"
        assert loaded.default_profile.provider == "cli"
        assert loaded.default_profile.timeout == 999
        assert loaded.default_profile.retries == 2
        assert loaded.default_output_dir == "build"

    def test_creates_directory(self, tmp_path: Path) -> None:
        config = Config()
        path = save_config(config, tmp_path)
        assert (tmp_path / ".intentc").is_dir()
        assert path.name == "config.yaml"


# ---------------------------------------------------------------------------
# Command tests
# ---------------------------------------------------------------------------


class TestInitCommand:
    def test_init_creates_project(self, tmp_path: Path) -> None:
        os.chdir(tmp_path)
        result = runner.invoke(app, ["init", "myproject"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        assert "initialized" in result.output.lower() or "created" in result.output.lower()

    def test_init_uses_cwd_name(self, tmp_path: Path) -> None:
        os.chdir(tmp_path)
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0, result.output
        assert (tmp_path / "intent" / "project.ic").exists()

    def test_init_aborts_if_exists(self, tmp_path: Path) -> None:
        os.chdir(tmp_path)
        intent_dir = tmp_path / "intent"
        intent_dir.mkdir()
        (intent_dir / "project.ic").write_text("existing")
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 2

    def test_init_creates_config(self, tmp_path: Path) -> None:
        os.chdir(tmp_path)
        result = runner.invoke(app, ["init", "testproj"])
        assert result.exit_code == 0, result.output
        assert (tmp_path / ".intentc" / "config.yaml").exists()


class TestBuildCommand:
    def test_build_requires_project(self, tmp_path: Path) -> None:
        os.chdir(tmp_path)
        result = runner.invoke(app, ["build"])
        assert result.exit_code == 2

    def test_build_with_dry_run(self, tmp_path: Path) -> None:
        os.chdir(tmp_path)
        # Set up a minimal project
        runner.invoke(app, ["init", "testproj"])
        result = runner.invoke(app, ["build", "--dry-run"])
        # Should succeed (no agent needed for dry run)
        assert result.exit_code == 0, result.output


class TestValidateCommand:
    def test_validate_requires_project(self, tmp_path: Path) -> None:
        os.chdir(tmp_path)
        result = runner.invoke(app, ["validate"])
        assert result.exit_code == 2


class TestCleanCommand:
    def test_clean_requires_target_or_all(self, tmp_path: Path) -> None:
        os.chdir(tmp_path)
        result = runner.invoke(app, ["clean"])
        assert result.exit_code == 2

    def test_clean_all(self, tmp_path: Path) -> None:
        os.chdir(tmp_path)
        runner.invoke(app, ["init", "testproj"])
        result = runner.invoke(app, ["clean", "--all"])
        assert result.exit_code == 0, result.output
        assert "reset" in result.output.lower()


class TestPlanCommand:
    def test_plan_invalid_feature(self, tmp_path: Path) -> None:
        os.chdir(tmp_path)
        runner.invoke(app, ["init", "testproj"])
        result = runner.invoke(app, ["plan", "nonexistent"])
        assert result.exit_code == 2
        assert "not found" in result.output.lower()


class TestStatusCommand:
    def test_status_no_targets(self, tmp_path: Path) -> None:
        os.chdir(tmp_path)
        result = runner.invoke(app, ["status"])
        # Should succeed even without tracked targets
        assert result.exit_code == 0, result.output


class TestDiffCommand:
    def test_diff_no_build_result(self, tmp_path: Path) -> None:
        os.chdir(tmp_path)
        runner.invoke(app, ["init", "testproj"])
        result = runner.invoke(app, ["diff", "starter"])
        assert result.exit_code == 2
        assert "no build result" in result.output.lower()


class TestCompareCommand:
    def test_compare_no_differencing_module(self, tmp_path: Path) -> None:
        os.chdir(tmp_path)
        runner.invoke(app, ["init", "testproj"])
        result = runner.invoke(app, ["compare", "dir_a", "dir_b"])
        # Should fail gracefully since differencing module doesn't exist yet
        assert result.exit_code == 2


class TestNoArgsShowsHelp:
    def test_no_args(self) -> None:
        result = runner.invoke(app, [])
        # Typer's no_args_is_help causes exit code 0 for help display,
        # but some versions may use 2. Either way, help text should be shown.
        assert "usage" in result.output.lower() or "intentc" in result.output.lower()


class TestAllCommandsDefined:
    """Verify all eight commands are registered on the app."""

    def test_commands_registered(self) -> None:
        import typer.main

        click_app = typer.main.get_command(app)
        command_names = sorted(click_app.commands.keys()) if hasattr(click_app, 'commands') else []
        expected = sorted(["init", "build", "validate", "clean", "plan", "status", "diff", "compare"])
        assert command_names == expected, f"Got {command_names}, expected {expected}"
