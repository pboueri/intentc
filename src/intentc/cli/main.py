"""CLI commands for intentc — thin wrappers over core workflows."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer

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
    help="A compiler of intent — transforms specs into working code using AI agents.",
    invoke_without_command=True,
    no_args_is_help=True,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_project_or_exit(intent_dir: Path) -> Project:
    """Load a project, printing a friendly error and exiting on parse failure."""
    try:
        return load_project(intent_dir)
    except ParseErrors as exc:
        for err in exc.errors:
            print_error(
                f"{err.source_path}:{err.line}: {err.message}"
                if hasattr(err, "line") and err.line
                else f"{err.source_path}: {err.message}"
                if hasattr(err, "source_path") and err.source_path
                else str(err.message)
            )
        raise typer.Exit(code=2)


def _make_log_callback():
    """Create a timestamped log callback using Rich."""
    def _log(msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        console.print(f"[dim]{ts}[/dim] {msg}")
    return _log


def _resolve_output_dir(output_dir: str | None, config: Config) -> str:
    """Resolve the output directory from flag or config default."""
    return output_dir if output_dir else config.default_output_dir


def _resolve_profile(profile_name: str | None, config: Config):
    """Resolve agent profile: flag override > config default."""
    from intentc.build.agents import AgentProfile

    if profile_name:
        return AgentProfile(
            name=profile_name,
            provider=config.default_profile.provider,
            timeout=config.default_profile.timeout,
            retries=config.default_profile.retries,
        )
    return config.default_profile


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def init(
    name: Optional[str] = typer.Argument(None, help="Project name (default: current directory name)"),
) -> None:
    """Create a new intentc project in the current directory."""
    cwd = Path.cwd()
    intent_dir = cwd / "intent"

    if (intent_dir / "project.ic").exists():
        print_error("Project already exists (intent/project.ic found). Aborting.")
        raise typer.Exit(code=2)

    project_name = name or cwd.name
    project = blank_project(project_name)
    write_project(project, intent_dir)

    config = Config()
    config_path = save_config(config, cwd)

    # Collect created files for summary
    created_files: list[str] = []
    for p in sorted(intent_dir.rglob("*")):
        if p.is_file():
            created_files.append(str(p.relative_to(cwd)))
    created_files.append(str(config_path.relative_to(cwd)))

    render_init_summary(created_files)


@app.command()
def build(
    target: Optional[str] = typer.Argument(None, help="Feature path to build (omit for all)"),
    force: bool = typer.Option(False, "--force", "-f", help="Rebuild even if already built"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Print the build plan without executing"),
    output_dir: Optional[str] = typer.Option(None, "--output-dir", "-o", help="Override output directory"),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Agent profile name override"),
    implementation: Optional[str] = typer.Option(None, "--implementation", "-i", help="Implementation name"),
) -> None:
    """Build features using the configured agent."""
    from intentc.build.builder import Builder, BuildOptions
    from intentc.build.state import GitVersionControl, StateManager

    cwd = Path.cwd()
    project = _load_project_or_exit(cwd / "intent")
    config = load_config(cwd)

    resolved_output = _resolve_output_dir(output_dir, config)
    resolved_profile = _resolve_profile(profile, config)
    log = _make_log_callback()

    state_manager = StateManager(base_dir=cwd, output_dir=resolved_output)
    vc = GitVersionControl(repo_dir=cwd)
    builder = Builder(
        project=project,
        state_manager=state_manager,
        version_control=vc,
        agent_profile=resolved_profile,
        log=log,
    )

    opts = BuildOptions(
        target=target or "",
        force=force,
        dry_run=dry_run,
        output_dir=resolved_output,
        profile_override=profile or "",
        implementation=implementation or "",
    )

    results, error = builder.build(opts)
    render_build_results(results)

    if error:
        raise typer.Exit(code=1)


@app.command()
def validate(
    target: Optional[str] = typer.Argument(None, help="Feature to validate (omit for all)"),
    output_dir: Optional[str] = typer.Option(None, "--output-dir", "-o", help="Override output directory"),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Agent profile override"),
    implementation: Optional[str] = typer.Option(None, "--implementation", "-i", help="Implementation name"),
) -> None:
    """Run validations independently of the build pipeline."""
    from intentc.build.builder import Builder
    from intentc.build.state import GitVersionControl, StateManager
    from intentc.build.validations import ValidationSuiteResult

    cwd = Path.cwd()
    project = _load_project_or_exit(cwd / "intent")
    config = load_config(cwd)

    if implementation:
        project.resolve_implementation(implementation)

    resolved_output = _resolve_output_dir(output_dir, config)
    resolved_profile = _resolve_profile(profile, config)
    log = _make_log_callback()

    state_manager = StateManager(base_dir=cwd, output_dir=resolved_output)
    vc = GitVersionControl(repo_dir=cwd)
    builder = Builder(
        project=project,
        state_manager=state_manager,
        version_control=vc,
        agent_profile=resolved_profile,
        log=log,
    )

    result = builder.validate(target, resolved_output)

    # Normalize to list
    if isinstance(result, ValidationSuiteResult):
        results = [result]
    else:
        results = result

    render_validation_results(results)

    # Exit 1 if any error-severity validation failed
    for suite_result in results:
        if not suite_result.passed:
            raise typer.Exit(code=1)


@app.command()
def clean(
    target: Optional[str] = typer.Argument(None, help="Feature path to clean"),
    all_targets: bool = typer.Option(False, "--all", help="Reset all state"),
    output_dir: Optional[str] = typer.Option(None, "--output-dir", "-o", help="Override output directory"),
) -> None:
    """Revert a target's generated code and reset its state."""
    from intentc.build.builder import Builder
    from intentc.build.state import GitVersionControl, StateManager

    if not all_targets and not target:
        print_error("Specify a target or use --all to clean everything.")
        raise typer.Exit(code=2)

    cwd = Path.cwd()
    project = _load_project_or_exit(cwd / "intent")
    config = load_config(cwd)

    resolved_output = _resolve_output_dir(output_dir, config)
    log = _make_log_callback()

    state_manager = StateManager(base_dir=cwd, output_dir=resolved_output)
    vc = GitVersionControl(repo_dir=cwd)
    builder = Builder(
        project=project,
        state_manager=state_manager,
        version_control=vc,
        agent_profile=config.default_profile,
        log=log,
    )

    if all_targets:
        builder.clean_all(resolved_output)
        console.print("[green]All state reset.[/green]")
    else:
        builder.clean(target, resolved_output)
        console.print(f"[green]Cleaned target '{target}'.[/green]")


@app.command()
def plan(
    target: str = typer.Argument(..., help="Feature path to plan"),
    output_dir: Optional[str] = typer.Option(None, "--output-dir", "-o", help="Override output directory"),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Agent profile override"),
    implementation: Optional[str] = typer.Option(None, "--implementation", "-i", help="Implementation name"),
) -> None:
    """Enter interactive planning mode with the agent for a specific feature."""
    from intentc.build.agents import AgentProfile, BuildContext, create_from_profile

    cwd = Path.cwd()
    project = _load_project_or_exit(cwd / "intent")
    config = load_config(cwd)

    # Validate feature exists
    if target not in project.features:
        available = ", ".join(sorted(project.features.keys())) or "(none)"
        print_error(f"Feature '{target}' not found. Available features: {available}")
        raise typer.Exit(code=2)

    resolved_output = _resolve_output_dir(output_dir, config)
    resolved_profile = _resolve_profile(profile, config)

    # Resolve implementation if specified
    impl = project.resolve_implementation(implementation) if implementation else None

    # Construct BuildContext
    node = project.features[target]
    if node.intents:
        intent = node.intents[0]
    else:
        intent = IntentFile(name=target)

    ctx = BuildContext(
        intent=intent,
        validations=node.validations,
        output_dir=resolved_output,
        generation_id="planning",
        dependency_names=list(node.depends_on),
        project_intent=project.project_intent,
        implementation=impl,
        response_file_path="",
    )

    agent = create_from_profile(resolved_profile)
    agent.plan(ctx)


@app.command()
def status(
    output_dir: Optional[str] = typer.Option(None, "--output-dir", "-o", help="Override output directory"),
    outdated: bool = typer.Option(False, "--outdated", help="Check for outdated targets"),
) -> None:
    """Show the build state for all tracked targets."""
    from intentc.build.builder import Builder
    from intentc.build.state import GitVersionControl, StateManager

    cwd = Path.cwd()
    config = load_config(cwd)
    resolved_output = _resolve_output_dir(output_dir, config)

    state_manager = StateManager(base_dir=cwd, output_dir=resolved_output)
    targets = state_manager.list_targets()

    # Collect build results for display
    build_results = {}
    for target_name, _ in targets:
        result = state_manager.get_build_result(target_name)
        if result:
            build_results[target_name] = result

    outdated_list: list[str] = []
    if outdated:
        project = _load_project_or_exit(cwd / "intent")
        vc = GitVersionControl(repo_dir=cwd)
        builder = Builder(
            project=project,
            state_manager=state_manager,
            version_control=vc,
            agent_profile=config.default_profile,
        )
        outdated_list = builder.detect_outdated()

    render_status_table(targets, build_results=build_results, outdated=outdated_list)


@app.command()
def diff(
    target: str = typer.Argument(..., help="Feature path"),
    output_dir: Optional[str] = typer.Option(None, "--output-dir", "-o", help="Override output directory"),
) -> None:
    """Show the diff of what was generated for a target."""
    from intentc.build.state import GitVersionControl, StateManager

    cwd = Path.cwd()
    config = load_config(cwd)
    resolved_output = _resolve_output_dir(output_dir, config)

    state_manager = StateManager(base_dir=cwd, output_dir=resolved_output)
    result = state_manager.get_build_result(target)

    if result is None or not result.commit_id:
        print_error(f"No build result found for target '{target}'.")
        raise typer.Exit(code=2)

    vc = GitVersionControl(repo_dir=cwd)
    diff_text = vc.diff(f"{result.commit_id}~1", result.commit_id)
    render_diff(diff_text)


@app.command()
def compare(
    dir_a: str = typer.Argument(..., help="Path to the reference output directory"),
    dir_b: str = typer.Argument(..., help="Path to the candidate output directory"),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Agent profile override"),
    implementation: Optional[str] = typer.Option(None, "--implementation", "-i", help="Implementation name"),
) -> None:
    """Evaluate functional equivalence between two output directories."""
    from intentc.differencing import run_differencing

    cwd = Path.cwd()
    project = _load_project_or_exit(cwd / "intent")
    config = load_config(cwd)

    # Validate directories exist
    if not Path(dir_a).is_dir():
        print_error(f"Directory not found: {dir_a}")
        raise typer.Exit(code=2)
    if not Path(dir_b).is_dir():
        print_error(f"Directory not found: {dir_b}")
        raise typer.Exit(code=2)

    resolved_profile = _resolve_profile(profile, config)

    response = run_differencing(
        output_dir_a=dir_a,
        output_dir_b=dir_b,
        project=project,
        profile=resolved_profile,
        implementation=implementation,
    )

    render_compare_results(response)

    if response.status != "equivalent":
        raise typer.Exit(code=1)
