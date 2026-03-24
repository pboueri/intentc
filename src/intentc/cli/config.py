"""Config loading and saving for intentc CLI."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from intentc.build.agents.models import AgentProfile


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

    model_config = {"extra": "ignore"}


def load_config(project_root: Path) -> Config:
    """Read .intentc/config.yaml and return a Config.

    Returns sensible defaults if the file is missing or malformed.
    """
    config_path = Path(project_root) / ".intentc" / "config.yaml"
    if not config_path.exists():
        return Config()

    try:
        raw = yaml.safe_load(config_path.read_text()) or {}
    except (yaml.YAMLError, OSError):
        return Config()

    if not isinstance(raw, dict):
        return Config()

    profile_data = raw.get("default_profile")
    profile = (
        AgentProfile(**profile_data)
        if isinstance(profile_data, dict)
        else Config().default_profile
    )

    output_dir = raw.get("default_output_dir", "src")
    if not isinstance(output_dir, str):
        output_dir = "src"

    return Config(default_profile=profile, default_output_dir=output_dir)


def save_config(config: Config, project_root: Path) -> Path:
    """Write config to .intentc/config.yaml. Returns the path written."""
    config_dir = Path(project_root) / ".intentc"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.yaml"

    data: dict = {
        "default_profile": {
            "name": config.default_profile.name,
            "provider": config.default_profile.provider,
            "timeout": config.default_profile.timeout,
            "retries": config.default_profile.retries,
        },
        "default_output_dir": config.default_output_dir,
    }

    config_path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
    return config_path
