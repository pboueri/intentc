from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from intentc.build.agents.base import create_from_profile
from intentc.build.agents.types import (
    AgentError,
    AgentProfile,
    BuildContext,
    BuildResponse,
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
from intentc.core.project import FeatureNode, Project
from intentc.core.types import IntentFile


class BuildOptions:
    def __init__(
        self,
        target: str = "",
        force: bool = False,
        dry_run: bool = False,
        output_dir: str = "",
        profile_override: str = "",
        implementation: str = "",
    ) -> None:
        self.target = target
        self.force = force
        self.dry_run = dry_run
        self.output_dir = output_dir
        self.profile_override = profile_override
        self.implementation = implementation


class Builder:
    def __init__(
        self,
        project: Project,
        state_manager: StateManager,
        version_control: VersionControl,
        agent_profile: AgentProfile,
        create_agent: Callable[[AgentProfile], Any] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._project = project
        self._state_manager = state_manager
        self._version_control = version_control
        self._agent_profile = agent_profile
        self._create_agent = create_agent or create_from_profile
        self._storage: StorageBackend = state_manager._backend
        self._log = log or (lambda _: None)

    def build(
        self, opts: BuildOptions
    ) -> tuple[list[BuildResult], RuntimeError | None]:
        # 1. Determine build set
        build_set = self._determine_build_set(opts)
        if not build_set:
            return ([], None)

        # 2. Dry run check
        if opts.dry_run:
            results = []
            for target in build_set:
                status = self._state_manager.get_status(target)
                results.append(
                    BuildResult(
                        target=target,
                        generation_id="",
                        status=status,
                    )
                )
            return (results, None)

        # 3. Resolve implementation
        impl_name = opts.implementation or None
        implementation = self._project.resolve_implementation(impl_name)

        # 4. Generate generation ID
        generation_id = str(uuid.uuid4())
        opts_dict = {
            "target": opts.target,
            "force": opts.force,
            "dry_run": opts.dry_run,
            "output_dir": opts.output_dir,
            "profile_override": opts.profile_override,
            "implementation": opts.implementation,
        }
        profile_name = self._agent_profile.name
        self._storage.create_generation(
            generation_id, opts.output_dir, profile_name, opts_dict
        )

        # 5. Resolve output directory
        output_dir = opts.output_dir
        if output_dir:
            Path(output_dir).mkdir(parents=True, exist_ok=True)

        self._log(
            f"Build starting: {len(build_set)} target(s) [{', '.join(build_set)}]"
        )
        self._storage.log_generation_event(
            generation_id,
            f"Build plan: {len(build_set)} targets in order: {build_set}",
        )

        # 6. Build each target
        results: list[BuildResult] = []
        error: RuntimeError | None = None

        for idx, target in enumerate(build_set):
            self._log(f"[{idx + 1}/{len(build_set)}] Building target '{target}'...")

            # Skip check
            current_status = self._state_manager.get_status(target)
            if current_status == TargetStatus.BUILT and not opts.force:
                self._log(f"  Skipping '{target}' (already built)")
                self._storage.log_generation_event(
                    generation_id, f"Skipping '{target}': already built"
                )
                continue

            # Resolve agent profile
            profile = self._resolve_profile(opts.profile_override)
            # Apply sandbox paths
            profile = self._apply_sandbox_paths(profile, target, output_dir)

            node = self._project.features.get(target)
            intent = self._resolve_intent(node)
            validations = node.validations if node else []
            dep_names: list[str] = []

            steps: list[BuildStep] = []
            build_response: BuildResponse | None = None
            commit_id = ""
            git_diff = ""
            failed = False
            last_step_summary = ""

            retries = profile.retries
            for attempt in range(retries):
                steps = []
                build_response = None
                commit_id = ""
                git_diff = ""
                failed = False

                # Step 1: resolve_deps
                step_start = datetime.now(timezone.utc)
                if node:
                    dep_names = node.depends_on
                steps.append(
                    BuildStep(
                        phase="resolve_deps",
                        status="success",
                        duration=datetime.now(timezone.utc) - step_start,
                        summary=f"Dependencies: {dep_names}" if dep_names else "No dependencies",
                    )
                )
                self._log(f"  resolve_deps: {dep_names}")

                # Step 2: build
                step_start = datetime.now(timezone.utc)
                response_file_path = str(
                    self._state_manager.build_response_dir
                    / f"build_{target.replace('/', '_')}_{generation_id[:8]}.json"
                )
                build_ctx = BuildContext(
                    intent=intent,
                    validations=validations,
                    output_dir=output_dir,
                    generation_id=generation_id,
                    dependency_names=dep_names,
                    project_intent=self._project.project_intent,
                    implementation=implementation,
                    response_file_path=response_file_path,
                )
                try:
                    agent = self._create_agent(profile)
                    build_response = agent.build(build_ctx)
                    build_status = "success" if build_response.status == "success" else "failed"
                    steps.append(
                        BuildStep(
                            phase="build",
                            status=build_status,
                            duration=datetime.now(timezone.utc) - step_start,
                            summary=build_response.summary,
                        )
                    )
                    if build_status == "failed":
                        failed = True
                        last_step_summary = build_response.summary
                        self._log(f"  build: FAILED - {build_response.summary}")
                        if attempt < retries - 1:
                            self._log(f"  Retrying ({attempt + 2}/{retries})...")
                            continue
                        break
                    self._log(f"  build: {build_response.summary}")
                except AgentError as exc:
                    steps.append(
                        BuildStep(
                            phase="build",
                            status="failed",
                            duration=datetime.now(timezone.utc) - step_start,
                            summary=str(exc),
                        )
                    )
                    failed = True
                    last_step_summary = str(exc)
                    self._log(f"  build: FAILED - {exc}")
                    if attempt < retries - 1:
                        self._log(f"  Retrying ({attempt + 2}/{retries})...")
                        continue
                    break

                # Step 3: validate
                if validations:
                    step_start = datetime.now(timezone.utc)
                    self._log(f"  validate: running validations...")
                    suite = ValidationSuite(
                        project=self._project,
                        agent_profile=profile,
                        output_dir=output_dir,
                        storage_backend=self._storage,
                        val_response_dir=self._state_manager.val_response_dir,
                    )
                    suite_result = suite.validate_feature(target)
                    val_status = "success" if suite_result.passed else "failed"
                    steps.append(
                        BuildStep(
                            phase="validate",
                            status=val_status,
                            duration=datetime.now(timezone.utc) - step_start,
                            summary=suite_result.summary,
                        )
                    )
                    if not suite_result.passed:
                        failed = True
                        last_step_summary = suite_result.summary
                        self._log(f"  validate: FAILED - {suite_result.summary}")
                        if attempt < retries - 1:
                            self._log(f"  Retrying ({attempt + 2}/{retries})...")
                            continue
                        break
                    self._log(f"  validate: {suite_result.summary}")

                # Step 4: checkpoint
                step_start = datetime.now(timezone.utc)
                checkpoint_msg = f"build {target} [gen:{generation_id}]"
                try:
                    commit_id = self._version_control.checkpoint(checkpoint_msg)
                    git_diff = self._version_control.diff(f"{commit_id}~1", commit_id)
                except Exception:
                    git_diff = ""
                steps.append(
                    BuildStep(
                        phase="checkpoint",
                        status="success",
                        duration=datetime.now(timezone.utc) - step_start,
                        summary=f"Committed {commit_id[:8]}" if commit_id else "Checkpoint",
                    )
                )
                self._log(f"  checkpoint: {commit_id[:8] if commit_id else 'done'}")

                # All steps passed, break retry loop
                break

            # Collect result
            total_duration = sum(
                (s.duration for s in steps), timedelta(0)
            )
            result_status = TargetStatus.FAILED if failed else TargetStatus.BUILT
            result = BuildResult(
                target=target,
                generation_id=generation_id,
                status=result_status,
                steps=steps,
                commit_id=commit_id,
                total_duration=total_duration,
                timestamp=datetime.now(timezone.utc),
            )

            # Save
            files_created = build_response.files_created if build_response else []
            files_modified = build_response.files_modified if build_response else []
            self._state_manager.save_build_result(target, result)

            # Try to save extended info to storage
            try:
                build_result_id = self._storage.save_build_result(
                    target,
                    result,
                    git_diff=git_diff,
                    files_created=files_created,
                    files_modified=files_modified,
                )
            except Exception:
                pass

            # Read and store agent response file, then delete
            response_file = Path(response_file_path) if 'response_file_path' in dir() else None
            if response_file and response_file.exists():
                try:
                    response_json = json.loads(response_file.read_text())
                    self._storage.save_agent_response(
                        build_result_id=None,
                        validation_result_id=None,
                        response_type="build",
                        response_json=response_json,
                    )
                    response_file.unlink()
                except Exception:
                    pass

            results.append(result)

            # On failure, stop immediately
            if failed:
                self._storage.log_generation_event(
                    generation_id,
                    f"Build failed for target '{target}': {last_step_summary}",
                )
                error = RuntimeError(
                    f"Build failed for target '{target}': {last_step_summary}"
                )
                self._log(f"Build failed for target '{target}'")
                break

            self._log(f"  Target '{target}' complete")

        # Complete generation
        gen_status = (
            GenerationStatus.FAILED if error else GenerationStatus.COMPLETED
        )
        self._storage.complete_generation(generation_id, gen_status)

        return (results, error)

    def clean(self, target: str, output_dir: str) -> None:
        result = self._state_manager.get_build_result(target)
        if result is None:
            self._log(f"  No build result found for '{target}', nothing to clean")
            return

        if result.commit_id:
            self._version_control.restore(result.commit_id)

        self._state_manager.reset(target)
        self._state_manager.mark_dependents_outdated(target, self._project)
        self._log(f"Cleaned target '{target}'")

    def clean_all(self, output_dir: str) -> None:
        self._state_manager.reset_all()
        self._log("Cleaned all state")

    def validate(
        self, target: str | None, output_dir: str
    ) -> ValidationSuiteResult | list[ValidationSuiteResult]:
        profile = self._resolve_profile("")
        suite = ValidationSuite(
            project=self._project,
            agent_profile=profile,
            output_dir=output_dir,
            storage_backend=self._storage,
            val_response_dir=self._state_manager.val_response_dir,
        )
        if target:
            return suite.validate_feature(target)
        return suite.validate_project()

    def detect_outdated(self) -> list[str]:
        outdated: list[str] = []
        targets = self._state_manager.list_targets()
        for target, status in targets:
            if status != TargetStatus.BUILT:
                continue
            result = self._state_manager.get_build_result(target)
            if result is None:
                continue
            node = self._project.features.get(target)
            if node is None:
                continue
            build_ts = result.timestamp
            # Check .ic files
            for intent in node.intents:
                if intent.source_path and intent.source_path.exists():
                    mtime = datetime.fromtimestamp(
                        intent.source_path.stat().st_mtime, tz=timezone.utc
                    )
                    if mtime > build_ts:
                        outdated.append(target)
                        break
            else:
                # Check .icv files
                for vf in node.validations:
                    if vf.source_path and vf.source_path.exists():
                        mtime = datetime.fromtimestamp(
                            vf.source_path.stat().st_mtime, tz=timezone.utc
                        )
                        if mtime > build_ts:
                            outdated.append(target)
                            break
        return outdated

    def _determine_build_set(self, opts: BuildOptions) -> list[str]:
        topo = self._project.topological_order()
        rebuildable = {TargetStatus.PENDING, TargetStatus.OUTDATED, TargetStatus.FAILED}

        if opts.target:
            ancestors = self._project.ancestors(opts.target)
            candidates = ancestors | {opts.target}
            if opts.force:
                filtered = candidates
            else:
                filtered = {
                    t
                    for t in candidates
                    if self._state_manager.get_status(t) in rebuildable
                }
            return [t for t in topo if t in filtered]

        if opts.force:
            return topo

        return [
            t for t in topo if self._state_manager.get_status(t) in rebuildable
        ]

    def _resolve_profile(self, profile_override: str) -> AgentProfile:
        if profile_override:
            return AgentProfile(name=profile_override, provider=self._agent_profile.provider)
        return self._agent_profile

    def _apply_sandbox_paths(
        self, profile: AgentProfile, target: str, output_dir: str
    ) -> AgentProfile:
        node = self._project.features.get(target)

        # Write paths: output dir, build response dir, validation response dir
        write_paths = [output_dir] if output_dir else []
        write_paths.append(str(self._state_manager.build_response_dir))
        write_paths.append(str(self._state_manager.val_response_dir))

        # Read paths: output dir, target+ancestor intents, project intent, implementations dir
        read_paths = [output_dir] if output_dir else []
        # Target and ancestor intent files
        targets_to_read = {target} | self._project.ancestors(target)
        for t in targets_to_read:
            t_node = self._project.features.get(t)
            if t_node:
                for intent in t_node.intents:
                    if intent.source_path:
                        read_paths.append(str(intent.source_path))
        # Project intent
        if self._project.project_intent.source_path:
            read_paths.append(str(self._project.project_intent.source_path))
        # Implementations directory
        if self._project.intent_dir:
            impl_dir = self._project.intent_dir / "implementations"
            if impl_dir.exists():
                read_paths.append(str(impl_dir))
            # Legacy implementation.ic
            legacy = self._project.intent_dir / "implementation.ic"
            if legacy.exists():
                read_paths.append(str(legacy))

        return profile.model_copy(
            update={
                "sandbox_write_paths": write_paths,
                "sandbox_read_paths": read_paths,
            }
        )

    def _resolve_intent(self, node: FeatureNode | None) -> IntentFile:
        if node and node.intents:
            return node.intents[0]
        return IntentFile(name="", body="")
