"""Tests for the intentc CLI module."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from intentc.build.agents import AgentProfile
from intentc.cli.config import Config, load_config, save_config
from intentc.cli.main import app
from intentc.cli.output import (
    console,
    create_console,
    print_error,
    render_build_results,
    render_diff,
    render_init_summary,
    render_validation_result,
    render_validation_results,
)

runner = CliRunner()


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestConfig:
    def test_load_config_missing_file(self, tmp_path: Path) -> None:
        config = load_config(tmp_path)
        assert config.default_output_dir == "src"
        assert config.default_profile.name == "default"
        assert config.default_profile.provider == "claude"

    def test_save_and_load_config(self, tmp_path: Path) -> None:
        config = Config(
            default_profile=AgentProfile(
                name="test", provider="cli", timeout=60, retries=1
            ),
            default_output_dir="out",
        )
        path = save_config(config, tmp_path)
        assert path.exists()

        loaded = load_config(tmp_path)
        assert loaded.default_output_dir == "out"
        assert loaded.default_profile.name == "test"
        assert loaded.default_profile.provider == "cli"

    def test_config_ignores_extra_fields(self, tmp_path: Path) -> None:
        config_file = tmp_path / ".intentc" / "config.yaml"
        config_file.parent.mkdir(parents=True)
        config_file.write_text(
            yaml.dump({"default_output_dir": "build", "unknown_field": True})
        )
        config = load_config(tmp_path)
        assert config.default_output_dir == "build"


# ---------------------------------------------------------------------------
# CLI command tests
# ---------------------------------------------------------------------------


class TestInit:
    def test_init_creates_project(self, tmp_path: Path) -> None:
        os.chdir(tmp_path)
        result = runner.invoke(app, ["init", "myproject"])
        assert result.exit_code == 0
        assert (tmp_path / "intent" / "project.ic").exists()
        assert (tmp_path / ".intentc" / "config.yaml").exists()

    def test_init_default_name(self, tmp_path: Path) -> None:
        os.chdir(tmp_path)
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert (tmp_path / "intent" / "project.ic").exists()

    def test_init_refuses_overwrite(self, tmp_path: Path) -> None:
        os.chdir(tmp_path)
        (tmp_path / "intent").mkdir()
        (tmp_path / "intent" / "project.ic").write_text("existing")
        result = runner.invoke(app, ["init", "myproject"])
        assert result.exit_code == 2


class TestBuild:
    def _setup_project(self, tmp_path: Path) -> None:
        os.chdir(tmp_path)
        runner.invoke(app, ["init", "testproject"])

    @patch("intentc.cli.main.Builder")
    @patch("intentc.cli.main.GitVersionControl")
    def test_build_all(self, mock_vcs_cls: MagicMock, mock_builder_cls: MagicMock, tmp_path: Path) -> None:
        self._setup_project(tmp_path)
        mock_builder = mock_builder_cls.return_value
        mock_builder.build.return_value = ([], None)
        result = runner.invoke(app, ["build"])
        assert result.exit_code == 0
        mock_builder.build.assert_called_once()

    @patch("intentc.cli.main.Builder")
    @patch("intentc.cli.main.GitVersionControl")
    def test_build_with_target(self, mock_vcs_cls: MagicMock, mock_builder_cls: MagicMock, tmp_path: Path) -> None:
        self._setup_project(tmp_path)
        mock_builder = mock_builder_cls.return_value
        mock_builder.build.return_value = ([], None)
        result = runner.invoke(app, ["build", "some/feature"])
        assert result.exit_code == 0
        call_args = mock_builder.build.call_args[0][0]
        assert call_args.target == "some/feature"

    @patch("intentc.cli.main.Builder")
    @patch("intentc.cli.main.GitVersionControl")
    def test_build_failure_exit_code(self, mock_vcs_cls: MagicMock, mock_builder_cls: MagicMock, tmp_path: Path) -> None:
        self._setup_project(tmp_path)
        mock_builder = mock_builder_cls.return_value
        mock_builder.build.return_value = ([], RuntimeError("fail"))
        result = runner.invoke(app, ["build"])
        assert result.exit_code == 1

    @patch("intentc.cli.main.Builder")
    @patch("intentc.cli.main.GitVersionControl")
    def test_build_flags(self, mock_vcs_cls: MagicMock, mock_builder_cls: MagicMock, tmp_path: Path) -> None:
        self._setup_project(tmp_path)
        mock_builder = mock_builder_cls.return_value
        mock_builder.build.return_value = ([], None)
        result = runner.invoke(app, ["build", "--force", "--dry-run", "-o", "out", "-i", "pyimpl"])
        assert result.exit_code == 0
        opts = mock_builder.build.call_args[0][0]
        assert opts.force is True
        assert opts.dry_run is True
        assert opts.output_dir == "out"
        assert opts.implementation == "pyimpl"


class TestValidate:
    def _setup_project(self, tmp_path: Path) -> None:
        os.chdir(tmp_path)
        runner.invoke(app, ["init", "testproject"])

    @patch("intentc.cli.main.Builder")
    @patch("intentc.cli.main.GitVersionControl")
    def test_validate_project(self, mock_vcs_cls: MagicMock, mock_builder_cls: MagicMock, tmp_path: Path) -> None:
        from intentc.build.validations import ValidationSuiteResult

        self._setup_project(tmp_path)
        mock_builder = mock_builder_cls.return_value
        mock_builder.validate.return_value = [ValidationSuiteResult(target="all", passed=True)]
        result = runner.invoke(app, ["validate"])
        assert result.exit_code == 0

    @patch("intentc.cli.main.Builder")
    @patch("intentc.cli.main.GitVersionControl")
    def test_validate_failure_exit_code(self, mock_vcs_cls: MagicMock, mock_builder_cls: MagicMock, tmp_path: Path) -> None:
        from intentc.build.validations import ValidationSuiteResult

        self._setup_project(tmp_path)
        mock_builder = mock_builder_cls.return_value
        mock_builder.validate.return_value = [ValidationSuiteResult(target="all", passed=False)]
        result = runner.invoke(app, ["validate"])
        assert result.exit_code == 1


class TestClean:
    def _setup_project(self, tmp_path: Path) -> None:
        os.chdir(tmp_path)
        runner.invoke(app, ["init", "testproject"])

    @patch("intentc.cli.main.Builder")
    @patch("intentc.cli.main.GitVersionControl")
    def test_clean_target(self, mock_vcs_cls: MagicMock, mock_builder_cls: MagicMock, tmp_path: Path) -> None:
        self._setup_project(tmp_path)
        mock_builder = mock_builder_cls.return_value
        result = runner.invoke(app, ["clean", "some/feature"])
        assert result.exit_code == 0
        mock_builder.clean.assert_called_once()

    @patch("intentc.cli.main.Builder")
    @patch("intentc.cli.main.GitVersionControl")
    def test_clean_all(self, mock_vcs_cls: MagicMock, mock_builder_cls: MagicMock, tmp_path: Path) -> None:
        self._setup_project(tmp_path)
        mock_builder = mock_builder_cls.return_value
        result = runner.invoke(app, ["clean", "--all"])
        assert result.exit_code == 0
        mock_builder.clean_all.assert_called_once()

    @patch("intentc.cli.main.Builder")
    @patch("intentc.cli.main.GitVersionControl")
    def test_clean_no_target_no_all(self, mock_vcs_cls: MagicMock, mock_builder_cls: MagicMock, tmp_path: Path) -> None:
        self._setup_project(tmp_path)
        result = runner.invoke(app, ["clean"])
        assert result.exit_code == 2


class TestPlan:
    def _setup_project(self, tmp_path: Path) -> None:
        os.chdir(tmp_path)
        runner.invoke(app, ["init", "testproject"])

    @patch("intentc.cli.main.create_from_profile")
    def test_plan_unknown_feature(self, mock_create: MagicMock, tmp_path: Path) -> None:
        self._setup_project(tmp_path)
        result = runner.invoke(app, ["plan", "nonexistent/feature"])
        assert result.exit_code == 2
        assert "not found" in result.output.lower() or "not found" in (result.stderr or "").lower()


class TestStatus:
    @patch("intentc.cli.main.StateManager")
    @patch("intentc.cli.main.SQLiteBackend")
    def test_status_empty(self, mock_backend_cls: MagicMock, mock_state_cls: MagicMock, tmp_path: Path) -> None:
        os.chdir(tmp_path)
        save_config(Config(), tmp_path)
        mock_state = mock_state_cls.return_value
        mock_state.list_targets.return_value = []
        mock_state.get_build_result.return_value = None
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0


class TestDiff:
    @patch("intentc.cli.main.GitVersionControl")
    @patch("intentc.cli.main.StateManager")
    @patch("intentc.cli.main.SQLiteBackend")
    def test_diff_no_result(self, mock_backend_cls: MagicMock, mock_state_cls: MagicMock, mock_vcs_cls: MagicMock, tmp_path: Path) -> None:
        os.chdir(tmp_path)
        save_config(Config(), tmp_path)
        mock_state = mock_state_cls.return_value
        mock_state.get_build_result.return_value = None
        result = runner.invoke(app, ["diff", "some/feature"])
        assert result.exit_code == 2


class TestCompare:
    def test_compare_requires_args(self, tmp_path: Path) -> None:
        os.chdir(tmp_path)
        result = runner.invoke(app, ["compare"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Output formatting tests
# ---------------------------------------------------------------------------


class TestOutput:
    def test_create_console(self) -> None:
        c = create_console()
        assert c is not None

    def test_render_init_summary(self, capsys: pytest.CaptureFixture[str]) -> None:
        render_init_summary(["file1.ic", "file2.ic"])

    def test_print_error(self) -> None:
        print_error("something went wrong")

    def test_render_diff(self) -> None:
        render_diff("--- a/file\n+++ b/file\n@@ -1 +1 @@\n-old\n+new")


# ---------------------------------------------------------------------------
# No-args shows help
# ---------------------------------------------------------------------------


class TestNoArgs:
    def test_no_args_shows_help(self) -> None:
        result = runner.invoke(app, [])
        assert "Usage" in result.output or "compiler of intent" in result.output.lower()
