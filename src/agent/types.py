from __future__ import annotations

from pydantic import BaseModel, Field


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


class IntentFileRef(BaseModel):
    """Lightweight intent file reference for standalone use."""
    name: str
    depends_on: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    body: str = ""


class ProjectIntentRef(BaseModel):
    """Lightweight project intent reference for standalone use."""
    name: str
    tags: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    body: str = ""


class ImplementationRef(BaseModel):
    """Lightweight implementation reference for standalone use."""
    name: str
    tags: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    body: str = ""


class ValidationRef(BaseModel):
    """Lightweight validation reference for standalone use."""
    name: str
    type: str = "agent_validation"
    severity: str = "error"
    args: dict = Field(default_factory=dict)


class ValidationFileRef(BaseModel):
    """Lightweight validation file reference for standalone use."""
    target: str
    agent_profile: str | None = None
    validations: list[ValidationRef] = Field(default_factory=list)


class BuildContext(BaseModel):
    intent: IntentFileRef
    validations: list[ValidationFileRef] = Field(default_factory=list)
    output_dir: str
    generation_id: str
    dependency_names: list[str] = Field(default_factory=list)
    project_intent: ProjectIntentRef
    implementation: ImplementationRef | None = None
    response_file_path: str


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
    project_intent: ProjectIntentRef
    response_file_path: str
    implementation: ImplementationRef | None = None
