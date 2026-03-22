from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from intentc.build.agents.types import AgentProfile


class Config(BaseModel):
    default_profile: AgentProfile = Field(
        default_factory=lambda: AgentProfile(
            name="default",
            provider="claude",
            timeout=3600.0,
            retries=3,
        )
    )
    default_output_dir: str = "src"


def load_config(project_root: Path) -> Config:
    """Read .intentc/config.yaml and return a Config. Returns defaults if missing."""
    config_path = Path(project_root) / ".intentc" / "config.yaml"
    if not config_path.exists():
        return Config()
    try:
        data = yaml.safe_load(config_path.read_text()) or {}
    except Exception:
        return Config()

    profile_data = data.get("default_profile")
    if profile_data and isinstance(profile_data, dict):
        # Only pick known fields
        profile = AgentProfile(
            name=profile_data.get("name", "default"),
            provider=profile_data.get("provider", "claude"),
            timeout=float(profile_data.get("timeout", 3600)),
            retries=int(profile_data.get("retries", 3)),
        )
    else:
        profile = Config().default_profile

    output_dir = data.get("default_output_dir", "src")

    return Config(default_profile=profile, default_output_dir=output_dir)


def save_config(config: Config, project_root: Path) -> Path:
    """Write config to .intentc/config.yaml. Returns the path written."""
    config_dir = Path(project_root) / ".intentc"
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
    config_path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
    return config_path
