"""Builder — core workflow engine for intentc builds."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field

from intentc.build.agents import (
    Agent,
    AgentError,
    AgentProfile,
    BuildContext,
    BuildResponse,
    create_from_profile,
)
from intentc.build.state import (
    BuildResult,
    BuildStep,
    StateManager,
    TargetStatus,
    VersionControl,
)
from intentc.build.storage.backend import GenerationStatus, StorageBackend
from intentc.build.validations import ValidationSuite, ValidationSuiteResult
from intentc.core.project import Project
from intentc.core.types import IntentFile


class BuildOptions(BaseModel):
    """Options controlling a build invocation."""

    model_config = {"extra": "ignore"}

    target: str = ""
    force: bool = False
    dry_run: bool = False
    output_dir: str = ""
    profile_override: str = ""
    implementation: str = ""


class Builder:
    """Core workflow engine — builds targets along the project DAG."""

    def __init__(
        self,
        project: Project,
        state_manager: StateManager,
        version_control: VersionControl,
        agent_profile: AgentProfile,
    ) -> None:
        self._project = project
        self._state_manager = state_manager
        self._version_control = version_control
        self._agent_profile = agent_profile
        self._create_agent: Callable[[AgentProfile], Agent] = create_from_profile
        self._storage: StorageBackend = state_manager._backend

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build(
        self, opts: BuildOptions
    ) -> tuple[list[BuildResult], RuntimeError | None]:
        """Execute a build. Returns (results, error_or_none)."""
        # 1. Determine build set
        build_set = self._determine_build_set(opts)
        if not build_set:
            return ([], None)

        # 2. Dry run
        if opts.dry_run:
            results = []
            for target in build_set:
                status = self._state_manager.get_status(target)
                results.append(
                    BuildResult(
                        generation_id="",
                        target=target,
                        status=status,
                        timestamp=datetime.utcnow(),
                    )
                )
            return (results, None)

        # 3. Resolve implementation
        impl = None
        if opts.implementation:
            impl = self._project.resolve_implementation(opts.implementation)
        else:
            impl = self._project.resolve_implementation(None)

        # 4. Generate generation ID
        generation_id = str(uuid.uuid4())
        opts_dict: dict[str, Any] = {
            "target": opts.target,
            "force": opts.force,
            "dry_run": opts.dry_run,
            "output_dir": opts.output_dir,
            "profile_override": opts.profile_override,
            "implementation": opts.implementation,
        }
        self._storage.create_generation(
            generation_id=generation_id,
            output_dir=opts.output_dir,
            profile_name=self._agent_profile.name,
            options=opts_dict,
        )

        # 5. Resolve output directory
        if opts.output_dir:
            os.makedirs(opts.output_dir, exist_ok=True)

        # 6. Build each target
        self._storage.log_generation_event(
            generation_id, f"Build plan: {build_set}"
        )

        results: list[BuildResult] = []
        error: RuntimeError | None = None

        for target in build_set:
            # Skip check
            current_status = self._state_manager.get_status(target)
            if current_status == TargetStatus.BUILT and not opts.force:
                self._storage.log_generation_event(
                    generation_id, f"Skipping {target}: already built"
                )
                continue

            result = self._build_target(target, opts, generation_id, impl)
            results.append(result)

            # Save result
            self._save_result(target, result, generation_id)

            if result.status == TargetStatus.FAILED:
                last_step_summary = ""
                if result.steps:
                    last_step_summary = result.steps[-1].summary
                error = RuntimeError(
                    f"Build failed for target '{target}': {last_step_summary}"
                )
                self._storage.log_generation_event(
                    generation_id, f"Build failed for target '{target}'"
                )
                break

        # Complete generation
        gen_status = (
            GenerationStatus.FAILED if error else GenerationStatus.COMPLETED
        )
        self._storage.complete_generation(generation_id, gen_status)

        return (results, error)

    # ------------------------------------------------------------------
    # Clean
    # ------------------------------------------------------------------

    def clean(self, target: str, output_dir: str) -> None:
        """Revert a target's generated code and reset its state."""
        result = self._state_manager.get_build_result(target)
        if result is None:
            return

        if result.commit_id:
            self._version_control.restore(result.commit_id)

        self._state_manager.reset(target)
        self._state_manager.mark_dependents_outdated(target, self._project)

    def clean_all(self, output_dir: str) -> None:
        """Reset all state without modifying files."""
        self._state_manager.reset_all()

    # ------------------------------------------------------------------
    # Validate
    # ------------------------------------------------------------------

    def validate(
        self, target: str, output_dir: str
    ) -> ValidationSuiteResult | list[ValidationSuiteResult]:
        """Run validations independently of the build pipeline."""
        profile = self._resolve_profile("")
        suite = ValidationSuite(
            project=self._project,
            agent_profile=profile,
            output_dir=output_dir,
            val_response_dir=self._state_manager.val_response_dir,
            storage_backend=self._storage,
        )
        if target:
            return suite.validate_feature(target)
        return suite.validate_project()

    # ------------------------------------------------------------------
    # Invalidation
    # ------------------------------------------------------------------

    def detect_outdated(self) -> list[str]:
        """Walk all built targets and check if source files are newer."""
        outdated: list[str] = []
        for target, status in self._state_manager.list_targets():
            if status != TargetStatus.BUILT:
                continue
            result = self._state_manager.get_build_result(target)
            if result is None:
                continue
            node = self._project.features.get(target)
            if node is None:
                continue
            build_ts = result.timestamp
            for intent in node.intents:
                if intent.source_path and intent.source_path.exists():
                    mtime = datetime.utcfromtimestamp(
                        intent.source_path.stat().st_mtime
                    )
                    if mtime > build_ts:
                        outdated.append(target)
                        break
            else:
                # Check validation files
                for vf in node.validations:
                    if vf.source_path and vf.source_path.exists():
                        mtime = datetime.utcfromtimestamp(
                            vf.source_path.stat().st_mtime
                        )
                        if mtime > build_ts:
                            outdated.append(target)
                            break
        return outdated

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _determine_build_set(self, opts: BuildOptions) -> list[str]:
        """Determine which targets to build, in topological order."""
        topo = self._project.topological_order()

        if opts.target:
            # Collect target + ancestors
            candidates = self._project.ancestors(opts.target) | {opts.target}
            if opts.force:
                selected = candidates
            else:
                selected = set()
                for t in candidates:
                    status = self._state_manager.get_status(t)
                    if status in (
                        TargetStatus.PENDING,
                        TargetStatus.OUTDATED,
                        TargetStatus.FAILED,
                    ):
                        selected.add(t)
        else:
            if opts.force:
                selected = set(self._project.features.keys())
            else:
                selected = set()
                for t in self._project.features:
                    status = self._state_manager.get_status(t)
                    if status in (TargetStatus.PENDING, TargetStatus.OUTDATED):
                        selected.add(t)

        # Return in topological order
        return [t for t in topo if t in selected]

    def _resolve_profile(self, profile_override: str) -> AgentProfile:
        """Resolve agent profile: override > builder's default."""
        if profile_override:
            return AgentProfile(
                name=profile_override, provider=self._agent_profile.provider
            )
        return self._agent_profile

    def _apply_sandbox_paths(
        self,
        profile: AgentProfile,
        target: str,
        opts: BuildOptions,
    ) -> AgentProfile:
        """Compute filesystem boundaries and attach to profile."""
        sandbox_write: list[str] = []
        sandbox_read: list[str] = []

        if opts.output_dir:
            output_dir_abs = str(Path(opts.output_dir).resolve())
            sandbox_write.append(output_dir_abs)
            sandbox_read.append(output_dir_abs)

        sandbox_write.append(
            str(Path(self._state_manager.build_response_dir).resolve())
        )
        sandbox_write.append(
            str(Path(self._state_manager.val_response_dir).resolve())
        )

        intent_dir = self._project.intent_dir
        if intent_dir is not None:
            ancestors = self._project.ancestors(target)
            for feat in ancestors | {target}:
                feat_dir = intent_dir / feat
                if feat_dir.is_dir():
                    sandbox_read.append(str(feat_dir.resolve()))

            project_ic = intent_dir / "project.ic"
            if project_ic.exists():
                sandbox_read.append(str(project_ic.resolve()))

            impl_dir = intent_dir / "implementations"
            if impl_dir.is_dir():
                sandbox_read.append(str(impl_dir.resolve()))

            # Backward compat: legacy implementation.ic
            impl_ic = intent_dir / "implementation.ic"
            if impl_ic.exists():
                sandbox_read.append(str(impl_ic.resolve()))

        return profile.model_copy(
            update={
                "sandbox_write_paths": sandbox_write,
                "sandbox_read_paths": sandbox_read,
            }
        )

    def _build_target(
        self,
        target: str,
        opts: BuildOptions,
        generation_id: str,
        implementation: Any,
    ) -> BuildResult:
        """Execute the full build pipeline for a single target."""
        node = self._project.features[target]
        steps: list[BuildStep] = []
        failed = False

        # Resolve agent profile
        profile = self._resolve_profile(opts.profile_override)
        profile = self._apply_sandbox_paths(profile, target, opts)
        agent = self._create_agent(profile)

        # Step 1: resolve_deps
        step_start = datetime.utcnow()
        dep_names = list(node.depends_on)
        steps.append(
            BuildStep(
                phase="resolve_deps",
                status="success",
                duration_secs=(datetime.utcnow() - step_start).total_seconds(),
                summary=f"Dependencies: {dep_names}" if dep_names else "No dependencies",
            )
        )

        # Step 2: build
        if not failed:
            step_start = datetime.utcnow()
            response_file = (
                self._state_manager.build_response_dir
                / f"build-{target.replace('/', '_')}-{generation_id[:8]}.json"
            )
            intent = node.intents[0] if node.intents else IntentFile(name=target)
            ctx = BuildContext(
                intent=intent,
                validations=node.validations,
                output_dir=opts.output_dir,
                generation_id=generation_id,
                dependency_names=dep_names,
                project_intent=self._project.project_intent,
                implementation=implementation,
                response_file_path=str(response_file.resolve()),
            )

            build_error: AgentError | None = None
            build_response: BuildResponse | None = None
            for attempt in range(profile.retries):
                try:
                    build_response = agent.build(ctx)
                    build_error = None
                    break
                except AgentError as exc:
                    build_error = exc
                    if attempt < profile.retries - 1:
                        continue

            if build_error is not None:
                steps.append(
                    BuildStep(
                        phase="build",
                        status="failure",
                        duration_secs=(datetime.utcnow() - step_start).total_seconds(),
                        summary=f"Agent error: {build_error}",
                    )
                )
                failed = True
            else:
                steps.append(
                    BuildStep(
                        phase="build",
                        status="success",
                        duration_secs=(datetime.utcnow() - step_start).total_seconds(),
                        summary=build_response.summary if build_response else "Build completed",
                    )
                )

        # Step 3: validate
        commit_id = ""
        git_diff = ""
        if not failed:
            all_validations = []
            for vf in node.validations:
                all_validations.extend(vf.validations)

            if all_validations:
                step_start = datetime.utcnow()
                suite = ValidationSuite(
                    project=self._project,
                    agent_profile=profile,
                    output_dir=opts.output_dir,
                    val_response_dir=self._state_manager.val_response_dir,
                    storage_backend=self._storage,
                )
                suite_result = suite.validate_feature(target)
                if not suite_result.passed:
                    steps.append(
                        BuildStep(
                            phase="validate",
                            status="failure",
                            duration_secs=(datetime.utcnow() - step_start).total_seconds(),
                            summary=suite_result.summary,
                        )
                    )
                    failed = True
                else:
                    steps.append(
                        BuildStep(
                            phase="validate",
                            status="success",
                            duration_secs=(datetime.utcnow() - step_start).total_seconds(),
                            summary=suite_result.summary,
                        )
                    )

        # Step 4: checkpoint
        if not failed:
            step_start = datetime.utcnow()
            try:
                message = f"build {target} [gen:{generation_id}]"
                commit_id = self._version_control.checkpoint(message)
                try:
                    git_diff = self._version_control.diff(
                        f"{commit_id}~1", commit_id
                    )
                except RuntimeError:
                    git_diff = ""
                steps.append(
                    BuildStep(
                        phase="checkpoint",
                        status="success",
                        duration_secs=(datetime.utcnow() - step_start).total_seconds(),
                        summary=f"Committed: {commit_id[:12]}",
                    )
                )
            except RuntimeError as exc:
                steps.append(
                    BuildStep(
                        phase="checkpoint",
                        status="failure",
                        duration_secs=(datetime.utcnow() - step_start).total_seconds(),
                        summary=str(exc),
                    )
                )
                failed = True

        total_duration = sum(s.duration_secs for s in steps)
        status = TargetStatus.FAILED if failed else TargetStatus.BUILT

        result = BuildResult(
            generation_id=generation_id,
            target=target,
            status=status,
            commit_id=commit_id,
            total_duration_secs=total_duration,
            timestamp=datetime.utcnow(),
            steps=steps,
        )
        result._git_diff = git_diff  # type: ignore[attr-defined]
        result._build_response = build_response  # type: ignore[attr-defined]
        result._response_file = str(response_file) if 'response_file' in dir() else ""  # type: ignore[attr-defined]
        return result

    def _save_result(
        self, target: str, result: BuildResult, generation_id: str
    ) -> None:
        """Persist a build result, steps, agent response, and clean up."""
        git_diff = getattr(result, "_git_diff", "")
        build_response: BuildResponse | None = getattr(
            result, "_build_response", None
        )
        response_file = getattr(result, "_response_file", "")

        files_created = (
            build_response.files_created if build_response else None
        )
        files_modified = (
            build_response.files_modified if build_response else None
        )

        build_result_id = self._storage.save_build_result(
            target=target,
            result=result,
            intent_version_id=None,
            git_diff=git_diff or None,
            files_created=files_created,
            files_modified=files_modified,
        )

        # Save build steps
        for i, step in enumerate(result.steps):
            self._storage.save_build_step(
                build_result_id=build_result_id,
                step=step,
                log=step.summary,
                step_order=i,
            )

        # Save agent response and delete response file
        if response_file:
            resp_path = Path(response_file)
            if resp_path.exists():
                try:
                    resp_data = json.loads(
                        resp_path.read_text(encoding="utf-8")
                    )
                    self._storage.save_agent_response(
                        build_result_id=build_result_id,
                        validation_result_id=None,
                        response_type="build",
                        response_json=resp_data,
                    )
                    resp_path.unlink()
                except (json.JSONDecodeError, OSError):
                    pass

        # Update target status
        self._state_manager.set_status(target, result.status)
