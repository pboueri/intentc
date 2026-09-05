"""The `intentc` CLI: a thin wrapper over the core workflows.

Every command wires its own dependencies at the call site (project, config,
agent profile, storage, state manager, version control, builder) and defers
all logic to the underlying modules. No shared state between commands.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from intentc.build.agents import AgentError, BuildContext, create_from_profile
from intentc.build.builder.builder import Builder, BuildOptions
from intentc.build.state import GitVersionControl, StateManager
from intentc.build.validations import ValidationSuite, ValidationSuiteResult
from intentc.cli import output as out
from intentc.cli.config import Config, ConfigError, load_config, save_config
from intentc.core import (
    FeatureNode,
    IntentFile,
    ParseErrors,
    Project,
    blank_project,
    check_project,
    load_project,
    write_intent_file,
    write_project,
)

app = typer.Typer(
    name="intentc",
    help="A compiler of intent — transforms specs into working code using AI agents.",
    no_args_is_help=True,
    add_completion=False,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _load_project_or_exit(intent_dir: Path) -> Project:
    if not intent_dir.exists():
        out.print_error(
            f"No intent/ directory found in {Path.cwd()}. Run 'intentc init' to start a project."
        )
        raise typer.Exit(code=2)
    try:
        return load_project(intent_dir)
    except ParseErrors as exc:
        console = Console(stderr=True, soft_wrap=True)
        console.print(f"{len(exc.errors)} problem(s) in intent/:")
        for error in exc.errors:
            console.print(str(error))
        raise typer.Exit(code=2) from exc


def _require_target(project: Project, target: Optional[str], allow_project: bool = False) -> None:
    if not target:
        return
    if allow_project and target == "project":
        return
    if target not in project.features:
        available = ", ".join(sorted(project.features)) if project.features else "(none)"
        out.print_error(f"Unknown feature '{target}'. Available: {available}")
        raise typer.Exit(code=2)


def _load_config_or_exit(project_root: Path) -> Config:
    try:
        return load_config(project_root)
    except ConfigError as exc:
        out.print_error(str(exc))
        raise typer.Exit(code=2) from exc


def _implementation_error_hint(project: Project) -> str:
    return ", ".join(sorted(project.implementations)) if project.implementations else "(none)"


def _resolve_implementation_or_exit(project: Project, name: Optional[str]):
    try:
        return project.resolve_implementation(name or None)
    except KeyError as exc:
        out.print_error(
            f"Unknown implementation '{name}'. Available: {_implementation_error_hint(project)}"
        )
        raise typer.Exit(code=2) from exc
    except ValueError as exc:
        out.print_error(f"{exc} Available: {_implementation_error_hint(project)}")
        raise typer.Exit(code=2) from exc


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


@app.command()
def init(
    name: Optional[str] = typer.Argument(
        None, help="Project name (defaults to the current directory name)."
    ),
) -> None:
    """Create a new intentc project in the current directory."""
    cwd = Path.cwd()
    intent_dir = cwd / "intent"
    project_ic = intent_dir / "project.ic"
    if project_ic.exists():
        out.print_error(f"{project_ic} already exists; refusing to overwrite an existing project.")
        raise typer.Exit(code=2)

    project = blank_project(name or cwd.name)
    write_project(project, intent_dir)
    config_path = save_config(Config(), cwd)

    created: list[Path] = [intent_dir / "project.ic"]
    for implementation in project.implementations.values():
        created.append(intent_dir / "implementations" / f"{implementation.name}.ic")
    for feature_path, node in project.features.items():
        for intent in node.intents:
            created.append(intent_dir / feature_path / f"{intent.name}.ic")
        for index, _vf in enumerate(node.validations):
            filename = "validation.icv" if index == 0 else f"validation_{index}.icv"
            created.append(intent_dir / feature_path / filename)
    created.append(config_path)

    out.render_init_summary(created)


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


@app.command()
def check(
    strict: bool = typer.Option(False, "--strict", help="Treat warnings as errors."),
) -> None:
    """Lint the intent project without building anything."""
    project = _load_project_or_exit(Path.cwd() / "intent")

    issues = check_project(project)
    out.render_check_results(issues, total_features=len(project.features))
    out.render_dag(project)

    errors = [issue for issue in issues if issue.level == "error"]
    warnings = [issue for issue in issues if issue.level == "warning"]
    if errors or (strict and warnings):
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


@app.command()
def build(
    target: Optional[str] = typer.Argument(
        None, help="Feature path to build. Builds all pending/outdated targets if omitted."
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Rebuild even if already built."),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Print the build plan without executing."),
    output_dir: Optional[str] = typer.Option(
        None, "--output-dir", "-o", help="Override the output directory."
    ),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Agent profile name override."),
    implementation: Optional[str] = typer.Option(
        None, "--implementation", "-i", help="Implementation name to use."
    ),
) -> None:
    """Build features using the configured agent."""
    cwd = Path.cwd()
    project = _load_project_or_exit(cwd / "intent")

    issues = check_project(project)
    errors = [issue for issue in issues if issue.level == "error"]
    for issue in issues:
        if issue.level == "warning":
            out.print_warning(str(issue))
    if errors:
        for issue in errors:
            out.print_error(str(issue))
        raise typer.Exit(code=2)

    config = _load_config_or_exit(cwd)
    _require_target(project, target)

    resolved_output_dir = output_dir or config.default_output_dir
    state_manager = StateManager(base_dir=cwd, output_dir=resolved_output_dir)
    version_control = GitVersionControl(cwd, output_dir=resolved_output_dir)

    console = Console()
    builder = Builder(
        project=project,
        state_manager=state_manager,
        version_control=version_control,
        agent_profile=config.default_profile,
        log=out.timestamped_log(console),
    )

    opts = BuildOptions(
        target=target or "",
        force=force,
        dry_run=dry_run,
        output_dir=resolved_output_dir,
        profile_override=profile or "",
        implementation=implementation or "",
    )

    try:
        results, error = builder.build(opts)
    except AgentError as exc:
        out.print_error(f"Agent error: {exc}")
        raise typer.Exit(code=1) from exc
    except KeyError as exc:
        out.print_error(
            f"Unknown implementation '{implementation}'. Available: {_implementation_error_hint(project)}"
        )
        raise typer.Exit(code=2) from exc
    except ValueError as exc:
        out.print_error(f"{exc} Available: {_implementation_error_hint(project)}")
        raise typer.Exit(code=2) from exc
    except RuntimeError as exc:
        out.print_error(
            f"{exc} — intentc build checkpoints into git — run 'git init' and commit once first"
        )
        raise typer.Exit(code=2) from exc

    if not results and error is None:
        console.print("Nothing to build — all targets are up to date. Use --force to rebuild.")
        return

    if dry_run:
        out.render_build_plan(results, console=console)
        return

    out.render_build_results(results, console=console)

    if error is not None:
        failed_result = results[-1] if results else None
        retry_target = failed_result.target if failed_result is not None else (target or "")
        if failed_result is not None:
            failing_step = next((step for step in failed_result.steps if step.status == "failed"), None)
            if failing_step is not None:
                console.print(f"[red]{failing_step.phase}[/red]: {failing_step.summary}")
                if failing_step.phase == "validate":
                    for entry in state_manager.backend.get_validation_results(failed_result.target):
                        if entry.get("status") != "pass":
                            console.print(f"  - {entry.get('name')}: {entry.get('reason')}")
        console.print(f"Fix the intent or the output, then run: intentc build {retry_target}")
        raise typer.Exit(code=1)

    next_targets = builder.next_targets()
    if next_targets:
        console.print(f"Next you can build: {', '.join(next_targets)}")
    else:
        console.print("All targets built.")


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


@app.command()
def validate(
    target: Optional[str] = typer.Argument(
        None, help="Feature to validate. Validates the whole project if omitted."
    ),
    output_dir: Optional[str] = typer.Option(
        None, "--output-dir", "-o", help="Override the output directory."
    ),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Agent profile override."),
    implementation: Optional[str] = typer.Option(
        None, "--implementation", "-i", help="Implementation name to use."
    ),
) -> None:
    """Run validations independently of the build pipeline."""
    cwd = Path.cwd()
    project = _load_project_or_exit(cwd / "intent")
    config = _load_config_or_exit(cwd)

    agent_profile = config.default_profile
    if profile:
        agent_profile = agent_profile.model_copy(update={"name": profile})

    implementation_obj = _resolve_implementation_or_exit(project, implementation) if implementation else None
    _require_target(project, target, allow_project=True)

    resolved_output_dir = output_dir or config.default_output_dir
    state_manager = StateManager(base_dir=cwd, output_dir=resolved_output_dir)
    version_control = GitVersionControl(cwd, output_dir=resolved_output_dir)

    console = Console()
    builder = Builder(
        project=project,
        state_manager=state_manager,
        version_control=version_control,
        agent_profile=agent_profile,
        log=console.print,
    )

    try:
        if target == "project":
            suite = ValidationSuite(
                project=project,
                agent_profile=agent_profile,
                output_dir=resolved_output_dir,
                storage_backend=state_manager.backend,
                val_response_dir=state_manager.val_response_dir,
                log=console.print,
                implementation=implementation_obj or project.resolve_implementation(None),
            )
            assertion_entries = [v for vf in project.assertions for v in vf.validations]
            result = suite.validate_entries("project", assertion_entries)
        else:
            result = builder.validate(target or "", resolved_output_dir)
    except AgentError as exc:
        out.print_error(f"Agent error: {exc}")
        raise typer.Exit(code=1) from exc

    results: list[ValidationSuiteResult] = result if isinstance(result, list) else [result]

    total_errors = 0
    total_warnings = 0
    total_passed = 0
    total_count = 0
    for suite_result in results:
        out.render_validation_results(suite_result, console=console)
        total_errors += suite_result.error_count
        total_warnings += suite_result.warning_count
        total_passed += suite_result.passed_count
        total_count += len(suite_result.results)

    console.print(f"{total_passed}/{total_count} passed, {total_errors} error(s), {total_warnings} warning(s)")

    if total_errors:
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# clean
# ---------------------------------------------------------------------------


@app.command()
def clean(
    target: Optional[str] = typer.Argument(None, help="Feature path to clean."),
    all_targets: bool = typer.Option(
        False, "--all", help="Reset all state for the output directory."
    ),
    output_dir: Optional[str] = typer.Option(
        None, "--output-dir", "-o", help="Override the output directory."
    ),
) -> None:
    """Revert a target's generated code and reset its state."""
    cwd = Path.cwd()
    project = _load_project_or_exit(cwd / "intent")
    config = _load_config_or_exit(cwd)

    if not all_targets and not target:
        out.print_error("A target is required unless --all is given.")
        raise typer.Exit(code=2)

    _require_target(project, target)

    resolved_output_dir = output_dir or config.default_output_dir
    state_manager = StateManager(base_dir=cwd, output_dir=resolved_output_dir)
    version_control = GitVersionControl(cwd, output_dir=resolved_output_dir)

    console = Console()
    builder = Builder(
        project=project,
        state_manager=state_manager,
        version_control=version_control,
        agent_profile=config.default_profile,
        log=console.print,
    )

    if all_targets:
        builder.clean_all(resolved_output_dir)
        console.print(f"Cleared all build state for '{resolved_output_dir}'.")
    else:
        assert target is not None
        builder.clean(target, resolved_output_dir)
        console.print(f"Cleaned '{target}'.")


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


@app.command()
def plan(
    target: str = typer.Argument(..., help="Feature path to plan."),
    prompt: str = typer.Argument(..., help="Seed prompt describing what to plan for this feature."),
    output_dir: Optional[str] = typer.Option(
        None, "--output-dir", "-o", help="Override the output directory."
    ),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Agent profile override."),
    implementation: Optional[str] = typer.Option(
        None, "--implementation", "-i", help="Implementation name to use."
    ),
) -> None:
    """Enter interactive planning mode with the agent for a specific feature."""
    cwd = Path.cwd()
    project = _load_project_or_exit(cwd / "intent")
    config = _load_config_or_exit(cwd)

    agent_profile = config.default_profile
    if profile:
        agent_profile = agent_profile.model_copy(update={"name": profile})

    implementation_obj = _resolve_implementation_or_exit(project, implementation)

    console = Console()

    if target not in project.features:
        feature_name = target.rsplit("/", 1)[-1]
        new_intent = IntentFile(name=feature_name)
        intent_path = cwd / "intent" / target / f"{feature_name}.ic"
        new_intent.source_path = write_intent_file(new_intent, intent_path)
        console.print(f"Created {intent_path}")
        project.features[target] = FeatureNode(path=target, intents=[new_intent], validations=[])

    node = project.features[target]
    feature_intent = node.intents[0] if node.intents else IntentFile(name=target)

    ctx = BuildContext(
        intent=feature_intent,
        validations=node.validations,
        output_dir=output_dir or config.default_output_dir,
        generation_id="plan",
        dependency_names=node.depends_on,
        project_intent=project.project_intent,
        implementation=implementation_obj,
        response_file_path="",
        seed_prompt=prompt,
        feature_path=target,
    )

    agent = create_from_profile(agent_profile, log=console.print)
    try:
        agent.plan(ctx)
    except AgentError as exc:
        out.print_error(f"Agent error: {exc}")
        raise typer.Exit(code=1) from exc


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@app.command()
def status(
    output_dir: Optional[str] = typer.Option(
        None, "--output-dir", "-o", help="Override the output directory."
    ),
    outdated: bool = typer.Option(False, "--outdated", help="Show only outdated targets."),
) -> None:
    """Show the build state for all tracked targets."""
    cwd = Path.cwd()
    project = _load_project_or_exit(cwd / "intent")
    config = _load_config_or_exit(cwd)

    resolved_output_dir = output_dir or config.default_output_dir
    state_manager = StateManager(base_dir=cwd, output_dir=resolved_output_dir)
    version_control = GitVersionControl(cwd, output_dir=resolved_output_dir)

    console = Console()
    builder = Builder(
        project=project,
        state_manager=state_manager,
        version_control=version_control,
        agent_profile=config.default_profile,
    )
    builder.refresh_outdated()

    db_targets = dict(state_manager.list_targets())
    rows: list[out.StatusRow] = []

    for feature in project.topological_order():
        node = project.features[feature]
        db_status = db_targets.pop(feature, None)
        validations_count = sum(len(vf.validations) for vf in node.validations)
        if db_status is None:
            rows.append((feature, "pending", node.depends_on, validations_count, "", 0, ""))
            continue
        result = state_manager.get_build_result(feature)
        rows.append(
            (
                feature,
                db_status.value,
                node.depends_on,
                validations_count,
                result.timestamp if result else "",
                result.attempts if result else 0,
                result.generation_id if result else "",
            )
        )

    for removed_target, removed_status in db_targets.items():
        rows.append((f"{removed_target} (removed from intent/)", removed_status.value, [], 0, "", 0, ""))

    if outdated:
        rows = [row for row in rows if row[1] == "outdated"]

    out.render_status_table(rows, console=console)

    next_targets = builder.next_targets()
    built_count = sum(1 for row in rows if row[1] == "built")
    if next_targets:
        console.print(f"Next you can build: {', '.join(next_targets)}")
    elif built_count == 0:
        console.print("Nothing built yet — run: intentc build")
    else:
        console.print("All targets built.")


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


@app.command()
def diff(
    target: str = typer.Argument(..., help="Feature path."),
    output_dir: Optional[str] = typer.Option(
        None, "--output-dir", "-o", help="Override the output directory."
    ),
    stat: bool = typer.Option(False, "--stat", help="File list only, no patch."),
) -> None:
    """Show the diff of what was generated for a target."""
    cwd = Path.cwd()
    config = _load_config_or_exit(cwd)

    resolved_output_dir = output_dir or config.default_output_dir
    state_manager = StateManager(base_dir=cwd, output_dir=resolved_output_dir)

    result = state_manager.get_build_result(target)
    if result is None:
        out.print_error(
            f"No build recorded for '{target}' in {resolved_output_dir}. Run: intentc build {target}"
        )
        raise typer.Exit(code=2)

    version_control = GitVersionControl(cwd, output_dir=resolved_output_dir)
    diff_text = version_control.diff(f"{result.commit_id}~1", result.commit_id)

    out.render_diff(
        target=target,
        commit_id=result.commit_id,
        timestamp=result.timestamp,
        files_created=result.files_created,
        files_modified=result.files_modified,
        diff_text=diff_text,
        stat_only=stat,
    )


# ---------------------------------------------------------------------------
# log
# ---------------------------------------------------------------------------


@app.command(name="log")
def log_command(
    target: str = typer.Argument(..., help="Feature path."),
    output_dir: Optional[str] = typer.Option(
        None, "--output-dir", "-o", help="Override the output directory."
    ),
    limit: int = typer.Option(10, "--limit", "-n", help="Number of history rows."),
) -> None:
    """Show the build history of a target."""
    cwd = Path.cwd()
    config = _load_config_or_exit(cwd)

    resolved_output_dir = output_dir or config.default_output_dir
    state_manager = StateManager(base_dir=cwd, output_dir=resolved_output_dir)

    history = state_manager.get_build_history(target, limit=limit)
    if not history:
        out.print_error(
            f"No build recorded for '{target}' in {resolved_output_dir}. Run: intentc build {target}"
        )
        raise typer.Exit(code=2)

    validation_results = state_manager.backend.get_validation_results(target)
    out.render_build_log(target, history, validation_results)


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


@app.command()
def compare(
    dir_a: str = typer.Argument(..., help="Path to the reference output directory."),
    dir_b: str = typer.Argument(..., help="Path to the candidate output directory."),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Agent profile override."),
    implementation: Optional[str] = typer.Option(
        None, "--implementation", "-i", help="Implementation name to use."
    ),
) -> None:
    """Evaluate functional equivalence between two output directories."""
    cwd = Path.cwd()
    project = _load_project_or_exit(cwd / "intent")
    config = _load_config_or_exit(cwd)

    agent_profile = config.default_profile
    if profile:
        agent_profile = agent_profile.model_copy(update={"name": profile})

    if not Path(dir_a).is_dir():
        out.print_error(f"Directory not found: {dir_a}")
        raise typer.Exit(code=2)
    if not Path(dir_b).is_dir():
        out.print_error(f"Directory not found: {dir_b}")
        raise typer.Exit(code=2)

    try:
        from intentc.differencing import run_differencing
    except ImportError as exc:
        out.print_error(f"compare requires the differencing module: {exc}")
        raise typer.Exit(code=2) from exc

    try:
        response = run_differencing(
            dir_a, dir_b, project, agent_profile, implementation=implementation or None
        )
    except AgentError as exc:
        out.print_error(f"Agent error: {exc}")
        raise typer.Exit(code=1) from exc
    except KeyError as exc:
        out.print_error(
            f"Unknown implementation '{implementation}'. Available: {_implementation_error_hint(project)}"
        )
        raise typer.Exit(code=2) from exc
    except ValueError as exc:
        out.print_error(f"{exc} Available: {_implementation_error_hint(project)}")
        raise typer.Exit(code=2) from exc

    out.render_compare_results(response)
    raise typer.Exit(code=0 if response.status == "equivalent" else 1)


if __name__ == "__main__":
    app()
