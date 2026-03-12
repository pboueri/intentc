# Build Logs Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add structured build logs with timed steps, diff capture, real-time progress output, and an `intentc log` CLI command.

**Architecture:** Extend core types with BuildPhase/StepStatus/BuildStep enums and models. Modify the builder to wrap each pipeline phase in step-logging. Add `get_diff`/`get_diff_stat` to the git manager. Add `intentc log` CLI command for viewing historical logs.

**Tech Stack:** Python 3, Pydantic models, typer CLI, subprocess git commands, pytest

---

### Task 1: Add BuildPhase, StepStatus, BuildStep types to core

**Files:**
- Modify: `src/core/types.py` (add after TargetStatus enum, ~line 28)
- Modify: `src/core/__init__.py` (add exports)
- Test: `src/core/test_core.py`

**Step 1: Write the failing tests**

Add to `src/core/test_core.py`:

```python
from core.types import BuildPhase, StepStatus, BuildStep


class TestBuildPhase:
    def test_enum_values(self):
        assert BuildPhase.RESOLVE_DEPS == "resolve_deps"
        assert BuildPhase.READ_PLAN == "read_plan"
        assert BuildPhase.BUILD == "build"
        assert BuildPhase.POST_BUILD == "post_build"
        assert BuildPhase.VALIDATE == "validate"

    def test_all_phases(self):
        assert len(BuildPhase) == 5


class TestStepStatus:
    def test_enum_values(self):
        assert StepStatus.SUCCESS == "success"
        assert StepStatus.FAILED == "failed"
        assert StepStatus.SKIPPED == "skipped"


class TestBuildStep:
    def test_defaults(self):
        from datetime import datetime
        now = datetime.now()
        step = BuildStep(
            phase=BuildPhase.BUILD,
            status=StepStatus.SUCCESS,
            started_at=now,
            ended_at=now,
            duration_seconds=1.5,
            summary="test",
        )
        assert step.error == ""
        assert step.files_changed == 0
        assert step.diff_stat == ""
        assert step.diff == ""

    def test_serialization_roundtrip(self):
        import json
        from datetime import datetime
        step = BuildStep(
            phase=BuildPhase.POST_BUILD,
            status=StepStatus.SUCCESS,
            started_at=datetime(2026, 1, 1, 0, 0, 0),
            ended_at=datetime(2026, 1, 1, 0, 0, 1),
            duration_seconds=1.0,
            summary="3 files changed",
            files_changed=3,
            diff_stat="3 files changed, +45 -12",
            diff="--- a/f.py\n+++ b/f.py\n",
        )
        data = json.loads(step.model_dump_json())
        restored = BuildStep.model_validate(data)
        assert restored.phase == BuildPhase.POST_BUILD
        assert restored.files_changed == 3
        assert restored.diff_stat == "3 files changed, +45 -12"
```

**Step 2: Run tests to verify they fail**

Run: `cd src && uv run pytest core/test_core.py -v -k "BuildPhase or StepStatus or BuildStep"`
Expected: ImportError — BuildPhase, StepStatus, BuildStep not defined

**Step 3: Implement the types**

In `src/core/types.py`, add after the `TargetStatus` class (after line 28):

```python
class BuildPhase(str, Enum):
    """Discrete lifecycle phases of a target build."""

    RESOLVE_DEPS = "resolve_deps"
    READ_PLAN = "read_plan"
    BUILD = "build"
    POST_BUILD = "post_build"
    VALIDATE = "validate"


class StepStatus(str, Enum):
    """Outcome status for a single build step."""

    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
```

Then add the `BuildStep` model after the `ValidationFile` class (after line 65):

```python
class BuildStep(BaseModel):
    """Captures one discrete phase of a build with timing and details."""

    phase: BuildPhase = BuildPhase.BUILD
    status: StepStatus = StepStatus.SUCCESS
    started_at: datetime = Field(default_factory=datetime.now)
    ended_at: datetime = Field(default_factory=datetime.now)
    duration_seconds: float = 0.0
    summary: str = ""
    error: str = ""
    files_changed: int = 0
    diff_stat: str = ""
    diff: str = ""

    model_config = {"extra": "ignore"}
```

Update `src/core/__init__.py` to add `BuildPhase`, `StepStatus`, `BuildStep` to imports and `__all__`.

**Step 4: Run tests to verify they pass**

Run: `cd src && uv run pytest core/test_core.py -v -k "BuildPhase or StepStatus or BuildStep"`
Expected: PASS

**Step 5: Commit**

```bash
git add src/core/types.py src/core/__init__.py src/core/test_core.py
git commit -m "feat: add BuildPhase, StepStatus, BuildStep core types"
```

---

### Task 2: Extend BuildResult with steps and total_duration_seconds

**Files:**
- Modify: `src/core/types.py` (BuildResult class, ~line 207)
- Test: `src/core/test_core.py`
- Test: `src/state/test_state.py`

**Step 1: Write failing tests**

Add to `src/core/test_core.py`:

```python
class TestBuildResultSteps:
    def test_defaults_empty_steps(self):
        r = BuildResult(target="x", generation_id="gen-1", success=True)
        assert r.steps == []
        assert r.total_duration_seconds == 0.0

    def test_with_steps(self):
        from datetime import datetime
        step = BuildStep(
            phase=BuildPhase.BUILD,
            status=StepStatus.SUCCESS,
            started_at=datetime.now(),
            ended_at=datetime.now(),
            duration_seconds=5.0,
            summary="ok",
        )
        r = BuildResult(
            target="x",
            generation_id="gen-1",
            success=True,
            steps=[step],
            total_duration_seconds=5.0,
        )
        assert len(r.steps) == 1
        assert r.total_duration_seconds == 5.0

    def test_backward_compatible_deserialization(self):
        """Old BuildResult JSON without steps/total_duration should still parse."""
        import json
        old_data = {
            "target": "auth",
            "generation_id": "gen-1",
            "success": True,
            "error": "",
            "generated_at": "2026-01-01T00:00:00",
            "files": [],
            "output_dir": "/tmp/out",
        }
        r = BuildResult.model_validate(old_data)
        assert r.steps == []
        assert r.total_duration_seconds == 0.0
```

**Step 2: Run tests to verify they fail**

Run: `cd src && uv run pytest core/test_core.py -v -k "BuildResultSteps"`
Expected: FAIL — `steps` and `total_duration_seconds` fields not found

**Step 3: Implement**

In `src/core/types.py`, add two fields to `BuildResult` (after line 216, the `files` field):

```python
    steps: list["BuildStep"] = Field(default_factory=list)
    total_duration_seconds: float = 0.0
```

**Step 4: Run tests to verify they pass**

Run: `cd src && uv run pytest core/test_core.py -v -k "BuildResultSteps"`
Expected: PASS

**Step 5: Run existing state manager tests to verify backward compatibility**

Run: `cd src && uv run pytest state/test_state.py -v`
Expected: PASS (existing state serialization should tolerate new fields via `extra = "ignore"`)

**Step 6: Commit**

```bash
git add src/core/types.py src/core/test_core.py
git commit -m "feat: extend BuildResult with steps and total_duration_seconds"
```

---

### Task 3: Add get_diff and get_diff_stat to git manager

**Files:**
- Modify: `src/git/manager.py` (GitManager protocol + GitCLIManager)
- Test: `src/git/test_git.py`

**Step 1: Write failing tests**

Add to `src/git/test_git.py`:

```python
class TestGitDiff:
    def test_get_diff_empty_on_clean_repo(self, tmp_path):
        path = str(tmp_path)
        _init_repo(path)
        gm = GitCLIManager()
        gm.initialize(path)
        assert gm.get_diff() == ""

    def test_get_diff_shows_changes(self, tmp_path):
        path = str(tmp_path)
        _init_repo(path)
        # Create and commit a file
        f = os.path.join(path, "hello.py")
        with open(f, "w") as fh:
            fh.write("a = 1\n")
        gm = GitCLIManager()
        gm.initialize(path)
        gm.add([f])
        gm.commit("add hello")
        # Modify the file
        with open(f, "w") as fh:
            fh.write("a = 2\n")
        diff = gm.get_diff()
        assert "-a = 1" in diff
        assert "+a = 2" in diff

    def test_get_diff_with_paths(self, tmp_path):
        path = str(tmp_path)
        _init_repo(path)
        f1 = os.path.join(path, "a.py")
        f2 = os.path.join(path, "b.py")
        with open(f1, "w") as fh:
            fh.write("x\n")
        with open(f2, "w") as fh:
            fh.write("y\n")
        gm = GitCLIManager()
        gm.initialize(path)
        gm.add([f1, f2])
        gm.commit("add both")
        with open(f1, "w") as fh:
            fh.write("x2\n")
        with open(f2, "w") as fh:
            fh.write("y2\n")
        diff = gm.get_diff(paths=[f1])
        assert "a.py" in diff
        assert "b.py" not in diff

    def test_get_diff_stat(self, tmp_path):
        path = str(tmp_path)
        _init_repo(path)
        f = os.path.join(path, "hello.py")
        with open(f, "w") as fh:
            fh.write("a = 1\n")
        gm = GitCLIManager()
        gm.initialize(path)
        gm.add([f])
        gm.commit("add hello")
        with open(f, "w") as fh:
            fh.write("a = 2\nb = 3\n")
        stat = gm.get_diff_stat()
        assert "hello.py" in stat
        assert "1 file changed" in stat

    def test_get_diff_stat_empty(self, tmp_path):
        path = str(tmp_path)
        _init_repo(path)
        gm = GitCLIManager()
        gm.initialize(path)
        assert gm.get_diff_stat() == ""

    def test_get_diff_includes_untracked(self, tmp_path):
        """get_diff with include_untracked stages new files temporarily for diff."""
        path = str(tmp_path)
        _init_repo(path)
        f = os.path.join(path, "new.py")
        with open(f, "w") as fh:
            fh.write("new content\n")
        gm = GitCLIManager()
        gm.initialize(path)
        diff = gm.get_diff(include_untracked=True, paths=[f])
        assert "new.py" in diff
        assert "+new content" in diff
```

Note: `_init_repo` is a helper that already exists in `test_git.py`. Look at the existing helper and use it.

**Step 2: Run tests to verify they fail**

Run: `cd src && uv run pytest git/test_git.py::TestGitDiff -v`
Expected: FAIL — `get_diff` and `get_diff_stat` not defined

**Step 3: Implement**

Add to `GitManager` protocol in `src/git/manager.py`:

```python
    def get_diff(self, paths: list[str] | None = None, include_untracked: bool = False) -> str: ...
    def get_diff_stat(self, paths: list[str] | None = None, include_untracked: bool = False) -> str: ...
```

Add to `GitCLIManager` class:

```python
    def get_diff(self, paths: list[str] | None = None, include_untracked: bool = False) -> str:
        """Get unified diff of working directory changes.

        If include_untracked is True, untracked files in paths are staged
        with --intent-to-add first so they appear in the diff.
        """
        if include_untracked and paths:
            # Add untracked files with --intent-to-add so they show up in diff
            self._run(["add", "--intent-to-add"] + paths, check=False)
        args = ["diff"]
        if paths:
            args.append("--")
            args.extend(paths)
        result = self._run(args, check=False)
        return result.stdout

    def get_diff_stat(self, paths: list[str] | None = None, include_untracked: bool = False) -> str:
        """Get diff stat summary (e.g. '3 files changed, +10 -2')."""
        if include_untracked and paths:
            self._run(["add", "--intent-to-add"] + paths, check=False)
        args = ["diff", "--stat"]
        if paths:
            args.append("--")
            args.extend(paths)
        result = self._run(args, check=False)
        return result.stdout.strip()
```

**Step 4: Run tests to verify they pass**

Run: `cd src && uv run pytest git/test_git.py::TestGitDiff -v`
Expected: PASS

**Step 5: Run all git tests to verify no regressions**

Run: `cd src && uv run pytest git/test_git.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add src/git/manager.py src/git/test_git.py
git commit -m "feat: add get_diff and get_diff_stat to git manager"
```

---

### Task 4: Update builder to emit build steps

**Files:**
- Modify: `src/builder/builder.py`
- Test: `src/builder/test_builder.py`

**Step 1: Write failing tests**

Add to `src/builder/test_builder.py`. First, update the `MockGitManager` class to support the new methods:

```python
class MockGitManager:
    """Mock git manager for testing."""

    def __init__(self):
        self.diff_result = ""
        self.diff_stat_result = ""

    def initialize(self, project_root: str) -> None:
        pass

    def is_git_repo(self) -> bool:
        return True

    def add(self, files: list[str]) -> None:
        pass

    def commit(self, message: str) -> None:
        pass

    def get_diff(self, paths=None, include_untracked=False):
        return self.diff_result

    def get_diff_stat(self, paths=None, include_untracked=False):
        return self.diff_stat_result
```

Then add the test class:

```python
class TestBuildSteps:
    """Tests for structured build step logging."""

    def test_build_result_has_steps(self, simple_project):
        """A successful build produces steps in the BuildResult."""
        state = MockStateManager()
        agent = MockAgent(files=["auth.py"])
        git = MockGitManager()
        cfg = get_default_config()

        builder = _mock_builder(simple_project, agent, state, git, cfg)
        builder.build(BuildOptions())

        result = state.results["auth"]
        assert len(result.steps) > 0
        phases = [s.phase.value for s in result.steps]
        assert "resolve_deps" in phases
        assert "read_plan" in phases
        assert "build" in phases
        assert "post_build" in phases

    def test_steps_have_timing(self, simple_project):
        """Each step has duration_seconds >= 0."""
        state = MockStateManager()
        agent = MockAgent(files=["auth.py"])
        git = MockGitManager()
        cfg = get_default_config()

        builder = _mock_builder(simple_project, agent, state, git, cfg)
        builder.build(BuildOptions())

        result = state.results["auth"]
        for step in result.steps:
            assert step.duration_seconds >= 0
            assert step.started_at is not None
            assert step.ended_at is not None

    def test_total_duration(self, simple_project):
        """total_duration_seconds is computed as sum of step durations."""
        state = MockStateManager()
        agent = MockAgent(files=["auth.py"])
        git = MockGitManager()
        cfg = get_default_config()

        builder = _mock_builder(simple_project, agent, state, git, cfg)
        builder.build(BuildOptions())

        result = state.results["auth"]
        expected = sum(s.duration_seconds for s in result.steps)
        assert abs(result.total_duration_seconds - expected) < 0.01

    def test_successful_steps_have_success_status(self, simple_project):
        """All steps in a successful build have status=success."""
        state = MockStateManager()
        agent = MockAgent(files=["auth.py"])
        git = MockGitManager()
        cfg = get_default_config()

        builder = _mock_builder(simple_project, agent, state, git, cfg)
        builder.build(BuildOptions())

        result = state.results["auth"]
        for step in result.steps:
            # validate may be skipped
            if step.phase.value != "validate":
                assert step.status.value == "success", f"{step.phase} was {step.status}"

    def test_build_failure_records_failed_step(self, simple_project):
        """When agent.build() fails, the build step has status=failed."""
        state = MockStateManager()
        agent = MockAgent(error=RuntimeError("Agent crashed"))
        git = MockGitManager()
        cfg = get_default_config()

        builder = _mock_builder(simple_project, agent, state, git, cfg)

        with pytest.raises(RuntimeError):
            builder.build(BuildOptions())

        result = state.results["auth"]
        build_step = [s for s in result.steps if s.phase.value == "build"][0]
        assert build_step.status.value == "failed"
        assert "Agent crashed" in build_step.error

    def test_post_build_captures_diff(self, simple_project):
        """The post_build step captures diff_stat and diff from git."""
        state = MockStateManager()
        agent = MockAgent(files=["auth.py"])
        git = MockGitManager()
        git.diff_result = "--- a/auth.py\n+++ b/auth.py\n+hello\n"
        git.diff_stat_result = " 1 file changed, 1 insertion(+)"
        cfg = get_default_config()

        builder = _mock_builder(simple_project, agent, state, git, cfg)
        builder.build(BuildOptions())

        result = state.results["auth"]
        post_build = [s for s in result.steps if s.phase.value == "post_build"][0]
        assert post_build.diff_stat == " 1 file changed, 1 insertion(+)"
        assert "auth.py" in post_build.diff

    def test_resolve_deps_summary(self, dep_project):
        """resolve_deps step summary mentions dependency count."""
        state = MockStateManager()
        agent = MockAgent(files=["output.py"])
        git = MockGitManager()
        cfg = get_default_config()

        builder = _mock_builder(dep_project, agent, state, git, cfg)
        builder.build(BuildOptions())

        # Check the 'api' target which has 1 dependency
        result = state.results["api"]
        resolve_step = [s for s in result.steps if s.phase.value == "resolve_deps"][0]
        assert "1" in resolve_step.summary  # "Resolved 1 dependency"

    def test_read_plan_summary(self, simple_project):
        """read_plan step summary mentions the spec name."""
        state = MockStateManager()
        agent = MockAgent(files=[])
        git = MockGitManager()
        cfg = get_default_config()

        builder = _mock_builder(simple_project, agent, state, git, cfg)
        builder.build(BuildOptions())

        result = state.results["auth"]
        read_step = [s for s in result.steps if s.phase.value == "read_plan"][0]
        assert "auth" in read_step.summary
```

**Step 2: Run tests to verify they fail**

Run: `cd src && uv run pytest builder/test_builder.py::TestBuildSteps -v`
Expected: FAIL — result.steps is empty, total_duration_seconds is 0

**Step 3: Implement**

Modify `src/builder/builder.py`. The key changes to the `build` method's per-target loop (the `for target in build_set:` block starting at line 180):

Import the new types at the top:
```python
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
```

Replace the per-target build logic (lines 180-257) with step-wrapped versions. The builder should:

1. Create a `steps: list[BuildStep]` at the start of each target build
2. Use a helper method `_run_step` that takes a phase name, callable, and target name, wraps it in timing, catches exceptions, and appends a BuildStep
3. After all steps complete, compute `total_duration_seconds` and attach `steps` to the `BuildResult`

Add a helper method to `Builder`:

```python
    @staticmethod
    def _run_step(
        phase: BuildPhase,
        fn,
        target_name: str,
    ) -> BuildStep:
        """Execute fn() wrapped in timing, return a BuildStep."""
        started = datetime.now()
        t0 = time.monotonic()
        try:
            summary = fn()
            elapsed = time.monotonic() - t0
            step = BuildStep(
                phase=phase,
                status=StepStatus.SUCCESS,
                started_at=started,
                ended_at=datetime.now(),
                duration_seconds=round(elapsed, 3),
                summary=summary or "",
            )
            _log_step(target_name, phase.value, summary or "done", elapsed)
            return step
        except Exception as e:
            elapsed = time.monotonic() - t0
            step = BuildStep(
                phase=phase,
                status=StepStatus.FAILED,
                started_at=started,
                ended_at=datetime.now(),
                duration_seconds=round(elapsed, 3),
                summary=f"Failed: {e}",
                error=str(e),
            )
            _log_step(target_name, phase.value, f"FAILED: {e}", elapsed, failed=True)
            return step
```

Add a module-level helper for real-time output:

```python
def _log_step(target: str, phase: str, summary: str, duration: float, *, failed: bool = False) -> None:
    """Print structured step progress to stderr."""
    import sys
    status = "FAILED" if failed else "done"
    print(f"[{target}] {phase}... {summary} ({duration:.1f}s)", file=sys.stderr)
```

Then rewrite the per-target build loop to use `_run_step` for each phase. The overall structure:

```python
for target in build_set:
    # Skip check (unchanged)
    status = self.state_manager.get_target_status(target.name)
    if status == TargetStatus.BUILT and not opts.force:
        logger.info("Skipping %s (already built)", target.name)
        continue

    # Profile resolution (unchanged)
    profile_name = opts.profile_name or target.intent.profile or "default"
    profile = get_profile(self.config, profile_name)
    target_agent = self._agent_factory(profile)

    self.state_manager.update_target_status(target.name, TargetStatus.BUILDING)
    generation_id = f"gen-{int(time.time())}"
    logger.info("Building target: %s", target.name)

    steps: list[BuildStep] = []
    build_failed = False
    agent_files: list[str] = []

    # Phase 1: resolve_deps
    dep_names = target.intent.depends_on
    def _resolve_deps():
        n = len(dep_names)
        deps_str = ", ".join(dep_names) if dep_names else "none"
        return f"Resolved {n} dependencies: [{deps_str}]"
    steps.append(self._run_step(BuildPhase.RESOLVE_DEPS, _resolve_deps, target.name))

    # Phase 2: read_plan
    visible_validations = [
        vf for vf in target.validations
        if not all(v.hidden for v in vf.validations)
    ]
    val_count = sum(len(vf.validations) for vf in visible_validations)
    def _read_plan():
        return f"Read {target.name} with {val_count} validations"
    steps.append(self._run_step(BuildPhase.READ_PLAN, _read_plan, target.name))

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

    def _do_build():
        nonlocal agent_files
        agent_files = target_agent.build(build_ctx)
        return f"Agent generated {len(agent_files)} files"

    build_step = self._run_step(BuildPhase.BUILD, _do_build, target.name)
    steps.append(build_step)
    if build_step.status == StepStatus.FAILED:
        build_failed = True

    # Phase 4: post_build (only if build succeeded)
    if not build_failed:
        def _post_build():
            diff_stat = self.git_manager.get_diff_stat(
                paths=[output_dir], include_untracked=True,
            )
            diff = self.git_manager.get_diff(
                paths=[output_dir], include_untracked=True,
            )
            # Parse files_changed from diff_stat
            files_changed = len(agent_files)
            step = steps[-1]  # Will be replaced below
            return diff_stat, diff, files_changed

        started = datetime.now()
        t0 = time.monotonic()
        try:
            diff_stat = self.git_manager.get_diff_stat(
                paths=[output_dir], include_untracked=True,
            )
            diff = self.git_manager.get_diff(
                paths=[output_dir], include_untracked=True,
            )
            elapsed = time.monotonic() - t0
            files_changed = len(agent_files)
            summary = f"{files_changed} files changed"
            if diff_stat:
                summary = diff_stat.splitlines()[-1].strip() if diff_stat.strip() else summary
            post_step = BuildStep(
                phase=BuildPhase.POST_BUILD,
                status=StepStatus.SUCCESS,
                started_at=started,
                ended_at=datetime.now(),
                duration_seconds=round(elapsed, 3),
                summary=summary,
                files_changed=files_changed,
                diff_stat=diff_stat,
                diff=diff,
            )
            _log_step(target.name, "post_build", summary, elapsed)
            steps.append(post_step)
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

    # Phase 5: validate (skipped for now — optional future enhancement)
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
        result = BuildResult(
            target=target.name,
            generation_id=generation_id,
            success=False,
            error=build_step.error,
            generated_at=datetime.now(),
            output_dir=output_dir,
            steps=steps,
            total_duration_seconds=round(total_duration, 3),
        )
        self.state_manager.save_build_result(result)
        self.state_manager.update_target_status(target.name, TargetStatus.FAILED)
        _log_step(target.name, "TOTAL", f"FAILED in {total_duration:.1f}s", total_duration, failed=True)
        raise RuntimeError(f"Failed to build target: {target.name}: {build_step.error}")
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
        self.state_manager.update_target_status(target.name, TargetStatus.BUILT)
        import sys
        print(f"Built {target.name} ({generation_id}) in {total_duration:.1f}s", file=sys.stderr)
        logger.info("Successfully built target: %s (%s)", target.name, generation_id)
```

**Step 4: Run new tests**

Run: `cd src && uv run pytest builder/test_builder.py::TestBuildSteps -v`
Expected: PASS

**Step 5: Run ALL existing builder tests to verify no regressions**

Run: `cd src && uv run pytest builder/test_builder.py -v`
Expected: PASS (all existing tests must still pass — the MockGitManager update must be backward compatible)

**Step 6: Commit**

```bash
git add src/builder/builder.py src/builder/test_builder.py
git commit -m "feat: builder emits structured build steps with timing"
```

---

### Task 5: Add `intentc log` CLI command

**Files:**
- Modify: `src/cli/main.py`
- Test: `src/cli/test_cli.py`

**Step 1: Write failing tests**

Add to `src/cli/test_cli.py`:

```python
class TestLogCommand:
    """Tests for the 'intentc log' command."""

    def test_log_no_builds(self):
        """Log with no build history prints message."""
        with tempfile.TemporaryDirectory() as tmp:
            _init_project(tmp)
            old_cwd = os.getcwd()
            try:
                os.chdir(tmp)
                result = runner.invoke(app, ["log", "nonexistent"])
                assert "No builds found" in result.output
            finally:
                os.chdir(old_cwd)

    def test_log_list_all(self):
        """Log with no target lists all targets with builds."""
        with tempfile.TemporaryDirectory() as tmp:
            _init_project(tmp)
            old_cwd = os.getcwd()
            try:
                os.chdir(tmp)
                # Seed a build result
                from state.manager import new_state_manager
                from core.types import BuildResult, BuildStep, BuildPhase, StepStatus
                from datetime import datetime
                sm = new_state_manager(tmp)
                sm.initialize()
                output_dir = os.path.join(tmp, "build-default")
                os.makedirs(output_dir, exist_ok=True)
                sm.set_output_dir(output_dir)
                sm.save_build_result(BuildResult(
                    target="auth",
                    generation_id="gen-123",
                    success=True,
                    generated_at=datetime(2026, 3, 11, 14, 0),
                    files=["auth.py"],
                    output_dir=output_dir,
                    steps=[
                        BuildStep(
                            phase=BuildPhase.BUILD,
                            status=StepStatus.SUCCESS,
                            started_at=datetime(2026, 3, 11, 14, 0),
                            ended_at=datetime(2026, 3, 11, 14, 0, 5),
                            duration_seconds=5.0,
                            summary="Agent generated 1 file",
                        ),
                    ],
                    total_duration_seconds=5.0,
                ))
                result = runner.invoke(app, ["log"])
                assert result.exit_code == 0
                assert "auth" in result.output
                assert "gen-123" in result.output
            finally:
                os.chdir(old_cwd)

    def test_log_target_summary(self):
        """Log for a specific target shows step details."""
        with tempfile.TemporaryDirectory() as tmp:
            _init_project(tmp)
            old_cwd = os.getcwd()
            try:
                os.chdir(tmp)
                from state.manager import new_state_manager
                from core.types import BuildResult, BuildStep, BuildPhase, StepStatus
                from datetime import datetime
                sm = new_state_manager(tmp)
                sm.initialize()
                output_dir = os.path.join(tmp, "build-default")
                os.makedirs(output_dir, exist_ok=True)
                sm.set_output_dir(output_dir)
                sm.save_build_result(BuildResult(
                    target="auth",
                    generation_id="gen-456",
                    success=True,
                    generated_at=datetime(2026, 3, 11, 14, 0),
                    files=["auth.py"],
                    output_dir=output_dir,
                    steps=[
                        BuildStep(
                            phase=BuildPhase.RESOLVE_DEPS,
                            status=StepStatus.SUCCESS,
                            started_at=datetime(2026, 3, 11, 14, 0),
                            ended_at=datetime(2026, 3, 11, 14, 0, 1),
                            duration_seconds=0.1,
                            summary="Resolved 0 dependencies",
                        ),
                        BuildStep(
                            phase=BuildPhase.BUILD,
                            status=StepStatus.SUCCESS,
                            started_at=datetime(2026, 3, 11, 14, 0, 1),
                            ended_at=datetime(2026, 3, 11, 14, 0, 6),
                            duration_seconds=5.0,
                            summary="Agent generated 1 file",
                        ),
                    ],
                    total_duration_seconds=5.1,
                ))
                result = runner.invoke(app, ["log", "auth"])
                assert result.exit_code == 0
                assert "Build Log: auth" in result.output
                assert "gen-456" in result.output
                assert "resolve_deps" in result.output
                assert "build" in result.output.lower()
                assert "5.1" in result.output  # total duration
            finally:
                os.chdir(old_cwd)

    def test_log_diff_flag(self):
        """Log --diff appends the unified diff."""
        with tempfile.TemporaryDirectory() as tmp:
            _init_project(tmp)
            old_cwd = os.getcwd()
            try:
                os.chdir(tmp)
                from state.manager import new_state_manager
                from core.types import BuildResult, BuildStep, BuildPhase, StepStatus
                from datetime import datetime
                sm = new_state_manager(tmp)
                sm.initialize()
                output_dir = os.path.join(tmp, "build-default")
                os.makedirs(output_dir, exist_ok=True)
                sm.set_output_dir(output_dir)
                sm.save_build_result(BuildResult(
                    target="auth",
                    generation_id="gen-789",
                    success=True,
                    generated_at=datetime(2026, 3, 11, 14, 0),
                    files=["auth.py"],
                    output_dir=output_dir,
                    steps=[
                        BuildStep(
                            phase=BuildPhase.POST_BUILD,
                            status=StepStatus.SUCCESS,
                            started_at=datetime(2026, 3, 11, 14, 0),
                            ended_at=datetime(2026, 3, 11, 14, 0, 1),
                            duration_seconds=0.1,
                            summary="1 file changed",
                            diff="--- a/auth.py\n+++ b/auth.py\n+hello\n",
                            diff_stat="1 file changed, 1 insertion(+)",
                        ),
                    ],
                    total_duration_seconds=0.1,
                ))
                result = runner.invoke(app, ["log", "auth", "--diff"])
                assert result.exit_code == 0
                assert "--- a/auth.py" in result.output
                assert "+hello" in result.output
            finally:
                os.chdir(old_cwd)

    def test_log_no_steps_graceful(self):
        """Log handles build results without step data (pre-logging builds)."""
        with tempfile.TemporaryDirectory() as tmp:
            _init_project(tmp)
            old_cwd = os.getcwd()
            try:
                os.chdir(tmp)
                from state.manager import new_state_manager
                from core.types import BuildResult
                from datetime import datetime
                sm = new_state_manager(tmp)
                sm.initialize()
                output_dir = os.path.join(tmp, "build-default")
                os.makedirs(output_dir, exist_ok=True)
                sm.set_output_dir(output_dir)
                sm.save_build_result(BuildResult(
                    target="auth",
                    generation_id="gen-old",
                    success=True,
                    generated_at=datetime(2026, 3, 11, 14, 0),
                    files=["auth.py"],
                    output_dir=output_dir,
                ))
                result = runner.invoke(app, ["log", "auth"])
                assert result.exit_code == 0
                assert "No step data" in result.output
            finally:
                os.chdir(old_cwd)
```

**Step 2: Run tests to verify they fail**

Run: `cd src && uv run pytest cli/test_cli.py::TestLogCommand -v`
Expected: FAIL — "No such command 'log'"

**Step 3: Implement the log command**

Add to `src/cli/main.py` (after the `status` command block):

```python
# ---------------------------------------------------------------------------
# log
# ---------------------------------------------------------------------------


@app.command()
def log(
    target: Optional[str] = typer.Argument(None, help="Target to show log for"),
    generation: str = typer.Option("", "--generation", help="Specific generation ID"),
    diff: bool = typer.Option(False, "--diff", help="Show full unified diff"),
    output: str = typer.Option("", "--output", "-o", help="Output directory"),
) -> None:
    """View structured build logs."""
    project_root = os.getcwd()
    cfg = _load_config_with_overrides(project_root)

    from state.manager import new_state_manager

    sm = new_state_manager(project_root)
    sm.initialize()

    output_dir = output or cfg.build.default_output
    output_dir = os.path.abspath(os.path.join(project_root, output_dir))
    sm.set_output_dir(output_dir)

    if not target:
        # List all targets with builds
        from parser.parser import TargetRegistry

        try:
            registry = TargetRegistry(project_root)
            registry.load_targets()
            targets = registry.get_all_targets()
            target_names = [t.name for t in targets]
        except Exception:
            target_names = []

        # Also check state for targets that might have builds
        found_any = False
        lines: list[str] = []
        for name in target_names:
            try:
                result = sm.get_latest_build_result(name)
                status_str = "success" if result.success else "failed"
                dur = f"{result.total_duration_seconds:.1f}s" if result.total_duration_seconds else "-"
                files_count = len(result.files)
                files_str = f"{files_count} file{'s' if files_count != 1 else ''}"
                ts = result.generated_at.strftime("%Y-%m-%dT%H:%M")
                lines.append(
                    f"  {name:<20} {result.generation_id}  {status_str:<7}  {dur:>6}  {files_str:<8}  {ts}"
                )
                found_any = True
            except FileNotFoundError:
                continue

        if not found_any:
            typer.echo("No builds found.")
            return

        typer.echo("Build History:")
        for line in lines:
            typer.echo(line)
        return

    # Specific target
    try:
        if generation:
            result = sm.get_build_result(target, generation)
        else:
            result = sm.get_latest_build_result(target)
    except FileNotFoundError as e:
        typer.echo(f"No builds found for target '{target}'")
        return

    status_str = "success" if result.success else "failed"
    ts = result.generated_at.strftime("%Y-%m-%dT%H:%M:%S")
    typer.echo(f"Build Log: {target} ({result.generation_id})")
    typer.echo(f"Status: {status_str} | {ts}")
    typer.echo("")

    if not result.steps:
        typer.echo("  No step data available (build predates logging)")
        typer.echo("")
    else:
        for step in result.steps:
            dur = f"{step.duration_seconds:.1f}s"
            typer.echo(
                f"  [{step.status.value:<7}]  {step.phase.value:<14}  {step.summary:<40}  {dur}"
            )
        typer.echo("")

    total_dur = f"{result.total_duration_seconds:.1f}s" if result.total_duration_seconds else "-"
    files_changed = sum(s.files_changed for s in result.steps)
    typer.echo(f"Total: {total_dur} | Files: {files_changed} changed")

    if diff:
        typer.echo("")
        post_build_steps = [s for s in result.steps if s.phase.value == "post_build"]
        if post_build_steps and post_build_steps[0].diff:
            typer.echo(post_build_steps[0].diff)
        else:
            typer.echo("No diff available.")
```

**Step 4: Run the log command tests**

Run: `cd src && uv run pytest cli/test_cli.py::TestLogCommand -v`
Expected: PASS

**Step 5: Run all CLI tests to verify no regressions**

Run: `cd src && uv run pytest cli/test_cli.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add src/cli/main.py src/cli/test_cli.py
git commit -m "feat: add 'intentc log' CLI command for viewing build logs"
```

---

### Task 6: Run full test suite and fix any issues

**Files:**
- All modified files from tasks 1-5

**Step 1: Run full test suite**

Run: `cd src && uv run pytest -v`
Expected: ALL PASS

**Step 2: Fix any failures**

If any tests fail, fix them. Common issues:
- MockGitManager in builder tests missing new methods
- Import ordering
- Serialization of new enum types

**Step 3: Final commit if fixes needed**

```bash
git add -u
git commit -m "fix: resolve test failures from build logs integration"
```

---

### Task 7: Verify schema validation passes

**Step 1: Run intentc check**

Run: `cd src && uv run python -m cli.main check`
Expected: "All spec files are valid."

This verifies that the new `.ic` and `.icv` files in `intent/build/logs/` and `intent/cli/log/` are valid.

**Step 2: Commit if any spec fixes needed**

```bash
git add intent/
git commit -m "fix: correct spec file schema issues"
```
