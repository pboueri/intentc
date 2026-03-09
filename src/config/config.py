"""Config package for intentc - manages .intentc/config.yaml configuration."""

from __future__ import annotations

import os
from datetime import timedelta
from typing import Any

import yaml
from pydantic import BaseModel, Field

from core.types import (
    AgentProfile,
    SchemaViolation,
    ToolConfig,
    _parse_duration,
)


class BuildConfig(BaseModel):
    """Build-related configuration."""

    default_output: str = "build-default"

    model_config = {"extra": "ignore"}


class LoggingConfig(BaseModel):
    """Logging configuration."""

    level: str = "info"  # debug, info, warn, error

    model_config = {"extra": "ignore"}


class Config(BaseModel):
    """Root configuration object loaded from .intentc/config.yaml."""

    version: int = 1
    profiles: dict[str, AgentProfile] = Field(default_factory=dict)
    build: BuildConfig = Field(default_factory=BuildConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    model_config = {"extra": "ignore"}


_VALID_PROVIDERS = {"claude", "codex", "cli"}
_VALID_LOG_LEVELS = {"debug", "info", "warn", "error"}


def get_default_profile() -> AgentProfile:
    """Return the default claude agent profile with standard tools and skills."""
    return AgentProfile(
        name="default",
        provider="claude",
        timeout=timedelta(minutes=5),
        retries=3,
        rate_limit=timedelta(seconds=1),
        tools=[
            ToolConfig(name="bash", enabled=True),
            ToolConfig(name="file_read", enabled=True),
            ToolConfig(name="file_write", enabled=True),
        ],
        skills=["code-generation"],
    )


def get_default_config() -> Config:
    """Return a Config with sensible defaults."""
    return Config(
        version=1,
        profiles={"default": get_default_profile()},
        build=BuildConfig(default_output="build-default"),
        logging=LoggingConfig(level="info"),
    )


def load_config(project_root: str) -> Config:
    """Load configuration from {project_root}/.intentc/config.yaml.

    If the file does not exist, returns the default config.
    If the file has parse errors, raises an exception.
    Validates the config after loading.
    """
    config_path = os.path.join(project_root, ".intentc", "config.yaml")

    if not os.path.isfile(config_path):
        return get_default_config()

    with open(config_path, "r") as f:
        raw = yaml.safe_load(f)

    if raw is None:
        return get_default_config()

    if not isinstance(raw, dict):
        raise ValueError(f"Config file {config_path} must contain a YAML mapping, got {type(raw).__name__}")

    config = _parse_config_dict(raw)

    violations = validate_config(config)
    errors = [v for v in violations if v.severity == "error"]
    if errors:
        messages = "; ".join(f"{v.field}: {v.message}" for v in errors)
        raise ValueError(f"Config validation failed: {messages}")

    return config


def _parse_config_dict(raw: dict[str, Any]) -> Config:
    """Parse a raw YAML dictionary into a Config object."""
    version = raw.get("version", 1)

    # Parse profiles
    profiles: dict[str, AgentProfile] = {}
    raw_profiles = raw.get("profiles", {})
    if isinstance(raw_profiles, dict):
        for name, profile_data in raw_profiles.items():
            if isinstance(profile_data, dict):
                profiles[name] = AgentProfile.from_yaml_dict(name, profile_data)

    # If no profiles were parsed, use the default
    if not profiles:
        profiles = {"default": get_default_profile()}

    # Parse build config
    build = BuildConfig()
    raw_build = raw.get("build", {})
    if isinstance(raw_build, dict):
        build = BuildConfig(**raw_build)

    # Parse logging config
    logging_config = LoggingConfig()
    raw_logging = raw.get("logging", {})
    if isinstance(raw_logging, dict):
        logging_config = LoggingConfig(**raw_logging)

    return Config(
        version=version,
        profiles=profiles,
        build=build,
        logging=logging_config,
    )


def save_config(project_root: str, config: Config) -> None:
    """Write configuration to {project_root}/.intentc/config.yaml.

    Creates the .intentc/ directory if it does not exist.
    """
    config_dir = os.path.join(project_root, ".intentc")
    os.makedirs(config_dir, exist_ok=True)

    config_path = os.path.join(config_dir, "config.yaml")
    data = _serialize_config(config)

    with open(config_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def _serialize_config(config: Config) -> dict[str, Any]:
    """Serialize a Config to a YAML-friendly dictionary."""
    data: dict[str, Any] = {"version": config.version}

    # Serialize profiles
    profiles: dict[str, Any] = {}
    for name, profile in config.profiles.items():
        profiles[name] = profile.to_yaml_dict()
    data["profiles"] = profiles

    # Serialize build
    data["build"] = {"default_output": config.build.default_output}

    # Serialize logging
    data["logging"] = {"level": config.logging.level}

    return data


def merge_config(base: Config, override: Config) -> Config:
    """Merge two configs, with non-default fields in override taking precedence.

    Profile merging is done per-profile: non-default fields in each overriding
    profile replace the corresponding fields in the base profile.
    """
    defaults = get_default_config()
    default_profile = get_default_profile()

    # Merge version
    version = override.version if override.version != defaults.version else base.version

    # Merge profiles
    merged_profiles: dict[str, AgentProfile] = dict(base.profiles)
    for name, override_profile in override.profiles.items():
        if name in merged_profiles:
            merged_profiles[name] = _merge_profile(merged_profiles[name], override_profile, default_profile)
        else:
            merged_profiles[name] = override_profile

    # Merge build
    build = BuildConfig(
        default_output=(
            override.build.default_output
            if override.build.default_output != defaults.build.default_output
            else base.build.default_output
        ),
    )

    # Merge logging
    logging_config = LoggingConfig(
        level=(
            override.logging.level
            if override.logging.level != defaults.logging.level
            else base.logging.level
        ),
    )

    return Config(
        version=version,
        profiles=merged_profiles,
        build=build,
        logging=logging_config,
    )


def _merge_profile(base: AgentProfile, override: AgentProfile, defaults: AgentProfile) -> AgentProfile:
    """Merge two agent profiles, with non-default override fields winning."""
    return AgentProfile(
        name=override.name if override.name != defaults.name else base.name,
        provider=override.provider if override.provider != defaults.provider else base.provider,
        command=override.command if override.command != defaults.command else base.command,
        cli_args=override.cli_args if override.cli_args != defaults.cli_args else base.cli_args,
        timeout=override.timeout if override.timeout != defaults.timeout else base.timeout,
        retries=override.retries if override.retries != defaults.retries else base.retries,
        rate_limit=override.rate_limit if override.rate_limit != defaults.rate_limit else base.rate_limit,
        prompt_templates=(
            override.prompt_templates
            if override.prompt_templates != defaults.prompt_templates
            else base.prompt_templates
        ),
        tools=override.tools if override.tools != defaults.tools else base.tools,
        skills=override.skills if override.skills != defaults.skills else base.skills,
        model_id=override.model_id if override.model_id != defaults.model_id else base.model_id,
    )


def get_profile(cfg: Config, name: str) -> AgentProfile:
    """Look up a named profile in the config.

    If name is empty, falls back to "default".
    Raises KeyError if the profile is not found.
    """
    if not name:
        name = "default"

    if name not in cfg.profiles:
        available = ", ".join(sorted(cfg.profiles.keys()))
        raise KeyError(f"Profile '{name}' not found. Available profiles: {available}")

    return cfg.profiles[name]


def validate_config(cfg: Config) -> list[SchemaViolation]:
    """Validate a Config and return a list of schema violations.

    Checks:
    - version present and supported (currently 1)
    - profiles map present with "default" entry
    - Each profile has valid provider ("claude", "codex", "cli")
    - CLI provider profiles have non-empty command
    - Duration fields parseable
    - Tool names non-empty
    """
    violations: list[SchemaViolation] = []

    # Check version
    if cfg.version is None:
        violations.append(SchemaViolation(
            field="version",
            message="version is required",
            severity="error",
        ))
    elif cfg.version not in (1,):
        violations.append(SchemaViolation(
            field="version",
            message=f"unsupported version {cfg.version}, supported versions: [1]",
            severity="error",
        ))

    # Check profiles map
    if not cfg.profiles:
        violations.append(SchemaViolation(
            field="profiles",
            message="profiles map is required and must not be empty",
            severity="error",
        ))
    else:
        if "default" not in cfg.profiles:
            violations.append(SchemaViolation(
                field="profiles",
                message="profiles must contain a 'default' entry",
                severity="error",
            ))

        # Validate each profile
        for name, profile in cfg.profiles.items():
            prefix = f"profiles.{name}"

            # Valid provider
            if profile.provider not in _VALID_PROVIDERS:
                violations.append(SchemaViolation(
                    field=f"{prefix}.provider",
                    message=f"invalid provider '{profile.provider}', must be one of: {', '.join(sorted(_VALID_PROVIDERS))}",
                    severity="error",
                ))

            # CLI provider must have command
            if profile.provider == "cli" and not profile.command:
                violations.append(SchemaViolation(
                    field=f"{prefix}.command",
                    message="CLI provider profiles must have a non-empty command",
                    severity="error",
                ))

            # Duration fields parseable (timeout, rate_limit)
            # Since they are already parsed into timedelta by pydantic/from_yaml_dict,
            # we just verify they are valid timedeltas (non-negative)
            if profile.timeout.total_seconds() < 0:
                violations.append(SchemaViolation(
                    field=f"{prefix}.timeout",
                    message="timeout must be non-negative",
                    severity="error",
                ))
            if profile.rate_limit.total_seconds() < 0:
                violations.append(SchemaViolation(
                    field=f"{prefix}.rate_limit",
                    message="rate_limit must be non-negative",
                    severity="error",
                ))

            # Tool names non-empty
            for i, tool in enumerate(profile.tools):
                if not tool.name:
                    violations.append(SchemaViolation(
                        field=f"{prefix}.tools[{i}].name",
                        message="tool name must not be empty",
                        severity="error",
                    ))

    # Validate logging level
    if cfg.logging.level not in _VALID_LOG_LEVELS:
        violations.append(SchemaViolation(
            field="logging.level",
            message=f"invalid log level '{cfg.logging.level}', must be one of: {', '.join(sorted(_VALID_LOG_LEVELS))}",
            severity="warning",
        ))

    return violations
