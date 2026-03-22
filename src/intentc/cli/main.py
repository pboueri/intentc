"""CLI commands for intentc — a compiler of intent."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer

from intentc.build.agents import AgentProfile, BuildContext, create_from_profile
from intentc.build.builder.builder import Builder, BuildOptions
from intentc.build.state import GitVersionControl, StateManager
from intentc.build.storage import SQLiteBackend
from intentc.cli.config import Config, load_config, save_config
from intentc.cli.output import (
    console,
    print_error,
    render_build_results,
    render_compare_result,
    render_diff,
    render_init_summary,
    render_status_table,
    render_validation_result,
    render_validation_results,
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
    """Load project from intent directory, printing friendly errors on failure."""
    try:
        return load_project(intent_dir)
    except ParseErrors as exc:
        for err in exc.errors:
            print_error(f"{err.path}: {err.message}" + (f" (field: {err.field})" if err.field else ""))
        raise typer.Exit(code=2)


def _resolve_profile(profile_name: str | None, config: Config) -> AgentProfile:
    """Return the agent profile: CLI override or config default."""
    if profile_name:
        return AgentProfile(name=profile_name, provider=profile_name)
    return config.default_profile


def _resolve_output_dir(output_dir: str | None, config: Config) -> str:
    """Return the output directory: CLI override or config default."""
    return output_dir or config.default_output_dir


@app.command()
def init(
    name: Optional[str] = typer.Argument(None, help="Project name (default: current directory name)"),
) -> None:
    """Create a new intentc project in the current directory."""
    cwd = Path.cwd()
    intent_dir = cwd / "intent"

    if (intent_dir / "project.ic").exists():
        print_error("intent/project.ic already exists — refusing to overwrite.")
        raise typer.Exit(code=2)

    project_name = name or cwd.name
    project = blank_project(project_name)
    write_project(project, intent_dir)

    config = Config()
    config_path = save_config(config, cwd)

    created_files: list[str] = []
    for p in sorted(intent_dir.rglob("*")):
        if p.is_file():
            created_files.append(str(p.relative_to(cwd)))
    created_files.append(str(config_path.relative_to(cwd)))

    render_init_summary(created_files)


@app.command()
def build(
    target: Optional[str] = typer.Argument(None, help="Feature path to build"),
    force: bool = typer.Option(False, "--force", "-f", help="Rebuild even if already built"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Print the build plan without executing"),
    output_dir: Optional[str] = typer.Option(None, "--output-dir", "-o", help="Override the output directory"),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Agent profile name override"),
    implementation: Optional[str] = typer.Option(None, "--implementation", "-i", help="Implementation name"),
) -> None:
    """Build features using the configured agent."""
    cwd = Path.cwd()
    intent_dir = cwd / "intent"
    project = _load_project_or_exit(intent_dir)
    config = load_config(cwd)

    agent_profile = _resolve_profile(profile, config)
    resolved_output = _resolve_output_dir(output_dir, config)

    backend = SQLiteBackend(cwd, resolved_output)
    state_mgr = StateManager(cwd, resolved_output, backend)
    vcs = GitVersionControl(Path(resolved_output))
    builder = Builder(project, state_mgr, vcs, agent_profile)

    opts = BuildOptions(
        target=target or "",
        force=force,
        dry_run=dry_run,
        output_dir=resolved_output,
        implementation=implementation or "",
    )
    results, err = builder.build(opts)
    render_build_results(results, dry_run=dry_run)

    if err:
        raise typer.Exit(code=1)


@app.command()
def validate(
    target: Optional[str] = typer.Argument(None, help="Feature to validate"),
    output_dir: Optional[str] = typer.Option(None, "--output-dir", "-o", help="Override the output directory"),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Agent profile override"),
    implementation: Optional[str] = typer.Option(None, "--implementation", "-i", help="Implementation name"),
) -> None:
    """Run validations independently of the build pipeline."""
    cwd = Path.cwd()
    intent_dir = cwd / "intent"
    project = _load_project_or_exit(intent_dir)
    config = load_config(cwd)

    agent_profile = _resolve_profile(profile, config)
    resolved_output = _resolve_output_dir(output_dir, config)

    if implementation:
        project.resolve_implementation(implementation)

    backend = SQLiteBackend(cwd, resolved_output)
    state_mgr = StateManager(cwd, resolved_output, backend)
    vcs = GitVersionControl(Path(resolved_output))
    builder = Builder(project, state_mgr, vcs, agent_profile)

    result = builder.validate(target or "", resolved_output)

    if isinstance(result, list):
        render_validation_results(result)
        has_failure = any(not r.passed for r in result)
    else:
        render_validation_result(result)
        console.print(
            f"\n{sum(1 for v in result.results if v.status == 'pass')}/{len(result.results)} passed, "
            f"{sum(1 for v in result.results if v.status != 'pass')} error(s), 0 warning(s)"
        )
        has_failure = not result.passed

    if has_failure:
        raise typer.Exit(code=1)


@app.command()
def clean(
    target: Optional[str] = typer.Argument(None, help="Feature path to clean"),
    all_targets: bool = typer.Option(False, "--all", help="Reset all state"),
    output_dir: Optional[str] = typer.Option(None, "--output-dir", "-o", help="Override the output directory"),
) -> None:
    """Revert a target's generated code and reset its state."""
    cwd = Path.cwd()
    intent_dir = cwd / "intent"
    project = _load_project_or_exit(intent_dir)
    config = load_config(cwd)
    resolved_output = _resolve_output_dir(output_dir, config)

    backend = SQLiteBackend(cwd, resolved_output)
    state_mgr = StateManager(cwd, resolved_output, backend)
    vcs = GitVersionControl(Path(resolved_output))
    builder = Builder(project, state_mgr, vcs, config.default_profile)

    if all_targets:
        builder.clean_all(resolved_output)
        console.print("All targets cleaned.")
    elif target:
        builder.clean(target, resolved_output)
        console.print(f"Cleaned: {target}")
    else:
        print_error("Specify a target or use --all.")
        raise typer.Exit(code=2)


@app.command()
def plan(
    target: str = typer.Argument(..., help="Feature path to plan"),
    output_dir: Optional[str] = typer.Option(None, "--output-dir", "-o", help="Override the output directory"),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Agent profile override"),
    implementation: Optional[str] = typer.Option(None, "--implementation", "-i", help="Implementation name"),
) -> None:
    """Enter interactive planning mode with the agent for a specific feature."""
    cwd = Path.cwd()
    intent_dir = cwd / "intent"
    project = _load_project_or_exit(intent_dir)
    config = load_config(cwd)

    if target not in project.features:
        available = ", ".join(sorted(project.features.keys()))
        print_error(f"Feature '{target}' not found. Available features: {available}")
        raise typer.Exit(code=2)

    agent_profile = _resolve_profile(profile, config)
    resolved_output = _resolve_output_dir(output_dir, config)

    feature = project.features[target]
    if feature.intents:
        intent = feature.intents[0]
    else:
        intent = IntentFile(name=target)

    impl = None
    if implementation:
        impl = project.resolve_implementation(implementation)
    else:
        try:
            impl = project.resolve_implementation(None)
        except (KeyError, ValueError):
            pass

    ctx = BuildContext(
        intent=intent,
        output_dir=resolved_output,
        generation_id="plan",
        project_intent=project.project_intent,
        implementation=impl,
        response_file_path="",
    )

    agent = create_from_profile(agent_profile)
    agent.plan(ctx)


@app.command()
def status(
    output_dir: Optional[str] = typer.Option(None, "--output-dir", "-o", help="Override the output directory"),
    outdated: bool = typer.Option(False, "--outdated", help="Check for outdated targets"),
) -> None:
    """Show the build state for all tracked targets."""
    cwd = Path.cwd()
    config = load_config(cwd)
    resolved_output = _resolve_output_dir(output_dir, config)

    backend = SQLiteBackend(cwd, resolved_output)
    state_mgr = StateManager(cwd, resolved_output, backend)
    targets = state_mgr.list_targets()

    results: dict[str, object] = {}
    for t, _ in targets:
        r = state_mgr.get_build_result(t)
        if r:
            results[t] = r

    outdated_list = None
    if outdated:
        intent_dir = cwd / "intent"
        project = _load_project_or_exit(intent_dir)
        vcs = GitVersionControl(Path(resolved_output))
        builder = Builder(project, state_mgr, vcs, config.default_profile)
        outdated_list = builder.detect_outdated()

    render_status_table(targets, results, outdated_list)


@app.command()
def diff(
    target: str = typer.Argument(..., help="Feature path"),
    output_dir: Optional[str] = typer.Option(None, "--output-dir", "-o", help="Override the output directory"),
) -> None:
    """Show the diff of what was generated for a target."""
    cwd = Path.cwd()
    config = load_config(cwd)
    resolved_output = _resolve_output_dir(output_dir, config)

    backend = SQLiteBackend(cwd, resolved_output)
    state_mgr = StateManager(cwd, resolved_output, backend)

    result = state_mgr.get_build_result(target)
    if result is None:
        print_error(f"No build result found for target '{target}'.")
        raise typer.Exit(code=2)

    vcs = GitVersionControl(Path(resolved_output))
    diff_text = vcs.diff(f"{result.commit_id}~1", result.commit_id)
    render_diff(diff_text)


@app.command()
def compare(
    dir_a: str = typer.Argument(..., help="Path to the reference output directory"),
    dir_b: str = typer.Argument(..., help="Path to the candidate output directory"),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Agent profile override"),
    implementation: Optional[str] = typer.Option(None, "--implementation", "-i", help="Implementation name"),
) -> None:
    """Evaluate functional equivalence between two output directories."""
    from intentc.differencing.differencing import run_differencing

    cwd = Path.cwd()
    intent_dir = cwd / "intent"
    project = _load_project_or_exit(intent_dir)
    config = load_config(cwd)

    agent_profile = _resolve_profile(profile, config)

    impl = None
    if implementation:
        impl = project.resolve_implementation(implementation)

    result = run_differencing(
        project=project,
        agent_profile=agent_profile,
        dir_a=dir_a,
        dir_b=dir_b,
        implementation=impl,
    )

    render_compare_result(result)
    if result.status != "equivalent":
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
