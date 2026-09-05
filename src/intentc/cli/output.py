"""Terminal rendering for the intentc CLI (Rich)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

if TYPE_CHECKING:  # pragma: no cover
    from intentc.build.agents import DifferencingResponse
    from intentc.build.state import BuildResult, TargetStatus
    from intentc.build.validations import ValidationSuiteResult
    from intentc.core import Project, ProjectIssue

console = Console(highlight=False)
error_console = Console(stderr=True, highlight=False)

STATUS_STYLE = {
    "built": "green",
    "pass": "green",
    "equivalent": "green",
    "success": "green",
    "failed": "red",
    "fail": "red",
    "divergent": "red",
    "failure": "red",
    "outdated": "yellow",
    "warning": "yellow",
    "pending": "dim",
}


def styled(word: str) -> str:
    style = STATUS_STYLE.get(word, "white")
    return f"[{style}]{word}[/{style}]"


def print_error(message: str) -> None:
    error_console.print(f"[bold red]Error:[/bold red] {message}")


def print_warning(message: str) -> None:
    error_console.print(f"[bold yellow]Warning:[/bold yellow] {message}")


def print_hint(message: str) -> None:
    console.print(f"[dim]{message}[/dim]")


# ---------------------------------------------------------------------------
# init / check
# ---------------------------------------------------------------------------


def render_init_summary(files: list[str]) -> None:
    console.print("[bold green]Project initialized.[/bold green]")
    console.print("Created:")
    for f in files:
        console.print(f"  [dim]•[/dim] {f}")
    console.print()
    console.print("Next steps:")
    console.print("  1. Describe the project in intent/project.ic and the stack in intent/implementations/default.ic")
    console.print("  2. Write your first feature and its validations")
    console.print("  3. [bold]intentc check[/bold] to lint, then [bold]intentc build[/bold] to generate")


def render_check_results(issues: list[ProjectIssue], feature_count: int) -> None:
    errors = [i for i in issues if i.level == "error"]
    warnings = [i for i in issues if i.level != "error"]
    for issue in errors:
        location = f"{issue.path}: " if issue.path else ""
        error_console.print(f"[red]error[/red]   {location}{issue.message}")
    for issue in warnings:
        location = f"{issue.path}: " if issue.path else ""
        console.print(f"[yellow]warning[/yellow] {location}{issue.message}")
    if issues:
        console.print()
    summary = f"{len(errors)} error(s), {len(warnings)} warning(s) across {feature_count} feature(s)"
    if errors:
        console.print(f"[bold red]✗ {summary}[/bold red]")
    elif warnings:
        console.print(f"[bold yellow]! {summary}[/bold yellow]")
    else:
        console.print(f"[bold green]✓ {summary}[/bold green]")


def render_dag(project: Project) -> None:
    console.print("[bold]Dependency graph[/bold] (dependency-first order):")
    for fp in project.topological_order():
        deps = project.features[fp].depends_on
        arrow = f" [dim]← {', '.join(deps)}[/dim]" if deps else ""
        console.print(f"  {fp}{arrow}")


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


def render_build_plan(results: list[BuildResult]) -> None:
    console.print(f"[bold]Build plan (dry run) — {len(results)} target(s):[/bold]")
    for index, r in enumerate(results, start=1):
        console.print(f"  {index}. {r.target}  ({styled(r.status.value)})")


def _last_summary(result: BuildResult) -> str:
    for step in reversed(result.steps):
        if step.summary:
            return step.summary.splitlines()[0]
    return "-"


def render_build_results(results: list[BuildResult]) -> None:
    table = Table(title="Build Results", title_justify="left")
    table.add_column("Target", style="cyan")
    table.add_column("Status")
    table.add_column("Attempts", justify="right")
    table.add_column("Duration", justify="right")
    table.add_column("Last step")
    total = 0.0
    for r in results:
        total += r.total_duration_secs
        table.add_row(r.target, styled(r.status.value), str(r.attempts), f"{r.total_duration_secs:.1f}s", _last_summary(r))
    console.print(table)
    built = sum(1 for r in results if r.status.value == "built")
    failed = sum(1 for r in results if r.status.value == "failed")
    console.print(f"{built} built, {failed} failed in {total:.1f}s")


def render_build_failure(result: BuildResult) -> None:
    failing = next((s for s in reversed(result.steps) if s.status != "success"), None)
    if failing is None:
        return
    console.print()
    console.print(f"[bold red]'{result.target}' failed in step '{failing.phase}':[/bold red]")
    for line in failing.summary.splitlines():
        console.print(f"  {line}")
    print_hint(f"Fix the intent or the output, then run: intentc build {result.target}")
    print_hint(f"Full history: intentc log {result.target}")


def render_next_targets(next_targets: list[str], nothing_built: bool = False) -> None:
    if nothing_built:
        starts = f" (starts with: {', '.join(next_targets)})" if next_targets else ""
        console.print(f"Nothing built yet — run: [bold]intentc build[/bold]{starts}")
    elif next_targets:
        console.print(f"Next you can build: [bold]{', '.join(next_targets)}[/bold]")
    else:
        console.print("[green]All targets built.[/green]")


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def render_validation_results(results: list[ValidationSuiteResult]) -> None:
    passed = errors = warnings = 0
    for suite in results:
        console.print(f"[bold]{suite.target}[/bold] [dim]({suite.summary})[/dim]")
        for r in suite.results:
            duration = f"{r.duration_secs:.1f}s"
            first_line = r.reason.splitlines()[0] if r.reason else ""
            if r.status == "pass":
                passed += 1
                console.print(f"  [green]✓[/green] {r.name} [dim]({r.type}, {duration})[/dim] {first_line}")
            elif r.severity == "warning":
                warnings += 1
                console.print(f"  [yellow]![/yellow] {r.name} [yellow]warning[/yellow] [dim]({r.type}, {duration})[/dim] {first_line}")
            else:
                errors += 1
                console.print(f"  [red]✗[/red] {r.name} [dim]({r.type}, {duration})[/dim] {first_line}")
                for line in r.reason.splitlines()[1:8]:
                    console.print(f"      [dim]{line}[/dim]")
    console.print()
    total = passed + errors + warnings
    style = "red" if errors else ("yellow" if warnings else "green")
    console.print(f"[{style}]{passed}/{total} passed, {errors} error(s), {warnings} warning(s)[/{style}]")


# ---------------------------------------------------------------------------
# status / diff / log
# ---------------------------------------------------------------------------


def render_status_table(rows: list[dict[str, Any]], output_dir: str) -> None:
    table = Table(title=f"Build Status ({output_dir})", title_justify="left")
    table.add_column("Target", style="cyan")
    table.add_column("Status")
    table.add_column("Depends on")
    table.add_column("Vals", justify="right")
    table.add_column("Last build")
    table.add_column("Tries", justify="right")
    table.add_column("Gen")
    for row in rows:
        target = row["target"] + (" [dim](removed from intent/)[/dim]" if row.get("removed") else "")
        table.add_row(
            target,
            styled(row["status"]),
            ", ".join(row.get("depends_on", [])) or "-",
            str(row.get("validations", 0)),
            row.get("timestamp") or "-",
            str(row["attempts"]) if row.get("attempts") else "-",
            (row.get("generation_id") or "")[:8] or "-",
        )
    console.print(table)


def render_diff(diff_text: str, result: BuildResult | None = None, stat_only: bool = False) -> None:
    if result is not None:
        console.print(
            f"[bold]{result.target}[/bold] commit {result.commit_id[:8]} at {result.timestamp} — "
            f"{len(result.files_created)} created, {len(result.files_modified)} modified"
        )
        for f in result.files_created:
            console.print(f"  [green]+[/green] {f}")
        for f in result.files_modified:
            console.print(f"  [yellow]~[/yellow] {f}")
    if stat_only:
        return
    if not diff_text:
        console.print("[dim]No diff recorded.[/dim]")
        return
    console.print(Syntax(diff_text, "diff", theme="ansi_dark", word_wrap=False))


def render_build_log(target: str, history: list[BuildResult], validations: list[dict[str, Any]]) -> None:
    table = Table(title=f"Build history for {target}", title_justify="left")
    table.add_column("Generation")
    table.add_column("Status")
    table.add_column("Attempts", justify="right")
    table.add_column("Duration", justify="right")
    table.add_column("Timestamp")
    table.add_column("Commit")
    for r in history:
        table.add_row(r.generation_id[:8] or "-", styled(r.status.value), str(r.attempts), f"{r.total_duration_secs:.1f}s", r.timestamp, r.commit_id[:8] or "-")
    console.print(table)
    if not history:
        return
    latest = history[0]
    console.print(f"[bold]Latest build steps[/bold] (generation {latest.generation_id[:8]}):")
    for s in latest.steps:
        console.print(f"  {styled(s.status)} {s.phase} [dim]({s.duration_secs:.1f}s)[/dim] {s.summary.splitlines()[0] if s.summary else ''}")
    if validations:
        console.print("[bold]Validations recorded for the latest build:[/bold]")
        for v in validations:
            console.print(f"  {styled(v['status'])} {v['name']} [dim]({v['type']}, {v['severity']})[/dim] {(v.get('reason') or '').splitlines()[0] if v.get('reason') else ''}")


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


def render_compare_results(response: DifferencingResponse) -> None:
    table = Table(title="Functional Equivalence", title_justify="left")
    table.add_column("Dimension", style="cyan")
    table.add_column("Status")
    table.add_column("Rationale")
    for dim in response.dimensions:
        table.add_row(dim.name, styled(dim.status), dim.rationale)
    console.print(table)
    console.print(f"[bold]Result:[/bold] {styled(response.status)}")
    console.print(f"[bold]Summary:[/bold] {response.summary}")
