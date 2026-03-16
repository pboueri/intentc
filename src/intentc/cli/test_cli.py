"""Tests for the CLI — command wiring, config, and output formatting."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from intentc.build.agents import AgentProfile
from intentc.build.state import BuildResult, BuildStep, StateManager, TargetStatus
from intentc.cli.config import Config, load_config, save_config
from intentc.cli.main import app
from intentc.core.project import FeatureNode, Project
from intentc.core.types import IntentFile, ProjectIntent

runner = CliRunner()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class TestConfig:
    def test_defaults(self):
        config = Config()
        assert config.default_profile.provider == "claude"
        assert config.default_profile.timeout == 3600
        assert config.default_profile.retries == 3
        assert config.default_output_dir == "src"

    def test_load_missing_file(self, tmp_path: Path):
        config = load_config(tmp_path)
        assert config.default_profile.provider == "claude"
        assert config.default_output_dir == "src"

    def test_save_and_load_roundtrip(self, tmp_path: Path):
        config = Config(
            default_profile=AgentProfile(
                name="custom", provider="cli", timeout=60, retries=1
            ),
            default_output_dir="build",
        )
        save_config(config, tmp_path)
        loaded = load_config(tmp_path)
        assert loaded.default_profile.name == "custom"
        assert loaded.default_profile.provider == "cli"
        assert loaded.default_profile.timeout == 60
        assert loaded.default_profile.retries == 1
        assert loaded.default_output_dir == "build"

    def test_load_invalid_yaml(self, tmp_path: Path):
        config_path = tmp_path / ".intentc" / "config.yaml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(": invalid: yaml: [")
        config = load_config(tmp_path)
        assert config.default_profile.provider == "claude"

    def test_load_non_dict_yaml(self, tmp_path: Path):
        config_path = tmp_path / ".intentc" / "config.yaml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text("just a string")
        config = load_config(tmp_path)
        assert config.default_output_dir == "src"

    def test_save_creates_directory(self, tmp_path: Path):
        config = Config()
        path = save_config(config, tmp_path)
        assert path.exists()
        assert (tmp_path / ".intentc").is_dir()

    def test_load_partial_config(self, tmp_path: Path):
        config_path = tmp_path / ".intentc" / "config.yaml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(yaml.dump({"default_output_dir": "out"}))
        config = load_config(tmp_path)
        assert config.default_output_dir == "out"
        assert config.default_profile.provider == "claude"


# ---------------------------------------------------------------------------
# Init command
# ---------------------------------------------------------------------------


class TestInitCommand:
    def test_init_creates_project(self, tmp_path: Path):
        os.chdir(tmp_path)
        result = runner.invoke(app, ["init", "myproject"])
        assert result.exit_code == 0
        assert (tmp_path / "intent" / "project.ic").exists()
        assert (tmp_path / ".intentc" / "config.yaml").exists()

    def test_init_uses_dir_name_as_default(self, tmp_path: Path):
        os.chdir(tmp_path)
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert (tmp_path / "intent" / "project.ic").exists()

    def test_init_aborts_if_project_exists(self, tmp_path: Path):
        os.chdir(tmp_path)
        (tmp_path / "intent").mkdir()
        (tmp_path / "intent" / "project.ic").write_text("existing")
        result = runner.invoke(app, ["init", "test"])
        assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Build command
# ---------------------------------------------------------------------------


def _setup_project_dir(tmp_path: Path) -> Path:
    """Create a minimal project on disk for CLI testing."""
    intent_dir = tmp_path / "intent"
    intent_dir.mkdir()
    (intent_dir / "project.ic").write_text(
        "---\nname: test\ntags: [project]\n---\n# Test"
    )
    feat_dir = intent_dir / "a"
    feat_dir.mkdir()
    (feat_dir / "a.ic").write_text("---\nname: a\n---\n# Feature A")
    return tmp_path


class TestBuildCommand:
    @patch("intentc.cli.main.Builder")
    def test_build_calls_builder(self, MockBuilder, tmp_path: Path):
        _setup_project_dir(tmp_path)
        os.chdir(tmp_path)

        mock_builder = MagicMock()
        mock_builder.build.return_value = ([], None)
        MockBuilder.return_value = mock_builder

        result = runner.invoke(app, ["build"])
        assert result.exit_code == 0
        mock_builder.build.assert_called_once()

    @patch("intentc.cli.main.Builder")
    def test_build_specific_target(self, MockBuilder, tmp_path: Path):
        _setup_project_dir(tmp_path)
        os.chdir(tmp_path)

        mock_builder = MagicMock()
        mock_builder.build.return_value = ([], None)
        MockBuilder.return_value = mock_builder

        result = runner.invoke(app, ["build", "a"])
        assert result.exit_code == 0
        call_args = mock_builder.build.call_args[0][0]
        assert call_args.target == "a"

    @patch("intentc.cli.main.Builder")
    def test_build_force_flag(self, MockBuilder, tmp_path: Path):
        _setup_project_dir(tmp_path)
        os.chdir(tmp_path)

        mock_builder = MagicMock()
        mock_builder.build.return_value = ([], None)
        MockBuilder.return_value = mock_builder

        result = runner.invoke(app, ["build", "--force"])
        assert result.exit_code == 0
        call_args = mock_builder.build.call_args[0][0]
        assert call_args.force is True

    @patch("intentc.cli.main.Builder")
    def test_build_dry_run_flag(self, MockBuilder, tmp_path: Path):
        _setup_project_dir(tmp_path)
        os.chdir(tmp_path)

        mock_builder = MagicMock()
        mock_builder.build.return_value = ([], None)
        MockBuilder.return_value = mock_builder

        result = runner.invoke(app, ["build", "--dry-run"])
        assert result.exit_code == 0
        call_args = mock_builder.build.call_args[0][0]
        assert call_args.dry_run is True

    @patch("intentc.cli.main.Builder")
    def test_build_failure_exits_1(self, MockBuilder, tmp_path: Path):
        _setup_project_dir(tmp_path)
        os.chdir(tmp_path)

        mock_builder = MagicMock()
        mock_builder.build.return_value = ([], RuntimeError("build failed"))
        MockBuilder.return_value = mock_builder

        result = runner.invoke(app, ["build"])
        assert result.exit_code == 1

    def test_build_no_project_exits_2(self, tmp_path: Path):
        os.chdir(tmp_path)
        result = runner.invoke(app, ["build"])
        assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Validate command
# ---------------------------------------------------------------------------


class TestValidateCommand:
    @patch("intentc.cli.main.Builder")
    def test_validate_target(self, MockBuilder, tmp_path: Path):
        _setup_project_dir(tmp_path)
        os.chdir(tmp_path)

        mock_builder = MagicMock()
        mock_result = MagicMock(spec=["target", "results", "passed", "summary"])
        mock_result.target = "a"
        mock_result.results = []
        mock_result.passed = True
        mock_result.summary = "0/0 passed"
        mock_builder.validate.return_value = mock_result
        # Make isinstance check work
        mock_builder.validate.return_value.__class__ = type(mock_result)
        MockBuilder.return_value = mock_builder

        result = runner.invoke(app, ["validate", "a"])
        assert result.exit_code == 0

    @patch("intentc.cli.main.Builder")
    def test_validate_failure_exits_1(self, MockBuilder, tmp_path: Path):
        _setup_project_dir(tmp_path)
        os.chdir(tmp_path)

        mock_builder = MagicMock()
        mock_result = MagicMock()
        mock_result.passed = False
        mock_result.target = "a"
        mock_result.results = []
        mock_result.summary = "0/1 passed"
        mock_builder.validate.return_value = mock_result
        MockBuilder.return_value = mock_builder

        result = runner.invoke(app, ["validate", "a"])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Clean command
# ---------------------------------------------------------------------------


class TestCleanCommand:
    @patch("intentc.cli.main.Builder")
    def test_clean_target(self, MockBuilder, tmp_path: Path):
        _setup_project_dir(tmp_path)
        os.chdir(tmp_path)

        mock_builder = MagicMock()
        MockBuilder.return_value = mock_builder

        result = runner.invoke(app, ["clean", "a"])
        assert result.exit_code == 0
        mock_builder.clean.assert_called_once()

    @patch("intentc.cli.main.Builder")
    def test_clean_all(self, MockBuilder, tmp_path: Path):
        _setup_project_dir(tmp_path)
        os.chdir(tmp_path)

        mock_builder = MagicMock()
        MockBuilder.return_value = mock_builder

        result = runner.invoke(app, ["clean", "--all"])
        assert result.exit_code == 0
        mock_builder.clean_all.assert_called_once()

    def test_clean_no_target_no_all_exits_2(self, tmp_path: Path):
        _setup_project_dir(tmp_path)
        os.chdir(tmp_path)
        result = runner.invoke(app, ["clean"])
        assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Status command
# ---------------------------------------------------------------------------


class TestStatusCommand:
    def test_status_empty(self, tmp_path: Path):
        os.chdir(tmp_path)
        # status doesn't require a project, just a state manager
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0

    def test_status_with_output_dir(self, tmp_path: Path):
        os.chdir(tmp_path)
        result = runner.invoke(app, ["status", "--output-dir", "custom"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Diff command
# ---------------------------------------------------------------------------


class TestDiffCommand:
    def test_diff_no_build_result_exits_2(self, tmp_path: Path):
        os.chdir(tmp_path)
        result = runner.invoke(app, ["diff", "a"])
        assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Plan command
# ---------------------------------------------------------------------------


class TestPlanCommand:
    def test_plan_missing_project_exits_2(self, tmp_path: Path):
        os.chdir(tmp_path)
        result = runner.invoke(app, ["plan", "a"])
        assert result.exit_code == 2

    def test_plan_missing_feature_exits_2(self, tmp_path: Path):
        _setup_project_dir(tmp_path)
        os.chdir(tmp_path)
        result = runner.invoke(app, ["plan", "nonexistent"])
        assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Profile resolution
# ---------------------------------------------------------------------------


class TestProfileResolution:
    def test_flag_overrides_config(self, tmp_path: Path):
        from intentc.cli.main import _resolve_profile

        config = Config(
            default_profile=AgentProfile(name="default", provider="claude"),
        )
        profile = _resolve_profile("custom", config)
        assert profile.name == "custom"

    def test_none_returns_config_default(self, tmp_path: Path):
        from intentc.cli.main import _resolve_profile

        config = Config(
            default_profile=AgentProfile(name="mydefault", provider="cli"),
        )
        profile = _resolve_profile(None, config)
        assert profile.name == "mydefault"
        assert profile.provider == "cli"
