"""The refine workflow: an interactive refinement session followed by a
non-interactive bake that folds the session's journal back into intent.

Three entry points, mirroring the CLI's three modes (`intentc refine`,
`--bake`, `--abandon`):

- `run_refine` starts or resumes a session, drops the user into an
  interactive agent session, then (unless `--no-bake`) bakes it.
- `bake_refinement` runs the non-interactive bake loop on an already-open
  session.
- `abandon_refinement` discards an open session and restores its base commit.

The session's journal is the only memory the workflow relies on across the
two phases -- no agent-specific transcript or conversation id is assumed.
"""

from __future__ import annotations

import json
import re
import tempfile
import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from intentc.build.agents import (
    Agent,
    AgentError,
    AgentProfile,
    RefineBakeResponse,
    RefineContext,
    create_from_profile,
)
from intentc.build.builder.builder import Builder, BuildOptions
from intentc.build.state import StateManager, TargetStatus, VersionControl
from intentc.build.state.state import response_file_name
from intentc.build.storage import RefinementSession
from intentc.core import Implementation, IntentFile, ParseErrors, Project, check_project, load_project
from intentc.differencing import run_differencing

LogFn = Callable[[str], None]


def _noop_log(_message: str) -> None:
    return None


def _now_iso() -> str:
    return datetime.now().isoformat()


class RefineOutcome(str, Enum):
    """The outcome of a `run_refine`/`bake_refinement` call."""

    RECORDED = "recorded"
    BAKED = "baked"
    FAILED = "failed"
    ABANDONED = "abandoned"


class RefineUsageError(Exception):
    """Raised for usage errors the CLI maps to exit code 2."""


_JOURNAL_ENTRY_RE = re.compile(r"^## ", re.MULTILINE)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def _session_dir(base_dir: Path, output_dir: str, session_id: str) -> Path:
    return Path(base_dir) / ".intentc" / "state" / output_dir / "refinements" / session_id


def _journal_path(base_dir: Path, output_dir: str, session_id: str) -> Path:
    return _session_dir(base_dir, output_dir, session_id) / "journal.md"


def _count_journal_entries(text: str) -> int:
    return len(_JOURNAL_ENTRY_RE.findall(text))


def _feature_paths(project: Project, target: str) -> tuple[str, str, str]:
    """(intent_path, validation_path, feature_dir) for `target`, using the
    existing file when there is one, or the conventional path to create."""
    intent_dir = Path(project.intent_dir) if project.intent_dir is not None else Path("intent")
    feature_dir = intent_dir / target
    node = project.features.get(target)
    leaf = target.rsplit("/", 1)[-1]

    intent_path = feature_dir / f"{leaf}.ic"
    if node is not None and node.intents and node.intents[0].source_path is not None:
        intent_path = node.intents[0].source_path

    validation_path = feature_dir / "validation.icv"
    if node is not None and node.validations and node.validations[0].source_path is not None:
        validation_path = node.validations[0].source_path

    return str(intent_path), str(validation_path), str(feature_dir)


# ---------------------------------------------------------------------------
# Diff attribution
# ---------------------------------------------------------------------------

_DIFF_FILE_RE = re.compile(r"^diff --git a/(?:.+?) b/(.+)$", re.MULTILINE)


def _changed_files_from_diff(diff_text: str) -> list[str]:
    return [match.group(1) for match in _DIFF_FILE_RE.finditer(diff_text)]


def _relative_to_output_dir(path: str, output_dir: str) -> str:
    prefix = output_dir.rstrip("/") + "/"
    if path.startswith(prefix):
        return path[len(prefix) :]
    return path


def _owner_map(project: Project, state_manager: StateManager) -> dict[str, str]:
    owners: dict[str, str] = {}
    for feature in project.features:
        result = state_manager.get_build_result(feature)
        if result is None:
            continue
        for file_path in (*result.files_created, *result.files_modified):
            owners[file_path] = feature
    return owners


def _files_by_owner(
    diff_text: str, output_dir: str, target: str, project: Project, state_manager: StateManager
) -> dict[str, list[str]]:
    owners = _owner_map(project, state_manager)
    by_owner: dict[str, list[str]] = {}
    for raw_path in _changed_files_from_diff(diff_text):
        relative = _relative_to_output_dir(raw_path, output_dir)
        owner = owners.get(relative, target)
        by_owner.setdefault(owner, []).append(relative)
    return by_owner


# ---------------------------------------------------------------------------
# Sandbox profiles (reuse the builder's own path computation)
# ---------------------------------------------------------------------------


def _refine_sandbox_profile(
    builder: Builder,
    profile: AgentProfile,
    target: str,
    output_dir: str,
    implementation: Optional[Implementation],
    session_dir: Path,
) -> AgentProfile:
    base = builder._apply_sandbox_paths(profile, target, Path(output_dir), implementation)
    write_paths = [str(Path(output_dir).resolve()), str(session_dir.resolve())]
    read_paths = [*base.sandbox_read_paths, str(session_dir.resolve())]
    return base.model_copy(update={"sandbox_write_paths": write_paths, "sandbox_read_paths": read_paths})


def _bake_sandbox_profile(
    builder: Builder,
    profile: AgentProfile,
    target: str,
    output_dir: str,
    implementation: Optional[Implementation],
    feature_dir: str,
    response_dir: Path,
    snapshot_root: str,
) -> AgentProfile:
    base = builder._apply_sandbox_paths(profile, target, Path(output_dir), implementation)
    read_paths = [*base.sandbox_read_paths, str(Path(snapshot_root).resolve())]
    write_paths = [str(Path(feature_dir).resolve()), str(response_dir.resolve())]
    return base.model_copy(update={"sandbox_read_paths": read_paths, "sandbox_write_paths": write_paths})


# ---------------------------------------------------------------------------
# Confirmation
# ---------------------------------------------------------------------------


def _default_confirm(prompt_text: str) -> bool:
    try:
        answer = input(f"{prompt_text} [Y/n] ")
    except EOFError:
        return True
    return answer.strip().lower() in ("", "y", "yes")


# ---------------------------------------------------------------------------
# run_refine
# ---------------------------------------------------------------------------


def run_refine(
    project: Project,
    profile: AgentProfile,
    implementation: Optional[Implementation],
    state_manager: StateManager,
    version_control: VersionControl,
    builder: Builder,
    target: str,
    output_dir: str,
    seed_prompt: str = "",
    no_bake: bool = False,
    no_compare: bool = False,
    confirm: Optional[Callable[[str], bool]] = None,
    create_agent: Optional[Callable[[AgentProfile], Agent]] = None,
    log: Optional[LogFn] = None,
) -> tuple[RefineOutcome, RefinementSession, Optional[RefineBakeResponse]]:
    """Start or resume an interactive refinement session for `target`, then
    (unless `no_bake`) bake it. Raises `RefineUsageError` for usage errors."""
    log_fn = log or _noop_log
    confirm_fn = confirm or _default_confirm
    backend = state_manager.backend

    session = backend.get_open_refinement_session(target)
    if session is None:
        other = backend.get_open_refinement_session(None)
        if other is not None:
            raise RefineUsageError(
                f"'{other.target}' has an open refinement session {other.session_id}. "
                "Bake or abandon it first."
            )

        status = state_manager.get_status(target)
        if status in (TargetStatus.PENDING, TargetStatus.FAILED):
            raise RefineUsageError(
                f"'{target}' has not been built in {output_dir}. Run: intentc build {target}"
            )
        if version_control.has_changes():
            raise RefineUsageError(
                f"{output_dir} has uncommitted changes. Commit or discard them before refining."
            )

        head_log = version_control.log()
        base_commit_id = head_log[0] if head_log else ""

        session_id = str(uuid.uuid4())
        session = RefinementSession(
            session_id=session_id,
            target=target,
            output_dir=output_dir,
            status="recording",
            base_commit=base_commit_id,
            seed_prompt=seed_prompt,
            journal="",
            started_at=_now_iso(),
        )
        backend.create_refinement_session(session)

        journal_path = _journal_path(state_manager.base_dir, output_dir, session_id)
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        journal_path.write_text(f"# Refinement journal — {target} — {session_id}\n", encoding="utf-8")
    else:
        journal_path = _journal_path(state_manager.base_dir, output_dir, session.session_id)
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        if not journal_path.exists():
            journal_path.write_text(session.journal, encoding="utf-8")

    node = project.features[target]
    feature_intent = node.intents[0] if node.intents else IntentFile(name=target, body="")
    intent_path, validation_path, feature_dir = _feature_paths(project, target)
    session_dir = _session_dir(state_manager.base_dir, output_dir, session.session_id)

    resolved_create_agent = create_agent or (lambda p: create_from_profile(p, log=log_fn))
    # The DB's `journal` field (not the file on disk, which already carries the
    # heading) is what the template's "journal so far" means: "" for a brand
    # new session, the accumulated entries on resume.
    journal_before = session.journal
    sandboxed_profile = _refine_sandbox_profile(
        builder, profile, target, output_dir, implementation, session_dir
    )
    agent = resolved_create_agent(sandboxed_profile)

    ctx = RefineContext(
        session_id=session.session_id,
        feature_path=target,
        intent=feature_intent,
        validations=node.validations,
        artifacts=project.artifacts_for(target, implementation),
        project_intent=project.project_intent,
        implementation=implementation,
        output_dir=output_dir,
        journal_path=str(journal_path),
        journal=journal_before,
        seed_prompt=seed_prompt,
        base_commit=session.base_commit,
        intent_path=intent_path,
        validation_path=validation_path,
        feature_dir=feature_dir,
    )

    agent.refine(ctx)

    journal_text = journal_path.read_text(encoding="utf-8") if journal_path.exists() else journal_before
    backend.update_refinement_session(session.session_id, journal=journal_text)
    session = backend.get_refinement_session(session.session_id)
    assert session is not None
    entry_count = _count_journal_entries(journal_text)
    log_fn(f"Refinement session {session.session_id[:8]} recorded: {entry_count} journal entries")

    if no_bake:
        return RefineOutcome.RECORDED, session, None

    if not confirm_fn("Bake this refinement into the intent now?"):
        return RefineOutcome.RECORDED, session, None

    return bake_refinement(
        project=project,
        profile=profile,
        implementation=implementation,
        state_manager=state_manager,
        version_control=version_control,
        builder=builder,
        session=session,
        output_dir=output_dir,
        no_compare=no_compare,
        create_agent=create_agent,
        log=log_fn,
    )


# ---------------------------------------------------------------------------
# bake_refinement
# ---------------------------------------------------------------------------


def bake_refinement(
    project: Project,
    profile: AgentProfile,
    implementation: Optional[Implementation],
    state_manager: StateManager,
    version_control: VersionControl,
    builder: Builder,
    session: RefinementSession,
    output_dir: str,
    no_compare: bool = False,
    create_agent: Optional[Callable[[AgentProfile], Agent]] = None,
    log: Optional[LogFn] = None,
) -> tuple[RefineOutcome, RefinementSession, Optional[RefineBakeResponse]]:
    """Snapshot the refined tree, then repeatedly rewrite intent, lint,
    rebuild from scratch, and (unless `no_compare`) compare against the
    refined snapshot, until it succeeds or the retry budget is exhausted."""
    log_fn = log or _noop_log
    backend = state_manager.backend
    target = session.target
    implementation_name = implementation.name if implementation is not None else ""
    resolved_create_agent = create_agent or (lambda p: create_from_profile(p, log=log_fn))

    intent_path, validation_path, feature_dir = _feature_paths(project, target)
    total_attempts = max(profile.retries, 1)
    previous_errors: list[str] = []
    bake_response: Optional[RefineBakeResponse] = None
    attempt = 0
    succeeded = False
    successful_generation_id = ""

    # Anything that escapes an attempt below (an unexpected exception, a git
    # `RuntimeError`, ...) must not leave the session stuck in `baking`: mark
    # it `failed` before propagating so `--bake` can re-run it later.
    try:
        if session.snapshot_id:
            # Re-baking a previously failed session: the refined tree was
            # already captured on the earlier attempt, and `builder.clean`
            # will overwrite the output directory anyway, so there is
            # nothing new to snapshot.
            snapshot_id = session.snapshot_id
        else:
            ref_name = f"refs/intentc/refinements/{session.session_id}"
            snapshot_id = version_control.snapshot(
                f"refine: {target} session {session.session_id}", ref_name
            )
        diff_text = version_control.diff(session.base_commit, snapshot_id)
        by_owner = _files_by_owner(diff_text, output_dir, target, project, state_manager)

        backend.update_refinement_session(session.session_id, status="baking", snapshot_id=snapshot_id)

        snapshot_tmp_dir = tempfile.mkdtemp(prefix="intentc-refine-snapshot-")
        version_control.materialize(snapshot_id, snapshot_tmp_dir)
        materialized_output_dir = str(Path(snapshot_tmp_dir) / output_dir)

        while attempt < total_attempts:
            attempt += 1
            # Recorded when the attempt *starts* so an attempt that crashes
            # midway is still counted.
            backend.update_refinement_session(session.session_id, bake_attempts=attempt)

            node = project.features[target]
            feature_intent = node.intents[0] if node.intents else IntentFile(name=target, body="")

            response_path = state_manager.build_response_dir / response_file_name(f"{target}-bake")
            sandboxed_profile = _bake_sandbox_profile(
                builder, profile, target, output_dir, implementation, feature_dir, response_path.parent, snapshot_tmp_dir
            )
            agent = resolved_create_agent(sandboxed_profile)

            ctx = RefineContext(
                session_id=session.session_id,
                feature_path=target,
                intent=feature_intent,
                validations=node.validations,
                artifacts=project.artifacts_for(target, implementation),
                project_intent=project.project_intent,
                implementation=implementation,
                output_dir=output_dir,
                journal=session.journal,
                base_commit=session.base_commit,
                snapshot_dir=materialized_output_dir,
                diff=diff_text,
                files_by_owner=by_owner,
                previous_errors=list(previous_errors),
                response_file_path=str(response_path),
                intent_path=intent_path,
                validation_path=validation_path,
                feature_dir=feature_dir,
            )

            log_fn(f"bake {attempt}/{total_attempts}: rewriting intent")
            try:
                bake_response = agent.refine_bake(ctx)
            except AgentError as exc:
                previous_errors = [f"agent error: {exc}"]
                continue

            try:
                reloaded_project = load_project(Path(project.intent_dir))
            except ParseErrors as exc:
                previous_errors = [str(error) for error in exc.errors]
                continue

            lint_issues = [issue for issue in check_project(reloaded_project) if issue.level == "error"]
            if lint_issues:
                previous_errors = [str(issue) for issue in lint_issues]
                continue

            project = reloaded_project
            # Rebuild from the freshly-reloaded intent. `Builder` is
            # constructed once by the caller; its project reference is
            # updated in place so the rebuild sees this attempt's intent
            # edits.
            builder._project = project

            # Commit the target's feature directory as a dedicated intent
            # commit *before* rebuilding. The rebuild's own checkpoint
            # stages everything (`git add -A`), so without this the baked
            # intent edits would be swept into the `build:` commit instead
            # of getting their own traceable history entry.
            commit_message = f"refine {target}: attempt {attempt} [session:{session.session_id[:8]}]"
            commit_id = version_control.commit_paths([feature_dir], commit_message)
            if commit_id:
                log_fn(f"bake {attempt}/{total_attempts}: committing intent ({commit_id[:8]})")

            log_fn(f"bake {attempt}/{total_attempts}: rebuilding {target} from scratch")
            builder.clean(target, output_dir)
            results, error = builder.build(
                BuildOptions(target=target, force=False, output_dir=output_dir, implementation=implementation_name)
            )
            if error is not None:
                failing_step = None
                if results:
                    failing_step = next((step for step in results[-1].steps if step.status == "failed"), None)
                summary = failing_step.summary if failing_step is not None else str(error)
                build_errors = [summary]
                if failing_step is not None and failing_step.phase == "validate":
                    validation_rows = backend.get_validation_results(target)
                    build_errors.extend(
                        f"{row['name']}: {row['reason']}"
                        for row in validation_rows
                        if row["status"] != "pass"
                    )
                previous_errors = build_errors
                continue

            if not no_compare:
                log_fn(f"bake {attempt}/{total_attempts}: comparing against refined snapshot")
                diff_response = None
                compare_error_message = ""
                for compare_attempt in range(1, total_attempts + 1):
                    try:
                        diff_response = run_differencing(
                            output_dir_a=materialized_output_dir,
                            output_dir_b=output_dir,
                            project=project,
                            profile=profile,
                            implementation=implementation_name or None,
                        )
                        break
                    except AgentError as exc:
                        compare_error_message = str(exc)
                        if compare_attempt < total_attempts:
                            log_fn(
                                f"bake {attempt}/{total_attempts}: compare agent error, "
                                f"retrying ({compare_attempt}/{total_attempts}): {exc}"
                            )

                if diff_response is None:
                    # The compare agent itself crashed on every retry: this is
                    # not the intent's fault, but it is still a bake failure
                    # (there is no equivalence verdict to act on).
                    previous_errors = [f"compare: agent error: {compare_error_message}"]
                    continue

                if diff_response.status != "equivalent":
                    failing_dimensions = [d for d in diff_response.dimensions if d.status != "pass"]
                    previous_errors = [f"{d.name}: {d.rationale}" for d in failing_dimensions]
                    for dimension in failing_dimensions:
                        log_fn(f"  ✗ divergent: {dimension.name} — {dimension.rationale}")
                    continue

            successful_generation_id = results[-1].generation_id if results else ""
            succeeded = True
            break
    except Exception:
        backend.update_refinement_session(session.session_id, status="failed", ended_at=_now_iso())
        raise

    if succeeded:
        journal_path = _journal_path(state_manager.base_dir, output_dir, session.session_id)
        journal_path.unlink(missing_ok=True)
        backend.update_refinement_session(
            session.session_id,
            status="baked",
            ended_at=_now_iso(),
            bake_generation_id=successful_generation_id or None,
            bake_response_json=json.dumps(bake_response.model_dump()) if bake_response is not None else None,
        )
        outcome = RefineOutcome.BAKED
    else:
        log_fn(f"Bake exhausted after {total_attempts} attempt(s); restoring refined snapshot")
        version_control.restore(snapshot_id)
        state_manager.set_status(target, TargetStatus.OUTDATED)
        backend.update_refinement_session(
            session.session_id,
            status="failed",
            ended_at=_now_iso(),
            bake_response_json=json.dumps(bake_response.model_dump()) if bake_response is not None else None,
        )
        outcome = RefineOutcome.FAILED

    updated_session = backend.get_refinement_session(session.session_id)
    assert updated_session is not None
    return outcome, updated_session, bake_response


# ---------------------------------------------------------------------------
# abandon_refinement
# ---------------------------------------------------------------------------


def abandon_refinement(
    state_manager: StateManager,
    version_control: VersionControl,
    session: RefinementSession,
    log: Optional[LogFn] = None,
) -> RefinementSession:
    """Discard an open session: restore the output directory to its base
    commit, delete the journal, and mark the session abandoned."""
    log_fn = log or _noop_log
    version_control.restore(session.base_commit)
    journal_path = _journal_path(state_manager.base_dir, session.output_dir, session.session_id)
    journal_path.unlink(missing_ok=True)
    state_manager.backend.update_refinement_session(
        session.session_id, status="abandoned", ended_at=_now_iso()
    )
    log_fn(f"Refinement session {session.session_id[:8]} abandoned; restored to {session.base_commit[:8]}")
    updated = state_manager.backend.get_refinement_session(session.session_id)
    assert updated is not None
    return updated
