"""Differencing workflow: evaluate functional equivalence between two output directories."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from intentc.build.agents import (
    AgentError,
    AgentProfile,
    DifferencingContext,
    DifferencingResponse,
    PromptTemplates,
    create_from_profile,
    load_default_prompts,
)
from intentc.core.project import Project


def load_differencing_prompt(intent_dir: Path) -> str:
    """Load the differencing prompt template from the intent directory.

    Resolves the prompt file relative to the intent directory's
    differencing/prompts/ folder.  Falls back to the bundled package
    default when the file does not exist on disk.

    Args:
        intent_dir: Root intent directory (e.g. ``<project>/intent``).

    Returns:
        The prompt template string.
    """
    prompt_path = intent_dir / "differencing" / "prompts" / "difference.prompt"
    if prompt_path.is_file():
        return prompt_path.read_text(encoding="utf-8")

    # Fallback: bundled package default
    defaults = load_default_prompts()
    return defaults.difference


def run_differencing(
    output_dir_a: str,
    output_dir_b: str,
    project: Project,
    profile: AgentProfile,
    implementation: str | None = None,
) -> DifferencingResponse:
    """Evaluate functional equivalence between two output directories.

    Args:
        output_dir_a: Path to the reference output directory.
        output_dir_b: Path to the candidate output directory.
        project: The loaded project.
        profile: Agent profile to use.
        implementation: Implementation name to resolve (None for default).

    Returns:
        DifferencingResponse with the evaluation result.

    Raises:
        AgentError: If the response file is missing, empty, or malformed.
    """
    impl = project.resolve_implementation(implementation)

    # Load the differencing prompt from the intent directory
    prompt_text = load_differencing_prompt(project.intent_dir)
    templates = profile.prompt_templates or load_default_prompts()
    templates = templates.model_copy(update={"difference": prompt_text})
    profile = profile.model_copy(update={"prompt_templates": templates})

    # Create a temporary response file (not auto-deleted)
    fd, response_path = tempfile.mkstemp(suffix=".json", prefix="diff-response-")
    os.close(fd)

    ctx = DifferencingContext(
        output_dir_a=output_dir_a,
        output_dir_b=output_dir_b,
        project_intent=project.project_intent,
        response_file_path=response_path,
        implementation=impl,
    )

    agent = create_from_profile(profile)
    agent.difference(ctx)

    # Manually read and parse the response file
    response_file = Path(response_path)
    if not response_file.exists():
        raise AgentError(f"Differencing response file not found: {response_path}")

    content = response_file.read_text(encoding="utf-8")
    if not content.strip():
        raise AgentError(f"Differencing response file is empty: {response_path}")

    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise AgentError(
            f"Malformed JSON in differencing response file {response_path}: {exc}"
        ) from exc

    return DifferencingResponse(**data)
