"""Project configuration: loading and saving `.intentc/config.yaml`.

The config file lets the CLI resolve a default agent profile and output
directory without requiring flags on every invocation. Missing config falls
back to hardcoded defaults; a malformed config is a hard error (`ConfigError`)
so a broken build never happens silently with the wrong agent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from intentc.build.agents import AgentProfile

_CONFIG_RELATIVE_PATH = Path(".intentc") / "config.yaml"


class ConfigError(ValueError):
    """Raised when `.intentc/config.yaml` exists but cannot be parsed."""


def _default_profile() -> AgentProfile:
    return AgentProfile(
        name="default",
        provider="claude",
        timeout=3600,
        retries=3,
        permission_mode="auto",
    )


class Config(BaseModel):
    """CLI-level configuration loaded from `.intentc/config.yaml`."""

    model_config = ConfigDict(extra="ignore")

    default_profile: AgentProfile = Field(default_factory=_default_profile)
    default_output_dir: str = "src"


def load_config(project_root: Union[str, Path]) -> Config:
    """Read `.intentc/config.yaml` under `project_root`.

    Returns hardcoded defaults when the file is missing. Raises `ConfigError`
    (naming the file and the problem) when it exists but is malformed.
    """
    config_path = Path(project_root) / _CONFIG_RELATIVE_PATH
    if not config_path.is_file():
        return Config()

    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{config_path}: could not read config file: {exc}") from exc

    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{config_path}: invalid YAML: {exc}") from exc

    if data is None:
        return Config()

    if not isinstance(data, dict):
        raise ConfigError(
            f"{config_path}: expected a mapping at the top level, got {type(data).__name__}"
        )

    if "default_profile" in data and not isinstance(data["default_profile"], dict):
        raise ConfigError(
            f"{config_path}: 'default_profile' must be a mapping, "
            f"got {type(data['default_profile']).__name__}"
        )

    try:
        return Config(**data)
    except ValidationError as exc:
        raise ConfigError(f"{config_path}: {exc}") from exc


def save_config(config: Config, project_root: Union[str, Path]) -> Path:
    """Write `config` to `.intentc/config.yaml` under `project_root`.

    Parameter order is config first, project_root second. Returns the path written.
    """
    config_path = Path(project_root) / _CONFIG_RELATIVE_PATH
    config_path.parent.mkdir(parents=True, exist_ok=True)
    data = config.model_dump(mode="json", exclude_none=True)
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return config_path
