"""Tests for the builder package."""

from __future__ import annotations

import logging
import os
import textwrap
from datetime import datetime
from typing import Any

import pytest

from agent.base import BuildContext
from builder.builder import Builder, BuildOptions
from config.config import Config, get_default_config
from core.types import (
    AgentProfile,
    BuildPhase,
    BuildResult,
    BuildStep,
    StepStatus,
    Target,
    TargetStatus,
    Validation,
    ValidationFile,
)


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


class MockStateManager:
    """In-memory state manager for testing."""

    def __init__(self) -> None:
        self.statuses: dict[str, TargetStatus] = {}
        self.results: dict[str, BuildResult] = {}
        self.output_dir: str = ""

    def initialize(self) -> None:
        pass

    def set_output_dir(self, output_dir: str) -> None:
        self.output_dir = output_dir

    def get_target_status(self, name: str) -> TargetStatus:
        return self.statuses.get(name, TargetStatus.PENDING)

    def update_target_status(self, name: str, status: TargetStatus) -> None:
        self.statuses[name] = status

    def save_build_result(self, result: BuildResult) -> None:
        self.results[result.target] = result

    def get_latest_build_result(self, name: str) -> BuildResult:
        if name not in self.results:
            raise FileNotFoundError(f"No builds found for target '{name}'")
        return self.results[name]

    def list_build_results(self, name: str) -> list[BuildResult]:
        if name in self.results:
            return [self.results[name]]
        return []

    def reset_target(self, name: str) -> None:
        self.statuses[name] = TargetStatus.PENDING
        self.results.pop(name, None)

    def reset_all(self) -> None:
        self.statuses.clear()
        self.results.clear()


class MockAgent:
    """Mock agent that records calls and returns configurable file lists."""

    def __init__(
        self, files: list[str] | None = None, error: Exception | None = None
    ) -> None:
        self._files = files or []
        self._error = error
        self.build_calls: list[BuildContext] = []

    def build(self, build_ctx: BuildContext) -> list[str]:
        self.build_calls.append(build_ctx)
        if self._error:
            raise self._error
        return list(self._files)

    def validate_with_llm(
        self, validation: Validation, generated_files: list[str]
    ) -> tuple[bool, str]:
        return True, "ok"

    def get_name(self) -> str:
        return "mock-agent"

    def get_type(self) -> str:
        return "mock"


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


def _mock_builder(
    project_root: str,
    agent: MockAgent,
    state: MockStateManager,
    git: MockGitManager,
    cfg: Config,
) -> Builder:
    """Create a Builder that uses the given mock agent for all targets."""
    return Builder(
        project_root,
        agent,
        state,
        git,
        cfg,
        agent_factory=lambda _profile: agent,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_file(path: str, content: str) -> None:
    """Write a file creating parent directories as needed."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(textwrap.dedent(content))


def _make_project(
    root: str,
    *,
    targets: dict[str, dict[str, str]] | None = None,
    project_ic: str | None = None,
    config_yaml: str | None = None,
) -> None:
    """Create a minimal intentc project structure.

    Args:
        root: Project root directory.
        targets: Mapping of target_name -> {"ic": content, "icv": content}.
                 The "icv" key is optional.
        project_ic: Content for intent/project.ic.
        config_yaml: Content for .intentc/config.yaml.
    """
    intent_dir = os.path.join(root, "intent")
    os.makedirs(intent_dir, exist_ok=True)

    # project.ic
    if project_ic is None:
        project_ic = """\
            ---
            name: test-project
            version: 1
            ---
            Test project.
        """
    _write_file(os.path.join(intent_dir, "project.ic"), project_ic)

    # Config
    if config_yaml is None:
        config_yaml = """\
            version: 1
            profiles:
              default:
                provider: claude
                timeout: 5m
                retries: 3
                rate_limit: 1s
            build:
              default_output: build-default
            logging:
              level: info
        """
    _write_file(os.path.join(root, ".intentc", "config.yaml"), config_yaml)

    # Targets
    if targets:
        for name, files in targets.items():
            target_dir = os.path.join(intent_dir, name)
            os.makedirs(target_dir, exist_ok=True)
            _write_file(os.path.join(target_dir, f"{name}.ic"), files["ic"])
            if "icv" in files:
                _write_file(
                    os.path.join(target_dir, f"{name}.icv"), files["icv"]
                )


@pytest.fixture
def simple_project(tmp_path):
    """Create a single-target project with no dependencies."""
    root = str(tmp_path / "proj")
    _make_project(
        root,
        targets={
            "auth": {
                "ic": textwrap.dedent("""\
                    ---
                    name: auth
                    version: 1
                    ---
                    Build an auth module.
                """),
                "icv": textwrap.dedent("""\
                    ---
                    target: auth
                    version: 1
                    validations:
                      - name: check-auth-file
                        type: file_check
                        path: auth.py
                    ---
                """),
            },
        },
    )
    return root


@pytest.fixture
def dep_project(tmp_path):
    """Create a two-target project where 'api' depends on 'auth'."""
    root = str(tmp_path / "proj")
    _make_project(
        root,
        targets={
            "auth": {
                "ic": textwrap.dedent("""\
                    ---
                    name: auth
                    version: 1
                    ---
                    Build an auth module.
                """),
                "icv": textwrap.dedent("""\
                    ---
                    target: auth
                    version: 1
                    validations:
                      - name: check-auth-file
                        type: file_check
                        path: auth.py
                    ---
                """),
            },
            "api": {
                "ic": textwrap.dedent("""\
                    ---
                    name: api
                    version: 1
                    depends_on:
                      - auth
                    ---
                    Build the API on top of auth.
                """),
                "icv": textwrap.dedent("""\
                    ---
                    target: api
                    version: 1
                    validations:
                      - name: check-api-file
                        type: file_check
                        path: api.py
                    ---
                """),
            },
        },
    )
    return root


@pytest.fixture
def profile_project(tmp_path):
    """Create a project with a per-target profile and multiple config profiles."""
    root = str(tmp_path / "proj")
    _make_project(
        root,
        targets={
            "frontend": {
                "ic": textwrap.dedent("""\
                    ---
                    name: frontend
                    version: 1
                    profile: fast
                    ---
                    Build a frontend.
                """),
                "icv": textwrap.dedent("""\
                    ---
                    target: frontend
                    version: 1
                    validations:
                      - name: check-frontend
                        type: file_check
                        path: index.html
                    ---
                """),
            },
        },
        config_yaml=textwrap.dedent("""\
            version: 1
            profiles:
              default:
                provider: claude
                timeout: 5m
                retries: 3
                rate_limit: 1s
              fast:
                provider: claude
                timeout: 1m
                retries: 1
                rate_limit: 0s
            build:
              default_output: build-default
            logging:
              level: info
        """),
    )
    return root


# ---------------------------------------------------------------------------
# Tests: full pipeline
# ---------------------------------------------------------------------------


class TestBuildFullPipeline:
    """Tests for the build() method end-to-end."""

    def test_build_single_target(self, simple_project):
        """Full pipeline with a single target succeeds and records state."""
        state = MockStateManager()
        agent = MockAgent(files=["auth.py"])
        git = MockGitManager()
        cfg = get_default_config()

        builder = _mock_builder(simple_project, agent, state, git, cfg)
        builder.build(BuildOptions())

        # Agent was invoked once
        assert len(agent.build_calls) == 1
        ctx = agent.build_calls[0]
        assert ctx.intent.name == "auth"
        assert ctx.project_root == simple_project

        # State was updated
        assert state.statuses["auth"] == TargetStatus.BUILT
        assert "auth" in state.results
        assert state.results["auth"].success is True
        assert state.results["auth"].files == ["auth.py"]

    def test_build_creates_output_dir(self, simple_project):
        """Build creates the output directory if it does not exist."""
        state = MockStateManager()
        agent = MockAgent(files=[])
        git = MockGitManager()
        cfg = get_default_config()

        output_dir = os.path.join(simple_project, "custom-output")
        assert not os.path.exists(output_dir)

        builder = _mock_builder(simple_project, agent, state, git, cfg)
        builder.build(BuildOptions(output_dir="custom-output"))

        assert os.path.isdir(output_dir)

    def test_build_skips_already_built(self, simple_project):
        """Targets with status 'built' are skipped unless force is set."""
        state = MockStateManager()
        state.statuses["auth"] = TargetStatus.BUILT
        agent = MockAgent(files=[])
        git = MockGitManager()
        cfg = get_default_config()

        builder = _mock_builder(simple_project, agent, state, git, cfg)
        builder.build(BuildOptions())

        # Agent should not have been invoked
        assert len(agent.build_calls) == 0


# ---------------------------------------------------------------------------
# Tests: dry run
# ---------------------------------------------------------------------------


class TestBuildDryRun:
    """Tests for dry_run mode."""

    def test_dry_run_never_invokes_agent(self, simple_project):
        """Dry run prints the plan but never calls the agent."""
        state = MockStateManager()
        agent = MockAgent(files=["auth.py"])
        git = MockGitManager()
        cfg = get_default_config()

        builder = _mock_builder(simple_project, agent, state, git, cfg)
        builder.build(BuildOptions(dry_run=True))

        assert len(agent.build_calls) == 0

    def test_dry_run_does_not_update_state(self, simple_project):
        """Dry run leaves state untouched."""
        state = MockStateManager()
        agent = MockAgent(files=[])
        git = MockGitManager()
        cfg = get_default_config()

        builder = _mock_builder(simple_project, agent, state, git, cfg)
        builder.build(BuildOptions(dry_run=True))

        assert state.statuses == {}
        assert state.results == {}

    def test_dry_run_output(self, simple_project, caplog):
        """Dry run logs the target names."""
        state = MockStateManager()
        agent = MockAgent(files=[])
        git = MockGitManager()
        cfg = get_default_config()

        builder = _mock_builder(simple_project, agent, state, git, cfg)
        with caplog.at_level(logging.INFO, logger="intentc.builder"):
            builder.build(BuildOptions(dry_run=True))

        assert any("auth" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Tests: force rebuild
# ---------------------------------------------------------------------------


class TestBuildForce:
    """Tests for force rebuild."""

    def test_force_rebuilds_already_built(self, simple_project):
        """Force rebuilds targets that already have 'built' status."""
        state = MockStateManager()
        state.statuses["auth"] = TargetStatus.BUILT
        agent = MockAgent(files=["auth.py"])
        git = MockGitManager()
        cfg = get_default_config()

        builder = _mock_builder(simple_project, agent, state, git, cfg)
        builder.build(BuildOptions(force=True))

        # Agent should have been invoked
        assert len(agent.build_calls) == 1

    def test_force_rebuilds_failed_target(self, simple_project):
        """Force rebuilds targets that had previously failed."""
        state = MockStateManager()
        state.statuses["auth"] = TargetStatus.FAILED
        agent = MockAgent(files=["auth.py"])
        git = MockGitManager()
        cfg = get_default_config()

        builder = _mock_builder(simple_project, agent, state, git, cfg)
        builder.build(BuildOptions(force=True))

        assert len(agent.build_calls) == 1
        assert state.statuses["auth"] == TargetStatus.BUILT


# ---------------------------------------------------------------------------
# Tests: dependency ordering
# ---------------------------------------------------------------------------


class TestBuildDependencies:
    """Tests for dependency-aware build ordering."""

    def test_dependencies_built_before_dependents(self, dep_project):
        """'auth' is built before 'api' because api depends on auth."""
        state = MockStateManager()
        agent = MockAgent(files=["output.py"])
        git = MockGitManager()
        cfg = get_default_config()

        builder = _mock_builder(dep_project, agent, state, git, cfg)
        builder.build(BuildOptions())

        assert len(agent.build_calls) == 2
        build_order = [c.intent.name for c in agent.build_calls]
        assert build_order.index("auth") < build_order.index("api")

    def test_specific_target_includes_deps(self, dep_project):
        """Building a specific target also builds its dependencies."""
        state = MockStateManager()
        agent = MockAgent(files=["output.py"])
        git = MockGitManager()
        cfg = get_default_config()

        builder = _mock_builder(dep_project, agent, state, git, cfg)
        builder.build(BuildOptions(target="api"))

        # Both auth and api should be built
        assert len(agent.build_calls) == 2
        build_order = [c.intent.name for c in agent.build_calls]
        assert "auth" in build_order
        assert "api" in build_order
        assert build_order.index("auth") < build_order.index("api")


# ---------------------------------------------------------------------------
# Tests: failure handling
# ---------------------------------------------------------------------------


class TestBuildFailure:
    """Tests for build failure scenarios."""

    def test_failure_stops_build(self, simple_project):
        """An agent error raises RuntimeError and records failure."""
        state = MockStateManager()
        agent = MockAgent(error=RuntimeError("Agent crashed"))
        git = MockGitManager()
        cfg = get_default_config()

        builder = _mock_builder(simple_project, agent, state, git, cfg)

        with pytest.raises(RuntimeError, match="Failed to build target: auth"):
            builder.build(BuildOptions())

        assert state.statuses["auth"] == TargetStatus.FAILED
        assert "auth" in state.results
        assert state.results["auth"].success is False
        assert "Agent crashed" in state.results["auth"].error

    def test_failure_stops_subsequent_targets(self, dep_project):
        """Failure on 'auth' prevents 'api' from being built."""
        call_count = 0

        class FailOnceAgent:
            def __init__(self):
                self.build_calls = []

            def build(self, ctx: BuildContext) -> list[str]:
                self.build_calls.append(ctx)
                if ctx.intent.name == "auth":
                    raise RuntimeError("auth build failed")
                return ["api.py"]

            def validate_with_llm(self, v, f):
                return True, "ok"

            def get_name(self):
                return "fail-once"

            def get_type(self):
                return "mock"

        state = MockStateManager()
        agent = FailOnceAgent()
        git = MockGitManager()
        cfg = get_default_config()

        builder = _mock_builder(dep_project, agent, state, git, cfg)

        with pytest.raises(RuntimeError, match="auth"):
            builder.build(BuildOptions())

        # Only auth was attempted; api was never reached
        assert len(agent.build_calls) == 1
        assert agent.build_calls[0].intent.name == "auth"

    def test_failure_records_error_message(self, simple_project):
        """Build failure records the error string in BuildResult."""
        state = MockStateManager()
        agent = MockAgent(error=ValueError("bad config"))
        git = MockGitManager()
        cfg = get_default_config()

        builder = _mock_builder(simple_project, agent, state, git, cfg)

        with pytest.raises(RuntimeError):
            builder.build(BuildOptions())

        result = state.results["auth"]
        assert result.success is False
        assert "bad config" in result.error


# ---------------------------------------------------------------------------
# Tests: profile resolution
# ---------------------------------------------------------------------------


class TestProfileResolution:
    """Tests for agent profile resolution priority."""

    def test_cli_profile_overrides_target(self, profile_project):
        """CLI profile_name takes precedence over per-target profile."""
        from config.config import load_config

        cfg = load_config(profile_project)
        state = MockStateManager()
        agent = MockAgent(files=["index.html"])
        git = MockGitManager()

        # Track which profile names get resolved
        resolved_profiles: list[str] = []

        def tracking_factory(profile):
            resolved_profiles.append(profile.name)
            return agent

        builder = Builder(
            profile_project, agent, state, git, cfg,
            agent_factory=tracking_factory,
        )
        builder.build(BuildOptions(profile_name="default"))

        # CLI profile_name="default" should override the target's profile="fast"
        assert resolved_profiles == ["default"]

    def test_target_profile_used_when_no_cli_override(self, profile_project):
        """Per-target profile is used when no CLI override is given."""
        from config.config import load_config

        cfg = load_config(profile_project)
        state = MockStateManager()
        agent = MockAgent(files=["index.html"])
        git = MockGitManager()

        resolved_profiles: list[str] = []

        def tracking_factory(profile):
            resolved_profiles.append(profile.name)
            return agent

        builder = Builder(
            profile_project, agent, state, git, cfg,
            agent_factory=tracking_factory,
        )
        builder.build(BuildOptions())

        # No CLI override, target has profile="fast"
        assert resolved_profiles == ["fast"]

    def test_default_profile_when_none_specified(self, simple_project):
        """Falls back to 'default' when neither CLI nor target specifies a profile."""
        state = MockStateManager()
        agent = MockAgent(files=[])
        git = MockGitManager()
        cfg = get_default_config()

        resolved_profiles: list[str] = []

        def tracking_factory(profile):
            resolved_profiles.append(profile.name)
            return agent

        builder = Builder(
            simple_project, agent, state, git, cfg,
            agent_factory=tracking_factory,
        )
        builder.build(BuildOptions())

        # No CLI override, no target profile -> falls back to "default"
        assert resolved_profiles == ["default"]


# ---------------------------------------------------------------------------
# Tests: schema validation abort
# ---------------------------------------------------------------------------


class TestSchemaValidationAbort:
    """Tests that schema errors prevent the build from proceeding."""

    def test_invalid_spec_aborts_build(self, tmp_path):
        """A .ic file with missing required fields causes build to abort."""
        root = str(tmp_path / "bad")
        _make_project(
            root,
            targets={
                "broken": {
                    "ic": textwrap.dedent("""\
                        ---
                        name: wrong-name
                        version: 1
                        ---
                        Broken intent.
                    """),
                    "icv": textwrap.dedent("""\
                        ---
                        target: broken
                        version: 1
                        validations:
                          - name: check
                            type: file_check
                            path: out.py
                        ---
                    """),
                },
            },
        )

        state = MockStateManager()
        agent = MockAgent(files=[])
        git = MockGitManager()
        cfg = get_default_config()

        builder = _mock_builder(root, agent, state, git, cfg)

        with pytest.raises(RuntimeError, match="Schema validation failed"):
            builder.build(BuildOptions())

        # Agent was never called
        assert len(agent.build_calls) == 0

    def test_missing_intent_dir_aborts(self, tmp_path):
        """A project with no intent/ directory causes build to abort."""
        root = str(tmp_path / "empty")
        os.makedirs(root, exist_ok=True)
        # Create config but no intent dir
        _write_file(
            os.path.join(root, ".intentc", "config.yaml"),
            textwrap.dedent("""\
                version: 1
                profiles:
                  default:
                    provider: claude
                    timeout: 5m
                    retries: 3
                    rate_limit: 1s
                build:
                  default_output: build-default
                logging:
                  level: info
            """),
        )

        state = MockStateManager()
        agent = MockAgent(files=[])
        git = MockGitManager()
        cfg = get_default_config()

        builder = _mock_builder(root, agent, state, git, cfg)

        with pytest.raises(RuntimeError, match="Schema validation failed"):
            builder.build(BuildOptions())


# ---------------------------------------------------------------------------
# Tests: clean
# ---------------------------------------------------------------------------


class TestClean:
    """Tests for the clean() method."""

    def test_clean_removes_files(self, simple_project, tmp_path):
        """Clean removes generated files and resets status."""
        state = MockStateManager()
        agent = MockAgent(files=[])
        git = MockGitManager()
        cfg = get_default_config()

        # Create a fake build result with a real file
        output_file = str(tmp_path / "generated.py")
        with open(output_file, "w") as f:
            f.write("# generated")

        state.results["auth"] = BuildResult(
            target="auth",
            generation_id="gen-1",
            success=True,
            files=[output_file],
            output_dir=str(tmp_path),
        )
        state.statuses["auth"] = TargetStatus.BUILT

        builder = _mock_builder(simple_project, agent, state, git, cfg)
        builder.clean("auth", str(tmp_path))

        assert not os.path.exists(output_file)
        assert state.statuses["auth"] == TargetStatus.PENDING

    def test_clean_nonexistent_target(self, simple_project):
        """Clean gracefully handles a target with no build history."""
        state = MockStateManager()
        agent = MockAgent(files=[])
        git = MockGitManager()
        cfg = get_default_config()

        builder = _mock_builder(simple_project, agent, state, git, cfg)
        # Should not raise
        builder.clean("nonexistent", "")

    def test_clean_all_resets_all_state(self, simple_project):
        """Clean --all resets all targets via reset_all."""
        state = MockStateManager()
        agent = MockAgent(files=[])
        git = MockGitManager()
        cfg = get_default_config()

        state.statuses["auth"] = TargetStatus.BUILT
        state.statuses["core"] = TargetStatus.BUILT

        builder = _mock_builder(simple_project, agent, state, git, cfg)
        builder.clean(target="", output_dir="", all=True)

        assert state.statuses == {}
        assert state.results == {}

    def test_clean_no_target_no_all_is_noop(self, simple_project):
        """Clean with no target and no --all just logs."""
        state = MockStateManager()
        agent = MockAgent(files=[])
        git = MockGitManager()
        cfg = get_default_config()

        state.statuses["auth"] = TargetStatus.BUILT

        builder = _mock_builder(simple_project, agent, state, git, cfg)
        builder.clean(target="", output_dir="", all=False)

        # State should be unchanged
        assert state.statuses["auth"] == TargetStatus.BUILT


# ---------------------------------------------------------------------------
# Tests: build state transitions
# ---------------------------------------------------------------------------


class TestBuildStateTransitions:
    """Tests that verify correct state transitions during a build."""

    def test_state_transitions_on_success(self, simple_project):
        """Status goes: pending -> building -> built on success."""
        transitions: list[tuple[str, TargetStatus]] = []

        class TrackingStateManager(MockStateManager):
            def update_target_status(
                self, name: str, status: TargetStatus
            ) -> None:
                transitions.append((name, status))
                super().update_target_status(name, status)

        state = TrackingStateManager()
        agent = MockAgent(files=["auth.py"])
        git = MockGitManager()
        cfg = get_default_config()

        builder = _mock_builder(simple_project, agent, state, git, cfg)
        builder.build(BuildOptions())

        assert ("auth", TargetStatus.BUILDING) in transitions
        assert ("auth", TargetStatus.BUILT) in transitions
        # Building comes before built
        building_idx = transitions.index(("auth", TargetStatus.BUILDING))
        built_idx = transitions.index(("auth", TargetStatus.BUILT))
        assert building_idx < built_idx

    def test_state_transitions_on_failure(self, simple_project):
        """Status goes: pending -> building -> failed on error."""
        transitions: list[tuple[str, TargetStatus]] = []

        class TrackingStateManager(MockStateManager):
            def update_target_status(
                self, name: str, status: TargetStatus
            ) -> None:
                transitions.append((name, status))
                super().update_target_status(name, status)

        state = TrackingStateManager()
        agent = MockAgent(error=RuntimeError("boom"))
        git = MockGitManager()
        cfg = get_default_config()

        builder = _mock_builder(simple_project, agent, state, git, cfg)

        with pytest.raises(RuntimeError):
            builder.build(BuildOptions())

        assert ("auth", TargetStatus.BUILDING) in transitions
        assert ("auth", TargetStatus.FAILED) in transitions


# ---------------------------------------------------------------------------
# Tests: BuildOptions model
# ---------------------------------------------------------------------------


class TestBuildOptions:
    """Tests for the BuildOptions pydantic model."""

    def test_defaults(self):
        opts = BuildOptions()
        assert opts.target == ""
        assert opts.force is False
        assert opts.dry_run is False
        assert opts.output_dir == ""
        assert opts.profile_name == ""

    def test_custom_values(self):
        opts = BuildOptions(
            target="auth",
            force=True,
            dry_run=True,
            output_dir="/tmp/out",
            profile_name="fast",
        )
        assert opts.target == "auth"
        assert opts.force is True
        assert opts.dry_run is True
        assert opts.output_dir == "/tmp/out"
        assert opts.profile_name == "fast"

    def test_serialization(self):
        opts = BuildOptions(target="x", force=True)
        d = opts.model_dump()
        assert d["target"] == "x"
        assert d["force"] is True


# ---------------------------------------------------------------------------
# Tests: output directory resolution
# ---------------------------------------------------------------------------


class TestOutputDir:
    """Tests for output directory resolution logic."""

    def test_default_output_dir(self, simple_project):
        """Uses config.build.default_output when no override given."""
        state = MockStateManager()
        agent = MockAgent(files=[])
        git = MockGitManager()
        cfg = get_default_config()

        builder = _mock_builder(simple_project, agent, state, git, cfg)
        builder.build(BuildOptions(dry_run=True))

        expected = os.path.abspath(
            os.path.join(simple_project, "build-default")
        )
        # Output dir is created even in dry_run (before the dry_run check)
        assert os.path.isdir(expected)

    def test_custom_output_dir(self, simple_project):
        """Uses the override output_dir when specified."""
        state = MockStateManager()
        agent = MockAgent(files=[])
        git = MockGitManager()
        cfg = get_default_config()

        builder = _mock_builder(simple_project, agent, state, git, cfg)
        builder.build(BuildOptions(output_dir="my-output", dry_run=True))

        expected = os.path.abspath(
            os.path.join(simple_project, "my-output")
        )
        assert os.path.isdir(expected)


# ---------------------------------------------------------------------------
# Tests: generation ID
# ---------------------------------------------------------------------------


class TestGenerationId:
    """Tests for generation ID assignment."""

    def test_generation_id_format(self, simple_project):
        """Generation IDs follow the gen-{timestamp} pattern."""
        state = MockStateManager()
        agent = MockAgent(files=[])
        git = MockGitManager()
        cfg = get_default_config()

        builder = _mock_builder(simple_project, agent, state, git, cfg)
        builder.build(BuildOptions())

        result = state.results["auth"]
        assert result.generation_id.startswith("gen-")
        # Timestamp part should be numeric
        ts_part = result.generation_id[4:]
        assert ts_part.isdigit()


# ---------------------------------------------------------------------------
# Tests: build context
# ---------------------------------------------------------------------------


class TestBuildContext:
    """Tests for the build context passed to agents."""

    def test_context_has_project_intent(self, simple_project):
        """BuildContext includes the project-level intent."""
        state = MockStateManager()
        agent = MockAgent(files=[])
        git = MockGitManager()
        cfg = get_default_config()

        builder = _mock_builder(simple_project, agent, state, git, cfg)
        builder.build(BuildOptions())

        ctx = agent.build_calls[0]
        assert ctx.project_intent.name == "test-project"

    def test_context_has_dependency_names(self, dep_project):
        """BuildContext for a dependent target includes dependency names."""
        state = MockStateManager()
        agent = MockAgent(files=[])
        git = MockGitManager()
        cfg = get_default_config()

        builder = _mock_builder(dep_project, agent, state, git, cfg)
        builder.build(BuildOptions())

        # Find the 'api' build context
        api_ctx = [c for c in agent.build_calls if c.intent.name == "api"][0]
        assert "auth" in api_ctx.dependency_names

    def test_context_has_output_dir(self, simple_project):
        """BuildContext includes the resolved output directory."""
        state = MockStateManager()
        agent = MockAgent(files=[])
        git = MockGitManager()
        cfg = get_default_config()

        builder = _mock_builder(simple_project, agent, state, git, cfg)
        builder.build(BuildOptions(output_dir="out"))

        ctx = agent.build_calls[0]
        expected = os.path.abspath(os.path.join(simple_project, "out"))
        assert ctx.output_dir == expected


# ---------------------------------------------------------------------------
# Tests: build steps
# ---------------------------------------------------------------------------


class TestBuildSteps:
    """Tests for structured build step emission."""

    def test_build_result_has_steps(self, simple_project):
        """A successful build produces steps with the expected phases."""
        state = MockStateManager()
        agent = MockAgent(files=["auth.py"])
        git = MockGitManager()
        cfg = get_default_config()

        builder = _mock_builder(simple_project, agent, state, git, cfg)
        builder.build(BuildOptions())

        result = state.results["auth"]
        phases = [s.phase for s in result.steps]
        assert BuildPhase.RESOLVE_DEPS in phases
        assert BuildPhase.READ_PLAN in phases
        assert BuildPhase.BUILD in phases
        assert BuildPhase.POST_BUILD in phases

    def test_steps_have_timing(self, simple_project):
        """Each step has duration_seconds >= 0 and started_at/ended_at set."""
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
        """total_duration_seconds equals the sum of step durations."""
        state = MockStateManager()
        agent = MockAgent(files=["auth.py"])
        git = MockGitManager()
        cfg = get_default_config()

        builder = _mock_builder(simple_project, agent, state, git, cfg)
        builder.build(BuildOptions())

        result = state.results["auth"]
        expected = round(sum(s.duration_seconds for s in result.steps), 3)
        assert result.total_duration_seconds == expected

    def test_successful_steps_have_success_status(self, simple_project):
        """All non-validate steps in a successful build have status=success."""
        state = MockStateManager()
        agent = MockAgent(files=["auth.py"])
        git = MockGitManager()
        cfg = get_default_config()

        builder = _mock_builder(simple_project, agent, state, git, cfg)
        builder.build(BuildOptions())

        result = state.results["auth"]
        for step in result.steps:
            if step.phase != BuildPhase.VALIDATE:
                assert step.status == StepStatus.SUCCESS

    def test_build_failure_records_failed_step(self, simple_project):
        """When agent fails, the build step has status=failed with error."""
        state = MockStateManager()
        agent = MockAgent(error=RuntimeError("Agent crashed"))
        git = MockGitManager()
        cfg = get_default_config()

        builder = _mock_builder(simple_project, agent, state, git, cfg)

        with pytest.raises(RuntimeError):
            builder.build(BuildOptions())

        result = state.results["auth"]
        build_step = [
            s for s in result.steps if s.phase == BuildPhase.BUILD
        ][0]
        assert build_step.status == StepStatus.FAILED
        assert "Agent crashed" in build_step.error

    def test_post_build_captures_diff(self, simple_project):
        """post_build step captures diff and diff_stat from git manager."""
        state = MockStateManager()
        agent = MockAgent(files=["auth.py"])
        git = MockGitManager()
        git.diff_result = "diff --git a/auth.py b/auth.py\n+hello"
        git.diff_stat_result = " auth.py | 1 +\n 1 file changed, 1 insertion(+)"
        cfg = get_default_config()

        builder = _mock_builder(simple_project, agent, state, git, cfg)
        builder.build(BuildOptions())

        result = state.results["auth"]
        post_step = [
            s for s in result.steps if s.phase == BuildPhase.POST_BUILD
        ][0]
        assert post_step.diff == git.diff_result
        assert post_step.diff_stat == git.diff_stat_result

    def test_resolve_deps_summary(self, dep_project):
        """For dep_project, api target has '1' in resolve summary."""
        state = MockStateManager()
        agent = MockAgent(files=["output.py"])
        git = MockGitManager()
        cfg = get_default_config()

        builder = _mock_builder(dep_project, agent, state, git, cfg)
        builder.build(BuildOptions())

        result = state.results["api"]
        resolve_step = [
            s for s in result.steps if s.phase == BuildPhase.RESOLVE_DEPS
        ][0]
        assert "1" in resolve_step.summary

    def test_read_plan_summary(self, simple_project):
        """Summary mentions the target name 'auth'."""
        state = MockStateManager()
        agent = MockAgent(files=["auth.py"])
        git = MockGitManager()
        cfg = get_default_config()

        builder = _mock_builder(simple_project, agent, state, git, cfg)
        builder.build(BuildOptions())

        result = state.results["auth"]
        read_step = [
            s for s in result.steps if s.phase == BuildPhase.READ_PLAN
        ][0]
        assert "auth" in read_step.summary
