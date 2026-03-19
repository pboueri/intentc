"""Builder — the core workflow engine of intentc.

Takes a loaded project, manages each incremental build along the DAG,
runs validations, and manages all project state through the state manager.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from pydantic import BaseModel

from intentc.build.agents import (
    Agent,
    AgentError,
    AgentProfile,
    BuildContext,
    create_from_profile,
)
from intentc.build.state import (
    BuildResult,
    BuildStep,
    StateManager,
    TargetStatus,
    VersionControl,
)
from intentc.build.validations import ValidationSuite, ValidationSuiteResult
from intentc.core.project import Project
from intentc.core.types import IntentFile


class BuildOptions(BaseModel):
    """Options for a single build invocation."""

    model_config = {"extra": "ignore"}

    target: str = ""
    force: bool = False
    dry_run: bool = False
    output_dir: str = ""
    profile_override: str = ""


class Builder:
    """Core workflow engine that builds targets along the project DAG."""

    def __init__(
        self,
        project: Project,
        state_manager: StateManager,
        version_control: VersionControl,
        agent_profile: AgentProfile,
    ) -> None:
        self.project = project
        self.state_manager = state_manager
        self.version_control = version_control
        self.agent_profile = agent_profile
        self._create_agent: Callable[[AgentProfile], Agent] = create_from_profile
        self._named_profiles: dict[str, AgentProfile] = {}

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build(self, opts: BuildOptions) -> tuple[list[BuildResult], Exception | None]:
        """Execute the build pipeline.

        Returns:
            A tuple of (results, error). Error is non-None if any target failed.
        """
        # 1. Determine build set
        build_set = self._determine_build_set(opts)
        if not build_set:
            return [], None

        # 2. Dry run check
        if opts.dry_run:
            return self._dry_run_results(build_set), None

        # 3. Generate generation ID
        generation_id = str(uuid.uuid4())

        # 4. Resolve output directory
        output_dir = Path(opts.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # 5. Build each target in topological order
        results: list[BuildResult] = []
        for target in build_set:
            # Skip if already built and not forcing
            if not opts.force and self.state_manager.get_status(target) == TargetStatus.BUILT:
                continue

            result = self._build_target(target, generation_id, opts)
            results.append(result)

            # Save result
            self.state_manager.save_build_result(target, result)

            # On failure, stop immediately
            if result.status == TargetStatus.FAILED:
                return results, RuntimeError(
                    f"Build failed for target '{target}': "
                    + (result.steps[-1].summary if result.steps else "unknown error")
                )

        return results, None

    def _determine_build_set(self, opts: BuildOptions) -> list[str]:
        """Determine which targets to build, in topological order."""
        topo = self.project.topological_order()

        if opts.target:
            # Specific target: collect it and its unbuilt ancestors
            ancestors = self.project.ancestors(opts.target)
            candidates = ancestors | {opts.target}
            if opts.force:
                return [t for t in topo if t in candidates]
            return [
                t for t in topo
                if t in candidates
                and self.state_manager.get_status(t) in (
                    TargetStatus.PENDING, TargetStatus.OUTDATED, TargetStatus.FAILED,
                )
            ]

        # No target: all pending/outdated (or all if force)
        if opts.force:
            return list(topo)
        return [
            t for t in topo
            if self.state_manager.get_status(t) in (
                TargetStatus.PENDING, TargetStatus.OUTDATED,
            )
        ]

    def _dry_run_results(self, build_set: list[str]) -> list[BuildResult]:
        """Return results with current statuses for dry run."""
        now = datetime.now()
        return [
            BuildResult(
                target=t,
                generation_id="dry-run",
                status=self.state_manager.get_status(t),
                timestamp=now,
            )
            for t in build_set
        ]

    def _apply_sandbox_paths(
        self, profile: AgentProfile, target: str, opts: BuildOptions
    ) -> AgentProfile:
        """Compute sandbox paths from the project DAG and attach to the profile."""
        output_dir_abs = str(Path(opts.output_dir).resolve())

        sandbox_write = [
            output_dir_abs,
            str(self.state_manager.build_response_dir.resolve()),
            str(self.state_manager.val_response_dir.resolve()),
        ]
        sandbox_read = [output_dir_abs]

        intent_dir = self.project.intent_dir
        if intent_dir is not None:
            # Intent directories for this target and all ancestors
            ancestors = self.project.ancestors(target)
            for feat in ancestors | {target}:
                feat_dir = intent_dir / feat
                if feat_dir.is_dir():
                    sandbox_read.append(str(feat_dir.resolve()))

            # project.ic and implementation.ic
            project_ic = intent_dir / "project.ic"
            impl_ic = intent_dir / "implementation.ic"
            if project_ic.exists():
                sandbox_read.append(str(project_ic.resolve()))
            if impl_ic.exists():
                sandbox_read.append(str(impl_ic.resolve()))

        return profile.model_copy(update={
            "sandbox_write_paths": sandbox_write,
            "sandbox_read_paths": sandbox_read,
        })

    def _build_target(
        self, target: str, generation_id: str, opts: BuildOptions
    ) -> BuildResult:
        """Build a single target through the full step pipeline."""
        steps: list[BuildStep] = []
        now = datetime.now()
        node = self.project.features[target]

        # Resolve agent profile and apply sandbox paths
        profile = self._resolve_profile(opts)
        profile = self._apply_sandbox_paths(profile, target, opts)
        agent = self._create_agent(profile)

        # Step 1: resolve_deps
        step_start = datetime.now()
        dep_names = list(node.depends_on)
        steps.append(BuildStep(
            phase="resolve_deps",
            status="success",
            duration=datetime.now() - step_start,
            summary=f"Resolved {len(dep_names)} dependencies",
        ))

        # Step 2: build (with retries)
        step_start = datetime.now()
        self.state_manager.build_response_dir.mkdir(parents=True, exist_ok=True)
        response_file = self.state_manager.build_response_dir / f"{target.replace('/', '_')}-{uuid.uuid4().hex[:8]}.json"
        ctx = BuildContext(
            intent=node.intents[0] if node.intents else IntentFile(name=target),
            validations=node.validations,
            output_dir=opts.output_dir,
            generation_id=generation_id,
            dependency_names=dep_names,
            project_intent=self.project.project_intent,
            implementation=self.project.implementation,
            response_file_path=str(response_file.resolve()),
        )

        build_error = None
        for attempt in range(profile.retries):
            try:
                agent.build(ctx)
                build_error = None
                break
            except AgentError as exc:
                build_error = exc
                if attempt < profile.retries - 1:
                    continue

        if build_error is not None:
            steps.append(BuildStep(
                phase="build",
                status="failure",
                duration=datetime.now() - step_start,
                summary=f"Agent error after {profile.retries} attempts: {build_error}",
            ))
            return BuildResult(
                target=target,
                generation_id=generation_id,
                status=TargetStatus.FAILED,
                steps=steps,
                total_duration=sum((s.duration for s in steps), timedelta()),
                timestamp=now,
            )

        steps.append(BuildStep(
            phase="build",
            status="success",
            duration=datetime.now() - step_start,
            summary="Build completed",
        ))

        # Step 3: validate (if validations exist)
        has_validations = any(
            vf.validations for vf in node.validations
        )
        if has_validations:
            step_start = datetime.now()
            suite = ValidationSuite(
                project=self.project,
                agent_profile=profile,
                output_dir=opts.output_dir,
                val_response_dir=self.state_manager.val_response_dir,
            )
            suite_result = suite.validate_feature(target)

            if not suite_result.passed:
                steps.append(BuildStep(
                    phase="validate",
                    status="failure",
                    duration=datetime.now() - step_start,
                    summary=f"Validation failed: {suite_result.summary}",
                ))
                return BuildResult(
                    target=target,
                    generation_id=generation_id,
                    status=TargetStatus.FAILED,
                    steps=steps,
                    total_duration=sum((s.duration for s in steps), timedelta()),
                    timestamp=now,
                )

            steps.append(BuildStep(
                phase="validate",
                status="success",
                duration=datetime.now() - step_start,
                summary=suite_result.summary,
            ))

        # Step 4: checkpoint
        step_start = datetime.now()
        commit_msg = f"build {target} [gen:{generation_id}]"
        commit_id = self.version_control.checkpoint(commit_msg)
        steps.append(BuildStep(
            phase="checkpoint",
            status="success",
            duration=datetime.now() - step_start,
            summary=f"Checkpoint {commit_id}",
        ))

        return BuildResult(
            target=target,
            generation_id=generation_id,
            status=TargetStatus.BUILT,
            steps=steps,
            commit_id=commit_id,
            total_duration=sum((s.duration for s in steps), timedelta()),
            timestamp=now,
        )

    def _resolve_profile(self, opts: BuildOptions) -> AgentProfile:
        """Resolve the agent profile, respecting overrides."""
        if opts.profile_override and opts.profile_override in self._named_profiles:
            return self._named_profiles[opts.profile_override]
        return self.agent_profile

    # ------------------------------------------------------------------
    # Clean
    # ------------------------------------------------------------------

    def clean(self, target: str, output_dir: str) -> None:
        """Revert a target's generated code and reset its state."""
        result = self.state_manager.get_build_result(target)
        if result is None:
            return

        # Create a revert via version control
        if result.commit_id:
            self.version_control.restore(result.commit_id)

        # Reset target state to pending
        self.state_manager.reset(target)

        # Mark descendants as outdated
        self.state_manager.mark_dependents_outdated(target, self.project)

    def clean_all(self, output_dir: str) -> None:
        """Reset all state for the output directory."""
        self.state_manager.reset_all()

    # ------------------------------------------------------------------
    # Validate
    # ------------------------------------------------------------------

    def validate(
        self, target: str | None, output_dir: str
    ) -> ValidationSuiteResult | list[ValidationSuiteResult]:
        """Run validations independently of the build pipeline.

        If target is specified, validates that feature.
        If target is None, validates the entire project.
        Does not modify any state.
        """
        profile = self.agent_profile
        suite = ValidationSuite(
            project=self.project,
            agent_profile=profile,
            output_dir=output_dir,
            val_response_dir=self.state_manager.val_response_dir,
        )
        if target is not None:
            return suite.validate_feature(target)
        return suite.validate_project()

    # ------------------------------------------------------------------
    # DetectOutdated
    # ------------------------------------------------------------------

    def detect_outdated(self) -> list[str]:
        """Walk all targets and find those whose source files are newer than their build.

        Returns the list of outdated targets without modifying state.
        """
        outdated: list[str] = []
        intent_dir = self.project.intent_dir
        if intent_dir is None:
            return outdated

        for target, status in self.state_manager.list_targets():
            if status != TargetStatus.BUILT:
                continue

            result = self.state_manager.get_build_result(target)
            if result is None:
                continue

            build_ts = result.timestamp

            # Check .ic and .icv files for this target
            target_dir = intent_dir / target
            if not target_dir.is_dir():
                continue

            is_outdated = False
            for pattern in ("*.ic", "*.icv"):
                for path in target_dir.glob(pattern):
                    mtime = datetime.fromtimestamp(path.stat().st_mtime)
                    if mtime > build_ts:
                        is_outdated = True
                        break
                if is_outdated:
                    break

            if is_outdated:
                outdated.append(target)

        return outdated
