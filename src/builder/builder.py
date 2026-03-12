"""Builder implementation - orchestrates loading specs, resolving dependencies,
invoking agents, and tracking build results."""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel

from agent.base import Agent, BuildContext
from agent.factory import create_from_profile as _create_from_profile
from config.config import Config, get_profile, validate_config
from core.types import (
    AgentProfile,
    BuildPhase,
    BuildResult,
    BuildStep,
    SchemaViolation,
    StepStatus,
    Target,
    TargetStatus,
    ValidationFile,
)
from graph.dag import DAG
from parser.parser import TargetRegistry, validate_all_specs
from validation.registry import Registry as ValidatorRegistry

logger = logging.getLogger("intentc.builder")


def _log_step(target: str, phase: str, summary: str, duration: float, *, failed: bool = False) -> None:
    logger.info(f"[{target}] {phase}... {summary} ({duration:.1f}s)")


# ---------------------------------------------------------------------------
# Protocols for injected dependencies
# ---------------------------------------------------------------------------


class StateManagerProtocol(Protocol):
    """Minimal interface the builder needs from a state manager."""

    def initialize(self) -> None: ...
    def set_output_dir(self, output_dir: str) -> None: ...
    def get_target_status(self, name: str) -> Any: ...
    def update_target_status(self, name: str, status: Any) -> None: ...
    def save_build_result(self, result: BuildResult) -> None: ...
    def get_latest_build_result(self, name: str) -> BuildResult: ...
    def list_build_results(self, name: str) -> list[BuildResult]: ...
    def reset_target(self, name: str) -> None: ...
    def reset_all(self) -> None: ...


class GitManagerProtocol(Protocol):
    """Minimal interface the builder needs from a git manager."""

    def initialize(self, project_root: str) -> None: ...
    def is_git_repo(self) -> bool: ...
    def add(self, files: list[str]) -> None: ...
    def commit(self, message: str) -> None: ...


# ---------------------------------------------------------------------------
# BuildOptions
# ---------------------------------------------------------------------------


class BuildOptions(BaseModel):
    """Options controlling a build invocation."""

    target: str = ""  # Specific target, empty = all unbuilt
    force: bool = False  # Force rebuild
    dry_run: bool = False  # Print plan only
    output_dir: str = ""  # Override output dir
    profile_name: str = ""  # Agent profile name


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class Builder:
    """Orchestrates the full build pipeline.

    Loads intent specs, resolves the dependency graph, invokes agents for
    each target in topological order, and persists build results via the
    state manager.
    """

    def __init__(
        self,
        project_root: str,
        agent: Agent,
        state_manager: StateManagerProtocol,
        git_manager: GitManagerProtocol,
        cfg: Config,
        *,
        agent_factory: Any | None = None,
    ) -> None:
        self.project_root = project_root
        self.agent = agent
        self.state_manager = state_manager
        self.git_manager = git_manager
        self.config = cfg
        self._agent_factory = agent_factory or _create_from_profile

    # ------------------------------------------------------------------
    # build
    # ------------------------------------------------------------------

    def build(self, opts: BuildOptions) -> None:
        """Execute the full build pipeline.

        Steps:
        0. Validate all spec schemas and config.
        1. Load targets from the intent/ directory.
        2. Build the dependency DAG.
        3. Determine the set of targets to build.
        4. Resolve the output directory.
        5. If dry_run, log the plan and return.
        6. Build each target in dependency order.

        Raises:
            RuntimeError: On schema validation failure or target build failure.
        """
        # Step 0: Schema validation ----------------------------------------
        violations = validate_all_specs(self.project_root)
        violations.extend(validate_config(self.config))
        errors = [v for v in violations if v.severity == "error"]
        if errors:
            msg = "Schema validation failed:\n" + "\n".join(
                f"  {v.file_path}: {v.field}: {v.message}" for v in errors
            )
            raise RuntimeError(msg)

        # Step 1: Load targets ---------------------------------------------
        registry = TargetRegistry(self.project_root)
        registry.load_targets()

        # Step 2: Build DAG ------------------------------------------------
        dag = DAG()
        for target in registry.get_all_targets():
            dag.add_target(target)
        dag.resolve()
        dag.detect_cycles()

        # Step 3: Resolve output dir (moved before build set so status
        #         reads are scoped to the correct output directory) --------
        output_dir = opts.output_dir or self.config.build.default_output
        output_dir = os.path.abspath(
            os.path.join(self.project_root, output_dir)
        )
        os.makedirs(output_dir, exist_ok=True)
        self.state_manager.set_output_dir(output_dir)

        # Step 4: Determine build set --------------------------------------
        if opts.target:
            build_set = dag.get_dependency_chain(opts.target)
        elif opts.force:
            build_set = dag.topological_sort()
        else:
            all_targets = dag.topological_sort()
            build_set = [
                t
                for t in all_targets
                if self.state_manager.get_target_status(t.name)
                != TargetStatus.BUILT
            ]

        # Step 5: Dry run check --------------------------------------------
        if opts.dry_run:
            logger.info(
                "Dry run - would build: %s", [t.name for t in build_set]
            )
            for t in build_set:
                logger.info("  Would build: %s", t.name)
            return

        # Step 6: Build each target ----------------------------------------
        project_intent = registry.get_project_intent()

        for target in build_set:
            # Skip if already built and not force
            status = self.state_manager.get_target_status(target.name)
            if status == TargetStatus.BUILT and not opts.force:
                logger.info("[%s] skipped (already built)", target.name)
                continue

            # Resolve agent profile for this target
            profile_name = (
                opts.profile_name or target.intent.profile or "default"
            )
            profile = get_profile(self.config, profile_name)
            target_agent = self._agent_factory(profile)

            self.state_manager.update_target_status(
                target.name, TargetStatus.BUILDING
            )
            generation_id = f"gen-{int(time.time())}"
            logger.info("Building target: %s", target.name)

            steps: list[BuildStep] = []
            build_failed = False
            agent_files: list[str] = []

            # Phase 1: resolve_deps
            dep_names = target.intent.depends_on
            t0 = time.monotonic()
            started = datetime.now()
            n_deps = len(dep_names)
            deps_str = ", ".join(dep_names) if dep_names else "none"
            resolve_summary = f"Resolved {n_deps} dependencies: [{deps_str}]"
            elapsed = time.monotonic() - t0
            steps.append(BuildStep(
                phase=BuildPhase.RESOLVE_DEPS,
                status=StepStatus.SUCCESS,
                started_at=started,
                ended_at=datetime.now(),
                duration_seconds=round(elapsed, 3),
                summary=resolve_summary,
            ))
            _log_step(target.name, "resolve_deps", resolve_summary, elapsed)

            # Phase 2: read_plan
            t0 = time.monotonic()
            started = datetime.now()
            visible_validations = [
                vf
                for vf in target.validations
                if not all(v.hidden for v in vf.validations)
            ]
            val_count = sum(len(vf.validations) for vf in visible_validations)
            read_summary = f"Read {target.name} with {val_count} validations"
            elapsed = time.monotonic() - t0
            steps.append(BuildStep(
                phase=BuildPhase.READ_PLAN,
                status=StepStatus.SUCCESS,
                started_at=started,
                ended_at=datetime.now(),
                duration_seconds=round(elapsed, 3),
                summary=read_summary,
            ))
            _log_step(target.name, "read_plan", read_summary, elapsed)

            # Phase 3: build
            build_ctx = BuildContext(
                intent=target.intent,
                validations=visible_validations,
                project_root=self.project_root,
                output_dir=output_dir,
                generation_id=generation_id,
                dependency_names=dep_names,
                project_intent=project_intent,
            )

            t0 = time.monotonic()
            started = datetime.now()
            try:
                agent_files = target_agent.build(build_ctx)
                elapsed = time.monotonic() - t0
                build_summary = f"Agent generated {len(agent_files)} files"
                steps.append(BuildStep(
                    phase=BuildPhase.BUILD,
                    status=StepStatus.SUCCESS,
                    started_at=started,
                    ended_at=datetime.now(),
                    duration_seconds=round(elapsed, 3),
                    summary=build_summary,
                ))
                _log_step(target.name, "build", build_summary, elapsed)
            except Exception as e:
                elapsed = time.monotonic() - t0
                steps.append(BuildStep(
                    phase=BuildPhase.BUILD,
                    status=StepStatus.FAILED,
                    started_at=started,
                    ended_at=datetime.now(),
                    duration_seconds=round(elapsed, 3),
                    summary=f"Failed: {e}",
                    error=str(e),
                ))
                _log_step(target.name, "build", f"FAILED: {e}", elapsed, failed=True)
                build_failed = True

            # Phase 4: post_build
            if not build_failed:
                t0 = time.monotonic()
                started = datetime.now()
                try:
                    diff_stat = self.git_manager.get_diff_stat(
                        paths=[output_dir], include_untracked=True,
                    )
                    diff = self.git_manager.get_diff(
                        paths=[output_dir], include_untracked=True,
                    )
                    elapsed = time.monotonic() - t0
                    files_changed = len(agent_files)
                    summary = diff_stat.splitlines()[-1].strip() if diff_stat.strip() else f"{files_changed} files changed"
                    steps.append(BuildStep(
                        phase=BuildPhase.POST_BUILD,
                        status=StepStatus.SUCCESS,
                        started_at=started,
                        ended_at=datetime.now(),
                        duration_seconds=round(elapsed, 3),
                        summary=summary,
                        files_changed=files_changed,
                        diff_stat=diff_stat,
                        diff=diff,
                    ))
                    _log_step(target.name, "post_build", summary, elapsed)
                except Exception as e:
                    elapsed = time.monotonic() - t0
                    steps.append(BuildStep(
                        phase=BuildPhase.POST_BUILD,
                        status=StepStatus.FAILED,
                        started_at=started,
                        ended_at=datetime.now(),
                        duration_seconds=round(elapsed, 3),
                        summary=f"Failed: {e}",
                        error=str(e),
                    ))
            else:
                steps.append(BuildStep(
                    phase=BuildPhase.POST_BUILD,
                    status=StepStatus.SKIPPED,
                    started_at=datetime.now(),
                    ended_at=datetime.now(),
                    summary="Skipped (build failed)",
                ))

            # Phase 5: validate (always skipped during build - run via 'intentc validate')
            if not build_failed and target.validations:
                steps.append(BuildStep(
                    phase=BuildPhase.VALIDATE,
                    status=StepStatus.SKIPPED,
                    started_at=datetime.now(),
                    ended_at=datetime.now(),
                    summary="Validation available via 'intentc validate'",
                ))
            else:
                steps.append(BuildStep(
                    phase=BuildPhase.VALIDATE,
                    status=StepStatus.SKIPPED,
                    started_at=datetime.now(),
                    ended_at=datetime.now(),
                    summary="Skipped" if build_failed else "No validations defined",
                ))

            # Compute totals and save result
            total_duration = sum(s.duration_seconds for s in steps)

            if build_failed:
                build_error = steps[2].error  # build phase is always index 2
                result = BuildResult(
                    target=target.name,
                    generation_id=generation_id,
                    success=False,
                    error=build_error,
                    generated_at=datetime.now(),
                    output_dir=output_dir,
                    steps=steps,
                    total_duration_seconds=round(total_duration, 3),
                )
                self.state_manager.save_build_result(result)
                self.state_manager.update_target_status(
                    target.name, TargetStatus.FAILED
                )
                raise RuntimeError(
                    f"Failed to build target: {target.name}: {build_error}"
                ) from None
            else:
                result = BuildResult(
                    target=target.name,
                    generation_id=generation_id,
                    success=True,
                    generated_at=datetime.now(),
                    files=agent_files,
                    output_dir=output_dir,
                    steps=steps,
                    total_duration_seconds=round(total_duration, 3),
                )
                self.state_manager.save_build_result(result)
                self.state_manager.update_target_status(
                    target.name, TargetStatus.BUILT
                )
                logger.info(
                    "Built %s (%s) in %.1fs",
                    target.name,
                    generation_id,
                    total_duration,
                )

    # ------------------------------------------------------------------
    # clean
    # ------------------------------------------------------------------

    def clean(self, target: str, output_dir: str, all: bool = False) -> None:
        """Clean generated files and reset state.

        Set the output directory on the state manager first.

        - If *all* is True, reset all state for this output directory via
          ``stateManager.ResetAll()``.
        - If a specific *target* is given, reset that target's status to
          pending via ``stateManager.ResetTarget(target)``, delete its
          generated files from the output directory, and mark all
          dependents as outdated.
        - If no target and no *all*, print a usage hint.
        """
        output_dir = output_dir or self.config.build.default_output
        output_dir = os.path.abspath(
            os.path.join(self.project_root, output_dir)
        )
        self.state_manager.set_output_dir(output_dir)

        if all:
            self.state_manager.reset_all()
            return

        if target:
            # Delete generated files from last build
            try:
                result = self.state_manager.get_latest_build_result(target)
                for f in result.files:
                    if os.path.exists(f):
                        os.remove(f)
            except FileNotFoundError:
                pass

            # Reset target status and remove build results
            self.state_manager.reset_target(target)

            # Mark dependents as outdated
            try:
                registry = TargetRegistry(self.project_root)
                registry.load_targets()
                dag = DAG()
                for t in registry.get_all_targets():
                    dag.add_target(t)
                dag.resolve()
                affected = dag.get_affected(target)
                for t in affected:
                    self.state_manager.update_target_status(
                        t.name, TargetStatus.OUTDATED
                    )
            except Exception:
                pass
        else:
            logger.info("No target specified. Use --all to clean all targets.")

    # ------------------------------------------------------------------
    # validate
    # ------------------------------------------------------------------

    def validate(
        self,
        target: str,
        output_dir: str,
        parallel: bool = False,
        timeout: float = 60.0,
    ) -> Any:
        """Run validations for a target.

        Returns a RunReport from the validation runner, or raises
        RuntimeError if schema validation fails.
        """
        # Step 0: Schema validation
        violations = validate_all_specs(self.project_root)
        violations.extend(validate_config(self.config))
        errors = [v for v in violations if v.severity == "error"]
        if errors:
            msg = "Schema validation failed:\n" + "\n".join(
                f"  {v.file_path}: {v.field}: {v.message}" for v in errors
            )
            raise RuntimeError(msg)

        registry = TargetRegistry(self.project_root)
        registry.load_targets()

        target_obj = registry.get_target(target)

        output_dir = output_dir or self.config.build.default_output
        output_dir = os.path.abspath(
            os.path.join(self.project_root, output_dir)
        )

        val_registry = ValidatorRegistry()
        # Register LLM judge with the default agent
        val_registry.register_llm_judge(self.agent)

        from validation.runner import Runner, RunOptions

        runner = Runner(val_registry, self.agent, self.config)
        run_opts = RunOptions(parallel=parallel, timeout=timeout)
        return runner.run_target_validations(target_obj, output_dir, run_opts)
