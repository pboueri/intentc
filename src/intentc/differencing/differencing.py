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

    # Compute sandbox paths for differencing
    sandbox_read = [
        str(Path(output_dir_a).resolve()),
        str(Path(output_dir_b).resolve()),
    ]
    sandbox_write = [str(Path(response_file_path).parent.resolve())]

    if project.intent_dir is not None:
        intent_dir = project.intent_dir
        project_ic = intent_dir / "project.ic"
        impl_ic = intent_dir / "implementation.ic"
        if project_ic.exists():
            sandbox_read.append(str(project_ic.resolve()))
        if impl_ic.exists():
            sandbox_read.append(str(impl_ic.resolve()))

    profile = profile.model_copy(update={
        "sandbox_write_paths": sandbox_write,
        "sandbox_read_paths": sandbox_read,
    })

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
