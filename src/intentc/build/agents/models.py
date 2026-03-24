"""Agent data models: responses, contexts, profiles, and prompt templates."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

from intentc.core.models import (
    Implementation,
    IntentFile,
    ProjectIntent,
    ValidationFile,
)


class AgentError(Exception):
    """Raised when an agent invocation fails."""


# --- Structured Responses ---


class BuildResponse(BaseModel):
    status: str  # "success" or "failure"
    summary: str = ""
    files_created: list[str] = Field(default_factory=list)
    files_modified: list[str] = Field(default_factory=list)


class ValidationResponse(BaseModel):
    name: str
    status: str  # "pass" or "fail"
    reason: str = ""


class DimensionResult(BaseModel):
    name: str
    status: str  # "pass" or "fail"
    rationale: str = ""


class DifferencingResponse(BaseModel):
    status: str  # "equivalent" or "divergent"
    dimensions: list[DimensionResult] = Field(default_factory=list)
    summary: str = ""


# --- Prompt Templates ---


class PromptTemplates(BaseModel):
    build: str = ""
    validate_template: str = ""
    plan: str = ""
    difference: str = ""


def load_default_prompts() -> PromptTemplates:
    """Load default prompt templates from intent directory relative to CWD.

    Prompt files are resolved relative to CWD/intent/. Missing files result
    in empty template strings (no error raised).
    """
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


# --- Contexts ---


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


# --- Agent Profile ---


class AgentProfile(BaseModel):
    name: str = ""
    provider: str = ""  # "claude", "codex", or "cli"
    command: str = ""
    cli_args: list[str] = Field(default_factory=list)
    timeout: float = 3600.0
    retries: int = 3
    model_id: str | None = None
    prompt_templates: PromptTemplates | None = None
    sandbox_write_paths: list[str] = Field(default_factory=list)
    sandbox_read_paths: list[str] = Field(default_factory=list)


# Type alias for log callbacks
LogFn = Callable[[str], None]
