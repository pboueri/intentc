from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from intentc.build.agents.base import create_from_profile
from intentc.build.agents.types import (
    AgentError,
    AgentProfile,
    DifferencingContext,
    DifferencingResponse,
)
from intentc.core.project import Project


def run_differencing(
    dir_a: str,
    dir_b: str,
    project: Project,
    agent_profile: AgentProfile,
    implementation: str | None = None,
) -> DifferencingResponse:
    """Evaluate functional equivalence between two output directories.

    Returns a DifferencingResponse directly. On error (missing/malformed
    response file), raises AgentError with a descriptive message.
    """
    resolved_impl = project.resolve_implementation(implementation)

    # Create a temporary response file that is not auto-deleted
    fd, response_path = tempfile.mkstemp(suffix=".json", prefix="diff_response_")
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

    # Manually read and parse the response file (do NOT rely on the agent's return value)
    p = Path(response_path)
    if not p.is_file():
        raise AgentError(f"Differencing response file not found: {response_path}")

    content = p.read_text()
    if not content.strip():
        raise AgentError(f"Differencing response file is empty: {response_path}")

    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise AgentError(
            f"Malformed JSON in differencing response file {response_path}: {exc}"
        ) from exc

    return DifferencingResponse(**data)
