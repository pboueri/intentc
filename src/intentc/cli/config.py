"""Project configuration: .intentc/config.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError

from intentc.build.agents import AgentProfile

CONFIG_RELATIVE_PATH = Path(".intentc") / "config.yaml"


class ConfigError(ValueError):
    """The config file exists but cannot be used."""


def _default_profile() -> AgentProfile:
    return AgentProfile(name="default", provider="claude", timeout=3600, retries=3)


class Config(BaseModel):
    """CLI defaults. Only two fields; unknown keys in the file are ignored."""

    default_profile: AgentProfile = Field(default_factory=_default_profile)
    default_output_dir: str = "src"


def config_path(project_root: Path) -> Path:
    return Path(project_root) / CONFIG_RELATIVE_PATH


def load_config(project_root: Path) -> Config:
    """Read the config, or return defaults when the file is missing. Malformed files raise ConfigError."""
    path = config_path(project_root)
    if not path.exists():
        return Config()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"{path}: could not read config: {exc}") from exc
    if data is None:
        return Config()
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a YAML mapping at the top level")

    fields: dict[str, Any] = {}
    profile_data = data.get("default_profile")
    if profile_data is not None:
        if not isinstance(profile_data, dict):
            raise ConfigError(f"{path}: 'default_profile' must be a mapping of profile fields")
        merged = {"name": "default", "provider": "claude", **profile_data}
        try:
            fields["default_profile"] = AgentProfile(**merged)
        except ValidationError as exc:
            problems = "; ".join(f"{'.'.join(str(l) for l in e['loc'])}: {e['msg']}" for e in exc.errors())
            raise ConfigError(f"{path}: invalid default_profile ({problems})") from exc
    output_dir = data.get("default_output_dir")
    if output_dir is not None:
        if not isinstance(output_dir, str) or not output_dir.strip():
            raise ConfigError(f"{path}: 'default_output_dir' must be a non-empty string")
        fields["default_output_dir"] = output_dir
    return Config(**fields)


def save_config(config: Config, project_root: Path) -> Path:
    """Write the config file (creating .intentc/). Returns the path written."""
    path = config_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = config.default_profile
    profile_data: dict[str, Any] = {
        "name": profile.name,
        "provider": profile.provider,
        "timeout": profile.timeout,
        "retries": profile.retries,
    }
    for key in ("command", "model_id", "effort"):
        value = getattr(profile, key)
        if value:
            profile_data[key] = value
    if profile.cli_args:
        profile_data["cli_args"] = list(profile.cli_args)
    data = {"default_profile": profile_data, "default_output_dir": config.default_output_dir}
    path.write_text(yaml.safe_dump(data, default_flow_style=False, sort_keys=False), encoding="utf-8")
    return path
