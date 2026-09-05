"""intentc command-line interface — thin wrappers that wire dependencies and defer to workflows."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator, Optional

import typer

from intentc.build.agents import AgentError, AgentProfile, BuildContext, create_from_profile
from intentc.cli.config import Config, ConfigError, load_config, save_config
from intentc.cli.output import (
    console,
    print_error,
    print_hint,
    print_warning,
    render_build_failure,
    render_build_log,
    render_build_plan,
    render_build_results,
    render_check_results,
    render_compare_results,
    render_dag,
    render_diff,
    render_init_summary,
    render_next_targets,
    render_status_table,
    render_validation_results,
)
from intentc.core import (
    FeatureNode,
    IntentFile,
    ParseErrors,
    Project,
    check_project,
    load_project,
    write_intent_file,
    write_project,
)
from intentc.core.project import blank_project

app = typer.Typer(
    name="intentc",
    help="A compiler of intent — transforms specs into working code using AI agents.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)

EXIT_FAILURE = 1
EXIT_USAGE = 2

OutputDirOption = typer.Option(None, "--output-dir", "-o", help="Override the output directory (default from config)")
ProfileOption = typer.Option(None, "--profile", "-p", help="Agent profile name override")
ImplementationOption = typer.Option(None, "--implementation", "-i", help="Implementation name from intent/implementations/")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_log() -> Callable[[str], None]:
    def _log(message: str) -> None:
        console.print(f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim] {message}")

    return _log


def _load_project_or_exit(intent_dir: Path) -> Project:
    if not intent_dir.is_dir():
        print_error(f"No intent/ directory found in {intent_dir.parent}. Run 'intentc init' to start a project.")
        raise typer.Exit(code=EXIT_USAGE)
    try:
        return load_project(intent_dir)
    except ParseErrors as exc:
        print_error(f"{len(exc.errors)} problem(s) in {intent_dir}:")
        for err in exc.errors:
            typer.echo(f"  {err}", err=True)
        raise typer.Exit(code=EXIT_USAGE)


def _load_config_or_exit(root: Path) -> Config:
    try:
        return load_config(root)
    except ConfigError as exc:
        print_error(str(exc))
        print_hint("Fix .intentc/config.yaml or delete it to use the defaults.")
        raise typer.Exit(code=EXIT_USAGE)


def _require_target(project: Project, target: str) -> None:
    if target == "project":
        return
    try:
        project._require_feature(target)
    except KeyError as exc:
        print_error(f"Unknown feature '{target}'. {str(exc.args[0]).split('. ', 1)[-1]}")
        raise typer.Exit(code=EXIT_USAGE)


def _check_or_exit(project: Project, strict: bool = False) -> None:
    issues = check_project(project)
    errors = [i for i in issues if i.level == "error"]
    warnings = [i for i in issues if i.level != "error"]
    if errors or (strict and warnings):
        render_check_results(issues, len(project.features))
        print_hint("Fix the problems above (see 'intentc check') before building.")
        raise typer.Exit(code=EXIT_USAGE)
    for issue in warnings:
        print_warning(f"{issue.path}: {issue.message}" if issue.path else issue.message)


def _resolve_output_dir(flag: str | None, config: Config) -> str:
    return flag or config.default_output_dir


def _resolve_profile(flag: str | None, config: Config) -> AgentProfile:
    if flag and flag != config.default_profile.name:
        return config.default_profile.model_copy(update={"name": flag})
    return config.default_profile


def _resolve_implementation_or_exit(project: Project, name: str | None):
    try:
        return project.resolve_implementation(name)
    except (KeyError, ValueError) as exc:
        print_error(str(exc.args[0]) if exc.args else str(exc))
        raise typer.Exit(code=EXIT_USAGE)


@contextmanager
def _expected_failures() -> Iterator[None]:
    """Turn expected runtime problems into short messages and exit codes, never tracebacks."""
    try:
        yield
    except AgentError as exc:
        print_error(f"Agent error: {exc}")
        raise typer.Exit(code=EXIT_FAILURE)
    except RuntimeError as exc:
        message = str(exc)
        print_error(message)
        if "git" in message.lower():
            print_hint("intentc build checkpoints into git — run 'git init' and commit once first.")
        raise typer.Exit(code=EXIT_USAGE)


def _make_builder(project: Project, root: Path, output_dir: str, profile: AgentProfile, log: Callable[[str], None] | None):
    from intentc.build.builder.builder import Builder
    from intentc.build.state import GitVersionControl, StateManager

    state = StateManager(root, output_dir)
    builder = Builder(project, state, GitVersionControl(root, output_dir), profile, log=log)
    return builder, state


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


@app.command()
def init(
    name: Optional[str] = typer.Argument(None, help="Project name (default: current directory name)"),
    no_interactive: bool = typer.Option(False, "--no-interactive", help="Skip the agent dialog and write a minimal skeleton"),
    prompt: Optional[str] = typer.Option(None, "-P", "--prompt", help="Project description for single-shot (non-interactive) init"),
) -> None:
    """Create a new intentc project in the current directory."""
    root = Path.cwd()
    intent_dir = root / "intent"
    if (intent_dir / "project.ic").exists():
        print_error(f"A project already exists here ({intent_dir / 'project.ic'}). Not overwriting.")
        raise typer.Exit(code=EXIT_USAGE)
    if no_interactive and prompt is not None:
        print_error("--no-interactive and --prompt are mutually exclusive.")
        raise typer.Exit(code=EXIT_USAGE)

    project_name = name or root.name
    write_project(blank_project(project_name), intent_dir)

    if not no_interactive:
        profile = AgentProfile(name="default", provider="claude")
        log = _make_log()
        with _expected_failures():
            agent = create_from_profile(profile, log=log)
            agent.init(project_name, str(intent_dir), prompt=prompt)
        try:
            project = load_project(intent_dir)
        except ParseErrors as exc:
            print_error(f"The agent left {len(exc.errors)} problem(s) in intent/:")
            for err in exc.errors:
                typer.echo(f"  {err}", err=True)
            raise typer.Exit(code=EXIT_FAILURE)
        issues = check_project(project)
        if issues:
            render_check_results(issues, len(project.features))
            if any(i.level == "error" for i in issues):
                raise typer.Exit(code=EXIT_FAILURE)

    config_path = save_config(Config(), root)
    created = [str(p.relative_to(root)) for p in sorted(intent_dir.rglob("*")) if p.is_file()]
    created.append(str(config_path.relative_to(root)))
    render_init_summary(created)


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


@app.command()
def check(
    strict: bool = typer.Option(False, "--strict", help="Treat warnings as errors"),
) -> None:
    """Lint the intent project (parse, dependencies, cycles, validations) without building."""
    root = Path.cwd()
    project = _load_project_or_exit(root / "intent")
    issues = check_project(project)
    render_check_results(issues, len(project.features))
    console.print()
    render_dag(project)
    has_errors = any(i.level == "error" for i in issues)
    if has_errors or (strict and issues):
        raise typer.Exit(code=EXIT_FAILURE)


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


@app.command()
def build(
    target: Optional[str] = typer.Argument(None, help="Feature path to build (with its dependencies); omit for everything pending"),
    force: bool = typer.Option(False, "--force", "-f", help="Rebuild even if already built"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Print the build plan without executing"),
    output_dir: Optional[str] = OutputDirOption,
    profile: Optional[str] = ProfileOption,
    implementation: Optional[str] = ImplementationOption,
) -> None:
    """Build features using the configured agent."""
    from intentc.build.builder.builder import BuildOptions
    from intentc.build.state import TargetStatus

    root = Path.cwd()
    project = _load_project_or_exit(root / "intent")
    _check_or_exit(project)
    config = _load_config_or_exit(root)
    if target:
        _require_target(project, target)
    if implementation:
        _resolve_implementation_or_exit(project, implementation)

    resolved_output = _resolve_output_dir(output_dir, config)
    resolved_profile = _resolve_profile(profile, config)
    builder, _ = _make_builder(project, root, resolved_output, resolved_profile, _make_log())
    opts = BuildOptions(
        target=target or "",
        force=force,
        dry_run=dry_run,
        output_dir=resolved_output,
        profile_override=profile or "",
        implementation=implementation or "",
    )
    with _expected_failures():
        results, error = builder.build(opts)

    if dry_run:
        if results:
            render_build_plan(results)
        else:
            console.print("Nothing to build — all targets are up to date. Use --force to rebuild.")
        return

    if not results:
        console.print("Nothing to build — all targets are up to date. Use --force to rebuild.")
        render_next_targets(builder.next_targets())
        return

    render_build_results(results)
    if error is not None:
        failed = next((r for r in results if r.status == TargetStatus.FAILED), None)
        if failed is not None:
            render_build_failure(failed)
        raise typer.Exit(code=EXIT_FAILURE)
    render_next_targets(builder.next_targets())


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


@app.command()
def validate(
    target: Optional[str] = typer.Argument(None, help="Feature to validate ('project' for assertions only); omit for everything"),
    output_dir: Optional[str] = OutputDirOption,
    profile: Optional[str] = ProfileOption,
    implementation: Optional[str] = ImplementationOption,
) -> None:
    """Run validations independently of the build pipeline."""
    from intentc.build.validations import ValidationSuiteResult

    root = Path.cwd()
    project = _load_project_or_exit(root / "intent")
    config = _load_config_or_exit(root)
    if target:
        _require_target(project, target)
    if implementation:
        _resolve_implementation_or_exit(project, implementation)

    resolved_output = _resolve_output_dir(output_dir, config)
    builder, _ = _make_builder(project, root, resolved_output, _resolve_profile(profile, config), _make_log())
    with _expected_failures():
        result = builder.validate(target, resolved_output)
    results = [result] if isinstance(result, ValidationSuiteResult) else list(result)
    render_validation_results(results)
    if any(not r.passed for r in results):
        raise typer.Exit(code=EXIT_FAILURE)


# ---------------------------------------------------------------------------
# clean
# ---------------------------------------------------------------------------


@app.command()
def clean(
    target: Optional[str] = typer.Argument(None, help="Feature path to clean"),
    all_targets: bool = typer.Option(False, "--all", help="Reset all build state for the output directory"),
    output_dir: Optional[str] = OutputDirOption,
) -> None:
    """Revert a target's generated code and reset its state (and mark dependents outdated)."""
    if not all_targets and not target:
        print_error("Specify a feature to clean, or --all to reset every target.")
        raise typer.Exit(code=EXIT_USAGE)
    root = Path.cwd()
    project = _load_project_or_exit(root / "intent")
    config = _load_config_or_exit(root)
    if target and not all_targets:
        _require_target(project, target)
    resolved_output = _resolve_output_dir(output_dir, config)
    builder, _ = _make_builder(project, root, resolved_output, config.default_profile, _make_log())
    with _expected_failures():
        if all_targets:
            builder.clean_all(resolved_output)
            console.print(f"[green]All build state reset for '{resolved_output}'.[/green]")
        else:
            builder.clean(target or "", resolved_output)
            console.print(f"[green]Cleaned '{target}'.[/green] Restored files are left in the working tree for you to review.")


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


@app.command()
def plan(
    target: str = typer.Argument(..., help="Feature path to plan (created if it does not exist)"),
    prompt: str = typer.Argument(..., help="Seed prompt describing what to plan"),
    output_dir: Optional[str] = OutputDirOption,
    profile: Optional[str] = ProfileOption,
    implementation: Optional[str] = ImplementationOption,
) -> None:
    """Refine a feature's intent and validations interactively with the agent."""
    root = Path.cwd()
    intent_dir = root / "intent"
    project = _load_project_or_exit(intent_dir)
    config = _load_config_or_exit(root)
    resolved_output = _resolve_output_dir(output_dir, config)
    resolved_profile = _resolve_profile(profile, config)
    impl = _resolve_implementation_or_exit(project, implementation) if implementation else _resolve_implementation_or_exit(project, None)

    if target not in project.features:
        feature_name = target.rstrip("/").rsplit("/", 1)[-1]
        intent = IntentFile(name=feature_name)
        ic_path = write_intent_file(intent, intent_dir / target / f"{feature_name}.ic")
        console.print(f"[green]Created new feature:[/green] {ic_path.relative_to(root)}")
        project.features[target] = FeatureNode(path=target, intents=[intent])
    node = project.features[target]
    intent = node.intents[0] if node.intents else IntentFile(name=target)

    ctx = BuildContext(
        intent=intent,
        validations=list(node.validations),
        output_dir=resolved_output,
        generation_id="planning",
        dependency_names=list(node.depends_on),
        project_intent=project.project_intent,
        implementation=impl,
        response_file_path="",
        seed_prompt=prompt,
        feature_path=target,
    )
    with _expected_failures():
        agent = create_from_profile(resolved_profile, log=_make_log())
        agent.plan(ctx)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@app.command()
def status(
    output_dir: Optional[str] = OutputDirOption,
    outdated: bool = typer.Option(False, "--outdated", help="Show only outdated targets"),
) -> None:
    """Show the build state of every feature."""
    from intentc.build.state import TargetStatus

    root = Path.cwd()
    project = _load_project_or_exit(root / "intent")
    config = _load_config_or_exit(root)
    resolved_output = _resolve_output_dir(output_dir, config)
    builder, state = _make_builder(project, root, resolved_output, config.default_profile, None)
    builder.refresh_outdated()

    tracked = dict(state.list_targets())
    order = project.topological_order()
    removed = sorted(t for t in tracked if t not in project.features)
    rows = []
    for target in order + removed:
        status_value = tracked.get(target, TargetStatus.PENDING).value
        if outdated and status_value != TargetStatus.OUTDATED.value:
            continue
        result = state.get_build_result(target)
        node = project.features.get(target)
        rows.append(
            {
                "target": target,
                "status": status_value,
                "depends_on": node.depends_on if node else [],
                "validations": sum(len(vf.validations) for vf in node.validations) if node else 0,
                "timestamp": result.timestamp if result else "",
                "attempts": result.attempts if result else 0,
                "generation_id": result.generation_id if result else "",
                "removed": node is None,
            }
        )
    render_status_table(rows, resolved_output)
    if not outdated:
        render_next_targets(builder.next_targets(), nothing_built=not any(s == TargetStatus.BUILT for s in tracked.values()))


# ---------------------------------------------------------------------------
# diff / log
# ---------------------------------------------------------------------------


@app.command()
def diff(
    target: str = typer.Argument(..., help="Feature path"),
    output_dir: Optional[str] = OutputDirOption,
    stat: bool = typer.Option(False, "--stat", help="File list only, no patch"),
) -> None:
    """Show what the last build of a target generated."""
    from intentc.build.state import GitVersionControl, StateManager

    root = Path.cwd()
    config = _load_config_or_exit(root)
    resolved_output = _resolve_output_dir(output_dir, config)
    state = StateManager(root, resolved_output)
    result = state.get_build_result(target)
    if result is None or not result.commit_id:
        print_error(f"No build recorded for '{target}' in {resolved_output}. Run: intentc build {target}")
        raise typer.Exit(code=EXIT_USAGE)
    diff_text = "" if stat else state.backend.get_build_diff(target) or ""
    if not stat and not diff_text:
        with _expected_failures():
            diff_text = GitVersionControl(root, resolved_output).diff(f"{result.commit_id}~1", result.commit_id)
    render_diff(diff_text, result, stat_only=stat)


@app.command()
def log(
    target: str = typer.Argument(..., help="Feature path"),
    output_dir: Optional[str] = OutputDirOption,
    limit: int = typer.Option(10, "--limit", "-n", help="Number of history rows"),
) -> None:
    """Show a target's build history, the latest build's steps and validation results."""
    from intentc.build.state import StateManager

    root = Path.cwd()
    config = _load_config_or_exit(root)
    resolved_output = _resolve_output_dir(output_dir, config)
    state = StateManager(root, resolved_output)
    history = state.get_build_history(target, limit)
    if not history:
        print_error(f"'{target}' has never been built in {resolved_output}. Run: intentc build {target}")
        raise typer.Exit(code=EXIT_USAGE)
    render_build_log(target, history, state.backend.get_validation_results(target))


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


@app.command()
def compare(
    dir_a: str = typer.Argument(..., help="Reference output directory"),
    dir_b: str = typer.Argument(..., help="Candidate output directory"),
    profile: Optional[str] = ProfileOption,
    implementation: Optional[str] = ImplementationOption,
) -> None:
    """Evaluate functional equivalence between two output directories."""
    from intentc.differencing import run_differencing

    root = Path.cwd()
    project = _load_project_or_exit(root / "intent")
    config = _load_config_or_exit(root)
    for label, directory in (("Reference", dir_a), ("Candidate", dir_b)):
        if not Path(directory).is_dir():
            print_error(f"{label} directory not found: {directory}")
            raise typer.Exit(code=EXIT_USAGE)
    if implementation:
        _resolve_implementation_or_exit(project, implementation)
    with _expected_failures():
        response = run_differencing(dir_a, dir_b, project, _resolve_profile(profile, config), implementation=implementation, log=_make_log())
    render_compare_results(response)
    if response.status != "equivalent":
        raise typer.Exit(code=EXIT_FAILURE)


def main() -> None:  # pragma: no cover - console entry point helper
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
