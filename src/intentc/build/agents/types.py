from __future__ import annotations

from pydantic import BaseModel, Field

from intentc.core.types import (
    Implementation,
    IntentFile,
    ProjectIntent,
    ValidationFile,
)


class AgentError(Exception):
    """Raised when an agent invocation fails."""


class PromptTemplates(BaseModel):
    build: str = ""
    validate_template: str = ""
    plan: str = ""
    difference: str = ""


class AgentProfile(BaseModel):
    name: str
    provider: str  # "claude", "codex", or "cli"
    command: str = ""
    cli_args: list[str] = Field(default_factory=list)
    timeout: float = 3600.0
    retries: int = 3
    model_id: str | None = None
    prompt_templates: PromptTemplates | None = None
    sandbox_write_paths: list[str] = Field(default_factory=list)
    sandbox_read_paths: list[str] = Field(default_factory=list)


class BuildContext(BaseModel):
    intent: IntentFile
    validations: list[ValidationFile] = Field(default_factory=list)
    output_dir: str
    generation_id: str
    dependency_names: list[str] = Field(default_factory=list)
    project_intent: ProjectIntent
    implementation: Implementation | None = None
    response_file_path: str
    previous_errors: list[str] = Field(default_factory=list)


class BuildResponse(BaseModel):
    status: str  # "success" or "failure"
    summary: str
    files_created: list[str] = Field(default_factory=list)
    files_modified: list[str] = Field(default_factory=list)


class ValidationResponse(BaseModel):
    name: str
    status: str  # "pass" or "fail"
    reason: str


class DimensionResult(BaseModel):
    name: str
    status: str  # "pass" or "fail"
    rationale: str


class DifferencingResponse(BaseModel):
    status: str  # "equivalent" or "divergent"
    dimensions: list[DimensionResult] = Field(default_factory=list)
    summary: str


class DifferencingContext(BaseModel):
    output_dir_a: str
    output_dir_b: str
    project_intent: ProjectIntent
    response_file_path: str
    implementation: Implementation | None = None
