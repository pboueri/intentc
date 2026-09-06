"""Tests for the refine workflow: session lifecycle and the bake loop.

Uses a `MockAgent`-derived scripted agent and an in-memory `MockVersionControl`
against a real `SQLiteBackend` in a temporary directory. No test depends on
the `claude` binary -- interactive launches are exercised entirely through
the mock agent's `refine`/`refine_bake` methods.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pytest

from intentc.build.agents import (
    AgentError,
    AgentProfile,
    BuildResponse,
    DifferencingResponse,
    DimensionResult,
    MockAgent,
    RefineBakeResponse,
    RefineContext,
)
from intentc.build.builder.builder import Builder, BuildOptions
from intentc.build.state import StateManager, TargetStatus, VersionControl
from intentc.build.storage import SQLiteBackend
from intentc.core import (
    Artifact,
    Implementation,
    IntentFile,
    ProjectIntent,
    write_intent_file,
    load_project,
)
from intentc.refine import RefineOutcome, RefineUsageError, abandon_refinement, bake_refinement, run_refine
from intentc.refine import workflow
from intentc.refine.workflow import (
    _changed_files_from_diff,
    _count_journal_entries,
    _feature_paths,
    _files_by_owner,
    _journal_path,
    _relative_to_output_dir,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class MockVersionControl(VersionControl):
    """In-memory `VersionControl` for refine tests: fake incrementing commit
    ids, no real git repo or filesystem interaction for diff/restore."""

    def __init__(self, output_dir: str = "src") -> None:
        self.output_dir = output_dir
        self._commits: list[str] = []
        self.dirty = False
        self.restored_to: list[str] = []
        self.snapshot_refs: dict[str, str] = {}
        self.materialized: list[tuple[str, str]] = []
        self.diff_text = (
            f"diff --git a/{output_dir}/store.py b/{output_dir}/store.py\n"
            "@@ -1 +1 @@\n-old\n+new\n"
        )

    def checkpoint(self, message: str) -> str:
        commit_id = f"commit-{len(self._commits) + 1}"
        self._commits.append(commit_id)
        return commit_id

    def diff(self, from_id: str, to_id: str) -> str:
        return self.diff_text

    def restore(self, commit_id: str) -> None:
        self.restored_to.append(commit_id)

    def log(self, target: Optional[str] = None) -> list[str]:
        return list(reversed(self._commits))

    def has_changes(self) -> bool:
        return self.dirty

    def snapshot(self, message: str, ref_name: str) -> str:
        commit_id = f"snapshot-{len(self._commits) + 1}"
        self._commits.append(commit_id)
        self.snapshot_refs[ref_name] = commit_id
        return commit_id

    def materialize(self, commit_id: str, dest_dir) -> None:
        self.materialized.append((commit_id, str(dest_dir)))


class ScriptedRefineAgent(MockAgent):
    """A `MockAgent` whose `build`/`refine_bake` consume a queue of scripted
    responses (or exceptions) so bake-loop retries can be driven deterministically."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.bake_script: list = []
        self.build_script: list = []

    def refine_bake(self, ctx: RefineContext) -> RefineBakeResponse:
        self.refine_bake_calls.append(ctx)
        if self.bake_script:
            item = self.bake_script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return self.refine_bake_response

    def build(self, ctx):
        self.build_calls.append(ctx)
        if self.build_script:
            item = self.build_script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return self.build_response


# ---------------------------------------------------------------------------
# Project / harness setup
# ---------------------------------------------------------------------------


def _write_project(intent_dir: Path, target: str = "store", with_artifact: bool = False) -> None:
    project_intent = ProjectIntent(name="demo", body="A demo project.")
    write_intent_file(project_intent, intent_dir / "project.ic")

    implementation = Implementation(name="default", body="Python 3.11.")
    write_intent_file(implementation, intent_dir / "implementations" / "default.ic")

    intent = IntentFile(name=target, depends_on=[], body="Store the data.")
    if with_artifact:
        schema_path = intent_dir / target / "schema.json"
        schema_path.parent.mkdir(parents=True, exist_ok=True)
        schema_path.write_text('{"type": "object"}', encoding="utf-8")
        intent.artifacts = [Artifact(path="schema.json", kind="schema", note="Task schema.")]
    write_intent_file(intent, intent_dir / target / f"{target}.ic")


class Harness:
    def __init__(self, tmp_path: Path, target: str = "store", output_dir: str = "src", with_artifact: bool = False):
        self.tmp_path = tmp_path
        self.target = target
        self.output_dir = output_dir
        intent_dir = tmp_path / "intent"
        _write_project(intent_dir, target, with_artifact=with_artifact)
        self.project = load_project(intent_dir)

        self.state_manager = StateManager(
            base_dir=tmp_path, output_dir=output_dir, backend=SQLiteBackend(tmp_path, output_dir)
        )
        self.vc = MockVersionControl(output_dir)
        self.profile = AgentProfile(name="test", provider="mock", retries=2)
        self.agent = ScriptedRefineAgent()
        self.agent.build_response = BuildResponse(
            status="success", summary="built", files_created=[f"{target}.py"], files_modified=[]
        )
        self.created_profiles: list[AgentProfile] = []
        self.builder = Builder(
            project=self.project,
            state_manager=self.state_manager,
            version_control=self.vc,
            agent_profile=self.profile,
            create_agent=self._create_agent,
        )

    def _create_agent(self, profile: AgentProfile):
        self.created_profiles.append(profile)
        return self.agent

    def build_initial(self) -> None:
        results, error = self.builder.build(BuildOptions(target=self.target, output_dir=self.output_dir))
        assert error is None, results

    def implementation(self) -> Optional[Implementation]:
        return self.project.resolve_implementation()

    def run_refine(self, **kwargs):
        defaults = dict(
            project=self.project,
            profile=self.profile,
            implementation=self.implementation(),
            state_manager=self.state_manager,
            version_control=self.vc,
            builder=self.builder,
            target=self.target,
            output_dir=self.output_dir,
            create_agent=self._create_agent,
        )
        defaults.update(kwargs)
        return run_refine(**defaults)

    def bake(self, session, **kwargs):
        defaults = dict(
            project=self.project,
            profile=self.profile,
            implementation=self.implementation(),
            state_manager=self.state_manager,
            version_control=self.vc,
            builder=self.builder,
            session=session,
            output_dir=self.output_dir,
            create_agent=self._create_agent,
        )
        defaults.update(kwargs)
        return bake_refinement(**defaults)


@pytest.fixture()
def harness(tmp_path: Path, monkeypatch) -> Harness:
    monkeypatch.chdir(tmp_path)
    h = Harness(tmp_path)
    h.build_initial()
    return h


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_changed_files_from_diff_extracts_b_side_paths():
    diff_text = (
        "diff --git a/src/store/store.py b/src/store/store.py\n@@\n"
        "diff --git a/src/store/models.py b/src/store/models.py\n@@\n"
    )
    assert _changed_files_from_diff(diff_text) == ["src/store/store.py", "src/store/models.py"]


def test_relative_to_output_dir_strips_prefix():
    assert _relative_to_output_dir("src/store/store.py", "src") == "store/store.py"
    assert _relative_to_output_dir("other/store.py", "src") == "other/store.py"


def test_count_journal_entries_counts_h2_headings():
    text = "# Refinement journal\n\n## 1. First\nbody\n\n## 2. Second\nbody\n"
    assert _count_journal_entries(text) == 2
    assert _count_journal_entries("# Refinement journal\n") == 0


def test_files_by_owner_attributes_by_build_manifest(harness: Harness):
    by_owner = _files_by_owner(
        harness.vc.diff_text, harness.output_dir, harness.target, harness.project, harness.state_manager
    )
    assert by_owner == {"store": ["store.py"]}


def test_feature_paths_uses_existing_source_path_when_present(harness: Harness):
    intent_path, validation_path, feature_dir = _feature_paths(harness.project, harness.target)
    assert intent_path.endswith("store/store.ic")
    assert validation_path.endswith("store/validation.icv")
    assert feature_dir.endswith("store")


# ---------------------------------------------------------------------------
# Session start: refusals
# ---------------------------------------------------------------------------


def test_start_refuses_on_dirty_output_dir(harness: Harness):
    harness.vc.dirty = True
    with pytest.raises(RefineUsageError, match="uncommitted changes"):
        harness.run_refine(no_bake=True)
    assert harness.agent.refine_calls == []


def test_start_refuses_on_pending_target(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    h = Harness(tmp_path)
    # Never built: status is pending.
    with pytest.raises(RefineUsageError, match="has not been built"):
        h.run_refine(no_bake=True)
    assert h.agent.refine_calls == []
    assert h.created_profiles == []


def test_start_refuses_on_failed_target(harness: Harness):
    harness.state_manager.set_status(harness.target, TargetStatus.FAILED)
    with pytest.raises(RefineUsageError, match="has not been built"):
        harness.run_refine(no_bake=True)
    assert harness.agent.refine_calls == []


def test_start_refuses_when_another_target_has_open_session(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    intent_dir = tmp_path / "intent"
    _write_project(intent_dir, "store")
    # A second, independent feature in the same project/output dir.
    other_intent = IntentFile(name="other", depends_on=[], body="Another feature.")
    write_intent_file(other_intent, intent_dir / "other" / "other.ic")
    project = load_project(intent_dir)

    output_dir = "src"
    state_manager = StateManager(base_dir=tmp_path, output_dir=output_dir, backend=SQLiteBackend(tmp_path, output_dir))
    vc = MockVersionControl(output_dir)
    profile = AgentProfile(name="test", provider="mock", retries=2)
    agent = ScriptedRefineAgent()
    agent.build_response = BuildResponse(status="success", summary="built", files_created=[], files_modified=[])
    builder = Builder(
        project=project, state_manager=state_manager, version_control=vc, agent_profile=profile,
        create_agent=lambda p: agent,
    )
    builder.build(BuildOptions(target="store", output_dir=output_dir))
    builder.build(BuildOptions(target="other", output_dir=output_dir))

    run_refine(
        project=project, profile=profile, implementation=None, state_manager=state_manager,
        version_control=vc, builder=builder, target="store", output_dir=output_dir,
        create_agent=lambda p: agent, no_bake=True,
    )

    with pytest.raises(RefineUsageError, match="Bake or abandon it first"):
        run_refine(
            project=project, profile=profile, implementation=None, state_manager=state_manager,
            version_control=vc, builder=builder, target="other", output_dir=output_dir,
            create_agent=lambda p: agent, no_bake=True,
        )


# ---------------------------------------------------------------------------
# Session start / resume: recording
# ---------------------------------------------------------------------------


def test_session_records_journal_and_persists_row(harness: Harness):
    def write_journal(ctx: RefineContext) -> None:
        Path(ctx.journal_path).write_text(
            ctx.journal + "## 1. Bigger buttons\n**Asked:** \"bigger buttons\"\n"
            "**Decided:** raised hit area\n**Rule:** 44px minimum\n**Changed:** x.py\n**Check:** none\n",
            encoding="utf-8",
        )

    harness.agent.refine_side_effect = write_journal

    outcome, session, response = harness.run_refine(seed_prompt="bigger buttons", no_bake=True)

    assert outcome == RefineOutcome.RECORDED
    assert response is None
    assert session.status == "recording"
    assert session.base_commit == harness.vc._commits[0]
    assert "Bigger buttons" in session.journal
    assert len(harness.agent.refine_calls) == 1
    ctx = harness.agent.refine_calls[0]
    assert ctx.seed_prompt == "bigger buttons"
    assert ctx.journal == ""  # empty on a brand new session

    fetched = harness.state_manager.backend.get_refinement_session(session.session_id)
    assert fetched is not None
    assert fetched.status == "recording"
    assert "Bigger buttons" in fetched.journal


def test_session_write_paths_cover_only_output_dir_and_session_dir(harness: Harness):
    harness.created_profiles.clear()
    harness.run_refine(no_bake=True)
    profile = harness.created_profiles[0]
    session_dir = str((harness.tmp_path / ".intentc" / "state" / harness.output_dir / "refinements").resolve())
    assert len(profile.sandbox_write_paths) == 2
    assert any(str((harness.tmp_path / harness.output_dir).resolve()) == p for p in profile.sandbox_write_paths)
    assert any(p.startswith(session_dir) for p in profile.sandbox_write_paths)


def test_session_carries_target_artifacts(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    h = Harness(tmp_path, with_artifact=True)
    h.build_initial()

    h.run_refine(no_bake=True)

    ctx = h.agent.refine_calls[0]
    assert len(ctx.artifacts) == 1
    assert ctx.artifacts[0].path == "schema.json"
    assert ctx.artifacts[0].kind == "schema"


def test_resume_appends_to_journal_and_passes_prior_journal(harness: Harness):
    def first_ask(ctx: RefineContext) -> None:
        Path(ctx.journal_path).write_text(ctx.journal + "## 1. First ask\nbody\n", encoding="utf-8")

    harness.agent.refine_side_effect = first_ask
    outcome1, session1, _ = harness.run_refine(seed_prompt="first ask", no_bake=True)
    assert outcome1 == RefineOutcome.RECORDED

    def second_ask(ctx: RefineContext) -> None:
        assert "## 1. First ask" in ctx.journal  # prior journal passed through
        Path(ctx.journal_path).write_text(ctx.journal + "## 2. Second ask\nbody\n", encoding="utf-8")

    harness.agent.refine_side_effect = second_ask
    outcome2, session2, _ = harness.run_refine(seed_prompt="second ask", no_bake=True)

    assert outcome2 == RefineOutcome.RECORDED
    assert session2.session_id == session1.session_id
    assert "## 1. First ask" in session2.journal
    assert "## 2. Second ask" in session2.journal
    assert len(harness.agent.refine_calls) == 2


# ---------------------------------------------------------------------------
# Abandon
# ---------------------------------------------------------------------------


def test_abandon_restores_base_commit_and_marks_abandoned(harness: Harness):
    outcome, session, _ = harness.run_refine(no_bake=True)
    journal_file = _journal_path(harness.tmp_path, harness.output_dir, session.session_id)
    assert journal_file.exists()

    updated = abandon_refinement(harness.state_manager, harness.vc, session)

    assert updated.status == "abandoned"
    assert updated.ended_at is not None
    assert harness.vc.restored_to == [session.base_commit]
    assert not journal_file.exists()


# ---------------------------------------------------------------------------
# Bake: happy path and call order
# ---------------------------------------------------------------------------


def test_bake_success_calls_refine_bake_then_clean_build_compare_in_order(harness: Harness, monkeypatch):
    outcome0, session, _ = harness.run_refine(no_bake=True)

    call_order: list[str] = []
    original_clean = Builder.clean
    original_build = Builder.build

    def tracking_clean(self, *a, **k):
        call_order.append("clean")
        return original_clean(self, *a, **k)

    def tracking_build(self, *a, **k):
        call_order.append("build")
        return original_build(self, *a, **k)

    def fake_differencing(**kwargs):
        call_order.append("compare")
        return DifferencingResponse(status="equivalent", dimensions=[], summary="same")

    original_refine_bake = harness.agent.refine_bake

    def tracking_refine_bake(ctx):
        call_order.append("refine_bake")
        return original_refine_bake(ctx)

    harness.agent.refine_bake = tracking_refine_bake

    monkeypatch.setattr(Builder, "clean", tracking_clean)
    monkeypatch.setattr(Builder, "build", tracking_build)
    monkeypatch.setattr(workflow, "run_differencing", fake_differencing)

    outcome, session, response = harness.bake(session)

    assert outcome == RefineOutcome.BAKED
    assert call_order == ["refine_bake", "clean", "build", "compare"]
    assert len(harness.agent.refine_bake_calls) == 1
    assert session.status == "baked"
    assert session.bake_generation_id
    assert response is not None
    assert response.status == "success"

    journal_file = _journal_path(harness.tmp_path, harness.output_dir, session.session_id)
    assert not journal_file.exists()


def test_bake_populates_diff_files_by_owner_journal_and_snapshot_dir(harness: Harness):
    def write_journal(ctx: RefineContext) -> None:
        Path(ctx.journal_path).write_text(ctx.journal + "## 1. Ask\nbody\n", encoding="utf-8")

    harness.agent.refine_side_effect = write_journal
    outcome0, session, _ = harness.run_refine(no_bake=True)

    outcome, session, response = harness.bake(session, no_compare=True)

    assert outcome == RefineOutcome.BAKED
    ctx = harness.agent.refine_bake_calls[0]
    assert ctx.diff == harness.vc.diff_text
    assert ctx.files_by_owner == {"store": ["store.py"]}
    assert "## 1. Ask" in ctx.journal
    assert ctx.snapshot_dir.endswith(harness.output_dir)
    assert harness.vc.materialized  # materialize was called


def test_bake_no_compare_skips_differencing(harness: Harness, monkeypatch):
    outcome0, session, _ = harness.run_refine(no_bake=True)

    def boom(**kwargs):
        raise AssertionError("run_differencing should not be called with --no-compare")

    monkeypatch.setattr(workflow, "run_differencing", boom)
    outcome, session, _ = harness.bake(session, no_compare=True)

    assert outcome == RefineOutcome.BAKED


# ---------------------------------------------------------------------------
# Bake: retries fed by divergence / build failure
# ---------------------------------------------------------------------------


def test_bake_divergent_compare_feeds_dimension_into_next_attempt(harness: Harness, monkeypatch):
    outcome0, session, _ = harness.run_refine(no_bake=True)

    responses = [
        DifferencingResponse(
            status="divergent",
            dimensions=[DimensionResult(name="runtime_behavior", status="fail", rationale="output differs")],
            summary="not equivalent",
        ),
        DifferencingResponse(status="equivalent", dimensions=[], summary="same"),
    ]

    def fake_differencing(**kwargs):
        return responses.pop(0)

    monkeypatch.setattr(workflow, "run_differencing", fake_differencing)
    outcome, session, response = harness.bake(session)

    assert outcome == RefineOutcome.BAKED
    assert len(harness.agent.refine_bake_calls) == 2
    assert harness.agent.refine_bake_calls[1].previous_errors == ["runtime_behavior: output differs"]
    assert session.bake_attempts == 2


def test_bake_failed_rebuild_feeds_failing_step_summary_into_next_attempt(harness: Harness):
    outcome0, session, _ = harness.run_refine(no_bake=True)

    # profile.retries == 2: the first bake attempt's builder.build() exhausts
    # 2 internal attempts before giving up; the second bake attempt succeeds
    # on its first internal attempt.
    harness.agent.build_script = [
        AgentError("boom"),
        AgentError("boom"),
        BuildResponse(status="success", summary="built", files_created=["store.py"], files_modified=[]),
    ]

    outcome, session, response = harness.bake(session, no_compare=True)

    assert outcome == RefineOutcome.BAKED
    assert len(harness.agent.refine_bake_calls) == 2
    assert "agent error: boom" in harness.agent.refine_bake_calls[1].previous_errors[0]
    assert session.bake_attempts == 2


def test_refine_bake_agent_error_is_retried_with_error_in_previous_errors(harness: Harness):
    outcome0, session, _ = harness.run_refine(no_bake=True)
    harness.agent.bake_script = [AgentError("bake exploded")]

    outcome, session, response = harness.bake(session, no_compare=True)

    assert outcome == RefineOutcome.BAKED
    assert len(harness.agent.refine_bake_calls) == 2
    assert "bake exploded" in harness.agent.refine_bake_calls[1].previous_errors[0]


# ---------------------------------------------------------------------------
# Bake: exhaustion
# ---------------------------------------------------------------------------


def test_bake_exhaustion_restores_snapshot_marks_outdated_and_failed(harness: Harness):
    harness.profile.retries = 1
    outcome0, session, _ = harness.run_refine(no_bake=True)
    harness.agent.bake_script = [AgentError("always fails")]

    outcome, session, response = harness.bake(session, no_compare=True)

    assert outcome == RefineOutcome.FAILED
    assert session.status == "failed"
    assert session.ended_at is not None
    assert harness.state_manager.get_status(harness.target) == TargetStatus.OUTDATED
    assert harness.vc.restored_to == [session.snapshot_id]
    intent_path, _, _ = _feature_paths(harness.project, harness.target)
    assert Path(intent_path).exists()


def test_run_refine_confirm_declined_leaves_session_open(harness: Harness):
    outcome, session, response = harness.run_refine(confirm=lambda _prompt: False)

    assert outcome == RefineOutcome.RECORDED
    assert response is None
    assert session.status == "recording"
    assert harness.agent.refine_bake_calls == []


def test_run_refine_confirm_accepted_bakes(harness: Harness):
    outcome, session, response = harness.run_refine(confirm=lambda _prompt: True, no_compare=True)

    assert outcome == RefineOutcome.BAKED
    assert len(harness.agent.refine_bake_calls) == 1
