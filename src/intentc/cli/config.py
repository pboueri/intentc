"""Configuration loading and saving for intentc CLI."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from intentc.build.agents import AgentProfile


class Config(BaseModel):
    """CLI configuration loaded from .intentc/config.yaml."""

    default_profile: AgentProfile = Field(
        default_factory=lambda: AgentProfile(
            name="default",
            provider="claude",
            timeout=3600,
            retries=3,
        )
    )
    default_output_dir: str = "src"


def load_config(project_root: Path) -> Config:
    """Load config from .intentc/config.yaml, returning defaults if missing."""
    config_path = project_root / ".intentc" / "config.yaml"
    if not config_path.exists():
        return Config()

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (yaml.YAMLError, OSError):
        return Config()

    profile_data = data.get("default_profile")
    if profile_data and isinstance(profile_data, dict):
        profile = AgentProfile(**profile_data)
    else:
        profile = Config().default_profile

    output_dir = data.get("default_output_dir", "src")

    return Config(default_profile=profile, default_output_dir=output_dir)


def save_config(config: Config, project_root: Path) -> Path:
    """Write config to .intentc/config.yaml. Returns the path written."""
    config_dir = project_root / ".intentc"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.yaml"

    data = {
        "default_profile": {
            "name": config.default_profile.name,
            "provider": config.default_profile.provider,
            "timeout": config.default_profile.timeout,
            "retries": config.default_profile.retries,
        },
        "default_output_dir": config.default_output_dir,
    }

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    return config_path
