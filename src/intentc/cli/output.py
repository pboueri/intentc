"""Terminal rendering for every CLI command: tables, plans, diffs, and the
consistent color coding used across all of them (built/pass = green,
failed/fail = red, outdated/warning = yellow, pending = dim).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional, Sequence, Union

from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

import re

from intentc.build.agents import DifferencingResponse, RefineBakeResponse
from intentc.build.storage import BuildResult, RefinementSession
from intentc.build.validations import ValidationSuiteResult
from intentc.core import Project, ProjectIssue

LogFn = Callable[[str], None]

_STATUS_STYLES = {
    "built": "green",
    "pass": "green",
    "success": "green",
    "equivalent": "green",
    "baked": "green",
    "failed": "red",
    "fail": "red",
    "divergent": "red",
    "outdated": "yellow",
    "warning": "yellow",
    "recording": "yellow",
    "baking": "yellow",
    "refining": "yellow",
    "pending": "dim",
    "abandoned": "dim",
}

_JOURNAL_ENTRY_RE = re.compile(r"^## ", re.MULTILINE)


def _status_value(status: Any) -> str:
    return status.value if hasattr(status, "value") else str(status)


def _style_for(status: Any) -> str:
    value = _status_value(status).lower()
    if value.startswith("refining"):
        return "yellow"
    return _STATUS_STYLES.get(value, "")


def _styled(status: Any) -> str:
    value = _status_value(status)
    style = _style_for(value)
    return f"[{style}]{value}[/{style}]" if style else value


def print_error(message: str, console: Optional[Console] = None) -> None:
    """Print an error message to stderr in red. Callers compose the exact text."""
    (console or Console(stderr=True, soft_wrap=True)).print(f"[red]{message}[/red]")


def print_warning(message: str, console: Optional[Console] = None) -> None:
    """Print a warning message to stderr in yellow."""
    (console or Console(stderr=True, soft_wrap=True)).print(f"[yellow]{message}[/yellow]")


def timestamped_log(console: Optional[Console] = None) -> LogFn:
    """Build a single `HH:MM:SS`-prefixed log callback threaded through the
    builder, validation suite, and agents for one command invocation."""
    out = console or Console()

    def _log(message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        out.print(f"[dim]{timestamp}[/dim] {message}")

    return _log


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


def render_build_plan(results: Sequence[BuildResult], console: Optional[Console] = None) -> None:
    out = console or Console()
    out.print(f"Build plan (dry run) — {len(results)} target(s):")
    for index, result in enumerate(results, start=1):
        out.print(f"{index}. {result.target}  ({_status_value(result.status)})")


def render_build_results(results: Sequence[BuildResult], console: Optional[Console] = None) -> None:
    out = console or Console()
    table = Table(title="Build Results")
    table.add_column("Target")
    table.add_column("Status")
    table.add_column("Attempts", justify="right")
    table.add_column("Duration", justify="right")
    table.add_column("Summary")

    built = 0
    failed = 0
    total_duration = 0.0
    for result in results:
        status = _status_value(result.status)
        if status == "built":
            built += 1
        elif status == "failed":
            failed += 1
        total_duration += result.total_duration_secs
        summary = result.steps[-1].summary if result.steps else ""
        table.add_row(
            result.target,
            _styled(result.status),
            str(result.attempts),
            f"{result.total_duration_secs:.1f}s",
            summary,
        )
    out.print(table)
    out.print(f"{built} built, {failed} failed in {total_duration:.1f}s")


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def render_validation_results(result: ValidationSuiteResult, console: Optional[Console] = None) -> None:
    out = console or Console()
    for entry in result.results:
        is_warning = entry.severity == "warning"
        if entry.status == "pass":
            icon, style = "✓", "green"
        elif is_warning:
            icon, style = "!", "yellow"
        else:
            icon, style = "✗", "red"

        label = ""
        if entry.status != "pass":
            label = "  [yellow]warning[/yellow]" if is_warning else "  [red]error[/red]"
        out.print(f"[{style}]{icon}[/{style}] {entry.name}  ({entry.type})  {entry.duration_secs:.1f}s{label}")
        if entry.status != "pass" and entry.reason:
            for line in entry.reason.splitlines():
                out.print(f"    {line}")
    out.print(result.summary)


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


def render_check_results(
    issues: Sequence[ProjectIssue], total_features: int, console: Optional[Console] = None
) -> None:
    out = console or Console()
    errors = [i for i in issues if i.level == "error"]
    warnings = [i for i in issues if i.level == "warning"]
    for issue in errors:
        out.print(f"[red]{issue}[/red]")
    for issue in warnings:
        out.print(f"[yellow]{issue}[/yellow]")
    out.print(f"{len(errors)} error(s), {len(warnings)} warning(s) across {total_features} feature(s)")


def render_dag(project: Project, console: Optional[Console] = None) -> None:
    out = console or Console()
    out.print("Dependency graph:")
    for feature in project.topological_order():
        deps = project.features[feature].depends_on
        deps_str = ", ".join(deps) if deps else "(none)"
        out.print(f"  {feature} -> {deps_str}")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

StatusRow = tuple[str, str, Sequence[str], int, str, int, str]


def render_status_table(rows: Sequence[StatusRow], console: Optional[Console] = None) -> None:
    out = console or Console()
    table = Table(title="Status")
    table.add_column("Target")
    table.add_column("Status")
    table.add_column("Depends On")
    table.add_column("Validations", justify="right")
    table.add_column("Last Build")
    table.add_column("Attempts", justify="right")
    table.add_column("Generation")
    for target, status, deps, validations, last_build, attempts, generation_id in rows:
        table.add_row(
            target,
            _styled(status),
            ", ".join(deps) if deps else "-",
            str(validations),
            last_build or "-",
            str(attempts),
            generation_id[:8] if generation_id else "-",
        )
    out.print(table)


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


def render_diff(
    target: str,
    commit_id: str,
    timestamp: str,
    files_created: Sequence[str],
    files_modified: Sequence[str],
    diff_text: str,
    stat_only: bool = False,
    console: Optional[Console] = None,
) -> None:
    out = console or Console()
    out.print(
        f"{target}  commit {commit_id}  {timestamp}  "
        f"({len(files_created)} created, {len(files_modified)} modified)"
    )
    if files_created:
        out.print("Created:")
        for path in files_created:
            out.print(f"  + {path}")
    if files_modified:
        out.print("Modified:")
        for path in files_modified:
            out.print(f"  ~ {path}")
    if not stat_only:
        out.print(Syntax(diff_text, "diff"))


# ---------------------------------------------------------------------------
# log
# ---------------------------------------------------------------------------


def render_build_log(
    target: str,
    history: Sequence[BuildResult],
    validation_results: Sequence[dict[str, Any]],
    console: Optional[Console] = None,
) -> None:
    out = console or Console()
    table = Table(title=f"Build history: {target}")
    table.add_column("Generation")
    table.add_column("Status")
    table.add_column("Attempts", justify="right")
    table.add_column("Duration", justify="right")
    table.add_column("Timestamp")
    table.add_column("Commit")
    for result in history:
        table.add_row(
            (result.generation_id or "-")[:8],
            _styled(result.status),
            str(result.attempts),
            f"{result.total_duration_secs:.1f}s",
            result.timestamp or "-",
            (result.commit_id or "-")[:8] if result.commit_id else "-",
        )
    out.print(table)

    if history:
        latest = history[0]
        out.print(f"Steps for generation {(latest.generation_id or '-')[:8]}:")
        for step in latest.steps:
            out.print(f"  {_styled(step.status)}  {step.phase}  {step.duration_secs:.1f}s  {step.summary}")

    if validation_results:
        out.print("Validation results:")
        for entry in validation_results:
            out.print(
                f"  {_styled(entry.get('status', ''))}  {entry.get('name')}  {entry.get('reason', '')}"
            )


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def render_init_summary(paths: Sequence[Union[str, Path]], console: Optional[Console] = None) -> None:
    out = console or Console()
    out.print("Created:")
    for path in paths:
        out.print(f"  {path}")


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


def render_compare_results(response: DifferencingResponse, console: Optional[Console] = None) -> None:
    out = console or Console()
    table = Table(title="Compare")
    table.add_column("Dimension")
    table.add_column("Status")
    table.add_column("Rationale")
    for dimension in response.dimensions:
        table.add_row(dimension.name, _styled(dimension.status), dimension.rationale)
    out.print(table)
    out.print(f"{_styled(response.status)}: {response.summary}")


# ---------------------------------------------------------------------------
# refine
# ---------------------------------------------------------------------------


def render_refine_summary(
    session: RefinementSession,
    response: Optional[RefineBakeResponse],
    console: Optional[Console] = None,
) -> None:
    out = console or Console()
    out.print(f"Session {session.session_id[:8]}: {_styled(session.status)}")
    if response is None:
        return
    out.print(response.summary)
    if response.generalizations:
        out.print("Generalizations:")
        for generalization in response.generalizations:
            out.print(f"  - {generalization}")
    if response.open_questions:
        for question in response.open_questions:
            print_warning(f"  ? {question}", console=out)


def render_refinement_log(sessions: Sequence[RefinementSession], console: Optional[Console] = None) -> None:
    out = console or Console()
    table = Table(title="Refinements")
    table.add_column("Session")
    table.add_column("Status")
    table.add_column("Started")
    table.add_column("Attempts", justify="right")
    table.add_column("Bake Generation")
    table.add_column("Journal Entries", justify="right")
    for session in sessions:
        entry_count = len(_JOURNAL_ENTRY_RE.findall(session.journal))
        table.add_row(
            session.session_id[:8],
            _styled(session.status),
            session.started_at or "-",
            str(session.bake_attempts),
            (session.bake_generation_id or "-")[:8] if session.bake_generation_id else "-",
            str(entry_count),
        )
    out.print(table)
