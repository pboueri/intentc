"""Tests for intentc.cli.config."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from intentc.cli.config import Config, ConfigError, load_config, save_config


class TestLoadConfig:
    def test_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        config = load_config(tmp_path)

        assert isinstance(config, Config)
        assert config.default_output_dir == "src"
        assert config.default_profile.name == "default"
        assert config.default_profile.provider == "claude"

    def test_empty_file_returns_defaults(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".intentc" / "config.yaml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text("", encoding="utf-8")

        config = load_config(tmp_path)

        assert config.default_output_dir == "src"

    def test_reads_custom_values(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".intentc" / "config.yaml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(
            yaml.safe_dump(
                {
                    "default_profile": {
                        "name": "fast",
                        "provider": "claude",
                        "model_id": "claude-sonnet-5",
                        "effort": "low",
                    },
                    "default_output_dir": "out",
                }
            ),
            encoding="utf-8",
        )

        config = load_config(tmp_path)

        assert config.default_output_dir == "out"
        assert config.default_profile.name == "fast"
        assert config.default_profile.model_id == "claude-sonnet-5"
        assert config.default_profile.effort == "low"

    def test_ignores_unknown_top_level_fields(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".intentc" / "config.yaml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(
            yaml.safe_dump({"default_output_dir": "out", "something_else": "ignored"}),
            encoding="utf-8",
        )

        config = load_config(tmp_path)

        assert config.default_output_dir == "out"
        assert not hasattr(config, "something_else")

    def test_invalid_yaml_raises_config_error(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".intentc" / "config.yaml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text("default_output_dir: [unterminated", encoding="utf-8")

        with pytest.raises(ConfigError) as exc_info:
            load_config(tmp_path)

        assert str(config_path) in str(exc_info.value)

    def test_non_mapping_top_level_raises_config_error(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".intentc" / "config.yaml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text("- just\n- a\n- list\n", encoding="utf-8")

        with pytest.raises(ConfigError):
            load_config(tmp_path)

    def test_default_profile_not_a_mapping_raises_config_error(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".intentc" / "config.yaml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(
            yaml.safe_dump({"default_profile": "not-a-mapping"}), encoding="utf-8"
        )

        with pytest.raises(ConfigError) as exc_info:
            load_config(tmp_path)

        assert "default_profile" in str(exc_info.value)

    def test_default_profile_failing_validation_raises_config_error(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".intentc" / "config.yaml"
        config_path.parent.mkdir(parents=True)
        # AgentProfile.provider is required; omitting it should fail validation.
        config_path.write_text(
            yaml.safe_dump({"default_profile": {"name": "broken"}}), encoding="utf-8"
        )

        with pytest.raises(ConfigError):
            load_config(tmp_path)


class TestSaveConfig:
    def test_round_trip(self, tmp_path: Path) -> None:
        config = Config()
        path = save_config(config, tmp_path)

        assert path == tmp_path / ".intentc" / "config.yaml"
        assert path.is_file()

        reloaded = load_config(tmp_path)
        assert reloaded.default_output_dir == config.default_output_dir
        assert reloaded.default_profile.name == config.default_profile.name

    def test_parameter_order_is_config_then_project_root(self, tmp_path: Path) -> None:
        config = Config(default_output_dir="build-out")
        path = save_config(config, tmp_path)
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert data["default_output_dir"] == "build-out"
