"""Builder — core workflow engine that orchestrates builds along the project DAG."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

from intentc.build.agents.base import Agent
from intentc.build.agents.factory import create_from_profile
from intentc.build.agents.models import (
    AgentError,
    AgentProfile,
    BuildContext,
    BuildResponse,
    LogFn,
)
from intentc.build.state import StateManager, VersionControl
from intentc.build.storage.backend import (
    BuildResult,
    BuildStep,
    GenerationStatus,
    StorageBackend,
    TargetStatus,
)
from intentc.build.validations import ValidationSuite, ValidationSuiteResult
from intentc.core.models import IntentFile
from intentc.core.project import Project


class BuildOptions(BaseModel):
    """Options controlling a build invocation."""

    target: str = ""
    force: bool = False
    dry_run: bool = False
    output_dir: str = ""
    profile_override: str = ""
    implementation: str = ""


class Builder:
    """Core workflow engine for intentc builds.

    Dependencies are injected at construction. The builder receives an
    AgentProfile and uses a _create_agent callable when it needs an agent
    instance.
    """

    def __init__(
        self,
        project: Project,
        state_manager: StateManager,
        version_control: VersionControl,
        agent_profile: AgentProfile,
        *,
        log: Callable[[str], None] | None = None,
        create_agent: Callable[[AgentProfile], Agent] | None = None,
    ) -> None:
        self._project = project
        self._state_manager = state_manager
        self._version_control = version_control
        self._agent_profile = agent_profile
        self._storage: StorageBackend = state_manager.backend
        self._log: Callable[[str], None] = log or (lambda _: None)

        if create_agent is not None:
            self._create_agent = create_agent
        else:
            # Wrap the default factory to pass our log callback
            def _default_factory(profile: AgentProfile) -> Agent:
                return create_from_profile(profile, log=self._log)

            self._create_agent = _default_factory

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build(
        self, opts: BuildOptions
    ) -> tuple[list[BuildResult], RuntimeError | None]:
        """Execute the build pipeline.

        Returns a tuple of (results, error). Error is non-null if any target
        failed. The error is NOT raised — it is returned.
        """
        # 1. Determine build set
        buildable_statuses = {
            TargetStatus.PENDING,
            TargetStatus.OUTDATED,
            TargetStatus.FAILED,
        }

        if opts.target:
            if opts.target not in self._project.features:
                return ([], RuntimeError(f"Target '{opts.target}' not found in project"))
            # Collect target + ancestors, filter to actionable
            candidates = {opts.target} | self._project.ancestors(opts.target)
            if opts.force:
                build_set_unordered = candidates
            else:
                build_set_unordered = {
                    t
                    for t in candidates
                    if t in self._project.features
                    and self._state_manager.get_status(t) in buildable_statuses
                }
        else:
            if opts.force:
                build_set_unordered = set(self._project.features.keys())
            else:
                build_set_unordered = {
                    t
                    for t in self._project.features
                    if self._state_manager.get_status(t) in buildable_statuses
                }

        # Keep only features that exist in the project
        build_set_unordered = {
            t for t in build_set_unordered if t in self._project.features
        }

        # Topological order
        topo = self._project.topological_order()
        build_set = [t for t in topo if t in build_set_unordered]

        if not build_set:
            return ([], None)

        # 2. Dry run check
        if opts.dry_run:
            results = [
                BuildResult(
                    target=t,
                    status=self._state_manager.get_status(t).value,
                )
                for t in build_set
            ]
            return (results, None)

        # 3. Resolve implementation
        impl_name = opts.implementation or None
        implementation = self._project.resolve_implementation(impl_name)

        # 4. Generate generation ID
        generation_id = str(uuid.uuid4())
        profile_name = opts.profile_override or self._agent_profile.name
        opts_dict = opts.model_dump()
        self._storage.create_generation(
            generation_id, opts.output_dir, profile_name, opts_dict
        )

        self._log(
            f"Starting build: {len(build_set)} target(s) [{', '.join(build_set)}]"
        )
        self._storage.log_generation_event(
            generation_id,
            f"Build plan: {build_set}",
        )

        # 5. Resolve output directory
        output_dir = opts.output_dir
        if output_dir:
            Path(output_dir).mkdir(parents=True, exist_ok=True)

        # 6. Build each target
        results: list[BuildResult] = []
        gen_status = GenerationStatus.COMPLETED

        for idx, target in enumerate(build_set):
            self._log(f"[{idx + 1}/{len(build_set)}] Building target '{target}'...")

            # Skip check
            current_status = self._state_manager.get_status(target)
            if current_status == TargetStatus.BUILT and not opts.force:
                self._log(f"  Skipping '{target}' — already built")
                self._storage.log_generation_event(
                    generation_id, f"Skipped '{target}' (already built)"
                )
                continue

            # Resolve agent profile
            if opts.profile_override:
                profile = AgentProfile(
                    **{
                        **self._agent_profile.model_dump(),
                        "name": opts.profile_override,
                    }
                )
            else:
                profile = self._agent_profile

            # Apply sandbox paths
            profile = self._apply_sandbox_paths(profile, target, output_dir)

            # Create agent from sandboxed profile
            agent = self._create_agent(profile)

            # Get node info
            node = self._project.features[target]

            # Execute build steps with retries
            max_attempts = max(profile.retries, 1)
            previous_errors: list[str] = []
            result: BuildResult | None = None

            for attempt in range(max_attempts):
                steps: list[BuildStep] = []
                build_response: BuildResponse | None = None
                failed = False

                # Step 1: resolve_deps
                step_start = datetime.now()
                dep_names = list(node.depends_on)
                duration = (datetime.now() - step_start).total_seconds()
                steps.append(
                    BuildStep(
                        phase="resolve_deps",
                        status="success",
                        duration_secs=duration,
                        summary=f"Dependencies: {dep_names}" if dep_names else "No dependencies",
                    )
                )
                self._log(f"  resolve_deps: {dep_names}")

                # Step 2: build
                step_start = datetime.now()
                intent = node.intents[0] if node.intents else IntentFile(name=target)
                response_file = str(
                    self._state_manager.build_response_dir
                    / f"response-{target.replace('/', '_')}-{generation_id[:8]}.json"
                )
                val_response_dir = self._state_manager.val_response_dir

                ctx = BuildContext(
                    intent=intent,
                    validations=node.validations,
                    output_dir=output_dir,
                    generation_id=generation_id,
                    dependency_names=dep_names,
                    project_intent=self._project.project_intent,
                    implementation=implementation,
                    response_file_path=response_file,
                    previous_errors=previous_errors,
                )

                try:
                    build_response = agent.build(ctx)
                    duration = (datetime.now() - step_start).total_seconds()
                    steps.append(
                        BuildStep(
                            phase="build",
                            status="success",
                            duration_secs=duration,
                            summary=build_response.summary
                            if build_response
                            else "Build completed",
                        )
                    )
                    self._log(f"  build: success")
                except AgentError as e:
                    duration = (datetime.now() - step_start).total_seconds()
                    summary = f"Agent error: {e}"
                    steps.append(
                        BuildStep(
                            phase="build",
                            status="failed",
                            duration_secs=duration,
                            summary=summary,
                        )
                    )
                    previous_errors.append(summary)
                    self._log(f"  build: FAILED — {e}")
                    failed = True

                # Step 3: validate (only if build succeeded)
                if not failed and node.validations:
                    step_start = datetime.now()
                    suite = ValidationSuite(
                        project=self._project,
                        agent_profile=profile,
                        output_dir=output_dir,
                        val_response_dir=val_response_dir,
                        storage_backend=self._storage,
                        log=self._log,
                    )
                    suite_result = suite.validate_feature(target)
                    duration = (datetime.now() - step_start).total_seconds()

                    if suite_result.passed:
                        steps.append(
                            BuildStep(
                                phase="validate",
                                status="success",
                                duration_secs=duration,
                                summary=suite_result.summary,
                            )
                        )
                        self._log(f"  validate: passed — {suite_result.summary}")
                    else:
                        summary = f"Validation failed: {suite_result.summary}"
                        steps.append(
                            BuildStep(
                                phase="validate",
                                status="failed",
                                duration_secs=duration,
                                summary=summary,
                            )
                        )
                        previous_errors.append(summary)
                        self._log(f"  validate: FAILED — {suite_result.summary}")
                        failed = True

                if failed:
                    if attempt < max_attempts - 1:
                        self._log(
                            f"  Retrying target '{target}' (attempt {attempt + 2}/{max_attempts})..."
                        )
                        continue
                    # All retries exhausted
                    total_duration = sum(s.duration_secs for s in steps)
                    result = BuildResult(
                        target=target,
                        generation_id=generation_id,
                        status="failed",
                        total_duration_secs=total_duration,
                        timestamp=datetime.now().isoformat(),
                        steps=steps,
                        files_created=build_response.files_created if build_response else [],
                        files_modified=build_response.files_modified if build_response else [],
                    )
                    break

                # Step 4: checkpoint (build + validate succeeded)
                step_start = datetime.now()
                commit_msg = f"build {target} [gen:{generation_id}]"
                try:
                    commit_id = self._version_control.checkpoint(commit_msg)
                    git_diff = ""
                    try:
                        git_diff = self._version_control.diff(
                            f"{commit_id}~1", commit_id
                        )
                    except RuntimeError:
                        pass
                    duration = (datetime.now() - step_start).total_seconds()
                    steps.append(
                        BuildStep(
                            phase="checkpoint",
                            status="success",
                            duration_secs=duration,
                            summary=f"Committed {commit_id[:8]}",
                        )
                    )
                    self._log(f"  checkpoint: {commit_id[:8]}")

                    total_duration = sum(s.duration_secs for s in steps)
                    result = BuildResult(
                        target=target,
                        generation_id=generation_id,
                        status="success",
                        commit_id=commit_id,
                        total_duration_secs=total_duration,
                        timestamp=datetime.now().isoformat(),
                        git_diff=git_diff,
                        steps=steps,
                        files_created=build_response.files_created if build_response else [],
                        files_modified=build_response.files_modified if build_response else [],
                    )
                except RuntimeError as e:
                    duration = (datetime.now() - step_start).total_seconds()
                    steps.append(
                        BuildStep(
                            phase="checkpoint",
                            status="failed",
                            duration_secs=duration,
                            summary=f"Checkpoint failed: {e}",
                        )
                    )
                    total_duration = sum(s.duration_secs for s in steps)
                    result = BuildResult(
                        target=target,
                        generation_id=generation_id,
                        status="failed",
                        total_duration_secs=total_duration,
                        timestamp=datetime.now().isoformat(),
                        steps=steps,
                        files_created=build_response.files_created if build_response else [],
                        files_modified=build_response.files_modified if build_response else [],
                    )
                break  # Success or checkpoint failure — either way, done with retries

            assert result is not None

            # Save build result (backend maps "success" → BUILT, else → FAILED)
            build_result_id = self._state_manager.save_build_result(target, result)

            # Save build steps to storage
            for step_order, step in enumerate(result.steps):
                self._storage.save_build_step(
                    build_result_id, step, step.summary, step_order
                )

            # Read and store agent response file, then delete it
            response_path = Path(response_file)
            if response_path.exists():
                try:
                    raw = json.loads(response_path.read_text())
                    self._storage.save_agent_response(
                        build_result_id=build_result_id,
                        validation_result_id=None,
                        response_type="build",
                        response_json=raw,
                    )
                except (json.JSONDecodeError, OSError):
                    pass
                try:
                    response_path.unlink()
                except OSError:
                    pass

            results.append(result)

            # On failure, stop DAG walk
            if result.status == "failed":
                last_step_summary = result.steps[-1].summary if result.steps else "Unknown error"
                self._storage.log_generation_event(
                    generation_id, f"Build failed for target '{target}': {last_step_summary}"
                )
                gen_status = GenerationStatus.FAILED
                self._storage.complete_generation(generation_id, gen_status)
                self._log(f"Build failed for target '{target}'")
                return (
                    results,
                    RuntimeError(
                        f"Build failed for target '{target}': {last_step_summary}"
                    ),
                )

            self._log(f"  Target '{target}' completed successfully")

        # Complete generation
        self._storage.complete_generation(generation_id, gen_status)
        return (results, None)

    # ------------------------------------------------------------------
    # Clean
    # ------------------------------------------------------------------

    def clean(self, target: str, output_dir: str) -> None:
        """Revert a target's generated code and reset its state.

        Creates a revert commit (linear history, not destructive rollback).
        """
        result = self._state_manager.get_build_result(target)
        if result is None:
            return

        if result.commit_id:
            self._version_control.restore(result.commit_id)
            # Do NOT checkpoint — restored files are left unstaged

        self._state_manager.reset(target)
        self._state_manager.mark_dependents_outdated(target, self._project)

    def clean_all(self, output_dir: str) -> None:
        """Reset all state for the output directory. Does not modify files."""
        self._state_manager.reset_all()

    # ------------------------------------------------------------------
    # Validate
    # ------------------------------------------------------------------

    def validate(
        self, target: str | None, output_dir: str
    ) -> ValidationSuiteResult | list[ValidationSuiteResult]:
        """Run validations independently of the build pipeline.

        Does not modify any state. Returns the result.
        """
        # Resolve agent profile (same priority as build)
        profile = self._agent_profile

        suite = ValidationSuite(
            project=self._project,
            agent_profile=profile,
            output_dir=output_dir,
            storage_backend=self._storage,
            log=self._log,
        )

        if target:
            return suite.validate_feature(target)
        return suite.validate_project()

    # ------------------------------------------------------------------
    # Invalidation
    # ------------------------------------------------------------------

    def detect_outdated(self) -> list[str]:
        """Walk all targets tracked by state manager with status 'built'.

        Compare BuildResult.timestamp against modification times of .ic and
        .icv files. Returns list of outdated targets without modifying state.
        """
        outdated: list[str] = []
        tracked = self._state_manager.list_targets()

        for target, status in tracked:
            if status != TargetStatus.BUILT:
                continue
            result = self._state_manager.get_build_result(target)
            if result is None or not result.timestamp:
                continue

            build_time = datetime.fromisoformat(result.timestamp)

            if target not in self._project.features:
                continue

            node = self._project.features[target]
            is_outdated = False

            # Check intent files
            for intent in node.intents:
                if intent.source_path and intent.source_path.exists():
                    mtime = datetime.fromtimestamp(
                        intent.source_path.stat().st_mtime
                    )
                    if mtime > build_time:
                        is_outdated = True
                        break

            # Check validation files
            if not is_outdated:
                for vf in node.validations:
                    if vf.source_path and vf.source_path.exists():
                        mtime = datetime.fromtimestamp(
                            vf.source_path.stat().st_mtime
                        )
                        if mtime > build_time:
                            is_outdated = True
                            break

            if is_outdated:
                outdated.append(target)

        return outdated

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_sandbox_paths(
        self, profile: AgentProfile, target: str, output_dir: str
    ) -> AgentProfile:
        """Return a copy of the profile with sandbox paths scoped to this target."""
        node = self._project.features[target]
        output_path = Path(output_dir).resolve() if output_dir else Path.cwd().resolve()
        build_response_path = self._state_manager.build_response_dir.resolve()
        val_response_path = self._state_manager.val_response_dir.resolve()

        # Write access: output dir, build response dir, validation response dir
        write_paths = [
            str(output_path),
            str(build_response_path),
            str(val_response_path),
        ]

        # Read access: output dir + intent files for target and ancestors + project intent + implementations
        read_paths = [str(output_path)]

        # Target intent files
        for intent in node.intents:
            if intent.source_path:
                read_paths.append(str(Path(intent.source_path).resolve()))

        # Ancestor intent files
        ancestors = self._project.ancestors(target)
        for anc in ancestors:
            if anc in self._project.features:
                for intent in self._project.features[anc].intents:
                    if intent.source_path:
                        read_paths.append(str(Path(intent.source_path).resolve()))

        # Project intent file
        if self._project.project_intent.source_path:
            read_paths.append(
                str(Path(self._project.project_intent.source_path).resolve())
            )

        # Implementations directory
        if self._project.intent_dir:
            impl_dir = self._project.intent_dir / "implementations"
            if impl_dir.exists():
                read_paths.append(str(impl_dir.resolve()))
            # Legacy implementation.ic
            legacy = self._project.intent_dir / "implementation.ic"
            if legacy.exists():
                read_paths.append(str(legacy.resolve()))

        data = profile.model_dump()
        data["sandbox_write_paths"] = write_paths
        data["sandbox_read_paths"] = read_paths
        return AgentProfile(**data)
