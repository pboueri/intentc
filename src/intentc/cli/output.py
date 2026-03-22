from __future__ import annotations

import sys
from pathlib import Path

from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from intentc.build.state import BuildResult, TargetStatus
from intentc.build.validations import ValidationSuiteResult


console = Console()
error_console = Console(stderr=True)


def print_error(message: str) -> None:
    """Print an error message to stderr."""
    error_console.print(f"[bold red]Error:[/bold red] {message}")


def render_build_results(results: list[BuildResult]) -> None:
    """Print build results as a table."""
    table = Table(title="Build Results")
    table.add_column("Target", style="cyan")
    table.add_column("Status")
    table.add_column("Duration")
    table.add_column("Steps")

    for result in results:
        status_style = "green" if result.status == TargetStatus.BUILT else "red"
        status_text = f"[{status_style}]{result.status.value}[/{status_style}]"
        duration = f"{result.total_duration.total_seconds():.1f}s"
        step_summaries = "; ".join(
            f"{s.phase}: {s.summary}" for s in result.steps if s.summary
        )
        table.add_row(result.target, status_text, duration, step_summaries)

    console.print(table)


def render_validation_results(results: list[ValidationSuiteResult]) -> None:
    """Print validation results."""
    table = Table(title="Validation Results")
    table.add_column("Target", style="cyan")
    table.add_column("Validation", style="white")
    table.add_column("Status")
    table.add_column("Reason")

    for suite_result in results:
        for vr in suite_result.results:
            status_style = "green" if vr.status == "pass" else "red"
            status_text = f"[{status_style}]{vr.status}[/{status_style}]"
            table.add_row(suite_result.target, vr.name, status_text, vr.reason)

    console.print(table)


def render_validation_summary(results: list[ValidationSuiteResult]) -> None:
    """Print a summary line for validation results."""
    total = sum(len(r.results) for r in results)
    passed = sum(1 for r in results for vr in r.results if vr.status == "pass")
    errors = total - passed
    console.print(f"\n{passed}/{total} passed, {errors} error(s)")


def render_status_table(
    targets: list[tuple[str, TargetStatus]],
    build_results: dict[str, BuildResult | None] | None = None,
    outdated: list[str] | None = None,
) -> None:
    """Print the status table for all tracked targets."""
    table = Table(title="Build Status")
    table.add_column("Target", style="cyan")
    table.add_column("Status")
    table.add_column("Last Build")
    table.add_column("Generation ID")
    if outdated is not None:
        table.add_column("Outdated")

    for target, status in targets:
        status_style = {
            TargetStatus.BUILT: "green",
            TargetStatus.FAILED: "red",
            TargetStatus.PENDING: "yellow",
            TargetStatus.OUTDATED: "magenta",
        }.get(status, "white")
        status_text = f"[{status_style}]{status.value}[/{status_style}]"

        timestamp = ""
        gen_id = ""
        if build_results and target in build_results and build_results[target]:
            br = build_results[target]
            timestamp = br.timestamp.strftime("%Y-%m-%d %H:%M:%S") if br.timestamp else ""
            gen_id = br.generation_id[:8] if br.generation_id else ""

        row = [target, status_text, timestamp, gen_id]
        if outdated is not None:
            row.append("yes" if target in outdated else "")
        table.add_row(*row)

    console.print(table)


def render_diff(diff_text: str) -> None:
    """Print a diff with syntax highlighting."""
    if not diff_text:
        console.print("[dim]No changes[/dim]")
        return
    syntax = Syntax(diff_text, "diff", theme="monokai")
    console.print(syntax)


def render_init_summary(files: list[str]) -> None:
    """Print a summary of files created during init."""
    console.print("[bold green]Project initialized![/bold green]")
    console.print("\nCreated files:")
    for f in files:
        console.print(f"  {f}")


def render_compare_result(status: str, summary: str, dimensions: list | None = None) -> None:
    """Print comparison results."""
    style = "green" if status == "equivalent" else "red"
    console.print(f"\nResult: [{style}]{status}[/{style}]")
    console.print(f"Summary: {summary}")
    if dimensions:
        table = Table(title="Dimensions")
        table.add_column("Dimension")
        table.add_column("Status")
        table.add_column("Rationale")
        for dim in dimensions:
            dim_style = "green" if dim.status == "pass" else "red"
            table.add_row(
                dim.name,
                f"[{dim_style}]{dim.status}[/{dim_style}]",
                dim.rationale,
            )
        console.print(table)
