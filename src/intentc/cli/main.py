from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from intentc.cli.config import Config, load_config, save_config
from intentc.cli.output import (
    console,
    print_error,
    render_build_results,
    render_compare_result,
    render_diff,
    render_init_summary,
    render_status_table,
    render_validation_results,
    render_validation_summary,
)
from intentc.core.project import Project, blank_project, load_project, write_project
from intentc.core.types import IntentFile, ParseErrors

app = typer.Typer(
    name="intentc",
    help="A compiler of intent — transforms specs into working code using AI agents.",
    invoke_without_command=True,
    no_args_is_help=True,
)


def _load_project_or_exit(intent_dir: Path) -> Project:
    """Load a project, printing a friendly error and exiting with code 2 on parse errors."""
    try:
        return load_project(intent_dir)
    except ParseErrors as exc:
        for err in exc.errors:
            print_error(str(err))
        raise typer.Exit(code=2)


@app.command()
def init(
    name: Optional[str] = typer.Argument(None, help="Project name (default: current directory name)"),
) -> None:
    """Create a new intentc project in the current directory."""
    cwd = Path.cwd()
    intent_dir = cwd / "intent"

    if (intent_dir / "project.ic").exists():
        print_error(f"Project already exists at {intent_dir / 'project.ic'}")
        raise typer.Exit(code=2)

    project_name = name or cwd.name
    project = blank_project(project_name)
    write_project(project, intent_dir)
    config = Config()
    config_path = save_config(config, cwd)

    # Collect created files
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
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Print build plan without executing"),
    output_dir: Optional[str] = typer.Option(None, "--output-dir", "-o", help="Override output directory"),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Agent profile name override"),
    implementation: Optional[str] = typer.Option(None, "--implementation", "-i", help="Implementation name to use"),
) -> None:
    """Build features using the configured agent."""
    from intentc.build.agents.types import AgentProfile
    from intentc.build.builder.builder import Builder, BuildOptions
    from intentc.build.state import GitVersionControl, StateManager, TargetStatus

    cwd = Path.cwd()
    project = _load_project_or_exit(cwd / "intent")
    config = load_config(cwd)

    # Resolve agent profile
    agent_profile = config.default_profile
    if profile:
        agent_profile = AgentProfile(name=profile, provider=agent_profile.provider)

    resolved_output_dir = output_dir or config.default_output_dir

    state_manager = StateManager(base_dir=cwd, output_dir=resolved_output_dir)
    version_control = GitVersionControl(repo_dir=cwd)
    builder = Builder(
        project=project,
        state_manager=state_manager,
        version_control=version_control,
        agent_profile=agent_profile,
        log=console.print,
    )

    opts = BuildOptions(
        target=target or "",
        force=force,
        dry_run=dry_run,
        output_dir=resolved_output_dir,
        profile_override=profile or "",
        implementation=implementation or "",
    )

    results, error = builder.build(opts)
    if results:
        render_build_results(results)

    if error or any(r.status == TargetStatus.FAILED for r in results):
        raise typer.Exit(code=1)


@app.command()
def validate(
    target: Optional[str] = typer.Argument(None, help="Feature to validate (omit for all)"),
    output_dir: Optional[str] = typer.Option(None, "--output-dir", "-o", help="Override output directory"),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Agent profile override"),
    implementation: Optional[str] = typer.Option(None, "--implementation", "-i", help="Implementation name to use"),
) -> None:
    """Run validations independently of the build pipeline."""
    from intentc.build.agents.types import AgentProfile
    from intentc.build.builder.builder import Builder
    from intentc.build.state import GitVersionControl, StateManager
    from intentc.build.validations import ValidationSuiteResult

    cwd = Path.cwd()
    project = _load_project_or_exit(cwd / "intent")
    config = load_config(cwd)

    agent_profile = config.default_profile
    if profile:
        agent_profile = AgentProfile(name=profile, provider=agent_profile.provider)

    if implementation:
        project.resolve_implementation(implementation)

    resolved_output_dir = output_dir or config.default_output_dir

    state_manager = StateManager(base_dir=cwd, output_dir=resolved_output_dir)
    version_control = GitVersionControl(repo_dir=cwd)
    builder = Builder(
        project=project,
        state_manager=state_manager,
        version_control=version_control,
        agent_profile=agent_profile,
        log=console.print,
    )

    result = builder.validate(target, resolved_output_dir)

    # Normalize to list
    if isinstance(result, ValidationSuiteResult):
        results = [result]
    else:
        results = result

    render_validation_results(results)
    render_validation_summary(results)

    # Exit 1 if any error-severity validation failed
    if any(not r.passed for r in results):
        raise typer.Exit(code=1)


@app.command()
def clean(
    target: Optional[str] = typer.Argument(None, help="Feature path to clean"),
    all_targets: bool = typer.Option(False, "--all", help="Reset all state"),
    output_dir: Optional[str] = typer.Option(None, "--output-dir", "-o", help="Override output directory"),
) -> None:
    """Revert a target's generated code and reset its state."""
    from intentc.build.builder.builder import Builder
    from intentc.build.state import GitVersionControl, StateManager

    if not target and not all_targets:
        print_error("Either provide a target or use --all")
        raise typer.Exit(code=2)

    cwd = Path.cwd()
    project = _load_project_or_exit(cwd / "intent")
    config = load_config(cwd)

    resolved_output_dir = output_dir or config.default_output_dir

    state_manager = StateManager(base_dir=cwd, output_dir=resolved_output_dir)
    version_control = GitVersionControl(repo_dir=cwd)
    builder = Builder(
        project=project,
        state_manager=state_manager,
        version_control=version_control,
        agent_profile=config.default_profile,
        log=console.print,
    )

    if all_targets:
        builder.clean_all(resolved_output_dir)
        console.print("All state cleaned.")
    else:
        builder.clean(target, resolved_output_dir)
        console.print(f"Cleaned target '{target}'.")


@app.command()
def plan(
    target: str = typer.Argument(..., help="Feature path to plan"),
    output_dir: Optional[str] = typer.Option(None, "--output-dir", "-o", help="Override output directory"),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Agent profile override"),
    implementation: Optional[str] = typer.Option(None, "--implementation", "-i", help="Implementation name to use"),
) -> None:
    """Enter interactive planning mode with the agent for a specific feature."""
    from intentc.build.agents.base import create_from_profile
    from intentc.build.agents.types import AgentProfile, BuildContext

    cwd = Path.cwd()
    project = _load_project_or_exit(cwd / "intent")
    config = load_config(cwd)

    # Validate feature exists
    if target not in project.features:
        available = list(project.features.keys())
        print_error(f"Feature '{target}' not found. Available features: {available}")
        raise typer.Exit(code=2)

    agent_profile = config.default_profile
    if profile:
        agent_profile = AgentProfile(name=profile, provider=agent_profile.provider)

    resolved_output_dir = output_dir or config.default_output_dir

    # Resolve implementation
    impl = None
    if implementation:
        impl = project.resolve_implementation(implementation)
    else:
        try:
            impl = project.resolve_implementation()
        except (KeyError, ValueError):
            pass

    # Build context
    node = project.features[target]
    if node.intents:
        intent = node.intents[0]
    else:
        intent = IntentFile(name=target)

    ctx = BuildContext(
        intent=intent,
        validations=node.validations,
        output_dir=resolved_output_dir,
        generation_id="",
        dependency_names=node.depends_on,
        project_intent=project.project_intent,
        implementation=impl,
        response_file_path="",
    )

    agent = create_from_profile(agent_profile)
    agent.plan(ctx)


@app.command()
def status(
    output_dir: Optional[str] = typer.Option(None, "--output-dir", "-o", help="Override output directory"),
    outdated: bool = typer.Option(False, "--outdated", help="Check for outdated targets"),
) -> None:
    """Show the build state for all tracked targets."""
    from intentc.build.builder.builder import Builder
    from intentc.build.state import GitVersionControl, StateManager

    cwd = Path.cwd()
    config = load_config(cwd)
    resolved_output_dir = output_dir or config.default_output_dir

    state_manager = StateManager(base_dir=cwd, output_dir=resolved_output_dir)

    targets = state_manager.list_targets()

    # Collect build results for display
    build_results: dict = {}
    for target_name, _ in targets:
        build_results[target_name] = state_manager.get_build_result(target_name)

    outdated_list = None
    if outdated:
        project = _load_project_or_exit(cwd / "intent")
        version_control = GitVersionControl(repo_dir=cwd)
        builder = Builder(
            project=project,
            state_manager=state_manager,
            version_control=version_control,
            agent_profile=config.default_profile,
        )
        outdated_list = builder.detect_outdated()

    render_status_table(targets, build_results, outdated_list)


@app.command()
def diff(
    target: str = typer.Argument(..., help="Feature path"),
    output_dir: Optional[str] = typer.Option(None, "--output-dir", "-o", help="Override output directory"),
) -> None:
    """Show the diff of what was generated for a target."""
    from intentc.build.state import GitVersionControl, StateManager

    cwd = Path.cwd()
    config = load_config(cwd)
    resolved_output_dir = output_dir or config.default_output_dir

    state_manager = StateManager(base_dir=cwd, output_dir=resolved_output_dir)
    result = state_manager.get_build_result(target)

    if result is None:
        print_error(f"No build result found for target '{target}'")
        raise typer.Exit(code=2)

    if not result.commit_id:
        print_error(f"No commit ID recorded for target '{target}'")
        raise typer.Exit(code=2)

    version_control = GitVersionControl(repo_dir=cwd)
    diff_text = version_control.diff(f"{result.commit_id}~1", result.commit_id)
    render_diff(diff_text)


@app.command()
def compare(
    dir_a: str = typer.Argument(..., help="Path to the reference output directory"),
    dir_b: str = typer.Argument(..., help="Path to the candidate output directory"),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Agent profile override"),
    implementation: Optional[str] = typer.Option(None, "--implementation", "-i", help="Implementation name to use"),
) -> None:
    """Evaluate functional equivalence between two output directories."""
    from intentc.differencing import run_differencing

    cwd = Path.cwd()
    project = _load_project_or_exit(cwd / "intent")
    config = load_config(cwd)

    agent_profile = config.default_profile
    if profile:
        from intentc.build.agents.types import AgentProfile
        agent_profile = AgentProfile(name=profile, provider=agent_profile.provider)

    impl = None
    if implementation:
        impl = project.resolve_implementation(implementation)

    result = run_differencing(
        dir_a=dir_a,
        dir_b=dir_b,
        project=project,
        agent_profile=agent_profile,
        implementation=impl,
    )

    render_compare_result(result.status, result.summary, result.dimensions)

    if result.status != "equivalent":
        raise typer.Exit(code=1)
