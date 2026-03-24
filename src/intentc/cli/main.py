"""CLI commands for intentc — thin wrappers over core workflows."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from intentc.build.agents.models import AgentProfile, BuildContext
from intentc.build.builder.builder import Builder, BuildOptions
from intentc.build.state import GitVersionControl, StateManager
from intentc.cli.config import Config, load_config, save_config
from intentc.cli.output import (
    console,
    print_error,
    render_build_results,
    render_compare_results,
    render_diff,
    render_init_summary,
    render_status_table,
    render_validation_results,
)
from intentc.core.models import IntentFile, ParseErrors
from intentc.core.project import Project, blank_project, load_project, write_project

app = typer.Typer(
    name="intentc",
    help="A compiler of intent \u2014 transforms specs into working code using AI agents.",
    invoke_without_command=True,
    no_args_is_help=True,
)


def _load_project_or_exit(intent_dir: Path) -> Project:
    """Load a project, printing a friendly error and exiting on parse errors."""
    try:
        return load_project(intent_dir)
    except ParseErrors as exc:
        for err in exc.errors:
            path_str = str(err.path) if err.path else "unknown"
            field_str = f" (field: {err.field})" if err.field else ""
            print_error(f"{path_str}{field_str}: {err.message}")
        raise typer.Exit(code=2)


def _make_log_callback() -> callable:
    """Create a timestamped log callback using Rich dim markup."""

    def _log(message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        console.print(f"[dim]{timestamp}[/dim] {message}")

    return _log


def _resolve_profile(
    profile_name: str | None, config: Config
) -> AgentProfile:
    """Resolve agent profile: CLI flag > config default."""
    profile = config.default_profile
    if profile_name:
        profile = AgentProfile(**{**profile.model_dump(), "name": profile_name})
    return profile


@app.command()
def init(
    name: Optional[str] = typer.Argument(None, help="Project name (default: current directory name)"),
) -> None:
    """Create a new intentc project in the current directory."""
    intent_dir = Path.cwd() / "intent"
    project_ic = intent_dir / "project.ic"

    if project_ic.exists():
        print_error(f"Project already exists at {project_ic}")
        raise typer.Exit(code=2)

    project_name = name or Path.cwd().name
    project = blank_project(project_name)
    write_project(project, intent_dir)

    config = Config()
    config_path = save_config(config, Path.cwd())

    created_files: list[str | Path] = []
    for p in sorted(intent_dir.rglob("*")):
        if p.is_file():
            created_files.append(str(p.relative_to(Path.cwd())))
    created_files.append(str(config_path.relative_to(Path.cwd())))

    render_init_summary(created_files)


@app.command()
def build(
    target: Optional[str] = typer.Argument(None, help="Specific feature path to build"),
    force: bool = typer.Option(False, "--force", "-f", help="Rebuild even if already built"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Print build plan without executing"),
    output_dir: Optional[str] = typer.Option(None, "--output-dir", "-o", help="Override output directory"),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Agent profile name override"),
    implementation: Optional[str] = typer.Option(None, "--implementation", "-i", help="Implementation name to use"),
) -> None:
    """Build features using the configured agent."""
    intent_dir = Path.cwd() / "intent"
    project = _load_project_or_exit(intent_dir)
    config = load_config(Path.cwd())

    resolved_output = output_dir or config.default_output_dir
    agent_profile = _resolve_profile(profile, config)
    log = _make_log_callback()

    state_manager = StateManager(Path.cwd(), resolved_output)
    vc = GitVersionControl(Path.cwd())
    builder = Builder(
        project=project,
        state_manager=state_manager,
        version_control=vc,
        agent_profile=agent_profile,
        log=log,
    )

    opts = BuildOptions(
        target=target or "",
        force=force,
        dry_run=dry_run,
        output_dir=resolved_output,
        implementation=implementation or "",
    )

    results, error = builder.build(opts)
    render_build_results(results)

    if error:
        raise typer.Exit(code=1)


@app.command()
def validate(
    target: Optional[str] = typer.Argument(None, help="Specific feature to validate"),
    output_dir: Optional[str] = typer.Option(None, "--output-dir", "-o", help="Override output directory"),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Agent profile override"),
    implementation: Optional[str] = typer.Option(None, "--implementation", "-i", help="Implementation name to use"),
) -> None:
    """Run validations independently of the build pipeline."""
    intent_dir = Path.cwd() / "intent"
    project = _load_project_or_exit(intent_dir)
    config = load_config(Path.cwd())

    resolved_output = output_dir or config.default_output_dir

    if implementation:
        project.resolve_implementation(implementation)

    agent_profile = _resolve_profile(profile, config)
    log = _make_log_callback()

    state_manager = StateManager(Path.cwd(), resolved_output)
    vc = GitVersionControl(Path.cwd())
    builder = Builder(
        project=project,
        state_manager=state_manager,
        version_control=vc,
        agent_profile=agent_profile,
        log=log,
    )

    result = builder.validate(target, resolved_output)

    if isinstance(result, list):
        suite_results = result
    else:
        suite_results = [result]

    render_validation_results(suite_results)

    has_error = any(
        vr.status != "pass" for sr in suite_results for vr in sr.results
    )
    if has_error:
        raise typer.Exit(code=1)


@app.command()
def clean(
    target: Optional[str] = typer.Argument(None, help="Feature path to clean"),
    all_targets: bool = typer.Option(False, "--all", help="Reset all state"),
    output_dir: Optional[str] = typer.Option(None, "--output-dir", "-o", help="Override output directory"),
) -> None:
    """Revert a target's generated code and reset its state."""
    if not target and not all_targets:
        print_error("Provide a target or use --all")
        raise typer.Exit(code=2)

    intent_dir = Path.cwd() / "intent"
    project = _load_project_or_exit(intent_dir)
    config = load_config(Path.cwd())

    resolved_output = output_dir or config.default_output_dir
    agent_profile = config.default_profile
    log = _make_log_callback()

    state_manager = StateManager(Path.cwd(), resolved_output)
    vc = GitVersionControl(Path.cwd())
    builder = Builder(
        project=project,
        state_manager=state_manager,
        version_control=vc,
        agent_profile=agent_profile,
        log=log,
    )

    if all_targets:
        builder.clean_all(resolved_output)
        console.print("[green]All state reset.[/green]")
    else:
        builder.clean(target, resolved_output)
        console.print(f"[green]Cleaned target: {target}[/green]")


@app.command()
def plan(
    target: str = typer.Argument(..., help="Feature path to plan"),
    output_dir: Optional[str] = typer.Option(None, "--output-dir", "-o", help="Override output directory"),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Agent profile override"),
    implementation: Optional[str] = typer.Option(None, "--implementation", "-i", help="Implementation name to use"),
) -> None:
    """Enter interactive planning mode with the agent for a specific feature."""
    intent_dir = Path.cwd() / "intent"
    project = _load_project_or_exit(intent_dir)
    config = load_config(Path.cwd())

    if target not in project.features:
        available = sorted(project.features.keys())
        print_error(
            f"Feature '{target}' not found. Available features: {', '.join(available)}"
        )
        raise typer.Exit(code=2)

    resolved_output = output_dir or config.default_output_dir
    agent_profile = _resolve_profile(profile, config)

    from intentc.build.agents.factory import create_from_profile

    agent = create_from_profile(agent_profile)

    node = project.features[target]
    if node.intents:
        intent = node.intents[0]
    else:
        intent = IntentFile(name=target)

    impl = None
    if implementation:
        impl = project.resolve_implementation(implementation)
    else:
        try:
            impl = project.resolve_implementation()
        except ValueError:
            pass

    ctx = BuildContext(
        intent=intent,
        validations=node.validations,
        output_dir=resolved_output,
        project_intent=project.project_intent,
        implementation=impl,
    )

    agent.plan(ctx)


@app.command()
def status(
    output_dir: Optional[str] = typer.Option(None, "--output-dir", "-o", help="Override output directory"),
    outdated: bool = typer.Option(False, "--outdated", help="Check for outdated targets"),
) -> None:
    """Show the build state for all tracked targets."""
    config = load_config(Path.cwd())
    resolved_output = output_dir or config.default_output_dir

    state_manager = StateManager(Path.cwd(), resolved_output)
    targets = state_manager.list_targets()

    outdated_list = None
    if outdated:
        intent_dir = Path.cwd() / "intent"
        project = _load_project_or_exit(intent_dir)
        agent_profile = config.default_profile
        vc = GitVersionControl(Path.cwd())
        builder = Builder(
            project=project,
            state_manager=state_manager,
            version_control=vc,
            agent_profile=agent_profile,
        )
        outdated_list = builder.detect_outdated()

    render_status_table(targets, outdated_list)


@app.command()
def diff(
    target: str = typer.Argument(..., help="Feature path"),
    output_dir: Optional[str] = typer.Option(None, "--output-dir", "-o", help="Override output directory"),
) -> None:
    """Show the diff of what was generated for a target."""
    config = load_config(Path.cwd())
    resolved_output = output_dir or config.default_output_dir

    state_manager = StateManager(Path.cwd(), resolved_output)
    result = state_manager.get_build_result(target)

    if result is None:
        print_error(f"No build result found for target '{target}'")
        raise typer.Exit(code=2)

    if not result.commit_id:
        print_error(f"No commit ID recorded for target '{target}'")
        raise typer.Exit(code=2)

    vc = GitVersionControl(Path.cwd())
    try:
        diff_text = vc.diff(f"{result.commit_id}~1", result.commit_id)
    except RuntimeError as e:
        print_error(f"Failed to get diff: {e}")
        raise typer.Exit(code=1)

    render_diff(diff_text)


@app.command()
def compare(
    dir_a: str = typer.Argument(..., help="Path to the reference output directory"),
    dir_b: str = typer.Argument(..., help="Path to the candidate output directory"),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Agent profile override"),
    implementation: Optional[str] = typer.Option(None, "--implementation", "-i", help="Implementation name to use"),
) -> None:
    """Evaluate functional equivalence between two output directories."""
    try:
        from intentc.differencing import run_differencing
    except ImportError:
        print_error("Differencing module not available")
        raise typer.Exit(code=2)

    intent_dir = Path.cwd() / "intent"
    project = _load_project_or_exit(intent_dir)
    config = load_config(Path.cwd())

    agent_profile = _resolve_profile(profile, config)

    impl = None
    if implementation:
        impl = project.resolve_implementation(implementation)
    else:
        try:
            impl = project.resolve_implementation()
        except ValueError:
            pass

    response = run_differencing(
        dir_a=dir_a,
        dir_b=dir_b,
        project=project,
        agent_profile=agent_profile,
        implementation=impl,
    )

    render_compare_results(response)

    if response.status != "equivalent":
        raise typer.Exit(code=1)
