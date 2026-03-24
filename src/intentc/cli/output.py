"""Output formatting for intentc CLI using Rich."""

from __future__ import annotations

import sys
from pathlib import Path

from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from intentc.build.storage.backend import BuildResult, TargetStatus
from intentc.build.validations import ValidationSuiteResult
from intentc.build.agents.models import DifferencingResponse

console = Console()
error_console = Console(stderr=True)


def print_error(message: str) -> None:
    """Print an error message to stderr."""
    error_console.print(f"[red]Error:[/red] {message}")


def render_build_results(results: list[BuildResult]) -> None:
    """Render build results as a Rich table."""
    if not results:
        console.print("[dim]No targets were built.[/dim]")
        return

    table = Table(title="Build Results")
    table.add_column("Target", style="cyan")
    table.add_column("Status")
    table.add_column("Duration", justify="right")
    table.add_column("Summary")

    for r in results:
        status_style = "green" if r.status == "success" else "red"
        status_text = f"[{status_style}]{r.status}[/{status_style}]"
        duration = f"{r.total_duration_secs:.1f}s"
        summaries = [s.summary for s in r.steps if s.summary]
        summary = "; ".join(summaries) if summaries else ""
        table.add_row(r.target, status_text, duration, summary)

    console.print(table)


def render_validation_results(results: list[ValidationSuiteResult]) -> None:
    """Render validation results."""
    passed_count = 0
    error_count = 0
    warning_count = 0
    total = 0

    for suite in results:
        for vr in suite.results:
            total += 1
            if vr.status == "pass":
                passed_count += 1
            else:
                error_count += 1
            status_style = "green" if vr.status == "pass" else "red"
            console.print(
                f"  [{status_style}]{vr.status}[/{status_style}] {vr.name}: {vr.reason}"
            )

    console.print(
        f"\n{passed_count}/{total} passed, {error_count} error(s), {warning_count} warning(s)"
    )


def render_status_table(
    targets: list[tuple[str, TargetStatus]],
    outdated: list[str] | None = None,
) -> None:
    """Render a status table for all tracked targets."""
    if not targets:
        console.print("[dim]No tracked targets.[/dim]")
        return

    table = Table(title="Build Status")
    table.add_column("Target", style="cyan")
    table.add_column("Status")
    if outdated is not None:
        table.add_column("Outdated")

    outdated_set = set(outdated) if outdated else set()

    for target, status in targets:
        style = {
            TargetStatus.BUILT: "green",
            TargetStatus.FAILED: "red",
            TargetStatus.BUILDING: "yellow",
            TargetStatus.OUTDATED: "yellow",
            TargetStatus.PENDING: "dim",
        }.get(status, "white")
        row = [target, f"[{style}]{status.value}[/{style}]"]
        if outdated is not None:
            row.append("[yellow]yes[/yellow]" if target in outdated_set else "")
        table.add_row(*row)

    console.print(table)


def render_diff(diff_text: str) -> None:
    """Render a diff with syntax highlighting."""
    if not diff_text:
        console.print("[dim]No diff available.[/dim]")
        return
    syntax = Syntax(diff_text, "diff", theme="monokai")
    console.print(syntax)


def render_init_summary(files: list[str | Path]) -> None:
    """Print a summary of files created during init."""
    console.print("[green]Project initialized![/green]")
    console.print("Created files:")
    for f in files:
        console.print(f"  {f}")


def render_compare_results(response: DifferencingResponse) -> None:
    """Render comparison/differencing results."""
    status_style = "green" if response.status == "equivalent" else "red"
    console.print(
        f"\nResult: [{status_style}]{response.status}[/{status_style}]"
    )
    if response.dimensions:
        table = Table(title="Dimensions")
        table.add_column("Dimension", style="cyan")
        table.add_column("Status")
        table.add_column("Rationale")
        for dim in response.dimensions:
            dim_style = "green" if dim.status == "pass" else "red"
            table.add_row(
                dim.name,
                f"[{dim_style}]{dim.status}[/{dim_style}]",
                dim.rationale,
            )
        console.print(table)
    if response.summary:
        console.print(f"\n{response.summary}")
