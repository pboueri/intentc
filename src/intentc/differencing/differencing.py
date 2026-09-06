"""The differencing workflow: evaluate functional equivalence between two output directories."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional

from intentc.build.agents import (
    AgentProfile,
    DifferencingContext,
    DifferencingResponse,
    LogFn,
    create_from_profile,
)
from intentc.core import Project


def run_differencing(
    output_dir_a: str,
    output_dir_b: str,
    project: Project,
    profile: AgentProfile,
    implementation: Optional[str] = None,
    log: Optional[LogFn] = None,
) -> DifferencingResponse:
    """Evaluate whether `output_dir_a` and `output_dir_b` are functionally equivalent.

    Pure evaluation: no build state is read or modified. The agent owns the
    response-file lifecycle (read, validate, delete) and returns the parsed
    `DifferencingResponse` directly; a missing, empty, or malformed response
    file surfaces as the `AgentError` the agent raises, propagated unchanged.
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

    agent = create_from_profile(profile, log=log)
    return agent.difference(ctx)
