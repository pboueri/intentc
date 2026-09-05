"""Differencing: agent-driven functional-equivalence evaluation of two output directories."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Callable

from intentc.build.agents import (
    AgentError,
    AgentProfile,
    DifferencingContext,
    DifferencingResponse,
    create_from_profile,
)
from intentc.core.project import Project

LogFn = Callable[[str], None]


def run_differencing(
    output_dir_a: str,
    output_dir_b: str,
    project: Project,
    profile: AgentProfile,
    implementation: str | None = None,
    log: LogFn | None = None,
) -> DifferencingResponse:
    """Ask the agent whether two builds are functionally equivalent. Pure evaluation: no state changes."""
    for label, directory in (("reference", output_dir_a), ("candidate", output_dir_b)):
        if not Path(directory).is_dir():
            raise AgentError(f"{label} directory does not exist: {directory}")

    resolved = project.resolve_implementation(implementation)

    fd, response_path = tempfile.mkstemp(prefix="intentc-diff-", suffix=".json")
    os.close(fd)
    os.remove(response_path)  # the agent creates it; a leftover empty file must not look like a response

    ctx = DifferencingContext(
        output_dir_a=str(Path(output_dir_a).resolve()),
        output_dir_b=str(Path(output_dir_b).resolve()),
        project_intent=project.project_intent,
        implementation=resolved,
        response_file_path=response_path,
    )
    agent = create_from_profile(profile, log=log)
    if log:
        log(f"Comparing {ctx.output_dir_a} (reference) with {ctx.output_dir_b} (candidate) using '{agent.get_name()}'")
    try:
        agent.difference(ctx)
        return _read_response(response_path)
    finally:
        try:
            os.remove(response_path)
        except OSError:
            pass


def _read_response(path: str) -> DifferencingResponse:
    p = Path(path)
    if not p.exists():
        raise AgentError(f"Differencing response file not found: {path}")
    text = p.read_text(encoding="utf-8")
    if not text.strip():
        raise AgentError(f"Differencing response file is empty: {path}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AgentError(f"Malformed JSON in differencing response file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise AgentError(f"Differencing response must be a JSON object: {path}")
    response = DifferencingResponse(**data)
    if any(d.status != "pass" for d in response.dimensions):
        response.status = "divergent"
    return response
