"""Builder: walks the feature DAG, drives the agent, validates, checkpoints, records state."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel

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
    response_file_name,
)
from intentc.build.storage import GenerationStatus, StorageBackend
from intentc.build.validations import ValidationSuite, ValidationSuiteResult
from intentc.core.models import Implementation, IntentFile
from intentc.core.parser import content_hash
from intentc.core.project import Project

LogFn = Callable[[str], None]
REBUILDABLE = {TargetStatus.PENDING, TargetStatus.OUTDATED, TargetStatus.FAILED}


def _noop(_message: str) -> None:
    return None


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class BuildOptions(BaseModel):
    target: str = ""
    force: bool = False
    dry_run: bool = False
    output_dir: str = ""
    profile_override: str = ""
    implementation: str = ""


class _Timer:
    def __init__(self) -> None:
        self._start = datetime.now()

    def elapsed(self) -> float:
        return round((datetime.now() - self._start).total_seconds(), 3)


class Builder:
    """Core workflow engine. Dependencies are injected; the project is already loaded."""

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
        self._log = log or _noop
        self._storage: StorageBackend = state_manager.backend
        if create_agent is not None:
            self._create_agent = create_agent
        else:
            self._create_agent = lambda profile: create_from_profile(profile, log=self._log)

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build(self, opts: BuildOptions) -> tuple[list[BuildResult], RuntimeError | None]:
        self.refresh_outdated()

        build_set = self._determine_build_set(opts)
        if not build_set:
            self._log("Nothing to build — all targets are up to date.")
            return [], None

        self._log(f"Build plan: {len(build_set)} target(s): {', '.join(build_set)}")

        if opts.dry_run:
            return [
                BuildResult(target=t, status=self._state_manager.get_status(t), timestamp=_now())
                for t in build_set
            ], None

        implementation = self._project.resolve_implementation(opts.implementation or None)
        profile = self._resolve_profile(opts.profile_override)

        generation_id = str(uuid.uuid4())
        self._storage.create_generation(generation_id, opts.output_dir, profile.name, opts.model_dump())
        self._storage.log_generation_event(
            generation_id, f"Build plan ({len(build_set)} targets): {', '.join(build_set)}"
        )
        self._log(f"Generation {generation_id[:8]} using agent profile '{profile.name}' ({profile.provider})")

        if opts.output_dir:
            os.makedirs(self._output_path(opts.output_dir), exist_ok=True)

        results: list[BuildResult] = []
        error: RuntimeError | None = None
        for index, target in enumerate(build_set, start=1):
            status = self._state_manager.get_status(target)
            if status == TargetStatus.BUILT and not opts.force:
                self._log(f"[{index}/{len(build_set)}] {target} — skipped (already built)")
                self._storage.log_generation_event(generation_id, f"Skipped '{target}': already built")
                continue

            self._log(f"[{index}/{len(build_set)}] {target}")
            result, git_diff, response_raw = self._build_target(
                target, generation_id, opts.output_dir, profile, implementation
            )
            build_result_id = self._state_manager.save_build_result(target, result, git_diff=git_diff)
            if response_raw is not None:
                self._storage.save_agent_response(build_result_id, None, "build", response_raw)
            results.append(result)

            if result.status == TargetStatus.FAILED:
                last = result.steps[-1] if result.steps else None
                detail = last.summary if last else "unknown failure"
                message = f"Build failed for target '{target}': {detail}"
                self._storage.log_generation_event(generation_id, message)
                self._log(f"  ✗ '{target}' failed after {result.attempts} attempt(s): {detail.splitlines()[0]}")
                error = RuntimeError(message)
                break

            self._log(
                f"  ✓ '{target}' built in {result.total_duration_secs:.1f}s ({result.attempts} attempt(s))"
            )
            self._storage.log_generation_event(generation_id, f"Built '{target}' ({result.commit_id[:8]})")

        self._storage.complete_generation(
            generation_id, GenerationStatus.FAILED if error else GenerationStatus.COMPLETED
        )
        return results, error

    def next_targets(self) -> list[str]:
        built = {t for t, s in self._state_manager.list_targets() if s == TargetStatus.BUILT}
        return self._project.buildable_after(built)

    # ------------------------------------------------------------------
    # Clean / validate / invalidation
    # ------------------------------------------------------------------

    def clean(self, target: str, output_dir: str) -> None:
        self._project._require_feature(target)
        result = self._state_manager.get_build_result(target)
        if result is None:
            self._log(f"'{target}' has no build to clean.")
            return
        if result.commit_id:
            self._log(f"Restoring files from before {result.commit_id[:8]}")
            self._version_control.restore(f"{result.commit_id}~1")
        self._state_manager.reset(target)
        self._log(f"Reset '{target}' to pending")
        for dependent in self._state_manager.mark_dependents_outdated(target, self._project):
            self._log(f"Marked '{dependent}' outdated (depends on '{target}')")

    def clean_all(self, output_dir: str) -> None:
        self._state_manager.reset_all()
        self._log(f"Reset all build state for '{output_dir}' (files left untouched)")

    def validate(self, target: str | None, output_dir: str) -> ValidationSuiteResult | list[ValidationSuiteResult]:
        suite = self._suite(self._resolve_profile(""), output_dir, None, None, None)
        if target:
            return suite.validate_feature(target)
        return suite.validate_project()

    def detect_outdated(self) -> list[str]:
        stale: list[str] = []
        for target, status in self._state_manager.list_targets():
            if status != TargetStatus.BUILT or target not in self._project.features:
                continue
            result = self._state_manager.get_build_result(target)
            if result is None:
                continue
            if self._is_stale(target, result):
                stale.append(target)
        order = {fp: i for i, fp in enumerate(self._project.topological_order())}
        return sorted(stale, key=lambda t: order.get(t, len(order)))

    def refresh_outdated(self) -> list[str]:
        changed: list[str] = []
        for target in self.detect_outdated():
            self._state_manager.set_status(target, TargetStatus.OUTDATED)
            self._log(f"  Marked '{target}' outdated: intent changed since last build")
            changed.append(target)
            for dependent in self._state_manager.mark_dependents_outdated(target, self._project):
                self._log(f"  Marked '{dependent}' outdated: dependency '{target}' changed")
                changed.append(dependent)
        return changed

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _is_stale(self, target: str, result: BuildResult) -> bool:
        files = self._project.source_files(target)
        if result.source_hash:
            return content_hash(files) != result.source_hash
        if not result.timestamp:
            return False
        try:
            built_at = datetime.fromisoformat(result.timestamp)
        except ValueError:
            return False
        built_epoch = int(built_at.timestamp())
        return any(f.exists() and int(f.stat().st_mtime) > built_epoch for f in files)

    def _determine_build_set(self, opts: BuildOptions) -> list[str]:
        order = self._project.topological_order()
        if opts.target:
            wanted = self._project.ancestors(opts.target) | {opts.target}
            candidates = [t for t in order if t in wanted]
        else:
            candidates = order
        if opts.force:
            return candidates
        return [t for t in candidates if self._state_manager.get_status(t) in REBUILDABLE]

    def _resolve_profile(self, override: str) -> AgentProfile:
        if override and override != self._agent_profile.name:
            return self._agent_profile.model_copy(update={"name": override})
        return self._agent_profile

    def _project_root(self) -> Path:
        if self._project.intent_dir is not None:
            return Path(self._project.intent_dir).resolve().parent
        return Path.cwd()

    def _output_path(self, output_dir: str) -> Path:
        path = Path(output_dir)
        return path.resolve() if path.is_absolute() else (self._project_root() / path).resolve()

    def _apply_sandbox_paths(self, profile: AgentProfile, target: str, output_dir: str) -> AgentProfile:
        output = self._output_path(output_dir)
        write_paths = [
            str(output),
            str(self._state_manager.build_response_dir.resolve()),
            str(self._state_manager.val_response_dir.resolve()),
        ]
        read_paths = [str(output)]
        for fp in [target, *sorted(self._project.ancestors(target))]:
            node = self._project.features.get(fp)
            if node is None:
                continue
            for intent in node.intents:
                if intent.source_path is not None:
                    read_paths.append(str(intent.source_path.resolve()))
            for vf in node.validations:
                if vf.source_path is not None:
                    read_paths.append(str(vf.source_path.resolve()))
        if self._project.project_intent.source_path is not None:
            read_paths.append(str(self._project.project_intent.source_path.resolve()))
        if self._project.intent_dir is not None:
            impl_dir = self._project.intent_dir / "implementations"
            if impl_dir.is_dir():
                read_paths.append(str(impl_dir.resolve()))
            legacy = self._project.intent_dir / "implementation.ic"
            if legacy.is_file():
                read_paths.append(str(legacy.resolve()))
        return profile.model_copy(update={"sandbox_write_paths": write_paths, "sandbox_read_paths": read_paths})

    def _record_source_versions(self, target: str) -> str:
        node = self._project.features[target]
        for intent in node.intents:
            if intent.source_path is not None:
                self._storage.record_intent_version(
                    intent.name, str(intent.source_path), content_hash([intent.source_path])
                )
        for vf in node.validations:
            if vf.source_path is not None:
                self._storage.record_validation_version(target, str(vf.source_path), content_hash([vf.source_path]))
        return content_hash(self._project.source_files(target))

    def _suite(
        self,
        profile: AgentProfile,
        output_dir: str,
        implementation: Implementation | None,
        build_result_id: int | None,
        generation_id: str | None,
    ) -> ValidationSuite:
        return ValidationSuite(
            project=self._project,
            agent_profile=profile,
            output_dir=output_dir,
            val_response_dir=self._state_manager.val_response_dir,
            storage_backend=self._storage,
            log=self._log,
            implementation=implementation,
            build_result_id=build_result_id,
            generation_id=generation_id,
            create_agent=self._create_agent,
        )

    def _build_target(
        self,
        target: str,
        generation_id: str,
        output_dir: str,
        profile: AgentProfile,
        implementation: Implementation | None,
    ) -> tuple[BuildResult, str, dict[str, Any] | None]:
        node = self._project.features[target]
        intent = node.intents[0] if node.intents else IntentFile(name=target)
        source_hash = self._record_source_versions(target)
        sandboxed = self._apply_sandbox_paths(profile, target, output_dir)
        attempts_allowed = max(1, profile.retries)

        previous_errors: list[str] = []
        steps: list[BuildStep] = []
        response: BuildResponse | None = None
        response_raw: dict[str, Any] | None = None
        commit_id = ""
        git_diff = ""
        attempts = 0
        succeeded = False

        for attempt in range(1, attempts_allowed + 1):
            attempts = attempt
            steps = []
            if attempt > 1:
                self._log(f"  Retry {attempt}/{attempts_allowed} for '{target}'")

            dep_step, dep_names = self._step_resolve_deps(target)
            steps.append(dep_step)

            response_file = str(self._state_manager.build_response_dir / response_file_name(target))
            ctx = BuildContext(
                intent=intent,
                validations=list(node.validations),
                output_dir=output_dir,
                generation_id=generation_id,
                dependency_names=dep_names,
                project_intent=self._project.project_intent,
                implementation=implementation,
                response_file_path=response_file,
                previous_errors=list(previous_errors),
                feature_path=target,
            )
            build_step, response = self._step_build(sandboxed, ctx)
            response_raw = self._consume_response_file(response_file)
            steps.append(build_step)
            if build_step.status != "success":
                previous_errors.append(build_step.summary)
                continue

            if any(vf.validations for vf in node.validations):
                val_step = self._step_validate(target, sandboxed, output_dir, implementation, generation_id)
                steps.append(val_step)
                if val_step.status != "success":
                    previous_errors.append(val_step.summary)
                    continue
            else:
                self._log("  validate: no validations defined, skipping")

            ckpt_step, commit_id, git_diff = self._step_checkpoint(target, generation_id)
            steps.append(ckpt_step)
            succeeded = ckpt_step.status == "success"
            break

        result = BuildResult(
            target=target,
            generation_id=generation_id,
            status=TargetStatus.BUILT if succeeded else TargetStatus.FAILED,
            steps=steps,
            commit_id=commit_id,
            total_duration_secs=round(sum(s.duration_secs for s in steps), 3),
            timestamp=_now(),
            source_hash=source_hash,
            files_created=list(response.files_created) if response else [],
            files_modified=list(response.files_modified) if response else [],
            attempts=attempts,
        )
        return result, git_diff, response_raw

    def _consume_response_file(self, path: str) -> dict[str, Any] | None:
        p = Path(path)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        finally:
            try:
                p.unlink()
            except OSError:
                pass
        return data if isinstance(data, dict) else None

    def _step_resolve_deps(self, target: str) -> tuple[BuildStep, list[str]]:
        timer = _Timer()
        deps = list(self._project.features[target].depends_on)
        self._log(f"  resolve_deps: {', '.join(deps) if deps else '(none)'}")
        return BuildStep(
            phase="resolve_deps",
            status="success",
            duration_secs=timer.elapsed(),
            summary=f"Dependencies: {', '.join(deps) if deps else '(none)'}",
        ), deps

    def _step_build(self, profile: AgentProfile, ctx: BuildContext) -> tuple[BuildStep, BuildResponse | None]:
        timer = _Timer()
        self._log("  build: invoking agent")
        try:
            agent = self._create_agent(profile)
            response = agent.build(ctx)
        except AgentError as exc:
            self._log(f"  build: agent error: {exc}")
            return BuildStep(phase="build", status="failure", duration_secs=timer.elapsed(), summary=f"Agent error: {exc}"), None
        if response.status != "success":
            self._log(f"  build: agent reported failure: {response.summary}")
            return BuildStep(
                phase="build", status="failure", duration_secs=timer.elapsed(), summary=response.summary or "agent reported failure"
            ), response
        self._log(f"  build: {response.summary}")
        return BuildStep(phase="build", status="success", duration_secs=timer.elapsed(), summary=response.summary), response

    def _step_validate(
        self,
        target: str,
        profile: AgentProfile,
        output_dir: str,
        implementation: Implementation | None,
        generation_id: str,
    ) -> BuildStep:
        timer = _Timer()
        self._log("  validate: running validations")
        suite = self._suite(profile, output_dir, implementation, None, generation_id)
        result = suite.validate_feature(target)
        if result.passed:
            self._log(f"  validate: passed ({result.summary})")
            return BuildStep(phase="validate", status="success", duration_secs=timer.elapsed(), summary=result.summary)
        failures = [f"{r.name}: {r.reason.splitlines()[0] if r.reason else r.status}" for r in result.results if r.status != "pass" and r.severity == "error"]
        summary = f"Validation failed ({result.summary})" + ("\n" + "\n".join(failures) if failures else "")
        self._log(f"  validate: failed ({result.summary})")
        return BuildStep(phase="validate", status="failure", duration_secs=timer.elapsed(), summary=summary)

    def _step_checkpoint(self, target: str, generation_id: str) -> tuple[BuildStep, str, str]:
        timer = _Timer()
        message = f"build {target} [gen:{generation_id}]"
        try:
            commit_id = self._version_control.checkpoint(message)
        except Exception as exc:  # noqa: BLE001
            self._log(f"  checkpoint: failed: {exc}")
            return BuildStep(phase="checkpoint", status="failure", duration_secs=timer.elapsed(), summary=f"Checkpoint failed: {exc}"), "", ""
        try:
            git_diff = self._version_control.diff(f"{commit_id}~1", commit_id)
        except Exception:  # noqa: BLE001
            git_diff = ""
        self._log(f"  checkpoint: {commit_id[:8]}")
        return BuildStep(phase="checkpoint", status="success", duration_secs=timer.elapsed(), summary=f"Committed {commit_id[:8]}"), commit_id, git_diff
