"""Differencing workflow — evaluates functional equivalence between two builds."""

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
) -> DifferencingResponse:
    """Evaluate functional equivalence between two output directories.

    This is a pure evaluation — no build state is modified.

    Args:
        output_dir_a: Path to the reference output directory.
        output_dir_b: Path to the candidate output directory.
        project: The loaded project (provides project_intent and implementation).
        profile: Agent profile to use for the evaluation.

    Returns:
        A DifferencingResponse with per-dimension results and overall status.

    Raises:
        AgentError: If the response file is missing or contains invalid JSON.
    """
    # Create a temporary response file
    response_file = tempfile.NamedTemporaryFile(
        prefix="intentc-diff-",
        suffix=".json",
        delete=False,
    )
    response_file.close()
    response_file_path = response_file.name

    ctx = DifferencingContext(
        output_dir_a=output_dir_a,
        output_dir_b=output_dir_b,
        project_intent=project.project_intent,
        implementation=project.implementation,
        response_file_path=response_file_path,
    )

    agent = create_from_profile(profile)
    agent.difference(ctx)

    # Read and parse the response
    path = Path(response_file_path)
    if not path.exists():
        raise AgentError(
            f"Differencing response file not found: {response_file_path}. "
            f"The agent did not write the expected output."
        )

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AgentError(f"Failed to read differencing response file: {exc}")

    if not raw.strip():
        raise AgentError(
            f"Differencing response file is empty: {response_file_path}"
        )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AgentError(
            f"Malformed JSON in differencing response file {response_file_path}: {exc}"
        )

    try:
        return DifferencingResponse(**data)
    except Exception as exc:
        raise AgentError(
            f"Invalid differencing response structure in {response_file_path}: {exc}"
        )
