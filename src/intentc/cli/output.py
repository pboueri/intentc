"""Rich output helpers for CLI commands."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from intentc.build.agents import DifferencingResponse, ValidationResponse
from intentc.build.state import BuildResult, TargetStatus
from intentc.build.validations import ValidationSuiteResult

console = Console()
err_console = Console(stderr=True)


def render_build_results(results: list[BuildResult], dry_run: bool = False) -> None:
    """Print build results as a Rich table."""
    if not results:
        console.print("[dim]Nothing to build.[/dim]")
        return

    if dry_run:
        console.print("[bold]Dry run — targets that would be built:[/bold]")

    table = Table(title="Build Results")
    table.add_column("Target", style="cyan")
    table.add_column("Status")
    table.add_column("Duration", justify="right")
    table.add_column("Steps")

    for r in results:
        status_style = "green" if r.status == TargetStatus.BUILT else "red"
        if dry_run:
            status_style = "yellow"
        duration = f"{r.total_duration.total_seconds():.1f}s" if not dry_run else "-"
        step_summary = "; ".join(
            f"{s.phase}: {s.summary}" for s in r.steps
        ) if r.steps else ("-" if not dry_run else r.status.value)
        table.add_row(
            r.target,
            f"[{status_style}]{r.status.value}[/{status_style}]",
            duration,
            step_summary,
        )

    console.print(table)


def render_validation_result(result: ValidationSuiteResult) -> None:
    """Print a single validation suite result."""
    table = Table(title=f"Validations: {result.target}")
    table.add_column("Name", style="cyan")
    table.add_column("Status")
    table.add_column("Reason")

    for r in result.results:
        style = "green" if r.status == "pass" else "red"
        table.add_row(r.name, f"[{style}]{r.status}[/{style}]", r.reason)

    console.print(table)
    console.print(f"  {result.summary}")


def render_validation_results(results: list[ValidationSuiteResult]) -> None:
    """Print multiple validation suite results."""
    for result in results:
        render_validation_result(result)


def render_status_table(
    targets: list[tuple[str, TargetStatus]],
    results: dict[str, BuildResult],
    outdated: list[str] | None = None,
) -> None:
    """Print a status table for all tracked targets."""
    if not targets:
        console.print("[dim]No tracked targets.[/dim]")
        return

    table = Table(title="Build Status")
    table.add_column("Target", style="cyan")
    table.add_column("Status")
    table.add_column("Last Build", justify="right")
    table.add_column("Generation ID")

    for name, status in targets:
        status_style = {
            TargetStatus.BUILT: "green",
            TargetStatus.PENDING: "yellow",
            TargetStatus.FAILED: "red",
            TargetStatus.OUTDATED: "yellow",
        }.get(status, "white")

        annotation = ""
        if outdated and name in outdated:
            annotation = " [yellow](stale)[/yellow]"

        br = results.get(name)
        timestamp = br.timestamp.strftime("%Y-%m-%d %H:%M:%S") if br else "-"
        gen_id = br.generation_id[:8] if br else "-"

        table.add_row(
            name,
            f"[{status_style}]{status.value}[/{status_style}]{annotation}",
            timestamp,
            gen_id,
        )

    console.print(table)


def render_diff(diff_text: str) -> None:
    """Print a syntax-highlighted diff."""
    if not diff_text.strip():
        console.print("[dim]No changes.[/dim]")
        return
    syntax = Syntax(diff_text, "diff", theme="monokai")
    console.print(syntax)


def render_init_summary(files: list[str]) -> None:
    """Print a summary of files created during init."""
    console.print(Panel(
        "\n".join(f"  [green]+[/green] {f}" for f in files),
        title="[bold]Project initialized[/bold]",
    ))


def render_compare_result(result: DifferencingResponse) -> None:
    """Print a differencing result as a dimensions table plus summary."""
    status_style = "green" if result.status == "equivalent" else "red"
    table = Table(title=f"Compare: [{status_style}]{result.status}[/{status_style}]")
    table.add_column("Dimension", style="cyan")
    table.add_column("Status")
    table.add_column("Rationale")

    for dim in result.dimensions:
        style = "green" if dim.status == "pass" else "red"
        table.add_row(dim.name, f"[{style}]{dim.status}[/{style}]", dim.rationale)

    console.print(table)
    console.print(Panel(result.summary, title="Summary"))


def print_error(message: str) -> None:
    """Print an error message to stderr."""
    err_console.print(f"[bold red]Error:[/bold red] {message}")
