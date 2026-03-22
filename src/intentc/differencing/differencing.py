"""Differencing workflow — evaluate functional equivalence between two output directories."""

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
from intentc.core.types import Implementation


def run_differencing(
    *,
    dir_a: str,
    dir_b: str,
    project: Project,
    agent_profile: AgentProfile,
    implementation: Implementation | None = None,
) -> DifferencingResponse:
    """Evaluate functional equivalence between two output directories.

    Args:
        dir_a: Path to the reference output directory.
        dir_b: Path to the candidate output directory.
        project: The loaded project.
        agent_profile: Agent profile to use for the evaluation.
        implementation: Resolved implementation, or None to use project default.

    Returns:
        DifferencingResponse with the evaluation result.

    Raises:
        AgentError: If the agent fails or the response file is missing/malformed.
    """
    # Resolve implementation if not provided
    if implementation is None:
        implementation = project.resolve_implementation(None)

    # Create a temporary response file (not auto-deleted)
    fd, response_path = tempfile.mkstemp(suffix=".json", prefix="intentc-diff-")
    import os
    os.close(fd)

    # Build context
    ctx = DifferencingContext(
        output_dir_a=dir_a,
        output_dir_b=dir_b,
        project_intent=project.project_intent,
        implementation=implementation,
        response_file_path=response_path,
    )

    # Create agent and run differencing
    agent = create_from_profile(agent_profile)
    agent.difference(ctx)

    # Manually read and parse the response file
    p = Path(response_path)
    if not p.exists():
        raise AgentError(f"Differencing response file not found: {response_path}")

    raw = p.read_text(encoding="utf-8")
    if not raw.strip():
        raise AgentError(f"Differencing response file is empty: {response_path}")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AgentError(
            f"Malformed JSON in differencing response file {response_path}: {exc}"
        ) from exc

    return DifferencingResponse(**data)
