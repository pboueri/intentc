"""Rich output formatting for intentc CLI."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

if TYPE_CHECKING:
    from intentc.build.agents import DifferencingResponse
    from intentc.build.state import BuildResult, TargetStatus
    from intentc.build.validations import ValidationSuiteResult


def create_console() -> Console:
    """Create the module-level console instance."""
    return Console()


console = create_console()


def render_build_results(
    results: list[BuildResult], dry_run: bool = False
) -> None:
    """Print build results to the console."""
    if dry_run:
        console.print("[bold]Dry-run build plan:[/bold]")
        for r in results:
            console.print(f"  • {r.target}")
        return

    for r in results:
        status_style = "green" if r.status.value == "built" else "red"
        console.print(
            f"[{status_style}]{r.status.value.upper()}[/{status_style}] "
            f"{r.target} ({r.total_duration_secs:.1f}s)"
        )
        for step in r.steps:
            step_style = "green" if step.status == "success" else "red"
            console.print(
                f"    [{step_style}]{step.phase}[/{step_style}]: {step.summary}"
            )


def render_validation_result(result: ValidationSuiteResult) -> None:
    """Print a single validation suite result."""
    for vr in result.results:
        style = "green" if vr.status == "pass" else "red"
        console.print(f"  [{style}]{vr.status.upper()}[/{style}] {vr.name}: {vr.reason}")


def render_validation_results(results: list[ValidationSuiteResult]) -> None:
    """Print validation results with a summary line."""
    total = 0
    passed = 0
    errors = 0
    warnings = 0
    for suite in results:
        console.print(f"[bold]{suite.target}[/bold]")
        render_validation_result(suite)
        for vr in suite.results:
            total += 1
            if vr.status == "pass":
                passed += 1
            else:
                errors += 1
    console.print(f"\n{passed}/{total} passed, {errors} error(s), {warnings} warning(s)")


def render_status_table(
    targets: list[tuple[str, TargetStatus]],
    results: dict[str, BuildResult],
    outdated: list[str] | None = None,
) -> None:
    """Print a status table for all tracked targets."""
    table = Table(title="Build Status")
    table.add_column("Target")
    table.add_column("Status")
    table.add_column("Last Build")
    table.add_column("Generation ID")
    if outdated is not None:
        table.add_column("Outdated")

    outdated_set = set(outdated) if outdated else set()

    for target, status in targets:
        result = results.get(target)
        timestamp = str(result.timestamp) if result else ""
        gen_id = result.generation_id if result else ""
        row = [target, status.value, timestamp, gen_id]
        if outdated is not None:
            row.append("yes" if target in outdated_set else "")
        table.add_row(*row)

    console.print(table)


def render_diff(diff_text: str) -> None:
    """Print a diff with syntax highlighting."""
    syntax = Syntax(diff_text, "diff", theme="monokai")
    console.print(syntax)


def render_init_summary(files: list[str]) -> None:
    """Print a summary of files created during init."""
    console.print("[bold green]Project initialized![/bold green]")
    console.print("Created files:")
    for f in files:
        console.print(f"  • {f}")


def render_compare_result(result: DifferencingResponse) -> None:
    """Print the result of a differencing comparison."""
    style = "green" if result.status == "equivalent" else "red"
    console.print(f"[{style}]{result.status.upper()}[/{style}]: {result.summary}")
    for dim in result.dimensions:
        dim_style = "green" if dim.status == "pass" else "red"
        console.print(f"  [{dim_style}]{dim.name}[/{dim_style}]: {dim.rationale}")


def print_error(message: str) -> None:
    """Print an error message to stderr."""
    err_console = Console(stderr=True)
    err_console.print(f"[bold red]Error:[/bold red] {message}")
