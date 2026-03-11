"""Main CLI entry point for intentc - a compiler of intent.

Provides all subcommands: init, build, clean, validate, status, commit, check,
add (intent/validation), and list (intents/validations/profiles).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import typer

# ---------------------------------------------------------------------------
# Application and sub-command groups
# ---------------------------------------------------------------------------

app = typer.Typer(name="intentc", help="A compiler of intent")
add_app = typer.Typer(help="Add new intents or validations")
list_app = typer.Typer(help="List project components")
app.add_typer(add_app, name="add")
app.add_typer(list_app, name="list")

# ---------------------------------------------------------------------------
# Global CLI state populated by the root callback
# ---------------------------------------------------------------------------


class CLIState:
    """Mutable container for global CLI flags set by the root callback."""

    verbose: int = 0
    config_path: str = ""
    profile: str = "default"
    agent_provider: str = ""
    agent_command: str = ""
    agent_timeout: str = ""
    agent_retries: int = -1
    agent_cli_args: str = ""
    model: str = ""
    log_level: str = ""


state = CLIState()

# ---------------------------------------------------------------------------
# Root callback (global flags)
# ---------------------------------------------------------------------------


@app.callback()
def main(
    verbose: int = typer.Option(0, "--verbose", "-v", count=True, help="Increase verbosity"),
    config: str = typer.Option("", "--config", help="Config file path"),
    profile: str = typer.Option("default", "--profile", help="Agent profile"),
    agent_provider: str = typer.Option("", "--agent-provider", help="Override agent provider"),
    agent_command: str = typer.Option("", "--agent-command", help="Override agent command"),
    agent_timeout: str = typer.Option("", "--agent-timeout", help="Override timeout"),
    agent_retries: int = typer.Option(-1, "--agent-retries", help="Override retries"),
    agent_cli_args: str = typer.Option("", "--agent-cli-args", help="Override CLI args"),
    model: str = typer.Option("", "--model", help="Override model ID"),
    log_level: str = typer.Option("", "--log-level", help="Override log level"),
) -> None:
    """Root callback - applies global flags before any subcommand runs."""
    state.verbose = verbose
    state.config_path = config
    state.profile = profile
    state.agent_provider = agent_provider
    state.agent_command = agent_command
    state.agent_timeout = agent_timeout
    state.agent_retries = agent_retries
    state.agent_cli_args = agent_cli_args
    state.model = model
    state.log_level = log_level

    # Set up logging based on verbosity / explicit level
    level = log_level or (
        "debug" if verbose >= 2 else "info" if verbose >= 1 else "warning"
    )
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(levelname)s: %(message)s",
    )


# ---------------------------------------------------------------------------
# Helper: load config with CLI overrides applied
# ---------------------------------------------------------------------------


def _load_config_with_overrides(project_root: str):
    """Load configuration from disk and overlay any CLI flag overrides."""
    from config.config import load_config

    cfg = load_config(project_root)

    # Apply CLI overrides to the selected profile
    if state.agent_provider or state.agent_command or state.model:
        profile_name = state.profile
        if profile_name in cfg.profiles:
            profile = cfg.profiles[profile_name]
        else:
            profile = cfg.profiles.get("default", next(iter(cfg.profiles.values())))

        if state.agent_provider:
            profile.provider = state.agent_provider
        if state.agent_command:
            profile.command = state.agent_command
        if state.model:
            profile.model_id = state.model
        if state.agent_retries >= 0:
            profile.retries = state.agent_retries
        if state.agent_timeout:
            from core.types import _parse_duration

            profile.timeout = _parse_duration(state.agent_timeout)

        cfg.profiles[state.profile] = profile

    if state.log_level:
        cfg.logging.level = state.log_level

    return cfg


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


@app.command()
def init() -> None:
    """Initialize an intentc project."""
    project_root = os.getcwd()

    from git.manager import new_git_manager

    gm = new_git_manager()
    try:
        gm.initialize(project_root)
    except RuntimeError:
        typer.echo("Error: not a git repository. Run 'git init' first.", err=True)
        raise typer.Exit(1)

    if not gm.is_git_repo():
        typer.echo("Error: not a git repository. Run 'git init' first.", err=True)
        raise typer.Exit(1)

    intentc_dir = os.path.join(project_root, ".intentc")
    if os.path.exists(intentc_dir):
        typer.echo("Error: project already initialized.", err=True)
        raise typer.Exit(1)

    # Create .intentc/config.yaml with defaults
    from config.config import get_default_config, save_config

    os.makedirs(intentc_dir, exist_ok=True)
    save_config(project_root, get_default_config())

    # Create intent/ directory
    intent_dir = os.path.join(project_root, "intent")
    os.makedirs(intent_dir, exist_ok=True)

    # Create template project.ic
    project_ic = os.path.join(intent_dir, "project.ic")
    with open(project_ic, "w") as f:
        f.write(
            "---\n"
            "name: my-project\n"
            "version: 1\n"
            "tags: []\n"
            "---\n\n"
            "# My Project\n\n"
            "Describe your project here.\n"
        )

    # Add .intentc/state/ to .gitignore
    gitignore = os.path.join(project_root, ".gitignore")
    line = ".intentc/state/\n"
    if os.path.exists(gitignore):
        with open(gitignore, "r") as f:
            content = f.read()
        if ".intentc/state/" not in content:
            with open(gitignore, "a") as f:
                f.write("\n" + line)
    else:
        with open(gitignore, "w") as f:
            f.write(line)

    typer.echo("Initialized intentc project.")
    typer.echo("Next steps:")
    typer.echo("  1. Edit intent/project.ic with your project description")
    typer.echo("  2. Create features with: intentc add intent <name>")
    typer.echo("  3. Build with: intentc build --output <dir>")


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


@app.command()
def build(
    target: Optional[str] = typer.Argument(None, help="Target to build"),
    force: bool = typer.Option(False, "--force", "-f", help="Force rebuild"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show plan only"),
    output: str = typer.Option("", "--output", "-o", help="Output directory"),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Agent profile"),
) -> None:
    """Build targets from specs."""
    project_root = os.getcwd()
    cfg = _load_config_with_overrides(project_root)

    profile_name = profile or state.profile

    from agent.factory import create_from_profile
    from config.config import get_profile

    p = get_profile(cfg, profile_name)
    agent = create_from_profile(p)

    from state.manager import new_state_manager

    sm = new_state_manager(project_root)
    sm.initialize()

    from git.manager import new_git_manager

    gm = new_git_manager()
    gm.initialize(project_root)

    from builder.builder import Builder, BuildOptions

    b = Builder(project_root, agent, sm, gm, cfg)
    opts = BuildOptions(
        target=target or "",
        force=force,
        dry_run=dry_run,
        output_dir=output,
        profile_name=profile_name,
    )

    try:
        b.build(opts)
        typer.echo("Build complete.")
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# clean
# ---------------------------------------------------------------------------


@app.command()
def clean(
    target: Optional[str] = typer.Argument(None, help="Target to clean"),
    output: str = typer.Option("", "--output", "-o", help="Output directory"),
    all_targets: bool = typer.Option(False, "--all", help="Clean all targets"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be cleaned"),
) -> None:
    """Remove generated files and reset state."""
    project_root = os.getcwd()
    cfg = _load_config_with_overrides(project_root)

    from state.manager import new_state_manager

    sm = new_state_manager(project_root)
    sm.initialize()

    from git.manager import new_git_manager

    gm = new_git_manager()
    gm.initialize(project_root)

    from builder.builder import Builder

    # Clean does not need a real agent; create a minimal one from the default profile.
    from agent.factory import create_from_profile
    from config.config import get_profile

    p = get_profile(cfg, state.profile)
    agent = create_from_profile(p)

    b = Builder(project_root, agent, sm, gm, cfg)

    if not target and not all_targets:
        typer.echo("Specify a target or use --all to clean all targets.", err=True)
        raise typer.Exit(1)

    try:
        b.clean(target=target or "", output_dir=output, all=all_targets)
        typer.echo("Clean complete.")
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


@app.command()
def validate(
    target: Optional[str] = typer.Argument(None, help="Target to validate"),
    output: str = typer.Option("", "--output", "-o", help="Output directory"),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Agent profile"),
    parallel: bool = typer.Option(False, "--parallel", help="Run validations in parallel"),
    timeout: str = typer.Option("60s", "--timeout", help="Per-validation timeout"),
) -> None:
    """Run validations against generated code."""
    project_root = os.getcwd()
    cfg = _load_config_with_overrides(project_root)

    profile_name = profile or state.profile

    from agent.factory import create_from_profile
    from config.config import get_profile

    p = get_profile(cfg, profile_name)
    agent = create_from_profile(p)

    from state.manager import new_state_manager

    sm = new_state_manager(project_root)
    sm.initialize()

    from git.manager import new_git_manager

    gm = new_git_manager()
    gm.initialize(project_root)

    from builder.builder import Builder, BuildOptions
    from core.types import _parse_duration

    b = Builder(project_root, agent, sm, gm, cfg)
    opts = BuildOptions(
        target=target or "",
        output_dir=output,
        profile_name=profile_name,
    )

    try:
        report = b.validate(
            target=target or "",
            output_dir=output,
            parallel=parallel,
            timeout=_parse_duration(timeout).total_seconds(),
        )
        # Print the report
        from validation.runner import Runner

        typer.echo(Runner.generate_report(report))

        if report.failed > 0:
            raise typer.Exit(1)
    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@app.command()
def status(
    tree: bool = typer.Option(False, "--tree", help="Show dependency tree"),
    output: str = typer.Option("", "--output", "-o", help="Output directory to show status for"),
) -> None:
    """Show target status."""
    project_root = os.getcwd()

    from parser.parser import TargetRegistry
    from state.manager import new_state_manager

    registry = TargetRegistry(project_root)
    try:
        registry.load_targets()
    except FileNotFoundError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    targets = registry.get_all_targets()

    # Resolve output directory and scope state to it
    cfg = _load_config_with_overrides(project_root)
    output_dir = output or cfg.build.default_output
    output_dir = os.path.abspath(os.path.join(project_root, output_dir))

    sm = new_state_manager(project_root)
    sm.initialize()
    sm.set_output_dir(output_dir)

    if not targets:
        typer.echo("No targets found.")
        return

    # Print status for each target
    for t in targets:
        target_status = sm.get_target_status(t.name)
        dep_count = len(t.intent.depends_on)
        val_count = sum(len(vf.validations) for vf in t.validations)
        typer.echo(f"  [{target_status.value:8s}] {t.name}  (deps: {dep_count}, validations: {val_count})")

    # If --tree, build DAG and print visualization
    if tree:
        from graph.dag import DAG

        dag = DAG()
        for t in targets:
            dag.add_target(t)
        try:
            dag.resolve()
        except Exception as e:
            typer.echo(f"Warning: could not resolve dependencies: {e}", err=True)

        typer.echo("")
        typer.echo("Dependency tree:")
        typer.echo(dag.visualize())


# ---------------------------------------------------------------------------
# log
# ---------------------------------------------------------------------------


@app.command()
def log(
    target: Optional[str] = typer.Argument(None, help="Target to show log for"),
    generation: str = typer.Option("", "--generation", help="Specific generation ID"),
    diff: bool = typer.Option(False, "--diff", help="Show full unified diff"),
    output: str = typer.Option("", "--output", "-o", help="Output directory"),
) -> None:
    """View structured build logs."""
    project_root = os.getcwd()
    cfg = _load_config_with_overrides(project_root)

    from state.manager import new_state_manager

    sm = new_state_manager(project_root)
    sm.initialize()

    output_dir = output or cfg.build.default_output
    output_dir = os.path.abspath(os.path.join(project_root, output_dir))
    sm.set_output_dir(output_dir)

    if not target:
        # List all targets with builds
        from parser.parser import TargetRegistry

        try:
            registry = TargetRegistry(project_root)
            registry.load_targets()
            targets = registry.get_all_targets()
            target_names = [t.name for t in targets]
        except Exception:
            target_names = []

        found_any = False
        lines: list[str] = []
        for name in target_names:
            try:
                result = sm.get_latest_build_result(name)
                status_str = "success" if result.success else "failed"
                dur = f"{result.total_duration_seconds:.1f}s" if result.total_duration_seconds else "-"
                files_count = len(result.files)
                files_str = f"{files_count} file{'s' if files_count != 1 else ''}"
                ts = result.generated_at.strftime("%Y-%m-%dT%H:%M")
                lines.append(
                    f"  {name:<20} {result.generation_id}  {status_str:<7}  {dur:>6}  {files_str:<8}  {ts}"
                )
                found_any = True
            except FileNotFoundError:
                continue

        if not found_any:
            typer.echo("No builds found.")
            return

        typer.echo("Build History:")
        for line in lines:
            typer.echo(line)
        return

    # Specific target
    try:
        if generation:
            result = sm.get_build_result(target, generation)
        else:
            result = sm.get_latest_build_result(target)
    except FileNotFoundError:
        typer.echo(f"No builds found for target '{target}'")
        return

    status_str = "success" if result.success else "failed"
    ts = result.generated_at.strftime("%Y-%m-%dT%H:%M:%S")
    typer.echo(f"Build Log: {target} ({result.generation_id})")
    typer.echo(f"Status: {status_str} | {ts}")
    typer.echo("")

    if not result.steps:
        typer.echo("  No step data available (build predates logging)")
        typer.echo("")
    else:
        for step in result.steps:
            dur = f"{step.duration_seconds:.1f}s"
            typer.echo(
                f"  [{step.status.value:<7}]  {step.phase.value:<14}  {step.summary:<40}  {dur}"
            )
        typer.echo("")

    total_dur = f"{result.total_duration_seconds:.1f}s" if result.total_duration_seconds else "-"
    files_changed = sum(s.files_changed for s in result.steps)
    typer.echo(f"Total: {total_dur} | Files: {files_changed} changed")

    if diff:
        typer.echo("")
        post_build_steps = [s for s in result.steps if s.phase.value == "post_build"]
        if post_build_steps and post_build_steps[0].diff:
            typer.echo(post_build_steps[0].diff)
        else:
            typer.echo("No diff available.")


# ---------------------------------------------------------------------------
# commit
# ---------------------------------------------------------------------------


@app.command()
def commit(
    message: str = typer.Option("", "--message", "-m", help="Commit message"),
    all_changes: bool = typer.Option(False, "--all", help="Commit all changes"),
) -> None:
    """Commit changes with appropriate prefixes."""
    project_root = os.getcwd()

    from git.manager import GENERATED_PREFIX, INTENT_PREFIX, new_git_manager

    gm = new_git_manager()
    gm.initialize(project_root)

    git_status = gm.get_status()

    if git_status.clean and not all_changes:
        typer.echo("Nothing to commit.")
        return

    # Collect all changed files
    all_files = list(set(
        git_status.modified_files
        + git_status.untracked_files
        + git_status.staged_files
    ))

    if not all_files and not all_changes:
        typer.echo("Nothing to commit.")
        return

    # Separate intent files from generated/other files
    intent_files = [f for f in all_files if f.startswith("intent/") or f.startswith("intent" + os.sep)]
    generated_files = [f for f in all_files if f not in intent_files]

    commit_msg = message or "update"
    commits_created = 0

    # Commit intent files first
    if intent_files:
        gm.add(intent_files)
        gm.commit(f"{INTENT_PREFIX} {commit_msg}")
        commits_created += 1
        typer.echo(f"Committed {len(intent_files)} intent file(s) with prefix '{INTENT_PREFIX}'")

    # Commit generated files
    if generated_files:
        gm.add(generated_files)
        gm.commit(f"{GENERATED_PREFIX} {commit_msg}")
        commits_created += 1
        typer.echo(f"Committed {len(generated_files)} generated file(s) with prefix '{GENERATED_PREFIX}'")

    if commits_created == 0:
        typer.echo("Nothing to commit.")
    else:
        typer.echo(f"Created {commits_created} commit(s).")


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


@app.command()
def check(
    target: Optional[str] = typer.Argument(None, help="Target to check"),
) -> None:
    """Validate spec files against schemas."""
    project_root = os.getcwd()

    from config.config import load_config, validate_config
    from parser.parser import validate_all_specs

    violations = validate_all_specs(project_root)

    try:
        cfg = load_config(project_root)
        violations.extend(validate_config(cfg))
    except Exception as e:
        typer.echo(f"Config error: {e}", err=True)

    errors = [v for v in violations if v.severity == "error"]
    warnings = [v for v in violations if v.severity == "warning"]

    if warnings:
        typer.echo("Warnings:")
        for v in warnings:
            typer.echo(f"  {v.file_path}: {v.field}: {v.message}")

    if errors:
        typer.echo("Errors:")
        for v in errors:
            typer.echo(f"  {v.file_path}: {v.field}: {v.message}")
        raise typer.Exit(1)

    typer.echo("All spec files are valid.")


# ---------------------------------------------------------------------------
# add intent
# ---------------------------------------------------------------------------


@add_app.command("intent")
def add_intent(
    name: str = typer.Argument(..., help="Feature name"),
) -> None:
    """Scaffold a new feature."""
    project_root = os.getcwd()
    intent_dir = os.path.join(project_root, "intent")

    if not os.path.isdir(intent_dir):
        typer.echo("Error: intent/ directory not found. Run 'intentc init' first.", err=True)
        raise typer.Exit(1)

    feature_dir = os.path.join(intent_dir, name)
    if os.path.exists(feature_dir):
        typer.echo(f"Error: feature '{name}' already exists.", err=True)
        raise typer.Exit(1)

    os.makedirs(feature_dir, exist_ok=True)

    ic_path = os.path.join(feature_dir, f"{name}.ic")
    with open(ic_path, "w") as f:
        f.write(
            f"---\n"
            f"name: {name}\n"
            f"version: 1\n"
            f"depends_on: []\n"
            f"tags: []\n"
            f"---\n\n"
            f"# {name}\n\n"
            f"Describe the {name} feature here.\n"
        )

    typer.echo(f"Created feature scaffold: {ic_path}")


# ---------------------------------------------------------------------------
# add validation
# ---------------------------------------------------------------------------


@add_app.command("validation")
def add_validation(
    target: str = typer.Argument(..., help="Target name"),
    type: str = typer.Argument(..., help="Validation type (file_check, folder_check, command_check, llm_judge)"),
) -> None:
    """Add validation to a target."""
    project_root = os.getcwd()
    target_dir = os.path.join(project_root, "intent", target)

    if not os.path.isdir(target_dir):
        typer.echo(f"Error: target directory 'intent/{target}' not found.", err=True)
        raise typer.Exit(1)

    # Validate type
    from core.types import ValidationType

    valid_types = {vt.value for vt in ValidationType}
    if type not in valid_types:
        typer.echo(f"Error: unknown validation type '{type}'. Valid types: {', '.join(sorted(valid_types))}", err=True)
        raise typer.Exit(1)

    icv_path = os.path.join(target_dir, "validations.icv")

    # Build template entry based on type
    templates = {
        "file_check": (
            f"  - name: {target}-file-check\n"
            f"    type: file_check\n"
            f"    path: path/to/expected/file\n"
        ),
        "folder_check": (
            f"  - name: {target}-folder-check\n"
            f"    type: folder_check\n"
            f"    path: path/to/expected/directory\n"
        ),
        "command_check": (
            f"  - name: {target}-command-check\n"
            f"    type: command_check\n"
            f"    command: echo 'test'\n"
            f"    exit_code: 0\n"
        ),
        "llm_judge": (
            f"  - name: {target}-llm-judge\n"
            f"    type: llm_judge\n"
            f"    rubric: |\n"
            f"      Describe what to validate here.\n"
            f"    severity: error\n"
        ),
    }

    entry = templates[type]

    if os.path.exists(icv_path):
        # Append to existing validations list
        with open(icv_path, "r") as f:
            content = f.read()

        # Find the closing --- and insert before it, or append to validations list
        # The .icv files use frontmatter format; we append the entry before the closing ---
        lines = content.split("\n")
        closing_idx = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                closing_idx = i
                break

        if closing_idx is not None:
            # Insert the new validation entry before the closing ---
            new_lines = lines[:closing_idx]
            # Ensure there's a blank line before the new entry for readability
            if new_lines and new_lines[-1].strip():
                new_lines.append("")
            new_lines.append(entry.rstrip())
            new_lines.extend(lines[closing_idx:])
            with open(icv_path, "w") as f:
                f.write("\n".join(new_lines))
        else:
            # No closing delimiter found; append to end
            with open(icv_path, "a") as f:
                f.write("\n" + entry)
    else:
        # Create new .icv file
        with open(icv_path, "w") as f:
            f.write(
                f"---\n"
                f"target: {target}\n"
                f"version: 1\n"
                f"validations:\n"
                f"{entry}"
                f"---\n\n"
                f"# {target} Validations\n"
            )

    typer.echo(f"Added {type} validation to {icv_path}")


# ---------------------------------------------------------------------------
# list intents
# ---------------------------------------------------------------------------


@list_app.command("intents")
def list_intents() -> None:
    """List all discovered features."""
    project_root = os.getcwd()

    from parser.parser import TargetRegistry

    registry = TargetRegistry(project_root)
    try:
        registry.load_targets()
    except FileNotFoundError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    targets = registry.get_all_targets()

    if not targets:
        typer.echo("No features found.")
        return

    typer.echo(f"{'Name':<20} {'Dependencies':<15} {'Validations':<15} {'Tags'}")
    typer.echo("-" * 70)
    for t in targets:
        dep_count = len(t.intent.depends_on)
        val_count = sum(len(vf.validations) for vf in t.validations)
        tags = ", ".join(t.intent.tags) if t.intent.tags else "-"
        typer.echo(f"{t.name:<20} {dep_count:<15} {val_count:<15} {tags}")


# ---------------------------------------------------------------------------
# list validations
# ---------------------------------------------------------------------------


@list_app.command("validations")
def list_validations() -> None:
    """List available validation types."""
    from core.types import ValidationType

    type_info = {
        ValidationType.FILE_CHECK: (
            "Check that a file exists and optionally contains required strings.",
            "path (required), contains (optional list of strings)",
        ),
        ValidationType.FOLDER_CHECK: (
            "Check that a directory exists and optionally contains required children.",
            "path (required), children (optional list of names)",
        ),
        ValidationType.COMMAND_CHECK: (
            "Execute a shell command and check exit code / output.",
            "command (required), exit_code (default 0), stdout_contains, stderr_contains",
        ),
        ValidationType.LLM_JUDGE: (
            "Use an LLM agent to evaluate generated code against a rubric.",
            "rubric (required), severity (error|warning), context_files (optional globs)",
        ),
    }

    typer.echo(f"{'Type':<20} {'Description'}")
    typer.echo("=" * 70)
    for vt in ValidationType:
        desc, params = type_info.get(vt, ("", ""))
        typer.echo(f"{vt.value:<20} {desc}")
        typer.echo(f"{'':20} Parameters: {params}")
        typer.echo("")


# ---------------------------------------------------------------------------
# list profiles
# ---------------------------------------------------------------------------


@list_app.command("profiles")
def list_profiles() -> None:
    """List configured agent profiles."""
    project_root = os.getcwd()

    try:
        cfg = _load_config_with_overrides(project_root)
    except Exception as e:
        typer.echo(f"Error loading config: {e}", err=True)
        raise typer.Exit(1)

    if not cfg.profiles:
        typer.echo("No profiles configured.")
        return

    typer.echo(f"{'Name':<15} {'Provider':<10} {'Model':<25} {'Timeout':<10} {'Tools':<8} {'Skills'}")
    typer.echo("-" * 85)
    for name, profile in sorted(cfg.profiles.items()):
        from core.types import _serialize_duration

        timeout_str = _serialize_duration(profile.timeout)
        model = profile.model_id or "-"
        tool_count = len(profile.tools)
        skill_count = len(profile.skills)
        typer.echo(
            f"{name:<15} {profile.provider:<10} {model:<25} {timeout_str:<10} {tool_count:<8} {skill_count}"
        )


# ---------------------------------------------------------------------------
# edit
# ---------------------------------------------------------------------------


@app.command()
def edit(
    project_path: str = typer.Argument(".", help="Path to intentc project"),
    port: int = typer.Option(8080, "--port", help="Port for the editor server"),
) -> None:
    """Launch the interactive browser-based project editor."""
    resolved = os.path.abspath(project_path)

    if not os.path.isdir(os.path.join(resolved, ".intentc")):
        typer.echo(
            f"Error: '{resolved}' is not an initialized intentc project "
            "(missing .intentc/ directory). Run 'intentc init' first.",
            err=True,
        )
        raise typer.Exit(1)

    typer.echo(f"Starting intentc editor at http://127.0.0.1:{port}")
    typer.echo("Press Ctrl+C to stop.")

    from editor.server import start_server

    start_server(resolved, port)
