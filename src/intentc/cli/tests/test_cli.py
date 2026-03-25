"""Tests for the intentc CLI module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from intentc.build.agents import AgentProfile
from intentc.cli.config import Config, load_config, save_config
from intentc.cli.main import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestConfig:
    def test_load_config_defaults_when_missing(self, tmp_path: Path) -> None:
        config = load_config(tmp_path)
        assert config.default_output_dir == "src"
        assert config.default_profile.name == "default"
        assert config.default_profile.provider == "claude"
        assert config.default_profile.timeout == 3600
        assert config.default_profile.retries == 3

    def test_save_and_load_config(self, tmp_path: Path) -> None:
        config = Config(
            default_profile=AgentProfile(
                name="test-profile",
                provider="cli",
                timeout=1800,
                retries=5,
            ),
            default_output_dir="output",
        )
        path = save_config(config, tmp_path)
        assert path.exists()
        assert path == tmp_path / ".intentc" / "config.yaml"

        loaded = load_config(tmp_path)
        assert loaded.default_profile.name == "test-profile"
        assert loaded.default_profile.provider == "cli"
        assert loaded.default_profile.timeout == 1800
        assert loaded.default_profile.retries == 5
        assert loaded.default_output_dir == "output"

    def test_load_config_ignores_extra_fields(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".intentc"
        config_dir.mkdir(parents=True)
        config_file = config_dir / "config.yaml"
        config_file.write_text(
            "default_profile:\n  name: test\n  provider: claude\n"
            "default_output_dir: src\n"
            "unknown_field: ignored\n"
        )
        config = load_config(tmp_path)
        assert config.default_profile.name == "test"

    def test_load_config_handles_invalid_yaml(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".intentc"
        config_dir.mkdir(parents=True)
        config_file = config_dir / "config.yaml"
        config_file.write_text(":::invalid yaml:::")
        config = load_config(tmp_path)
        assert config.default_output_dir == "src"


# ---------------------------------------------------------------------------
# Init command tests
# ---------------------------------------------------------------------------


class TestInitCommand:
    def test_init_creates_project(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["init", "test-project"])
        assert result.exit_code == 0
        assert (tmp_path / "intent" / "project.ic").exists()
        assert (tmp_path / ".intentc" / "config.yaml").exists()

    def test_init_default_name_is_dir_name(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        content = (tmp_path / "intent" / "project.ic").read_text()
        assert tmp_path.name in content

    def test_init_aborts_if_project_exists(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "intent").mkdir()
        (tmp_path / "intent" / "project.ic").write_text("exists")
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 2

    def test_init_shows_summary(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["init", "myproject"])
        assert result.exit_code == 0
        assert "initialized" in result.output.lower() or "Created" in result.output


# ---------------------------------------------------------------------------
# Build command tests
# ---------------------------------------------------------------------------


class TestBuildCommand:
    def test_build_loads_project_and_calls_builder(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, ["init", "test-project"])

        mock_builder = MagicMock()
        mock_builder.build.return_value = ([], None)

        with patch("intentc.build.builder.Builder", return_value=mock_builder) as mock_cls, \
             patch("intentc.build.state.GitVersionControl"), \
             patch("intentc.build.state.state.SQLiteBackend"):
            result = runner.invoke(app, ["build"])

        assert result.exit_code == 0

    def test_build_exits_1_on_failure(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, ["init", "test-project"])

        mock_builder = MagicMock()
        mock_builder.build.return_value = ([], RuntimeError("build failed"))

        with patch("intentc.build.builder.Builder", return_value=mock_builder), \
             patch("intentc.build.state.GitVersionControl"), \
             patch("intentc.build.state.state.SQLiteBackend"):
            result = runner.invoke(app, ["build"])

        assert result.exit_code == 1

    def test_build_exits_2_on_missing_project(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["build"])
        assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Validate command tests
# ---------------------------------------------------------------------------


class TestValidateCommand:
    def test_validate_exits_2_on_missing_project(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["validate"])
        assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Clean command tests
# ---------------------------------------------------------------------------


class TestCleanCommand:
    def test_clean_requires_target_or_all(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, ["init", "test-project"])
        result = runner.invoke(app, ["clean"])
        assert result.exit_code == 2

    def test_clean_all(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, ["init", "test-project"])

        mock_builder = MagicMock()

        with patch("intentc.build.builder.Builder", return_value=mock_builder), \
             patch("intentc.build.state.GitVersionControl"), \
             patch("intentc.build.state.state.SQLiteBackend"):
            result = runner.invoke(app, ["clean", "--all"])

        assert result.exit_code == 0
        mock_builder.clean_all.assert_called_once()


# ---------------------------------------------------------------------------
# Plan command tests
# ---------------------------------------------------------------------------


class TestPlanCommand:
    def test_plan_exits_2_on_unknown_feature(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, ["init", "test-project"])
        result = runner.invoke(app, ["plan", "nonexistent/feature"])
        assert result.exit_code == 2
        assert "not found" in result.output.lower() or "Error" in result.output


# ---------------------------------------------------------------------------
# Status command tests
# ---------------------------------------------------------------------------


class TestStatusCommand:
    def test_status_with_no_targets(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)

        mock_state = MagicMock()
        mock_state.list_targets.return_value = []
        mock_state.get_build_result.return_value = None

        with patch("intentc.build.state.StateManager", return_value=mock_state):
            result = runner.invoke(app, ["status"])

        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Diff command tests
# ---------------------------------------------------------------------------


class TestDiffCommand:
    def test_diff_exits_2_when_no_build_result(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)

        mock_state = MagicMock()
        mock_state.get_build_result.return_value = None

        with patch("intentc.build.state.StateManager", return_value=mock_state):
            result = runner.invoke(app, ["diff", "some/target"])

        assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Compare command tests
# ---------------------------------------------------------------------------


class TestCompareCommand:
    def test_compare_exits_2_on_missing_dir(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, ["init", "test-project"])
        result = runner.invoke(app, ["compare", "/nonexistent/a", "/nonexistent/b"])
        assert result.exit_code == 2

    def test_compare_exits_2_on_missing_project(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        result = runner.invoke(app, ["compare", str(dir_a), str(dir_b)])
        assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Help / no-args tests
# ---------------------------------------------------------------------------


class TestAppHelp:
    def test_no_args_shows_help(self) -> None:
        result = runner.invoke(app, [])
        # Typer shows help and exits with code 0 or 2 depending on version
        assert "Usage" in result.output or "intentc" in result.output.lower()

    def test_help_flag(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "build" in result.output
        assert "init" in result.output
        assert "validate" in result.output
        assert "clean" in result.output
        assert "plan" in result.output
        assert "status" in result.output
        assert "diff" in result.output
        assert "compare" in result.output
