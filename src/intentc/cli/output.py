"""Output formatting for intentc CLI using Rich."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

if TYPE_CHECKING:
    from intentc.build.agents import DifferencingResponse
    from intentc.build.state import BuildResult, TargetStatus
    from intentc.build.validations import ValidationSuiteResult

console = Console()
error_console = Console(stderr=True)


def print_error(message: str) -> None:
    """Print an error message to stderr."""
    error_console.print(f"[bold red]Error:[/bold red] {message}")


def render_init_summary(files: list[str]) -> None:
    """Print a summary of files created during init."""
    console.print("[bold green]Project initialized![/bold green]")
    console.print()
    console.print("Created files:")
    for f in files:
        console.print(f"  [dim]•[/dim] {f}")


def render_build_results(results: list[BuildResult]) -> None:
    """Print build results as a table."""
    if not results:
        console.print("[dim]No targets were built.[/dim]")
        return

    table = Table(title="Build Results")
    table.add_column("Target", style="cyan")
    table.add_column("Status")
    table.add_column("Duration", justify="right")
    table.add_column("Summary")

    for r in results:
        status_style = "green" if r.status == "built" else "red"
        duration = f"{r.total_duration_secs:.1f}s" if r.total_duration_secs else "-"
        summary_parts = [s.summary for s in r.steps if s.summary] if r.steps else []
        summary = "; ".join(summary_parts) if summary_parts else "-"
        table.add_row(
            r.target,
            f"[{status_style}]{r.status}[/{status_style}]",
            duration,
            summary,
        )

    console.print(table)


def render_validation_results(results: list[ValidationSuiteResult]) -> None:
    """Print validation results."""
    total_passed = 0
    total_errors = 0
    total_warnings = 0

    for suite_result in results:
        console.print(f"\n[bold]{suite_result.target}[/bold]")
        for vr in suite_result.results:
            if vr.status == "pass":
                console.print(f"  [green]✓[/green] {vr.name}: {vr.reason}")
                total_passed += 1
            else:
                console.print(f"  [red]✗[/red] {vr.name}: {vr.reason}")
                total_errors += 1

    console.print()
    console.print(
        f"{total_passed}/{total_passed + total_errors} passed, "
        f"{total_errors} error(s), {total_warnings} warning(s)"
    )


def render_status_table(
    targets: list[tuple[str, TargetStatus]],
    build_results: dict[str, BuildResult] | None = None,
    outdated: list[str] | None = None,
) -> None:
    """Print status table for all tracked targets."""
    table = Table(title="Build Status")
    table.add_column("Target", style="cyan")
    table.add_column("Status")
    table.add_column("Last Build", justify="right")
    table.add_column("Generation ID")

    if outdated is None:
        outdated = []

    build_results = build_results or {}

    for target, status in targets:
        status_str = status.value
        if target in outdated:
            status_str += " [yellow](outdated)[/yellow]"

        result = build_results.get(target)
        timestamp = result.timestamp if result else "-"
        gen_id = result.generation_id[:8] if result and result.generation_id else "-"

        status_style = {
            "built": "green",
            "pending": "dim",
            "building": "yellow",
            "failed": "red",
            "outdated": "yellow",
        }.get(status.value, "white")

        table.add_row(
            target,
            f"[{status_style}]{status_str}[/{status_style}]",
            timestamp or "-",
            gen_id,
        )

    console.print(table)


def render_diff(diff_text: str) -> None:
    """Print a diff with syntax highlighting."""
    if not diff_text:
        console.print("[dim]No diff available.[/dim]")
        return
    syntax = Syntax(diff_text, "diff", theme="monokai")
    console.print(syntax)


def render_compare_results(response: DifferencingResponse) -> None:
    """Print differencing results: dimension table + summary."""
    table = Table(title="Differencing Results")
    table.add_column("Dimension", style="cyan")
    table.add_column("Status")
    table.add_column("Rationale")

    for dim in response.dimensions:
        status_style = "green" if dim.status == "pass" else "red"
        table.add_row(
            dim.name,
            f"[{status_style}]{dim.status}[/{status_style}]",
            dim.rationale,
        )

    console.print(table)
    console.print()

    status_style = "green" if response.status == "equivalent" else "red"
    console.print(
        f"[bold]Result:[/bold] [{status_style}]{response.status}[/{status_style}]"
    )
    console.print(f"[bold]Summary:[/bold] {response.summary}")
