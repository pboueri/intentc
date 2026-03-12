"""Core types for intentc - foundation types with zero internal dependencies."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ValidationType(str, Enum):
    """Validation type enum."""

    FILE_CHECK = "file_check"
    FOLDER_CHECK = "folder_check"
    COMMAND_CHECK = "command_check"
    LLM_JUDGE = "llm_judge"


class TargetStatus(str, Enum):
    """Target build status enum."""

    PENDING = "pending"
    BUILDING = "building"
    BUILT = "built"
    FAILED = "failed"
    OUTDATED = "outdated"


class BuildPhase(str, Enum):
    """Phase of a build step."""

    RESOLVE_DEPS = "resolve_deps"
    READ_PLAN = "read_plan"
    BUILD = "build"
    POST_BUILD = "post_build"
    VALIDATE = "validate"


class StepStatus(str, Enum):
    """Status of a build step."""

    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class Intent(BaseModel):
    """Represents a parsed .ic file."""

    name: str = ""
    version: int = 1
    depends_on: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    profile: str = ""
    content: str = ""
    file_path: str = ""

    model_config = {"extra": "ignore"}


class Validation(BaseModel):
    """Represents a single validation definition from an .icv file."""

    name: str = ""
    type: ValidationType = ValidationType.FILE_CHECK
    hidden: bool = False
    parameters: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "ignore"}


class ValidationFile(BaseModel):
    """Represents a parsed .icv file containing multiple validations."""

    target: str = ""
    version: int = 1
    judge_profile: str = ""
    validations: list[Validation] = Field(default_factory=list)
    file_path: str = ""

    model_config = {"extra": "ignore"}


class BuildStep(BaseModel):
    """Represents a single step within a build process."""

    phase: BuildPhase = BuildPhase.BUILD
    status: StepStatus = StepStatus.SUCCESS
    started_at: datetime = Field(default_factory=datetime.now)
    ended_at: datetime = Field(default_factory=datetime.now)
    duration_seconds: float = 0.0
    summary: str = ""
    error: str = ""
    files_changed: int = 0
    diff_stat: str = ""
    diff: str = ""

    model_config = {"extra": "ignore"}


class ToolConfig(BaseModel):
    """Represents a tool available to the agent."""

    name: str = ""
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "ignore"}


class PromptTemplates(BaseModel):
    """Customizable template strings for agent prompts."""

    build: str = ""
    validate_prompt: str = Field(default="", alias="validate")
    system: str = ""

    model_config = {"extra": "ignore", "populate_by_name": True}


def _parse_duration(value: Any) -> timedelta:
    """Parse a duration string like '5m', '1s', '2h' into timedelta."""
    if isinstance(value, timedelta):
        return value
    if isinstance(value, (int, float)):
        return timedelta(seconds=value)
    s = str(value).strip()
    if not s:
        return timedelta(seconds=0)
    if s.endswith("ms"):
        return timedelta(milliseconds=float(s[:-2]))
    if s.endswith("s"):
        return timedelta(seconds=float(s[:-1]))
    if s.endswith("m"):
        return timedelta(minutes=float(s[:-1]))
    if s.endswith("h"):
        return timedelta(hours=float(s[:-1]))
    try:
        return timedelta(seconds=float(s))
    except ValueError:
        return timedelta(seconds=0)


def _serialize_duration(td: timedelta) -> str:
    """Serialize timedelta to a human-readable duration string."""
    total_seconds = td.total_seconds()
    if total_seconds == 0:
        return "0s"
    if total_seconds < 1:
        return f"{int(total_seconds * 1000)}ms"
    if total_seconds < 60:
        s = total_seconds
        return f"{int(s)}s" if s == int(s) else f"{s}s"
    if total_seconds < 3600:
        m = total_seconds / 60
        return f"{int(m)}m" if m == int(m) else f"{m}m"
    h = total_seconds / 3600
    return f"{int(h)}h" if h == int(h) else f"{h}h"


class AgentProfile(BaseModel):
    """Named, reusable agent configuration."""

    name: str = "default"
    provider: str = "claude"
    command: str = ""
    cli_args: list[str] = Field(default_factory=list)
    timeout: timedelta = timedelta(minutes=5)
    retries: int = 3
    rate_limit: timedelta = timedelta(seconds=1)
    prompt_templates: PromptTemplates = Field(default_factory=PromptTemplates)
    tools: list[ToolConfig] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    model_id: str = ""

    model_config = {"extra": "ignore"}

    @classmethod
    def from_yaml_dict(cls, name: str, data: dict[str, Any]) -> "AgentProfile":
        """Create an AgentProfile from a YAML dictionary."""
        d = dict(data)
        d["name"] = name
        if "timeout" in d:
            d["timeout"] = _parse_duration(d["timeout"])
        if "rate_limit" in d:
            d["rate_limit"] = _parse_duration(d["rate_limit"])
        if "prompt_templates" in d and isinstance(d["prompt_templates"], dict):
            d["prompt_templates"] = PromptTemplates(**d["prompt_templates"])
        if "tools" in d and isinstance(d["tools"], list):
            d["tools"] = [
                ToolConfig(**t) if isinstance(t, dict) else t for t in d["tools"]
            ]
        return cls(**d)

    def to_yaml_dict(self) -> dict[str, Any]:
        """Serialize to a YAML-friendly dictionary."""
        d: dict[str, Any] = {}
        d["provider"] = self.provider
        if self.command:
            d["command"] = self.command
        if self.cli_args:
            d["cli_args"] = self.cli_args
        d["timeout"] = _serialize_duration(self.timeout)
        d["retries"] = self.retries
        d["rate_limit"] = _serialize_duration(self.rate_limit)
        if self.model_id:
            d["model_id"] = self.model_id
        if self.prompt_templates.build or self.prompt_templates.validate_prompt or self.prompt_templates.system:
            pt: dict[str, str] = {}
            if self.prompt_templates.build:
                pt["build"] = self.prompt_templates.build
            if self.prompt_templates.validate_prompt:
                pt["validate"] = self.prompt_templates.validate_prompt
            if self.prompt_templates.system:
                pt["system"] = self.prompt_templates.system
            d["prompt_templates"] = pt
        if self.tools:
            d["tools"] = [
                {"name": t.name, "enabled": t.enabled, **({"config": t.config} if t.config else {})}
                for t in self.tools
            ]
        if self.skills:
            d["skills"] = self.skills
        return d


class Target(BaseModel):
    """Represents a buildable unit."""

    name: str = ""
    intent: Intent = Field(default_factory=Intent)
    validations: list[ValidationFile] = Field(default_factory=list)
    dependencies: list["Target"] = Field(default_factory=list)
    status: TargetStatus = TargetStatus.PENDING

    model_config = {"extra": "ignore", "arbitrary_types_allowed": True}


class BuildResult(BaseModel):
    """Represents the outcome of a single target build."""

    target: str = ""
    generation_id: str = ""
    success: bool = False
    error: str = ""
    generated_at: datetime = Field(default_factory=datetime.now)
    files: list[str] = Field(default_factory=list)
    steps: list["BuildStep"] = Field(default_factory=list)
    total_duration_seconds: float = 0.0
    output_dir: str = ""

    model_config = {"extra": "ignore"}


class ValidationResult(BaseModel):
    """Represents the outcome of running a single validation."""

    validation_name: str = ""
    passed: bool = False
    message: str = ""
    details: list[str] = Field(default_factory=list)
    severity: str = "error"

    model_config = {"extra": "ignore"}


class SchemaViolation(BaseModel):
    """Represents a single schema validation error."""

    file_path: str = ""
    field: str = ""
    message: str = ""
    severity: str = "error"

    model_config = {"extra": "ignore"}
