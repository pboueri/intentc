"""Differencing workflow: evaluate functional equivalence between two output directories."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from intentc.build.agents import (
    AgentError,
    AgentProfile,
    DifferencingContext,
    DifferencingResponse,
    create_from_profile,
)
from intentc.core.project import Project


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

    # Create a temporary response file (not auto-deleted)
    fd, response_path = tempfile.mkstemp(suffix=".json", prefix="diff-response-")
    import os
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
