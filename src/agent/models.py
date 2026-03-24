"""Agent data models: responses, contexts, profiles, and prompt templates (standalone copy)."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field


# --- Minimal core types needed by standalone agent ---
# These mirror intentc.core.models so the standalone package has no dependency
# on the main intentc package.


class ValidationType(str, enum.Enum):
    AGENT_VALIDATION = "agent_validation"
    LLM_JUDGE = "llm_judge"
    FILE_CHECK = "file_check"
    FOLDER_CHECK = "folder_check"
    COMMAND_CHECK = "command_check"


class Severity(str, enum.Enum):
    ERROR = "error"
    WARNING = "warning"


class IntentFile(BaseModel):
    name: str
    depends_on: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    body: str = ""
    file_references: list[str] = Field(default_factory=list)
    source_path: Path | None = None


class ProjectIntent(BaseModel):
    name: str
    tags: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    body: str = ""
    file_references: list[str] = Field(default_factory=list)
    source_path: Path | None = None


class Implementation(BaseModel):
    name: str
    depends_on: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    body: str = ""
    file_references: list[str] = Field(default_factory=list)
    source_path: Path | None = None


class Validation(BaseModel):
    name: str
    type: ValidationType = ValidationType.AGENT_VALIDATION
    severity: Severity = Severity.ERROR
    args: dict[str, Any] = Field(default_factory=dict)


class ValidationFile(BaseModel):
    target: str = ""
    agent_profile: str | None = None
    validations: list[Validation] = Field(default_factory=list)
    source_path: Path | None = None


# --- Agent-specific types ---


class AgentError(Exception):
    """Raised when an agent invocation fails."""


class BuildResponse(BaseModel):
    status: str
    summary: str = ""
    files_created: list[str] = Field(default_factory=list)
    files_modified: list[str] = Field(default_factory=list)


class ValidationResponse(BaseModel):
    name: str
    status: str
    reason: str = ""


class DimensionResult(BaseModel):
    name: str
    status: str
    rationale: str = ""


class DifferencingResponse(BaseModel):
    status: str
    dimensions: list[DimensionResult] = Field(default_factory=list)
    summary: str = ""


class PromptTemplates(BaseModel):
    build: str = ""
    validate_template: str = ""
    plan: str = ""
    difference: str = ""


def load_default_prompts() -> PromptTemplates:
    """Load default prompt templates from intent directory relative to CWD."""
    base = Path.cwd() / "intent"
    mapping = {
        "build": base / "build" / "agents" / "prompts" / "build.prompt",
        "validate_template": base / "build" / "agents" / "prompts" / "validate.prompt",
        "plan": base / "build" / "agents" / "prompts" / "plan.prompt",
        "difference": base / "differencing" / "prompts" / "difference.prompt",
    }
    values: dict[str, str] = {}
    for field_name, path in mapping.items():
        if path.exists():
            values[field_name] = path.read_text()
        else:
            values[field_name] = ""
    return PromptTemplates(**values)


def render_prompt(
    template: str,
    ctx: BuildContext,
    validation: ValidationFile | None = None,
    single_validation_text: str = "",
) -> str:
    """Render a prompt template with BuildContext variables."""
    validations_text = "\n\n".join(
        v.model_dump_json(indent=2) if hasattr(v, "model_dump_json") else str(v)
        for v in ctx.validations
    ) if ctx.validations else ""

    previous_errors_text = ""
    if ctx.previous_errors:
        bullets = "\n".join(f"- {e}" for e in ctx.previous_errors)
        previous_errors_text = f"\n### Previous Errors\nThe following errors occurred in prior attempts. Fix them:\n{bullets}\n"

    return template.format(
        project=ctx.project_intent.body if ctx.project_intent else "",
        implementation=ctx.implementation.body if ctx.implementation else "",
        feature=ctx.intent.body if ctx.intent else "",
        validations=validations_text,
        validation=single_validation_text,
        output_dir=ctx.output_dir,
        response_file=ctx.response_file_path,
        previous_errors=previous_errors_text,
    )


def render_differencing_prompt(
    template: str,
    ctx: DifferencingContext,
) -> str:
    """Render a differencing prompt template with DifferencingContext variables."""
    return template.format(
        project=ctx.project_intent.body if ctx.project_intent else "",
        implementation=ctx.implementation.body if ctx.implementation else "",
        output_dir_a=ctx.output_dir_a,
        output_dir_b=ctx.output_dir_b,
        response_file=ctx.response_file_path,
    )


class BuildContext(BaseModel):
    intent: IntentFile
    validations: list[ValidationFile] = Field(default_factory=list)
    output_dir: str = ""
    generation_id: str = ""
    dependency_names: list[str] = Field(default_factory=list)
    project_intent: ProjectIntent | None = None
    implementation: Implementation | None = None
    response_file_path: str = ""
    previous_errors: list[str] = Field(default_factory=list)


class DifferencingContext(BaseModel):
    output_dir_a: str = ""
    output_dir_b: str = ""
    project_intent: ProjectIntent | None = None
    response_file_path: str = ""
    implementation: Implementation | None = None


class AgentProfile(BaseModel):
    name: str = ""
    provider: str = ""
    command: str = ""
    cli_args: list[str] = Field(default_factory=list)
    timeout: float = 3600.0
    retries: int = 3
    model_id: str | None = None
    prompt_templates: PromptTemplates | None = None
    sandbox_write_paths: list[str] = Field(default_factory=list)
    sandbox_read_paths: list[str] = Field(default_factory=list)


LogFn = Callable[[str], None]
