"""Config loading and saving for intentc CLI."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from intentc.build.agents import AgentProfile


class Config(BaseModel):
    """CLI configuration loaded from .intentc/config.yaml."""

    model_config = {"extra": "ignore"}

    default_profile: AgentProfile = Field(
        default_factory=lambda: AgentProfile(
            name="default", provider="claude", timeout=3600, retries=3
        )
    )
    default_output_dir: str = "src"


_CONFIG_PATH = ".intentc/config.yaml"


def load_config(project_root: Path) -> Config:
    """Read .intentc/config.yaml and return a Config.

    Returns sensible defaults when the file is missing.
    """
    config_file = project_root / _CONFIG_PATH
    if not config_file.exists():
        return Config()
    data = yaml.safe_load(config_file.read_text()) or {}
    return Config.model_validate(data)


def save_config(config: Config, project_root: Path) -> Path:
    """Write config to .intentc/config.yaml. Returns the path written."""
    config_file = project_root / _CONFIG_PATH
    config_file.parent.mkdir(parents=True, exist_ok=True)
    data = config.model_dump(mode="json")
    config_file.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
    return config_file
