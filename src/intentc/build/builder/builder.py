"""Builder: core workflow engine for intentc builds."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

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
from intentc.build.storage import StorageBackend
from intentc.build.storage.backend import GenerationStatus
from intentc.build.validations import ValidationSuite, ValidationSuiteResult
from intentc.core.models import IntentFile, ValidationFile
from intentc.core.project import Project

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

LogFn = Callable[[str], None]
_NOOP_LOG: LogFn = lambda _msg: None

# ---------------------------------------------------------------------------
# BuildOptions
# ---------------------------------------------------------------------------


class BuildOptions(BaseModel):
    """Options controlling a build invocation."""

    target: str = ""
    force: bool = False
    dry_run: bool = False
    output_dir: str = ""
    profile_override: str = ""
    implementation: str = ""


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class Builder:
    """Core workflow engine: builds targets along the DAG."""

    def __init__(
        self,
        project: Project,
        state_manager: StateManager,
        version_control: VersionControl,
        agent_profile: AgentProfile,
        log: LogFn | None = None,
        create_agent: Callable[[AgentProfile], Agent] | None = None,
    ) -> None:
        self._project = project
        self._state_manager = state_manager
        self._version_control = version_control
        self._agent_profile = agent_profile
        self._log = log or _NOOP_LOG
        self._storage: StorageBackend = state_manager.backend

        if create_agent is not None:
            self._create_agent = create_agent
        else:
            # Wrap the default factory to pass our log callback
            _log = self._log

            def _default_factory(profile: AgentProfile) -> Agent:
                return create_from_profile(profile, log=_log)

            self._create_agent = _default_factory

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build(
        self, opts: BuildOptions
    ) -> tuple[list[BuildResult], RuntimeError | None]:
        """Execute the build pipeline.

        Returns (results, error). Error is non-null if any target failed.
        """
        # 1. Determine build set
        build_set = self._determine_build_set(opts)
        if not build_set:
            return ([], None)

        self._log(
            f"Build plan: {len(build_set)} target(s) [{', '.join(build_set)}]"
        )

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
        profile = self._resolve_profile(opts.profile_override)
        opts_dict = opts.model_dump()
        self._storage.create_generation(
            generation_id,
            opts.output_dir,
            profile.name,
            opts_dict,
        )

        self._storage.log_generation_event(
            generation_id,
            f"Build started: {len(build_set)} target(s) in topological order",
        )

        # 5. Resolve output directory
        output_dir = opts.output_dir
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        # 6. Build each target
        results: list[BuildResult] = []
        error: RuntimeError | None = None

        for idx, target in enumerate(build_set):
            self._log(
                f"[{idx + 1}/{len(build_set)}] Building target '{target}'..."
            )

            # Skip check
            status = self._state_manager.get_status(target)
            if status == TargetStatus.BUILT and not opts.force:
                self._log(f"  Skipping '{target}' (already built)")
                self._storage.log_generation_event(
                    generation_id, f"Skipped '{target}': already built"
                )
                continue

            result, target_error = self._build_target(
                target=target,
                generation_id=generation_id,
                output_dir=output_dir,
                profile_override=opts.profile_override,
                implementation=implementation,
            )
            results.append(result)

            # Save result
            self._state_manager.save_build_result(target, result)

            # Read and store agent response, then delete from disk
            self._save_and_cleanup_response(target, result, generation_id)

            if target_error is not None:
                self._storage.log_generation_event(
                    generation_id,
                    f"Build failed for target '{target}': {target_error}",
                )
                error = target_error
                break

            self._log(f"  Target '{target}' completed successfully.")

        # 6. Complete generation
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
            # Do NOT checkpoint — restored files are left unstaged

        self._state_manager.reset(target)
        self._state_manager.mark_dependents_outdated(target, self._project)

    def clean_all(self, output_dir: str) -> None:
        """Reset all state. Does not modify files."""
        self._state_manager.reset_all()

    # ------------------------------------------------------------------
    # Validate
    # ------------------------------------------------------------------

    def validate(
        self, target: str | None, output_dir: str
    ) -> ValidationSuiteResult | list[ValidationSuiteResult]:
        """Run validations independently of the build pipeline."""
        profile = self._resolve_profile("")
        suite = ValidationSuite(
            project=self._project,
            agent_profile=profile,
            output_dir=output_dir,
            val_response_dir=self._state_manager.val_response_dir,
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
        """Walk all built targets and check if source files are newer."""
        outdated: list[str] = []

        for target_name, status in self._state_manager.list_targets():
            if status != TargetStatus.BUILT:
                continue

            result = self._state_manager.get_build_result(target_name)
            if result is None or not result.timestamp:
                continue

            build_time = datetime.fromisoformat(result.timestamp)

            if target_name not in self._project.features:
                continue

            node = self._project.features[target_name]
            is_outdated = False

            # Check .ic files
            for intent in node.intents:
                if intent.source_path and intent.source_path.exists():
                    mtime = datetime.fromtimestamp(
                        intent.source_path.stat().st_mtime
                    )
                    if mtime > build_time:
                        is_outdated = True
                        break

            # Check .icv files
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
                outdated.append(target_name)

        return outdated

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _determine_build_set(self, opts: BuildOptions) -> list[str]:
        """Determine which targets to build, in topological order."""
        topo = self._project.topological_order()
        buildable_statuses = {
            TargetStatus.PENDING,
            TargetStatus.OUTDATED,
            TargetStatus.FAILED,
        }

        if opts.target:
            # Specific target: collect it and its ancestors
            ancestors = self._project.ancestors(opts.target)
            candidates = ancestors | {opts.target}

            if not opts.force:
                candidates = {
                    t
                    for t in candidates
                    if self._state_manager.get_status(t) in buildable_statuses
                }
            # Maintain topological order
            return [t for t in topo if t in candidates]
        else:
            # All targets
            if opts.force:
                return topo
            return [
                t
                for t in topo
                if self._state_manager.get_status(t) in buildable_statuses
            ]

    def _resolve_profile(self, override: str) -> AgentProfile:
        """Resolve agent profile: override > builder's profile."""
        if override:
            return AgentProfile(name=override, provider=self._agent_profile.provider)
        return self._agent_profile

    def _apply_sandbox_paths(
        self,
        profile: AgentProfile,
        target: str,
        output_dir: str,
    ) -> AgentProfile:
        """Scope agent filesystem access based on the project DAG.

        All sandbox paths are absolute (resolved via Path.resolve()).
        """
        output_path = Path(output_dir).resolve()
        build_response_path = self._state_manager.build_response_dir.resolve()
        val_response_path = self._state_manager.val_response_dir.resolve()

        write_paths = [
            str(output_path),
            str(build_response_path),
            str(val_response_path),
        ]

        read_paths = [str(output_path)]

        # Add intent files for target and ancestors
        if target in self._project.features:
            node = self._project.features[target]
            for intent in node.intents:
                if intent.source_path:
                    read_paths.append(str(intent.source_path.resolve()))

        ancestors = self._project.ancestors(target) if target in self._project.features else set()
        for anc in ancestors:
            if anc in self._project.features:
                for intent in self._project.features[anc].intents:
                    if intent.source_path:
                        read_paths.append(str(intent.source_path.resolve()))

        # Project intent file
        if self._project.project_intent.source_path:
            read_paths.append(
                str(self._project.project_intent.source_path.resolve())
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

        return profile.model_copy(
            update={
                "sandbox_write_paths": write_paths,
                "sandbox_read_paths": read_paths,
            }
        )

    def _build_target(
        self,
        target: str,
        generation_id: str,
        output_dir: str,
        profile_override: str,
        implementation: object | None,
    ) -> tuple[BuildResult, RuntimeError | None]:
        """Build a single target through the step pipeline."""
        steps: list[BuildStep] = []
        commit_id = ""
        git_diff = ""
        previous_errors: list[str] = []
        build_response: BuildResponse | None = None

        profile = self._resolve_profile(profile_override)
        node = self._project.features.get(target)
        intent = (
            node.intents[0]
            if node and node.intents
            else IntentFile(name=target, body="")
        )
        validations = node.validations if node else []

        retries = profile.retries or 1  # total attempts

        for attempt in range(retries):
            steps_this_attempt: list[BuildStep] = []
            failed = False

            if attempt > 0:
                self._log(
                    f"  Retry {attempt}/{retries - 1} for target '{target}'..."
                )

            # Step 1: resolve_deps
            dep_step, dep_names = self._step_resolve_deps(target)
            steps_this_attempt.append(dep_step)

            # Step 2: build
            sandboxed_profile = self._apply_sandbox_paths(
                profile, target, output_dir
            )
            agent = self._create_agent(sandboxed_profile)

            response_file = str(
                self._state_manager.build_response_dir
                / f"response-{target.replace('/', '_')}-{generation_id[:8]}.json"
            )

            build_ctx = BuildContext(
                intent=intent,
                validations=validations,
                output_dir=output_dir,
                generation_id=generation_id,
                dependency_names=dep_names,
                project_intent=self._project.project_intent,
                implementation=implementation,
                response_file_path=response_file,
                previous_errors=previous_errors,
            )

            build_step, build_response = self._step_build(agent, build_ctx)
            steps_this_attempt.append(build_step)

            if build_step.status != "success":
                previous_errors.append(build_step.summary)
                steps = steps_this_attempt
                failed = True
                if attempt < retries - 1:
                    continue
                # Last attempt failed
                return self._make_result(
                    target, generation_id, "failed", steps, commit_id, git_diff
                ), RuntimeError(
                    f"Build failed for target '{target}': {build_step.summary}"
                )

            # Step 3: validate
            if validations:
                val_step = self._step_validate(
                    target, profile, output_dir
                )
                steps_this_attempt.append(val_step)

                if val_step.status != "success":
                    previous_errors.append(val_step.summary)
                    steps = steps_this_attempt
                    failed = True
                    if attempt < retries - 1:
                        continue
                    # Last attempt failed
                    return self._make_result(
                        target, generation_id, "failed", steps, commit_id, git_diff
                    ), RuntimeError(
                        f"Build failed for target '{target}': {val_step.summary}"
                    )

            # All steps succeeded
            steps = steps_this_attempt

            # Step 4: checkpoint
            ckpt_step, commit_id, git_diff = self._step_checkpoint(
                target, generation_id
            )
            steps.append(ckpt_step)
            break

        result, _ = self._make_result(
            target, generation_id, "built", steps, commit_id, git_diff
        ), None

        # Store file manifest from build response
        result._build_response = build_response  # type: ignore[attr-defined]
        result._git_diff = git_diff  # type: ignore[attr-defined]

        return result, None

    def _step_resolve_deps(
        self, target: str
    ) -> tuple[BuildStep, list[str]]:
        """Resolve dependency names for a target."""
        start = datetime.now()
        dep_names: list[str] = []
        if target in self._project.features:
            node = self._project.features[target]
            dep_names = list(node.depends_on)

        self._log(f"  resolve_deps: {dep_names or '(none)'}")
        duration = (datetime.now() - start).total_seconds()
        return (
            BuildStep(
                phase="resolve_deps",
                status="success",
                duration_secs=duration,
                summary=f"Dependencies: {dep_names or '(none)'}",
            ),
            dep_names,
        )

    def _step_build(
        self, agent: Agent, ctx: BuildContext
    ) -> tuple[BuildStep, BuildResponse | None]:
        """Invoke the agent to build."""
        start = datetime.now()
        self._log(f"  build: invoking agent...")

        try:
            response = agent.build(ctx)
            duration = (datetime.now() - start).total_seconds()

            if response.status == "success":
                return (
                    BuildStep(
                        phase="build",
                        status="success",
                        duration_secs=duration,
                        summary=response.summary,
                    ),
                    response,
                )
            else:
                return (
                    BuildStep(
                        phase="build",
                        status="failed",
                        duration_secs=duration,
                        summary=response.summary,
                    ),
                    response,
                )
        except AgentError as exc:
            duration = (datetime.now() - start).total_seconds()
            self._log(f"  build: agent error: {exc}")
            return (
                BuildStep(
                    phase="build",
                    status="failed",
                    duration_secs=duration,
                    summary=str(exc),
                ),
                None,
            )

    def _step_validate(
        self,
        target: str,
        profile: AgentProfile,
        output_dir: str,
    ) -> BuildStep:
        """Run validations for a target."""
        start = datetime.now()
        self._log(f"  validate: running validations...")

        suite = ValidationSuite(
            project=self._project,
            agent_profile=profile,
            output_dir=output_dir,
            val_response_dir=self._state_manager.val_response_dir,
            storage_backend=self._storage,
            log=self._log,
        )
        result = suite.validate_feature(target)
        duration = (datetime.now() - start).total_seconds()

        if result.passed:
            self._log(f"  validate: passed ({result.summary})")
            return BuildStep(
                phase="validate",
                status="success",
                duration_secs=duration,
                summary=result.summary,
            )
        else:
            self._log(f"  validate: failed ({result.summary})")
            return BuildStep(
                phase="validate",
                status="failed",
                duration_secs=duration,
                summary=result.summary,
            )

    def _step_checkpoint(
        self, target: str, generation_id: str
    ) -> tuple[BuildStep, str, str]:
        """Checkpoint via version control."""
        start = datetime.now()
        message = f"build {target} [gen:{generation_id}]"
        self._log(f"  checkpoint: committing '{message}'")

        try:
            commit_id = self._version_control.checkpoint(message)
            git_diff = ""
            try:
                git_diff = self._version_control.diff(
                    f"{commit_id}~1", commit_id
                )
            except Exception:
                pass  # diff may fail if first commit

            duration = (datetime.now() - start).total_seconds()
            self._log(f"  checkpoint: {commit_id[:8]}")

            return (
                BuildStep(
                    phase="checkpoint",
                    status="success",
                    duration_secs=duration,
                    summary=f"Committed {commit_id[:8]}",
                ),
                commit_id,
                git_diff,
            )
        except Exception as exc:
            duration = (datetime.now() - start).total_seconds()
            self._log(f"  checkpoint: failed: {exc}")
            return (
                BuildStep(
                    phase="checkpoint",
                    status="failed",
                    duration_secs=duration,
                    summary=str(exc),
                ),
                "",
                "",
            )

    def _make_result(
        self,
        target: str,
        generation_id: str,
        status: str,
        steps: list[BuildStep],
        commit_id: str,
        git_diff: str,
    ) -> BuildResult:
        """Build a BuildResult from steps."""
        total_duration = sum(s.duration_secs for s in steps)
        return BuildResult(
            target=target,
            generation_id=generation_id,
            status=status,
            commit_id=commit_id,
            total_duration_secs=total_duration,
            timestamp=datetime.now().isoformat(),
            steps=steps,
        )

    def _save_and_cleanup_response(
        self,
        target: str,
        result: BuildResult,
        generation_id: str,
    ) -> None:
        """Read the build agent response file, store it, then delete."""
        build_response: BuildResponse | None = getattr(
            result, "_build_response", None
        )
        git_diff: str = getattr(result, "_git_diff", "")

        files_created: list[str] = []
        files_modified: list[str] = []
        if build_response:
            files_created = build_response.files_created
            files_modified = build_response.files_modified

        # Save build result with extra metadata
        self._storage.save_build_result(
            target,
            result,
            git_diff=git_diff,
            files_created=files_created,
            files_modified=files_modified,
        )

        # Read response file from disk, persist, and clean up
        response_file = (
            self._state_manager.build_response_dir
            / f"response-{target.replace('/', '_')}-{generation_id[:8]}.json"
        )
        if response_file.exists():
            try:
                with open(response_file, "r", encoding="utf-8") as f:
                    response_json = json.load(f)
                self._storage.save_agent_response(
                    build_result_id=None,
                    validation_result_id=None,
                    response_type="build",
                    response_json=response_json,
                )
            except (json.JSONDecodeError, OSError):
                pass
            finally:
                try:
                    os.remove(response_file)
                except OSError:
                    pass
