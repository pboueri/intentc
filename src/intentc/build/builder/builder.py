"""The core workflow engine of intentc: walks the project DAG, invokes agents to
build each target, runs validations, checkpoints successful builds, and manages
all project state through the `StateManager`.

A target is only marked `built` and checkpointed after both the agent invocation
and all validations succeed. If anything fails, the target is marked `failed`
and the DAG walk stops immediately -- failed output is left on disk, uncommitted.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Union

from pydantic import BaseModel, ConfigDict

from intentc.build.agents import (
    Agent,
    AgentError,
    AgentProfile,
    BuildContext,
    BuildResponse,
    create_from_profile,
)
from intentc.build.state import BuildResult, BuildStep, StateManager, TargetStatus, VersionControl
from intentc.build.state.state import response_file_name
from intentc.build.storage import GenerationStatus, StorageBackend
from intentc.build.validations import ValidationSuite, ValidationSuiteResult
from intentc.core import Implementation, IntentFile, Project, content_hash

LogFn = Callable[[str], None]


def _noop_log(_message: str) -> None:
    return None


def _now_iso() -> str:
    return datetime.now().isoformat()


# ---------------------------------------------------------------------------
# BuildOptions
# ---------------------------------------------------------------------------


class BuildOptions(BaseModel):
    """Options controlling a single `Builder.build()` invocation."""

    model_config = ConfigDict(extra="ignore")

    target: str = ""
    force: bool = False
    dry_run: bool = False
    output_dir: str = ""
    profile_override: str = ""
    implementation: str = ""


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

_ACTIVE_STATUSES = (TargetStatus.PENDING, TargetStatus.OUTDATED, TargetStatus.FAILED)


class Builder:
    """Orchestrates incremental builds along the project DAG."""

    def __init__(
        self,
        project: Project,
        state_manager: StateManager,
        version_control: VersionControl,
        agent_profile: AgentProfile,
        create_agent: Optional[Callable[[AgentProfile], Agent]] = None,
        log: Optional[LogFn] = None,
    ) -> None:
        self._project = project
        self._state_manager = state_manager
        self._version_control = version_control
        self._agent_profile = agent_profile
        self._storage: StorageBackend = state_manager.backend
        self.log: LogFn = log or _noop_log
        if create_agent is not None:
            self._create_agent = create_agent
        else:
            self._create_agent = lambda profile: create_from_profile(profile, log=self.log)

    # -- Build ----------------------------------------------------------------

    def build(self, opts: BuildOptions) -> tuple[list[BuildResult], Optional[Exception]]:
        self.refresh_outdated()

        build_set = self._determine_build_set(opts.target, opts.force)
        if not build_set:
            self.log("Nothing to build — all targets are up to date.")
            return [], None

        if opts.dry_run:
            return [
                BuildResult(target=target, status=self._state_manager.get_status(target))
                for target in build_set
            ], None

        implementation = self._project.resolve_implementation(
            opts.implementation if opts.implementation else None
        )

        generation_id = str(uuid.uuid4())
        self._storage.create_generation(
            generation_id, opts.output_dir, self._agent_profile.name, opts.model_dump()
        )

        output_path = Path(opts.output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        results: list[BuildResult] = []
        total = len(build_set)
        self.log(f"Build plan: {total} target(s) -> {', '.join(build_set)}")
        self._storage.log_generation_event(generation_id, f"Build plan: {', '.join(build_set)}")

        for index, target in enumerate(build_set, start=1):
            status = self._state_manager.get_status(target)
            if status == TargetStatus.BUILT and not opts.force:
                self.log(f"[{index}/{total}] Skipping '{target}' (already built)")
                self._storage.log_generation_event(generation_id, f"Skipped '{target}': already built")
                continue

            self.log(f"[{index}/{total}] Building '{target}'...")
            result, error = self._build_target(target, opts, generation_id, implementation, output_path)
            results.append(result)

            if error is not None:
                self._storage.log_generation_event(generation_id, str(error))
                self._storage.complete_generation(generation_id, GenerationStatus.FAILED)
                return results, error

            self.log(
                f"  ✓ '{target}' built in {result.total_duration_secs:.1f}s "
                f"({result.attempts} attempt(s))"
            )

        self._storage.complete_generation(generation_id, GenerationStatus.COMPLETED)
        return results, None

    def next_targets(self) -> list[str]:
        """Features the user can build next: not yet built, all dependencies built."""
        built = {target for target, status in self._state_manager.list_targets() if status == TargetStatus.BUILT}
        return self._project.buildable_after(built)

    # -- Clean ------------------------------------------------------------------

    def clean(self, target: str, output_dir: str) -> None:
        """Revert a target's generated code and reset its state. Not destructive:
        creates a new revert commit rather than rewriting history."""
        result = self._state_manager.get_build_result(target)
        if result is None:
            return
        if result.commit_id:
            self.log(f"Reverting '{target}' to the state before commit {result.commit_id}")
            self._version_control.restore(f"{result.commit_id}~1")
        self._state_manager.reset(target)
        self._state_manager.mark_dependents_outdated(target, self._project)
        self.log(f"Cleaned '{target}'; descendants marked outdated")

    def clean_all(self, output_dir: str) -> None:
        """Reset all state for the output directory. Does not modify files."""
        self._state_manager.reset_all()
        self.log(f"Cleared all build state for output directory '{output_dir}'")

    # -- Validate ---------------------------------------------------------------

    def validate(
        self, target: str, output_dir: str
    ) -> Union[ValidationSuiteResult, list[ValidationSuiteResult]]:
        """Run validations independently of the build pipeline. Does not modify state."""
        implementation = self._project.resolve_implementation(None)
        suite = ValidationSuite(
            project=self._project,
            agent_profile=self._agent_profile,
            output_dir=output_dir,
            storage_backend=self._storage,
            val_response_dir=self._state_manager.val_response_dir,
            log=self.log,
            implementation=implementation,
            create_agent=self._create_agent,
        )
        if target:
            return suite.validate_feature(target)
        return suite.validate_project()

    # -- Invalidation -------------------------------------------------------------

    def detect_outdated(self) -> list[str]:
        """Directly-stale built targets, in topological order. Does not modify state."""
        stale: list[str] = []
        for target, status in self._state_manager.list_targets():
            if status != TargetStatus.BUILT or target not in self._project.features:
                continue
            result = self._state_manager.get_build_result(target)
            if result is None:
                continue
            source_paths = self._project.source_files(target)
            if result.source_hash:
                if content_hash(source_paths) != result.source_hash:
                    stale.append(target)
            elif self._sources_newer_than(source_paths, result.timestamp):
                stale.append(target)

        order = self._project.topological_order()
        order_index = {feature: index for index, feature in enumerate(order)}
        stale.sort(key=lambda feature: order_index.get(feature, len(order)))
        return stale

    def refresh_outdated(self) -> list[str]:
        """Mark stale targets and their descendants outdated. Called at the start of
        every build() so an edited intent is rebuilt without --force."""
        changed: list[str] = []
        for target in self.detect_outdated():
            self._state_manager.set_status(target, TargetStatus.OUTDATED)
            self.log(f"  Marked '{target}' outdated: intent changed")
            changed.append(target)
            for descendant in self._project.descendants(target):
                descendant_status = self._state_manager.get_status(descendant)
                if descendant_status in (TargetStatus.PENDING, TargetStatus.FAILED):
                    continue
                if descendant_status != TargetStatus.OUTDATED:
                    self._state_manager.set_status(descendant, TargetStatus.OUTDATED)
                    self.log(f"  Marked '{descendant}' outdated: dependency '{target}' changed")
                    changed.append(descendant)
        return changed

    # -- Internals: build set ----------------------------------------------------

    def _determine_build_set(self, target: str, force: bool) -> list[str]:
        order = self._project.topological_order()
        if target:
            candidates = self._project.ancestors(target) | {target}
        else:
            candidates = set(self._project.features.keys())
        if not force:
            candidates = {
                feature
                for feature in candidates
                if self._state_manager.get_status(feature) in _ACTIVE_STATUSES
            }
        return [feature for feature in order if feature in candidates]

    @staticmethod
    def _sources_newer_than(paths: list[Path], timestamp: str) -> bool:
        if not timestamp:
            return False
        try:
            build_time = datetime.fromisoformat(timestamp)
        except ValueError:
            return False
        for path in paths:
            if not path.exists():
                continue
            if datetime.fromtimestamp(path.stat().st_mtime) > build_time:
                return True
        return False

    # -- Internals: per-target build ---------------------------------------------

    def _resolve_profile(self, opts: BuildOptions) -> AgentProfile:
        if opts.profile_override:
            return self._agent_profile.model_copy(update={"name": opts.profile_override})
        return self._agent_profile

    def _apply_sandbox_paths(
        self,
        profile: AgentProfile,
        target: str,
        output_dir: Path,
        implementation: Optional[Implementation] = None,
    ) -> AgentProfile:
        write_paths = [
            str(output_dir.resolve()),
            str(self._state_manager.build_response_dir.resolve()),
            str(self._state_manager.val_response_dir.resolve()),
        ]

        read_paths = [str(output_dir.resolve())]
        for feature in [target, *sorted(self._project.ancestors(target))]:
            node = self._project.features.get(feature)
            if node is None:
                continue
            for intent in node.intents:
                if intent.source_path is not None:
                    read_paths.append(str(Path(intent.source_path).resolve()))

        project_intent = self._project.project_intent
        if project_intent.source_path is not None:
            read_paths.append(str(Path(project_intent.source_path).resolve()))

        intent_dir = self._project.intent_dir
        if intent_dir is not None:
            impl_dir = Path(intent_dir) / "implementations"
            if impl_dir.exists():
                read_paths.append(str(impl_dir.resolve()))
            legacy_impl = Path(intent_dir) / "implementation.ic"
            if legacy_impl.exists():
                read_paths.append(str(legacy_impl.resolve()))

        for artifact in self._project.artifacts_for(target, implementation):
            for resolved in artifact.resolved_paths:
                read_paths.append(str(Path(resolved).resolve()))

        return profile.model_copy(
            update={"sandbox_write_paths": write_paths, "sandbox_read_paths": read_paths}
        )

    def _record_source_versions(self, target: str) -> None:
        node = self._project.features[target]
        for intent in node.intents:
            if intent.source_path is None:
                continue
            self._storage.record_intent_version(
                intent.name, str(intent.source_path), content_hash([intent.source_path])
            )
        for validation_file in node.validations:
            if validation_file.source_path is None:
                continue
            self._storage.record_validation_version(
                target, str(validation_file.source_path), content_hash([validation_file.source_path])
            )

    def _build_target(
        self,
        target: str,
        opts: BuildOptions,
        generation_id: str,
        implementation: Optional[Implementation],
        output_path: Path,
    ) -> tuple[BuildResult, Optional[Exception]]:
        node = self._project.features[target]
        self._record_source_versions(target)
        source_hash = content_hash(self._project.source_files(target))

        profile = self._resolve_profile(opts)
        sandboxed_profile = self._apply_sandbox_paths(profile, target, output_path, implementation)
        agent = self._create_agent(sandboxed_profile)

        dependency_names = node.depends_on
        feature_intent = node.intents[0] if node.intents else IntentFile(name=target, body="")
        has_validations = any(vf.validations for vf in node.validations)
        artifacts = self._project.artifacts_for(target, implementation)

        previous_errors: list[str] = []
        steps: list[BuildStep] = []
        final_build_response: Optional[BuildResponse] = None
        commit_id = ""
        git_diff: Optional[str] = None
        attempts = 0
        target_failed = True

        for attempt in range(1, profile.retries + 1):
            attempts = attempt
            steps = []
            target_failed = False

            deps_start = time.monotonic()
            steps.append(
                BuildStep(
                    phase="resolve_deps",
                    status="success",
                    duration_secs=time.monotonic() - deps_start,
                    summary=f"{len(dependency_names)} dependency(ies)",
                )
            )

            ctx = BuildContext(
                intent=feature_intent,
                validations=node.validations,
                output_dir=str(output_path),
                generation_id=generation_id,
                dependency_names=dependency_names,
                project_intent=self._project.project_intent,
                implementation=implementation,
                response_file_path=str(self._state_manager.build_response_dir / response_file_name(target)),
                previous_errors=list(previous_errors),
                feature_path=target,
                artifacts=artifacts,
            )

            build_start = time.monotonic()
            try:
                build_response = agent.build(ctx)
            except AgentError as exc:
                summary = f"agent error: {exc}"
                steps.append(
                    BuildStep(
                        phase="build",
                        status="failed",
                        duration_secs=time.monotonic() - build_start,
                        summary=summary,
                    )
                )
                previous_errors.append(summary)
                target_failed = True
                self.log(f"  Build step failed for '{target}': {summary}")
                continue

            final_build_response = build_response
            steps.append(
                BuildStep(
                    phase="build",
                    status="success",
                    duration_secs=time.monotonic() - build_start,
                    summary=build_response.summary,
                )
            )
            self.log(f"  Build step succeeded for '{target}'")

            if has_validations:
                validate_start = time.monotonic()
                suite = ValidationSuite(
                    project=self._project,
                    agent_profile=profile,
                    output_dir=str(output_path),
                    val_response_dir=self._state_manager.val_response_dir,
                    storage_backend=self._storage,
                    log=self.log,
                    implementation=implementation,
                    generation_id=generation_id,
                    create_agent=self._create_agent,
                )
                self.log(f"  Validating '{target}'...")
                suite_result = suite.validate_feature(target)
                duration = time.monotonic() - validate_start
                if suite_result.passed:
                    steps.append(
                        BuildStep(
                            phase="validate", status="success", duration_secs=duration, summary=suite_result.summary
                        )
                    )
                    self.log(f"  Validation passed for '{target}': {suite_result.summary}")
                else:
                    summary = f"validation failed: {suite_result.summary}"
                    steps.append(
                        BuildStep(phase="validate", status="failed", duration_secs=duration, summary=summary)
                    )
                    previous_errors.append(summary)
                    target_failed = True
                    self.log(f"  Validation failed for '{target}': {summary}")
                    continue

            checkpoint_start = time.monotonic()
            commit_id = self._version_control.checkpoint(f"build: {target} (generation {generation_id})")
            git_diff = self._version_control.diff(f"{commit_id}~1", commit_id)
            steps.append(
                BuildStep(
                    phase="checkpoint",
                    status="success",
                    duration_secs=time.monotonic() - checkpoint_start,
                    summary=f"commit {commit_id}",
                )
            )
            self.log(f"  Checkpointed '{target}' at {commit_id}")
            break

        total_duration = sum(step.duration_secs for step in steps)
        result = BuildResult(
            target=target,
            generation_id=generation_id,
            status=TargetStatus.FAILED if target_failed else TargetStatus.BUILT,
            steps=steps,
            commit_id=commit_id,
            total_duration_secs=total_duration,
            timestamp=_now_iso(),
            source_hash=source_hash,
            files_created=final_build_response.files_created if final_build_response else [],
            files_modified=final_build_response.files_modified if final_build_response else [],
            attempts=attempts,
        )

        build_result_id = self._state_manager.save_build_result(target, result, git_diff=git_diff)
        if final_build_response is not None:
            self._storage.save_agent_response(
                build_result_id=build_result_id,
                validation_result_id=None,
                response_type="build",
                response_json=final_build_response.model_dump(),
            )

        if target_failed:
            failing_step = next((step for step in steps if step.status == "failed"), None)
            summary = failing_step.summary if failing_step is not None else "unknown error"
            error = RuntimeError(f"Build failed for target '{target}': {summary}")
            return result, error

        return result, None
