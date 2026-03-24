"""Differencing workflow — evaluate functional equivalence between two output directories."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from intentc.build.agents.factory import create_from_profile
from intentc.build.agents.models import (
    AgentError,
    AgentProfile,
    DifferencingContext,
    DifferencingResponse,
)
from intentc.core.models import Implementation
from intentc.core.project import Project


def run_differencing(
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
        agent_profile: Agent profile to use for evaluation.
        implementation: Optional implementation override. If None, the
            project's default implementation is resolved.

    Returns:
        A DifferencingResponse with the evaluation results.

    Raises:
        AgentError: If the agent fails or the response file is missing/malformed.
    """
    resolved_impl = implementation
    if resolved_impl is None:
        try:
            resolved_impl = project.resolve_implementation()
        except (ValueError, KeyError):
            resolved_impl = None

    # Create a temporary response file (not auto-deleted)
    fd, response_path = tempfile.mkstemp(suffix=".json", prefix="intentc_diff_")
    import os
    os.close(fd)

    ctx = DifferencingContext(
        output_dir_a=dir_a,
        output_dir_b=dir_b,
        project_intent=project.project_intent,
        implementation=resolved_impl,
        response_file_path=response_path,
    )

    agent = create_from_profile(agent_profile)
    agent.difference(ctx)

    # Manually read and parse the response file
    resp_file = Path(response_path)
    if not resp_file.exists():
        raise AgentError(f"Differencing response file not found: {response_path}")

    raw = resp_file.read_text()
    if not raw.strip():
        raise AgentError(f"Differencing response file is empty: {response_path}")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AgentError(
            f"Malformed JSON in differencing response file: {response_path}"
        ) from exc

    return DifferencingResponse(**data)
