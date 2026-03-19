"""CLI entry point for intentc — thin wrappers over core workflows."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Optional

import typer

from intentc.build.agents import AgentProfile, BuildContext, create_from_profile
from intentc.build.builder.builder import Builder, BuildOptions
from intentc.build.state import GitVersionControl, StateManager, TargetStatus
from intentc.build.validations import ValidationSuiteResult
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
from intentc.core.project import blank_project, load_project, write_project
from intentc.core.types import IntentFile

app = typer.Typer(
    name="intentc",
    help="A compiler of intent — transforms specs into working code using AI agents.",
    no_args_is_help=True,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_profile(profile_name: str | None, config: Config) -> AgentProfile:
    """Resolve agent profile: flag override > config default."""
    if profile_name:
        return AgentProfile(
            name=profile_name,
            provider=config.default_profile.provider,
            timeout=config.default_profile.timeout,
            retries=config.default_profile.retries,
        )
    return config.default_profile


def _load_project_or_exit():
    """Load the project or exit with code 2."""
    try:
        return load_project(Path("intent"))
    except Exception as exc:
        print_error(str(exc))
        raise typer.Exit(code=2)


def _make_builder(
    project,
    config: Config,
    profile_name: str | None,
    output_dir: str | None,
) -> tuple[Builder, str]:
    """Wire up a Builder with its dependencies. Returns (builder, resolved_output_dir)."""
    resolved_output = output_dir or config.default_output_dir
    agent_profile = _resolve_profile(profile_name, config)
    state_mgr = StateManager(Path("."), resolved_output)
    vcs = GitVersionControl(Path(resolved_output))
    builder = Builder(
        project=project,
        state_manager=state_mgr,
        version_control=vcs,
        agent_profile=agent_profile,
    )
    return builder, resolved_output


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def init(
    name: Annotated[
        Optional[str],
        typer.Argument(help="Project name (defaults to current directory name)."),
    ] = None,
) -> None:
    """Create a new intentc project in the current directory."""
    intent_dir = Path("intent")
    if (intent_dir / "project.ic").exists():
        print_error(
            "An intentc project already exists here (intent/project.ic). "
            "Remove it first if you want to reinitialize."
        )
        raise typer.Exit(code=2)

    project_name = name or Path.cwd().name
    project = blank_project(project_name)
    write_project(project, intent_dir)

    config = Config()
    config_path = save_config(config, Path("."))

    created_files = [
        "intent/project.ic",
        "intent/starter/starter.ic",
        str(config_path),
    ]
    render_init_summary(created_files)


@app.command()
def build(
    target: Annotated[
        Optional[str],
        typer.Argument(help="Feature path to build. Builds all pending/outdated if omitted."),
    ] = None,
    force: Annotated[bool, typer.Option("--force", "-f", help="Rebuild even if already built.")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", "-n", help="Print the build plan without executing.")] = False,
    output_dir: Annotated[Optional[str], typer.Option("--output-dir", "-o", help="Override the output directory.")] = None,
    profile: Annotated[Optional[str], typer.Option("--profile", "-p", help="Agent profile name override.")] = None,
    implementation: Annotated[Optional[str], typer.Option("--implementation", "-i", help="Implementation name to use (from implementations/ directory).")] = None,
) -> None:
    """Build features using the configured agent."""
    project = _load_project_or_exit()
    config = load_config(Path("."))
    builder, resolved_output = _make_builder(project, config, profile, output_dir)

    opts = BuildOptions(
        target=target or "",
        force=force,
        dry_run=dry_run,
        output_dir=resolved_output,
        implementation=implementation or "",
    )
    results, err = builder.build(opts)
    render_build_results(results, dry_run=dry_run)

    if err is not None:
        print_error(str(err))
        raise typer.Exit(code=1)


@app.command()
def validate(
    target: Annotated[
        Optional[str],
        typer.Argument(help="Feature to validate. Validates entire project if omitted."),
    ] = None,
    output_dir: Annotated[Optional[str], typer.Option("--output-dir", "-o", help="Override the output directory.")] = None,
    profile: Annotated[Optional[str], typer.Option("--profile", "-p", help="Agent profile override.")] = None,
    implementation: Annotated[Optional[str], typer.Option("--implementation", "-i", help="Implementation name to use.")] = None,
) -> None:
    """Run validations independently of the build pipeline."""
    project = _load_project_or_exit()
    config = load_config(Path("."))
    builder, resolved_output = _make_builder(project, config, profile, output_dir)

    result = builder.validate(target, resolved_output, implementation=implementation)

    if isinstance(result, list):
        render_validation_results(result)
        has_error = any(not r.passed for r in result)
    else:
        render_validation_result(result)
        has_error = not result.passed

    if has_error:
        raise typer.Exit(code=1)


@app.command()
def clean(
    target: Annotated[
        Optional[str],
        typer.Argument(help="Feature path to clean."),
    ] = None,
    all_targets: Annotated[bool, typer.Option("--all", help="Reset all state for the output directory.")] = False,
    output_dir: Annotated[Optional[str], typer.Option("--output-dir", "-o", help="Override the output directory.")] = None,
) -> None:
    """Revert a target's generated code and reset its state."""
    if not all_targets and not target:
        print_error("Provide a target or use --all to reset everything.")
        raise typer.Exit(code=2)

    project = _load_project_or_exit()
    config = load_config(Path("."))
    builder, resolved_output = _make_builder(project, config, None, output_dir)

    if all_targets:
        builder.clean_all(resolved_output)
        console.print("[green]All state reset.[/green]")
    else:
        builder.clean(target, resolved_output)
        console.print(f"[green]Cleaned target '{target}'.[/green]")


@app.command()
def plan(
    target: Annotated[
        str,
        typer.Argument(help="Feature path to plan."),
    ],
    output_dir: Annotated[Optional[str], typer.Option("--output-dir", "-o", help="Override the output directory.")] = None,
    profile: Annotated[Optional[str], typer.Option("--profile", "-p", help="Agent profile override.")] = None,
    implementation: Annotated[Optional[str], typer.Option("--implementation", "-i", help="Implementation name to use.")] = None,
) -> None:
    """Enter interactive planning mode with the agent for a specific feature."""
    project = _load_project_or_exit()
    config = load_config(Path("."))
    resolved_output = output_dir or config.default_output_dir
    agent_profile = _resolve_profile(profile, config)
    agent = create_from_profile(agent_profile)

    if target not in project.features:
        print_error(
            f"Feature '{target}' not found. "
            f"Available: {', '.join(sorted(project.features))}"
        )
        raise typer.Exit(code=2)

    resolved_impl = project.resolve_implementation(implementation)
    node = project.features[target]
    intent = node.intents[0] if node.intents else IntentFile(name=target)
    ctx = BuildContext(
        intent=intent,
        validations=node.validations,
        output_dir=resolved_output,
        generation_id="plan",
        dependency_names=list(node.depends_on),
        project_intent=project.project_intent,
        implementation=resolved_impl,
        response_file_path="",
    )
    agent.plan(ctx)


@app.command()
def status(
    output_dir: Annotated[Optional[str], typer.Option("--output-dir", "-o", help="Override the output directory.")] = None,
    outdated: Annotated[bool, typer.Option("--outdated", help="Check for targets with stale source files.")] = False,
) -> None:
    """Show the build state for all tracked targets."""
    config = load_config(Path("."))
    resolved_output = output_dir or config.default_output_dir
    state_mgr = StateManager(Path("."), resolved_output)

    targets = state_mgr.list_targets()
    results = {
        name: state_mgr.get_build_result(name)
        for name, _ in targets
    }
    results = {k: v for k, v in results.items() if v is not None}

    outdated_list = None
    if outdated:
        project = _load_project_or_exit()
        agent_profile = _resolve_profile(None, config)
        vcs = GitVersionControl(Path(resolved_output))
        builder = Builder(
            project=project,
            state_manager=state_mgr,
            version_control=vcs,
            agent_profile=agent_profile,
        )
        outdated_list = builder.detect_outdated()

    render_status_table(targets, results, outdated_list)


@app.command()
def diff(
    target: Annotated[
        str,
        typer.Argument(help="Feature path to diff."),
    ],
    output_dir: Annotated[Optional[str], typer.Option("--output-dir", "-o", help="Override the output directory.")] = None,
) -> None:
    """Show the diff of what was generated for a target."""
    config = load_config(Path("."))
    resolved_output = output_dir or config.default_output_dir
    state_mgr = StateManager(Path("."), resolved_output)

    result = state_mgr.get_build_result(target)
    if result is None:
        print_error(f"No build result found for target '{target}'.")
        raise typer.Exit(code=2)

    if not result.commit_id:
        print_error(f"Target '{target}' has no commit ID (build may have failed).")
        raise typer.Exit(code=2)

    vcs = GitVersionControl(Path(resolved_output))
    diff_text = vcs.diff(f"{result.commit_id}~1", result.commit_id)
    render_diff(diff_text)


@app.command()
def compare(
    dir_a: Annotated[
        str,
        typer.Argument(help="Path to the reference output directory."),
    ],
    dir_b: Annotated[
        str,
        typer.Argument(help="Path to the candidate output directory."),
    ],
    profile: Annotated[Optional[str], typer.Option("--profile", "-p", help="Agent profile override.")] = None,
    implementation: Annotated[Optional[str], typer.Option("--implementation", "-i", help="Implementation name to use.")] = None,
) -> None:
    """Evaluate functional equivalence between two output directories."""
    from intentc.differencing.differencing import run_differencing

    path_a = Path(dir_a)
    path_b = Path(dir_b)

    if not path_a.is_dir():
        print_error(f"Reference directory does not exist: {dir_a}")
        raise typer.Exit(code=2)
    if not path_b.is_dir():
        print_error(f"Candidate directory does not exist: {dir_b}")
        raise typer.Exit(code=2)

    project = _load_project_or_exit()
    config = load_config(Path("."))
    agent_profile = _resolve_profile(profile, config)

    try:
        result = run_differencing(dir_a, dir_b, project, agent_profile, implementation=implementation)
    except Exception as exc:
        print_error(str(exc))
        raise typer.Exit(code=1)

    render_compare_result(result)

    if result.status != "equivalent":
        raise typer.Exit(code=1)
