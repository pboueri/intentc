"""Tests for the config package."""

import os
import tempfile
from datetime import timedelta

import yaml

from config.config import (
    BuildConfig,
    Config,
    LoggingConfig,
    get_default_config,
    get_default_profile,
    get_profile,
    load_config,
    merge_config,
    save_config,
    validate_config,
)
from core.types import AgentProfile, PromptTemplates, ToolConfig


# ---------------------------------------------------------------------------
# Default config creation
# ---------------------------------------------------------------------------


def test_get_default_config():
    cfg = get_default_config()
    assert cfg.version == 1
    assert "default" in cfg.profiles
    assert cfg.build.default_output == "build-default"
    assert cfg.logging.level == "info"


def test_get_default_config_profile_details():
    cfg = get_default_config()
    profile = cfg.profiles["default"]
    assert profile.provider == "claude"
    assert profile.timeout == timedelta(minutes=5)
    assert profile.retries == 3
    assert profile.rate_limit == timedelta(seconds=1)
    assert len(profile.tools) == 3
    tool_names = [t.name for t in profile.tools]
    assert "bash" in tool_names
    assert "file_read" in tool_names
    assert "file_write" in tool_names
    assert all(t.enabled for t in profile.tools)
    assert profile.skills == ["code-generation"]


def test_get_default_profile():
    profile = get_default_profile()
    assert profile.name == "default"
    assert profile.provider == "claude"
    assert profile.timeout == timedelta(minutes=5)
    assert profile.retries == 3
    assert profile.rate_limit == timedelta(seconds=1)
    assert len(profile.tools) == 3
    assert profile.skills == ["code-generation"]


# ---------------------------------------------------------------------------
# Load from YAML file
# ---------------------------------------------------------------------------


def test_load_config_from_yaml():
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = os.path.join(tmpdir, ".intentc")
        os.makedirs(config_dir)
        config_path = os.path.join(config_dir, "config.yaml")

        yaml_content = {
            "version": 1,
            "profiles": {
                "default": {
                    "provider": "claude",
                    "timeout": "10m",
                    "retries": 5,
                    "rate_limit": "2s",
                    "model_id": "claude-sonnet-4-6",
                    "tools": [{"name": "bash", "enabled": True}],
                    "skills": ["code-generation"],
                },
            },
            "build": {"default_output": "dist"},
            "logging": {"level": "debug"},
        }

        with open(config_path, "w") as f:
            yaml.dump(yaml_content, f)

        cfg = load_config(tmpdir)
        assert cfg.version == 1
        assert "default" in cfg.profiles
        assert cfg.profiles["default"].timeout == timedelta(minutes=10)
        assert cfg.profiles["default"].retries == 5
        assert cfg.profiles["default"].rate_limit == timedelta(seconds=2)
        assert cfg.profiles["default"].model_id == "claude-sonnet-4-6"
        assert cfg.build.default_output == "dist"
        assert cfg.logging.level == "debug"


def test_load_config_multiple_profiles():
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = os.path.join(tmpdir, ".intentc")
        os.makedirs(config_dir)
        config_path = os.path.join(config_dir, "config.yaml")

        yaml_content = {
            "version": 1,
            "profiles": {
                "default": {
                    "provider": "claude",
                    "timeout": "5m",
                    "retries": 3,
                    "rate_limit": "1s",
                    "tools": [{"name": "bash", "enabled": True}],
                    "skills": ["code-generation"],
                },
                "fast": {
                    "provider": "claude",
                    "timeout": "1m",
                    "retries": 1,
                    "rate_limit": "500ms",
                    "model_id": "claude-haiku",
                },
            },
            "build": {"default_output": "build-default"},
            "logging": {"level": "info"},
        }

        with open(config_path, "w") as f:
            yaml.dump(yaml_content, f)

        cfg = load_config(tmpdir)
        assert len(cfg.profiles) == 2
        assert "default" in cfg.profiles
        assert "fast" in cfg.profiles
        assert cfg.profiles["fast"].timeout == timedelta(minutes=1)
        assert cfg.profiles["fast"].rate_limit == timedelta(milliseconds=500)


# ---------------------------------------------------------------------------
# Load returns defaults when file missing
# ---------------------------------------------------------------------------


def test_load_config_missing_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = load_config(tmpdir)
        default = get_default_config()
        assert cfg.version == default.version
        assert "default" in cfg.profiles
        assert cfg.build.default_output == default.build.default_output
        assert cfg.logging.level == default.logging.level


def test_load_config_missing_intentc_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = load_config(tmpdir)
        assert cfg.version == 1
        assert "default" in cfg.profiles


# ---------------------------------------------------------------------------
# Save and reload roundtrip
# ---------------------------------------------------------------------------


def test_save_and_reload_roundtrip():
    with tempfile.TemporaryDirectory() as tmpdir:
        original = get_default_config()
        save_config(tmpdir, original)

        # Verify the file was created
        config_path = os.path.join(tmpdir, ".intentc", "config.yaml")
        assert os.path.isfile(config_path)

        # Reload and compare
        loaded = load_config(tmpdir)
        assert loaded.version == original.version
        assert loaded.build.default_output == original.build.default_output
        assert loaded.logging.level == original.logging.level
        assert set(loaded.profiles.keys()) == set(original.profiles.keys())

        orig_profile = original.profiles["default"]
        loaded_profile = loaded.profiles["default"]
        assert loaded_profile.provider == orig_profile.provider
        assert loaded_profile.timeout == orig_profile.timeout
        assert loaded_profile.retries == orig_profile.retries
        assert loaded_profile.rate_limit == orig_profile.rate_limit
        assert len(loaded_profile.tools) == len(orig_profile.tools)
        assert loaded_profile.skills == orig_profile.skills


def test_save_creates_directory():
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = get_default_config()
        save_config(tmpdir, cfg)
        assert os.path.isdir(os.path.join(tmpdir, ".intentc"))
        assert os.path.isfile(os.path.join(tmpdir, ".intentc", "config.yaml"))


def test_save_roundtrip_with_custom_values():
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = Config(
            version=1,
            profiles={
                "default": AgentProfile(
                    name="default",
                    provider="claude",
                    timeout=timedelta(minutes=10),
                    retries=5,
                    rate_limit=timedelta(seconds=2),
                    model_id="claude-sonnet-4-6",
                    tools=[ToolConfig(name="bash", enabled=True)],
                    skills=["code-generation", "testing"],
                ),
            },
            build=BuildConfig(default_output="custom-out"),
            logging=LoggingConfig(level="debug"),
        )

        save_config(tmpdir, cfg)
        loaded = load_config(tmpdir)

        assert loaded.build.default_output == "custom-out"
        assert loaded.logging.level == "debug"
        assert loaded.profiles["default"].timeout == timedelta(minutes=10)
        assert loaded.profiles["default"].retries == 5
        assert loaded.profiles["default"].model_id == "claude-sonnet-4-6"
        assert loaded.profiles["default"].skills == ["code-generation", "testing"]


# ---------------------------------------------------------------------------
# Merge config precedence
# ---------------------------------------------------------------------------


def test_merge_config_override_takes_precedence():
    base = get_default_config()
    override = Config(
        version=1,
        profiles={
            "default": AgentProfile(
                name="default",
                provider="codex",
            ),
        },
        build=BuildConfig(default_output="override-out"),
        logging=LoggingConfig(level="debug"),
    )

    merged = merge_config(base, override)
    assert merged.profiles["default"].provider == "codex"
    assert merged.build.default_output == "override-out"
    assert merged.logging.level == "debug"


def test_merge_config_base_preserved_when_override_default():
    base = Config(
        version=1,
        profiles={
            "default": AgentProfile(
                name="default",
                provider="claude",
                timeout=timedelta(minutes=10),
                retries=5,
            ),
        },
        build=BuildConfig(default_output="base-out"),
        logging=LoggingConfig(level="debug"),
    )

    # Override has all default values, so base should be preserved
    override = get_default_config()

    merged = merge_config(base, override)
    # base's non-default values should survive
    assert merged.profiles["default"].timeout == timedelta(minutes=10)
    assert merged.profiles["default"].retries == 5
    assert merged.build.default_output == "base-out"
    assert merged.logging.level == "debug"


def test_merge_config_adds_new_profiles():
    base = get_default_config()
    override = Config(
        version=1,
        profiles={
            "fast": AgentProfile(
                name="fast",
                provider="claude",
                timeout=timedelta(minutes=1),
                retries=1,
            ),
        },
        build=BuildConfig(),
        logging=LoggingConfig(),
    )

    merged = merge_config(base, override)
    assert "default" in merged.profiles
    assert "fast" in merged.profiles
    assert merged.profiles["fast"].timeout == timedelta(minutes=1)


# ---------------------------------------------------------------------------
# Get profile by name
# ---------------------------------------------------------------------------


def test_get_profile_by_name():
    cfg = Config(
        version=1,
        profiles={
            "default": AgentProfile(name="default", provider="claude"),
            "fast": AgentProfile(name="fast", provider="codex"),
        },
        build=BuildConfig(),
        logging=LoggingConfig(),
    )

    profile = get_profile(cfg, "fast")
    assert profile.name == "fast"
    assert profile.provider == "codex"


def test_get_profile_default():
    cfg = get_default_config()
    profile = get_profile(cfg, "default")
    assert profile.name == "default"
    assert profile.provider == "claude"


def test_get_profile_not_found():
    cfg = get_default_config()
    try:
        get_profile(cfg, "nonexistent")
        assert False, "Expected KeyError"
    except KeyError as e:
        assert "nonexistent" in str(e)


# ---------------------------------------------------------------------------
# Get profile falls back to default for empty name
# ---------------------------------------------------------------------------


def test_get_profile_empty_name_falls_back_to_default():
    cfg = get_default_config()
    profile = get_profile(cfg, "")
    assert profile.name == "default"
    assert profile.provider == "claude"


# ---------------------------------------------------------------------------
# Validate config catches errors
# ---------------------------------------------------------------------------


def test_validate_config_valid():
    cfg = get_default_config()
    violations = validate_config(cfg)
    errors = [v for v in violations if v.severity == "error"]
    assert len(errors) == 0


def test_validate_config_missing_version():
    cfg = Config(
        version=0,
        profiles={"default": AgentProfile(name="default", provider="claude")},
        build=BuildConfig(),
        logging=LoggingConfig(),
    )
    violations = validate_config(cfg)
    version_errors = [v for v in violations if v.field == "version"]
    assert len(version_errors) == 1
    assert "unsupported version" in version_errors[0].message


def test_validate_config_unsupported_version():
    cfg = Config(
        version=99,
        profiles={"default": AgentProfile(name="default", provider="claude")},
        build=BuildConfig(),
        logging=LoggingConfig(),
    )
    violations = validate_config(cfg)
    version_errors = [v for v in violations if v.field == "version"]
    assert len(version_errors) == 1
    assert "unsupported version" in version_errors[0].message


def test_validate_config_missing_default_profile():
    cfg = Config(
        version=1,
        profiles={"custom": AgentProfile(name="custom", provider="claude")},
        build=BuildConfig(),
        logging=LoggingConfig(),
    )
    violations = validate_config(cfg)
    profile_errors = [v for v in violations if v.field == "profiles" and "default" in v.message]
    assert len(profile_errors) == 1


def test_validate_config_empty_profiles():
    cfg = Config(
        version=1,
        profiles={},
        build=BuildConfig(),
        logging=LoggingConfig(),
    )
    violations = validate_config(cfg)
    profile_errors = [v for v in violations if v.field == "profiles"]
    assert len(profile_errors) >= 1


def test_validate_config_invalid_provider():
    cfg = Config(
        version=1,
        profiles={"default": AgentProfile(name="default", provider="gpt")},
        build=BuildConfig(),
        logging=LoggingConfig(),
    )
    violations = validate_config(cfg)
    provider_errors = [v for v in violations if "provider" in v.field]
    assert len(provider_errors) == 1
    assert "invalid provider" in provider_errors[0].message


def test_validate_config_cli_without_command():
    cfg = Config(
        version=1,
        profiles={
            "default": AgentProfile(name="default", provider="claude"),
            "custom_cli": AgentProfile(name="custom_cli", provider="cli", command=""),
        },
        build=BuildConfig(),
        logging=LoggingConfig(),
    )
    violations = validate_config(cfg)
    command_errors = [v for v in violations if "command" in v.field]
    assert len(command_errors) == 1
    assert "non-empty command" in command_errors[0].message


def test_validate_config_cli_with_command_valid():
    cfg = Config(
        version=1,
        profiles={
            "default": AgentProfile(name="default", provider="claude"),
            "custom_cli": AgentProfile(name="custom_cli", provider="cli", command="/usr/bin/my-tool"),
        },
        build=BuildConfig(),
        logging=LoggingConfig(),
    )
    violations = validate_config(cfg)
    command_errors = [v for v in violations if "command" in v.field]
    assert len(command_errors) == 0


def test_validate_config_empty_tool_name():
    cfg = Config(
        version=1,
        profiles={
            "default": AgentProfile(
                name="default",
                provider="claude",
                tools=[ToolConfig(name="", enabled=True)],
            ),
        },
        build=BuildConfig(),
        logging=LoggingConfig(),
    )
    violations = validate_config(cfg)
    tool_errors = [v for v in violations if "tools" in v.field]
    assert len(tool_errors) == 1
    assert "tool name must not be empty" in tool_errors[0].message


def test_validate_config_returns_list():
    """ValidateConfig must return a list of SchemaViolation, not a single error."""
    cfg = get_default_config()
    violations = validate_config(cfg)
    assert isinstance(violations, list)


def test_load_config_calls_validate():
    """LoadConfig should call ValidateConfig and raise on violations."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = os.path.join(tmpdir, ".intentc")
        os.makedirs(config_dir)
        config_path = os.path.join(config_dir, "config.yaml")

        yaml_content = {
            "version": 1,
            "profiles": {
                "default": {
                    "provider": "invalid_agent",
                },
            },
        }

        with open(config_path, "w") as f:
            yaml.dump(yaml_content, f)

        try:
            load_config(tmpdir)
            assert False, "Expected ValueError"
        except ValueError as e:
            assert "validation failed" in str(e).lower()


def test_get_default_config_profiles_map_has_default():
    """Profiles map must contain a 'default' entry."""
    cfg = get_default_config()
    assert "default" in cfg.profiles
    assert cfg.profiles["default"].provider == "claude"


# ---------------------------------------------------------------------------
# Duration parsing in YAML
# ---------------------------------------------------------------------------


def test_duration_parsing_in_yaml():
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = os.path.join(tmpdir, ".intentc")
        os.makedirs(config_dir)
        config_path = os.path.join(config_dir, "config.yaml")

        yaml_content = {
            "version": 1,
            "profiles": {
                "default": {
                    "provider": "claude",
                    "timeout": "10m",
                    "retries": 3,
                    "rate_limit": "500ms",
                    "tools": [{"name": "bash", "enabled": True}],
                    "skills": ["code-generation"],
                },
            },
            "build": {"default_output": "build-default"},
            "logging": {"level": "info"},
        }

        with open(config_path, "w") as f:
            yaml.dump(yaml_content, f)

        cfg = load_config(tmpdir)
        profile = cfg.profiles["default"]
        assert profile.timeout == timedelta(minutes=10)
        assert profile.rate_limit == timedelta(milliseconds=500)


def test_duration_parsing_hours():
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = os.path.join(tmpdir, ".intentc")
        os.makedirs(config_dir)
        config_path = os.path.join(config_dir, "config.yaml")

        yaml_content = {
            "version": 1,
            "profiles": {
                "default": {
                    "provider": "claude",
                    "timeout": "2h",
                    "retries": 3,
                    "rate_limit": "5s",
                },
            },
            "build": {"default_output": "build-default"},
            "logging": {"level": "info"},
        }

        with open(config_path, "w") as f:
            yaml.dump(yaml_content, f)

        cfg = load_config(tmpdir)
        assert cfg.profiles["default"].timeout == timedelta(hours=2)
        assert cfg.profiles["default"].rate_limit == timedelta(seconds=5)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_load_config_parse_error():
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = os.path.join(tmpdir, ".intentc")
        os.makedirs(config_dir)
        config_path = os.path.join(config_dir, "config.yaml")

        # Write invalid YAML that safe_load can parse but is not a dict
        with open(config_path, "w") as f:
            f.write("- just\n- a\n- list\n")

        try:
            load_config(tmpdir)
            assert False, "Expected ValueError"
        except ValueError as e:
            assert "mapping" in str(e).lower()


def test_load_config_validation_error():
    """Config with invalid provider should raise on load."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = os.path.join(tmpdir, ".intentc")
        os.makedirs(config_dir)
        config_path = os.path.join(config_dir, "config.yaml")

        yaml_content = {
            "version": 1,
            "profiles": {
                "default": {
                    "provider": "invalid_provider",
                    "timeout": "5m",
                    "retries": 3,
                    "rate_limit": "1s",
                },
            },
            "build": {"default_output": "build-default"},
            "logging": {"level": "info"},
        }

        with open(config_path, "w") as f:
            yaml.dump(yaml_content, f)

        try:
            load_config(tmpdir)
            assert False, "Expected ValueError"
        except ValueError as e:
            assert "validation failed" in str(e).lower()


# ---------------------------------------------------------------------------
# YAML serialization format
# ---------------------------------------------------------------------------


def test_save_produces_valid_yaml():
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = get_default_config()
        save_config(tmpdir, cfg)

        config_path = os.path.join(tmpdir, ".intentc", "config.yaml")
        with open(config_path, "r") as f:
            raw = yaml.safe_load(f)

        assert raw["version"] == 1
        assert "default" in raw["profiles"]
        assert raw["profiles"]["default"]["provider"] == "claude"
        assert raw["build"]["default_output"] == "build-default"
        assert raw["logging"]["level"] == "info"


def test_save_includes_duration_strings():
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = get_default_config()
        save_config(tmpdir, cfg)

        config_path = os.path.join(tmpdir, ".intentc", "config.yaml")
        with open(config_path, "r") as f:
            raw = yaml.safe_load(f)

        default_profile = raw["profiles"]["default"]
        assert default_profile["timeout"] == "5m"
        assert default_profile["rate_limit"] == "1s"


# ---------------------------------------------------------------------------
# Prompt templates roundtrip
# ---------------------------------------------------------------------------


def test_save_roundtrip_with_prompt_templates():
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = Config(
            version=1,
            profiles={
                "default": AgentProfile(
                    name="default",
                    provider="claude",
                    prompt_templates=PromptTemplates(
                        build="Build {target}",
                        validate="Validate {target}",
                        system="You are a code generator.",
                    ),
                ),
            },
            build=BuildConfig(),
            logging=LoggingConfig(),
        )

        save_config(tmpdir, cfg)
        loaded = load_config(tmpdir)

        pt = loaded.profiles["default"].prompt_templates
        assert pt.build == "Build {target}"
        assert pt.validate_prompt == "Validate {target}"
        assert pt.system == "You are a code generator."
