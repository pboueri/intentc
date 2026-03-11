"""Tests for the CLI package."""

from __future__ import annotations

import os
import subprocess
import tempfile

import yaml
from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_git_repo(path: str) -> None:
    """Initialize a bare git repo at *path* so git commands succeed."""
    subprocess.run(
        ["git", "init"],
        cwd=path,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=path,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path,
        capture_output=True,
        check=True,
    )
    # Create an initial commit so HEAD exists
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "initial"],
        cwd=path,
        capture_output=True,
        check=True,
    )


def _init_project(path: str) -> None:
    """Run `intentc init` inside a git-initialized temp directory."""
    _make_git_repo(path)
    old_cwd = os.getcwd()
    try:
        os.chdir(path)
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0, result.output
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def test_init_creates_structure():
    with tempfile.TemporaryDirectory() as tmp:
        _make_git_repo(tmp)
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp)
            result = runner.invoke(app, ["init"])
            assert result.exit_code == 0, result.output
            assert "Initialized intentc project." in result.output

            # .intentc directory with config.yaml
            assert os.path.isdir(os.path.join(tmp, ".intentc"))
            config_path = os.path.join(tmp, ".intentc", "config.yaml")
            assert os.path.isfile(config_path)

            # Verify config has default profile
            with open(config_path) as f:
                cfg = yaml.safe_load(f)
            assert "profiles" in cfg
            assert "default" in cfg["profiles"]
            assert cfg["profiles"]["default"]["provider"] == "claude"

            # intent/ directory with project.ic
            assert os.path.isdir(os.path.join(tmp, "intent"))
            project_ic = os.path.join(tmp, "intent", "project.ic")
            assert os.path.isfile(project_ic)
            with open(project_ic) as f:
                content = f.read()
            assert "name: my-project" in content
            assert "version: 1" in content

            # .gitignore has .intentc/state/
            gitignore = os.path.join(tmp, ".gitignore")
            assert os.path.isfile(gitignore)
            with open(gitignore) as f:
                gi_content = f.read()
            assert ".intentc/state/" in gi_content
        finally:
            os.chdir(old_cwd)


def test_init_errors_if_not_git_repo():
    with tempfile.TemporaryDirectory() as tmp:
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp)
            result = runner.invoke(app, ["init"])
            assert result.exit_code != 0
            assert "not a git repository" in result.output.lower() or result.exit_code == 1
        finally:
            os.chdir(old_cwd)


def test_init_errors_if_already_initialized():
    with tempfile.TemporaryDirectory() as tmp:
        _make_git_repo(tmp)
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp)
            # First init succeeds
            result = runner.invoke(app, ["init"])
            assert result.exit_code == 0

            # Second init should fail
            result = runner.invoke(app, ["init"])
            assert result.exit_code != 0
            assert "already initialized" in result.output.lower()
        finally:
            os.chdir(old_cwd)


def test_init_appends_to_existing_gitignore():
    with tempfile.TemporaryDirectory() as tmp:
        _make_git_repo(tmp)
        # Create a pre-existing .gitignore
        gitignore = os.path.join(tmp, ".gitignore")
        with open(gitignore, "w") as f:
            f.write("node_modules/\n")

        old_cwd = os.getcwd()
        try:
            os.chdir(tmp)
            result = runner.invoke(app, ["init"])
            assert result.exit_code == 0

            with open(gitignore) as f:
                content = f.read()
            assert "node_modules/" in content
            assert ".intentc/state/" in content
        finally:
            os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


def test_check_validates_specs():
    with tempfile.TemporaryDirectory() as tmp:
        _init_project(tmp)
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp)
            result = runner.invoke(app, ["check"])
            assert result.exit_code == 0
            assert "All spec files are valid." in result.output
        finally:
            os.chdir(old_cwd)


def test_check_reports_errors_for_invalid_spec():
    with tempfile.TemporaryDirectory() as tmp:
        _init_project(tmp)
        # Create a malformed .ic file in a feature directory
        feature_dir = os.path.join(tmp, "intent", "bad-feature")
        os.makedirs(feature_dir)
        with open(os.path.join(feature_dir, "bad-feature.ic"), "w") as f:
            f.write("---\nversion: 1\n---\n\nNo name field.\n")

        old_cwd = os.getcwd()
        try:
            os.chdir(tmp)
            result = runner.invoke(app, ["check"])
            assert result.exit_code != 0
            assert "Errors:" in result.output or "Error" in result.output
        finally:
            os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# add intent
# ---------------------------------------------------------------------------


def test_add_intent_creates_scaffold():
    with tempfile.TemporaryDirectory() as tmp:
        _init_project(tmp)
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp)
            result = runner.invoke(app, ["add", "intent", "auth"])
            assert result.exit_code == 0
            assert "Created feature scaffold" in result.output

            feature_dir = os.path.join(tmp, "intent", "auth")
            assert os.path.isdir(feature_dir)

            ic_path = os.path.join(feature_dir, "auth.ic")
            assert os.path.isfile(ic_path)

            with open(ic_path) as f:
                content = f.read()
            assert "name: auth" in content
            assert "version: 1" in content
        finally:
            os.chdir(old_cwd)


def test_add_intent_errors_if_already_exists():
    with tempfile.TemporaryDirectory() as tmp:
        _init_project(tmp)
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp)
            runner.invoke(app, ["add", "intent", "auth"])
            result = runner.invoke(app, ["add", "intent", "auth"])
            assert result.exit_code != 0
            assert "already exists" in result.output.lower()
        finally:
            os.chdir(old_cwd)


def test_add_intent_errors_if_no_intent_dir():
    with tempfile.TemporaryDirectory() as tmp:
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp)
            result = runner.invoke(app, ["add", "intent", "auth"])
            assert result.exit_code != 0
            assert "intent/ directory not found" in result.output.lower() or result.exit_code == 1
        finally:
            os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# add validation
# ---------------------------------------------------------------------------


def test_add_validation_creates_icv():
    with tempfile.TemporaryDirectory() as tmp:
        _init_project(tmp)
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp)
            # Create a feature first
            runner.invoke(app, ["add", "intent", "auth"])

            result = runner.invoke(app, ["add", "validation", "auth", "file_check"])
            assert result.exit_code == 0
            assert "Added file_check validation" in result.output

            icv_path = os.path.join(tmp, "intent", "auth", "validations.icv")
            assert os.path.isfile(icv_path)

            with open(icv_path) as f:
                content = f.read()
            assert "file_check" in content
            assert "target: auth" in content
        finally:
            os.chdir(old_cwd)


def test_add_validation_errors_for_invalid_type():
    with tempfile.TemporaryDirectory() as tmp:
        _init_project(tmp)
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp)
            runner.invoke(app, ["add", "intent", "auth"])
            result = runner.invoke(app, ["add", "validation", "auth", "invalid_type"])
            assert result.exit_code != 0
            assert "unknown validation type" in result.output.lower()
        finally:
            os.chdir(old_cwd)


def test_add_validation_errors_if_target_missing():
    with tempfile.TemporaryDirectory() as tmp:
        _init_project(tmp)
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp)
            result = runner.invoke(app, ["add", "validation", "nonexistent", "file_check"])
            assert result.exit_code != 0
            assert "not found" in result.output.lower()
        finally:
            os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_shows_targets():
    with tempfile.TemporaryDirectory() as tmp:
        _init_project(tmp)
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp)
            # Add a feature so there is something to show
            runner.invoke(app, ["add", "intent", "auth"])
            result = runner.invoke(app, ["status"])
            assert result.exit_code == 0
            assert "auth" in result.output
        finally:
            os.chdir(old_cwd)


def test_status_no_targets():
    with tempfile.TemporaryDirectory() as tmp:
        _init_project(tmp)
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp)
            result = runner.invoke(app, ["status"])
            assert result.exit_code == 0
            assert "No targets found." in result.output
        finally:
            os.chdir(old_cwd)


def test_status_tree_flag():
    with tempfile.TemporaryDirectory() as tmp:
        _init_project(tmp)
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp)
            runner.invoke(app, ["add", "intent", "core"])
            runner.invoke(app, ["add", "intent", "auth"])

            # Add dependency: auth depends on core
            ic_path = os.path.join(tmp, "intent", "auth", "auth.ic")
            with open(ic_path, "w") as f:
                f.write(
                    "---\n"
                    "name: auth\n"
                    "version: 1\n"
                    "depends_on: [core]\n"
                    "tags: []\n"
                    "---\n\n"
                    "# auth\n"
                )

            result = runner.invoke(app, ["status", "--tree"])
            assert result.exit_code == 0
            assert "Dependency tree:" in result.output
            assert "core" in result.output
            assert "auth" in result.output
        finally:
            os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# list commands
# ---------------------------------------------------------------------------


def test_list_intents():
    with tempfile.TemporaryDirectory() as tmp:
        _init_project(tmp)
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp)
            runner.invoke(app, ["add", "intent", "auth"])
            result = runner.invoke(app, ["list", "intents"])
            assert result.exit_code == 0
            assert "auth" in result.output
            assert "Name" in result.output  # header
        finally:
            os.chdir(old_cwd)


def test_list_intents_empty():
    with tempfile.TemporaryDirectory() as tmp:
        _init_project(tmp)
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp)
            result = runner.invoke(app, ["list", "intents"])
            assert result.exit_code == 0
            assert "No features found." in result.output
        finally:
            os.chdir(old_cwd)


def test_list_validations():
    result = runner.invoke(app, ["list", "validations"])
    assert result.exit_code == 0
    assert "file_check" in result.output
    assert "folder_check" in result.output
    assert "command_check" in result.output
    assert "llm_judge" in result.output


def test_list_profiles():
    with tempfile.TemporaryDirectory() as tmp:
        _init_project(tmp)
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp)
            result = runner.invoke(app, ["list", "profiles"])
            assert result.exit_code == 0
            assert "default" in result.output
            assert "claude" in result.output
        finally:
            os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Global flags
# ---------------------------------------------------------------------------


def test_verbose_flag():
    """Verbose flag should not cause errors."""
    result = runner.invoke(app, ["-v", "list", "validations"])
    assert result.exit_code == 0


def test_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "intentc" in result.output.lower() or "compiler" in result.output.lower()


def test_init_help():
    result = runner.invoke(app, ["init", "--help"])
    assert result.exit_code == 0
    assert "Initialize" in result.output


def test_build_help():
    result = runner.invoke(app, ["build", "--help"])
    assert result.exit_code == 0
    assert "Build" in result.output


# ---------------------------------------------------------------------------
# check with valid feature
# ---------------------------------------------------------------------------


def test_check_with_valid_feature():
    with tempfile.TemporaryDirectory() as tmp:
        _init_project(tmp)
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp)
            runner.invoke(app, ["add", "intent", "auth"])
            result = runner.invoke(app, ["check"])
            assert result.exit_code == 0
            assert "All spec files are valid." in result.output
        finally:
            os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# commit
# ---------------------------------------------------------------------------


def test_commit_nothing_to_commit():
    with tempfile.TemporaryDirectory() as tmp:
        _init_project(tmp)
        # Stage and commit everything so working tree is clean
        subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "setup"],
            cwd=tmp,
            capture_output=True,
            check=True,
        )
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp)
            result = runner.invoke(app, ["commit", "-m", "test"])
            assert result.exit_code == 0
            assert "Nothing to commit" in result.output
        finally:
            os.chdir(old_cwd)


def test_commit_separates_intent_and_generated():
    with tempfile.TemporaryDirectory() as tmp:
        _init_project(tmp)
        # Commit the init state
        subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=tmp,
            capture_output=True,
            check=True,
        )

        # Create both intent and generated changes
        os.makedirs(os.path.join(tmp, "intent", "auth"), exist_ok=True)
        with open(os.path.join(tmp, "intent", "auth", "auth.ic"), "w") as f:
            f.write("---\nname: auth\nversion: 1\ndepends_on: []\ntags: []\n---\n\n# Auth\n")

        with open(os.path.join(tmp, "generated.txt"), "w") as f:
            f.write("generated content\n")

        old_cwd = os.getcwd()
        try:
            os.chdir(tmp)
            result = runner.invoke(app, ["commit", "-m", "add auth"])
            assert result.exit_code == 0
            assert "intent" in result.output.lower()
            assert "generated" in result.output.lower()
            assert "2 commit(s)" in result.output
        finally:
            os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# log
# ---------------------------------------------------------------------------


class TestLogCommand:
    """Tests for the 'intentc log' command."""

    def test_log_no_builds(self):
        """Log with no build history shows message."""
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
                    steps=[BuildStep(
                        phase=BuildPhase.BUILD,
                        status=StepStatus.SUCCESS,
                        started_at=datetime(2026, 3, 11, 14, 0),
                        ended_at=datetime(2026, 3, 11, 14, 0, 5),
                        duration_seconds=5.0,
                        summary="Agent generated 1 file",
                    )],
                    total_duration_seconds=5.0,
                ))

                # We need an intent file for the target to show up
                intent_dir = os.path.join(tmp, "intent", "auth")
                os.makedirs(intent_dir, exist_ok=True)
                with open(os.path.join(intent_dir, "auth.ic"), "w") as f:
                    f.write("---\nname: auth\nversion: 1\n---\nAuth module.\n")

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
                assert "5.1" in result.output
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
                    steps=[BuildStep(
                        phase=BuildPhase.POST_BUILD,
                        status=StepStatus.SUCCESS,
                        started_at=datetime(2026, 3, 11, 14, 0),
                        ended_at=datetime(2026, 3, 11, 14, 0, 1),
                        duration_seconds=0.1,
                        summary="1 file changed",
                        diff="--- a/auth.py\n+++ b/auth.py\n+hello\n",
                        diff_stat="1 file changed, 1 insertion(+)",
                    )],
                    total_duration_seconds=0.1,
                ))
                result = runner.invoke(app, ["log", "auth", "--diff"])
                assert result.exit_code == 0
                assert "--- a/auth.py" in result.output
                assert "+hello" in result.output
            finally:
                os.chdir(old_cwd)

    def test_log_no_steps_graceful(self):
        """Log handles build results without step data."""
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
