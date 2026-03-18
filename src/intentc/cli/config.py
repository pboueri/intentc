"""Config loading and saving for intentc projects."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

from intentc.build.agents import AgentProfile


class Config(BaseModel):
    """Project-level configuration loaded from .intentc/config.yaml."""

    model_config = {"extra": "ignore"}

    default_profile: AgentProfile = AgentProfile(
        name="default",
        provider="claude",
        timeout=3600,
        retries=3,
    )
    default_validation_profile: AgentProfile = AgentProfile(
        name="default-validation",
        provider="claude",
        timeout=3600,
        retries=3,
        model_id="claude-sonnet-4-6",
    )
    default_output_dir: str = "src"
    max_parallel_validations: int = 5


_CONFIG_PATH = ".intentc/config.yaml"


def load_config(project_root: Path) -> Config:
    """Load config from .intentc/config.yaml, returning sensible defaults if missing."""
    config_path = project_root / _CONFIG_PATH
    if not config_path.exists():
        return Config()
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError):
        return Config()
    if not isinstance(data, dict):
        return Config()

    profile_data = data.get("default_profile")
    profile = (
        AgentProfile(**profile_data)
        if isinstance(profile_data, dict)
        else Config().default_profile
    )
    val_profile_data = data.get("default_validation_profile")
    val_profile = (
        AgentProfile(**val_profile_data)
        if isinstance(val_profile_data, dict)
        else Config().default_validation_profile
    )
    return Config(
        default_profile=profile,
        default_validation_profile=val_profile,
        default_output_dir=data.get("default_output_dir", "src"),
        max_parallel_validations=int(data.get("max_parallel_validations", 5)),
    )


def save_config(config: Config, project_root: Path) -> Path:
    """Write config to .intentc/config.yaml. Returns the config file path."""
    config_path = project_root / _CONFIG_PATH
    config_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "default_profile": {
            "name": config.default_profile.name,
            "provider": config.default_profile.provider,
            "timeout": config.default_profile.timeout,
            "retries": config.default_profile.retries,
        },
        "default_validation_profile": {
            "name": config.default_validation_profile.name,
            "provider": config.default_validation_profile.provider,
            "timeout": config.default_validation_profile.timeout,
            "retries": config.default_validation_profile.retries,
            "model_id": config.default_validation_profile.model_id,
        },
        "default_output_dir": config.default_output_dir,
        "max_parallel_validations": config.max_parallel_validations,
    }
    config_path.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")
    return config_path
