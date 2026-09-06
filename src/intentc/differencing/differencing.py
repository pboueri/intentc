"""The differencing workflow: evaluate functional equivalence between two output directories."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from intentc.build.agents import (
    AgentError,
    AgentProfile,
    DifferencingContext,
    DifferencingResponse,
    create_from_profile,
)
from intentc.core import Project


def run_differencing(
    output_dir_a: str,
    output_dir_b: str,
    project: Project,
    profile: AgentProfile,
    implementation: Optional[str] = None,
) -> DifferencingResponse:
    """Evaluate whether `output_dir_a` and `output_dir_b` are functionally equivalent.

    Pure evaluation: no build state is read or modified. Raises `AgentError` if the
    agent's response file is missing, empty, or malformed.
    """
    resolved_implementation = project.resolve_implementation(implementation)

    response_fd, response_file_path = tempfile.mkstemp(
        prefix="intentc-difference-", suffix=".json"
    )
    os.close(response_fd)
    Path(response_file_path).unlink(missing_ok=True)

    ctx = DifferencingContext(
        output_dir_a=output_dir_a,
        output_dir_b=output_dir_b,
        project_intent=project.project_intent,
        implementation=resolved_implementation,
        response_file_path=response_file_path,
    )

    agent = create_from_profile(profile)
    agent.difference(ctx)

    return _read_differencing_response(response_file_path)


def _read_differencing_response(response_file_path: str) -> DifferencingResponse:
    path = Path(response_file_path)
    if not path.is_file():
        raise AgentError(f"Differencing response file not found: {response_file_path}")

    raw_text = path.read_text(encoding="utf-8")
    if not raw_text.strip():
        raise AgentError(f"Differencing response file is empty: {response_file_path}")

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise AgentError(
            f"Malformed JSON in differencing response file {response_file_path}: {exc}"
        ) from exc

    return DifferencingResponse(**data)
